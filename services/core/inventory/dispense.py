"""The dispense hook — the one place a prescription reduces stock.

`RxStateMachine._handle_transition_effects` used to log
"trigger inventory deduction (via Kafka event in production)" and do nothing.
That is why 46 fills existed against 8 movements: on-hand only ever went up, and
every number derived from it — turnover, dead stock, reorder points, stockout
risk — measured a shelf that no longer existed. ROADMAP:34.

Three properties this module has to hold, in order of importance:

1. **It must never block a dispense.** The medicine is already in the patient's
   hand by the time the transition fires. If stock is short, or the inventory
   layer fails outright, the prescription still dispenses and the discrepancy is
   recorded. A bookkeeping error must not become a clinical event. The
   reconciliation report (`fill_without_movement`, `dispense_shortfall`) is the
   safety net that makes best-effort acceptable — a failure here is *detected*,
   not lost.

2. **It must be idempotent.** A retried transition, a re-delivered event, or a
   second call must not decrement twice. Keyed on the fill, checked inside the
   caller's transaction.

3. **It must record which lot the patient received.** That is the whole recall
   answer: "who got lot X". FEFO picks it, and the fill row stores it.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from . import ledger as L

log = logging.getLogger(__name__)

# Used when a dispense has no acting staff member. Visibly a sentinel, so a
# report can find these rather than them looking like a real signature.
UNATTRIBUTED = UUID("00000000-0000-0000-0000-000000000000")


class DispenseResult:
    """What the hook did, for logging and for the caller's response."""

    def __init__(self, *, ok: bool, fill_id=None, movements: list | None = None,
                 allocated: Decimal = Decimal("0"), shortfall: Decimal = Decimal("0"),
                 lots: list[str] | None = None, skipped: str | None = None,
                 error: str | None = None):
        self.ok, self.fill_id = ok, fill_id
        self.movements = movements or []
        self.allocated, self.shortfall = allocated, shortfall
        self.lots = lots or []
        self.skipped, self.error = skipped, error

    def as_dict(self) -> dict:
        return {"ok": self.ok, "fill_id": str(self.fill_id) if self.fill_id else None,
                "movements": [str(m) for m in self.movements],
                "allocated": float(self.allocated), "shortfall": float(self.shortfall),
                "lots": self.lots, "skipped": self.skipped, "error": self.error}


async def _lots_for(db: AsyncSession, pharmacy_id, ndc11: str) -> list[L.Lot]:
    rows = (await db.execute(text("""
        SELECT id, lot_number, expiry_date, quantity_on_hand, quantity_reserved,
               is_quarantined, is_recalled, cold_chain_breach, storage_location
        FROM inventory_lots
        WHERE pharmacy_id = :pid AND ndc11 = :ndc AND is_deleted = false
          AND quantity_on_hand > 0
        ORDER BY expiry_date ASC NULLS LAST
        FOR UPDATE"""), {"pid": pharmacy_id, "ndc": ndc11})).mappings().all()
    return [L.Lot(lot_id=str(r["id"]), lot_number=r["lot_number"],
                  expiry_date=r["expiry_date"],
                  quantity_on_hand=L.q(r["quantity_on_hand"]),
                  quantity_reserved=L.q(r["quantity_reserved"] or 0),
                  is_quarantined=bool(r["is_quarantined"]),
                  is_recalled=bool(r["is_recalled"]),
                  cold_chain_breach=bool(r["cold_chain_breach"]),
                  storage_location=r["storage_location"]) for r in rows]


