"""Pricing & affordability-quote endpoint for the reception flow.

POST /pricing/quote takes a basket (lines: irc or free-text drug name + qty) and
an insurer, resolves each line against the drug catalog (effective price = max of
announced vs latest invoice), prices it through the Iranian pricing engine
(insurer share / patient share / مابه‌التفاوت / حق فنی / VAT), and returns the
per-line breakdown PLUS cheaper same-ingredient alternatives for the swap lever.
"""
from __future__ import annotations

from decimal import Decimal

import os
import tempfile

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.auth import require_permission
from services.platform.database import get_db
from shared.models.auth import Staff

from services.core.pricing_ir.config import get_plan, DEFAULT_TECHNICAL_FEE_RIAL, VAT_RATE_COSMETIC
from services.core.pricing_ir.engine import (
    DrugPrice, LineInput, ItemCategory, price_prescription,
)
from services.core.drug_catalog import repo
from services.core.drug_catalog import sync_service
from services.core.drug_catalog.alternatives import find_alternatives
from services.core.drug_catalog.schema import CatalogRecord
from services.core.pricing_ir.eligibility import get_eligibility_provider
from uuid import UUID

router = APIRouter()


class QuoteLineIn(BaseModel):
    irc: str | None = None
    drug_name: str | None = None
    quantity: float = 1
    removed: bool = False        # receptionist excluded this item


class QuoteRequest(BaseModel):
    insurer: str = "tamin"
    setting: str = "outpatient"
    technical_fee: float | None = None
    national_id: str | None = None      # enables live نسخه الکترونیک استعلام when a provider is configured
    lines: list[QuoteLineIn]


def _coverage(rec: CatalogRecord, insurer: str) -> tuple[bool, Decimal | None, Decimal]:
    """(is_covered, insurer_reference_price, vat_rate).

    Prefer the catalog's per-insurer coverage JSON when present:
      {"tamin": {"covered": true, "reference_price": 110000}, ...}
    The reference_price is what the insurer reimburses against — dispensing a
    pricier product yields مابه‌التفاوت. Falls back to a sane default (drugs/OTC
    covered at their own announced price → no differential; supplements/cosmetics
    not covered; cosmetics carry VAT)."""
    vat = VAT_RATE_COSMETIC if rec.category == ItemCategory.COSMETIC else Decimal("0")
    entry = (rec.coverage or {}).get(insurer) if isinstance(rec.coverage, dict) else None
    if entry is not None:
        covered = bool(entry.get("covered", True))
        ref = entry.get("reference_price")
        return covered, (Decimal(str(ref)) if ref is not None else None), vat
    covered = rec.category in (ItemCategory.DRUG, ItemCategory.OTC)
    return covered, None, vat


