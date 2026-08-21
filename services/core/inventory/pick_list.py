"""E10 — what to bring from the depot to the shelf before the doors open.

The replenishment machinery already exists: shelves, placements, a dual-verified
session, a transfer ledger. What it has never had is an answer to *which items*
and *how many* — `create_session` takes a list of NDCs somebody typed in, and
FEFO-orders every lot of each. So the intelligence in the morning round is
currently a person remembering what ran out yesterday.

The target is a quantile, not an average, and that is the whole design:

    A shelf stocked to the mean runs out half the time.

Half of all days is not a service level anybody would choose out loud. So the
shelf is stocked to roughly the 90th percentile of what today is likely to take —
enough that running out before the next round is the exception rather than a
coin toss.

How that quantile is reached depends on the shape of the demand, which is E6's
business, and the two cases are genuinely different:

  **smooth / erratic** — many small draws a day, so the daily total is roughly
      normal: `mean + 1.28σ` for the ninetieth percentile.
  **intermittent / lumpy** — mostly nothing, occasionally a whole event. The
      normal quantile is meaningless here; what the shelf needs is enough to
      serve *one event*, because that is how the demand arrives.

Two hard refusals, both about physical reality rather than statistics:

  * a refrigerated drug is never proposed for a room-temperature shelf. Not a
    warning — the line is dropped and the reason given, because a warning on a
    picking list is read at speed by somebody holding a crate.
  * nothing quarantined, recalled or already expired is ever picked.

Pure functions over already-fetched rows; `as_of` is always injected.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from . import intermittent as IM
from .ledger import q

# z for the 90th percentile of a normal. A shelf stocked to the mean runs out
# half the time; this is the difference between "usually there" and "there".
SERVICE_Z = Decimal("1.28")
SERVICE_LEVEL = "90th percentile"

# Demand shapes where the normal quantile does not describe the daily total, and
# cover for one whole event is the meaningful target instead.
EVENT_SHAPED = ("intermittent", "lumpy")

# A pull smaller than this is not worth a person walking to the depot. Trips cost
# more than the units.
MIN_WORTH_PULLING = Decimal("1")

# Why an item is NOT on the round. Kept in step with what `build` actually
# emits: a vocabulary the code cannot produce is a promise the UI may render a
# case for and the engine can never reach.
SKIP_REASONS = ("no_measured_demand", "storage_mismatch", "shelf_full",
                "depot_empty")


@dataclass(frozen=True)
class PickLine:
    """One item to bring forward, and the lots to take it from."""
    ndc11: str
    drug_name: str | None
    shelf_id: str | None
    shelf_label: str | None
    on_shelf: Decimal
    target: Decimal | None
    shortfall: Decimal
    pull: Decimal
    lots: list[dict]                 # FEFO-ordered allocation
    demand_class: str
    basis: str                       # observed | sparse | no_history
    depot_short: bool                # the depot cannot cover the shelf
    explanation: str

    def as_dict(self) -> dict:
        return {"ndc11": self.ndc11, "drug_name": self.drug_name,
                "shelf_id": self.shelf_id, "shelf_label": self.shelf_label,
                "on_shelf": float(self.on_shelf),
                "target": None if self.target is None else float(self.target),
                "shortfall": float(self.shortfall), "pull": float(self.pull),
                "lots": list(self.lots), "demand_class": self.demand_class,
                "basis": self.basis, "depot_short": self.depot_short,
                "explanation": self.explanation}


@dataclass(frozen=True)
class PickList:
    lines: list[PickLine]
    skipped: list[dict]
    units: Decimal
    depot_shortfalls: int
    explanation: str
    concerns: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"lines": [l.as_dict() for l in self.lines],
                "skipped": list(self.skipped), "units": float(self.units),
                "depot_shortfalls": self.depot_shortfalls,
                "explanation": self.explanation, "concerns": list(self.concerns)}


def shelf_target(pattern: IM.Pattern, *, cover_days: int = 1) -> Decimal | None:
    """How much the shelf should hold to get through to the next round.

    None when there is no measured demand. A target invented for an unmeasured
    item fills the shelf with something nobody has asked for, and shelf space is
    the one thing in a pharmacy that cannot be ordered more of.
    """
    if pattern.mean_rate is None:
        return None
    days = Decimal(max(1, cover_days))
    if pattern.demand_class in EVENT_SHAPED and pattern.typical_event:
        # Demand arrives whole. Half an event on the shelf serves nobody.
        return q(pattern.typical_event)
    sd = pattern.stdev_daily or Decimal("0")
    return q(pattern.mean_rate * days + SERVICE_Z * sd * Decimal(
        str(float(days) ** 0.5)))


def _fefo(lots: list[dict], want: Decimal, *, as_of: date) -> tuple[list[dict], Decimal]:
    """Allocate `want` units across depot lots, oldest expiry first.

    Anything quarantined, recalled or already expired is not a candidate — a
    picking list is followed, not audited, and the person holding the crate is
    not the last line of defence.
    """
    usable = []
    for l in lots:
        if l.get("is_quarantined") or l.get("is_recalled"):
            continue
        exp = l.get("expiry_date")
        if exp is not None and exp <= as_of:
            continue
        if q(l.get("available") or 0) <= 0:
            continue
        usable.append(l)
    usable.sort(key=lambda l: (l.get("expiry_date") is None, l.get("expiry_date"),
                               str(l.get("lot_number") or "")))

    taken, left = [], want
    for l in usable:
        if left <= 0:
            break
        take = min(left, q(l.get("available") or 0))
        if take <= 0:
            continue
        taken.append({"lot_id": str(l.get("id")), "lot_number": l.get("lot_number"),
                      "expiry_date": (l["expiry_date"].isoformat()
                                      if l.get("expiry_date") else None),
                      "units": float(take)})
        left = q(left - take)
    return taken, q(want - left)


def _compatible(item: dict, shelf: dict | None) -> bool:
    """Whether this drug may physically live on that shelf."""
    if shelf is None:
        return True
    need = ("REFRIGERATED" if item.get("requires_refrigeration")
            else str(item.get("storage_condition") or "ROOM_TEMP"))
    return str(shelf.get("storage_condition") or "ROOM_TEMP") == need


def build(items: list[dict], *, as_of: date, cover_days: int = 1,
          window_days: int = 84) -> PickList:
    """The morning round: what to bring forward, in the order it is walked.

    `items`: [{ndc11, drug_name, requires_refrigeration, on_shelf, shelf,
               depot_lots: [...], fills: [...]}]
    """
    lines: list[PickLine] = []
    skipped: list[dict] = []
    short = 0

    for it in items:
        ndc = str(it.get("ndc11"))
        shelf = it.get("shelf")
        on_shelf = q(it.get("on_shelf") or 0)

        if not _compatible(it, shelf):
            # Dropped, not warned. A warning on a picking list is read at speed
            # by somebody holding a crate.
            skipped.append({
                "ndc11": ndc, "reason": "storage_mismatch",
                "explanation": (
                    f"{ndc} needs "
                    f"{'refrigeration' if it.get('requires_refrigeration') else 'a different storage condition'}"
                    f" and shelf {shelf.get('label')} is "
                    f"{shelf.get('storage_condition')} — not proposed, because a "
                    f"warning here would be read at speed.")})
            continue

        pattern = IM.assess(ndc, it.get("fills") or [],
                            window_days=window_days, as_of=as_of)
        target = shelf_target(pattern, cover_days=cover_days)

        if target is None:
            skipped.append({
                "ndc11": ndc, "reason": "no_measured_demand",
                "explanation": (
                    f"No dispensing recorded for {ndc} in {window_days} days, so "
                    f"there is no target to stock to. Shelf space is the one "
                    f"thing that cannot be ordered more of, and filling it with "
                    f"an invented figure spends it on nothing.")})
            continue

        shortfall = q(max(Decimal("0"), target - on_shelf))
        if shortfall < MIN_WORTH_PULLING:
            continue

        # Shelf capacity is physical, so it caps the request whatever the
        # target says.
        if shelf and shelf.get("capacity_units"):
            room = q(max(Decimal("0"),
                         q(shelf["capacity_units"]) - q(shelf.get("current_units") or 0)))
            shortfall = min(shortfall, room)
            if shortfall < MIN_WORTH_PULLING:
                skipped.append({
                    "ndc11": ndc, "reason": "shelf_full",
                    "explanation": (f"Shelf {shelf.get('label')} has no room for "
                                    f"more {ndc}.")})
                continue

        lots, pulled = _fefo(it.get("depot_lots") or [], shortfall, as_of=as_of)
        if pulled < MIN_WORTH_PULLING:
            skipped.append({
                "ndc11": ndc, "reason": "depot_empty",
                "explanation": (f"{ndc} is short on the shelf by {shortfall} and "
                                f"there is none in the depot — this is a "
                                f"purchasing problem, not a picking one.")})
            short += 1
            continue

        depot_short = pulled < shortfall
        if depot_short:
            short += 1

        why = (f"{on_shelf} on the shelf against a {target} target "
               f"({pattern.demand_class}"
               + (f", one event is {pattern.typical_event}"
                  if pattern.demand_class in EVENT_SHAPED and pattern.typical_event
                  else f", {SERVICE_LEVEL} of a day's demand")
               + f"). Bring {pulled}.")
        if depot_short:
            why += (f" The depot can only cover {pulled} of {shortfall} — the "
                    f"rest has to be ordered.")

        lines.append(PickLine(
            ndc11=ndc, drug_name=it.get("drug_name"),
            shelf_id=str(shelf["id"]) if shelf else None,
            shelf_label=shelf.get("label") if shelf else None,
            on_shelf=on_shelf, target=target, shortfall=shortfall, pull=pulled,
            lots=lots, demand_class=pattern.demand_class, basis=pattern.basis,
            depot_short=depot_short, explanation=why))

    # Walked in shelf order, so the round is one pass rather than a tour of the
    # dispensary per line.
    lines.sort(key=lambda l: (l.shelf_label is None, l.shelf_label or "", l.ndc11))
    units = q(sum((l.pull for l in lines), Decimal("0")))

    concerns: list[str] = []
    if short:
        concerns.append(
            f"{short} item(s) cannot be brought up to target from the depot. "
            f"That is a purchasing gap and no amount of picking will close it")
    unmeasured = sum(1 for s in skipped if s["reason"] == "no_measured_demand")
    if unmeasured:
        concerns.append(
            f"{unmeasured} item(s) have no measured demand and were left out "
            f"rather than given an invented target")

    return PickList(
        lines=lines, skipped=skipped, units=units, depot_shortfalls=short,
        concerns=concerns,
        explanation=(
            f"{len(lines)} item(s), {units} units, stocked to the "
            f"{SERVICE_LEVEL} of a day's demand — a shelf stocked to the average "
            f"runs out half the time. {len(skipped)} item(s) not proposed."))
