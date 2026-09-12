"""Reservations — stock promised to a prescription that has not left the shelf.

`inventory_lots.quantity_reserved` and `stock_levels.quantity_reserved` existed
from the beginning, `Lot.available` subtracted them, and `pick_fefo` allocated
against that. Everything downstream was built as though reservations worked.
Nothing ever incremented them. The dispense hook decremented, the admin
endpoints initialised them to zero, and no code path raised one — so `available`
always equalled `on_hand`, `check_over_reservation` could never fire, and two
staff could each promise the same last box to a different patient.

This module supplies the missing half, and takes the reservation itself as the
record of truth rather than the counter:

  * A reservation is a row naming the prescription, the lot and the quantity.
    `quantity_reserved` is a denormalisation of the active rows, the same way
    `stock_levels.quantity_on_hand` denormalises its lots — so it can be
    checked, and drift between them is a finding rather than a mystery.

  * Release is exact. The previous decrement was
    `GREATEST(0, quantity_reserved - taken)`, which silently absorbs the case
    where the counter and the reality disagree. The ledger refuses to clamp
    anywhere else and this is no different: releasing more than was reserved
    means the books are wrong, and saying so is the only useful response.

  * Reservations expire. A will-call that nobody collects would otherwise hold
    its units out of `available` forever, and the shelf would show stock the
    allocator refuses to give anyone.

Pure functions over already-fetched rows; `as_of` is always injected.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from .ledger import Lot, LedgerError, pick_fefo, q

# Stock is committed when adjudication is settled and the pharmacy has agreed to
# dispense — not when a technician reaches the shelf, by which time two people
# may already have promised the same units.
RESERVE_ON = "READY_TO_FILL"

# Terminal or backward states that must return the units to the pool.
SHRINK_REASON = "stock no longer available — removed from the shelf"

RELEASE_ON = {
    "CANCELLED": "prescription cancelled",
    "ON_HOLD": "prescription put on hold",
    "RETURNED_TO_STOCK": "filled prescription returned to stock",
    "TRANSFERRED_OUT": "prescription transferred to another pharmacy",
    "ADJUDICATION_REJECTED": "claim rejected after commitment",
}

# A prescription waiting to be collected holds its stock this long. Long enough
# for a patient to come back after a weekend, short enough that an abandoned
# will-call does not sterilise the shelf.
DEFAULT_TTL_DAYS = 14

STATUSES = ("active", "consumed", "released", "expired")


class ReservationError(ValueError):
    """A reservation that cannot be made or released as asked."""


@dataclass(frozen=True)
class Allocation:
    """One lot's share of a reservation."""
    lot_id: str
    lot_number: str
    expiry_date: date | None
    quantity: Decimal

    def as_dict(self) -> dict:
        return {"lot_id": self.lot_id, "lot_number": self.lot_number,
                "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
                "quantity": float(self.quantity)}


@dataclass
class ReservationPlan:
    """What reserving would claim, before anything is written."""
    prescription_id: str
    ndc11: str
    quantity: Decimal
    allocations: list[Allocation] = field(default_factory=list)
    expires_at: datetime | None = None

    @property
    def total(self) -> Decimal:
        return q(sum((a.quantity for a in self.allocations), Decimal("0")))

    def as_dict(self) -> dict:
        return {"prescription_id": self.prescription_id, "ndc11": self.ndc11,
                "quantity": float(self.quantity),
                "allocations": [a.as_dict() for a in self.allocations],
                "expires_at": self.expires_at.isoformat() if self.expires_at else None}


def plan_reserve(
    lots: list[Lot],
    quantity,
    *,
    prescription_id: str,
    ndc11: str,
    as_of: date | None = None,
    now: datetime | None = None,
    ttl_days: int = DEFAULT_TTL_DAYS,
) -> ReservationPlan:
    """Claim `quantity` across lots, FEFO, without writing anything.

    Raises when the units are not there. A reservation that quietly claims less
    than it was asked for is worse than none: the queue would show the
    prescription ready to fill while the shelf could not supply it, and the
    shortfall would only surface with the patient at the counter.

    Expired and quarantined lots are excluded by `pick_fefo`, so a reservation
    can never promise stock that may not be dispensed.
    """
    want = q(quantity)
    if want <= 0:
        raise ReservationError("reserved quantity must be positive")

    try:
        picks = pick_fefo(lots, want, as_of=as_of)
    except LedgerError as exc:
        raise ReservationError(str(exc)) from exc

    stamp = now or datetime.now(timezone.utc)
    return ReservationPlan(
        prescription_id=prescription_id,
        ndc11=ndc11,
        quantity=want,
        allocations=[
            Allocation(lot_id=lot.lot_id, lot_number=lot.lot_number,
                       expiry_date=lot.expiry_date, quantity=take)
            for lot, take in picks
        ],
        expires_at=stamp + timedelta(days=ttl_days),
    )


