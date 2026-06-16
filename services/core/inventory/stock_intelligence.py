"""Stock movement intelligence — turnover, dead-stock, slow/fast classification,
and a composite inventory-health score. Pure functions over already-fetched rows
(no I/O) so the rules are unit-testable and stay out of the request handler.

Complements the existing expiry-risk (`expiry_prevention`) and supply-risk
(`supply_warning`) services — this is the turnover/dead-stock dimension they
don't cover.
"""
from __future__ import annotations

# Thresholds (annualized inventory turns)
FAST_TURNS = 12.0      # ≥ ~monthly turnover
SLOW_TURNS = 2.0       # < this is slow-moving
DEAD_STOCK_DAYS = 180  # no dispense in this many days (with stock on hand) = dead


def turnover_rate(avg_daily_demand: float | None, on_hand: float) -> float:
    """Annualized inventory turns = (avg daily demand × 365) / units on hand."""
    if not on_hand or on_hand <= 0:
        return 0.0
    demand = float(avg_daily_demand or 0.0)
    return round(demand * 365.0 / float(on_hand), 2)


def movement_class(*, avg_daily_demand: float | None, on_hand: float,
                   last_dispensed_days: int | None) -> str:
    """Classify an item: dead | slow | fast | normal."""
    on_hand = float(on_hand or 0.0)
    if on_hand <= 0:
        return "out"
    if last_dispensed_days is None or last_dispensed_days >= DEAD_STOCK_DAYS:
        return "dead"
    turns = turnover_rate(avg_daily_demand, on_hand)
    if turns >= FAST_TURNS:
        return "fast"
    if turns < SLOW_TURNS:
        return "slow"
    return "normal"


def item_value(on_hand: float, unit_cost: float | None) -> float:
    return round(float(on_hand or 0.0) * float(unit_cost or 0.0), 2)


def classify_items(items: list[dict]) -> list[dict]:
    """Annotate each item with turnover + movement class + tied-up value.

    item: {ndc11, on_hand, unit_cost, avg_daily_demand, last_dispensed_days}
    """
    out = []
    for it in items:
        cls = movement_class(
            avg_daily_demand=it.get("avg_daily_demand"),
            on_hand=it.get("on_hand", 0),
            last_dispensed_days=it.get("last_dispensed_days"),
        )
        out.append({
            **it,
            "turnover": turnover_rate(it.get("avg_daily_demand"), it.get("on_hand", 0)),
            "movement_class": cls,
            "value": item_value(it.get("on_hand", 0), it.get("unit_cost")),
        })
    return out


def health_score(classified: list[dict]) -> int:
    """Composite 0–100 inventory health. Penalizes capital tied up in dead +
    slow stock as a share of total on-hand value. 100 = no dead/slow capital."""
    total_value = sum(c["value"] for c in classified) or 0.0
    if total_value <= 0:
        return 100
    dead_value = sum(c["value"] for c in classified if c["movement_class"] == "dead")
    slow_value = sum(c["value"] for c in classified if c["movement_class"] == "slow")
    # dead capital weighed double vs slow capital
    penalty = (dead_value + 0.5 * slow_value) / total_value
    return max(0, min(100, round(100 * (1 - penalty))))


def summarize(items: list[dict]) -> dict:
    """Full dashboard payload: per-class counts/value, dead-stock list, score."""
    classified = classify_items(items)
    by_class: dict[str, dict] = {}
    for c in classified:
        k = c["movement_class"]
        b = by_class.setdefault(k, {"count": 0, "value": 0.0})
        b["count"] += 1
        b["value"] = round(b["value"] + c["value"], 2)
    dead = sorted([c for c in classified if c["movement_class"] == "dead"],
                  key=lambda c: c["value"], reverse=True)
    return {
        "health_score": health_score(classified),
        "total_value": round(sum(c["value"] for c in classified), 2),
        "dead_stock_value": round(sum(c["value"] for c in dead), 2),
        "by_class": by_class,
        "dead_stock": dead[:50],
        "fast_movers": [c for c in classified if c["movement_class"] == "fast"][:50],
        "slow_movers": [c for c in classified if c["movement_class"] == "slow"][:50],
    }
