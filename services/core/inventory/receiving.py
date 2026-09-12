"""Closing the loop from a delivery back to the order that asked for it.

`purchase_orders.ordered_at` is stamped when an order is submitted. Nothing has
ever stamped `received_at`, and nothing has ever written
`purchase_order_lines.quantity_received` — five places read those columns and no
code path fills them. So the two facts every supplier engine is built on have
never once been recorded:

    lead time      = received_at - ordered_at        (E11)
    fill rate      = Σ received / Σ ordered          (E12)

`supply_warning` (service ⑰) already derives fill-rate from exactly these
columns, which is why it has never produced a real number.

This is deliberately built *before* the engines that need it. The alternative —
ship the detectors now, capture the data later — arrives in three months with a
working detector, no history, and no way to tune it except by guessing, which is
where the fabricated demand signal came from.

Three shapes a delivery can take, and the difference matters to a supplier
scorecard:

  **short**   — less arrived than was ordered. The supplier's problem, and the
                one that costs the pharmacy a sale.
  **exact**   — what was asked for.
  **over**    — more arrived than was ordered. Not a favour: it is unbudgeted
                stock the pharmacy may not be able to sell before it expires,
                and it should be visible rather than absorbed.

Pure functions over already-fetched rows; `now` is always injected.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .ledger import q

# Delivered quantity within this fraction of the order counts as exact. Whole
# packs rarely split, but a 1-2 unit difference on a large line is a rounding
# artefact rather than a supplier failure, and scoring it as one would make
# every reliable supplier look unreliable.
EXACT_TOLERANCE = Decimal("0.02")

LINE_STATUSES = ("ordered", "partial", "complete", "over", "backordered",
                 "substituted")
ORDER_STATUSES = ("draft", "submitted", "acknowledged", "partial", "complete",
                  "cancelled")

# The status a short-shipped line takes when the pharmacy accepts that the rest
# is not coming. Distinct from `cancelled`, which is the pharmacy withdrawing a
# request: one is the supplier's failure and belongs in its fill rate, the other
# is not and does not.
CLOSED_SHORT = "backordered"

# The supplier sent a different product instead of the one ordered. Settled, and
# counted at what actually arrived against the ordered molecule — which is
# nothing.
SUBSTITUTED = "substituted"

# Lines that will not change again. Everything else is still in flight, and a
# line still in flight has not failed to arrive.
SETTLED = ("complete", "over", CLOSED_SHORT, SUBSTITUTED)


class ReceivingError(ValueError):
    """A receipt that cannot be reconciled to its order as asked."""


@dataclass(frozen=True)
class LineMatch:
    """One order line, and what this delivery does to it."""
    line_id: str
    ndc11: str
    ordered: Decimal
    already_received: Decimal
    now_receiving: Decimal
    total_received: Decimal
    status: str
    shape: str                      # short | exact | over
    explanation: str

    def as_dict(self) -> dict:
        return {"line_id": self.line_id, "ndc11": self.ndc11,
                "ordered": float(self.ordered),
                "already_received": float(self.already_received),
                "now_receiving": float(self.now_receiving),
                "total_received": float(self.total_received),
                "status": self.status, "shape": self.shape,
                "explanation": self.explanation}


def match_line(lines: list[dict], ndc11: str) -> dict | None:
    """The open order line this delivery belongs to.

    Oldest first, and only lines still expecting stock. A supplier delivering
    against the older of two open lines is the ordinary case, and matching the
    newest would leave the older one permanently outstanding — which reads as a
    supplier failure that never happened.
    """
    candidates = [
        l for l in lines
        if str(l.get("ndc11")) == str(ndc11)
        and str(l.get("status")) not in ("complete", "cancelled", CLOSED_SHORT,
                                         SUBSTITUTED)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda l: (l.get("created_at") is None,
                                   l.get("created_at")))
    return candidates[0]


def apply_receipt(line: dict, quantity) -> LineMatch:
    """What receiving `quantity` does to this order line."""
    qty = q(quantity)
    if qty <= 0:
        raise ReceivingError("received quantity must be positive")

    ordered = q(line.get("quantity_ordered") or 0)
    already = q(line.get("quantity_received") or 0)
    total = q(already + qty)

    if ordered <= 0:
        # An order line for nothing cannot be over- or under-delivered.
        return LineMatch(
            line_id=str(line.get("id")), ndc11=str(line.get("ndc11")),
            ordered=ordered, already_received=already, now_receiving=qty,
            total_received=total, status="complete", shape="exact",
            explanation=f"{qty} received against a line with no ordered quantity.")

    tolerance = q(ordered * EXACT_TOLERANCE)
    short_by = q(ordered - total)

    if abs(short_by) <= tolerance:
        shape, status = "exact", "complete"
        why = f"{total} of {ordered} received — complete."
    elif short_by > 0:
        shape, status = "short", "partial"
        why = (f"{total} of {ordered} received — {short_by} still outstanding. "
               f"A short delivery is the supplier's, and it is the one that "
               f"costs a sale.")
    else:
        shape, status = "over", "over"
        why = (f"{total} received against {ordered} ordered — {abs(short_by)} "
               f"more than asked for. Unbudgeted stock that may not sell before "
               f"it expires.")

    return LineMatch(
        line_id=str(line.get("id")), ndc11=str(line.get("ndc11")),
        ordered=ordered, already_received=already, now_receiving=qty,
        total_received=total, status=status, shape=shape, explanation=why)


def order_status(lines: list[dict]) -> str:
    """The order's status, derived from its lines rather than set by hand."""
    live = [l for l in lines if str(l.get("status")) != "cancelled"]
    if not live:
        return "cancelled"
    if all(str(l.get("status")) in SETTLED for l in live):
        return "complete"
    if any(q(l.get("quantity_received") or 0) > 0 for l in live):
        return "partial"
    return "submitted"