async def _ensure_fill(db: AsyncSession, rx, staff_id, quantity, now) -> tuple:
    """Find or create the PrescriptionFill this dispense belongs to.

    The fill is the durable dispensing record and the place the lot link lives,
    so it has to exist before the movements can point at it.
    """
    from shared.models.prescription import PrescriptionFill

    existing = (await db.execute(
        select(PrescriptionFill)
        .where(PrescriptionFill.prescription_id == rx.id,
               PrescriptionFill.is_deleted == False)  # noqa: E712
        .order_by(PrescriptionFill.fill_number.desc()))).scalars().first()
    # A refill is a new fill; the same fill re-transitioning is not. `fill_date`
    # distinguishes them without needing a separate marker.
    if existing is not None and existing.fill_date == now.date():
        return existing, False

    next_no = (existing.fill_number + 1) if existing else 1
    # The fill's pharmacist columns are NOT NULL, and a Prescription carries no
    # pharmacist of its own — the acting staff member is the only source. When a
    # transition is system-triggered there is nobody to name, so the record shows
    # the nil UUID rather than borrowing a real person's identity for an act they
    # did not perform. It is visibly a sentinel, and it is reportable.
    actor = staff_id or UNATTRIBUTED
    if staff_id is None:
        log.warning("dispense of %s has no acting staff; fill recorded as "
                    "unattributed", getattr(rx, "rx_number", rx.id))
    fill = PrescriptionFill(
        prescription_id=rx.id, fill_number=next_no, ndc_dispensed=rx.ndc,
        quantity_dispensed=quantity, days_supply=int(rx.days_supply or 0),
        fill_date=now.date(), dispensed_at=now,
        dispensing_pharmacist_id=actor, verifying_pharmacist_id=actor,
        created_by=staff_id, updated_by=staff_id)
    db.add(fill)
    await db.flush()
    return fill, True


async def already_dispensed(db: AsyncSession, fill_id) -> bool:
    """Has this fill already consumed stock? The idempotency guard."""
    return bool((await db.execute(text(
        "SELECT 1 FROM inventory_movements WHERE prescription_fill_id = :f "
        "AND movement_type = 'DISPENSE' LIMIT 1"), {"f": fill_id})).scalar())


async def apply_dispense(db: AsyncSession, rx, *, staff_id=None,
                         quantity=None, now: datetime | None = None,
                         as_of: date | None = None) -> DispenseResult:
    """Decrement stock for a dispensed prescription. Never raises.

    Returns a result describing what happened; the caller logs it. Any failure
    leaves the prescription dispensed and the discrepancy visible to
    reconciliation, which is the correct failure direction: the patient has the
    medicine either way, and an unrecorded decrement is a reportable defect
    rather than a reason to refuse care.
    """
    from services.platform.routers.inventory_integrity import append_movement

    now = now or datetime.now(timezone.utc)
    qty = L.q(quantity if quantity is not None
              else (rx.quantity_dispensed or rx.quantity_prescribed))
    try:
        if qty <= 0:
            return DispenseResult(ok=False, skipped="non-positive quantity")

        fill, created = await _ensure_fill(db, rx, staff_id, qty, now)
        if not created and await already_dispensed(db, fill.id):
            # A retried transition or a re-delivered event. Decrementing twice
            # would be worse than not decrementing at all.
            return DispenseResult(ok=True, fill_id=fill.id,
                                  skipped="already decremented for this fill")

        # Retire this prescription's own holds first. Until they are consumed
        # the units they name are excluded from `available`, so FEFO would
        # refuse to hand the prescription the very stock reserved for it.
        from . import reservation_service as RS
        await RS.consume(db, rx, fill_id=fill.id, now=now)

        lots = await _lots_for(db, rx.pharmacy_id, rx.ndc)
        alloc = L.plan_dispense(lots, qty, as_of=as_of or now.date(),
                                reason=f"dispense {rx.rx_number}")

        movement_ids = []
        irc = None
        for plan in alloc.plans:
            # `quantity_reserved` is not touched here. It used to be decremented
            # by GREATEST(0, reserved - taken), a clamp that silently absorbed
            # any disagreement between the counter and reality. The reservation
            # rows are now the record, `RS.consume` above retires them exactly,
            # and a mismatch is reported rather than flattened.
            await db.execute(text(
                "UPDATE inventory_lots SET quantity_on_hand = :after, "
                "updated_at = NOW() WHERE id = :id"),
                {"after": float(plan.quantity_after), "id": plan.lot_id})
            if irc is None:
                irc = (await db.execute(text(
                    "SELECT irc FROM inventory_lots WHERE id = :id"),
                    {"id": plan.lot_id})).scalar()
            m = await append_movement(
                db, pharmacy_id=rx.pharmacy_id, ndc11=rx.ndc, irc=irc,
                lot_id=plan.lot_id, plan=plan, actor_id=staff_id,
                prescription_fill_id=fill.id)
            movement_ids.append(m.id)

        if alloc.allocated > 0:
            await db.execute(text("""
                UPDATE stock_levels
                   SET quantity_on_hand = quantity_on_hand - :taken,
                       last_dispensed_at = :now, updated_at = NOW()
                 WHERE pharmacy_id = :pid AND ndc11 = :ndc"""),
                {"taken": float(alloc.allocated), "now": now,
                 "pid": rx.pharmacy_id, "ndc": rx.ndc})

        # The recall link: which physical lot this patient received.
        if alloc.plans:
            first = alloc.plans[0]
            fill.inventory_lot_id = UUID(first.lot_id)
            fill.lot_number = first.lot_number
            fill.expiry_date = first.expiry_date

        if alloc.shortfall > 0:
            # Not an error — a measured disagreement between the books and the
            # shelf, recorded so a count can settle it.
            log.warning("dispense %s short by %s of %s (ndc %s): stock records "
                        "disagree with the shelf", rx.rx_number, alloc.shortfall,
                        alloc.requested, rx.ndc)
        return DispenseResult(ok=True, fill_id=fill.id, movements=movement_ids,
                              allocated=alloc.allocated, shortfall=alloc.shortfall,
                              lots=[p.lot_number for p in alloc.plans if p.lot_number])
    except Exception as e:  # noqa: BLE001 — deliberately broad; see docstring
        log.exception("inventory decrement failed for rx %s; the prescription "
                      "remains dispensed and reconciliation will report it",
                      getattr(rx, "rx_number", "?"))
        return DispenseResult(ok=False, error=f"{type(e).__name__}: {e}")


