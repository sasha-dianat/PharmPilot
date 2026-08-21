"""E6 — pharmacy demand is mostly zeros, and a rate alone cannot say so.

`demand.py` measures a rate: units over the window. That number is correct and
this module does not replace it. What a rate cannot tell you is the shape of the
demand behind it, and the shape decides whether a reorder point built on
`rate × lead time` means anything at all.

    Item A:  2 units a day, most days.            2.0/day
    Item B:  40 units once every three weeks.     1.9/day

Nearly the same rate, and nothing else about them is alike. A reorder point of
"about twelve units" serves item A perfectly and is useless for item B, where
demand arrives as one event that wants forty at once. Ordering against the mean
means a stockout every three weeks, on the day it matters.

So this classifies rather than forecasts, on the two statistics that separate
those cases — Syntetos and Boylan's scheme:

    ADI   average interval between demand events, in days
    CV²   squared coefficient of variation of the event *sizes*

                     CV² < 0.49        CV² ≥ 0.49
    ADI < 1.32       smooth            erratic
    ADI ≥ 1.32       intermittent      lumpy

**Lumpy is the honest quadrant.** Rare events of wildly varying size cannot be
forecast well by any method, and a system that produces a confident number for
one is lying about its own accuracy. What it reports instead is the size of a
demand event, because "hold enough to serve one typical event" is a rule that
works when a rate does not.

Croston/SBA is offered for the recency-weighted rate, not as a better average. A
mean over the window is an unbiased estimate of the long-run rate and beating it
is not the point.

It is deliberately **not** used to detect a moving rate, which is what it was
first built for here. The obvious construction — "where the recency-weighted
estimate disagrees with the average, demand is changing" — does not work at
α = 0.1: when demand rises, Croston's smoothed size climbs while its smoothed
interval shortens, and in the ratio the two effects very nearly cancel. On a
series that went from 2 units a week to 60 units every three days, SBA and the
mean differed by one percent. Drift is measured instead by halving the window and
comparing, which needs no smoothing constant to argue about and lets both numbers
be shown.

Pure functions over already-fetched rows; `as_of` is always injected.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from statistics import mean as _mean, pstdev

from .ledger import q

# Syntetos-Boylan cut-offs. Not tuned here and deliberately not: they are the
# published boundaries, and inventing house values would make the classification
# unfalsifiable against the literature it comes from.
ADI_CUT = Decimal("1.32")
CV2_CUT = Decimal("0.49")

# Croston needs intervals, and one interval is not a distribution. Three events
# give two intervals, which is the least that can show a pattern — the same
# threshold `demand.MIN_EVENTS_FOR_RATE` uses, for the same reason.
MIN_EVENTS = 3

# Croston's smoothing constant. 0.1 is the usual choice for intermittent series:
# high enough to track a genuine shift, low enough that one large fill does not
# become the forecast.
ALPHA = Decimal("0.1")

# Drift is measured by halving the window and comparing the two halves, not by
# SBA against the mean. The obvious construction — "where the recency-weighted
# estimate disagrees with the average, the rate is moving" — does not work at
# α = 0.1: Croston is deliberately sluggish, and when demand rises the smoothed
# size climbs while the smoothed interval shortens, so the two effects very
# nearly cancel in the ratio. Measured on a series that went from 2 units a week
# to 60 units every three days, SBA and the mean differed by 1%.
DRIFT_RATIO = Decimal("1.5")

# Halves smaller than this make the ratio meaningless: 3 units against 1 is a 3x
# change and tells you nothing. Same lesson as `demand.MIN_UNITS_TO_JUDGE`.
MIN_UNITS_FOR_DRIFT = Decimal("10")

# Cover for one demand event is taken at this quantile of observed sizes rather
# than the largest ever seen. The maximum is one bad day promoted to a policy.
EVENT_QUANTILE = 0.9

CLASSES = ("smooth", "intermittent", "erratic", "lumpy", "unknown")


@dataclass(frozen=True)
class Pattern:
    """The shape of one item's demand, and what that shape permits."""
    ndc11: str
    window_days: int
    events: int
    units: Decimal
    adi: Decimal | None              # average days between demand events
    cv2: Decimal | None              # squared CV of event sizes
    demand_class: str
    mean_rate: Decimal | None        # units / window — the unbiased long-run rate
    sba_rate: Decimal | None         # recency-weighted, for comparison only
    typical_event: Decimal | None    # q90 of event sizes
    largest_event: Decimal | None
    stdev_daily: Decimal | None
    drifting: bool
    direction: str                   # rising | falling | steady | indeterminate
    earlier_rate: Decimal | None
    recent_rate: Decimal | None
    basis: str                       # observed | sparse | no_history
    forecastable: bool
    explanation: str
    concerns: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        f = lambda v: None if v is None else float(v)   # noqa: E731
        return {"ndc11": self.ndc11, "window_days": self.window_days,
                "events": self.events, "units": float(self.units),
                "adi": f(self.adi), "cv2": f(self.cv2),
                "demand_class": self.demand_class,
                "mean_rate": f(self.mean_rate), "sba_rate": f(self.sba_rate),
                "typical_event": f(self.typical_event),
                "largest_event": f(self.largest_event),
                "stdev_daily": f(self.stdev_daily), "drifting": self.drifting,
                "direction": self.direction,
                "earlier_rate": f(self.earlier_rate),
                "recent_rate": f(self.recent_rate),
                "basis": self.basis, "forecastable": self.forecastable,
                "explanation": self.explanation, "concerns": list(self.concerns)}


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def daily_series(fills: list[dict], *, window_days: int,
                 as_of: date) -> list[Decimal]:
    """Units dispensed on each day of the window, zeros included.

    The zeros are not padding: for an intermittent item they are most of the
    distribution, and a series built only from the days something moved would
    describe a different drug entirely.
    """
    if window_days <= 0:
        raise ValueError("window_days must be positive")
    start = as_of.toordinal() - window_days + 1
    per_day: dict[int, Decimal] = {}
    for row in fills:
        d = _as_date(row.get("fill_date") or row.get("dispensed_at")
                     or row.get("created_at"))
        if d is None:
            continue
        o = d.toordinal()
        if o < start or o > as_of.toordinal():
            continue
        amount = q(row.get("quantity") if row.get("quantity") is not None
                   else row.get("quantity_dispensed") or 0)
        if amount <= 0:
            continue
        per_day[o] = q(per_day.get(o, Decimal("0")) + amount)
    return [per_day.get(start + i, Decimal("0")) for i in range(window_days)]


