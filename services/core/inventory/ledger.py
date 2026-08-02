"""The conserving stock ledger — pure rules, no I/O.

Every unit that enters, moves inside, or leaves the pharmacy passes through
`plan_issue` / `plan_receipt` / `plan_count_variance` and lands as one or more
`MovementPlan` rows. Nothing else is allowed to write a quantity.

Why this exists
---------------
Before this module, `prescription_fills` grew (46 rows) while `inventory_movements`
did not (8 rows): dispensing never decremented stock, so on-hand only ever went
up. Every number built on top of it — turnover, dead stock, reorder points,
stockout risk, shrinkage — was therefore measuring a quantity that no longer
described the shelf. ROADMAP line 34 records the same gap.

Three invariants, each enforced here rather than by convention:

1. **Conservation.** For any issue, `sum(allocated) == requested` or the whole
   plan is rejected. Partial silent fulfilment is what lets stock drift.
2. **No clamping.** A quantity that would go negative raises. The old aggregate
   update used `max(0.0, ...)`, which turns shrinkage — the thing you most want
   to see — into a silent zero.
3. **Tamper evidence.** Each movement carries the hash of the previous movement
   for that pharmacy, so a deleted or edited row breaks the chain and is
   detectable. This mirrors `RxStateEvent.event_hash`.

FEFO, not FIFO: pharmacy stock is picked first-expiry-first-out, because the
binding constraint is the expiry date, not the receipt date.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

# Quantities are exact. Floats accumulate error across a ledger, and a ledger
# whose sum is off by 1e-9 cannot be reconciled against a physical count.
QUANT = Decimal("0.001")


def q(value) -> Decimal:
    """Coerce to the ledger's fixed 3-decimal quantity scale."""
    if isinstance(value, Decimal):
        d = value
    else:
        d = Decimal(str(value))
    return d.quantize(QUANT, rounding=ROUND_HALF_UP)


class LedgerError(ValueError):
    """A movement that would break conservation, ordering, or a safety rule."""


# ── Movement vocabulary ───────────────────────────────────────────────────
# Sign is intrinsic to the type: a RECEIPT can never reduce stock and a DISPENSE
# can never raise it, whatever quantity the caller passes.
ISSUE_TYPES = {
    "DISPENSE",         # to a patient against a prescription
    "TRANSFER_OUT",     # depot → shelf, or pharmacy → pharmacy
    "WASTE",            # breakage, contamination
    "EXPIRY_REMOVAL",
    "RECALL_REMOVAL",
    "RETURN_TO_SUPPLIER",
}
RECEIPT_TYPES = {
    "RECEIPT",          # from a supplier against a PO
    "TRANSFER_IN",
    "RETURN_FROM_PATIENT",   # re-stockable only where law allows; see `restockable`
    "COUNT_GAIN",       # physical count found more than the books said
}
ISSUE_ONLY_COUNT = {"COUNT_LOSS"}   # count found less — an issue, but not a supply event
ALL_TYPES = ISSUE_TYPES | RECEIPT_TYPES | ISSUE_ONLY_COUNT

# Movements a pharmacist must approve before they take effect, regardless of
# amount. Every one of them destroys or exports value without a patient on the
# other end, which is exactly the shape of a diversion covered by paperwork:
# "it broke", "it expired", "we sent it back", "the count was always wrong".
APPROVAL_ALWAYS = {"WASTE", "EXPIRY_REMOVAL", "RECALL_REMOVAL",
                   "RETURN_TO_SUPPLIER", "COUNT_LOSS", "COUNT_GAIN"}


@dataclass(frozen=True)
class Lot:
    """The subset of an `inventory_lots` row the allocator needs."""
    lot_id: str
    lot_number: str
    expiry_date: date | None
    quantity_on_hand: Decimal
    quantity_reserved: Decimal = Decimal("0")
    is_quarantined: bool = False
    is_recalled: bool = False
    cold_chain_breach: bool = False
    storage_location: str | None = None

    @property
    def available(self) -> Decimal:
        """On-hand minus what is already committed to un-dispensed fills."""
        return q(self.quantity_on_hand) - q(self.quantity_reserved)


