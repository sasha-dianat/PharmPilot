"""Ground truth, computed independently of the application.

This module imports **nothing** from `services.core.inventory`. That is the
whole point: an oracle that reuses the code under test can only confirm the code
agrees with itself. Every rule here is written from the specification —

    total(lot) = receipts - issues ± authorised adjustments
    total(lot) = on_hand + damaged + returned + in_transit
    available(lot) = on_hand - reserved
    every quantity >= 0

— rather than from the implementation.

It also uses a deliberately different *algorithm*. The application maintains
running balances and updates them in place. The oracle keeps only an append-only
event log and recomputes every balance from zero whenever asked. A drift bug in
an incremental updater cannot exist here, and a shared bug is unlikely because
the two arrive at the same number by different routes.

Quantities are `Decimal` quantised to three places, matching the schema. Money
is `Decimal` and never float: a float total is wrong by an amount that grows
with the size of the pharmacy.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

Q3 = Decimal("0.001")
BUCKETS = ("damaged", "returned", "in_transit")


def q(v) -> Decimal:
    """Three-decimal quantisation, half-up. Independent of the app's `q`."""
    if isinstance(v, Decimal):
        d = v
    elif isinstance(v, float):
        d = Decimal(repr(v))
    else:
        d = Decimal(str(v if v is not None else 0))
    return d.quantize(Q3, rounding=ROUND_HALF_UP)


class OracleViolation(AssertionError):
    """The oracle refused an event because it would break a stated invariant."""


@dataclass(frozen=True)
class Event:
    """One thing that happened, in the order it happened."""
    seq: int
    kind: str
    lot: str
    qty: Decimal = Decimal("0")
    bucket: str | None = None
    ref: str | None = None          # prescription / approval / idempotency key
    at: datetime | None = None
    meta: dict = field(default_factory=dict)


# What each event does to a lot, stated once so the rules are readable in one
# place rather than spread across handlers.
#
#   on_hand delta, bucket delta, changes total?
KINDS = {
    "RECEIPT":            ("+qty", None, True),
    "DISPENSE":           ("-qty", None, True),
    "WRITEOFF_ONHAND":    ("-qty", None, True),      # WASTE etc. from sellable
    "WRITEOFF_BUCKET":    (None, "-qty", True),      # from damaged/returned
    "TO_BUCKET":          ("-qty", "+qty", False),   # DAMAGE / TRANSFER_OUT
    "FROM_BUCKET":        ("+qty", "-qty", False),   # release back to sellable
    "COUNT_GAIN":         ("+qty", None, True),
    "COUNT_LOSS":         ("-qty", None, True),
    "RESERVE":            (None, None, False),
    "UNRESERVE":          (None, None, False),
    "CONSUME_RESERVE":    (None, None, False),
}


@dataclass
class LotState:
    on_hand: Decimal = Decimal("0.000")
    # Individual holds, oldest first: (sequence, reference, quantity). The total
    # is derived. Tracking them individually is what lets the oracle model the
    # rule the application follows when stock is removed from under a promise —
    # whole reservations are released, newest first, rather than the total being
    # clamped, and those two give different answers whenever a release would
    # have to split a hold.
    holds: list = field(default_factory=list)
    damaged: Decimal = Decimal("0.000")
    returned: Decimal = Decimal("0.000")
    in_transit: Decimal = Decimal("0.000")

    @property
    def reserved(self) -> Decimal:
        return q(sum((h[2] for h in self.holds), Decimal("0")))

    @property
    def total(self) -> Decimal:
        return q(self.on_hand + self.damaged + self.returned + self.in_transit)

    @property
    def available(self) -> Decimal:
        return q(self.on_hand - self.reserved)

    def as_dict(self) -> dict:
        return {"on_hand": self.on_hand, "reserved": self.reserved,
                "damaged": self.damaged, "returned": self.returned,
                "in_transit": self.in_transit, "total": self.total,
                "available": self.available}


