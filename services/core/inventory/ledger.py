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
    "WASTE",            # breakage, contamination
    "EXPIRY_REMOVAL",
    "RECALL_REMOVAL",
    "SUPPLIER_CREDIT",  # the supplier credited the return; the units leave the books
}
RECEIPT_TYPES = {
    "RECEIPT",          # from a supplier against a PO
    "TRANSFER_IN",
    "RETURN_FROM_PATIENT",   # re-stockable only where law allows; see `restockable`
    "COUNT_GAIN",       # physical count found more than the books said
}
ISSUE_ONLY_COUNT = {"COUNT_LOSS"}   # count found less — an issue, but not a supply event

# Movements that relocate units between states *inside* a lot rather than
# taking them out of the pharmacy. `DAMAGE` used to be an ordinary removal, so
# a broken carton simply decremented on-hand and the units ceased to exist —
# they could not be counted, valued, claimed from the supplier, or produced
# during an audit. A transfer conserves them instead: on-hand falls, the
# destination bucket rises, and the lot's physical total is unchanged.
BUCKET_TRANSFER_TYPES = {
    "DAMAGE": "damaged",              # broken on arrival or on the shelf
    "TRANSFER_OUT": "in_transit",     # left this location, not yet arrived
    "RETURN_TO_SUPPLIER": "returned",  # staged for return, awaiting a credit note
}
# Types that may finally remove units from a holding bucket. DISPENSE is
# deliberately absent: a patient may never be handed damaged, in-transit or
# returned stock, and the way to prevent that is to make it unrepresentable.
BUCKET_WRITEOFF_TYPES = {"WASTE", "EXPIRY_REMOVAL", "RECALL_REMOVAL",
                         "SUPPLIER_CREDIT", "COUNT_LOSS"}
# Buckets a write-off may draw from, so damaged stock has a way out of the
# bucket. Without this it would accumulate for ever, which is a worse trap than
# the vanishing it replaced.
BUCKETS = {"damaged": "quantity_damaged", "returned": "quantity_returned",
           "in_transit": "quantity_in_transit"}

ALL_TYPES = (ISSUE_TYPES | RECEIPT_TYPES | ISSUE_ONLY_COUNT
             | set(BUCKET_TRANSFER_TYPES))

# Movements a pharmacist must approve before they take effect, regardless of
# amount. Every one of them destroys or exports value without a patient on the
# other end, which is exactly the shape of a diversion covered by paperwork:
# "it broke", "it expired", "we sent it back", "the count was always wrong".
# `RETURN_TO_SUPPLIER` is absent on purpose: staging goods for return takes
# them out of use but they remain the pharmacy's asset, so it follows the same
# rule as quarantine and damage — immediate. The loss is recognised later, by
# SUPPLIER_CREDIT drawn from the `returned` bucket, and that needs two people.
APPROVAL_ALWAYS = {"WASTE", "EXPIRY_REMOVAL", "RECALL_REMOVAL",
                   "SUPPLIER_CREDIT", "COUNT_LOSS", "COUNT_GAIN"}


@dataclass(frozen=True)
class Lot:
    """The subset of an `inventory_lots` row the allocator needs."""
    lot_id: str
    lot_number: str
    expiry_date: date | None
    quantity_on_hand: Decimal
    quantity_reserved: Decimal = Decimal("0")
    # Units still physically in the pharmacy but not sellable. They are held
    # apart rather than deducted, because damaged stock is money awaiting a
    # supplier claim and returned stock is money awaiting a credit note — both
    # are reportable assets until someone writes them off.
    quantity_damaged: Decimal = Decimal("0")
    quantity_returned: Decimal = Decimal("0")
    quantity_in_transit: Decimal = Decimal("0")
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
    # Set when the movement relocates units rather than removing them.
    # `to_bucket` receives them; `from_bucket` is the bucket a write-off draws
    # from instead of sellable on-hand.
    to_bucket: str | None = None
    from_bucket: str | None = None
    bucket_before: Decimal | None = None
    bucket_after: Decimal | None = None
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


@dataclass
class DispenseAllocation:
    """What the shelf could actually supply for a dispense, and what it could not."""
    plans: list[MovementPlan]
    requested: Decimal
    allocated: Decimal
    shortfall: Decimal
    lots_used: list[str]

    @property
    def complete(self) -> bool:
        return self.shortfall == 0