@dataclass
class MovementPlan:
    """One ledger row, not yet persisted."""
    lot_id: str
    movement_type: str
    quantity_delta: Decimal          # signed: negative removes
    quantity_before: Decimal
    quantity_after: Decimal
    lot_number: str | None = None
    expiry_date: date | None = None
    reason: str = ""
    requires_approval: bool = False
    meta: dict = field(default_factory=dict)


def _blocked_reason(lot: Lot, *, allow_quarantined: bool) -> str | None:
    """Why this lot may not be issued to a patient. Recall and cold-chain
    breach are absolute: a breached refrigerated lot is not merely suspect, its
    potency is unknown."""
    if lot.is_recalled:
        return "recalled"
    if lot.cold_chain_breach:
        return "cold_chain_breach"
    if lot.is_quarantined and not allow_quarantined:
        return "quarantined"
    return None


def pick_fefo(lots: list[Lot], quantity, *, as_of: date | None = None,
              allow_expired: bool = False,
              allow_quarantined: bool = False) -> list[tuple[Lot, Decimal]]:
    """Allocate `quantity` across lots, first-expiry-first-out.

    Raises rather than under-filling: a caller that receives less than it asked
    for and does not notice is exactly how the books and the shelf diverge.
    Lots with no expiry date sort last — an unknown expiry must not win a FEFO
    race against a known one.
    """
    want = q(quantity)
    if want <= 0:
        raise LedgerError("quantity must be positive")
    today = as_of or date.today()

    usable: list[Lot] = []
    for lot in lots:
        if _blocked_reason(lot, allow_quarantined=allow_quarantined):
            continue
        if not allow_expired and lot.expiry_date and lot.expiry_date < today:
            continue
        if lot.available > 0:
            usable.append(lot)

    usable.sort(key=lambda l: (l.expiry_date is None,
                               l.expiry_date or date.max,
                               l.lot_number))

    picks: list[tuple[Lot, Decimal]] = []
    remaining = want
    for lot in usable:
        if remaining <= 0:
            break
        take = min(lot.available, remaining)
        if take > 0:
            picks.append((lot, q(take)))
            remaining = q(remaining - take)

    if remaining > 0:
        have = sum((l.available for l in usable), Decimal("0"))
        raise LedgerError(
            f"insufficient stock: requested {want}, allocatable {q(have)}, "
            f"short {remaining}"
        )
    return picks


def plan_issue(lots: list[Lot], quantity, *, movement_type: str, reason: str,
               as_of: date | None = None, allow_expired: bool = False,
               allow_quarantined: bool = False,
               is_controlled: bool = False) -> list[MovementPlan]:
    """Plan the removal of `quantity` units, FEFO across lots."""
    if movement_type not in (ISSUE_TYPES | ISSUE_ONLY_COUNT):
        raise LedgerError(f"{movement_type!r} is not an issue movement")
    if not reason or not reason.strip():
        raise LedgerError("every movement needs a reason")

    # An expiry or recall pull is precisely a removal OF blocked stock, so it
    # must be allowed to reach lots a dispense may not touch.
    if movement_type in ("EXPIRY_REMOVAL", "RECALL_REMOVAL", "WASTE"):
        allow_expired = True
        allow_quarantined = True

    picks = pick_fefo(lots, quantity, as_of=as_of, allow_expired=allow_expired,
                      allow_quarantined=allow_quarantined)
    plans = []
    for lot, take in picks:
        before = q(lot.quantity_on_hand)
        after = q(before - take)
        if after < 0:                      # unreachable via pick_fefo; belt and braces
            raise LedgerError(f"lot {lot.lot_id} would go negative")
        plans.append(MovementPlan(
            lot_id=lot.lot_id, movement_type=movement_type,
            quantity_delta=q(-take), quantity_before=before, quantity_after=after,
            lot_number=lot.lot_number, expiry_date=lot.expiry_date, reason=reason,
            requires_approval=(movement_type in APPROVAL_ALWAYS) or is_controlled,
            meta={"location": lot.storage_location},
        ))
    return plans