@router.post("/quote")
async def quote(body: QuoteRequest,
                staff: Staff = Depends(require_permission("clinical:read")),
                db: AsyncSession = Depends(get_db)):
    plan = get_plan(body.insurer)

    # ── Resolve each line to a catalog record ─────────────────────────────────
    resolved: list[tuple[QuoteLineIn, CatalogRecord | None]] = []
    irc_hits = await repo.fetch_by_irc(db, [l.irc for l in body.lines if l.irc])
    for l in body.lines:
        rec = irc_hits.get(l.irc) if l.irc else None
        if rec is None and l.drug_name:
            rec = await repo.resolve_by_name(db, l.drug_name)
        resolved.append((l, rec))

    # ── Live e-prescription استعلام (authoritative سهم), if a provider is wired ─
    elig = None
    provider = get_eligibility_provider()
    if body.national_id:
        ircs = [rec.irc for _, rec in resolved if rec is not None]
        elig = await provider.inquire(national_id=body.national_id, insurer=body.insurer, ircs=ircs)
    elig_lines = {ln.irc: ln for ln in elig.lines} if elig else {}

    # ── Cheaper alternatives per line (same ingredient_key) ───────────────────
    keys = [rec.ingredient_key for _, rec in resolved if rec is not None]
    pool = await repo.fetch_by_ingredient_keys(db, keys)

    engine_lines: list[LineInput] = []
    line_meta: list[dict] = []
    for l, rec in resolved:
        if l.removed:
            line_meta.append({"line": l, "rec": rec, "skip": True})
            continue
        if rec is None:
            line_meta.append({"line": l, "rec": None, "skip": False, "unmatched": True})
            continue
        covered, ref, vat = _coverage(rec, body.insurer)
        el = elig_lines.get(rec.irc)          # live استعلام overrides catalog coverage
        if el is not None:
            covered = el.covered
            if el.reference_price is not None:
                ref = Decimal(str(el.reference_price))
        engine_lines.append(LineInput(
            drug=DrugPrice(
                irc=rec.irc, name=rec.name_fa, consumer_price=rec.effective_price,
                insurer_reference_price=ref, category=rec.category,
                is_covered=covered, vat_rate=vat,
            ),
            quantity=Decimal(str(l.quantity)),
        ))
        line_meta.append({"line": l, "rec": rec, "skip": False})

    fee = Decimal(str(body.technical_fee)) if body.technical_fee is not None else DEFAULT_TECHNICAL_FEE_RIAL
    pricing = price_prescription(engine_lines, plan, technical_fee=fee, setting=body.setting)

    # ── Stitch breakdowns back to lines (+ alternatives) ──────────────────────
    out_lines = []
    bd_iter = iter(pricing.lines)
    for meta in line_meta:
        l, rec = meta["line"], meta["rec"]
        if meta.get("skip"):
            out_lines.append({"drug_name": l.drug_name or (rec.name_fa if rec else ""),
                              "irc": l.irc, "removed": True})
            continue
        if meta.get("unmatched"):
            out_lines.append({"drug_name": l.drug_name, "irc": l.irc, "unmatched": True,
                              "quantity": l.quantity})
            continue
        b = next(bd_iter)
        alts = find_alternatives(pool, rec.irc)
        out_lines.append({
            "irc": rec.irc, "name": rec.name_fa, "generic_name": rec.generic_name,
            "brand_name": rec.brand_name, "is_generic": rec.is_generic,
            "category": rec.category.value, "quantity": float(l.quantity),
            "unit_price": float(rec.effective_price),
            "gross": float(b.gross), "covered": b.covered,
            "insurer_share": float(b.insurer_share), "patient_share": float(b.patient_share),
            "differential": float(b.differential), "vat": float(b.vat),
            "patient_total": float(b.patient_total),
            "alternatives": [{
                "irc": a.record.irc, "name": a.record.name_fa, "brand_name": a.record.brand_name,
                "is_generic": a.record.is_generic, "unit_price": float(a.effective_price),
                "savings_per_unit": float(a.savings_vs_current),
                "savings_total": float(a.savings_vs_current * Decimal(str(l.quantity))),
            } for a in alts],
        })

    t = pricing.totals
    return {
        "insurer": plan.code, "insurer_name_fa": plan.name_fa, "setting": body.setting,
        "lines": out_lines,
        "technical_fee": {"total": float(pricing.technical_fee.total),
                          "insurer": float(pricing.technical_fee.insurer),
                          "patient": float(pricing.technical_fee.patient)},
        "totals": {"gross": float(t.gross), "insurer": float(t.insurer),
                   "patient": float(t.patient), "differential": float(t.differential),
                   "vat": float(t.vat), "grand_total": float(t.grand_total)},
        "notes": pricing.notes,
        "eligibility": {
            "source": "live" if elig else "local",
            "provider": provider.code,
            "tracking_code": (elig.tracking_code if elig else None),
        },
    }


# ── Daily price sync → manager-approved proposals ─────────────────────────────
class SyncLine(BaseModel):
    irc: str
    name_fa: str | None = None
    announced_price: float | None = None
    last_invoice_price: float | None = None


class SyncRequest(BaseModel):
    source: str = "sync"
    lines: list[SyncLine]


