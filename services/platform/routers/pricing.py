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


@router.get("/catalog/country-proposals")
async def country_proposals(min_confidence: float = 0.0, limit: int = 5000,
                            staff: Staff = Depends(require_permission("inventory:read")),
                            db: AsyncSession = Depends(get_db)):
    """Proposed countries for products whose NFI page never stated one. Each
    carries the manufacturer's own observed split as evidence — importers and
    firms with too little history are excluded rather than guessed at."""
    from services.core.drug_catalog import country_inference
    return await country_inference.propose(db, min_confidence=min_confidence, limit=limit)


class CountryApplyRequest(BaseModel):
    ircs: list[str] = []
    min_confidence: float | None = None      # bulk-approve everything at/above


@router.post("/catalog/country-proposals/apply")
async def country_proposals_apply(body: CountryApplyRequest,
                                  staff: Staff = Depends(require_permission("inventory:write")),
                                  db: AsyncSession = Depends(get_db)):
    from services.core.drug_catalog import country_inference
    ircs = list(body.ircs)
    if body.min_confidence is not None:
        res = await country_inference.propose(db, min_confidence=body.min_confidence)
        ircs = [p["irc"] for p in res["proposals"]]
    if not ircs:
        raise HTTPException(status_code=400, detail="موردی برای اعمال انتخاب نشده است.")
    return await country_inference.apply(db, ircs, staff_id=getattr(staff, "id", None))


@router.get("/catalog/nfi/failures")
async def nfi_failures(staff: Staff = Depends(require_permission("inventory:read")),
                       db: AsyncSession = Depends(get_db)):
    """Pages a harvest lost, so the next pass can target ONLY those. A full sweep
    is ~70,000 pages; recovering a few thousand proxy-dropped ones must not cost
    another full run."""
    from sqlalchemy import func, select
    from services.core.drug_catalog.nfi_harvest_service import RETRYABLE
    from shared.models.harvest_failure import HarvestFailure
    rows = (await db.execute(
        select(HarvestFailure.category,
               func.count().label("n"),
               func.min(HarvestFailure.page_id),
               func.max(HarvestFailure.page_id))
        .where(HarvestFailure.crawler == "nfi",
               HarvestFailure.resolved_at.is_(None))
        .group_by(HarvestFailure.category).order_by(func.count().desc()))).all()
    resolved = (await db.execute(select(func.count()).select_from(HarvestFailure)
                                 .where(HarvestFailure.resolved_at.isnot(None)))).scalar() or 0
    by_cat = [{"category": c, "count": n, "min_id": lo, "max_id": hi,
               "retryable": c in RETRYABLE} for c, n, lo, hi in rows]
    return {"pending": sum(x["count"] for x in by_cat),
            "retryable": sum(x["count"] for x in by_cat if x["retryable"]),
            "resolved": resolved, "by_category": by_cat}


class NfiRetryRequest(BaseModel):
    proxy: str | None = None
    delay: float = 0.0
    limit: int = 20000


@router.post("/catalog/nfi/retry-failed")
async def nfi_retry_failed(body: NfiRetryRequest,
                           staff: Staff = Depends(require_permission("inventory:write"))):
    """Re-harvest exactly the pages previously lost to transport failures."""
    from services.core.drug_catalog import nfi_harvest_service as svc
    ids = await svc.pending_failures("nfi")
    if not ids:
        raise HTTPException(status_code=409, detail="صفحهٔ ناموفقی برای تلاش دوباره نیست.")
    ids = ids[:max(1, body.limit)]
    try:
        return {**svc.start(min(ids), max(ids), delay=body.delay, proxy=body.proxy,
                            page_ids=ids), "retrying": len(ids)}
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


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
        from services.core.drug_catalog.crosswalk import load_overrides
        n = await upsert_catalog(db, records, source=f"import:{file.filename}",
                                 overrides=await load_overrides(db))
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


# ── دارونامه coverage sources & staged runs ───────────────────────────────────
class CoverageSourceIn(BaseModel):
    insurer: str
    name: str
    url: str | None = None
    strategy: str = "auto"
    settings: dict | None = None
    check_interval_days: int = 7
    enabled: bool = True


class ApproveRunRequest(BaseModel):
    remove_missing: bool = False
    accepted_review_ids: list[int] = []
    # review-id → {code, note}: WHY a pair was refused. Codes feed هوش تطبیق as
    # feature-targeted negatives; notes are audit context.
    reject_reasons: dict[str, dict] = {}


def _source_json(s, lock_holder: str | None) -> dict:
    from datetime import datetime, timezone
    due = bool(s.enabled and (
        s.last_run_at is None or
        (datetime.now(timezone.utc) - s.last_run_at).days >= s.check_interval_days))
    return {"id": str(s.id), "insurer": s.insurer, "name": s.name, "url": s.url,
            "strategy": s.strategy, "settings": s.settings or {},
            "check_interval_days": s.check_interval_days, "enabled": s.enabled,
            "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None,
            "last_run_status": s.last_run_status, "due": due,
            "lock_holder": lock_holder}


