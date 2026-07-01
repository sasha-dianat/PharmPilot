"""Pricing & affordability-quote endpoint for the reception flow.

POST /pricing/quote takes a basket (lines: irc or free-text drug name + qty) and
an insurer, resolves each line against the drug catalog (effective price = max of
announced vs latest invoice), prices it through the Iranian pricing engine
(insurer share / patient share / مابه‌التفاوت / حق فنی / VAT), and returns the
per-line breakdown PLUS cheaper same-ingredient alternatives for the swap lever.
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends
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
    lines: list[QuoteLineIn]


def _coverage(rec: CatalogRecord, insurer: str) -> tuple[bool, Decimal | None, Decimal]:
    """(is_covered, reference_price, vat_rate). Seed has no per-insurer coverage
    JSON yet, so default: drugs/OTC covered at announced reference, supplements &
    cosmetics not covered; cosmetics carry VAT."""
    covered = rec.category in (ItemCategory.DRUG, ItemCategory.OTC)
    vat = VAT_RATE_COSMETIC if rec.category == ItemCategory.COSMETIC else Decimal("0")
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