def plan_receipt(lot: Lot, quantity, *, movement_type: str, reason: str,
                 is_controlled: bool = False) -> MovementPlan:
    """Plan an addition to a single, identified lot. Receipts are never spread
    across lots — the units physically arrived in one carton with one expiry."""
    if movement_type not in RECEIPT_TYPES:
        raise LedgerError(f"{movement_type!r} is not a receipt movement")
    if not reason or not reason.strip():
        raise LedgerError("every movement needs a reason")
    add = q(quantity)
    if add <= 0:
        raise LedgerError("receipt quantity must be positive")
    if lot.is_recalled:
        raise LedgerError("cannot receive into a recalled lot")
    before = q(lot.quantity_on_hand)
    return MovementPlan(
        lot_id=lot.lot_id, movement_type=movement_type, quantity_delta=add,
        quantity_before=before, quantity_after=q(before + add),
        lot_number=lot.lot_number, expiry_date=lot.expiry_date, reason=reason,
        requires_approval=(movement_type in APPROVAL_ALWAYS) or is_controlled,
    )


def plan_count_variance(lot: Lot, counted_quantity, *, reason: str,
                        is_controlled: bool = False) -> MovementPlan | None:
    """Reconcile a lot to a physical count.

    A variance is never a silent overwrite: it becomes a COUNT_GAIN or
    COUNT_LOSS movement that has to be approved, because an unexplained loss of
    a controlled substance is a diversion signal, not a typo.
    """
    counted = q(counted_quantity)
    if counted < 0:
        raise LedgerError("counted quantity cannot be negative")
    before = q(lot.quantity_on_hand)
    delta = q(counted - before)
    if delta == 0:
        return None
    return MovementPlan(
        lot_id=lot.lot_id,
        movement_type="COUNT_GAIN" if delta > 0 else "COUNT_LOSS",
        quantity_delta=delta, quantity_before=before, quantity_after=counted,
        lot_number=lot.lot_number, expiry_date=lot.expiry_date, reason=reason,
        requires_approval=True,
        meta={"variance_pct": float(abs(delta) / before * 100) if before else None,
              "controlled": is_controlled},
    )


# ── Tamper-evident chain ──────────────────────────────────────────────────

GENESIS = "0" * 64


def movement_hash(*, prev_hash: str, pharmacy_id: str, irc: str | None,
                  lot_id: str | None, movement_type: str,
                  quantity_delta, quantity_after, actor_id: str | None,
                  created_at_iso: str) -> str:
    """SHA-256 over the fields that define the movement, chained to its
    predecessor. Editing or deleting any row breaks every hash after it.

    Field order is fixed and the payload is canonical JSON, so the digest is
    reproducible from the stored row alone — that is what makes verification
    possible years later, during an inspection.
    """
    payload = json.dumps({
        "prev": prev_hash or GENESIS,
        "pharmacy_id": str(pharmacy_id),
        "irc": irc,
        "lot_id": str(lot_id) if lot_id else None,
        "type": movement_type,
        "delta": str(q(quantity_delta)),
        "after": str(q(quantity_after)),
        "actor": str(actor_id) if actor_id else None,
        "at": created_at_iso,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_chain(rows: list[dict]) -> dict:
    """Re-derive every hash in order and report the first break.

    `rows` must be ascending by created_at, each with the fields passed to
    `movement_hash` plus the stored `event_hash`/`prev_hash`.
    """
    prev = GENESIS
    for i, r in enumerate(rows):
        expected = movement_hash(
            prev_hash=prev, pharmacy_id=r["pharmacy_id"], irc=r.get("irc"),
            lot_id=r.get("inventory_lot_id"), movement_type=r["movement_type"],
            quantity_delta=r["quantity_delta"], quantity_after=r["quantity_after"],
            actor_id=r.get("created_by"), created_at_iso=r["created_at_iso"],
        )
        if r.get("prev_hash", GENESIS) != prev:
            return {"intact": False, "break_index": i, "row_id": r.get("id"),
                    "detail": "prev_hash does not match the preceding row — a row "
                              "was deleted, reordered, or inserted"}
        if r.get("event_hash") != expected:
            return {"intact": False, "break_index": i, "row_id": r.get("id"),
                    "detail": "event_hash does not match the row's own contents — "
                              "the row was edited after it was written"}
        prev = expected
    return {"intact": True, "verified": len(rows), "head": prev}