def completion(lines: list[dict], *, now: datetime) -> datetime | None:
    """When the order became fully received — the other half of the lead time.

    None while anything is outstanding. Stamping `received_at` on the first
    delivery would make a part-filled order look faster than it was, and lead
    time is what safety stock is computed from.
    """
    return now if order_status(lines) == "complete" else None


def close_short(lines: list[dict]) -> list[LineMatch]:
    """Accept that the rest of this order is not coming.

    Without this an order that was short-shipped stays open for ever, and the
    consequence is worse than untidy bookkeeping: the order never completes, so
    `received_at` is never stamped, so the supplier never acquires a lead time —
    and a supplier that *always* short-ships ends up indistinguishable from one
    that has never delivered at all. The engine goes blind to precisely the
    behaviour it exists to catch.

    Outstanding lines close as `backordered`, deliberately not `cancelled`.
    A cancelled line is the pharmacy withdrawing a request and is excluded from
    the fill rate; a backordered line is the supplier failing to deliver and is
    counted at what actually arrived. Closing an order must not erase the
    failure that made closing it necessary.

    The *order* then reads `complete`, which is the only settled value the
    column allows and does not mean it went well. What went wrong is on the
    lines, and it is what the fill rate is computed from.
    """
    out = []
    for line in lines:
        status = str(line.get("status"))
        if status in SETTLED or status == "cancelled":
            continue
        ordered = q(line.get("quantity_ordered") or 0)
        got = q(line.get("quantity_received") or 0)
        out.append(LineMatch(
            line_id=str(line.get("id")), ndc11=str(line.get("ndc11")),
            ordered=ordered, already_received=got, now_receiving=q(0),
            total_received=got, status=CLOSED_SHORT, shape="short",
            explanation=(
                f"Closed with {got} of {ordered} delivered — {q(ordered - got)} "
                f"never arrived. Counted against the supplier rather than "
                f"written off the order.")))
    return out


