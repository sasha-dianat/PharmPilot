"""Demand signal — measured from dispensing, never assumed.

`stock_levels.avg_daily_demand` drives every purchasing decision the platform
makes: order quantity, order-by date, urgency band, days-of-supply, the
`overstocked` filter, and the movement class that decides what counts as dead
stock. It was carrying seeded numbers that no dispense record supports — one
item claimed 14 units/day against 0 units actually dispensed, another claimed 4
against an observed 12.9. Nothing recomputed it and nothing checked it, so the
recommendations looked authoritative and were fiction.

Two rules follow from that, and this module exists to enforce both:

1. **Never invent a demand rate.** When there is no history the estimate is
   `None` with `basis="no_history"` — not a fallback constant. A purchasing
   engine that receives `None` recommends nothing, which is the correct
   behaviour; one that receives an invented 1.0/day orders stock for a drug
   nobody takes.

2. **A stored signal must be falsifiable.** `divergence()` compares what the
   stored rate predicts against what the fills actually recorded, so a
   fabricated or stale number is a finding rather than a silent input.

Pure functions over already-fetched rows — no I/O, no `datetime.now()`. `as_of`
is always injected, so every judgement is reproducible.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from .ledger import q

# ── Tunables, each with the reason it has the value it has ────────────────

# Below this many days of observation we can measure units but not a *rate* —
# a fortnight of data cannot distinguish a weekly drug from a daily one.
MIN_WINDOW_DAYS = 28

# One or two fills is an anecdote. Three is the least that can show a pattern.
MIN_EVENTS_FOR_RATE = 3

# A stored rate may be this many times off the observed one before we call it
# wrong. Demand is genuinely lumpy; 2x absorbs seasonality without absorbing
# fabrication.
DIVERGENCE_RATIO = Decimal("2.0")

# Counts below this are too small to judge either way: 3 expected vs 1 observed
# is a 3x ratio and means nothing. Judging starts once the larger side of the
# comparison is a real quantity.
MIN_UNITS_TO_JUDGE = Decimal("10")

# A demand signal older than this is not evidence about today's dispensing.
STALE_AFTER_DAYS = 14

BASES = ("observed", "sparse", "no_history")
VERDICTS = ("agrees", "understated", "overstated", "contradicted", "indeterminate")


@dataclass(frozen=True)
class DemandEstimate:
    """What the dispense record actually says about one item's demand."""
    ndc11: str
    window_days: int
    units: Decimal          # total units dispensed in the window
    events: int             # number of fill events
    active_days: int        # distinct days on which anything was dispensed
    basis: str              # observed | sparse | no_history
    avg_daily_demand: Decimal | None   # None means "we decline to guess"
    confidence: float       # 0.0-1.0, how much weight a planner should give it
    explanation: str
    # Day-to-day variability, measured across the whole window including days
    # with no dispensing. Safety stock is computed from this, and averaging only
    # the active days would understate it badly for an intermittent drug — the
    # zeros are most of the distribution.
    stdev_daily: Decimal | None = None

    def as_dict(self) -> dict:
        return {
            "ndc11": self.ndc11,
            "window_days": self.window_days,
            "units": float(self.units),
            "events": self.events,
            "active_days": self.active_days,
            "basis": self.basis,
            "avg_daily_demand": (
                None if self.avg_daily_demand is None else float(self.avg_daily_demand)
            ),
            "confidence": self.confidence,
            "stdev_daily": (
                None if self.stdev_daily is None else float(self.stdev_daily)),
            "explanation": self.explanation,
        }


@dataclass(frozen=True)
class Divergence:
    """Whether a stored demand rate survives contact with the fill record."""
    ndc11: str
    stored_adq: Decimal | None
    observed_adq: Decimal | None
    expected_units: Decimal
    observed_units: Decimal
    window_days: int
    verdict: str            # one of VERDICTS
    severity: str           # critical | high | medium | info
    explanation: str

    @property
    def disagrees(self) -> bool:
        return self.verdict in ("understated", "overstated", "contradicted")

    def as_dict(self) -> dict:
        return {
            "ndc11": self.ndc11,
            "stored_adq": None if self.stored_adq is None else float(self.stored_adq),
            "observed_adq": None if self.observed_adq is None else float(self.observed_adq),
            "expected_units": float(self.expected_units),
            "observed_units": float(self.observed_units),
            "window_days": self.window_days,
            "verdict": self.verdict,
            "severity": self.severity,
            "explanation": self.explanation,
        }


