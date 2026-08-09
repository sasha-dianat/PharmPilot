"""Lead time — how long a supplier actually takes, rather than how long we guess.

Lead time sets the reorder point (`demand × lead_time + safety stock`), the
order-by date, and the urgency band. It was two different hard-coded constants
that never met: `procurement.build_recommendations` assumed 7 days and
`forecaster._ensemble_forecast` assumed 2, so the same item could be "critical"
in one screen and comfortable in the other. Neither was measured, and
`purchase_orders` — which carries `ordered_at` and `received_at`, everything
needed to derive it — had no rows at all.

The discipline is the one `demand.py` uses, for the same reason: a planning
input must be able to say where it came from.

  observed          derived from this supplier's delivered orders
  sparse            derived from too few to be a distribution
  declared_default  nobody has measured it; this is a stated assumption

A `declared_default` is not a failure — a new pharmacy has no purchase history
and still has to order. It is a failure to let it *look* like a measurement,
which is what an unlabelled constant buried in two modules did.

Variability matters as much as the average. A supplier that takes 3 days ±1
needs less safety stock than one averaging 3 days that sometimes takes 10, and
a mean alone cannot tell them apart.

Pure functions over already-fetched rows; `as_of` is always injected.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from statistics import median, pstdev

from .ledger import q

# The stated assumption when nothing has been measured. One constant, named,
# in one place — replacing the 7 in procurement and the 2 in the forecaster.
DECLARED_DEFAULT_DAYS = 7

# Below this many delivered orders the spread is not a distribution, only a
# couple of anecdotes that happen to have a mean.
MIN_DELIVERIES_FOR_RATE = 3

# A delivery recorded as taking longer than this is almost always a receiving
# clerk closing an old order, not a supplier taking three months. Including it
# would drag the mean and inflate every reorder point that uses it.
MAX_PLAUSIBLE_DAYS = 90

BASES = ("observed", "sparse", "declared_default")


@dataclass(frozen=True)
class LeadTime:
    supplier: str | None
    days: int                  # what a planner should use
    mean_days: float | None
    median_days: float | None
    stdev_days: float | None
    samples: int
    basis: str
    confidence: float
    explanation: str
    excluded: int = 0          # implausible deliveries dropped

    def as_dict(self) -> dict:
        return {"supplier": self.supplier, "days": self.days,
                "mean_days": self.mean_days, "median_days": self.median_days,
                "stdev_days": self.stdev_days, "samples": self.samples,
                "basis": self.basis, "confidence": self.confidence,
                "excluded": self.excluded, "explanation": self.explanation}


def _days_between(ordered, received) -> int | None:
    if ordered is None or received is None:
        return None
    o = ordered.date() if isinstance(ordered, datetime) else ordered
    r = received.date() if isinstance(received, datetime) else received
    if not isinstance(o, date) or not isinstance(r, date):
        return None
    delta = r.toordinal() - o.toordinal()
    # A delivery before its order is a data error, not a zero-day supplier.
    return None if delta < 0 else delta


def samples_from(orders: list[dict]) -> tuple[list[int], int]:
    """Delivered lead times in days, and how many were dropped as implausible."""
    kept, dropped = [], 0
    for o in orders:
        d = _days_between(o.get("ordered_at"), o.get("received_at"))
        if d is None:
            continue
        if d > MAX_PLAUSIBLE_DAYS:
            dropped += 1
            continue
        kept.append(d)
    return kept, dropped


def declared_default(supplier: str | None = None,
                     days: int = DECLARED_DEFAULT_DAYS) -> LeadTime:
    """The stated assumption, labelled as one."""
    return LeadTime(
        supplier=supplier, days=days, mean_days=None, median_days=None,
        stdev_days=None, samples=0, basis="declared_default", confidence=0.0,
        explanation=(
            f"No delivered orders to measure — using the declared default of "
            f"{days} days. This is an assumption, not an observation."),
    )


def estimate(orders: list[dict], *, supplier: str | None = None,
             default_days: int = DECLARED_DEFAULT_DAYS) -> LeadTime:
    """Lead time for one supplier from its delivered purchase orders.

    Plans on the median rather than the mean: one order held up for a month
    should not move every reorder point for the next quarter, and lead-time
    distributions are right-skewed — deliveries can be arbitrarily late and only
    minimally early.
    """
    kept, dropped = samples_from(orders)
    if not kept:
        lt = declared_default(supplier, default_days)
        return LeadTime(**{**lt.__dict__, "excluded": dropped})

    med = float(median(kept))
    mean = round(sum(kept) / len(kept), 2)
    sd = round(float(pstdev(kept)), 2) if len(kept) > 1 else 0.0
    plan_days = max(1, int(round(med)))

    if len(kept) < MIN_DELIVERIES_FOR_RATE:
        return LeadTime(
            supplier=supplier, days=plan_days, mean_days=mean, median_days=med,
            stdev_days=sd, samples=len(kept), basis="sparse", confidence=0.35,
            excluded=dropped,
            explanation=(
                f"{len(kept)} delivered order(s), median {med} days — too few "
                f"to describe a supplier; treat {plan_days} days as provisional."),
        )

    # Confidence rises with the number of deliveries and falls as they scatter.
    spread_penalty = min(0.4, (sd / med) if med else 0.4)
    confidence = round(min(0.95, 0.55 + 0.05 * len(kept)) - spread_penalty, 3)
    return LeadTime(
        supplier=supplier, days=plan_days, mean_days=mean, median_days=med,
        stdev_days=sd, samples=len(kept), basis="observed",
        confidence=max(0.1, confidence), excluded=dropped,
        explanation=(
            f"{len(kept)} delivered orders, median {med} days "
            f"(mean {mean}, sd {sd})."),
    )


def by_supplier(orders: list[dict], *,
                default_days: int = DECLARED_DEFAULT_DAYS) -> dict[str, LeadTime]:
    """One estimate per supplier named in the orders."""
    grouped: dict[str, list[dict]] = {}
    for o in orders:
        grouped.setdefault(str(o.get("wholesaler") or "unknown"), []).append(o)
    return {name: estimate(rows, supplier=name, default_days=default_days)
            for name, rows in grouped.items()}


# ── what lead time is actually for ────────────────────────────────────────

# Service level for safety stock. 1.65 sigma ≈ 95% — the usual retail-pharmacy
# posture: a stockout is a patient sent away, so it is worth carrying a little
# more than the mean demands.
SERVICE_FACTOR = 1.65


@dataclass(frozen=True)
class ReorderSignals:
    """Reorder point and safety stock, with the provenance of both inputs.

    Both are None when either input is unmeasured. That is deliberate: a reorder
    point derived from an invented demand rate is exactly the number that had
    an item claiming 14 units/day against nothing dispensed, and writing it
    would rebuild what Phase 1 removed.
    """
    reorder_point: Decimal | None
    safety_stock: Decimal | None
    demand_basis: str
    lead_time_basis: str
    lead_time_days: int
    confidence: float
    explanation: str

    def as_dict(self) -> dict:
        return {
            "reorder_point": None if self.reorder_point is None else float(self.reorder_point),
            "safety_stock": None if self.safety_stock is None else float(self.safety_stock),
            "demand_basis": self.demand_basis,
            "lead_time_basis": self.lead_time_basis,
            "lead_time_days": self.lead_time_days,
            "confidence": self.confidence,
            "explanation": self.explanation,
        }


def reorder_signals(*, avg_daily_demand, demand_basis: str,
                    demand_stdev=None, lead: LeadTime) -> ReorderSignals:
    """Reorder point and safety stock from measured demand and measured lead time.

        safety stock  = z × sqrt( lead × var(demand) + demand² × var(lead) )
        reorder point = demand × lead + safety stock

    The second term under the root is the one usually dropped, and it is the one
    that matters here: a supplier whose delivery time varies puts as much stock
    at risk as a drug whose demand does. Ignoring it understates safety stock
    for exactly the suppliers worth carrying cover against.
    """
    if avg_daily_demand is None or demand_basis == "no_history":
        return ReorderSignals(
            reorder_point=None, safety_stock=None, demand_basis=demand_basis,
            lead_time_basis=lead.basis, lead_time_days=lead.days,
            confidence=0.0,
            explanation=("No measured demand — a reorder point would be a "
                         "restatement of the assumption, not a decision rule."),
        )

    d = q(avg_daily_demand)
    sd_d = q(demand_stdev if demand_stdev is not None else d * Decimal("0.5"))
    sd_l = Decimal(str(lead.stdev_days or 0))
    lead_days = Decimal(str(lead.days))

    variance = (lead_days * sd_d * sd_d) + (d * d * sd_l * sd_l)
    safety = q(Decimal(str(SERVICE_FACTOR)) * Decimal(str(float(variance) ** 0.5)))
    rop = q(d * lead_days + safety)

    conf = round(min(1.0, 0.5 + 0.5 * lead.confidence), 3) \
        if lead.basis != "declared_default" else 0.3
    return ReorderSignals(
        reorder_point=rop, safety_stock=safety, demand_basis=demand_basis,
        lead_time_basis=lead.basis, lead_time_days=lead.days, confidence=conf,
        explanation=(
            f"{d}/day over a {lead.days}-day lead time ({lead.basis}) "
            f"+ {safety} safety stock at {SERVICE_FACTOR} sigma = reorder at {rop}."),
    )