class DecideRequest(BaseModel):
    ids: list[UUID]
    approve: bool = True


def _proposal_json(p) -> dict:
    return {
        "id": str(p.id), "irc": p.irc, "name_fa": p.name_fa, "kind": p.kind,
        "status": p.status, "source": p.source,
        "current_effective": int(p.current_effective), "proposed_effective": int(p.proposed_effective),
        "current_announced": (int(p.current_announced) if p.current_announced is not None else None),
        "proposed_announced": (int(p.proposed_announced) if p.proposed_announced is not None else None),
        "current_invoice": (int(p.current_invoice) if p.current_invoice is not None else None),
        "proposed_invoice": (int(p.proposed_invoice) if p.proposed_invoice is not None else None),
        "delta": int(p.delta), "pct_change": float(p.pct_change),
    }


@router.post("/sync")
async def price_sync(body: SyncRequest,
                     staff: Staff = Depends(require_permission("inventory:write")),
                     db: AsyncSession = Depends(get_db)):
    """Ingest an updated price feed (announced and/or invoice) → create pending
    proposals. Catalog prices are NOT changed until a manager approves."""
    rows = [l.model_dump() for l in body.lines]
    return await sync_service.run_sync(db, rows, source=body.source)


@router.get("/catalog/stats")
async def catalog_stats(staff: Staff = Depends(require_permission("inventory:read")),
                        db: AsyncSession = Depends(get_db)):
    """Catalog size + freshness for the admin dashboard."""
    from sqlalchemy import func, select as _select
    from shared.models.drug_catalog import DrugCatalogItem as _D
    total = (await db.execute(_select(func.count()).select_from(_D))).scalar() or 0
    priced = (await db.execute(_select(func.count()).select_from(_D)
                               .where(_D.announced_price.isnot(None)))).scalar() or 0
    ingredients = (await db.execute(_select(func.count(func.distinct(_D.ingredient_key))))).scalar() or 0
    last = (await db.execute(_select(func.max(_D.updated_at)).select_from(_D))).scalar()
    return {"total": total, "priced": priced, "ingredient_groups": ingredients,
            "last_updated": (last.isoformat() if last else None)}


class NfiHarvestRequest(BaseModel):
    start_id: int = 1
    end_id: int = 60000
    delay: float = 0.25
    proxy: str | None = None      # Iran proxy URL (else server HTTPS_PROXY is used)


@router.post("/catalog/nfi/start")
async def nfi_harvest_start(body: NfiHarvestRequest,
                            staff: Staff = Depends(require_permission("inventory:write"))):
    """Start a background NFI crawl → catalog. Requires an Iran-reachable proxy."""
    from services.core.drug_catalog import nfi_harvest_service as svc
    try:
        return svc.start(body.start_id, body.end_id, delay=body.delay, proxy=body.proxy)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/catalog/nfi/status")
async def nfi_harvest_status(staff: Staff = Depends(require_permission("inventory:read"))):
    from services.core.drug_catalog import nfi_harvest_service as svc
    return svc.status()


@router.post("/catalog/nfi/stop")
async def nfi_harvest_stop(staff: Staff = Depends(require_permission("inventory:write"))):
    from services.core.drug_catalog import nfi_harvest_service as svc
    return {"stopping": svc.request_stop()}


@router.post("/catalog/import")
async def import_catalog(file: UploadFile = File(...),
                         staff: Staff = Depends(require_permission("inventory:write")),
                         db: AsyncSession = Depends(get_db)):
    """Ingest the official فهرست رسمی دارویی / NFI export (.xlsx or .csv) into the
    catalog. Column headers (Persian/English) are auto-mapped; prices with Persian
    digits/separators are parsed. Returns rows written + a small sample."""
    from services.core.drug_catalog.excel_import import records_from_file
    from services.core.drug_catalog.importer import upsert_catalog

    suffix = os.path.splitext(file.filename or "")[1].lower() or ".xlsx"
    if suffix not in (".xlsx", ".xlsm", ".xls", ".csv", ".tsv"):
        raise HTTPException(status_code=400, detail="Upload an .xlsx or .csv drug list.")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(await file.read())
        tmp.close()
        records = records_from_file(tmp.name)
        if not records:
            raise HTTPException(status_code=422, detail="No rows recognized — check the file has an IRC/کد and نام column.")
        n = await upsert_catalog(db, records, source=f"import:{file.filename}")
    finally:
        os.unlink(tmp.name)
    sample = [{"irc": r.irc, "name": r.name_fa, "generic": r.generic_name,
               "price": (int(r.effective_price) if r.effective_price else 0)} for r in records[:5]]
    return {"imported": n, "sample": sample}