@router.get("/coverage/sources")
async def list_coverage_sources(staff: Staff = Depends(require_permission("inventory:read")),
                                db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    from shared.models.coverage import CoverageSource
    from services.core.drug_catalog import harvest_lock
    rows = (await db.execute(select(CoverageSource)
                             .order_by(CoverageSource.created_at))).scalars().all()
    return {"sources": [_source_json(s, harvest_lock.holder()) for s in rows]}


@router.post("/coverage/sources")
async def create_coverage_source(body: CoverageSourceIn,
                                 staff: Staff = Depends(require_permission("inventory:write")),
                                 db: AsyncSession = Depends(get_db)):
    from shared.models.coverage import CoverageSource
    from services.core.drug_catalog import harvest_lock
    s = CoverageSource(**body.model_dump())
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return _source_json(s, harvest_lock.holder())


@router.put("/coverage/sources/{source_id}")
async def update_coverage_source(source_id: UUID, body: CoverageSourceIn,
                                 staff: Staff = Depends(require_permission("inventory:write")),
                                 db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    from shared.models.coverage import CoverageSource
    from services.core.drug_catalog import harvest_lock
    s = (await db.execute(select(CoverageSource)
                          .where(CoverageSource.id == source_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Source not found")
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    await db.commit()
    await db.refresh(s)
    return _source_json(s, harvest_lock.holder())


@router.delete("/coverage/sources/{source_id}")
async def delete_coverage_source(source_id: UUID,
                                 staff: Staff = Depends(require_permission("inventory:write")),
                                 db: AsyncSession = Depends(get_db)):
    from sqlalchemy import delete as sa_delete, select
    from shared.models.coverage import CoverageRun, CoverageSource
    s = (await db.execute(select(CoverageSource)
                          .where(CoverageSource.id == source_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Source not found")
    await db.execute(sa_delete(CoverageRun).where(CoverageRun.source_id == source_id))
    await db.delete(s)
    await db.commit()
    return {"deleted": True}


@router.post("/coverage/sources/{source_id}/probe")
async def probe_coverage_source(source_id: UUID,
                                staff: Staff = Depends(require_permission("inventory:write")),
                                db: AsyncSession = Depends(get_db)):
    import asyncio
    from sqlalchemy import select
    from shared.models.coverage import CoverageSource
    from services.core.drug_catalog import coverage_harvest as ch
    from services.core.drug_catalog.harvest_diagnostics import DiagnosticRecorder
    s = (await db.execute(select(CoverageSource)
                          .where(CoverageSource.id == source_id))).scalar_one_or_none()
    if not s or not s.url:
        raise HTTPException(status_code=404, detail="Source (or its URL) not found")
    recorder = DiagnosticRecorder("coverage", f"{s.insurer}-probe", mode="all")
    fetch = ch.make_fetcher((s.settings or {}).get("proxy"), recorder=recorder)
    try:
        return await asyncio.to_thread(ch.probe_payload, s.url,
                                       settings=s.settings or {}, fetch=fetch)
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail={
            "message": str(e), "diagnostics": recorder.to_db()})
    finally:
        recorder.close()


@router.post("/coverage/sources/{source_id}/harvest")
async def start_coverage_harvest(source_id: UUID,
                                 staff: Staff = Depends(require_permission("inventory:write")),
                                 db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    from shared.models.coverage import CoverageSource
    from services.core.drug_catalog import coverage_harvest as ch
    s = (await db.execute(select(CoverageSource)
                          .where(CoverageSource.id == source_id))).scalar_one_or_none()
    if not s or not s.url:
        raise HTTPException(status_code=404, detail="Source (or its URL) not found")
    try:
        return ch.start_harvest(s.id, s.insurer)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/catalog/integrity")
async def catalog_integrity(staff: Staff = Depends(require_permission("inventory:read")),
                            db: AsyncSession = Depends(get_db)):
    """Spliced-monograph audit: NFI rows whose product block contradicts their
    monograph (reused generic-entity ids on legacy pages), with a deterministic
    repair proposal per row (donor sibling or the brand string itself)."""
    from services.core.drug_catalog.nfi_integrity import audit
    return await audit(db)


class IntegrityApplyRequest(BaseModel):
    ircs: list[str]


@router.post("/catalog/integrity/apply")
async def catalog_integrity_apply(body: IntegrityApplyRequest,
                                  staff: Staff = Depends(require_permission("inventory:write")),
                                  db: AsyncSession = Depends(get_db)):
    """Apply proposals for the accepted IRCs: each repaired field becomes a
    field_override (a re-crawl of the still-broken page cannot re-poison it),
    ingredient_key is recomputed, the foreign monograph text is dropped."""
    from services.core.drug_catalog.nfi_integrity import apply_repairs
    return await apply_repairs(db, body.ircs, staff_id=getattr(staff, "id", None))


@router.post("/coverage/upload-run")
async def coverage_upload_run(files: list[UploadFile] = File(...),
                              insurer: str = "tamin",
                              force: bool = False,
                              staff: Staff = Depends(require_permission("inventory:write")),
                              db: AsyncSession = Depends(get_db)):
    """Staged manual upload: extract rows from one or MORE files of the same
    دارونامه (e.g. tamin's .json + .csv — merged by the insurer's drug code,
    empty fields filled across files), then run the full staging pipeline
    (crosswalk → national-code registry → linking → snapshots) as a background
    CoverageRun for review — identical to a crawl, minus the network."""
    from services.core.drug_catalog import coverage_harvest as ch

    ok_suffixes = (".xlsx", ".xlsm", ".xls", ".csv", ".tsv", ".html", ".htm", ".json")
    row_sets, names, per_file = [], [], []
    for f in files:
        suffix = os.path.splitext(f.filename or "")[1].lower()
        if suffix and suffix not in ok_suffixes:
            raise HTTPException(status_code=400,
                                detail=f"قالب پشتیبانی‌نشده: {f.filename}")
        body = await f.read()
        rows = ch.rows_from_upload(f.filename or "", body)
        row_sets.append(rows)
        names.append(f.filename or "file")
        per_file.append({"file": f.filename, "rows": len(rows)})
    merged = ch.merge_row_sets(row_sets)
    if not merged:
        raise HTTPException(status_code=422,
                            detail={"message": "هیچ ردیفی از فایل(ها) استخراج نشد.",
                                    "files": per_file})
    try:
        state = await ch.start_upload(db, insurer, merged, names, force=force)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"state": state, "files": per_file, "merged_rows": len(merged)}


@router.get("/issues/board")
async def issues_board(include_closed: bool = True,
                       staff: Staff = Depends(require_permission("inventory:read")),
                       db: AsyncSession = Depends(get_db)):
    """ناسازگاری‌ها as a managed backlog: every ROOT CAUSE with its count, the
    lane that can close it, the recommended action, and the owner's ruling.
    Replaces an unbounded flat list (~45k item-issues re-derived every run) with
    ~13 causes the owner can actually act on."""
    from services.core.drug_catalog.issue_registry import board
    return await board(db, include_closed=include_closed)


class IssueRulingIn(BaseModel):
    cause: str
    disposition: str                      # accepted | wont_fix | resolved | deferred
    reason: str | None = None
    subject_key: str = "*"                # '*' = the whole cause


@router.post("/issues/ruling")
async def issues_ruling(body: IssueRulingIn,
                        staff: Staff = Depends(require_permission("inventory:write")),
                        db: AsyncSession = Depends(get_db)):
    """Record the owner's ruling so an accepted-as-normal group leaves the OPEN
    list permanently — this is what lets the backlog converge instead of
    re-reporting the same structurally-normal rows after every harvest."""
    from services.core.drug_catalog.issue_registry import CAUSES, set_disposition
    if body.cause not in CAUSES:
        raise HTTPException(status_code=400, detail=f"علت ناشناخته: {body.cause}")
    try:
        res = await set_disposition(
            db, issue_type=body.cause, subject_key=body.subject_key,
            disposition=body.disposition, reason=body.reason,
            details={"lane": CAUSES[body.cause]["lane"]},
            staff_id=getattr(staff, "id", None))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.commit()
    return {"result": res, "cause": body.cause, "disposition": body.disposition}


@router.delete("/issues/ruling/{cause}")
async def issues_ruling_clear(cause: str, subject_key: str = "*",
                              staff: Staff = Depends(require_permission("inventory:write")),
                              db: AsyncSession = Depends(get_db)):
    """Re-open a cause (delete its ruling)."""
    from sqlalchemy import delete
    from shared.models.issue_disposition import IssueDisposition
    n = (await db.execute(delete(IssueDisposition).where(
        IssueDisposition.issue_type == cause,
        IssueDisposition.subject_key == subject_key))).rowcount
    await db.commit()
    return {"reopened": n}


@router.get("/coverage/harvest/status")
async def coverage_harvest_status(staff: Staff = Depends(require_permission("inventory:read"))):
    from services.core.drug_catalog import coverage_harvest as ch
    return ch.status()


@router.get("/coverage/runs")
async def list_coverage_runs(source_id: UUID | None = None, limit: int = 20,
                             staff: Staff = Depends(require_permission("inventory:read")),
                             db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    from shared.models.coverage import CoverageRun
    q = select(CoverageRun).order_by(CoverageRun.started_at.desc()).limit(min(limit, 100))
    if source_id:
        q = q.where(CoverageRun.source_id == source_id)
    rows = (await db.execute(q)).scalars().all()
    return {"runs": [{"id": str(r.id), "source_id": str(r.source_id), "insurer": r.insurer,
                      "status": r.status,
                      "started_at": r.started_at.isoformat() if r.started_at else None,
                      "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                      "stats": r.stats, "diff_counts": {k: (r.diff or {}).get(k)
                                                        for k in ("added", "changed", "removed")},
                      "diag_summary": (r.diagnostics or {}).get("summary") if r.diagnostics else None,
                      "error": r.error} for r in rows]}


@router.get("/coverage/runs/{run_id}")
async def get_coverage_run(run_id: UUID,
                           staff: Staff = Depends(require_permission("inventory:read")),
                           db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    from shared.models.coverage import CoverageRun
    r = (await db.execute(select(CoverageRun)
                          .where(CoverageRun.id == run_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"id": str(r.id), "source_id": str(r.source_id), "insurer": r.insurer,
            "status": r.status, "stats": r.stats, "diff": r.diff,
            "review": r.review, "unmatched": r.unmatched, "error": r.error,
            "diagnostics": r.diagnostics,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None}


@router.post("/coverage/runs/{run_id}/approve")
async def approve_coverage_run(run_id: UUID, body: ApproveRunRequest,
                               staff: Staff = Depends(require_permission("inventory:write")),
                               db: AsyncSession = Depends(get_db)):
    from services.core.drug_catalog import coverage_harvest as ch
    try:
        return await ch.apply_run(db, run_id, remove_missing=body.remove_missing,
                                  accepted_review_ids=body.accepted_review_ids,
                                  reject_reasons=body.reject_reasons,
                                  staff_id=staff.id)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


# Fields an owner may correct on an NFI catalog row. Deliberately excludes irc
# (identity) and ingredient_key (derived) — those are recomputed, never typed.
CATALOG_EDITABLE = (
    "name_fa", "name_en", "generic_name", "dosage_form", "strength", "brand_name",
    "manufacturer", "atc", "package_count", "gtin", "erx_code", "country",
    "license_owner", "brand_owner", "license_valid_until", "category",
    "is_generic", "is_otc", "announced_price", "last_invoice_price",
)


def _catalog_json(it) -> dict:
    out = {f: getattr(it, f, None) for f in
           ("irc", *CATALOG_EDITABLE, "ingredient_key", "coverage", "source",
            "announced_price_at", "last_invoice_at")}
    # surface price provenance so an insurer-derived figure is visibly NOT an
    # NFI-verified consumer price (see sync_service._apply_to_catalog)
    mono = getattr(it, "monograph", None)
    if isinstance(mono, dict) and isinstance(mono.get("price_provenance"), dict):
        out["price_provenance"] = mono["price_provenance"]
    return out


@router.get("/catalog/items")
async def catalog_items(q: str | None = None, limit: int = 50, offset: int = 0,
                        missing: str | None = None,
                        staff: Staff = Depends(require_permission("inventory:read")),
                        db: AsyncSession = Depends(get_db)):
    """Browse the NFI catalog with every column the owner may need to audit.
    `q` matches IRC / Persian name / generic; `missing` filters rows lacking a
    field (price|generic|country|atc|form|strength) — the usual gap hunt."""
    from sqlalchemy import func, or_, select
    from shared.models.drug_catalog import DrugCatalogItem as D

    stmt = select(D)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(D.irc.ilike(like), D.name_fa.ilike(like),
                              D.generic_name.ilike(like), D.brand_name.ilike(like)))
    gap = {"price": D.announced_price.is_(None),
           "generic": (D.generic_name.is_(None)) | (func.btrim(D.generic_name) == ""),
           "country": D.country.is_(None), "atc": D.atc.is_(None),
           "form": (D.dosage_form.is_(None)) | (func.btrim(D.dosage_form) == ""),
           "strength": (D.strength.is_(None)) | (func.btrim(D.strength) == "")}
    if missing in gap:
        stmt = stmt.where(gap[missing])
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    rows = (await db.execute(
        stmt.order_by(D.name_fa).limit(min(limit, 200)).offset(max(offset, 0)))).scalars().all()
    return {"total": total, "limit": limit, "offset": offset,
            "items": [_catalog_json(r) for r in rows]}


class CatalogEditRequest(BaseModel):
    fields: dict
    reason: str | None = None
    # persist as durable overrides so the next NFI crawl cannot erase the fix
    persist: bool = True


@router.get("/crosswalk")
async def crosswalk_list(insurer: str | None = None, status: str | None = None,
                         limit: int = 200,
                         staff: Staff = Depends(require_permission("inventory:read")),
                         db: AsyncSession = Depends(get_db)):
    """Durable owner decisions: which insurer row IS which product."""
    from sqlalchemy import func, select
    from shared.models.crosswalk import CrosswalkEntry as C
    q = select(C).order_by(C.decided_at.desc().nullslast())
    if insurer:
        q = q.where(C.insurer == insurer)
    if status:
        q = q.where(C.status == status)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    rows = (await db.execute(q.limit(min(limit, 500)))).scalars().all()
    return {"total": total, "entries": [
        {"id": str(e.id), "insurer": e.insurer, "source_code": e.source_code,
         "raw_key": e.raw_key, "raw_name": e.raw_name, "irc": e.irc,
         "status": e.status, "reason": e.reason,
         "decided_at": e.decided_at.isoformat() if e.decided_at else None} for e in rows]}


@router.get("/overrides")
async def overrides_list(staff: Staff = Depends(require_permission("inventory:read")),
                         db: AsyncSession = Depends(get_db)):
    """Field corrections that re-apply after every crawl."""
    from sqlalchemy import select
    from shared.models.crosswalk import FieldOverride as F
    rows = (await db.execute(select(F).order_by(F.decided_at.desc().nullslast())
                             .limit(500))).scalars().all()
    return {"total": len(rows), "overrides": [
        {"id": str(o.id), "irc": o.irc, "field": o.field, "value": o.value,
         "reason": o.reason,
         "decided_at": o.decided_at.isoformat() if o.decided_at else None} for o in rows]}


@router.post("/canonical/export")
async def canonical_export(staff: Staff = Depends(require_permission("inventory:write")),
                           db: AsyncSession = Depends(get_db)):
    """Write the canonical bundle (catalog + resolved formularies + crosswalk +
    overrides + current prices) to data/canonical/ with a checksummed manifest."""
    from services.core.drug_catalog.canonical_export import export_bundle
    return await export_bundle(db)


@router.post("/canonical/import-decided")
async def canonical_import(staff: Staff = Depends(require_permission("inventory:write")),
                           db: AsyncSession = Depends(get_db)):
    """Re-seed the DECIDED layer (crosswalk + overrides) from data/canonical/.
    Observed data always re-enters through the normal import flows."""
    from services.core.drug_catalog.canonical_export import import_decided
    return await import_decided(db)


@router.delete("/overrides/{override_id}")
async def override_delete(override_id: UUID,
                          staff: Staff = Depends(require_permission("inventory:write")),
                          db: AsyncSession = Depends(get_db)):
    """Drop an override — the field reverts to whatever the source publishes."""
    from sqlalchemy import delete
    from shared.models.crosswalk import FieldOverride as F
    res = await db.execute(delete(F).where(F.id == override_id))
    await db.commit()
    return {"deleted": res.rowcount}


@router.patch("/catalog/items/{irc}")
async def catalog_edit(irc: str, body: CatalogEditRequest,
                       staff: Staff = Depends(require_permission("inventory:write")),
                       db: AsyncSession = Depends(get_db)):
    """Correct an NFI catalog row. Only whitelisted fields; ingredient_key is
    recomputed when identity fields change; price edits append a dated point to
    price_history so the correction is auditable like any other price move."""
    from sqlalchemy import select
    from shared.models.drug_catalog import DrugCatalogItem
    from services.core.drug_catalog.price_history import record_price
    from services.core.drug_catalog.schema import ingredient_key

    it = (await db.execute(select(DrugCatalogItem)
                           .where(DrugCatalogItem.irc == irc))).scalar_one_or_none()
    if it is None:
        raise HTTPException(status_code=404, detail="قلم یافت نشد")

    changed: dict = {}
    for f, v in (body.fields or {}).items():
        if f not in CATALOG_EDITABLE:
            continue
        old = getattr(it, f, None)
        if f in ("announced_price", "last_invoice_price", "package_count"):
            v = None if v in (None, "") else int(float(v))
        elif f in ("is_generic", "is_otc"):
            v = bool(v)
        elif v is not None:
            v = str(v).strip() or None
        if (str(old) if old is not None else None) != (str(v) if v is not None else None):
            setattr(it, f, v)
            changed[f] = {"from": old, "to": v}
    if not changed:
        return {"irc": irc, "changed": {}, "note": "تغییری اعمال نشد"}

    if {"generic_name", "strength", "dosage_form"} & set(changed):
        it.ingredient_key = ingredient_key(it.generic_name or "", it.strength or "",
                                           it.dosage_form or "")
    it.source = "manual-edit"
    for f, ptype in (("announced_price", "announced"), ("last_invoice_price", "invoice")):
        if f in changed and changed[f]["to"]:
            await record_price(db, irc, ptype, changed[f]["to"], source="manual-edit")
    # X3: record the correction as policy, so the next NFI crawl re-applies it
    # instead of silently restoring the source's wrong value.
    persisted = 0
    if body.persist:
        from services.core.drug_catalog.crosswalk import set_override
        for f, d in changed.items():
            await set_override(db, irc, f, d["to"], reason=body.reason, staff_id=staff.id)
            persisted += 1
    await db.commit()
    return {"irc": irc, "changed": changed, "reason": body.reason,
            "overrides_persisted": persisted}


class TaminHarvestRequest(BaseModel):
    input_html: str | None = None
    delay: float = 1
    timeout: int = 120
    max_retries: int = 20
    retry_delay: int = 5
    pages: str | None = None


@router.post("/coverage/tamin-harvest/start")
async def tamin_harvest_start(body: TaminHarvestRequest,
                              staff: Staff = Depends(require_permission("inventory:write"))):
    """Run the تأمین اجتماعی formulary harvester (DevExpress callback replay) as
    a supervised background subprocess. --resume continues an interrupted run."""
    from services.core.drug_catalog import tamin_harvest as th
    try:
        return th.start(input_html=body.input_html, delay=body.delay, timeout=body.timeout,
                        max_retries=body.max_retries, retry_delay=body.retry_delay,
                        pages=body.pages)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/coverage/tamin-harvest/status")
async def tamin_harvest_status(staff: Staff = Depends(require_permission("inventory:read"))):
    from services.core.drug_catalog import tamin_harvest as th
    return {**th.status(), "outputs": th.output_info()}


@router.post("/coverage/tamin-harvest/stop")
async def tamin_harvest_stop(staff: Staff = Depends(require_permission("inventory:write"))):
    from services.core.drug_catalog import tamin_harvest as th
    try:
        return th.stop()
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


class PriceRefreshRequest(BaseModel):
    min_confidence: float = 0.85
    min_pct: float = 25.0
    # the insurer figure is a capped acceptance amount, so by default only
    # UPWARD refreshes are proposed (a stale NFI price) — see sync_service
    only_increases: bool = True


@router.post("/coverage/runs/{run_id}/propose-prices")
async def propose_prices_from_run(run_id: UUID, body: PriceRefreshRequest,
                                  staff: Staff = Depends(require_permission("inventory:write")),
                                  db: AsyncSession = Depends(get_db)):
    """Price-refresh: turn this run's high-confidence matched insurer prices into
    price proposals (reviewed & applied via the existing proposals flow, dated in
    price_history). Nothing changes catalog prices without approval."""
    from services.core.drug_catalog import sync_service
    return await sync_service.propose_prices_from_run(
        db, run_id, min_confidence=body.min_confidence, min_pct=body.min_pct,
        only_increases=body.only_increases)


@router.post("/coverage/runs/{run_id}/reject")
async def reject_coverage_run(run_id: UUID,
                              staff: Staff = Depends(require_permission("inventory:write")),
                              db: AsyncSession = Depends(get_db)):
    from services.core.drug_catalog import coverage_harvest as ch
    try:
        await ch.reject_run(db, run_id)
        return {"rejected": True}
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/inconsistencies")
async def pricing_inconsistencies(
        insurer: str = "salamat", price_threshold_pct: float = 25.0,
        staff: Staff = Depends(require_permission("inventory:read")),
        db: AsyncSession = Depends(get_db)):
    """Read-only data-quality review surface. Surfaces, for one insurer:
    coverage problems (the latest non-failed run's unmatched + uncertain-review
    queues, plus catalog rows where the insurer reference price diverges from
    the announced NFI price) and NFI catalog gaps (missing price/generic/
    country/atc). No writes."""
    from sqlalchemy import select, func
    from shared.models.coverage import CoverageRun
    from shared.models.drug_catalog import DrugCatalogItem
    from services.core.drug_catalog.coverage_harvest import price_conflict

    # ── coverage: latest non-failed run's staged unmatched + review queues ──
    run = (await db.execute(
        select(CoverageRun)
        .where(CoverageRun.insurer == insurer)
        .where(CoverageRun.status.in_(("parsed", "approved")))
        .order_by(CoverageRun.started_at.desc())
        .limit(1))).scalar_one_or_none()
    unmatched = list(run.unmatched or [])[:200] if run else []
    review = list(run.review or []) if run else []

    # ── coverage: reference-vs-announced price conflicts across the catalog ──
    rows = (await db.execute(
        select(DrugCatalogItem.irc, DrugCatalogItem.name_fa,
               DrugCatalogItem.announced_price, DrugCatalogItem.coverage)
        .where(DrugCatalogItem.coverage.has_key(insurer))
        .where(DrugCatalogItem.announced_price.isnot(None))
        .where(DrugCatalogItem.coverage[insurer].has_key("reference_price")))).all()
    conflicts = []
    for irc, name_fa, announced, coverage in rows:
        entry = (coverage or {}).get(insurer) or {}
        c = price_conflict(irc, name_fa, announced, entry.get("reference_price"),
                           price_threshold_pct)
        if c:
            conflicts.append(c)
    conflicts.sort(key=lambda c: abs(c["gap_pct"]), reverse=True)
    conflicts = conflicts[:500]

    # ── nfi: catalog completeness gaps ──
    async def _count(cond):
        return int((await db.execute(
            select(func.count()).select_from(DrugCatalogItem).where(cond))).scalar() or 0)

    async def _sample(cond):
        rs = (await db.execute(
            select(DrugCatalogItem.irc, DrugCatalogItem.name_fa)
            .where(cond).limit(100))).all()
        return [{"irc": i, "name_fa": n} for i, n in rs]

    no_price_c = DrugCatalogItem.announced_price.is_(None)
    no_generic_c = (DrugCatalogItem.generic_name.is_(None)) | (
        func.btrim(DrugCatalogItem.generic_name) == "")
    no_country_c = DrugCatalogItem.country.is_(None)
    no_atc_c = DrugCatalogItem.atc.is_(None)
    total = int((await db.execute(
        select(func.count()).select_from(DrugCatalogItem))).scalar() or 0)

    return {
        "insurer": insurer,
        "coverage": {
            "counts": {"unmatched": len(unmatched), "review": len(review),
                       "price_conflicts": len(conflicts)},
            "unmatched": unmatched,
            "review": review,
            "price_conflicts": conflicts,
        },
        "nfi": {
            "counts": {
                "no_price": await _count(no_price_c),
                "no_generic": await _count(no_generic_c),
                "no_country": await _count(no_country_c),
                "no_atc": await _count(no_atc_c),
                "total": total,
            },
            "no_price": await _sample(no_price_c),
            "no_generic": await _sample(no_generic_c),
            "no_country": await _sample(no_country_c),
            "no_atc": await _sample(no_atc_c),
        },
    }


@router.get("/inconsistencies/drug/{irc}")
async def inconsistency_drug_detail(
        irc: str, insurer: str = "salamat",
        staff: Staff = Depends(require_permission("inventory:read")),
        db: AsyncSession = Depends(get_db)):
    """Read-only per-drug workbench detail: the NFI catalog record joined to ALL
    insurers' coverage entries + same-generic siblings + Persian root-cause hints
    for the selected insurer's entry. Diagnoses price conflicts (e.g. a reference
    price inherited from a different-strength sibling). Selects only, no writes.
    Unknown IRC → 200 with catalog null and empty coverage/siblings/analysis."""
    from sqlalchemy import select
    from shared.models.drug_catalog import DrugCatalogItem
    from services.core.drug_catalog.coverage_harvest import diagnose_discrepancy

    def _int(v):
        return int(v) if v is not None else None

    item = (await db.execute(
        select(DrugCatalogItem).where(DrugCatalogItem.irc == irc))).scalar_one_or_none()
    if item is None:
        return {"irc": irc, "catalog": None, "coverage": {},
                "siblings": [], "analysis": []}

    catalog = {
        "irc": item.irc,
        "name_fa": item.name_fa,
        "name_en": item.name_en,
        "generic_name": item.generic_name,
        "ingredient_key": item.ingredient_key,
        "strength": item.strength,
        "dosage_form": item.dosage_form,
        "brand_name": item.brand_name,
        "manufacturer": item.manufacturer,
        "country": item.country,
        "atc": item.atc,
        "announced_price": _int(item.announced_price),
        "package_count": item.package_count,
        "gtin": item.gtin,
        "source": item.source,
    }

    # ── coverage: every insurer entry present on this drug, normalized ──
    coverage = {}
    for ins, entry in (item.coverage or {}).items():
        entry = entry or {}
        coverage[ins] = {
            "covered": entry.get("covered"),
            "share_pct": entry.get("share_pct"),
            "reference_price": entry.get("reference_price"),
            "ceiling": entry.get("ceiling"),
            "inpatient": entry.get("inpatient"),
            "match_confidence": entry.get("match_confidence"),
            "match_method": entry.get("match_method"),
        }

    # ── siblings: same generic, other IRCs — reveals cross-strength mislinks ──
    siblings = []
    if item.generic_name:
        sib_rows = (await db.execute(
            select(DrugCatalogItem.irc, DrugCatalogItem.name_fa,
                   DrugCatalogItem.strength, DrugCatalogItem.dosage_form,
                   DrugCatalogItem.announced_price, DrugCatalogItem.coverage)
            .where(DrugCatalogItem.generic_name == item.generic_name)
            .where(DrugCatalogItem.irc != item.irc)
            .order_by(DrugCatalogItem.announced_price.desc().nullslast())
            .limit(25))).all()
        for s_irc, s_name, s_strength, s_form, s_ann, s_cov in sib_rows:
            s_ref = ((s_cov or {}).get(insurer) or {}).get("reference_price")
            siblings.append({
                "irc": s_irc,
                "name_fa": s_name,
                "strength": s_strength,
                "dosage_form": s_form,
                "announced_price": _int(s_ann),
                "reference_price": _int(s_ref),
            })

    analysis = diagnose_discrepancy(catalog, coverage.get(insurer) or {}, siblings)

    return {"irc": irc, "catalog": catalog, "coverage": coverage,
            "siblings": siblings, "analysis": analysis}


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


# ── هوش‌یار دارو — drug enrichment intelligence ──────────────────────────────
class EnrichmentRunRequest(BaseModel):
    limit: int = 50
    min_confidence: float = 0.7
    workers: int = 5             # concurrent researchers, clamped 1-15 in service
    provider: str = "mistral"    # web-searching backend: mistral | gemini
    refresh: bool = False        # re-research existing 'suggested' rows w/ latest rules


class EnrichmentDecideRequest(BaseModel):
    ids: list[str]
    approve: bool = True


@router.get("/enrichment/worklist")
async def enrichment_worklist(insurer: str | None = None, min_confidence: float = 0.7,
                              staff: Staff = Depends(require_permission("inventory:read")),
                              db: AsyncSession = Depends(get_db)):
    """Deterministic research worklist (deduped by spelling-proof key). Optional
    `insurer` filters to one insurer's items. Returns items + per-reason counts."""
    from services.core.drug_catalog.enrichment import build_worklist
    items = await build_worklist(db, min_confidence=min_confidence)
    if insurer:
        items = [i for i in items if i.get("insurer") == insurer]
    counts: dict[str, int] = {}
    for i in items:
        counts[i["reason"]] = counts.get(i["reason"], 0) + 1
    return {"total": len(items), "counts": counts, "items": items[:1000]}


@router.post("/enrichment/run")
async def enrichment_run(body: EnrichmentRunRequest,
                         staff: Staff = Depends(require_permission("inventory:write"))):
    """Start a background web-research batch over the worklist. Non-blocking;
    poll /enrichment/run/status. Produces status='suggested' rows only."""
    from services.ai.enrichment import service as es
    try:
        return es.start_batch_background(limit=body.limit, min_confidence=body.min_confidence,
                                         workers=body.workers, provider=body.provider,
                                         refresh=body.refresh)
    except ValueError as e:                       # unknown/non-searching provider
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/enrichment/run/status")
async def enrichment_run_status(staff: Staff = Depends(require_permission("inventory:read"))):
    from services.ai.enrichment import service as es
    from services.ai.enrichment.providers import search_backend_name
    return {**es.status(), "search_backend": search_backend_name()}


@router.post("/enrichment/run/stop")
async def enrichment_run_stop(staff: Staff = Depends(require_permission("inventory:write"))):
    """Cancel the running research batch. Saved suggestions are kept;
    unresearched items stay in the worklist for the next run."""
    from services.ai.enrichment import service as es
    try:
        return es.stop_batch()
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


class RetrainRequest(BaseModel):
    mode: str = "decisions"      # decisions (strict labels) | bootstrap (all formulary data)


@router.post("/match-intel/retrain")
async def match_intel_retrain(body: RetrainRequest,
                              staff: Staff = Depends(require_permission("inventory:write")),
                              db: AsyncSession = Depends(get_db)):
    """Refit هوش تطبیق. mode='decisions': the owner's accepted/rejected review
    pairs only. mode='bootstrap': weak supervision over ALL formulary rows
    (linker's high-confidence pairings as positives). Both learn price bands
    from live approved coverage. Saves the versioned model artifact."""
    if body.mode not in ("decisions", "bootstrap"):
        raise HTTPException(status_code=400, detail="mode must be decisions|bootstrap")
    from services.core.drug_catalog.match_intel import fit_from_db
    m = await fit_from_db(db, mode=body.mode)
    return {"mode": body.mode, "fitted_at": m["fitted_at"], "pairs": m["counts"],
            "fs_armed": m.get("fs_armed", False),
            "price_bands": {k: v.get("n") for k, v in (m.get("price_bands") or {}).items()}}


@router.get("/match-intel/status")
async def match_intel_status(staff: Staff = Depends(require_permission("inventory:read"))):
    from services.core.drug_catalog.match_intel import load_model
    m = load_model()
    if not m:
        return {"fitted": False}
    return {"fitted": True, "fitted_at": m.get("fitted_at"), "counts": m.get("counts"),
            "price_bands": {k: v.get("n") for k, v in (m.get("price_bands") or {}).items()}}


@router.post("/enrichment/provider-test")
async def enrichment_provider_test(body: EnrichmentRunRequest,
                                   staff: Staff = Depends(require_permission("inventory:write"))):
    """Live connection test for the selected provider — a real API request
    decides validity, never a key-prefix heuristic."""
    import asyncio as _aio
    from services.ai.enrichment import providers as ep
    try:
        return await _aio.to_thread(ep.test_provider, body.provider)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/enrichment/suggestions")
async def enrichment_suggestions(status: str = "suggested", limit: int = 500,
                                 staff: Staff = Depends(require_permission("inventory:read")),
                                 db: AsyncSession = Depends(get_db)):
    """List enrichment rows for review. status='all' returns every status."""
    from services.core.drug_catalog.enrichment import list_enrichments
    rows = await list_enrichments(db, status=(None if status == "all" else status), limit=limit)
    return {"status": status, "count": len(rows), "suggestions": rows}


@router.post("/enrichment/decide")
async def enrichment_decide(body: EnrichmentDecideRequest,
                            staff: Staff = Depends(require_permission("inventory:write")),
                            db: AsyncSession = Depends(get_db)):
    """Approve or reject researched enrichment rows. Approved rows self-apply at
    the next harvest/ingest."""
    from services.core.drug_catalog.enrichment import decide_enrichments
    ids: list = []
    for raw in body.ids:
        try:
            ids.append(UUID(raw))
        except (ValueError, AttributeError):
            continue
    return await decide_enrichments(db, ids, approve=body.approve, staff_id=staff.id)


@router.post("/price-history/backfill")
async def price_history_backfill(staff: Staff = Depends(require_permission("inventory:write")),
                                 db: AsyncSession = Depends(get_db)):
    """Seed the price time-series from today's catalog prices (one-time / idempotent)."""
    from services.core.drug_catalog.price_history import backfill_from_catalog
    return await backfill_from_catalog(db)


@router.get("/price-history/stale")
async def price_history_stale(max_age_days: int = 180, price_type: str = "announced",
                              staff: Staff = Depends(require_permission("inventory:read")),
                              db: AsyncSession = Depends(get_db)):
    """Products whose current price is older than max_age_days — the structural
    replacement for stale-price re-crawl guesswork."""
    from services.core.drug_catalog.price_history import stale_prices
    return await stale_prices(db, max_age_days=max_age_days, price_type=price_type)


@router.get("/price-history/{irc}")
async def price_history_for(irc: str,
                            staff: Staff = Depends(require_permission("inventory:read")),
                            db: AsyncSession = Depends(get_db)):
    """Full dated price series for one product (newest first)."""
    from sqlalchemy import select
    from shared.models.price_history import PriceHistory
    rows = (await db.execute(
        select(PriceHistory).where(PriceHistory.irc == irc)
        .order_by(PriceHistory.valid_from.desc()).limit(200))).scalars().all()
    return {"irc": irc, "history": [
        {"price_type": r.price_type, "insurer": r.insurer, "value": r.value,
         "source": r.source, "current": r.valid_to is None,
         "valid_from": r.valid_from.isoformat() if r.valid_from else None,
         "valid_to": r.valid_to.isoformat() if r.valid_to else None} for r in rows]}


class MarkBulkRequest(BaseModel):
    names: list[str]


@router.post("/enrichment/mark-bulk")
async def enrichment_mark_bulk(body: MarkBulkRequest,
                               staff: Staff = Depends(require_permission("inventory:write")),
                               db: AsyncSession = Depends(get_db)):
    """Owner confirms formulary rows as compounding raw ingredients (item_kind=
    'bulk', approved) — kept out of drug matching thereafter."""
    from services.core.drug_catalog.enrichment import mark_bulk
    return await mark_bulk(db, body.names, staff_id=staff.id)


@router.post("/enrichment/export")
async def enrichment_export(staff: Staff = Depends(require_permission("inventory:write")),
                            db: AsyncSession = Depends(get_db)):
    """Write every approved row to the committed canonical artifact
    (data/reference/drug_enrichments.json). Returns the exported count."""
    from services.core.drug_catalog.enrichment import export_reference, REFERENCE_PATH
    n = await export_reference(db)
    return {"exported": n, "path": str(REFERENCE_PATH)}


@router.post("/enrichment/import")
async def enrichment_import(staff: Staff = Depends(require_permission("inventory:write")),
                            db: AsyncSession = Depends(get_db)):
    """Upsert the committed canonical artifact's entries as approved rows."""
    from services.core.drug_catalog.enrichment import import_reference, REFERENCE_PATH
    n = await import_reference(db)
    return {"imported": n, "path": str(REFERENCE_PATH)}