def _events(series: list[Decimal]) -> tuple[list[Decimal], list[int]]:
    """Demand sizes, and the gaps in days between successive demands."""
    sizes, positions = [], []
    for i, v in enumerate(series):
        if v > 0:
            sizes.append(v)
            positions.append(i)
    gaps = [positions[i] - positions[i - 1] for i in range(1, len(positions))]
    return sizes, gaps


def classify(adi: Decimal | None, cv2: Decimal | None) -> str:
    if adi is None or cv2 is None:
        return "unknown"
    if adi < ADI_CUT:
        return "erratic" if cv2 >= CV2_CUT else "smooth"
    return "lumpy" if cv2 >= CV2_CUT else "intermittent"


def croston(series: list[Decimal], *, alpha: Decimal = ALPHA,
            bias_corrected: bool = True) -> Decimal | None:
    """Recency-weighted rate for an intermittent series.

    Croston smooths the demand *size* and the *interval* separately and divides
    one by the other. The plain form is known to run high — the ratio of two
    smoothed quantities is not the smoothed ratio — so the Syntetos-Boylan
    correction of (1 − α/2) is applied by default. Reported beside the mean, not
    instead of it.
    """
    sizes, gaps = _events(series)
    if len(sizes) < MIN_EVENTS or not gaps:
        return None
    z = sizes[0]
    p = Decimal(str(_mean([float(g) for g in gaps])))
    for i in range(1, len(sizes)):
        z = alpha * sizes[i] + (Decimal("1") - alpha) * z
        p = alpha * Decimal(gaps[i - 1]) + (Decimal("1") - alpha) * p
    if p <= 0:
        return None
    rate = z / p
    if bias_corrected:
        rate = (Decimal("1") - alpha / Decimal("2")) * rate
    return q(rate)


@dataclass(frozen=True)
class Drift:
    """Whether the rate is moving, from the two halves of the window."""
    drifting: bool
    direction: str                   # rising | falling | steady | indeterminate
    earlier_rate: Decimal | None
    recent_rate: Decimal | None


def drift(series: list[Decimal]) -> Drift:
    """Compare the first half of the window against the second.

    A window average smears across a change: an item that sold nothing for six
    weeks and then thirty units a week averages out to something it has never
    once done. Halving is crude, and crude is the point — it needs no smoothing
    constant to argue about, and the two numbers can both be shown.
    """
    half = len(series) // 2
    if half == 0:
        return Drift(False, "indeterminate", None, None)
    early = q(sum(series[:half], Decimal("0")))
    late = q(sum(series[half:], Decimal("0")))
    er = q(early / Decimal(half))
    lr = q(late / Decimal(len(series) - half))

    if max(early, late) < MIN_UNITS_FOR_DRIFT:
        return Drift(False, "indeterminate", er, lr)
    if early <= 0:
        return Drift(True, "rising", er, lr)
    if late <= 0:
        return Drift(True, "falling", er, lr)
    ratio = max(early, late) / min(early, late)
    if ratio <= DRIFT_RATIO:
        return Drift(False, "steady", er, lr)
    return Drift(True, "rising" if late > early else "falling", er, lr)


def _quantile(values: list[Decimal], p: float) -> Decimal:
    """Nearest-rank quantile. Small samples, so no interpolation games."""
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round(p * (len(ordered) - 1)))))
    return ordered[idx]