def _as_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def estimate(
    ndc11: str,
    fills: list[dict],
    *,
    window_days: int,
    as_of: date,
) -> DemandEstimate:
    """Measure demand for one item from its fill rows.

    `fills`: [{"fill_date"|"dispensed_at"|"created_at": date/datetime,
               "quantity"|"quantity_dispensed": number}]

    Rows outside the window (or with no usable date) are ignored rather than
    silently counted at the boundary — an undated fill cannot be placed in time,
    and including it would inflate the rate for a window it may not belong to.
    """
    if window_days <= 0:
        raise ValueError("window_days must be positive")

    start = as_of.toordinal() - window_days + 1
    units = q(0)
    events = 0
    days: set[int] = set()
    per_day: dict[int, Decimal] = {}

    for row in fills:
        d = _as_date(
            row.get("fill_date") or row.get("dispensed_at") or row.get("created_at")
        )
        if d is None:
            continue
        o = d.toordinal()
        if o < start or o > as_of.toordinal():
            continue
        qty = row.get("quantity")
        if qty is None:
            qty = row.get("quantity_dispensed")
        amount = q(qty or 0)
        units += amount
        events += 1
        days.add(o)
        per_day[o] = q(per_day.get(o, Decimal("0")) + amount)

    active_days = len(days)

    if events == 0:
        return DemandEstimate(
            ndc11=ndc11, window_days=window_days, units=q(0), events=0,
            active_days=0, basis="no_history", avg_daily_demand=None,
            confidence=0.0,
            explanation=(
                f"No dispensing recorded in {window_days} days — "
                f"no demand rate can be derived, and none is assumed."
            ),
        )

    rate = q(units / Decimal(window_days))

    # Population stdev over every day in the window, not only the days with a
    # fill. For an intermittent drug the zeros are most of the distribution, and
    # dropping them would understate safety stock exactly where cover matters.
    mean = units / Decimal(window_days)
    var = sum(((per_day.get(start + i, Decimal("0")) - mean) ** 2
               for i in range(window_days)), Decimal("0")) / Decimal(window_days)
    stdev = q(Decimal(str(float(var) ** 0.5)))

    if events < MIN_EVENTS_FOR_RATE or window_days < MIN_WINDOW_DAYS:
        return DemandEstimate(
            ndc11=ndc11, window_days=window_days, units=units, events=events,
            active_days=active_days, basis="sparse", avg_daily_demand=rate,
            confidence=0.35, stdev_daily=stdev,
            explanation=(
                f"{units} units over {events} fill(s) in {window_days} days — "
                f"too few events to call this a rate; treat {rate}/day as "
                f"provisional."
            ),
        )

    # More active days across the window means the rate is better supported than
    # the same units arriving in a single burst.
    spread = min(1.0, active_days / max(1.0, window_days / 7.0))
    confidence = round(0.6 + 0.4 * spread, 3)

    return DemandEstimate(
        ndc11=ndc11, window_days=window_days, units=units, events=events,
        active_days=active_days, basis="observed", avg_daily_demand=rate,
        confidence=confidence, stdev_daily=stdev,
        explanation=(
            f"{units} units over {events} fills on {active_days} distinct days "
            f"in {window_days} days = {rate}/day."
        ),
    )


