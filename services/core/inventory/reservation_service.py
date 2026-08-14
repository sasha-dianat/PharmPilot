"""Persisting reservations — the database side of `reservations.py`.

Rules stay pure next door; this module holds the transactions. It maintains two
things in step: the `inventory_reservations` rows, which are the record, and the
`quantity_reserved` counters on the lot and the stock level, which are the
denormalisation the allocator reads. Both move together or neither does.

The counters are updated with an exact arithmetic that refuses to go negative
rather than the previous `GREATEST(0, quantity_reserved - taken)`. A clamp there
would let the counter and the rows disagree without anything noticing, and the
disagreement is precisely what `reservations.drift()` exists to report.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import reservations as RSV
from .dispense import _lots_for
from .ledger import q

log = logging.getLogger(__name__)


class ReservationResult:
    def __init__(self, *, ok: bool, reserved=None, released=0, rows=0,
                 skipped: str | None = None, error: str | None = None):
        self.ok = ok
        self.reserved = reserved or q(0)
        self.released = released
        self.rows = rows
        self.skipped, self.error = skipped, error

    def as_dict(self) -> dict:
        return {"ok": self.ok, "reserved": float(self.reserved),
                "released": self.released, "rows": self.rows,
                "skipped": self.skipped, "error": self.error}


async def _bump(db: AsyncSession, lot_id, ndc11, pharmacy_id, delta) -> None:
    """Move both counters by `delta`, refusing to drive either below zero.

    The lot counter is authoritative; the stock-level counter mirrors it for the
    fast aggregate read, the same way `quantity_on_hand` is mirrored.
    """
    before = (await db.execute(text(
        "SELECT quantity_reserved FROM inventory_lots WHERE id = :id FOR UPDATE"),
        {"id": lot_id})).scalar()
    after = q(q(before or 0) + q(delta))
    if after < 0:
        raise RSV.ReservationError(
            f"reserved would fall below zero on lot {lot_id} "
            f"(held {q(before or 0)}, change {q(delta)})")
    await db.execute(text(
        "UPDATE inventory_lots SET quantity_reserved = :v, updated_at = NOW() "
        "WHERE id = :id"), {"v": float(after), "id": lot_id})

    # The aggregate is re-derived from the lots rather than incremented
    # alongside them. An incremental update needed a GREATEST(0, ...) to stay
    # sane, which is the same clamp this module removed from the dispense hook —
    # and keeping it here would have meant the lot raises on an inconsistency
    # while its own mirror quietly absorbs one. Summing cannot drift.
    await db.execute(text("""
        UPDATE stock_levels s
           SET quantity_reserved = COALESCE((
                   SELECT SUM(il.quantity_reserved) FROM inventory_lots il
                   WHERE il.pharmacy_id = s.pharmacy_id AND il.ndc11 = s.ndc11
                     AND il.is_deleted = false), 0),
               updated_at = NOW()
         WHERE s.pharmacy_id = :pid AND s.ndc11 = :ndc"""),
        {"pid": pharmacy_id, "ndc": ndc11})


async def active_for(db: AsyncSession, prescription_id) -> list[dict]:
    rows = (await db.execute(text("""
        SELECT id, inventory_lot_id, ndc11, quantity, status, expires_at
        FROM inventory_reservations
        WHERE prescription_id = :rx AND status = 'active' AND is_deleted = false
        FOR UPDATE"""), {"rx": prescription_id})).mappings().all()
    return [dict(r) for r in rows]


async def reserve(db: AsyncSession, rx, *, staff_id=None, quantity=None,
                  now: datetime | None = None) -> ReservationResult:
    """Commit stock to a prescription. Never raises.

    A failure to reserve must not block the workflow — the prescription is
    clinically ready whether or not the shelf can be earmarked, and refusing the
    transition would strand it. The shortfall surfaces at fill time and in
    reconciliation instead, which is where a human can act on it.
    """
    now = now or datetime.now(timezone.utc)
    qty = q(quantity if quantity is not None
            else (rx.quantity_dispensed or rx.quantity_prescribed or 0))
    try:
        if qty <= 0:
            return ReservationResult(ok=False, skipped="non-positive quantity")
        if await active_for(db, rx.id):
            # A re-entered state or a redelivered event. Reserving twice would
            # hold the units out of availability a second time.
            return ReservationResult(ok=True, skipped="already reserved")

        lots = await _lots_for(db, rx.pharmacy_id, rx.ndc)
        plan = RSV.plan_reserve(lots, qty, prescription_id=str(rx.id),
                                ndc11=rx.ndc, as_of=now.date(), now=now)

        # Inside a savepoint. A reservation that cannot be written must not
        # abort the caller's transaction — the state change that triggered it
        # is valid on its own, and rolling the whole thing back would strand the
        # prescription over a shelf problem.
        async with db.begin_nested():
            for a in plan.allocations:
                irc = (await db.execute(text(
                    "SELECT irc FROM inventory_lots WHERE id = :id"),
                    {"id": a.lot_id})).scalar()
                await db.execute(text("""
                    INSERT INTO inventory_reservations
                        (id, pharmacy_id, prescription_id, inventory_lot_id,
                         ndc11, irc, quantity, status, expires_at,
                         created_by, updated_by)
                    VALUES (:id, :pid, :rx, :lot, :ndc, :irc, :qty, 'active',
                            :exp, :by, :by)"""), {
                    "id": uuid4(), "pid": rx.pharmacy_id, "rx": rx.id,
                    "lot": a.lot_id, "ndc": rx.ndc, "irc": irc,
                    "qty": float(a.quantity), "exp": plan.expires_at,
                    "by": staff_id})
                await _bump(db, a.lot_id, rx.ndc, rx.pharmacy_id, a.quantity)

        return ReservationResult(ok=True, reserved=plan.total,
                                 rows=len(plan.allocations))
    except RSV.ReservationError as exc:
        log.warning("Could not reserve %s for %s: %s",
                    qty, getattr(rx, "rx_number", rx.id), exc)
        return ReservationResult(ok=False, error=str(exc))
    except Exception as exc:                                   # pragma: no cover
        log.exception("Reservation failed for %s", getattr(rx, "rx_number", rx.id))
        return ReservationResult(ok=False, error=str(exc))


async def _terminate(db: AsyncSession, rows: list[dict], *, pharmacy_id,
                     status: str, reason: str, now: datetime,
                     fill_id=None) -> int:
    released = 0
    # Savepointed for the same reason as `reserve`: a counter that refuses to go
    # negative must surface as a reported failure, not as a poisoned transaction
    # that takes the caller's state change down with it.
    async with db.begin_nested():
        for r in rows:
            await db.execute(text("""
                UPDATE inventory_reservations
                   SET status = CAST(:st AS varchar), reason = :why,
                       released_at = :now, prescription_fill_id =
                           COALESCE(CAST(:fill AS uuid), prescription_fill_id),
                       updated_at = NOW()
                 WHERE id = :id"""), {
                "st": status, "why": reason[:240], "now": now,
                "fill": str(fill_id) if fill_id else None, "id": r["id"]})
            await _bump(db, r["inventory_lot_id"], r["ndc11"], pharmacy_id,
                        -q(r["quantity"]))
            released += 1
    return released


async def release_for_transition(db: AsyncSession, rx, to_status: str, *,
                                 now: datetime | None = None) -> ReservationResult:
    """Free the units when a prescription stops being on its way to a patient."""
    now = now or datetime.now(timezone.utc)
    reason = RSV.RELEASE_ON.get(str(to_status))
    if reason is None:
        return ReservationResult(ok=True, skipped="transition holds stock")
    try:
        rows = await active_for(db, rx.id)
        if not rows:
            return ReservationResult(ok=True, skipped="nothing reserved")
        n = await _terminate(db, rows, pharmacy_id=rx.pharmacy_id,
                             status="released", reason=reason, now=now)
        return ReservationResult(ok=True, released=n, rows=n)
    except Exception as exc:                                   # pragma: no cover
        log.exception("Release failed for %s", getattr(rx, "rx_number", rx.id))
        return ReservationResult(ok=False, error=str(exc))


async def consume(db: AsyncSession, rx, *, fill_id=None,
                  now: datetime | None = None) -> ReservationResult:
    """Retire this prescription's holds because the stock is being dispensed.

    Called before the dispense allocates. Until the hold is consumed the units
    are excluded from `available`, so FEFO would refuse to hand the prescription
    its own reserved stock.
    """
    now = now or datetime.now(timezone.utc)
    try:
        rows = await active_for(db, rx.id)
        if not rows:
            return ReservationResult(ok=True, skipped="nothing reserved")
        n = await _terminate(db, rows, pharmacy_id=rx.pharmacy_id,
                             status="consumed", reason="dispensed to patient",
                             now=now, fill_id=fill_id)
        return ReservationResult(ok=True, released=n, rows=n)
    except Exception as exc:                                   # pragma: no cover
        log.exception("Consume failed for %s", getattr(rx, "rx_number", rx.id))
        return ReservationResult(ok=False, error=str(exc))


async def shrink_to_capacity(db: AsyncSession, lot_id, *, pharmacy_id,
                             now: datetime | None = None) -> dict:
    """Release reservations a lot can no longer back.

    Called after anything removes units from sellable stock. Without it a
    damaged carton leaves `reserved` above `on_hand`: the patient's medicine
    has been written off, the reservation still claims it, and `available` goes
    negative — a promise against stock that does not exist.
    """
    now = now or datetime.now(timezone.utc)
    row = (await db.execute(text(
        "SELECT ndc11, quantity_on_hand, quantity_reserved FROM inventory_lots "
        "WHERE id = :id FOR UPDATE"), {"id": lot_id})).mappings().first()
    if row is None:
        return {"released": 0}
    on_hand, reserved = q(row["quantity_on_hand"]), q(row["quantity_reserved"])
    if reserved <= on_hand:
        return {"released": 0}

    rows = [dict(r) for r in (await db.execute(text("""
        SELECT id, inventory_lot_id, ndc11, quantity, status, created_at,
               prescription_id
        FROM inventory_reservations
        WHERE inventory_lot_id = :lot AND status = 'active' AND is_deleted = false
        FOR UPDATE"""), {"lot": lot_id})).mappings().all()]

    drop = RSV.plan_shrink(rows, on_hand)
    if not drop:
        return {"released": 0}
    n = await _terminate(db, drop, pharmacy_id=pharmacy_id, status="released",
                         reason=RSV.SHRINK_REASON, now=now)
    log.warning("Released %d reservation(s) on lot %s: stock fell to %s but %s "
                "was promised. Affected prescriptions: %s",
                n, lot_id, on_hand, reserved,
                ", ".join(str(r["prescription_id"]) for r in drop))
    return {"released": n,
            "prescriptions": [str(r["prescription_id"]) for r in drop]}


async def sweep_expired(db: AsyncSession, pharmacy_id, *,
                        now: datetime | None = None) -> dict:
    """Return the units held by lapsed reservations.

    Without this an uncollected will-call holds its stock out of `available`
    permanently: the shelf shows units the allocator will not give anyone, and
    the pharmacy reorders against a shortage it does not have.
    """
    now = now or datetime.now(timezone.utc)
    rows = (await db.execute(text("""
        SELECT id, inventory_lot_id, ndc11, quantity, status, expires_at,
               prescription_id
        FROM inventory_reservations
        WHERE pharmacy_id = :pid AND status = 'active' AND is_deleted = false
        FOR UPDATE"""), {"pid": pharmacy_id})).mappings().all()

    lapsed = RSV.expired_rows([dict(r) for r in rows], now=now)
    n = await _terminate(db, lapsed, pharmacy_id=pharmacy_id, status="expired",
                         reason="hold lapsed before collection", now=now)
    if n:
        log.info("Reservation sweep: pharmacy=%s expired=%d",
                 str(pharmacy_id)[:8], n)
    return {"checked": len(rows), "expired": n,
            "prescriptions": [str(r["prescription_id"]) for r in lapsed]}


async def counter_drift(db: AsyncSession, pharmacy_id) -> list[dict]:
    """Where `quantity_reserved` disagrees with the active reservation rows."""
    counters = {str(r["id"]): q(r["quantity_reserved"] or 0) for r in
                (await db.execute(text(
                    "SELECT id, quantity_reserved FROM inventory_lots "
                    "WHERE pharmacy_id = :pid AND is_deleted = false"),
                    {"pid": pharmacy_id})).mappings().all()}
    rows = [dict(r) for r in (await db.execute(text(
        "SELECT inventory_lot_id, quantity, status FROM inventory_reservations "
        "WHERE pharmacy_id = :pid AND is_deleted = false"),
        {"pid": pharmacy_id})).mappings().all()]
    return RSV.drift(counters, rows)