def plan_dispense(lots: list[Lot], quantity, *, reason: str,
                  as_of: date | None = None,
                  is_controlled: bool = False) -> DispenseAllocation:
    """Allocate a dispense FEFO, reporting any shortfall instead of raising.

    Every other issue type refuses to under-fill, because a transfer or a
    write-off that quietly moves less than it claims is how the books drift.
    Dispensing is the one exception, and the reason is physical: the medicine is
    already in the patient's hand. If our records say there is not enough, the
    *records* are wrong — the stock either walked, arrived uncounted, or was
    mis-entered. Refusing here would let a bookkeeping error stop patient care,
    and inventing the missing units would hide the very discrepancy worth
    knowing about.

    So we take what exists, and return the gap as data. `check_dispense_shortfall`
    surfaces it, and a cycle count settles it.
    """
    want = q(quantity)
    if want <= 0:
        raise LedgerError("dispense quantity must be positive")
    today = as_of or date.today()

    usable = [l for l in lots
              if not _blocked_reason(l, allow_quarantined=False)
              and not (l.expiry_date and l.expiry_date < today)
              and l.available > 0]
    usable.sort(key=lambda l: (l.expiry_date is None,
                               l.expiry_date or date.max, l.lot_number))

    plans: list[MovementPlan] = []
    remaining = want
    for lot in usable:
        if remaining <= 0:
            break
        take = q(min(lot.available, remaining))
        if take <= 0:
            continue
        before = q(lot.quantity_on_hand)
        plans.append(MovementPlan(
            lot_id=lot.lot_id, movement_type="DISPENSE",
            quantity_delta=q(-take), quantity_before=before,
            quantity_after=q(before - take), lot_number=lot.lot_number,
            expiry_date=lot.expiry_date, reason=reason,
            # A dispense is authorised by the prescription and the pharmacist's
            # verification, not by a stock write-off signature. Requiring an
            # approval here would put a second queue between a patient and their
            # medicine.
            requires_approval=False,
            meta={"location": lot.storage_location, "controlled": is_controlled}))
        remaining = q(remaining - take)

    allocated = q(want - remaining)
    return DispenseAllocation(plans=plans, requested=want, allocated=allocated,
                              shortfall=remaining,
                              lots_used=[p.lot_id for p in plans])


def bucket_quantity(lot: Lot, bucket: str) -> Decimal:
    attr = BUCKETS.get(bucket)
    if attr is None:
        raise LedgerError(f"unknown bucket {bucket!r}")
    return q(getattr(lot, attr, 0) or 0)


def plan_bucket_transfer(lot: Lot, quantity, *, movement_type: str,
                         reason: str, from_bucket: str | None = None) -> MovementPlan:
    """Move units into a holding bucket, from sellable stock or another bucket.

    `from_bucket=None` draws from on-hand — a carton crushed on the shelf, goods
    picked for a transfer, stock staged for return. `from_bucket` set moves
    between buckets, which is how damaged stock becomes a supplier return
    without ever passing back through sellable inventory.

    Deliberately needs no approval in either direction. The rule the whole
    module follows is that taking stock *out of use* is immediate — a
    technician holding a crushed carton must be able to pull it from the shelf
    at once, exactly as they can quarantine a lot. What needs two signatures is
    removing it from the books, which is `plan_bucket_writeoff`, or putting it
    back on sale, which is `plan_bucket_release`.
    """
    dest = BUCKET_TRANSFER_TYPES.get(movement_type)
    if dest is None:
        raise LedgerError(f"{movement_type!r} is not a bucket transfer")
    if not reason or not reason.strip():
        raise LedgerError("every movement needs a reason")
    move = q(quantity)
    if move <= 0:
        raise LedgerError("transfer quantity must be positive")
    if from_bucket == dest:
        raise LedgerError(f"a transfer into {dest} cannot also draw from it")

    on_hand = q(lot.quantity_on_hand)
    if from_bucket is None:
        source_held = on_hand
        source_name = "on hand"
    else:
        source_held = bucket_quantity(lot, from_bucket)
        source_name = from_bucket
    if move > source_held:
        raise LedgerError(
            f"cannot move {move} to {dest} — only {source_held} {source_name}")

    b_before = bucket_quantity(lot, dest)
    # On-hand only moves when the units came from it. A bucket-to-bucket
    # transfer leaves sellable stock alone, because those units stopped being
    # sellable when they first entered a bucket.
    after = q(on_hand - move) if from_bucket is None else on_hand
    return MovementPlan(
        lot_id=lot.lot_id, movement_type=movement_type,
        quantity_delta=q(-move), quantity_before=on_hand, quantity_after=after,
        lot_number=lot.lot_number, expiry_date=lot.expiry_date, reason=reason,
        requires_approval=False, to_bucket=dest, from_bucket=from_bucket,
        bucket_before=b_before, bucket_after=q(b_before + move),
        meta={"conserved": True, "source": source_name,
              "location": lot.storage_location})