def divergence(
    *,
    ndc11: str,
    stored_adq,
    observed: DemandEstimate,
) -> Divergence:
    """Test a stored demand rate against what was actually dispensed.

    The comparison is in *units over the window*, not in rates, because that is
    where small numbers are honest: a stored 0.2/day predicts 5.6 units in 28
    days, and observing 0 tells us almost nothing. A stored 14/day predicts 392
    units, and observing 0 tells us the number did not come from this pharmacy.

    That distinction is the whole point. `contradicted` is not a forecasting
    error — it means the signal has no basis in the fill record at all, which
    impeaches every other value from the same source.
    """
    window = observed.window_days
    stored = None if stored_adq is None else q(stored_adq)
    expected = q(0) if stored is None else q(stored * Decimal(window))
    actual = observed.units
    obs_rate = observed.avg_daily_demand

    def out(verdict: str, severity: str, why: str) -> Divergence:
        return Divergence(
            ndc11=ndc11, stored_adq=stored, observed_adq=obs_rate,
            expected_units=expected, observed_units=actual, window_days=window,
            verdict=verdict, severity=severity, explanation=why,
        )

    if stored is None:
        return out(
            "indeterminate", "info",
            "No stored demand signal to test.",
        )

    scale = max(expected, actual)
    if scale < MIN_UNITS_TO_JUDGE:
        return out(
            "indeterminate", "info",
            f"Stored {stored}/day predicts {expected} units in {window} days and "
            f"{actual} were dispensed — both too small to judge.",
        )

    # Predicted a real quantity, observed nothing at all.
    if actual == 0:
        return out(
            "contradicted", "high",
            f"Stored {stored}/day predicts {expected} units in {window} days, "
            f"but nothing was dispensed. The signal is not derived from this "
            f"pharmacy's records.",
        )

    if expected == 0:
        return out(
            "understated", "high",
            f"Stored demand is zero but {actual} units were dispensed in "
            f"{window} days ({obs_rate}/day). Purchasing will never reorder this.",
        )

    ratio = scale / min(expected, actual)
    if ratio <= DIVERGENCE_RATIO:
        return out(
            "agrees", "info",
            f"Stored {stored}/day predicts {expected} units; {actual} dispensed "
            f"in {window} days — within {DIVERGENCE_RATIO}x.",
        )

    times = round(float(ratio), 1)
    if expected > actual:
        return out(
            "overstated", "medium",
            f"Stored {stored}/day predicts {expected} units in {window} days but "
            f"only {actual} were dispensed — overstated {times}x. Drives "
            f"over-purchasing and expiry waste.",
        )
    return out(
        "understated", "high",
        f"Stored {stored}/day predicts {expected} units in {window} days but "
        f"{actual} were dispensed — understated {times}x. Real stockout risk: "
        f"reorder point and safety stock are both set too low.",
    )


@dataclass(frozen=True)
class RefreshRow:
    """One item's before/after, so a refresh can be reviewed before it is applied."""
    ndc11: str
    stored_adq: Decimal | None
    new_adq: Decimal | None
    basis: str
    confidence: float
    units_observed: Decimal
    window_days: int
    changed: bool
    verdict: str            # what the old value was, judged against the record
    explanation: str
    stdev_daily: Decimal | None = None

    def as_dict(self) -> dict:
        return {
            "ndc11": self.ndc11,
            "stored_adq": None if self.stored_adq is None else float(self.stored_adq),
            "new_adq": None if self.new_adq is None else float(self.new_adq),
            "basis": self.basis,
            "confidence": self.confidence,
            "units_observed": float(self.units_observed),
            "window_days": self.window_days,
            "changed": self.changed,
            "verdict": self.verdict,
            "stdev_daily": (
                None if self.stdev_daily is None else float(self.stdev_daily)),
            "explanation": self.explanation,
        }


def plan_refresh(
    stock_rows: list[dict],
    fills_by_ndc: dict[str, list[dict]],
    *,
    window_days: int,
    as_of: date,
) -> list[RefreshRow]:
    """What a demand refresh would write, per item, without writing it.

    Separated from the persistence so a refresh can be inspected first. The
    verdict on each row is the *old* value's standing — an operator approving a
    refresh should be able to see which numbers were wrong and by how much,
    not just the replacements.
    """
    out: list[RefreshRow] = []
    for r in stock_rows:
        ndc = str(r.get("ndc11") or "")
        stored = r.get("avg_daily_demand")
        stored_q = None if stored is None else q(stored)
        est = estimate(ndc, fills_by_ndc.get(ndc, []),
                       window_days=window_days, as_of=as_of)
        div = divergence(ndc11=ndc, stored_adq=stored, observed=est)
        changed = (stored_q != est.avg_daily_demand)
        out.append(RefreshRow(
            ndc11=ndc, stored_adq=stored_q, new_adq=est.avg_daily_demand,
            basis=est.basis, confidence=est.confidence, units_observed=est.units,
            window_days=window_days, changed=changed, verdict=div.verdict,
            explanation=est.explanation, stdev_daily=est.stdev_daily,
        ))
    return out


def staleness_days(updated_at, as_of: date) -> int | None:
    """Age of a demand signal in days. None when it was never computed."""
    d = _as_date(updated_at)
    if d is None:
        return None
    return as_of.toordinal() - d.toordinal()


def is_stale(updated_at, as_of: date, *, limit: int = STALE_AFTER_DAYS) -> bool:
    """A signal that was never computed is stale; so is one older than `limit`."""
    age = staleness_days(updated_at, as_of)
    return True if age is None else age > limit


def utc_date(dt: datetime | None = None) -> date:
    """Today in UTC — the single place a caller converts 'now' into `as_of`."""
    return (dt or datetime.now(timezone.utc)).astimezone(timezone.utc).date()
