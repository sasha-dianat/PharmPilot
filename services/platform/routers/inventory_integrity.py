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
from services.core.inventory import clock as CLK
from services.core.inventory import cycle_count as CC
from services.core.inventory import expiry_risk as ER
from services.core.inventory import expiry_probability as EXP
from services.core.inventory import intermittent as IM
from services.core.inventory import pick_list as PICK
from services.core.inventory import seasonality as SEA
from services.core.inventory import recommendations as RC
from services.core.inventory import valuation as VAL
from services.core.inventory import ledger as L
from services.core.inventory import lead_time as LT
from services.core.inventory import reservation_service as RS
from services.core.inventory import reservations as RSV
from services.core.inventory import negotiation as NEG
from services.core.inventory import shortage as SHORT
from services.core.inventory import supplier_reliability as SUP
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



async def _pharmacy_clock(db: AsyncSession, pharmacy_id) -> tuple[str, "date"]:
    """The pharmacy's timezone and the date it currently is there.

    Not `CURRENT_DATE` (the database session's zone) and not `date.today()`
    (the API process's). Measured on this installation those differ by 8.5
    hours, so for a third of every day they name different days.
    """
    tz = (await db.execute(text(
        "SELECT timezone FROM pharmacies WHERE id = :p"),
        {"p": pharmacy_id})).scalar()
    return (tz or CLK.FALLBACK_TZ), CLK.pharmacy_today(tz)


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

    # One source for the demand inputs rather than a second copy of the query
    # that could drift from it — and it already had, on the timezone.
    _stock_unused, by_ndc = await _demand_inputs(db, pharmacy_id,
                                                 DEMAND_WINDOW_DAYS)

    _tz, today = await _pharmacy_clock(db, pharmacy_id)
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
        R.check_expired_on_hand([dict(r) for r in lots], as_of=today),
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
    """Stock rows and their in-window fills, both scoped to one pharmacy.

    Every day boundary here is the pharmacy's local midnight. `created_at::date`
    casts in the *session's* timezone, so a dispense at 20:00 in New York was
    filed under the next day and fell in the wrong window; `CURRENT_DATE` moved
    the boundary itself by the same accident.
    """
    tz, today = await _pharmacy_clock(db, pharmacy_id)
    p = {"pid": pharmacy_id}
    stock = (await db.execute(text("""
        SELECT ndc11, avg_daily_demand, forecast_updated_at
        FROM stock_levels WHERE pharmacy_id = :pid ORDER BY ndc11"""),
        p)).mappings().all()

    day = CLK.local_date_sql("pf.created_at")
    fills = (await db.execute(text(f"""
        SELECT pf.ndc_dispensed AS ndc11, pf.quantity_dispensed,
               COALESCE(pf.fill_date, {day}) AS fill_date
        FROM prescription_fills pf
        JOIN prescriptions pr ON pr.id = pf.prescription_id
        WHERE pf.is_deleted = false AND pr.pharmacy_id = :pid
          AND COALESCE(pf.fill_date, {day})
              > (CAST(:today AS date) - CAST(:window AS integer))"""),
        {**p, "window": window_days, "tz": tz, "today": today})).mappings().all()

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
    _tz, today = await _pharmacy_clock(db, staff.pharmacy_id)
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

    # E11 is a distribution *per supplier*, and a blended average across all of
    # them is worth very little: an item bought from the eleven-day wholesaler
    # gets covered for four days because someone else is quick. Each item is
    # planned against whoever last supplied it — the relationship the next order
    # will most likely go through — and falls back to the blend, then to the
    # declared default, both of which say so in `lead_time_basis`.
    supplier_of = {r["ndc11"]: r["wholesaler"] for r in (await db.execute(text("""
        SELECT DISTINCT ON (l.ndc11) l.ndc11, o.wholesaler
        FROM purchase_order_lines l
        JOIN purchase_orders o ON o.id = l.order_id
        WHERE o.pharmacy_id = :pid AND o.received_at IS NOT NULL
          AND o.is_deleted = false AND l.is_deleted = false
        ORDER BY l.ndc11, o.received_at DESC"""),
        {"pid": staff.pharmacy_id})).mappings().all()}
    by_supplier = LT.by_supplier(pos)

    # E6: the shape of each item's demand. A rate cannot distinguish "2 a day,
    # most days" from "40 once every three weeks", and only the second needs
    # cover for a whole event — a safety stock derived from a daily average
    # cannot serve a demand that arrives all at once.
    patterns = {r.ndc11: IM.assess(r.ndc11, by_ndc.get(r.ndc11, []),
                                   window_days=window_days, as_of=today)
                for r in plan}

    signals = {r.ndc11: LT.reorder_signals(
        avg_daily_demand=r.new_adq, demand_basis=r.basis,
        demand_stdev=r.stdev_daily,
        lead=by_supplier.get(supplier_of.get(r.ndc11, ""), lead),
        demand_class=patterns[r.ndc11].demand_class,
        event_floor=IM.cover_floor(patterns[r.ndc11])) for r in plan}

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
        # The blend, plus the per-supplier estimates each item was actually
        # planned against. Reporting only the blend would hide that two items
        # on this list were covered for very different lengths of time.
        "lead_time": lead.as_dict(),
        "lead_time_by_supplier": {k: v.as_dict() for k, v in by_supplier.items()},
        "rows": [{**r.as_dict(), "signals": signals[r.ndc11].as_dict(),
                  "supplier": supplier_of.get(r.ndc11),
                  "pattern": patterns[r.ndc11].as_dict()}
                 for r in plan],
        "written": written,
        "summary": {
            "items": len(plan),
            "changed": sum(1 for r in plan if r.changed),
            "by_basis": by_basis,
            "prior_signal_verdict": by_verdict,
            "reorder_points_set": sum(
                1 for s in signals.values() if s.reorder_point is not None),
            "by_demand_class": {
                c: sum(1 for p in patterns.values() if p.demand_class == c)
                for c in IM.CLASSES},
            "covered_for_one_event": sum(
                1 for s in signals.values() if s.floor_applied),
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
    # The queue is useless without its clock: a write-off waiting three days
    # looks identical to one raised this morning, and the stock it covers is on
    # the books either way.
    overdue = {o.approval_id: o for o in SLA.overdue(
        [{"id": str(a.id), "movement_type": a.movement_type, "status": a.status,
          "is_controlled": a.is_controlled, "created_at": a.created_at,
          "due_at": a.due_at, "escalation_level": a.escalation_level}
         for a in rows])}

    def out(a):
        o = overdue.get(str(a.id))
        return {
            "id": str(a.id), "irc": a.irc, "ndc11": a.ndc11,
            "movement_type": a.movement_type, "quantity": float(a.quantity),
            "reason": a.reason, "is_controlled": a.is_controlled, "status": a.status,
            "requested_by": str(a.requested_by_id), "payload": a.payload,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "due_at": a.due_at.isoformat() if a.due_at else None,
            "overdue": o is not None,
            "hours_late": round(o.hours_late, 1) if o else 0.0,
            "escalation_level": o.level if o else 0,
            "audience": o.audience if o else None,
        }

    return {"approvals": [out(a) for a in rows], "count": len(rows),
            "overdue_count": len(overdue)}


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
    # An approved write-off removes sellable stock, so the same rule applies as
    # for damage: promises the lot can no longer back are released rather than
    # left claiming units that have gone.
    if a.inventory_lot_id is not None:
        await RS.shrink_to_capacity(db, a.inventory_lot_id,
                                    pharmacy_id=staff.pharmacy_id)
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
    _tz, today = await _pharmacy_clock(db, staff.pharmacy_id)
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
            pharmacy_id=pharmacy_id, kind=p.kind,
            **RC.subject_columns(p.subject),
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


# ── Expiry risk (engine E1, service ⑫) ────────────────────────────────────

@router.get("/expiry-exposure")
async def expiry_exposure(
    return_window: int = Query(ER.DEFAULT_RETURN_WINDOW_DAYS, ge=0, le=365),
    raise_advice: bool = Query(False, description="also file recommendations"),
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    """Which stock will still be here when it expires, and what can be done.

    Distinct from `check_expired_on_hand`, which reports what has *already*
    expired — a write-off discovered too late to do anything but destroy it.
    This is the forward-looking half, and the only one with money still on the
    table.

    Lots with no measured demand are reported as unknown exposure and kept out
    of the headline total. Counting them at zero would read as safe; counting
    them in full would flood the list with dead stock nobody can act on.
    """
    tz, today = await _pharmacy_clock(db, staff.pharmacy_id)

    rows = [dict(r) for r in (await db.execute(text("""
        SELECT s.ndc11, s.avg_daily_demand,
               COALESCE(s.demand_basis, 'no_history') AS demand_basis,
               il.id AS lot_id, il.lot_number, il.expiry_date,
               il.quantity_on_hand, il.unit_cost
        FROM stock_levels s
        JOIN inventory_lots il ON il.ndc11 = s.ndc11
                              AND il.pharmacy_id = s.pharmacy_id
        WHERE s.pharmacy_id = :pid AND il.is_deleted = false
          AND COALESCE(il.quantity_on_hand, 0) > 0
          AND COALESCE(il.is_quarantined, false) = false
        ORDER BY s.ndc11, il.expiry_date, il.lot_number"""),
        {"pid": staff.pharmacy_id})).mappings().all()]

    items: dict[str, dict] = {}
    for r in rows:
        item = items.setdefault(r["ndc11"], {
            "ndc11": r["ndc11"], "avg_daily_demand": r["avg_daily_demand"],
            "demand_basis": r["demand_basis"], "lots": []})
        item["lots"].append(r)

    exposure = ER.assess(list(items.values()), as_of=today,
                         return_window=return_window)
    out = exposure.as_dict()

    if raise_advice:
        # Only lots with something actionable become recommendations; the rest
        # stay in the exposure report. A recommendation nobody can act on is
        # noise that trains people to ignore the queue.
        proposals = [
            RC.Proposal(
                kind="expiry_risk", subject=str(l.lot_id),
                proposal={"action": l.action, "ndc11": l.ndc11,
                          "lot_number": l.lot_number,
                          "expiry": l.expiry.isoformat()},
                explanation=l.explanation, produced_by="expiry_risk_v1",
                features={"days_left": l.days_left,
                          "at_risk_units": float(l.at_risk_units or 0),
                          "at_risk_value": (None if l.at_risk_value is None
                                            else float(l.at_risk_value)),
                          "basis": l.basis},
                confidence=0.9 if l.basis == "observed" else 0.4,
                severity=l.severity)
            for l in exposure.lots if ER.worth_raising(l)]
        out["recommendations"] = await _record(
            db, staff.pharmacy_id, proposals, actor_id=staff.id)

    return out


# ── E11 / E12: how long each supplier takes, and how much of it turns up ──

# A supplier's record should describe the current relationship. Deliveries from
# three years ago are about a different account manager, a different contract,
# and often a different company.
SUPPLIER_WINDOW_DAYS = 365


@router.get("/suppliers")
async def supplier_scorecard(
    raise_advice: bool = Query(False, description="also file recommendations"),
    window_days: int = Query(SUPPLIER_WINDOW_DAYS, ge=30, le=1825,
                             description="how far back a supplier's record counts"),
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    """Each supplier's measured record: lead time (E11) and reliability (E12).

    Both are computed from `purchase_orders` and their lines, which only began
    carrying `received_at` and `quantity_received` when receiving started
    closing the loop. Before that this endpoint could only have reported the
    declared default dressed up as a measurement, which is why it did not exist.

    Duration is reported and deliberately not scored. A supplier that takes
    eleven days every time is already handled — those eleven days are in the
    reorder point. What is scored is how much of an order arrives, and how
    predictably.
    """
    p = {"pid": staff.pharmacy_id, "win": window_days}
    # Orders placed inside the window, plus any still open however old — an
    # ancient order nobody ever closed is exactly the thing worth surfacing.
    scope = ("(o.ordered_at IS NULL "
             "OR o.ordered_at > NOW() - make_interval(days => :win) "
             "OR o.received_at IS NULL)")

    orders = [dict(r) for r in (await db.execute(text(f"""
        SELECT o.id, o.wholesaler, o.ordered_at, o.received_at,
               o.expected_delivery, o.status
        FROM purchase_orders o
        WHERE o.pharmacy_id = :pid AND o.is_deleted = false AND {scope}"""),
        p)).mappings().all()]

    lines = [dict(r) for r in (await db.execute(text(f"""
        SELECT o.wholesaler, l.ndc11, l.quantity_ordered, l.quantity_received,
               l.status
        FROM purchase_order_lines l
        JOIN purchase_orders o ON o.id = l.order_id
        WHERE o.pharmacy_id = :pid AND o.is_deleted = false
          AND l.is_deleted = false AND {scope}"""),
        p)).mappings().all()]

    delivered = [o for o in orders if o.get("received_at") is not None]
    table = SUP.rank(delivered, lines)
    lead = LT.by_supplier(delivered)

    # A head-to-head only between the two best *measured* suppliers, and only
    # where their baskets overlap. `comparable` refuses the rest.
    measured = [s for s in table if s.score is not None]
    head_to_head = (SUP.comparable(measured[0], measured[1]).as_dict()
                    if len(measured) >= 2 else None)

    out = {
        "suppliers": [{**s.as_dict(),
                       "lead_time": lead[s.supplier].as_dict()
                                    if s.supplier in lead
                                    else LT.declared_default(s.supplier).as_dict()}
                      for s in table],
        "measured": len(measured),
        "orders_delivered": len(delivered),
        "orders_outstanding": len(orders) - len(delivered),
        "head_to_head": head_to_head,
        "explanation": (
            f"{len(measured)} of {len(table)} supplier(s) have enough delivered "
            f"history to score." if table else
            "No purchase orders yet. Every lead time in use is the declared "
            "default, and it is labelled as such wherever it appears."),
    }

    if raise_advice:
        proposals = [
            RC.Proposal(
                kind="supplier_reliability", subject=s.supplier,
                # The grade is the identity of the advice; the score is not.
                # A score that moves a hundredth on every delivery would make
                # each night's run a new recommendation and turn the acceptance
                # rate into a measure of how often the job ran.
                proposal={"supplier": s.supplier, "grade": s.grade},
                explanation=" ".join([s.explanation] + s.concerns),
                produced_by="supplier_reliability_v1",
                features={"fill_rate": float(s.fill_rate or 0),
                          "short_line_rate": float(s.short_line_rate or 0),
                          "lead_days": s.lead_days,
                          "consistency": float(s.consistency or 0),
                          "orders": s.orders, "basis": s.basis},
                confidence=0.8 if s.basis == "observed" else 0.4,
                severity="medium" if s.grade == "mixed" else "high")
            for s in table if SUP.worth_raising(s)]
        out["recommendations"] = await _record(
            db, staff.pharmacy_id, proposals, actor_id=staff.id)

    return out


# ── E13: which drugs are about to become unobtainable, and whose fault ────

@router.get("/shortage-warning")
async def shortage_warning(
    window_days: int = Query(SUPPLIER_WINDOW_DAYS, ge=30, le=1825),
    raise_advice: bool = Query(False, description="also file recommendations"),
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    """Molecules running short, separated by cause.

    Replaces service ⑰, which could not tell "every supplier is out of it" from
    "this one supplier is rationing it" and recommended buffer stock for both.
    Those need opposite actions: the first is stock and substitution and warning
    prescribers, the second is a phone call. Buying cover against a problem a
    phone call solves means paying to hold inventory that expires on the shelf.
    """
    _tz, today = await _pharmacy_clock(db, staff.pharmacy_id)
    p = {"pid": staff.pharmacy_id, "win": window_days}

    rows = [dict(r) for r in (await db.execute(text("""
        SELECT l.ndc11, o.wholesaler, o.ordered_at, l.quantity_ordered,
               l.quantity_received, l.status, l.unit_cost
        FROM purchase_order_lines l
        JOIN purchase_orders o ON o.id = l.order_id
        WHERE o.pharmacy_id = :pid AND o.is_deleted = false
          AND l.is_deleted = false
          AND (o.ordered_at IS NULL
               OR o.ordered_at > NOW() - make_interval(days => :win))"""),
        p)).mappings().all()]

    # Demand and on-hand come from the aggregate, with the basis carried. NULL
    # stays NULL: an item with no measured demand has unknown cover, and the
    # COALESCE(..., 0) this replaces made it look like it would last for ever.
    stock = {r["ndc11"]: dict(r) for r in (await db.execute(text("""
        SELECT ndc11, quantity_on_hand, avg_daily_demand,
               COALESCE(demand_basis, 'no_history') AS demand_basis
        FROM stock_levels WHERE pharmacy_id = :pid"""),
        {"pid": staff.pharmacy_id})).mappings().all()}

    names = {r["ndc11"]: r["generic_name"] for r in (await db.execute(text(
        "SELECT ndc11, generic_name FROM drug_products WHERE ndc11 = ANY(:n)"),
        {"n": list({r["ndc11"] for r in rows})})).mappings().all()} if rows else {}

    pos = [dict(r) for r in (await db.execute(text("""
        SELECT wholesaler, ordered_at, received_at FROM purchase_orders
        WHERE pharmacy_id = :pid AND received_at IS NOT NULL"""),
        {"pid": staff.pharmacy_id})).mappings().all()]
    leads = LT.by_supplier(pos)

    by_ndc: dict[str, list[dict]] = {}
    for r in rows:
        by_ndc.setdefault(r["ndc11"], []).append(r)

    # The stored demand signal is what purchasing acts on, so it is preferred.
    # But it only exists once somebody has run a demand refresh, and until then
    # this engine went blind in a way that changed its *action*: with no rate
    # there is no days-of-cover, so a market shortage about to empty the shelf
    # came out `high / buffer_stock` instead of `critical / alert_prescribers`.
    # The clinical escalation must not depend on whether a button was pressed on
    # another tab, so where the stored signal is absent the rate is computed from
    # the fill record directly — the same source the refresh would have used.
    _stock2, fills_by_ndc = await _demand_inputs(db, staff.pharmacy_id,
                                                 DEMAND_WINDOW_DAYS)
    items = []
    for ndc, lines in by_ndc.items():
        st = stock.get(ndc) or {}
        adq, basis = st.get("avg_daily_demand"), st.get("demand_basis")
        extra = []
        if adq is None:
            live = DM.estimate(ndc, fills_by_ndc.get(ndc, []),
                               window_days=DEMAND_WINDOW_DAYS, as_of=today)
            if live.avg_daily_demand is not None:
                adq, basis = live.avg_daily_demand, live.basis
                extra.append(
                    "the stored demand signal has never been refreshed for this "
                    "item, so days of cover here is computed from the fill "
                    "record directly rather than from the figure purchasing uses")
        items.append({
            "ndc11": ndc, "drug_name": names.get(ndc), "lines": lines,
            "on_hand": st.get("quantity_on_hand") or 0,
            "avg_daily_demand": adq,
            "demand_basis": basis or "no_history",
            "extra_concerns": extra})

    report = SHORT.assess(items, leads=leads, as_of=today)
    out = report.as_dict()
    out["as_of"] = today.isoformat()
    out["window_days"] = window_days

    if raise_advice:
        proposals = [
            RC.Proposal(
                kind="shortage_warning", subject=s.ndc11,
                # The verdict and action are the identity: a molecule moving from
                # "one supplier is rationing it" to "the market is out" is new
                # advice and should reopen. The quantities are not.
                proposal={"verdict": s.verdict, "action": s.action,
                          "ndc11": s.ndc11},
                explanation=" ".join([s.explanation] + s.concerns),
                produced_by="shortage_warning_v2",
                features={"fill_rate": float(s.fill_rate or 0),
                          "days_of_cover": (None if s.days_of_cover is None
                                            else float(s.days_of_cover)),
                          "creep": float(s.creep or 0),
                          "settled_lines": s.settled_lines,
                          "short_suppliers": s.short_suppliers,
                          "filling_suppliers": s.filling_suppliers,
                          "lead_basis": s.lead_basis,
                          "demand_basis": s.demand_basis},
                confidence=0.85 if s.demand_basis == "observed" else 0.5,
                severity=s.severity)
            for s in report.signals if SHORT.worth_raising(s)]
        out["recommendations"] = await _record(
            db, staff.pharmacy_id, proposals, actor_id=staff.id)

    return out


# ── ⑳: what to ask this distributor for, and what to concede ─────────────

@router.get("/negotiation-brief")
async def negotiation_brief(
    supplier: str = Query(..., min_length=1, max_length=50),
    window_days: int = Query(SUPPLIER_WINDOW_DAYS, ge=30, le=1825),
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    """Prepare a person for a conversation with a distributor.

    It does not negotiate, send, or commit to anything, and it never invents a
    benchmark. Every comparison is between two prices this pharmacy has actually
    paid — because the failure here is not a wrong figure on a screen, it is the
    owner repeating a fabricated market rate to someone who knows the real one.
    """
    p = {"pid": staff.pharmacy_id, "win": window_days}
    lines = [dict(r) for r in (await db.execute(text("""
        SELECT o.wholesaler, l.ndc11, l.quantity_ordered, l.quantity_received,
               l.status, l.unit_cost
        FROM purchase_order_lines l
        JOIN purchase_orders o ON o.id = l.order_id
        WHERE o.pharmacy_id = :pid AND o.is_deleted = false
          AND l.is_deleted = false
          AND (o.ordered_at IS NULL
               OR o.ordered_at > NOW() - make_interval(days => :win))"""),
        p)).mappings().all()]

    # Margin per molecule from the lots actually on the shelf. Where a lot has no
    # sale price it contributes nothing, and `reliability_cost` reports the units
    # without the money rather than applying a guessed margin.
    margins = {r["ndc11"]: r["margin"] for r in (await db.execute(text("""
        SELECT ndc11, AVG(sell_price - unit_cost) AS margin
        FROM inventory_lots
        WHERE pharmacy_id = :pid AND is_deleted = false
          AND sell_price IS NOT NULL AND unit_cost IS NOT NULL
        GROUP BY ndc11"""), {"pid": staff.pharmacy_id})).mappings().all()
        if r["margin"] is not None and r["margin"] > 0}

    safety = {r["ndc11"]: r["safety_stock"] for r in (await db.execute(text(
        "SELECT ndc11, safety_stock FROM stock_levels "
        "WHERE pharmacy_id = :pid AND safety_stock IS NOT NULL"),
        {"pid": staff.pharmacy_id})).mappings().all()}

    months = (await db.execute(text("""
        SELECT CEIL(EXTRACT(EPOCH FROM (NOW() - MIN(ordered_at))) / 2592000.0)
        FROM purchase_orders
        WHERE pharmacy_id = :pid AND ordered_at IS NOT NULL"""),
        {"pid": staff.pharmacy_id})).scalar()

    out = NEG.brief(lines, supplier=supplier, margins=margins,
                    safety_stock=safety,
                    months_of_history=int(months) if months is not None else None)
    return out.as_dict()


# ── E7 / ㉑: is this a real annual pattern, or last month being unusual? ──

@router.get("/seasonality")
async def seasonality(
    min_units: int = Query(50, ge=0, description="skip items too small to judge"),
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    """Which items have a real seasonal pattern, bucketed by Jalali month.

    The buckets matter arithmetically, not decoratively. Nowruz is 1 Farvardin
    every year and drifts across 20-21 March, so a Gregorian March bucket splits
    the new-year peak across two months and halves it; the school year turns on
    1 Mehr, which lands in September or October.

    Nothing is claimed below two complete cycles. One cold season is an anecdote,
    and a system that buys for a season that never comes is worse than one that
    does not try.
    """
    _tz, today = await _pharmacy_clock(db, staff.pharmacy_id)
    # Three years, so two complete cycles are reachable at all.
    _stock, by_ndc = await _demand_inputs(db, staff.pharmacy_id, 365 * 3)

    names = {r["ndc11"]: r["generic_name"] for r in (await db.execute(text(
        "SELECT ndc11, generic_name FROM drug_products WHERE ndc11 = ANY(:n)"),
        {"n": list(by_ndc)})).mappings().all()} if by_ndc else {}

    out = []
    for ndc, fills in by_ndc.items():
        total = sum(float(f.get("quantity_dispensed") or 0) for f in fills)
        if total < min_units:
            continue
        s = SEA.assess(ndc, fills, as_of=today)
        out.append({**s.as_dict(), "drug_name": names.get(ndc)})

    ranked = sorted(out, key=lambda s: -(s["strength"] or 0))
    seasonal = [s for s in ranked if s["verdict"] == "seasonal"]
    return {
        "items": ranked, "as_of": today.isoformat(),
        "seasonal": len(seasonal),
        "insufficient_cycles": sum(1 for s in ranked
                                   if s["verdict"] == "insufficient_cycles"),
        "no_pattern": sum(1 for s in ranked
                          if s["verdict"] == "no_detectable_seasonality"),
        "explanation": (
            f"{len(seasonal)} of {len(ranked)} item(s) show a seasonal pattern "
            f"strong enough to act on. Buckets are Jalali months; Ramadan is "
            f"lunar and is not captured, which understates seasonality rather "
            f"than inventing it." if ranked else
            "No item has enough dispensing history to judge. A seasonal claim "
            "needs two complete turns of the year and none is made without "
            "them."),
    }


# ── E9: the odds a lot expires before it sells ───────────────────────────

@router.get("/expiry-odds")
async def expiry_odds(
    window_days: int = Query(DEMAND_WINDOW_DAYS, ge=7, le=365),
    limit: int = Query(500, ge=1, le=5000,
                       description="worst-first; the total is always reported"),
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    """E1's deterministic verdict, upgraded to a probability where one is honest.

    A lot with a 5% chance of expiring is not worth discounting; the same lot at
    60% is worth discounting today, while there is still a customer for it. E1
    puts both in the same bucket.

    A probability is quoted only where the normal approximation has enough demand
    events behind it to mean something. Everywhere else the deterministic verdict
    stands and the refusal says why.
    """
    _tz, today = await _pharmacy_clock(db, staff.pharmacy_id)
    _stock, by_ndc = await _demand_inputs(db, staff.pharmacy_id, window_days)

    lots = [dict(r) for r in (await db.execute(text("""
        SELECT il.id AS lot_id, il.ndc11, il.lot_number, il.expiry_date,
               il.quantity_on_hand, il.unit_cost, dp.generic_name
        FROM inventory_lots il
        LEFT JOIN drug_products dp ON dp.ndc11 = il.ndc11
        WHERE il.pharmacy_id = :pid AND il.is_deleted = false
          AND COALESCE(il.quantity_on_hand, 0) > 0
          AND COALESCE(il.is_quarantined, false) = false
        -- lot_number breaks the tie. Lots sharing an expiry date are a normal
        -- case, and without a second key the FEFO cascade below runs in
        -- whatever order the planner returned — so the same shelf could be
        -- given different per-lot answers on two consecutive runs.
        ORDER BY il.ndc11, il.expiry_date, il.lot_number"""),
        {"pid": staff.pharmacy_id})).mappings().all()]

    patterns = {ndc: IM.assess(ndc, fills, window_days=window_days, as_of=today)
                for ndc, fills in by_ndc.items()}

    results, consumed = [], {}
    for l in lots:
        ndc = l["ndc11"]
        p = patterns.get(ndc)
        days_left = ((l["expiry_date"] - today).days
                     if l["expiry_date"] else 0)
        # FEFO: lots expiring sooner take their share of the same demand stream
        # first. Charging every lot the item's whole demand is the defect E1 was
        # rebuilt to fix, and it would reappear here unchanged.
        earlier = consumed.get(ndc, 0)
        o = EXP.assess_lot(
            lot_id=str(l["lot_id"]), ndc11=ndc, units=l["quantity_on_hand"],
            days_left=days_left,
            avg_daily_demand=p.mean_rate if p else None,
            stdev_daily=p.stdev_daily if p else None,
            unit_cost=l["unit_cost"],
            demand_class=p.demand_class if p else "unknown",
            adi=p.adi if p else None,
            consumed_by_earlier=earlier)
        consumed[ndc] = earlier + float(l["quantity_on_hand"] or 0)
        results.append({**o.as_dict(), "lot_number": l["lot_number"],
                        "drug_name": l["generic_name"],
                        "expiry_date": (l["expiry_date"].isoformat()
                                        if l["expiry_date"] else None)})

    priced = [r for r in results if r["expected_loss"] is not None]
    # Worst first, and capped. A pharmacy with 24,000 lots was getting all of
    # them in one response, which invites the UI to render all of them; the
    # useful part is the top of a list sorted by money. The totals below are over
    # EVERY lot, so a cap narrows what is shown and never what is counted —
    # silent truncation would read as "that is all of them".
    ranked = sorted(results, key=lambda r: -(r["expected_loss"] or 0))
    quantified = sum(1 for r in results if r["probability"] is not None)
    return {
        "lots": ranked[:limit],
        "as_of": today.isoformat(),
        "lots_total": len(results), "shown": min(limit, len(results)),
        "truncated": len(results) > limit,
        "quantified": quantified,
        "expected_loss": round(sum(r["expected_loss"] for r in priced), 2),
        "worth_discounting": [r["lot_id"] for r in results
                              if r["band"] in ("likely", "possible")],
        "explanation": (
            f"{quantified} of {len(results)} lot(s) have enough demand history "
            f"behind them for a probability to mean anything. The rest keep E1's "
            f"deterministic verdict, and each says why."
            + (f" Showing the {limit} with the most money on them; the totals "
               f"here are over all {len(results)}."
               if len(results) > limit else "")),
    }


# ── E10: what to bring from the depot before the doors open ──────────────

@router.get("/pick-list")
async def morning_pick_list(
    cover_days: int = Query(1, ge=1, le=14),
    window_days: int = Query(84, ge=28, le=365),
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    """The morning round: which items, how many, and from which lots.

    The replenishment session machinery already existed; what it never had was an
    answer to *which items and how many*. `create_session` takes a list somebody
    typed in, so the intelligence in the morning round has been a person
    remembering what ran out yesterday.

    The target is the 90th percentile of a day's demand, not the average, because
    a shelf stocked to the average runs out half the time.
    """
    _tz, today = await _pharmacy_clock(db, staff.pharmacy_id)
    _stock, by_ndc = await _demand_inputs(db, staff.pharmacy_id, window_days)
    p = {"pid": staff.pharmacy_id}

    # On the shelf now, and which shelf it lives on.
    placed = {r["ndc11"]: dict(r) for r in (await db.execute(text("""
        SELECT sp.ndc11, SUM(sp.units) AS on_shelf,
               MIN(sh.id::text) AS shelf_id, MIN(sh.label) AS shelf_label,
               MIN(sh.storage_condition) AS storage_condition,
               MIN(sh.capacity_units) AS capacity_units,
               MIN(sh.current_units) AS current_units
        FROM shelf_placements sp
        JOIN pharmacy_shelves sh ON sh.id = sp.shelf_id
        WHERE sp.pharmacy_id = :pid AND sp.is_deleted = false
        GROUP BY sp.ndc11"""), p)).mappings().all()}

    # Depot back-stock is computed, never stored: what the lot holds minus what
    # is already out on a shelf.
    lots = [dict(r) for r in (await db.execute(text("""
        SELECT il.id, il.ndc11, il.lot_number, il.expiry_date,
               COALESCE(il.is_quarantined, false) AS is_quarantined,
               COALESCE(il.is_recalled, false) AS is_recalled,
               COALESCE(il.quantity_on_hand, 0)
                 - COALESCE((SELECT SUM(sp.units) FROM shelf_placements sp
                             WHERE sp.inventory_lot_id = il.id
                               AND sp.is_deleted = false), 0) AS available,
               dp.generic_name,
               COALESCE(dp.requires_refrigeration, false) AS requires_refrigeration
        FROM inventory_lots il
        LEFT JOIN drug_products dp ON dp.ndc11 = il.ndc11
        WHERE il.pharmacy_id = :pid AND il.is_deleted = false
        ORDER BY il.ndc11, il.expiry_date"""), p)).mappings().all()]

    by_lot_ndc: dict[str, list[dict]] = {}
    meta: dict[str, dict] = {}
    for l in lots:
        by_lot_ndc.setdefault(l["ndc11"], []).append(l)
        meta.setdefault(l["ndc11"], {
            "drug_name": l["generic_name"],
            "requires_refrigeration": l["requires_refrigeration"]})

    items = []
    for ndc in sorted(set(by_lot_ndc) | set(placed) | set(by_ndc)):
        sh = placed.get(ndc)
        items.append({
            "ndc11": ndc,
            "drug_name": (meta.get(ndc) or {}).get("drug_name"),
            "requires_refrigeration": (meta.get(ndc) or {}).get(
                "requires_refrigeration", False),
            "on_shelf": (sh or {}).get("on_shelf") or 0,
            "shelf": ({"id": sh["shelf_id"], "label": sh["shelf_label"],
                       "storage_condition": sh["storage_condition"],
                       "capacity_units": sh["capacity_units"],
                       "current_units": sh["current_units"]} if sh else None),
            "depot_lots": by_lot_ndc.get(ndc, []),
            "fills": by_ndc.get(ndc, []),
        })

    result = PICK.build(items, as_of=today, cover_days=cover_days,
                        window_days=window_days)
    out = result.as_dict()
    out["as_of"] = today.isoformat()
    out["cover_days"] = cover_days
    return out