async def reverse_dispense(db: AsyncSession, fill_id, *, staff_id=None,
                           reason: str = "returned to stock") -> DispenseResult:
    """Put back what a reversed dispense took out.

    Returned-to-stock is not an edit of the original movements — those stay. It
    posts offsetting RETURN_FROM_PATIENT receipts against the same lots, so the
    ledger shows both that the units left and that they came back.
    """
    from services.platform.routers.inventory_integrity import append_movement
    try:
        rows = (await db.execute(text("""
            SELECT m.inventory_lot_id, m.quantity_delta, m.pharmacy_id, m.ndc11, m.irc,
                   l.lot_number, l.expiry_date, l.quantity_on_hand, l.is_recalled
            FROM inventory_movements m
            JOIN inventory_lots l ON l.id = m.inventory_lot_id
            WHERE m.prescription_fill_id = :f AND m.movement_type = 'DISPENSE'"""),
            {"f": fill_id})).mappings().all()
        if not rows:
            return DispenseResult(ok=True, skipped="no dispense movements to reverse")

        movement_ids, total = [], Decimal("0")
        for r in rows:
            back = L.q(abs(r["quantity_delta"]))
            lot = L.Lot(lot_id=str(r["inventory_lot_id"]), lot_number=r["lot_number"],
                        expiry_date=r["expiry_date"],
                        quantity_on_hand=L.q(r["quantity_on_hand"]),
                        is_recalled=bool(r["is_recalled"]))
            plan = L.plan_receipt(lot, back, movement_type="RETURN_FROM_PATIENT",
                                  reason=reason)
            await db.execute(text(
                "UPDATE inventory_lots SET quantity_on_hand = :after, updated_at = NOW() "
                "WHERE id = :id"),
                {"after": float(plan.quantity_after), "id": plan.lot_id})
            m = await append_movement(
                db, pharmacy_id=r["pharmacy_id"], ndc11=r["ndc11"], irc=r["irc"],
                lot_id=plan.lot_id, plan=plan, actor_id=staff_id,
                prescription_fill_id=fill_id)
            movement_ids.append(m.id)
            total = L.q(total + back)

        await db.execute(text(
            "UPDATE stock_levels SET quantity_on_hand = quantity_on_hand + :back, "
            "updated_at = NOW() WHERE pharmacy_id = :pid AND ndc11 = :ndc"),
            {"back": float(total), "pid": rows[0]["pharmacy_id"], "ndc": rows[0]["ndc11"]})
        return DispenseResult(ok=True, fill_id=fill_id, movements=movement_ids,
                              allocated=total)
    except Exception as e:  # noqa: BLE001
        log.exception("dispense reversal failed for fill %s", fill_id)
        return DispenseResult(ok=False, error=f"{type(e).__name__}: {e}")