class Oracle:
    """An append-only event log, and balances derived from it on demand."""

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._seq = 0
        # Idempotency keys already seen. A replayed event must be a no-op, and
        # the oracle enforces that itself rather than trusting the caller.
        self._seen_refs: set[str] = set()
        self.lot_cost: dict[str, Decimal] = {}
        self.lot_ndc: dict[str, str] = {}
        self.lot_expiry: dict[str, date] = {}
        self.rejected: list[tuple[Event, str]] = []

    # ── recording ────────────────────────────────────────────────────────
    def register_lot(self, lot: str, *, ndc: str, cost: Decimal,
                     expiry: date) -> None:
        self.lot_ndc[lot] = ndc
        self.lot_cost[lot] = q(cost) if cost is not None else Decimal("0.000")
        self.lot_expiry[lot] = expiry

    def record(self, kind: str, lot: str, qty=0, *, bucket: str | None = None,
               ref: str | None = None, at: datetime | None = None,
               idempotent: bool = False, **meta) -> Event | None:
        """Append an event. Returns None when it was a duplicate replay.

        Duplicate suppression lives here because "a retried request must not
        move stock twice" is a property of the *system*, and the oracle has to
        model the correct behaviour to be able to detect the incorrect one.
        """
        if kind not in KINDS:
            raise OracleViolation(f"unknown event kind {kind!r}")
        if idempotent and ref:
            if ref in self._seen_refs:
                return None
            self._seen_refs.add(ref)

        self._seq += 1
        ev = Event(seq=self._seq, kind=kind, lot=lot, qty=q(qty),
                   bucket=bucket, ref=ref, at=at, meta=meta)
        self._events.append(ev)
        return ev

    def undo(self, seq: int) -> None:
        """Remove an event, for modelling a rolled-back transaction.

        A cancelled operation must leave zero net effect, and the cleanest way
        to express that in ground truth is that it never happened.
        """
        self._events = [e for e in self._events if e.seq != seq]

    # ── deriving ─────────────────────────────────────────────────────────
    def state(self) -> dict[str, LotState]:
        """Replay everything from zero. Never incremental, deliberately."""
        out: dict[str, LotState] = defaultdict(LotState)
        for ev in sorted(self._events, key=lambda e: e.seq):
            st = out[ev.lot]
            self._apply(st, ev)
        return dict(out)

    def _apply(self, st: LotState, ev: Event) -> None:
        k = ev.kind
        qty = ev.qty

        if k == "RESERVE":
            st.holds.append((ev.seq, ev.ref, qty))
            return
        elif k in ("UNRESERVE", "CONSUME_RESERVE"):
            st.holds = [h for h in st.holds if h[1] != ev.ref]
            return
        elif k == "RECEIPT" or k == "COUNT_GAIN":
            st.on_hand = q(st.on_hand + qty)
        elif k in ("DISPENSE", "WRITEOFF_ONHAND", "COUNT_LOSS"):
            st.on_hand = q(st.on_hand - qty)
        elif k == "TO_BUCKET":
            st.on_hand = q(st.on_hand - qty)
            setattr(st, ev.bucket, q(getattr(st, ev.bucket) + qty))
        elif k == "FROM_BUCKET":
            st.on_hand = q(st.on_hand + qty)
            setattr(st, ev.bucket, q(getattr(st, ev.bucket) - qty))
        elif k == "WRITEOFF_BUCKET":
            setattr(st, ev.bucket, q(getattr(st, ev.bucket) - qty))
            return

        # Anything that removed sellable stock may have taken units out from
        # under a promise. The specification says the movement stands and the
        # promises it can no longer back are released, newest first.
        if k in ("DISPENSE", "WRITEOFF_ONHAND", "COUNT_LOSS", "TO_BUCKET"):
            self._shrink(st)

    @staticmethod
    def _shrink(st: "LotState") -> None:
        """Release whole holds, newest first, until the stock covers them."""
        while st.holds and st.reserved > st.on_hand:
            newest = max(st.holds, key=lambda h: h[0])
            st.holds.remove(newest)

    # ── aggregates ───────────────────────────────────────────────────────
    def by_ndc(self) -> dict[str, LotState]:
        """SKU totals, summed from lot balances — never stored separately."""
        lots = self.state()
        out: dict[str, LotState] = defaultdict(LotState)
        for lot, st in lots.items():
            ndc = self.lot_ndc.get(lot)
            if ndc is None:
                continue
            agg = out[ndc]
            agg.on_hand = q(agg.on_hand + st.on_hand)
            agg.holds = agg.holds + list(st.holds)
            agg.damaged = q(agg.damaged + st.damaged)
            agg.returned = q(agg.returned + st.returned)
            agg.in_transit = q(agg.in_transit + st.in_transit)
        return dict(out)

    def valuation(self) -> Decimal:
        """Sellable stock at each lot's own purchase price."""
        total = Decimal("0.000")
        for lot, st in self.state().items():
            total += st.on_hand * self.lot_cost.get(lot, Decimal("0"))
        return q(total)

    def bucket_value(self, bucket: str) -> Decimal:
        total = Decimal("0.000")
        for lot, st in self.state().items():
            total += getattr(st, bucket) * self.lot_cost.get(lot, Decimal("0"))
        return q(total)

    def movement_count(self, kind: str | None = None) -> int:
        if kind is None:
            return len(self._events)
        return sum(1 for e in self._events if e.kind == kind)

    # ── self-checks ──────────────────────────────────────────────────────
    def violations(self) -> list[str]:
        """Invariants the oracle asserts about its own derived state.

        If these ever fire the *simulation* is wrong, not the application, and
        that distinction is worth being able to make quickly.
        """
        bad = []
        for lot, st in self.state().items():
            if st.on_hand < 0:
                bad.append(f"{lot}: on_hand {st.on_hand} < 0")
            for b in BUCKETS:
                if getattr(st, b) < 0:
                    bad.append(f"{lot}: {b} {getattr(st, b)} < 0")
            if st.reserved < 0:
                bad.append(f"{lot}: reserved {st.reserved} < 0")
            if st.reserved > st.on_hand:
                bad.append(f"{lot}: reserved {st.reserved} > on_hand {st.on_hand}")
            recomputed = q(st.on_hand + st.damaged + st.returned + st.in_transit)
            if recomputed != st.total:
                bad.append(f"{lot}: total {st.total} != parts {recomputed}")
        return bad

    def log(self, lot: str | None = None) -> list[Event]:
        evs = sorted(self._events, key=lambda e: e.seq)
        return [e for e in evs if lot is None or e.lot == lot]
