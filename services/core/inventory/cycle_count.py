"""Cycle counting — where to spend the counting hours.

A pharmacy cannot count everything often, and counting everything equally is the
same as counting nothing carefully: the hours go mostly into cheap, slow-moving
stock while the items that actually carry the money and the risk get the same
thin attention. `StockCount` existed and could record a count, but nothing
decided *what* to count, so the only available schedule was a flat sweep.

Three inputs decide the schedule, and they answer different questions:

  ABC — where the money is. Classified on annual consumption value, not
        value-on-hand: an item that turns over twenty times a year carries far
        more risk than an equally valuable item sitting still, because twenty
        times as many transactions have had the chance to go wrong.

  XYZ — how predictable it is. A steady item's book figure can be trusted
        between counts; an erratic one cannot, and drift hides in the noise.

  Risk — what makes an error expensive regardless of value: controlled
        substances (a diversion signal, and a regulatory exposure), stock near
        expiry (a wrong count means the write-off is wrong too), and anything
        that has recently disagreed with its count, because the best predictor
        of a discrepancy is a previous discrepancy.

Deliberately *not* included: any notion of who counted it. Counter accuracy
belongs in a performance conversation, and building it into the schedule would
quietly turn a stock control into a staff surveillance tool.

Pure functions over already-fetched rows; `as_of` is always injected.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .ledger import q

# Share of annual consumption value each class accounts for. The usual 80/15/5
# split: A is the fifth of the catalogue holding four-fifths of the value.
ABC_THRESHOLDS = ((Decimal("0.80"), "A"), (Decimal("0.95"), "B"))

# Coefficient of variation bands. X is predictable enough to trust between
# counts; Z moves so unevenly that the book figure is a guess by the month end.
XYZ_THRESHOLDS = ((Decimal("0.5"), "X"), (Decimal("1.0"), "Y"))

# Days between counts, before risk adjustments. An A item four times a quarter,
# a C item once a year — the whole point of the exercise.
BASE_INTERVAL_DAYS = {
    "A": 30, "B": 90, "C": 365,
}

# Multipliers applied to the interval. Below 1.0 means count it more often.
RISK_FACTORS = {
    "controlled": (0.25, "controlled substance — diversion risk and a "
                         "regulatory exposure"),
    "recent_variance": (0.5, "last count disagreed with the book"),
    "expiring": (0.5, "approaching expiry — a wrong count makes the write-off "
                      "wrong too"),
    "unpredictable": (0.75, "demand too erratic for the book figure to hold"),
    "high_value_lot": (0.75, "a single lot carries unusual value"),
}

# No item goes uncounted for longer than this however cheap and dull it is,
# and none is counted more often than this however risky — a daily count is a
# process failure, not a control.
MAX_INTERVAL_DAYS = 365
MIN_INTERVAL_DAYS = 7

# A lot above this share of total inventory value is worth its own attention
# regardless of how the item classifies.
HIGH_VALUE_LOT_SHARE = Decimal("0.05")


@dataclass
class Classified:
    ndc11: str
    annual_value: Decimal
    abc: str
    xyz: str
    interval_days: int
    risks: list[str] = field(default_factory=list)
    explanation: str = ""

    @property
    def klass(self) -> str:
        return f"{self.abc}{self.xyz}"

    def as_dict(self) -> dict:
        return {"ndc11": self.ndc11, "annual_value": float(self.annual_value),
                "abc": self.abc, "xyz": self.xyz, "class": self.klass,
                "interval_days": self.interval_days, "risks": self.risks,
                "explanation": self.explanation}


@dataclass
class Due:
    ndc11: str
    klass: str
    interval_days: int
    last_counted: date | None
    days_since: int | None
    overdue_by: int
    priority: float
    risks: list[str]
    reason: str

    def as_dict(self) -> dict:
        return {"ndc11": self.ndc11, "class": self.klass,
                "interval_days": self.interval_days,
                "last_counted": self.last_counted.isoformat() if self.last_counted else None,
                "days_since": self.days_since, "overdue_by": self.overdue_by,
                "priority": round(self.priority, 3), "risks": self.risks,
                "reason": self.reason}


def annual_value(*, avg_daily_demand, unit_cost, on_hand=0) -> Decimal:
    """Yearly consumption value, falling back to value-on-hand.

    Consumption is the right basis — twenty turns a year means twenty times the
    opportunity for the book to drift. When demand is unmeasured the item still
    has to be classified somehow, and value-on-hand is the honest substitute:
    it says "we hold this much and do not know how fast it moves", which is
    itself a reason to look at it.
    """
    cost = q(unit_cost or 0)
    if avg_daily_demand is not None and q(avg_daily_demand) > 0:
        return q(q(avg_daily_demand) * Decimal("365") * cost)
    return q(q(on_hand or 0) * cost)


def _abc_for(cumulative_before: Decimal, total: Decimal) -> str:
    """Band from the cumulative share *before* this item is added.

    The item that carries the running total past 80% is still an A item — it is
    the one that got it there. Measuring after the addition puts the single
    largest item in whatever band its own value lands in, which for a
    concentrated catalogue classifies the most valuable line as C.
    """
    if total <= 0:
        return "C"
    share = cumulative_before / total
    for threshold, label in ABC_THRESHOLDS:
        if share < threshold:
            return label
    return "C"


def _xyz_for(avg_daily_demand, stdev_daily) -> str:
    """Variability band from the coefficient of variation."""
    if avg_daily_demand is None or q(avg_daily_demand) <= 0:
        # Nothing moving, nothing to be unpredictable about.
        return "X"
    if stdev_daily is None:
        return "Y"
    cv = q(stdev_daily) / q(avg_daily_demand)
    for threshold, label in XYZ_THRESHOLDS:
        if cv <= threshold:
            return label
    return "Z"


def classify(items: list[dict], *, as_of: date,
             expiring_within_days: int = 90) -> list[Classified]:
    """ABC/XYZ plus the risk adjustments, one row per item.

    `items`: {ndc11, avg_daily_demand, stdev_daily, unit_cost, on_hand,
              is_controlled, next_expiry, last_variance_at, max_lot_value}
    """
    scored = []
    for it in items:
        av = annual_value(avg_daily_demand=it.get("avg_daily_demand"),
                          unit_cost=it.get("unit_cost"),
                          on_hand=it.get("on_hand"))
        scored.append((av, it))
    scored.sort(key=lambda x: -x[0])

    total = q(sum((s[0] for s in scored), Decimal("0")))
    total_value_on_hand = q(sum(
        (q(i.get("on_hand") or 0) * q(i.get("unit_cost") or 0) for i in items),
        Decimal("0")))

    out: list[Classified] = []
    running = q(0)
    for av, it in scored:
        abc = _abc_for(running, total)
        running = q(running + av)
        xyz = _xyz_for(it.get("avg_daily_demand"), it.get("stdev_daily"))

        interval = Decimal(str(BASE_INTERVAL_DAYS[abc]))
        risks: list[str] = []
        notes: list[str] = []

        def apply(name: str) -> None:
            factor, why = RISK_FACTORS[name]
            nonlocal interval
            interval = interval * Decimal(str(factor))
            risks.append(name)
            notes.append(why)

        if it.get("is_controlled"):
            apply("controlled")
        if it.get("last_variance_at"):
            apply("recent_variance")
        nx = it.get("next_expiry")
        if nx is not None and (nx.toordinal() - as_of.toordinal()) <= expiring_within_days:
            apply("expiring")
        if xyz == "Z":
            apply("unpredictable")
        mlv = it.get("max_lot_value")
        if (mlv is not None and total_value_on_hand > 0
                and q(mlv) / total_value_on_hand >= HIGH_VALUE_LOT_SHARE):
            apply("high_value_lot")

        days = max(MIN_INTERVAL_DAYS,
                   min(MAX_INTERVAL_DAYS, int(round(float(interval)))))
        base = BASE_INTERVAL_DAYS[abc]
        explanation = (
            f"class {abc}{xyz} — {base}-day base"
            + (f", tightened to {days} ({'; '.join(notes)})" if notes
               else f", no risk adjustment")
        )
        out.append(Classified(ndc11=str(it.get("ndc11")), annual_value=av,
                              abc=abc, xyz=xyz, interval_days=days,
                              risks=risks, explanation=explanation))
    return out


def due_for_count(classified: list[Classified], last_counted: dict[str, date],
                  *, as_of: date) -> list[Due]:
    """What is due, worst first.

    Priority is how far past its own interval an item is, not how many days it
    has been. An A item ten days late on a thirty-day cycle outranks a C item
    sixty days late on a yearly one, because the first is a third of the way
    into unmonitored territory and the second is barely started.

    An item never counted is treated as fully overdue rather than skipped —
    "we have never checked this" is a stronger reason to count it than "we
    checked it a while ago".
    """
    out: list[Due] = []
    for c in classified:
        last = last_counted.get(c.ndc11)
        if last is None:
            days_since, overdue = None, c.interval_days
            reason = "never counted"
        else:
            days_since = as_of.toordinal() - last.toordinal()
            overdue = days_since - c.interval_days
            if overdue < 0:
                continue
            reason = (f"{days_since}d since the last count on a "
                      f"{c.interval_days}d cycle")
        priority = (overdue + c.interval_days) / max(1, c.interval_days)
        out.append(Due(ndc11=c.ndc11, klass=c.klass,
                       interval_days=c.interval_days, last_counted=last,
                       days_since=days_since, overdue_by=max(0, overdue),
                       priority=priority, risks=c.risks, reason=reason))
    out.sort(key=lambda d: (-d.priority, d.ndc11))
    return out


def plan_session(due: list[Due], *, capacity: int) -> dict:
    """The next count sheet, and an honest statement of what it leaves out.

    A planner that silently truncates to the day's capacity reads as "everything
    is covered". What is deferred is reported, because the gap between what is
    due and what can be counted is the number that justifies either more hands
    or a longer interval — and nobody can argue for either without seeing it.
    """
    if capacity <= 0:
        raise ValueError("capacity must be positive")
    take = due[:capacity]
    deferred = due[capacity:]
    return {
        "lines": [d.as_dict() for d in take],
        "counted": len(take),
        "deferred": len(deferred),
        "deferred_classes": sorted({d.klass for d in deferred}),
        "worst_deferred": deferred[0].as_dict() if deferred else None,
        "coverage_note": (
            f"{len(take)} of {len(due)} due items scheduled; {len(deferred)} "
            f"deferred to the next session." if deferred
            else f"All {len(take)} due items scheduled."),
    }


def effort_saved(classified: list[Classified], *, horizon_days: int = 365,
                 flat_interval_days: int = 90) -> dict:
    """Count-lines a risk-ranked schedule needs against a flat sweep.

    The comparison that decides whether any of this was worth building. It is
    reported rather than assumed, because a schedule that tightens intervals on
    risk can easily cost *more* effort than the flat one it replaced — and if it
    does, that should be visible and argued for, not discovered a year later.
    """
    ranked = sum(max(1, horizon_days // c.interval_days) for c in classified)
    flat = len(classified) * max(1, horizon_days // flat_interval_days)
    a_class = [c for c in classified if c.abc == "A"]
    ranked_a = sum(max(1, horizon_days // c.interval_days) for c in a_class)
    flat_a = len(a_class) * max(1, horizon_days // flat_interval_days)
    return {
        "ranked_lines": ranked,
        "flat_lines": flat,
        "difference": ranked - flat,
        "pct_change": (round(100.0 * (ranked - flat) / flat, 1) if flat else 0.0),
        "a_class_ranked_lines": ranked_a,
        "a_class_flat_lines": flat_a,
        "a_class_coverage_gain": ranked_a - flat_a,
        "note": (
            "Fewer total lines with more attention on A-class stock is the "
            "intended trade. More total lines is only worth it if the risk "
            "adjustments are carrying it."),
    }