@router.post("/coverage/import")
async def import_coverage(file: UploadFile = File(...),
                          insurer: str = "tamin",
                          min_confidence: float = 0.75,
                          staff: Staff = Depends(require_permission("inventory:write")),
                          db: AsyncSession = Depends(get_db)):
    """Smart دارونامه import: infer column roles (headers or value distributions),
    fuzzy-link rows to catalog products (salt-stripped ingredient + strength/form
    scoring, Persian trade-name fallback), spread coverage across each ingredient
    group, auto-apply high-confidence links and report the rest for review."""
    from services.core.drug_catalog.excel_import import read_table
    from services.core.drug_catalog.coverage_import import (
        infer_columns, normalize_rows, link_rows, build_coverage, apply_coverage,
    )
    suffix = os.path.splitext(file.filename or "")[1].lower() or ".xlsx"
    if suffix not in (".xlsx", ".xlsm", ".xls", ".csv", ".tsv", ".html", ".htm"):
        raise HTTPException(status_code=400, detail="Upload the دارونامه as .xlsx, .csv or a saved .html page.")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(await file.read())
        tmp.close()
        raw = read_table(tmp.name)
    finally:
        os.unlink(tmp.name)
    if not raw:
        raise HTTPException(status_code=422, detail="No table rows recognized in the file.")
    roles = infer_columns(raw)
    if "drug_name" not in roles.values() and "irc" not in roles.values():
        raise HTTPException(status_code=422,
                            detail=f"Could not locate a drug-name or IRC column. Columns: {list(raw[0].keys())[:12]}")
    rows = normalize_rows(raw, roles)
    catalog = await repo.fetch_all(db)
    links = link_rows(rows, catalog)
    cov = build_coverage(links, insurer=insurer, min_confidence=min_confidence, catalog=catalog)
    updated = await apply_coverage(db, cov.applied)
    return {
        "insurer": insurer, "columns": roles, "stats": {**cov.stats, "db_products_updated": updated},
        "review": cov.review[:50],
        "unmatched_sample": cov.unmatched[:20],
    }


@router.post("/sync/run")
async def price_sync_run(staff: Staff = Depends(require_permission("inventory:write")),
                         db: AsyncSession = Depends(get_db)):
    """Manually run the daily sync now: pull the configured feed → create proposals."""
    from services.core.drug_catalog.feed import fetch_daily_feed
    feed = await fetch_daily_feed()
    if not feed:
        return {"feed_rows": 0, "proposals_created": 0,
                "note": "No price feed configured — set PRICE_FEED_PATH or wire the NFI client."}
    return await sync_service.run_sync(db, feed, source="manual-run")


@router.get("/proposals")
async def list_price_proposals(status: str = "pending",
                               staff: Staff = Depends(require_permission("inventory:read")),
                               db: AsyncSession = Depends(get_db)):
    rows = await sync_service.list_proposals(db, status=status)
    return {"status": status, "count": len(rows), "proposals": [_proposal_json(p) for p in rows]}


@router.post("/proposals/decide")
async def decide_price_proposals(body: DecideRequest,
                                 staff: Staff = Depends(require_permission("inventory:write")),
                                 db: AsyncSession = Depends(get_db)):
    """Approve (apply to catalog) or reject pending price proposals."""
    return await sync_service.decide_proposals(db, body.ids, approve=body.approve, staff_id=staff.id)
