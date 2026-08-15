"""Which stock will expire before it sells, and what to do while there is time.

The existing expiry check answers "what has already expired" — a write-off
waiting to happen, discovered too late to do anything but destroy it. This
answers the question worth money: *of the stock on the shelf today, how much
will still be there when it expires?*

Three things make this harder than `quantity / demand`:

**FEFO decides who is at risk.** The soonest-expiring lot is consumed first, so
a later lot's exposure depends on everything in front of it. Two lots of 100
units with a demand of 1/day are not each "100 units at risk" — the first sells,
the second is the one in trouble. Risk is therefore allocated across lots in the
order the allocator will actually reach them.

**No demand is not zero demand.** An item with no measured rate cannot be
projected at all. It is reported as unknown exposure with its quantity and its
expiry date, not silently as zero risk (which reads as safe) and not as total
risk (which floods the list with dead stock nobody can act on).

**The action depends on how long is left.** Ninety days out, a discount or a
transfer to a busier branch still works. Ten days out only a supplier return
does, and only if the return window is open. Past that it is a write-off, and
the only thing left to decide is whether it was avoidable. A risk report that
does not say which of those applies leaves the reader to guess.

Pure functions over already-fetched rows; `as_of` is always injected.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .ledger import q

# Horizons that change what can still be done, not arbitrary buckets.
ACT_NOW_DAYS = 30        # only a return or a fast discount will clear it
NEGOTIABLE_DAYS = 90     # a discount or a transfer has time to work
WATCH_DAYS = 180         # worth knowing about, nothing to do yet

# Most suppliers accept returns up to some weeks before expiry, never after.
# Declared, not measured — no supplier agreement is in the data yet, and a
# guessed window would send staff to negotiate a return that cannot happen.
DEFAULT_RETURN_WINDOW_DAYS = 90

ACTIONS = ("return_to_supplier", "discount_or_promote", "transfer_out",
           "watch", "write_off", "unknown")


@dataclass
class LotRisk:
    lot_id: str
    ndc11: str
    lot_number: str
    expiry: date
    days_left: int
    on_hand: Decimal
    projected_sales: Decimal | None   # None when demand is unmeasured
    at_risk_units: Decimal | None
    at_risk_value: Decimal | None
    unit_cost: Decimal | None
    basis: str                        # observed | sparse | no_history
    action: str
    severity: str
    explanation: str

    def as_dict(self) -> dict:
        f = lambda v: None if v is None else float(v)   # noqa: E731
        return {"lot_id": self.lot_id, "ndc11": self.ndc11,
                "lot_number": self.lot_number, "expiry": self.expiry.isoformat(),
                "days_left": self.days_left, "on_hand": float(self.on_hand),
                "projected_sales": f(self.projected_sales),
                "at_risk_units": f(self.at_risk_units),
                "at_risk_value": f(self.at_risk_value),
                "unit_cost": f(self.unit_cost), "basis": self.basis,
                "action": self.action, "severity": self.severity,
                "explanation": self.explanation}


def _action_for(days_left: int, *, at_risk: Decimal | None,
                return_window: int) -> tuple[str, str]:
    """What can still be done, and how loudly to say it."""
    if days_left < 0:
        return "write_off", "critical"
    if at_risk is None:
        return "unknown", "info"
    if at_risk <= 0:
        return "watch", "info"
    if days_left <= ACT_NOW_DAYS:
        # A return is only possible while the supplier's window is open.
        if days_left <= return_window:
            return "return_to_supplier", "high"
        return "discount_or_promote", "high"
    if days_left <= NEGOTIABLE_DAYS:
        return "discount_or_promote", "medium"
    if days_left <= WATCH_DAYS:
        return "transfer_out", "medium"
    return "watch", "info"


def assess_item(lots: list[dict], *, avg_daily_demand, basis: str,
                as_of: date, return_window: int = DEFAULT_RETURN_WINDOW_DAYS,
                ) -> list[LotRisk]:
    """Exposure per lot for one item, allocated in FEFO order.

    `lots`: {lot_id, ndc11, lot_number, expiry_date, quantity_on_hand, unit_cost}

    Demand is consumed by the soonest-expiring lot first, because that is what
    the allocator does. Charging every lot the full demand would understate
    risk; charging none of them would overstate it.
    """
    usable = [l for l in lots if q(l.get("quantity_on_hand") or 0) > 0
              and l.get("expiry_date") is not None]
    usable.sort(key=lambda l: (l["expiry_date"], str(l.get("lot_number") or "")))

    rate = None if avg_daily_demand is None else q(avg_daily_demand)
    if basis == "no_history":
        rate = None

    out: list[LotRisk] = []
    # Demand already spoken for by lots that expire earlier.
    consumed_by_earlier = q(0)

    for lot in usable:
        expiry = lot["expiry_date"]
        days_left = expiry.toordinal() - as_of.toordinal()
        on_hand = q(lot.get("quantity_on_hand") or 0)
        cost = lot.get("unit_cost")
        cost = None if cost is None else q(cost)

        if rate is None or rate <= 0:
            projected = at_risk = None
            why = ("No measured demand, so how much of this will sell before "
                   f"{expiry.isoformat()} cannot be projected — "
                   f"{on_hand} units are exposed, not none.")
        else:
            # Whatever this item sells between now and this lot's expiry, minus
            # what the lots in front of it will already have taken.
            window = max(0, days_left)
            total_demand = q(rate * Decimal(window))
            projected = q(max(Decimal("0"), total_demand - consumed_by_earlier))
            projected = min(projected, on_hand)
            at_risk = q(on_hand - projected)
            consumed_by_earlier = q(consumed_by_earlier + projected)
            why = (f"{on_hand} on hand, about {projected} will sell in the "
                   f"{max(0, days_left)} days before it expires at "
                   f"{rate}/day — {at_risk} would be left.")

        action, severity = _action_for(days_left, at_risk=at_risk,
                                       return_window=return_window)
        if days_left < 0:
            why = (f"Expired {abs(days_left)} days ago; {on_hand} units are "
                   f"already a write-off.")

        out.append(LotRisk(
            lot_id=str(lot.get("lot_id") or lot.get("id")),
            ndc11=str(lot.get("ndc11")),
            lot_number=str(lot.get("lot_number") or ""),
            expiry=expiry, days_left=days_left, on_hand=on_hand,
            projected_sales=projected, at_risk_units=at_risk,
            at_risk_value=(None if at_risk is None or cost is None
                           else q(at_risk * cost)),
            unit_cost=cost, basis=basis, action=action, severity=severity,
            explanation=why))
    return out


@dataclass
class Exposure:
    """The whole shelf's expiry position."""
    as_of: date
    lots: list[LotRisk] = field(default_factory=list)
    value_at_risk: Decimal = Decimal("0.000")
    units_at_risk: Decimal = Decimal("0.000")
    unknown_lots: int = 0
    unknown_units: Decimal = Decimal("0.000")
    by_action: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "as_of": self.as_of.isoformat(),
            "value_at_risk": float(self.value_at_risk),
            "units_at_risk": float(self.units_at_risk),
            "unknown_lots": self.unknown_lots,
            "unknown_units": float(self.unknown_units),
            "by_action": {k: float(v) for k, v in self.by_action.items()},
            "lots": [l.as_dict() for l in self.lots],
            "coverage_note": (
                f"{self.unknown_lots} lot(s) holding {self.unknown_units} units "
                f"have no measured demand; their exposure is unknown and is not "
                f"counted in the total, which is therefore a floor."
                if self.unknown_lots else
                "Every lot on the shelf has a demand rate behind its projection."),
        }


