"""What is actually on the shelf, and what that is worth right now.

`shelf_placements` records that N units of a lot were put on shelf A-03. Until
now that number only ever went **up**: `depot_transfer` created placements and
added to `pharmacy_shelves.current_units`, and nothing anywhere subtracted. A
dispense reduced `inventory_lots.quantity_on_hand` and left the shelf believing
it still held everything ever brought to it.

Three things depended on that number and none of them could work:

  * the morning round (E10) reads `SUM(placements.units)` as what is already on
    the shelf, so after the first day the shelf looks permanently full and the
    round proposes nothing;
  * nobody could say what the sales floor is holding, or what it is worth;
  * theft detection is *expected against counted*, and expected was fiction.

**The distinction this module exists to keep.** When a delivery is placed on a
shelf, somebody scanned it and attested to it — that is an **observed** fact.
When a prescription is dispensed, nobody scans the shelf; the units left the
lot, and which shelf they came off is *inferred* by allocating against that
lot's placements. Both change the number. Only one of them is a measurement.

That matters because the whole point of a shelf count is to compare what should
be there with what is, and accuse the difference. A variance that is smaller
than the movement we merely inferred is not evidence of anything — it may be an
allocation artefact. `reconcile` refuses to call that theft, and says why.

Pure functions over already-fetched rows; `now`/`as_of` are always injected.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from .ledger import q

# How a change to a shelf figure came to be known.
OBSERVED = "observed"      # somebody scanned it and attested to it
INFERRED = "inferred"      # derived from a dispense; nobody looked at the shelf
BASES = (OBSERVED, INFERRED)

# A counted variance below this share of the units that were only *inferred* off
# the shelf is not evidence. Allocating a dispense across two placements of the
# same lot is a guess about which shelf the hand reached for, and a detector that
# calls its own guesswork theft will accuse an honest pharmacy every week.
INFERENCE_NOISE = Decimal("1.0")

# Below this many units a variance is a miscount, not a finding. Pharmacies
# fumble a tablet; a system that opens a case over one is switched off.
MIN_UNITS_TO_JUDGE = Decimal("2")

VERDICTS = ("agrees", "shrinkage", "surplus", "inconclusive", "uncounted")


@dataclass(frozen=True)
class Take:
    """Units taken off one placement, and how confidently we know it."""
    placement_id: str
    shelf_id: str
    lot_id: str
    ndc11: str
    before: Decimal
    units: Decimal
    after: Decimal
    basis: str

    def as_dict(self) -> dict:
        return {"placement_id": self.placement_id, "shelf_id": self.shelf_id,
                "lot_id": self.lot_id, "ndc11": self.ndc11,
                "before": float(self.before), "units": float(self.units),
                "after": float(self.after), "basis": self.basis}


@dataclass(frozen=True)
class Allocation:
    takes: list[Take]
    from_shelf: Decimal
    from_backstock: Decimal
    explanation: str

    def as_dict(self) -> dict:
        return {"takes": [t.as_dict() for t in self.takes],
                "from_shelf": float(self.from_shelf),
                "from_backstock": float(self.from_backstock),
                "explanation": self.explanation}


def allocate(placements: list[dict], units, *, lot_id: str, ndc11: str,
             basis: str = INFERRED) -> Allocation:
    """Take `units` off this lot's placements, oldest placement first.

    Oldest first because a pharmacy works the front of the shelf, and because it
    matches the FEFO order the units were placed in. Where a lot sits on two
    shelves the choice is genuinely unknowable without a scan, which is exactly
    why the result is marked `inferred`.

    Units the shelf cannot cover came from depot back-stock. That is **not** an
    error: a lot of 100 with only 40 placed is the ordinary case, and dispensing
    60 takes 40 off the shelf and 20 from behind it.
    """
    want = q(units)
    if want <= 0:
        return Allocation([], q(0), q(0), "nothing to take")

    mine = [p for p in placements
            if str(p.get("inventory_lot_id")) == str(lot_id)
            and q(p.get("units") or 0) > 0]
    mine.sort(key=lambda p: (p.get("placed_at") is None, p.get("placed_at"),
                             str(p.get("id"))))

    takes: list[Take] = []
    left = want
    for p in mine:
        if left <= 0:
            break
        have = q(p.get("units") or 0)
        take = min(left, have)
        if take <= 0:
            continue
        takes.append(Take(
            placement_id=str(p.get("id")), shelf_id=str(p.get("shelf_id")),
            lot_id=str(lot_id), ndc11=ndc11, before=have, units=take,
            after=q(have - take), basis=basis))
        left = q(left - take)

    from_shelf = q(want - left)
    why = (f"{from_shelf} off the shelf"
           + (f", {left} from depot back-stock" if left > 0 else "")
           + (". Which shelf the hand reached for was not scanned, so this is "
              "inferred from the lot's placements rather than observed."
              if basis == INFERRED and len(mine) > 1 else "."))
    return Allocation(takes=takes, from_shelf=from_shelf, from_backstock=q(left),
                      explanation=why)


# ── what the sales floor is holding, and what it is worth ────────────────

@dataclass(frozen=True)
class ShelfPosition:
    shelf_id: str
    label: str | None
    zone: str | None
    units: Decimal
    lines: int
    value: Decimal | None
    unpriced_lines: int
    capacity_units: int | None
    utilisation: Decimal | None

    def as_dict(self) -> dict:
        return {"shelf_id": self.shelf_id, "label": self.label, "zone": self.zone,
                "units": float(self.units), "lines": self.lines,
                "value": None if self.value is None else float(self.value),
                "unpriced_lines": self.unpriced_lines,
                "capacity_units": self.capacity_units,
                "utilisation": None if self.utilisation is None
                               else float(self.utilisation)}


@dataclass(frozen=True)
class FloorPosition:
    at: datetime
    shelves: list[ShelfPosition]
    units: Decimal
    value: Decimal
    unpriced_lines: int
    explanation: str

    def as_dict(self) -> dict:
        return {"at": self.at.isoformat(),
                "shelves": [s.as_dict() for s in self.shelves],
                "units": float(self.units), "value": float(self.value),
                "unpriced_lines": self.unpriced_lines,
                "explanation": self.explanation}


def position(placements: list[dict], *, at: datetime) -> FloorPosition:
    """What is on the sales floor this second, and what it would ring up for.

    Valued at the **sell** price, not cost: this answers "what is standing on
    the floor", which is a retail figure. A line whose product has no sell price
    contributes units and no money, and the count of those is carried through —
    a total that silently omits them reads as smaller than the floor really is.
    """
    by_shelf: dict[str, dict] = {}
    total_units = q(0)
    total_value = q(0)
    unpriced = 0

    for p in placements:
        units = q(p.get("units") or 0)
        if units <= 0:
            continue
        sid = str(p.get("shelf_id"))
        e = by_shelf.setdefault(sid, {
            "label": p.get("label"), "zone": p.get("zone"),
            "units": q(0), "lines": 0, "value": q(0), "unpriced": 0,
            "capacity_units": p.get("capacity_units")})
        e["units"] = q(e["units"] + units)
        e["lines"] += 1
        total_units = q(total_units + units)

        price = p.get("sell_price")
        if price is None:
            e["unpriced"] += 1
            unpriced += 1
            continue
        line = q(q(price) * units)
        e["value"] = q(e["value"] + line)
        total_value = q(total_value + line)

    shelves = []
    for sid, e in sorted(by_shelf.items(), key=lambda kv: (kv[1]["label"] or "")):
        cap = e["capacity_units"]
        shelves.append(ShelfPosition(
            shelf_id=sid, label=e["label"], zone=e["zone"], units=e["units"],
            lines=e["lines"],
            value=None if e["lines"] == e["unpriced"] else e["value"],
            unpriced_lines=e["unpriced"], capacity_units=cap,
            utilisation=q(e["units"] / Decimal(cap)) if cap else None))

    note = (f" {unpriced} line(s) carry no sell price, so this value is a floor "
            f"rather than a total." if unpriced else "")
    return FloorPosition(
        at=at, shelves=shelves, units=total_units, value=total_value,
        unpriced_lines=unpriced,
        explanation=(f"{total_units} unit(s) across {len(shelves)} shelf(s), "
                     f"worth {total_value} at shelf prices.{note}"))


# ── expected against counted: the only honest theft signal ───────────────

@dataclass(frozen=True)
class ShelfVariance:
    shelf_id: str
    ndc11: str
    expected: Decimal
    counted: Decimal
    variance: Decimal            # counted − expected; negative is missing stock
    inferred_units: Decimal      # movement that was never scanned
    verdict: str
    value_at_risk: Decimal | None
    explanation: str
    concerns: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"shelf_id": self.shelf_id, "ndc11": self.ndc11,
                "expected": float(self.expected), "counted": float(self.counted),
                "variance": float(self.variance),
                "inferred_units": float(self.inferred_units),
                "verdict": self.verdict,
                "value_at_risk": None if self.value_at_risk is None
                                 else float(self.value_at_risk),
                "explanation": self.explanation, "concerns": list(self.concerns)}


def reconcile(*, shelf_id: str, ndc11: str, expected, counted,
              inferred_units=0, sell_price=None) -> ShelfVariance:
    """Compare what the books say a shelf holds against what was counted.

    This is the theft detector, and most of its work is refusing to be one.

    **Shrinkage and surplus are different problems.** Counted below expected is
    stock that left without a record — the case worth investigating. Counted
    *above* expected means the books are wrong, almost always a placement nobody
    recorded, and treating that as a finding against a person would be absurd.

    **A variance no larger than the inferred movement is inconclusive.** Every
    dispense against a lot sitting on two shelves guessed which shelf the hand
    reached for. If four units were guessed and three are missing, the missing
    three may be standing on the next shelf along. Calling that theft accuses
    somebody on the strength of our own arithmetic, and one false accusation
    ends the credibility of every true one.
    """
    exp = q(expected)
    inferred = q(inferred_units)
    concerns: list[str] = []
    # An uncounted shelf is checked before anything is quantised: `counted` is
    # None there, and a shelf nobody counted has no variance to compute.
    got = q(0) if counted is None else q(counted)
    var = q(0) if counted is None else q(got - exp)

    def out(verdict: str, why: str, at_risk=None) -> ShelfVariance:
        return ShelfVariance(
            shelf_id=shelf_id, ndc11=ndc11, expected=exp, counted=got,
            variance=var, inferred_units=inferred, verdict=verdict,
            value_at_risk=at_risk, explanation=why, concerns=concerns)

    if counted is None:
        return out("uncounted", "This shelf was not counted; nothing is claimed.")

    if inferred > 0:
        concerns.append(
            f"{inferred} unit(s) of this line left the shelf without anybody "
            f"scanning it — the position was derived from the lot, not observed")

    if var == 0:
        return out("agrees", f"Counted {got}, expected {exp}.")

    if var > 0:
        return out("surplus",
                   f"Counted {got} against an expected {exp} — {var} more than "
                   f"the books show. Stock does not appear by itself, so this is "
                   f"a placement that was never recorded, not a loss.")

    missing = -var
    if missing < MIN_UNITS_TO_JUDGE:
        return out("inconclusive",
                   f"{missing} unit(s) short. Below {MIN_UNITS_TO_JUDGE} that is "
                   f"a miscount, and a system that opens a case over one gets "
                   f"switched off.")

    if missing <= q(inferred * INFERENCE_NOISE):
        return out("inconclusive",
                   f"{missing} unit(s) short, but {inferred} left this shelf "
                   f"without being scanned — the missing units may be standing "
                   f"on the next shelf along. One false accusation ends the "
                   f"credibility of every true one, so this is not called a loss.")

    at_risk = q(missing * q(sell_price)) if sell_price is not None else None
    if sell_price is None:
        concerns.append("no shelf price for this product, so the loss is in "
                        "units and not in money")
    return out("shrinkage",
               f"{missing} unit(s) missing from shelf {shelf_id}: counted {got} "
               f"against {exp} on the books"
               + (f", worth {at_risk}." if at_risk is not None else ".")
               + (f" Only {inferred} unit(s) of movement here were unscanned, so "
                  f"the gap is larger than the inference can explain."
                  if inferred else ""),
               at_risk=at_risk)


def worth_investigating(v: ShelfVariance) -> bool:
    """Only a shrinkage larger than our own guesswork reaches a person."""
    return v.verdict == "shrinkage"