def plan_release(rows: list[dict], *, reason: str,
                 status: str = "released") -> list[dict]:
    """Return the units held by `rows` to the pool.

    Only active reservations are released. Releasing a consumed one would credit
    back units that have already left with a patient, inflating `available` by
    stock that no longer exists — the mirror image of the double-promise this
    module exists to prevent.
    """
    if status not in ("released", "expired", "consumed"):
        raise ReservationError(f"unknown terminal status {status!r}")

    out = []
    for r in rows:
        if r.get("status") != "active":
            continue
        out.append({
            "reservation_id": r.get("id"),
            "lot_id": r.get("inventory_lot_id"),
            "quantity": q(r.get("quantity") or 0),
            "status": status,
            "reason": reason,
        })
    return out


def release_for_transition(rows: list[dict], to_status: str) -> list[dict]:
    """Releases implied by a workflow transition, if any."""
    reason = RELEASE_ON.get(to_status)
    if reason is None:
        return []
    return plan_release(rows, reason=reason)


def plan_shrink(rows: list[dict], capacity) -> list[dict]:
    """Which reservations to release when the stock behind them is gone.

    A crushed carton is a fact. Refusing to record it because units were
    promised would make the books describe a shelf that no longer exists, so
    the movement proceeds and the promises it invalidates are released here.

    Newest first, deliberately. When there is not enough stock for everyone who
    was promised some, the earlier promise keeps its place — that is the same
    rule a queue uses, and any other order means whoever asked first can be
    displaced by whoever asked last.

    Releases whole reservations rather than trimming them. A prescription for
    thirty tablets that is quietly reduced to eleven is not a smaller promise,
    it is a promise nobody can fill, and the shortfall would only be discovered
    with the patient at the counter.
    """
    cap = q(capacity)
    if cap < 0:
        cap = q(0)
    active = [r for r in rows if r.get("status") == "active"]
    # Oldest first by creation, so the newest are the ones dropped.
    active.sort(key=lambda r: (r.get("created_at") is None, r.get("created_at")))

    kept = q(0)
    survive: list[dict] = []
    for r in active:
        want = q(r.get("quantity") or 0)
        if q(kept + want) <= cap:
            kept = q(kept + want)
            survive.append(r)
    keep_ids = {id(r) for r in survive}
    return [r for r in active if id(r) not in keep_ids]


def expired_rows(rows: list[dict], *, now: datetime | None = None) -> list[dict]:
    """Active reservations whose hold has run out."""
    stamp = now or datetime.now(timezone.utc)
    out = []
    for r in rows:
        if r.get("status") != "active":
            continue
        exp = r.get("expires_at")
        if exp is None:
            continue
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= stamp:
            out.append(r)
    return out


def apply_release(reserved: Decimal | float | int, quantity) -> Decimal:
    """The new `quantity_reserved` after returning `quantity` units.

    Refuses to go negative rather than clamping at zero. `GREATEST(0, ...)` was
    the previous behaviour, and it turns a corrupted counter into a silently
    plausible one — which is precisely the state a reconciliation check can no
    longer detect.
    """
    before, take = q(reserved), q(quantity)
    after = q(before - take)
    if after < 0:
        raise ReservationError(
            f"releasing {take} would take reserved below zero (held {before}); "
            f"the reserved counter and the reservation rows disagree"
        )
    return after


def reserved_by_lot(rows: list[dict]) -> dict[str, Decimal]:
    """Active reserved units per lot — the figure `quantity_reserved` should hold."""
    out: dict[str, Decimal] = {}
    for r in rows:
        if r.get("status") != "active":
            continue
        lot = r.get("inventory_lot_id")
        if lot is None:
            continue
        out[str(lot)] = q(out.get(str(lot), Decimal("0")) + q(r.get("quantity") or 0))
    return out


def drift(counters: dict[str, Decimal | float | int],
          rows: list[dict]) -> list[dict]:
    """Where `quantity_reserved` disagrees with the active reservations.

    The counter is a denormalisation, and every denormalisation drifts
    eventually. This is what makes that drift visible instead of letting it
    quietly remove units from `available`.
    """
    expected = reserved_by_lot(rows)
    keys = set(counters) | set(expected)
    out = []
    for lot_id in sorted(keys):
        held = q(counters.get(lot_id, 0))
        want = expected.get(lot_id, q(0))
        if held != want:
            out.append({"lot_id": lot_id, "counter": float(held),
                        "active_reservations": float(want),
                        "drift": float(q(held - want))})
    return out
