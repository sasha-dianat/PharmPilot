"""Valuation — what the stock is worth, and what the losses cost.

`inventory_lots.unit_cost` has always been there and nothing has ever added it
up. The consequences run through the whole section: shrinkage was reported in
units, so a hundred lost paracetamol tablets and a hundred lost insulin pens
read identically; the ABC classification had no cost basis until the cycle-count
planner needed one; and no write-off could state what it cost, which is the
number an owner actually decides on.

The awkward part of pharmacy valuation is that the same drug arrives at
different prices, repeatedly, in a currency that moves. Three lots of metformin
bought three months apart are three costs, and picking one is a policy decision
rather than an arithmetic one:

  FIFO      — value what remains at the price of the most recent purchases,
              because the oldest units are the ones FEFO already dispensed.
              Matches physical flow, and matches this ledger's own allocator.
  WEIGHTED  — one blended cost per item. Smoother, and it hides exactly the
              price movement an Iranian pharmacy most needs to see.

FIFO is the default here because it agrees with what the shelf actually does.
Both are provided, and every figure says which produced it — a valuation whose
method is implicit is a number two people will read differently.

Decimal throughout, quantised at the ledger's precision. Rial has no minor unit
in practice and float would introduce a rounding error into a financial total.

Pure functions over already-fetched rows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .ledger import q

METHODS = ("fifo", "weighted")
DEFAULT_METHOD = "fifo"

# Movement types that destroy value rather than move it. DISPENSE is absent on
# purpose: dispensed stock was sold, not lost, and folding it into shrinkage
# would make every busy day look like a theft.
LOSS_TYPES = {"WASTE", "EXPIRY_REMOVAL", "RECALL_REMOVAL", "COUNT_LOSS", "DAMAGE"}
# Value that leaves but is expected back as a credit note.
RECOVERABLE_TYPES = {"RETURN_TO_SUPPLIER", "SUPPLIER_CREDIT"}


@dataclass
class ItemValue:
    ndc11: str
    on_hand: Decimal
    value: Decimal
    unit_cost: Decimal | None
    method: str
    lots_costed: int
    lots_uncosted: int
    explanation: str = ""

    def as_dict(self) -> dict:
        return {"ndc11": self.ndc11, "on_hand": float(self.on_hand),
                "value": float(self.value),
                "unit_cost": None if self.unit_cost is None else float(self.unit_cost),
                "method": self.method, "lots_costed": self.lots_costed,
                "lots_uncosted": self.lots_uncosted,
                "explanation": self.explanation}


@dataclass
class Valuation:
    method: str
    total: Decimal
    items: list[ItemValue] = field(default_factory=list)
    uncosted_units: Decimal = Decimal("0")
    uncosted_items: int = 0
    buckets: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"method": self.method, "total": float(self.total),
                "items": [i.as_dict() for i in self.items],
                "uncosted_units": float(self.uncosted_units),
                "uncosted_items": self.uncosted_items,
                "buckets": {k: float(v) for k, v in self.buckets.items()},
                "coverage_note": (
                    f"{self.uncosted_items} item(s) hold "
                    f"{self.uncosted_units} units with no cost on file and are "
                    f"valued at zero — the total is a floor, not the figure."
                    if self.uncosted_items else
                    "Every unit on hand carries a cost.")}


def _lot_cost(lot: dict) -> Decimal | None:
    c = lot.get("unit_cost")
    return None if c is None else q(c)


def value_item(ndc11: str, lots: list[dict], *,
               method: str = DEFAULT_METHOD) -> ItemValue:
    """Value one item's lots.

    Lots with no cost on file are counted in `lots_uncosted` and contribute
    nothing rather than borrowing a sibling lot's price. Borrowing would produce
    a plausible total that no purchase supports — the same failure as the
    fabricated demand rate, in currency.
    """
    if method not in METHODS:
        raise ValueError(f"unknown valuation method {method!r}")

    on_hand = q(0)
    value = q(0)
    costed = uncosted = 0
    weighted_units = q(0)

    # FEFO issues the soonest-expiring first, so what remains is the later
    # purchases. Ordering by expiry matches the physical flow this ledger
    # actually performs.
    ordered = sorted(lots, key=lambda l: (l.get("expiry_date") is None,
                                          l.get("expiry_date") or "",
                                          str(l.get("lot_number") or "")))
    for lot in ordered:
        qty = q(lot.get("quantity_on_hand") or 0)
        if qty <= 0:
            continue
        on_hand = q(on_hand + qty)
        cost = _lot_cost(lot)
        if cost is None:
            uncosted += 1
            continue
        costed += 1
        value = q(value + qty * cost)
        weighted_units = q(weighted_units + qty)

    if method == "weighted" and weighted_units > 0:
        blended = q(value / weighted_units)
        value = q(blended * weighted_units)
        unit = blended
    else:
        unit = q(value / weighted_units) if weighted_units > 0 else None

    note = (f"{costed} lot(s) costed at their own purchase price"
            if method == "fifo" else
            f"{costed} lot(s) blended into one cost")
    if uncosted:
        note += (f"; {uncosted} lot(s) have no cost on file and contribute "
                 f"nothing rather than borrowing a sibling's price")

    return ItemValue(ndc11=ndc11, on_hand=on_hand, value=value, unit_cost=unit,
                     method=method, lots_costed=costed, lots_uncosted=uncosted,
                     explanation=note)


def value_stock(lots: list[dict], *, method: str = DEFAULT_METHOD) -> Valuation:
    """Value everything, and say plainly what could not be valued.

    The holding buckets are valued separately. Damaged stock awaiting a supplier
    claim and returned stock awaiting a credit note are both real assets, and
    folding them into on-hand would overstate what is available to sell while
    leaving them invisible when they need chasing.
    """
    by_ndc: dict[str, list[dict]] = {}
    for lot in lots:
        by_ndc.setdefault(str(lot.get("ndc11")), []).append(lot)

    items = [value_item(ndc, rows, method=method)
             for ndc, rows in sorted(by_ndc.items())]

    buckets = {"damaged": q(0), "returned": q(0), "in_transit": q(0)}
    for lot in lots:
        cost = _lot_cost(lot)
        if cost is None:
            continue
        for name, col in (("damaged", "quantity_damaged"),
                          ("returned", "quantity_returned"),
                          ("in_transit", "quantity_in_transit")):
            buckets[name] = q(buckets[name] + q(lot.get(col) or 0) * cost)

    uncosted_units = q(0)
    for lot in lots:
        if _lot_cost(lot) is None:
            uncosted_units = q(uncosted_units + q(lot.get("quantity_on_hand") or 0))

    return Valuation(
        method=method,
        total=q(sum((i.value for i in items), Decimal("0"))),
        items=sorted(items, key=lambda i: -i.value),
        uncosted_units=uncosted_units,
        uncosted_items=sum(1 for i in items if i.lots_uncosted),
        buckets=buckets,
    )


@dataclass
class Shrinkage:
    period_days: int
    total_lost: Decimal
    recoverable: Decimal
    by_type: dict
    by_item: list[dict]
    explanation: str

    def as_dict(self) -> dict:
        return {"period_days": self.period_days,
                "total_lost": float(self.total_lost),
                "recoverable": float(self.recoverable),
                "by_type": {k: float(v) for k, v in self.by_type.items()},
                "by_item": self.by_item, "explanation": self.explanation}


def shrinkage(movements: list[dict], *, period_days: int = 90) -> Shrinkage:
    """What the losses cost, separated from what should come back.

    Reporting shrinkage in units treats a hundred lost paracetamol tablets and a
    hundred lost insulin pens as the same event. In currency they are not close,
    and the currency figure is the one an owner acts on.

    Damaged and returned stock is reported apart from written-off stock because
    the first is a claim somebody should be chasing and the second is money
    already gone. Merging them lets a recoverable loss quietly become a real
    one when nobody files the claim.
    """
    lost = q(0)
    recoverable = q(0)
    by_type: dict[str, Decimal] = {}
    per_item: dict[str, Decimal] = {}

    for m in movements:
        mtype = str(m.get("movement_type") or "")
        if mtype not in LOSS_TYPES and mtype not in RECOVERABLE_TYPES:
            continue
        cost = m.get("unit_cost")
        if cost is None:
            continue
        units = abs(q(m.get("quantity_delta") or 0))
        amount = q(units * q(cost))
        by_type[mtype] = q(by_type.get(mtype, Decimal("0")) + amount)
        if mtype in RECOVERABLE_TYPES or mtype == "DAMAGE":
            recoverable = q(recoverable + amount)
        else:
            lost = q(lost + amount)
            ndc = str(m.get("ndc11"))
            per_item[ndc] = q(per_item.get(ndc, Decimal("0")) + amount)

    by_item = sorted(
        ({"ndc11": k, "value": float(v)} for k, v in per_item.items()),
        key=lambda r: -r["value"])

    return Shrinkage(
        period_days=period_days, total_lost=lost, recoverable=recoverable,
        by_type=by_type, by_item=by_item[:20],
        explanation=(
            f"{lost} written off over {period_days} days; {recoverable} held as "
            f"damaged or returned stock that a supplier claim should recover. "
            f"The second figure becomes the first if nobody files the claim."),
    )


def drift(book_value, computed: Valuation,
          tolerance: Decimal = Decimal("1")) -> dict | None:
    """Whether a stored valuation still matches the lots underneath it."""
    if book_value is None:
        return None
    book = q(book_value)
    gap = q(book - computed.total)
    if abs(gap) <= tolerance:
        return None
    return {"book_value": float(book), "computed": float(computed.total),
            "drift": float(gap), "method": computed.method}
