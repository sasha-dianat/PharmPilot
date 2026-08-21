"""E7 / ㉑ — is this a real annual pattern, or last month being unusual?

A pharmacy's year has a shape. Antihistamines climb before Nowruz, cold remedies
in Azar and Dey, paediatric antibiotics when the schools go back in Mehr. Ordering
that anticipates the season instead of reacting a month late is worth real money —
and a system that mistakes one odd month for a pattern is worth less than nothing,
because it will buy for a season that never comes.

So this is built around two refusals, and they matter more than the arithmetic:

**Two complete cycles, or no seasonal claim. Ever.** One cold season is an
anecdote. With a single year of history there is no way to tell a seasonal peak
from the month a nearby clinic changed its prescribing, and the difference is
invisible in the data.

**The seasonal component must beat the noise.** Where it does not, the answer is
"no detectable seasonality", not a flat line dressed up as a finding. A strength
statistic is reported so the claim can be argued with.

**The buckets are Jalali months.** This is the part a Gregorian model gets wrong
in Iran, not as a nicety but arithmetically: Nowruz is 1 Farvardin every year and
drifts across 20-21 March in the Gregorian calendar, so a March bucket splits the
new-year peak across two months and halves it. The school year turns on 1 Mehr,
which lands in September or October. Bucketing by Jalali month puts each of those
in one place.

**Ramadan is not captured, and this module says so rather than appearing to.**
Ramadan is lunar and moves against the Jalali calendar as well — roughly eleven
days earlier each year — so it smears across Jalali months exactly the way Nowruz
smears across Gregorian ones. Capturing it needs a Hijri conversion this codebase
does not have. Any Ramadan effect will therefore land in the residual and depress
the strength statistic, which is the safe direction: it makes a claim harder to
make, not easier.

Pure functions over already-fetched rows; `as_of` is always injected.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from statistics import mean as _mean, pvariance

from services.core.localization.jalali import (JALALI_MONTHS_EN,
                                               gregorian_to_jalali)

from .ledger import q

# Two complete turns of the year. Not negotiable and not a tunable: with one
# cycle there is no second observation of any month, so "high in Dey" and "one
# unusual Dey" are the same data.
MIN_CYCLES = 2

# A month needs to have been observed at least this many times before its index
# is quoted. With MIN_CYCLES = 2 this is satisfied by construction, but a series
# with gaps can still leave a month thin.
MIN_OBSERVATIONS_PER_MONTH = 2

# Below this share of variance explained by the month-of-year component, the
# pattern is noise. 0.3 is the usual working threshold for a seasonal-strength
# statistic; a lower bar produces confident-looking seasonality for every item.
STRENGTH_FLOOR = Decimal("0.30")

# A month whose index is this far from 1.0 is worth acting on. Below it the
# ordering difference is smaller than the rounding on a pack size.
NOTABLE_INDEX = Decimal("0.20")

VERDICTS = ("seasonal", "no_detectable_seasonality", "insufficient_cycles",
            "no_history")


@dataclass(frozen=True)
class MonthIndex:
    """One Jalali month's demand relative to the item's own average."""
    month: int
    name: str
    observations: int
    mean_units: Decimal
    index: Decimal | None            # 1.0 = an average month
    basis: str                       # observed | thin

    def as_dict(self) -> dict:
        return {"month": self.month, "name": self.name,
                "observations": self.observations,
                "mean_units": float(self.mean_units),
                "index": None if self.index is None else float(self.index),
                "basis": self.basis}


@dataclass(frozen=True)
class Seasonality:
    ndc11: str
    verdict: str
    cycles: int                      # complete Jalali years covered
    months_observed: int
    total_units: Decimal
    strength: Decimal | None         # share of variance the month explains
    peak: MonthIndex | None
    trough: MonthIndex | None
    months: list[MonthIndex]
    explanation: str
    concerns: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"ndc11": self.ndc11, "verdict": self.verdict,
                "cycles": self.cycles, "months_observed": self.months_observed,
                "total_units": float(self.total_units),
                "strength": None if self.strength is None else float(self.strength),
                "peak": None if self.peak is None else self.peak.as_dict(),
                "trough": None if self.trough is None else self.trough.as_dict(),
                "months": [m.as_dict() for m in self.months],
                "explanation": self.explanation, "concerns": list(self.concerns)}


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def jalali_month_of(d: date) -> tuple[int, int]:
    """(Jalali year, Jalali month) for a Gregorian date."""
    jy, jm, _ = gregorian_to_jalali(d.year, d.month, d.day)
    return jy, jm


def monthly_totals(fills: list[dict]) -> dict[tuple[int, int], Decimal]:
    """Units per (Jalali year, Jalali month)."""
    out: dict[tuple[int, int], Decimal] = {}
    for row in fills:
        d = _as_date(row.get("fill_date") or row.get("dispensed_at")
                     or row.get("created_at"))
        if d is None:
            continue
        amount = q(row.get("quantity") if row.get("quantity") is not None
                   else row.get("quantity_dispensed") or 0)
        if amount <= 0:
            continue
        key = jalali_month_of(d)
        out[key] = q(out.get(key, Decimal("0")) + amount)
    return out


def assess(ndc11: str, fills: list[dict], *, as_of: date) -> Seasonality:
    """Whether this item has a real annual pattern, or not enough evidence to say."""
    totals = monthly_totals(fills)
    total_units = q(sum(totals.values(), Decimal("0")))

    if not totals:
        return Seasonality(
            ndc11=ndc11, verdict="no_history", cycles=0, months_observed=0,
            total_units=q(0), strength=None, peak=None, trough=None, months=[],
            explanation=("Nothing dispensed. A seasonal pattern cannot be "
                         "asserted from no observations, and none is."))

    # Complete cycles = how many distinct Jalali years have a full twelve months
    # of coverage between the first and last observation. Counting calendar
    # years present would call fourteen months "two years".
    keys = sorted(totals)
    span_months = ((keys[-1][0] - keys[0][0]) * 12 + (keys[-1][1] - keys[0][1])) + 1
    cycles = span_months // 12

    by_month: dict[int, list[Decimal]] = {}
    for (_jy, jm), units in totals.items():
        by_month.setdefault(jm, []).append(units)

    overall = Decimal(str(_mean([float(v) for v in totals.values()])))
    months = []
    for m in range(1, 13):
        vals = by_month.get(m, [])
        mu = q(Decimal(str(_mean([float(v) for v in vals])))) if vals else q(0)
        thin = len(vals) < MIN_OBSERVATIONS_PER_MONTH
        months.append(MonthIndex(
            month=m, name=JALALI_MONTHS_EN[m], observations=len(vals),
            mean_units=mu,
            index=None if (thin or overall <= 0) else q(mu / overall),
            basis="thin" if thin else "observed"))

    concerns: list[str] = [
        "Ramadan is lunar and moves against the Jalali calendar too, so any "
        "Ramadan effect is smeared across months here and lands in the residual "
        "— it is not captured, and this understates seasonality rather than "
        "inventing it"]

    if cycles < MIN_CYCLES:
        return Seasonality(
            ndc11=ndc11, verdict="insufficient_cycles", cycles=cycles,
            months_observed=len(totals), total_units=total_units, strength=None,
            peak=None, trough=None, months=months, concerns=concerns,
            explanation=(
                f"{span_months} month(s) of history — {cycles} complete cycle(s). "
                f"A seasonal claim needs {MIN_CYCLES}: with one turn of the year "
                f"there is no second observation of any month, so a real peak and "
                f"one unusual month are the same data. No pattern is asserted."))

    # Variance the month-of-year explains, against the variance left over. This
    # is the statistic the claim stands on, and it is reported so it can be
    # argued with rather than taken on trust.
    fitted = {k: float(next(mo.mean_units for mo in months if mo.month == k[1]))
              for k in totals}
    residuals = [float(totals[k]) - fitted[k] for k in totals]
    seasonal_part = [fitted[k] - float(overall) for k in totals]
    var_res = pvariance(residuals) if len(residuals) > 1 else 0.0
    var_sea = pvariance(seasonal_part) if len(seasonal_part) > 1 else 0.0
    strength = (q(Decimal(str(var_sea / (var_sea + var_res))))
                if (var_sea + var_res) > 0 else q(0))

    ranked = [m for m in months if m.index is not None]
    peak = max(ranked, key=lambda m: m.index) if ranked else None
    trough = min(ranked, key=lambda m: m.index) if ranked else None

    if strength < STRENGTH_FLOOR:
        return Seasonality(
            ndc11=ndc11, verdict="no_detectable_seasonality", cycles=cycles,
            months_observed=len(totals), total_units=total_units,
            strength=strength, peak=peak, trough=trough, months=months,
            concerns=concerns,
            explanation=(
                f"{cycles} cycles of history, but the month of the year explains "
                f"only {strength} of the variation — the rest is noise. Reporting "
                f"a pattern from this would be a flat line with a shape drawn on "
                f"it."))

    thin_months = [m.name for m in months if m.basis == "thin"]
    if thin_months:
        concerns.append(
            f"{len(thin_months)} month(s) observed fewer than "
            f"{MIN_OBSERVATIONS_PER_MONTH} times ({', '.join(thin_months[:3])}…) "
            f"— their indices are withheld rather than quoted from one year")

    return Seasonality(
        ndc11=ndc11, verdict="seasonal", cycles=cycles,
        months_observed=len(totals), total_units=total_units, strength=strength,
        peak=peak, trough=trough, months=months, concerns=concerns,
        explanation=(
            f"Seasonal: the month of the year explains {strength} of the "
            f"variation over {cycles} cycles. Busiest {peak.name} at "
            f"{peak.index}x an average month, quietest {trough.name} at "
            f"{trough.index}x."
            if peak and trough else
            f"Seasonal, strength {strength} over {cycles} cycles."))


def month_factor(s: Seasonality, month: int) -> Decimal | None:
    """The multiplier to apply to a flat rate for one Jalali month.

    None unless the item is genuinely seasonal *and* that month was observed
    enough times. A planner receiving None uses the flat rate, which is the
    correct behaviour — an unmeasured month has no factor, and 1.0 would be a
    claim rather than an absence.
    """
    if s.verdict != "seasonal":
        return None
    for m in s.months:
        if m.month == month:
            return m.index
    return None


def worth_raising(s: Seasonality) -> bool:
    """Whether the pattern is strong enough to change what somebody orders."""
    if s.verdict != "seasonal" or s.peak is None or s.peak.index is None:
        return False
    return abs(s.peak.index - Decimal("1")) >= NOTABLE_INDEX