def assess(ndc11: str, fills: list[dict], *, window_days: int,
           as_of: date) -> Pattern:
    """What shape this item's demand has, and whether it can be planned on a rate."""
    series = daily_series(fills, window_days=window_days, as_of=as_of)
    sizes, gaps = _events(series)
    units = q(sum(series, Decimal("0")))

    if not sizes:
        return Pattern(
            ndc11=ndc11, window_days=window_days, events=0, units=q(0),
            adi=None, cv2=None, demand_class="unknown", mean_rate=None,
            sba_rate=None, typical_event=None, largest_event=None,
            stdev_daily=None, drifting=False, direction="indeterminate",
            earlier_rate=None, recent_rate=None, basis="no_history",
            forecastable=False,
            explanation=(f"Nothing dispensed in {window_days} days. No shape to "
                         f"describe and no rate assumed."))

    dr = drift(series)
    mean_rate = q(units / Decimal(window_days))
    m = units / Decimal(window_days)
    var = sum(((v - m) ** 2 for v in series), Decimal("0")) / Decimal(window_days)
    stdev = q(Decimal(str(float(var) ** 0.5)))
    typical = q(_quantile(sizes, EVENT_QUANTILE))
    largest = q(max(sizes))

    if len(sizes) < MIN_EVENTS or not gaps:
        return Pattern(
            ndc11=ndc11, window_days=window_days, events=len(sizes), units=units,
            adi=None, cv2=None, demand_class="unknown", mean_rate=mean_rate,
            sba_rate=None, typical_event=typical, largest_event=largest,
            stdev_daily=stdev, drifting=dr.drifting, direction=dr.direction,
            earlier_rate=dr.earlier_rate, recent_rate=dr.recent_rate,
            basis="sparse",
            forecastable=False,
            concerns=[f"{len(sizes)} demand event(s) — an interval needs two, and "
                      f"a distribution of intervals needs more than that"],
            explanation=(
                f"{units} units in {len(sizes)} event(s) over {window_days} days. "
                f"Too few to tell a steady seller from an occasional one, so the "
                f"shape is not classified. Cover for one event is {typical}."))

    adi = q(Decimal(str(_mean([float(g) for g in gaps]))))
    size_mean = _mean([float(s) for s in sizes])
    size_sd = pstdev([float(s) for s in sizes]) if len(sizes) > 1 else 0.0
    cv2 = q(Decimal(str((size_sd / size_mean) ** 2))) if size_mean > 0 else q(0)
    cls = classify(adi, cv2)
    sba = croston(series)

    concerns: list[str] = []
    if cls == "lumpy":
        concerns.append(
            f"rare events of widely varying size — no method forecasts this "
            f"well, and a confident number for it would be a claim about "
            f"accuracy nobody can support. Plan cover for one event ({typical}), "
            f"not {mean_rate}/day")
    elif cls == "intermittent":
        concerns.append(
            f"demand arrives about every {adi} days rather than daily; a reorder "
            f"point near {mean_rate}/day × lead time will not serve the event "
            f"when it comes. One event is {typical} units")
    elif cls == "erratic":
        concerns.append(
            f"sold most days but in wildly different quantities — the rate is "
            f"sound, the cover needs to allow for a {largest}-unit day")
    if dr.drifting:
        concerns.append(
            f"the rate is moving and the window is averaging across the change: "
            f"{dr.earlier_rate}/day over the first half against "
            f"{dr.recent_rate}/day over the second, {dr.direction}. Planning on "
            f"{mean_rate}/day plans on a figure this item has never sustained")

    forecastable = cls in ("smooth", "erratic")
    readable = {
        "smooth": "moves most days in steady quantities — a rate describes it",
        "intermittent": "moves in bursts with quiet stretches between",
        "erratic": "moves most days but in unpredictable quantities",
        "lumpy": "rare and unpredictable in both timing and size",
    }[cls]
    return Pattern(
        ndc11=ndc11, window_days=window_days, events=len(sizes), units=units,
        adi=adi, cv2=cv2, demand_class=cls, mean_rate=mean_rate, sba_rate=sba,
        typical_event=typical, largest_event=largest, stdev_daily=stdev,
        drifting=dr.drifting, direction=dr.direction,
        earlier_rate=dr.earlier_rate, recent_rate=dr.recent_rate,
        basis="observed", forecastable=forecastable,
        concerns=concerns,
        explanation=(
            f"{cls}: {readable}. {units} units in {len(sizes)} events over "
            f"{window_days} days — every {adi} days on average, sizes varying by "
            f"CV² {cv2}. Mean {mean_rate}/day."))


def cover_floor(p: Pattern) -> Decimal | None:
    """The least stock that can serve one typical demand event.

    For a smooth item this is meaningless and returns None — the rate already
    describes it and a floor would only inflate holdings. For an intermittent or
    lumpy one it is the number that matters: a safety stock derived from a daily
    average cannot serve a demand that arrives all at once, and the shelf is
    empty on the one day a patient is standing at the counter.
    """
    if p.demand_class in ("intermittent", "lumpy") and p.typical_event:
        return p.typical_event
    return None


def worth_raising(p: Pattern) -> bool:
    """Whether the shape itself needs somebody's attention.

    A smooth item never does — the ordinary machinery handles it. `unknown` never
    does either: every item starts there, and filing it would bury the ones where
    the planning rule is actually wrong.
    """
    return p.basis == "observed" and (p.demand_class == "lumpy" or p.drifting)