def plan_bucket_release(lot: Lot, quantity, *, from_bucket: str, reason: str,
                        movement_type: str = "TRANSFER_IN",
                        is_controlled: bool = False,
                        verified_by: str | None = None) -> MovementPlan:
    """Return units from a bucket to sellable stock.

    The arrival end of a transfer, or a supplier refusing a return. Releasing is
    the direction this module always guards: units that have been in transit or
    staged for return were out of sight, so putting them back on sale needs
    more than one person's say-so.

    `verified_by` names a gate that has already supplied that assurance — the
    depot→shelf workflow scans the barcode, runs the count check and takes a
    pharmacist's attestation before it calls this, which is a stronger control
    than a second signature after the fact. A release therefore needs EITHER a
    recorded verification OR an approval, and never neither: with no
    `verified_by` the plan comes back requiring approval, and the caller cannot
    apply it directly.
    """
    if movement_type not in RECEIPT_TYPES:
        raise LedgerError(f"{movement_type!r} cannot release a bucket")
    if not reason or not reason.strip():
        raise LedgerError("every movement needs a reason")
    back = q(quantity)
    if back <= 0:
        raise LedgerError("release quantity must be positive")
    if lot.is_recalled:
        raise LedgerError("cannot release stock from a recalled lot")
    held = bucket_quantity(lot, from_bucket)
    if back > held:
        raise LedgerError(
            f"cannot release {back} from {from_bucket} — only {held} held")

    on_hand = q(lot.quantity_on_hand)
    return MovementPlan(
        lot_id=lot.lot_id, movement_type=movement_type,
        quantity_delta=back, quantity_before=on_hand, quantity_after=q(on_hand + back),
        lot_number=lot.lot_number, expiry_date=lot.expiry_date, reason=reason,
        requires_approval=verified_by is None, from_bucket=from_bucket,
        bucket_before=held, bucket_after=q(held - back),
        meta={"released_to_sale": True, "controlled": is_controlled,
              "verified_by": verified_by})


def plan_bucket_writeoff(lot: Lot, quantity, *, movement_type: str,
                         from_bucket: str, reason: str,
                         is_controlled: bool = False) -> MovementPlan:
    """Remove units from a holding bucket — the way damaged stock finally
    leaves. This one always needs approval: it is the step that turns a
    recoverable asset into a loss.
    """
    if movement_type not in BUCKET_WRITEOFF_TYPES:
        raise LedgerError(f"{movement_type!r} cannot write off a bucket")
    if not reason or not reason.strip():
        raise LedgerError("every movement needs a reason")
    take = q(quantity)
    if take <= 0:
        raise LedgerError("write-off quantity must be positive")
    held = bucket_quantity(lot, from_bucket)
    if take > held:
        raise LedgerError(
            f"cannot write off {take} from {from_bucket} — only {held} held")
    # On-hand is untouched: these units left sellable stock when they entered
    # the bucket, and deducting them twice is exactly the double-count the
    # bucket exists to prevent.
    on_hand = q(lot.quantity_on_hand)
    return MovementPlan(
        lot_id=lot.lot_id, movement_type=movement_type,
        quantity_delta=q(-take), quantity_before=on_hand, quantity_after=on_hand,
        lot_number=lot.lot_number, expiry_date=lot.expiry_date, reason=reason,
        requires_approval=True, from_bucket=from_bucket,
        bucket_before=held, bucket_after=q(held - take),
        meta={"drawn_from_bucket": from_bucket, "controlled": is_controlled})


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