def assess(items: list[dict], *, as_of: date,
           return_window: int = DEFAULT_RETURN_WINDOW_DAYS) -> Exposure:
    """Exposure across the shelf, worst value first.

    `items`: {ndc11, avg_daily_demand, demand_basis, lots: [...]}

    Lots with unmeasured demand are counted separately rather than folded into
    the total at zero. Folding them in would report a smaller number than the
    truth and call it the exposure.
    """
    exp = Exposure(as_of=as_of)
    for item in items:
        risks = assess_item(
            item.get("lots") or [],
            avg_daily_demand=item.get("avg_daily_demand"),
            basis=str(item.get("demand_basis") or "no_history"),
            as_of=as_of, return_window=return_window)
        for r in risks:
            exp.lots.append(r)
            if r.at_risk_units is None:
                exp.unknown_lots += 1
                exp.unknown_units = q(exp.unknown_units + r.on_hand)
                continue
            if r.at_risk_units > 0:
                exp.units_at_risk = q(exp.units_at_risk + r.at_risk_units)
                if r.at_risk_value is not None:
                    exp.value_at_risk = q(exp.value_at_risk + r.at_risk_value)
                    exp.by_action[r.action] = q(
                        exp.by_action.get(r.action, Decimal("0")) + r.at_risk_value)

    exp.lots.sort(key=lambda l: (
        -(float(l.at_risk_value) if l.at_risk_value is not None else -1),
        l.days_left))
    return exp


def worth_raising(risk: LotRisk, *, min_value: Decimal = Decimal("1")) -> bool:
    """Whether this lot deserves a recommendation of its own.

    A risk report that lists every lot expiring in two years is a list nobody
    reads. Only exposure that is both real and actionable is raised: something
    is at risk, there is still an action other than waiting, and it is worth
    more than the time it takes to act on it.
    """
    if risk.action in ("watch", "unknown"):
        return False
    if risk.at_risk_units is None or risk.at_risk_units <= 0:
        return False
    if risk.at_risk_value is not None and risk.at_risk_value < min_value:
        return False
    return True