@dataclass(frozen=True)
class FillRate:
    """How much of what was ordered actually arrived, per supplier."""
    supplier: str
    lines: int
    ordered: Decimal
    received: Decimal
    rate: Decimal | None
    basis: str
    explanation: str

    def as_dict(self) -> dict:
        return {"supplier": self.supplier, "lines": self.lines,
                "ordered": float(self.ordered), "received": float(self.received),
                "rate": None if self.rate is None else float(self.rate),
                "basis": self.basis, "explanation": self.explanation}


# Below this many closed lines a fill rate is an anecdote with a percentage
# sign on it, and quoting one to a supplier is worse than saying nothing.
MIN_LINES_FOR_RATE = 5


def fill_rate(rows: list[dict], *, supplier: str = "unknown") -> FillRate:
    """Fill rate from settled order lines, or an honest refusal.

    Only settled lines count. A part-filled line is still in flight and the
    balance may arrive tomorrow; scoring it as a shortfall today penalises a
    supplier for an order placed yesterday. A line the pharmacy has accepted
    will never be completed is settled — as `backordered`, at whatever actually
    arrived — which is how a chronic short-shipper reaches this calculation at
    all. See `close_short`.

    Over-delivery is capped at the ordered quantity. A supplier who sends 200
    against an order of 100 has not achieved a 200% fill rate, and letting the
    excess offset a genuine shortfall elsewhere would hide the failure the
    metric exists to find.
    """
    closed = [r for r in rows if str(r.get("status")) in SETTLED]
    ordered = q(sum((q(r.get("quantity_ordered") or 0) for r in closed),
                    Decimal("0")))
    received = q(sum((min(q(r.get("quantity_received") or 0),
                          q(r.get("quantity_ordered") or 0))
                      for r in closed), Decimal("0")))

    if len(closed) < MIN_LINES_FOR_RATE or ordered <= 0:
        return FillRate(
            supplier=supplier, lines=len(closed), ordered=ordered,
            received=received, rate=None, basis="insufficient_history",
            explanation=(
                f"{len(closed)} delivered line(s) — too few for a fill rate. "
                f"A percentage from this would be an anecdote, and quoting one "
                f"to a supplier is worse than saying nothing."))

    rate = q(received / ordered)
    return FillRate(
        supplier=supplier, lines=len(closed), ordered=ordered,
        received=received, rate=rate, basis="observed",
        explanation=(f"{received} of {ordered} units delivered across "
                     f"{len(closed)} lines."))


# ── When what arrived is not what was ordered ────────────────────────────

def substitution(line: dict, *, delivered_ndc: str, quantity) -> LineMatch:
    """The supplier sent something else instead of what was ordered.

    `LINE_STATUSES` has carried `substituted` since the model was written and
    nothing has ever produced it, which is why E12 reports its substitution
    count as `not_captured` — a zero there meant unmeasured, not never. This is
    where it starts being measured.

    A substitution is deliberately **not** a fill. The ordered molecule did not
    arrive, and counting the replacement towards the fill rate would let a
    supplier who never once sent what was asked for score a perfect record. It
    closes the line — nothing more is coming against it — and it is counted as a
    line the supplier did not fill, because that is what happened.

    Whether the substitute is clinically acceptable is a pharmacist's judgement
    and is not made here. This records that it happened.
    """
    qty = q(quantity)
    if qty <= 0:
        raise ReceivingError("substituted quantity must be positive")
    if str(delivered_ndc) == str(line.get("ndc11")):
        raise ReceivingError(
            "that is the product that was ordered, not a substitute — receive it "
            "as an ordinary delivery")

    ordered = q(line.get("quantity_ordered") or 0)
    already = q(line.get("quantity_received") or 0)
    return LineMatch(
        line_id=str(line.get("id")), ndc11=str(line.get("ndc11")),
        ordered=ordered, already_received=already, now_receiving=q(0),
        total_received=already, status="substituted", shape="short",
        explanation=(
            f"{qty} of {delivered_ndc} delivered against an order for {ordered} "
            f"of {line.get('ndc11')}. The line is closed as substituted: what "
            f"was ordered did not arrive, so it does not count towards the "
            f"supplier's fill rate. Whether the substitute is acceptable is a "
            f"pharmacist's decision, not this one."))
