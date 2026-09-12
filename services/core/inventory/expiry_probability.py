"""E9 — how likely is it that this lot expires before it sells?

E1 answers deterministically: at the measured rate, this lot's units either are
or are not consumed before the expiry date. That is the right first answer and it
drives the actions. What it cannot express is the middle, which is where most
real stock sits — the lot that will *probably* clear, and the lot that clears
only if the next two months are as good as the last two.

The difference decides money. A lot with a 5% chance of expiring is not worth
discounting; the same lot at 60% is worth discounting today, while there is still
a customer for it. E1 puts both in the same bucket, and returning stock costs
nothing compared with writing it off.

    P(expires) = P(demand over the remaining days < units allocated to this lot)

with demand over D days approximated as Normal(d·D, σ·√D). Which is exactly where
the honesty has to live, because that approximation is a claim about the shape of
the demand, and the shape is E6's business:

**A normal approximation needs enough demand events.** It is the central limit
theorem doing the work, and the theorem needs a sum of many things. An item that
sells twice a quarter, in wildly different quantities, has three demand events
before expiry and its total is not remotely normal — a probability computed there
is a decimal point on a guess. So the engine refuses unless the horizon is
expected to contain `MIN_EVENTS_FOR_NORMAL` demand events, computed from E6's
measured interval, and hands back E1's deterministic verdict instead.

**No measured spread, no probability.** Safety stock and this both stand entirely
on σ, and an assumed σ produces a confident number about nothing.

Pure functions over already-fetched values.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from math import erf, sqrt

from .ledger import q

# The central limit theorem needs a sum of many draws. Ten demand events over the
# horizon is the usual working minimum for a normal approximation to be worth
# anything; below it the sum is still shaped like the individual events.
MIN_EVENTS_FOR_NORMAL = 10

# Demand classes whose event sizes vary so much that even ten of them do not
# converge usefully. E6 already declines to forecast these; declining to put a
# probability on them is the same judgement.
UNMODELLABLE = ("lumpy",)

# Bands, for the action rather than the number. A probability quoted to three
# decimals invites a precision nobody has earned.
LIKELY = Decimal("0.50")
POSSIBLE = Decimal("0.15")

BASES = ("observed", "no_spread", "too_few_events", "unmodellable",
         "no_demand", "already_expired")


@dataclass(frozen=True)
class ExpiryOdds:
    lot_id: str
    ndc11: str
    units: Decimal
    days_left: int
    expected_demand: Decimal | None
    stdev_over_horizon: Decimal | None
    probability: Decimal | None
    band: str                        # likely | possible | unlikely | unknown
    expected_loss: Decimal | None    # probability x units at risk x unit cost
    basis: str
    expected_events: Decimal | None
    explanation: str
    concerns: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        f = lambda v: None if v is None else float(v)   # noqa: E731
        return {"lot_id": self.lot_id, "ndc11": self.ndc11,
                "units": float(self.units), "days_left": self.days_left,
                "expected_demand": f(self.expected_demand),
                "stdev_over_horizon": f(self.stdev_over_horizon),
                "probability": f(self.probability), "band": self.band,
                "expected_loss": f(self.expected_loss), "basis": self.basis,
                "expected_events": f(self.expected_events),
                "explanation": self.explanation, "concerns": list(self.concerns)}


def normal_cdf(z: float) -> float:
    """Φ(z), from the error function in the standard library.

    No scipy: this is one line of mathematics and a dependency for it would be a
    dependency to keep working for the next decade.
    """
    return 0.5 * (1.0 + erf(z / sqrt(2.0)))


def _band(p: Decimal) -> str:
    if p >= LIKELY:
        return "likely"
    if p >= POSSIBLE:
        return "possible"
    return "unlikely"


def assess_lot(*, lot_id: str, ndc11: str, units, days_left: int,
               avg_daily_demand=None, stdev_daily=None, unit_cost=None,
               demand_class: str = "unknown", adi=None,
               consumed_by_earlier=None) -> ExpiryOdds:
    """The odds this lot expires unsold, or an honest refusal to quote them.

    `consumed_by_earlier` is E1's FEFO allocation: the units that lots expiring
    sooner will take out of the same demand stream first. Ignoring it charges
    every lot the item's whole demand, which made five lots of a slow mover each
    look safe — the defect E1 was rebuilt to fix, and it would reappear here.
    """
    u = q(units)
    earlier = q(consumed_by_earlier or 0)
    concerns: list[str] = []

    def refuse(basis: str, why: str, events=None) -> ExpiryOdds:
        return ExpiryOdds(
            lot_id=lot_id, ndc11=ndc11, units=u, days_left=days_left,
            expected_demand=None, stdev_over_horizon=None, probability=None,
            band="unknown", expected_loss=None, basis=basis,
            expected_events=events, explanation=why, concerns=concerns)

    if days_left <= 0:
        # Not `observed`: nothing was observed about this lot's demand. It is
        # simply no longer a forecasting question.
        return refuse("already_expired",
                      "Already expired — this is a write-off, not a forecast.")
    if avg_daily_demand is None:
        return refuse("no_demand",
                      "No measured demand, so there is no distribution to "
                      "integrate. E1's deterministic verdict stands.")
    if stdev_daily is None:
        return refuse("no_spread",
                      "Demand rate is known but its spread is not, and the whole "
                      "probability is a function of the spread. An assumed one "
                      "would produce a confident number about nothing.")
    if demand_class in UNMODELLABLE:
        return refuse("unmodellable",
                      f"Demand is {demand_class}: event sizes vary so much that a "
                      f"normal approximation describes a different drug. E6 "
                      f"declines to forecast this and so does this.")

    d = q(avg_daily_demand)
    sd = q(stdev_daily)
    horizon = Decimal(days_left)

    # How many demand events the horizon is expected to contain. This is what the
    # central limit theorem is being asked to work with.
    events = (q(horizon / q(adi)) if adi not in (None, 0)
              else (q(horizon) if d > 0 else q(0)))
    if events < MIN_EVENTS_FOR_NORMAL:
        return refuse(
            "too_few_events",
            f"About {events} demand event(s) before expiry — too few for a normal "
            f"approximation, which is the central limit theorem doing the work "
            f"and needs a sum of many things. E1's deterministic verdict stands.",
            events=events)

    mu = q(d * horizon)
    sigma = q(sd * Decimal(str(float(horizon) ** 0.5)))
    # Demand available to THIS lot, after the ones expiring sooner have taken
    # their share out of the same stream.
    available = q(mu - earlier)

    if sigma <= 0:
        # Zero spread is a degenerate but real case: demand is exactly d every
        # day. The answer is deterministic and certain, not undefined.
        p = q(Decimal("1") if available < u else Decimal("0"))
    else:
        z = float((u - available) / sigma)
        p = q(Decimal(str(normal_cdf(z))))

    at_risk = q(max(Decimal("0"), u - max(Decimal("0"), available)))
    loss = (q(p * at_risk * q(unit_cost)) if unit_cost is not None else None)
    if unit_cost is None:
        concerns.append("no unit cost recorded for this lot, so the exposure is "
                        "in units and not in money")
    if earlier > 0:
        concerns.append(
            f"{earlier} unit(s) of the demand in this window are already spoken "
            f"for by lots that expire sooner, and are not counted here")

    band = _band(p)
    return ExpiryOdds(
        lot_id=lot_id, ndc11=ndc11, units=u, days_left=days_left,
        expected_demand=available, stdev_over_horizon=sigma, probability=p,
        band=band, expected_loss=loss, basis="observed", expected_events=events,
        concerns=concerns,
        explanation=(
            f"{u} units, {days_left} days left. Demand available to this lot is "
            f"{available} ± {sigma} over the horizon, so it expires unsold with "
            f"probability {p} ({band})."
            + (f" Expected loss {loss}." if loss is not None else "")))


def worth_discounting(o: ExpiryOdds) -> bool:
    """Whether to act now rather than watch.

    Only on a measured probability. An `unknown` lot is E1's to judge, and
    discounting on the strength of a refusal would be acting on the absence of
    information.
    """
    return o.basis == "observed" and o.probability is not None \
        and o.probability >= POSSIBLE
