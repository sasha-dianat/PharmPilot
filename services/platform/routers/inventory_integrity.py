"""Inventory integrity — reconciliation, physical counts, maker-checker
approvals, and formulary binding.

This router only fetches and persists. Every rule lives in
`services.core.inventory.{ledger,reconciliation,formulary_binding}` so it can be
tested against a counterexample rather than a live database.

Authorisation follows the value at risk, not the table being touched:
  inventory:read     — see stock and reports
  inventory:write    — request a movement or count a lot
  inventory:approve  — approve a write-off (must not be the requester)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from services.core.inventory import demand as DM
from services.core.inventory import approvals_sla as SLA
from services.core.inventory import anomaly_bridge as AB
from services.core.inventory import cycle_count as CC
from services.core.inventory import recommendations as RC
from services.core.inventory import valuation as VAL
from services.core.inventory import ledger as L
from services.core.inventory import lead_time as LT
from services.core.inventory import reservation_service as RS
from services.core.inventory import reservations as RSV
from services.core.inventory import reconciliation as R
from services.core.inventory import formulary_binding as FB
from services.platform.auth import require_permission
from services.platform.database import get_db
from shared.models.auth import Staff
from shared.models.inventory import (
    InventoryApproval, InventoryLot, InventoryMovement, InventoryRecommendation,
    StockCount, StockCountLine,
)

router = APIRouter()
log = logging.getLogger(__name__)

# Four weeks: long enough that a weekly drug appears several times, short enough
# that it still describes current dispensing rather than last quarter's.
DEMAND_WINDOW_DAYS = 28

# The chain digest is taken over the timestamp as a string, so reading and
# writing must agree on one format exactly. Formatting in Python (rather than
# to_char) also avoids SQLAlchemy reading ":MI"/":SS" as bind parameters.
ISO = "%Y-%m-%dT%H:%M:%S.%f+00:00"


def iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime(ISO)


def chain_rows_for(rows) -> list[dict]:
    out = []
    for r in rows:
        d = dict(r)
        d["created_at_iso"] = iso_utc(d.pop("created_at"))
        out.append(d)
    return out


# ── Reconciliation ────────────────────────────────────────────────────────

class _ReadOnly:
    """A staff stand-in for calling a read endpoint from inside another.

    `binding_proposals` takes the pharmacy from the caller's `Staff`. Reusing it
    here keeps one resolver rather than a second copy of the matching ladder
    that could drift from the one the operator sees.
    """
    def __init__(self, pharmacy_id):
        self.pharmacy_id = pharmacy_id
        self.id = None


async def _gather(db: AsyncSession, pharmacy_id) -> list[R.Finding]:
    """Run every check against the pharmacy's real rows."""
    p = {"pid": pharmacy_id}

    agg = (await db.execute(text("""
        SELECT s.ndc11, s.irc, s.quantity_on_hand AS aggregate,
               COALESCE(l.total, 0) AS lot_sum
        FROM stock_levels s
        LEFT JOIN (SELECT ndc11, pharmacy_id, SUM(quantity_on_hand) AS total
                   FROM inventory_lots WHERE is_deleted = false GROUP BY ndc11, pharmacy_id) l
          ON l.ndc11 = s.ndc11 AND l.pharmacy_id = s.pharmacy_id
        WHERE s.pharmacy_id = :pid"""), p)).mappings().all()

    lots = (await db.execute(text("""
        SELECT il.id AS lot_id, il.ndc11, il.irc, il.lot_number, il.expiry_date,
               il.quantity_on_hand, il.quantity_reserved, il.is_quarantined,
               il.unit_cost, (il.quantity_on_hand * COALESCE(il.unit_cost,0)) AS value,
               dp.generic_name AS drug_name, dp.is_controlled
        FROM inventory_lots il
        LEFT JOIN drug_products dp ON dp.id = il.drug_product_id
        WHERE il.pharmacy_id = :pid AND il.is_deleted = false"""), p)).mappings().all()

    # Scoped through the prescription: prescription_fills carries no
    # pharmacy_id of its own, and an unscoped count here would let one
    # tenant's dispensing inflate another's orphan-fill finding.
    fills = (await db.execute(text("""
        SELECT pf.id, pf.ndc_dispensed, pf.quantity_dispensed, pf.lot_number,
               pf.inventory_lot_id, pf.created_at AS filled_at
        FROM prescription_fills pf
        JOIN prescriptions p ON p.id = pf.prescription_id
        WHERE pf.is_deleted = false AND p.pharmacy_id = :pid
        ORDER BY pf.created_at DESC LIMIT 5000"""), p)).mappings().all()

    linked = {r[0] for r in (await db.execute(text(
        "SELECT DISTINCT prescription_fill_id FROM inventory_movements "
        "WHERE prescription_fill_id IS NOT NULL AND pharmacy_id = :pid"), p)).all()}

    # What each fill handed over versus what the shelf could actually supply.
    # The hook never refuses a dispense, so the gap is recorded rather than
    # prevented — and this is where it surfaces.
    shortfalls = (await db.execute(text("""
        SELECT pf.id AS fill_id, pf.ndc_dispensed AS ndc11,
               pf.quantity_dispensed, pf.created_at AS filled_at,
               COALESCE(SUM(-m.quantity_delta), 0) AS allocated
        FROM prescription_fills pf
        JOIN inventory_movements m ON m.prescription_fill_id = pf.id
                                  AND m.movement_type = 'DISPENSE'
        WHERE m.pharmacy_id = :pid AND pf.is_deleted = false
        GROUP BY pf.id, pf.ndc_dispensed, pf.quantity_dispensed, pf.created_at"""),
        p)).mappings().all()

    movements = (await db.execute(text("""
        SELECT m.id, m.created_by, m.irc, m.ndc11, m.movement_type, m.reason,
               m.quantity_before, m.quantity_delta, m.created_at,
               COALESCE(dp.is_controlled, false) AS is_controlled
        FROM inventory_movements m
        LEFT JOIN drug_products dp ON dp.ndc11 = m.ndc11
        WHERE m.pharmacy_id = :pid
        ORDER BY m.created_at DESC LIMIT 5000"""), p)).mappings().all()

    conv = (await db.execute(text("""
        SELECT s.ndc11, s.irc, s.quantity_on_hand, s.avg_daily_demand,
               dc.package_count
        FROM stock_levels s
        LEFT JOIN drug_catalog dc ON dc.irc = s.irc
        WHERE s.pharmacy_id = :pid"""), p)).mappings().all()

    chain_rows = (await db.execute(text("""
        SELECT id, pharmacy_id, irc, inventory_lot_id, movement_type,
               quantity_delta, quantity_after, created_by, prev_hash, event_hash,
               created_at
        FROM inventory_movements
        WHERE pharmacy_id = :pid AND event_hash IS NOT NULL
        ORDER BY created_at ASC, id ASC"""), p)).mappings().all()

    chain = chain_rows_for(chain_rows)

    # Which unbound rows are unbound because nobody has resolved them, and which
    # because the formulary holds several brands for the same molecule. Only the
    # first kind is work. Run over the unbound items only, so this stays cheap.
    ambiguous: dict[str, int] = {}
    try:
        proposals = (await binding_proposals(limit=500, staff=_ReadOnly(pharmacy_id),
                                             db=db)).get("proposals", [])
        for prop in proposals:
            if not prop.get("ambiguous"):
                continue
            n = prop.get("candidates")
            # `candidates` is a count in the proposal payload; tolerate a list
            # in case that ever changes, rather than reporting every ambiguous
            # row as unresolved work again.
            if isinstance(n, (list, tuple, set)):
                n = len(n)
            ambiguous[str(prop["ndc11"])] = int(n or 2)
    except Exception:                                          # pragma: no cover
        log.warning("binding ambiguity unavailable; reporting all unbound rows "
                    "as unresolved", exc_info=True)

    # Approvals still waiting for a second signature, with their deadline.
    pending_approvals = [dict(r) for r in (await db.execute(text("""
        SELECT id, movement_type, is_controlled, status, created_at, due_at,
               escalation_level
        FROM inventory_approvals
        WHERE pharmacy_id = :pid AND status = 'pending'"""), p)).mappings().all()]

    # The reserved counter against the rows it denormalises, plus holds that
    # have lapsed and are still withholding stock from availability.
    reservation_drift = await RS.counter_drift(db, pharmacy_id)
    lapsed = RSV.expired_rows([dict(r) for r in (await db.execute(text("""
        SELECT id, prescription_id, inventory_lot_id, ndc11, quantity, status,
               expires_at
        FROM inventory_reservations
        WHERE pharmacy_id = :pid AND status = 'active' AND is_deleted = false"""),
        p)).mappings().all()])
    lapsed = [{"reservation_id": str(r["id"]),
               "prescription_id": str(r["prescription_id"]),
               "lot_id": str(r["inventory_lot_id"]), "ndc11": r["ndc11"],
               "quantity": float(r["quantity"]),
               "expired_at": r["expires_at"].isoformat() if r["expires_at"] else None}
              for r in lapsed]

    # Demand signal vs the fill record. Scoped through the prescription for the
    # same reason the orphan-fill query is: a fill carries no pharmacy of its
    # own, and an unscoped read would test this tenant's signal against another
    # tenant's dispensing.
    signal = (await db.execute(text("""
        SELECT s.ndc11, s.avg_daily_demand, s.forecast_updated_at,
               s.demand_basis, COALESCE(s.quantity_on_hand, 0) AS on_hand
        FROM stock_levels s WHERE s.pharmacy_id = :pid"""), p)).mappings().all()

    demand_fills = (await db.execute(text("""
        SELECT pf.ndc_dispensed AS ndc11, pf.quantity_dispensed,
               COALESCE(pf.fill_date, pf.created_at::date) AS fill_date
        FROM prescription_fills pf
        JOIN prescriptions pr ON pr.id = pf.prescription_id
        WHERE pf.is_deleted = false AND pr.pharmacy_id = :pid
          AND COALESCE(pf.fill_date, pf.created_at::date)
              > (CURRENT_DATE - CAST(:window AS integer))"""),
        {**p, "window": DEMAND_WINDOW_DAYS})).mappings().all()

    by_ndc: dict[str, list[dict]] = {}
    for r in demand_fills:
        by_ndc.setdefault(r["ndc11"], []).append(dict(r))

    today = DM.utc_date()
    divergences, stale, blind = [], [], []
    for r in signal:
        est = DM.estimate(r["ndc11"], by_ndc.get(r["ndc11"], []),
                          window_days=DEMAND_WINDOW_DAYS, as_of=today)
        divergences.append(DM.divergence(
            ndc11=r["ndc11"], stored_adq=r["avg_daily_demand"], observed=est))
        if DM.is_stale(r["forecast_updated_at"], today):
            age = DM.staleness_days(r["forecast_updated_at"], today)
            stale.append({"ndc11": r["ndc11"], "age_days": age,
                          "detail": "never computed" if age is None
                                    else f"{age} days old"})
        elif r["avg_daily_demand"] is None and float(r["on_hand"] or 0) > 0:
            # Freshly computed and still no rate: a real measured absence, and
            # the units are on the shelf regardless.
            blind.append({"ndc11": r["ndc11"], "on_hand": float(r["on_hand"]),
                          "basis": r["demand_basis"],
                          "detail": "stock held with no measurable demand"})

    return [
        R.check_aggregate_drift([dict(r) for r in agg]),
        R.check_negative_stock([dict(r) for r in lots] + [dict(r) for r in agg]),
        R.check_fills_without_movements([dict(r) for r in fills], linked),
        R.check_untraceable_fills([dict(r) for r in fills]),
        R.check_dispense_shortfall([dict(r) for r in shortfalls]),
        R.check_formulary_binding([dict(r) for r in lots], ambiguous),
        R.check_expired_on_hand([dict(r) for r in lots]),
        R.check_suspicious_adjustments([dict(r) for r in movements]),
        R.check_duplicate_lots([dict(r) for r in lots]),
        R.check_unit_conversion([dict(r) for r in conv]),
        R.check_over_reservation([dict(r) for r in lots]),
        R.check_reservation_drift(reservation_drift, lapsed),
        R.check_demand_signal(divergences, stale=stale, blind=blind),
        R.check_overdue_approvals(SLA.overdue(pending_approvals)),
        R.check_chain(L.verify_chain(chain)),
    ]


@router.get("/reconciliation")
async def reconciliation_report(
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    """Full integrity report. Read-only: a check never repairs what it finds."""
    findings = await _gather(db, staff.pharmacy_id)
    out = R.summarize(findings)
    out["generated_at"] = datetime.now(timezone.utc).isoformat()
    return out


# ── Demand signal ─────────────────────────────────────────────────────────

async def _demand_inputs(db: AsyncSession, pharmacy_id, window_days: int):
    """Stock rows and their in-window fills, both scoped to one pharmacy."""
    p = {"pid": pharmacy_id}
    stock = (await db.execute(text("""
        SELECT ndc11, avg_daily_demand, forecast_updated_at
        FROM stock_levels WHERE pharmacy_id = :pid ORDER BY ndc11"""),
        p)).mappings().all()

    fills = (await db.execute(text("""
        SELECT pf.ndc_dispensed AS ndc11, pf.quantity_dispensed,
               COALESCE(pf.fill_date, pf.created_at::date) AS fill_date
        FROM prescription_fills pf
        JOIN prescriptions pr ON pr.id = pf.prescription_id
        WHERE pf.is_deleted = false AND pr.pharmacy_id = :pid
          AND COALESCE(pf.fill_date, pf.created_at::date)
              > (CURRENT_DATE - CAST(:window AS integer))"""),
        {**p, "window": window_days})).mappings().all()

    by_ndc: dict[str, list[dict]] = {}
    for r in fills:
        by_ndc.setdefault(r["ndc11"], []).append(dict(r))
    return [dict(r) for r in stock], by_ndc


@router.post("/demand/refresh")
async def refresh_demand(
    apply: bool = Query(False, description="False previews; True writes."),
    window_days: int = Query(DEMAND_WINDOW_DAYS, ge=7, le=365),
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    """Recompute each item's demand rate from the fill record, with provenance.

    Preview by default. The response shows the standing of each *old* value
    alongside its replacement, so an operator approving a refresh can see which
    numbers were wrong and by how much rather than only what they become.

    `no_history` writes NULL, deliberately. NULL propagates as "unknown" to the
    purchasing engine, which then recommends nothing for that item — the correct
    behaviour when the shelf has no evidence of demand. The alternative, which
    this replaces, was a fallback constant that produced confident orders for
    drugs nobody dispenses.
    """
    today = DM.utc_date()
    stock, by_ndc = await _demand_inputs(db, staff.pharmacy_id, window_days)
    plan = DM.plan_refresh(stock, by_ndc, window_days=window_days, as_of=today)

    # Lead time from this pharmacy's delivered orders, so the reorder point is
    # derived from two measurements rather than two hard-coded constants. With
    # no purchase history it falls back to the one declared default, labelled.
    pos = [dict(r) for r in (await db.execute(text("""
        SELECT wholesaler, ordered_at, received_at FROM purchase_orders
        WHERE pharmacy_id = :pid AND received_at IS NOT NULL"""),
        {"pid": staff.pharmacy_id})).mappings().all()]
    lead = LT.estimate(pos)

    signals = {r.ndc11: LT.reorder_signals(
        avg_daily_demand=r.new_adq, demand_basis=r.basis,
        demand_stdev=r.stdev_daily, lead=lead) for r in plan}

    written = 0
    if apply:
        for row in plan:
            sig = signals[row.ndc11]
            await db.execute(text("""
                UPDATE stock_levels
                   SET avg_daily_demand = CAST(:adq AS numeric),
                       demand_basis = CAST(:basis AS varchar),
                       demand_confidence = CAST(:conf AS numeric),
                       demand_window_days = CAST(:win AS integer),
                       demand_units_observed = CAST(:units AS numeric),
                       reorder_point = CAST(:rop AS numeric),
                       safety_stock = CAST(:ss AS numeric),
                       forecast_updated_at = :now
                 WHERE pharmacy_id = :pid AND ndc11 = :ndc"""), {
                "adq": None if row.new_adq is None else str(row.new_adq),
                "basis": row.basis, "conf": str(row.confidence),
                "win": row.window_days, "units": str(row.units_observed),
                "rop": None if sig.reorder_point is None else str(sig.reorder_point),
                "ss": None if sig.safety_stock is None else str(sig.safety_stock),
                "now": datetime.now(timezone.utc),
                "pid": staff.pharmacy_id, "ndc": row.ndc11,
            })
            written += 1
        await db.commit()
        log.info("Demand refresh applied: pharmacy=%s rows=%d window=%dd",
                 str(staff.pharmacy_id)[:8], written, window_days)

    by_basis: dict[str, int] = {}
    by_verdict: dict[str, int] = {}
    for r in plan:
        by_basis[r.basis] = by_basis.get(r.basis, 0) + 1
        by_verdict[r.verdict] = by_verdict.get(r.verdict, 0) + 1

    return {
        "applied": apply,
        "window_days": window_days,
        "as_of": today.isoformat(),
        "lead_time": lead.as_dict(),
        "rows": [{**r.as_dict(), "signals": signals[r.ndc11].as_dict()}
                 for r in plan],
        "written": written,
        "summary": {
            "items": len(plan),
            "changed": sum(1 for r in plan if r.changed),
            "by_basis": by_basis,
            "prior_signal_verdict": by_verdict,
            "reorder_points_set": sum(
                1 for s in signals.values() if s.reorder_point is not None),
        },
    }


# ── Formulary binding ─────────────────────────────────────────────────────

@router.get("/formulary-binding/proposals")
async def binding_proposals(
    limit: int = Query(500, ge=1, le=5000),
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    """Propose an IRC for every stocked product not yet bound to the formulary.

    Proposals only. Binding stock to the wrong catalogue row would attach a
    wrong price and wrong insurer coverage to a real product.
    """
    products = (await db.execute(text("""
        SELECT DISTINCT dp.ndc11, dp.generic_name, dp.strength, dp.dosage_form,
               dp.drug_db_metadata ->> 'gtin' AS gtin
        FROM inventory_lots il JOIN drug_products dp ON dp.id = il.drug_product_id
        WHERE il.pharmacy_id = :pid AND il.is_deleted = false AND il.irc IS NULL
        LIMIT :lim"""), {"pid": staff.pharmacy_id, "lim": limit})).mappings().all()
    if not products:
        return FB.summarize([])

    generics = {FB.normalize_generic(p["generic_name"]) for p in products}
    # Fetch only candidate generics rather than all 39,184 rows.
    catalog = (await db.execute(text("""
        SELECT irc, gtin, generic_name, strength, dosage_form, name_fa
        FROM drug_catalog
        WHERE generic_name IS NOT NULL"""))).mappings().all()
    catalog = [dict(c) for c in catalog
               if FB.normalize_generic(c["generic_name"]) in generics]

    return FB.summarize([FB.propose(dict(p), catalog) for p in products])


class BindingApply(BaseModel):
    bindings: list[dict] = Field(..., description="[{ndc11, irc}] — owner-approved only")


@router.post("/formulary-binding/apply")
async def apply_bindings(
    body: BindingApply,
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    """Write owner-approved IRC bindings onto this pharmacy's stock rows."""
    applied = 0
    for b in body.bindings:
        ndc, irc = b.get("ndc11"), b.get("irc")
        if not ndc or not irc:
            continue
        exists = (await db.execute(text("SELECT 1 FROM drug_catalog WHERE irc = :irc"),
                                   {"irc": irc})).scalar()
        if not exists:
            raise HTTPException(422, f"IRC {irc} is not in the formulary")
        for tbl in ("inventory_lots", "stock_levels"):
            res = await db.execute(text(
                f"UPDATE {tbl} SET irc = :irc WHERE pharmacy_id = :pid AND ndc11 = :ndc"),
                {"irc": irc, "pid": staff.pharmacy_id, "ndc": ndc})
            applied += res.rowcount or 0
    await db.commit()
    return {"rows_bound": applied, "items": len(body.bindings)}


# ── Physical counts ───────────────────────────────────────────────────────

class CountCreate(BaseModel):
    count_type: str = "CYCLE"
    blind: bool = True
    irc: Optional[list[str]] = None
    location: Optional[str] = None
    notes: Optional[str] = None


@router.post("/counts", status_code=201)
async def create_count(
    body: CountCreate,
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    """Open a count session and snapshot the expected quantity of every lot in
    scope, so a movement posted mid-count cannot silently change what the
    variance is measured against."""
    if body.count_type not in ("CYCLE", "FULL", "SPOT", "CONTROLLED"):
        raise HTTPException(422, f"unknown count_type {body.count_type!r}")

    sc = StockCount(pharmacy_id=staff.pharmacy_id, count_type=body.count_type,
                    blind=body.blind, status="counting",
                    scope={"irc": body.irc, "location": body.location},
                    started_at=datetime.now(timezone.utc), notes=body.notes,
                    created_by=staff.id, updated_by=staff.id)
    db.add(sc)
    await db.flush()

    q = select(InventoryLot).where(InventoryLot.pharmacy_id == staff.pharmacy_id,
                                   InventoryLot.is_deleted == False)  # noqa: E712
    if body.irc:
        q = q.where(InventoryLot.irc.in_(body.irc))
    if body.location:
        q = q.where(InventoryLot.storage_location == body.location)
    lots = (await db.execute(q)).scalars().all()

    for lot in lots:
        db.add(StockCountLine(
            stock_count_id=sc.id, inventory_lot_id=lot.id, irc=lot.irc,
            ndc11=lot.ndc11, lot_number=lot.lot_number,
            expected_quantity=lot.quantity_on_hand,
            created_by=staff.id, updated_by=staff.id))
    await db.commit()
    return {"count_id": str(sc.id), "lines": len(lots), "blind": sc.blind,
            "status": sc.status}


class CountLineSubmit(BaseModel):
    line_id: UUID
    counted_quantity: float
    note: Optional[str] = None


@router.post("/counts/{count_id}/lines")
async def submit_count_line(
    count_id: UUID, body: CountLineSubmit,
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    """Record one counted lot. The variance is computed but NOT applied — a
    count proposes, an approval disposes."""
    sc = (await db.execute(select(StockCount).where(
        StockCount.id == count_id,
        StockCount.pharmacy_id == staff.pharmacy_id))).scalar_one_or_none()
    if sc is None:
        raise HTTPException(404, "count session not found")
    if sc.status not in ("open", "counting"):
        raise HTTPException(409, f"count is {sc.status}; no further lines accepted")

    line = (await db.execute(select(StockCountLine).where(
        StockCountLine.id == body.line_id,
        StockCountLine.stock_count_id == sc.id))).scalar_one_or_none()
    if line is None:
        raise HTTPException(404, "count line not found")
    if body.counted_quantity < 0:
        raise HTTPException(422, "counted quantity cannot be negative")

    line.counted_quantity = body.counted_quantity
    line.variance = float(body.counted_quantity) - float(line.expected_quantity)
    line.counted_by_id = staff.id
    line.counted_at = datetime.now(timezone.utc)
    line.note = body.note
    await db.commit()
    return {"line_id": str(line.id), "variance": float(line.variance),
            "expected": None if sc.blind else float(line.expected_quantity)}


@router.get("/counts/{count_id}")
async def get_count(
    count_id: UUID,
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    sc = (await db.execute(select(StockCount).where(
        StockCount.id == count_id,
        StockCount.pharmacy_id == staff.pharmacy_id))).scalar_one_or_none()
    if sc is None:
        raise HTTPException(404, "count session not found")
    lines = (await db.execute(select(StockCountLine).where(
        StockCountLine.stock_count_id == sc.id))).scalars().all()
    counted = [l for l in lines if l.counted_quantity is not None]
    variance_rows = [l for l in counted if l.variance and float(l.variance) != 0]
    return {
        "count_id": str(sc.id), "status": sc.status, "type": sc.count_type,
        "blind": sc.blind, "lines": len(lines), "counted": len(counted),
        "variances": len(variance_rows),
        "accuracy_pct": round(100 * (len(counted) - len(variance_rows)) / len(counted), 1)
        if counted else None,
        "rows": [{
            "line_id": str(l.id), "irc": l.irc, "ndc11": l.ndc11,
            "lot_number": l.lot_number,
            # A blind count hides the book figure until the session is posted.
            "expected": float(l.expected_quantity)
            if (not sc.blind or sc.status == "posted") else None,
            "counted": float(l.counted_quantity) if l.counted_quantity is not None else None,
            "variance": float(l.variance)
            if (l.variance is not None and (not sc.blind or sc.status == "posted")) else None,
        } for l in lines],
    }


@router.post("/counts/{count_id}/post")
async def post_count(
    count_id: UUID,
    staff: Staff = Depends(require_permission("inventory:approve")),
    db: AsyncSession = Depends(get_db),
):
    """Turn every non-zero variance into a pending approval request.

    Nothing is applied to stock here. A variance on a controlled substance is a
    diversion signal, and the system's job is to put it in front of a named
    human, not to quietly make the books agree with the shelf.
    """
    sc = (await db.execute(select(StockCount).where(
        StockCount.id == count_id,
        StockCount.pharmacy_id == staff.pharmacy_id))).scalar_one_or_none()
    if sc is None:
        raise HTTPException(404, "count session not found")
    if sc.status == "posted":
        raise HTTPException(409, "count already posted")

    lines = (await db.execute(select(StockCountLine).where(
        StockCountLine.stock_count_id == sc.id,
        StockCountLine.counted_quantity.isnot(None)))).scalars().all()

    created = []
    for line in lines:
        lot = (await db.execute(select(InventoryLot).where(
            InventoryLot.id == line.inventory_lot_id))).scalar_one_or_none()
        if lot is None:
            continue
        plan = L.plan_count_variance(
            L.Lot(lot_id=str(lot.id), lot_number=lot.lot_number,
                  expiry_date=lot.expiry_date,
                  quantity_on_hand=L.q(lot.quantity_on_hand)),
            line.counted_quantity,
            reason=f"cycle count {sc.id}")
        if plan is None:
            continue
        appr = InventoryApproval(
            pharmacy_id=staff.pharmacy_id, irc=line.irc, ndc11=line.ndc11,
            inventory_lot_id=lot.id, movement_type=plan.movement_type,
            quantity=abs(plan.quantity_delta), status="pending",
            reason=f"count variance on lot {line.lot_number}: "
                   f"expected {line.expected_quantity}, counted {line.counted_quantity}",
            requested_by_id=staff.id, created_by=staff.id, updated_by=staff.id,
            due_at=SLA.deadline(plan.movement_type,
                                requested_at=datetime.now(timezone.utc)).due_at)
        db.add(appr)
        created.append(appr)

    sc.status = "review"
    await db.commit()
    return {"count_id": str(sc.id), "status": sc.status,
            "approvals_created": len(created),
            "note": "Variances are pending approval; stock is unchanged until approved."}


# ── Maker-checker ─────────────────────────────────────────────────────────

class ApprovalDecision(BaseModel):
    approve: bool
    note: Optional[str] = None
    witness_id: Optional[UUID] = None


def enforce_approval_authority(*, status: str, requested_by_id, approver_id,
                               is_controlled: bool, witness_id) -> None:
    """The separation-of-duties rules for a stock write-off. Pure and raising,
    so each rule is provable by a test instead of being a line inside a handler
    that only executes against a live database.

    Self-approval is the failure mode that matters: every other control in this
    module assumes two people saw the movement.
    """
    if status != "pending":
        raise HTTPException(409, f"approval already {status}")
    if requested_by_id == approver_id:
        raise HTTPException(403, "the requester cannot approve their own write-off")
    if is_controlled and not witness_id:
        raise HTTPException(422, "a controlled-substance movement needs a witness")
    if witness_id and witness_id == requested_by_id:
        raise HTTPException(422, "the witness cannot be the requester")
    if witness_id and witness_id == approver_id:
        raise HTTPException(422, "the witness must be a third person, not the approver")


@router.get("/approvals")
async def list_approvals(
    status: str = Query("pending"),
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(select(InventoryApproval).where(
        InventoryApproval.pharmacy_id == staff.pharmacy_id,
        InventoryApproval.status == status,
    ).order_by(InventoryApproval.created_at.desc()).limit(200))).scalars().all()
    return {"approvals": [{
        "id": str(a.id), "irc": a.irc, "ndc11": a.ndc11,
        "movement_type": a.movement_type, "quantity": float(a.quantity),
        "reason": a.reason, "is_controlled": a.is_controlled, "status": a.status,
        "requested_by": str(a.requested_by_id), "payload": a.payload,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    } for a in rows], "count": len(rows)}


@router.post("/approvals/{approval_id}/decide")
async def decide_approval(
    approval_id: UUID, body: ApprovalDecision,
    staff: Staff = Depends(require_permission("inventory:approve")),
    db: AsyncSession = Depends(get_db),
):
    """Approve or reject a write-off, and apply it only on approval.

    The approver may not be the requester — self-approval defeats the control
    entirely. A controlled substance additionally needs a witness who is also
    not the requester.
    """
    a = (await db.execute(select(InventoryApproval).where(
        InventoryApproval.id == approval_id,
        InventoryApproval.pharmacy_id == staff.pharmacy_id))).scalar_one_or_none()
    if a is None:
        raise HTTPException(404, "approval not found")
    enforce_approval_authority(
        status=a.status, requested_by_id=a.requested_by_id,
        approver_id=staff.id, is_controlled=a.is_controlled,
        witness_id=body.witness_id)

    a.decided_by_id = staff.id
    a.decided_at = datetime.now(timezone.utc)
    a.decision_note = body.note
    a.witness_id = body.witness_id
    if not body.approve:
        a.status = "rejected"
        await db.commit()
        return {"id": str(a.id), "status": a.status, "stock_changed": False}

    # A FIELD_EDIT carries no quantity: it is a correction that could put
    # blocked stock back on the shelf (extending an expiry, releasing a recall),
    # so it needs the same two signatures but applies to a column, not a lot
    # balance. One queue, one set of rules, two kinds of change.
    if a.movement_type == "FIELD_EDIT":
        applied = await _apply_field_edit(db, a, staff)
        a.status = "applied"
        await db.commit()
        return {"id": str(a.id), "status": a.status, "stock_changed": False,
                "field_changed": applied}

    movement = await _apply_movement(db, a, staff)
    a.status = "applied"
    await db.commit()
    return {"id": str(a.id), "status": a.status, "stock_changed": True,
            "movement_id": str(movement.id), "event_hash": movement.event_hash}


async def _apply_field_edit(db: AsyncSession, a: InventoryApproval,
                            staff: Staff) -> dict:
    """Apply an approved field correction, re-validating the policy at approval
    time. The rules are checked again here because the row may have moved since
    the request was raised — an approval is permission to make *this* change,
    not permission to overwrite whatever is there now."""
    from services.core.inventory import admin_rules as A

    payload = a.payload or {}
    field, new = payload.get("field"), payload.get("new")
    if payload.get("table") != "inventory_lots" or not field:
        raise HTTPException(422, "approval payload does not describe a lot field edit")

    lot = (await db.execute(select(InventoryLot).where(
        InventoryLot.id == a.inventory_lot_id,
        InventoryLot.pharmacy_id == a.pharmacy_id))).scalar_one_or_none()
    if lot is None:
        raise HTTPException(404, "lot no longer exists")
    if field in A.LOT_LEDGER_ONLY_FIELDS or field not in A.EDITABLE:
        raise HTTPException(422, f"{field} is not an approvable field edit")

    current = getattr(lot, field, None)
    if current != payload.get("old"):
        # Someone changed it in the meantime. Re-applying blind would overwrite
        # a change nobody approved.
        cur_j = current.isoformat() if hasattr(current, "isoformat") else current
        if cur_j != payload.get("old"):
            raise HTTPException(409,
                f"{field} changed since this approval was requested "
                f"(now {cur_j!r}, request assumed {payload.get('old')!r}); "
                f"raise a fresh request against the current value")

    if field == "expiry_date" and isinstance(new, str):
        from datetime import date as _date
        new = _date.fromisoformat(new[:10])
    setattr(lot, field, new)
    lot.updated_by = staff.id
    log.info("approved field edit %s on lot %s by %s (approval %s)",
             field, lot.id, staff.id, a.id)
    return {"field": field, "old": payload.get("old"), "new": payload.get("new")}


async def _apply_movement(db: AsyncSession, a: InventoryApproval,
                          staff: Staff) -> InventoryMovement:
    """Apply an approved movement: mutate the lot, keep the aggregate in step,
    and append a hash-chained ledger row. No clamping — a movement that would
    drive stock negative is rejected, because a negative is information."""
    lot = (await db.execute(select(InventoryLot).where(
        InventoryLot.id == a.inventory_lot_id))).scalar_one_or_none()
    if lot is None:
        raise HTTPException(404, "lot no longer exists")

    lot_view = L.Lot(lot_id=str(lot.id), lot_number=lot.lot_number,
                     expiry_date=lot.expiry_date,
                     quantity_on_hand=L.q(lot.quantity_on_hand),
                     quantity_reserved=L.q(lot.quantity_reserved or 0),
                     quantity_damaged=L.q(lot.quantity_damaged or 0),
                     quantity_returned=L.q(lot.quantity_returned or 0),
                     quantity_in_transit=L.q(lot.quantity_in_transit or 0),
                     is_quarantined=bool(lot.is_quarantined),
                     is_recalled=bool(lot.is_recalled),
                     cold_chain_breach=bool(lot.cold_chain_breach))
    # A write-off drawn from a holding bucket takes its units from there, not
    # from sellable on-hand; deducting both would double-count exactly what the
    # bucket exists to prevent.
    payload = a.payload or {}
    from_bucket = payload.get("from_bucket")
    try:
        if from_bucket and payload.get("release"):
            # Putting blocked stock back on sale — the releasing direction, so
            # it reaches here through approval rather than applying directly.
            plan = L.plan_bucket_release(
                lot_view, a.quantity, from_bucket=from_bucket,
                movement_type=a.movement_type, reason=a.reason,
                is_controlled=a.is_controlled)
        elif from_bucket:
            plan = L.plan_bucket_writeoff(
                lot_view, a.quantity, movement_type=a.movement_type,
                from_bucket=from_bucket, reason=a.reason,
                is_controlled=a.is_controlled)
        elif a.movement_type in L.RECEIPT_TYPES:
            plan = L.plan_receipt(lot_view, a.quantity,
                                  movement_type=a.movement_type, reason=a.reason,
                                  is_controlled=a.is_controlled)
        else:
            plan = L.plan_issue([lot_view], a.quantity,
                                movement_type=a.movement_type, reason=a.reason,
                                is_controlled=a.is_controlled)[0]
    except L.LedgerError as e:
        raise HTTPException(422, str(e))

    lot.quantity_on_hand = plan.quantity_after
    lot.updated_by = staff.id
    if a.movement_type == "RECALL_REMOVAL":
        lot.is_recalled = True
    if plan.from_bucket:
        setattr(lot, L.BUCKETS[plan.from_bucket], plan.bucket_after)
    if plan.to_bucket:
        setattr(lot, L.BUCKETS[plan.to_bucket], plan.bucket_after)

    # The aggregate tracks SELLABLE stock, so it only moves when on-hand does.
    # A bucket write-off leaves on-hand alone and must leave the aggregate alone
    # too — the units stopped being sellable when they entered the bucket.
    if plan.quantity_after != plan.quantity_before:
        await db.execute(text("""
            UPDATE stock_levels SET quantity_on_hand = quantity_on_hand + :d,
                                    updated_at = NOW()
            WHERE pharmacy_id = :pid AND ndc11 = :ndc"""),
            {"d": float(plan.quantity_after - plan.quantity_before),
             "pid": a.pharmacy_id, "ndc": lot.ndc11})

    return await append_movement(
        db, pharmacy_id=a.pharmacy_id, ndc11=lot.ndc11, irc=a.irc or lot.irc,
        lot_id=lot.id, plan=plan, actor_id=staff.id, approval_id=a.id)


async def append_movement(db: AsyncSession, *, pharmacy_id, ndc11: str,
                          irc: str | None, lot_id, plan: L.MovementPlan,
                          actor_id, approval_id=None,
                          prescription_fill_id=None) -> InventoryMovement:
    """Append one hash-chained row to the pharmacy's ledger.

    The chain head is read inside the caller's transaction, so two concurrent
    writers serialise on the same row rather than forking the chain.
    """
    head = (await db.execute(text("""
        SELECT event_hash FROM inventory_movements
        WHERE pharmacy_id = :pid AND event_hash IS NOT NULL
        ORDER BY created_at DESC, id DESC LIMIT 1
        FOR UPDATE"""), {"pid": pharmacy_id})).scalar()
    prev = head or L.GENESIS
    now = datetime.now(timezone.utc)

    m = InventoryMovement(
        pharmacy_id=pharmacy_id, ndc11=ndc11, irc=irc, inventory_lot_id=lot_id,
        movement_type=plan.movement_type, reason=plan.reason,
        quantity_before=plan.quantity_before, quantity_after=plan.quantity_after,
        quantity_delta=plan.quantity_delta, approval_id=approval_id,
        prescription_fill_id=prescription_fill_id, prev_hash=prev,
        created_at=now, created_by=actor_id, updated_by=actor_id,
        event_hash=L.movement_hash(
            prev_hash=prev, pharmacy_id=str(pharmacy_id), irc=irc,
            lot_id=str(lot_id) if lot_id else None,
            movement_type=plan.movement_type, quantity_delta=plan.quantity_delta,
            quantity_after=plan.quantity_after,
            actor_id=str(actor_id) if actor_id else None,
            created_at_iso=now.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")),
    )
    db.add(m)
    await db.flush()
    return m


@router.get("/ledger/verify")
async def verify_ledger(
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    """Re-derive the whole movement chain and report the first break.

    A break is evidence. The endpoint never offers to repair it.
    """
    rows = (await db.execute(text("""
        SELECT id, pharmacy_id, irc, inventory_lot_id, movement_type,
               quantity_delta, quantity_after, created_by, prev_hash, event_hash,
               created_at
        FROM inventory_movements
        WHERE pharmacy_id = :pid AND event_hash IS NOT NULL
        ORDER BY created_at ASC, id ASC"""),
        {"pid": staff.pharmacy_id})).mappings().all()
    return L.verify_chain(chain_rows_for(rows))


# ── Cycle counting ────────────────────────────────────────────────────────

@router.get("/cycle-count/plan")
async def cycle_count_plan(
    capacity: int = Query(25, ge=1, le=500, description="count lines per session"),
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    """What to count next, worst first, and what this schedule leaves out.

    Counting everything equally is the same as counting nothing carefully: the
    hours go into cheap slow-moving stock while the items carrying the money and
    the risk get the same thin attention. `StockCount` could always record a
    count; nothing decided what to count.

    The response reports the effort comparison against a flat sweep rather than
    asserting a saving, because a schedule that tightens intervals on risk can
    cost more than the one it replaced — and that should be visible now, not
    discovered a year later.
    """
    today = DM.utc_date()
    items = [dict(r) for r in (await db.execute(text("""
        SELECT s.ndc11, s.avg_daily_demand, s.demand_basis,
               dp.is_controlled,
               agg.unit_cost, agg.on_hand, agg.next_expiry, agg.max_lot_value,
               sc.last_counted, mv.last_variance_at
        FROM stock_levels s
        LEFT JOIN drug_products dp ON dp.id = s.drug_product_id
        LEFT JOIN (
            SELECT ndc11, pharmacy_id,
                   SUM(quantity_on_hand) AS on_hand,
                   MAX(COALESCE(unit_cost,0)) AS unit_cost,
                   MIN(CASE WHEN quantity_on_hand > 0 THEN expiry_date END) AS next_expiry,
                   MAX(quantity_on_hand * COALESCE(unit_cost,0)) AS max_lot_value
            FROM inventory_lots WHERE is_deleted = false GROUP BY ndc11, pharmacy_id
        ) agg ON agg.ndc11 = s.ndc11 AND agg.pharmacy_id = s.pharmacy_id
        LEFT JOIN (
            SELECT l.ndc11, MAX(l.counted_at::date) AS last_counted
            FROM stock_count_lines l
            WHERE l.counted_at IS NOT NULL AND l.is_deleted = false
            GROUP BY l.ndc11
        ) sc ON sc.ndc11 = s.ndc11
        LEFT JOIN (
            SELECT ndc11, MAX(created_at::date) AS last_variance_at
            FROM inventory_movements
            WHERE movement_type IN ('COUNT_GAIN','COUNT_LOSS')
            GROUP BY ndc11
        ) mv ON mv.ndc11 = s.ndc11
        WHERE s.pharmacy_id = :pid"""), {"pid": staff.pharmacy_id})).mappings().all()]

    # Variability comes from the same measured window the demand signal used, so
    # the XYZ band cannot disagree with the rate it is derived from.
    _, by_ndc = await _demand_inputs(db, staff.pharmacy_id, DEMAND_WINDOW_DAYS)
    for it in items:
        est = DM.estimate(str(it["ndc11"]), by_ndc.get(it["ndc11"], []),
                          window_days=DEMAND_WINDOW_DAYS, as_of=today)
        it["stdev_daily"] = est.stdev_daily

    classified = CC.classify(items, as_of=today)
    last = {str(i["ndc11"]): i["last_counted"] for i in items if i.get("last_counted")}
    due = CC.due_for_count(classified, last, as_of=today)
    session = CC.plan_session(due, capacity=capacity) if due else {
        "lines": [], "counted": 0, "deferred": 0, "deferred_classes": [],
        "worst_deferred": None, "coverage_note": "Nothing is due."}

    mix: dict[str, int] = {}
    for c in classified:
        mix[c.klass] = mix.get(c.klass, 0) + 1

    return {
        "as_of": today.isoformat(),
        "capacity": capacity,
        "items_classified": len(classified),
        "class_mix": dict(sorted(mix.items())),
        "due_now": len(due),
        "session": session,
        "effort": CC.effort_saved(classified),
        "classification": [c.as_dict() for c in classified],
    }


# ── Recommendations ───────────────────────────────────────────────────────

async def _record(db: AsyncSession, pharmacy_id, proposals: list, *, actor_id=None):
    """Persist a run's proposals, closing out what it no longer proposes.

    Re-raising advice that is already open would inflate the denominator every
    night and make the acceptance rate a measure of how often the job ran. The
    partial unique index enforces it; this skips the insert so a nightly sweep
    is not a stream of caught conflicts.
    """
    recent = [dict(r) for r in (await db.execute(text("""
        SELECT id, kind, status, fingerprint, created_at, decided_at
        FROM inventory_recommendations
        WHERE pharmacy_id = :pid AND is_deleted = false
          AND (status = 'open' OR decided_at > NOW() - INTERVAL '180 days')"""),
        {"pid": pharmacy_id})).mappings().all()]
    existing = [r for r in recent if r["status"] == "open"]
    open_fps = {r["fingerprint"] for r in existing}
    # Advice a human decided recently is not raised again. Re-asking is how an
    # alert queue teaches people to ignore it, and it re-inflates the
    # denominator the fingerprint exists to protect.
    quiet = RC.suppressed(recent)
    keep = set()

    written = skipped = 0
    for p in proposals:
        fp = RC.fingerprint(p)
        keep.add(fp)
        if fp in open_fps:
            continue
        if fp in quiet:
            skipped += 1
            continue
        db.add(InventoryRecommendation(
            pharmacy_id=pharmacy_id, kind=p.kind, ndc11=p.subject or None,
            proposal=p.proposal, features=p.features or None,
            confidence=p.confidence, explanation=p.explanation,
            severity=p.severity, produced_by=p.produced_by,
            model_version=p.model_version, fingerprint=fp, status="open",
            created_by=actor_id, updated_by=actor_id))
        written += 1

    closed = 0
    for row in RC.supersede(existing, keep):
        await db.execute(text(
            "UPDATE inventory_recommendations SET status = 'superseded', "
            "updated_at = NOW() WHERE id = :i"), {"i": row["id"]})
        closed += 1
    for row in RC.expired(existing):
        await db.execute(text(
            "UPDATE inventory_recommendations SET status = 'expired', "
            "updated_at = NOW() WHERE id = :i"), {"i": row["id"]})

    await db.commit()
    return {"produced": len(proposals), "written": written,
            "superseded": closed, "suppressed": skipped}


@router.get("/recommendations")
async def list_recommendations(
    kind: Optional[str] = Query(None),
    status: str = Query("open"),
    limit: int = Query(100, ge=1, le=1000),
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    """Advice awaiting a decision, worst first."""
    where = ["pharmacy_id = :pid", "is_deleted = false", "status = :st"]
    params: dict = {"pid": staff.pharmacy_id, "st": status, "lim": limit}
    if kind:
        where.append("kind = :kind")
        params["kind"] = kind
    rows = (await db.execute(text(f"""
        SELECT id, kind, ndc11, irc, proposal, features, confidence, explanation,
               severity, produced_by, model_version, status, created_at,
               decided_at, decision_note
        FROM inventory_recommendations
        WHERE {' AND '.join(where)}
        ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                               WHEN 'medium' THEN 2 ELSE 3 END,
                 confidence DESC NULLS LAST, created_at ASC
        LIMIT :lim"""), params)).mappings().all()
    return {"status": status, "count": len(rows),
            "recommendations": [dict(r) | {"id": str(r["id"])} for r in rows]}


class Decision(BaseModel):
    accept: bool
    note: Optional[str] = None


@router.post("/recommendations/{rec_id}/decide")
async def decide_recommendation(
    rec_id: UUID,
    body: Decision,
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    """Accept or reject a recommendation.

    A rejection must say why. That reason is the labelled negative — produced by
    an expert at the moment they had the full context — and it is the only part
    of this that cannot be reconstructed later.

    Accepting records agreement, not action. Applying the advice still goes
    through the ordinary approval and ledger paths.
    """
    row = (await db.execute(text(
        "SELECT id, status FROM inventory_recommendations "
        "WHERE id = :i AND pharmacy_id = :pid AND is_deleted = false"),
        {"i": rec_id, "pid": staff.pharmacy_id})).mappings().first()
    if row is None:
        raise HTTPException(404, "recommendation not found")

    try:
        out = RC.decide(dict(row), status="accepted" if body.accept else "rejected",
                        note=body.note, decided_by=staff.id,
                        now=datetime.now(timezone.utc))
    except RC.RecommendationError as exc:
        raise HTTPException(422, str(exc))

    await db.execute(text("""
        UPDATE inventory_recommendations
           SET status = CAST(:st AS varchar), decision_note = :note,
               decided_by_id = :by, decided_at = :at, updated_at = NOW()
         WHERE id = :i"""), {
        "st": out["status"], "note": out["decision_note"],
        "by": out["decided_by_id"], "at": out["decided_at"], "i": rec_id})
    await db.commit()
    return {"id": str(rec_id), "status": out["status"],
            "note": out["decision_note"]}


@router.get("/recommendations/scoreboard")
async def recommendation_scoreboard(
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    """Whether each advisory component is any good, per kind.

    `ignored` is the verdict worth watching for: plenty produced and almost
    none decided. On a dashboard counting alerts that reads as a vigilant
    detector; here it reads as staff who have learned to scroll past it.
    """
    rows = [dict(r) for r in (await db.execute(text("""
        SELECT id, kind, status, decision_note, decided_at, ndc11, confidence
        FROM inventory_recommendations
        WHERE pharmacy_id = :pid AND is_deleted = false"""),
        {"pid": staff.pharmacy_id})).mappings().all()]
    return {"scores": [s.as_dict() for s in RC.scoreboard(rows)],
            "rejection_reasons": RC.rejection_reasons(rows)[:50],
            "note": "acceptance is agreement, not correctness — outcome is "
                    "recorded separately where it can be observed"}


# ── Valuation ─────────────────────────────────────────────────────────────

@router.get("/valuation")
async def stock_valuation(
    method: str = Query(VAL.DEFAULT_METHOD, pattern="^(fifo|weighted)$"),
    shrinkage_days: int = Query(90, ge=7, le=730),
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    """What the stock is worth, and what the losses cost.

    `unit_cost` has been on the lot since the first migration and nothing ever
    added it up, so shrinkage was reported in units — a hundred lost
    paracetamol tablets and a hundred lost insulin pens read identically.

    The response says which method produced the figure and how much of the
    shelf it could not value. A valuation whose method is implicit is a number
    two people will read differently, and one that quietly values uncosted
    stock at zero is a floor being reported as a total.
    """
    lots = [dict(r) for r in (await db.execute(text("""
        SELECT ndc11, lot_number, expiry_date, quantity_on_hand, unit_cost,
               quantity_damaged, quantity_returned, quantity_in_transit
        FROM inventory_lots
        WHERE pharmacy_id = :pid AND is_deleted = false"""),
        {"pid": staff.pharmacy_id})).mappings().all()]
    valued = VAL.value_stock(lots, method=method)

    # The lot cost at the time is not on the movement, so the current lot cost
    # is used and the response says so. Costing a write-off at today's price is
    # right for stock bought recently and wrong for anything held through a
    # devaluation — worth knowing before the figure is quoted.
    movements = [dict(r) for r in (await db.execute(text("""
        SELECT m.movement_type, m.quantity_delta, m.ndc11, il.unit_cost
        FROM inventory_movements m
        LEFT JOIN inventory_lots il ON il.id = m.inventory_lot_id
        WHERE m.pharmacy_id = :pid
          AND m.created_at > NOW() - make_interval(days => CAST(:d AS integer))"""),
        {"pid": staff.pharmacy_id, "d": shrinkage_days})).mappings().all()]

    return {
        "valuation": valued.as_dict(),
        "shrinkage": VAL.shrinkage(movements, period_days=shrinkage_days).as_dict(),
        "cost_basis_note": (
            "Losses are costed at the lot's current unit_cost; the cost at the "
            "time of the movement is not recorded on the movement, so a "
            "write-off of stock held through a price change is valued at "
            "today's price, not what was paid."),
    }
