"""Procurement recommender — deterministic what/when/how-much-to-order rules.

Pure functions over already-fetched rows (no I/O, no datetime.now) so the
order-up-to logic is fully unit-testable and stays out of the request handler.
`today` is always injected by the caller.

Complements the turnover/dead-stock view (`stock_intelligence`) and the
supply/expiry risk services — this is the forward-looking purchasing dimension:
order-up-to-level replenishment that is lead-time aware (avoid stockout) and
expiry aware (don't overstock past a max days-of-supply ceiling).
"""
from __future__ import annotations

from datetime import date, timedelta
from math import ceil

# Urgency multiplier: within 1× lead time = critical, within 2× = high.
HIGH_LEAD_MULTIPLIER = 2.0


def days_of_stock(on_hand: float, avg_daily_demand: float | None) -> float | None:
    """How many days the on-hand quantity lasts at current demand.

    None when demand is zero/negative (a runway can't be computed)."""
    demand = float(avg_daily_demand or 0.0)
    if demand <= 0:
        return None
    return round(float(on_hand or 0.0) / demand, 2)


def recommend_order_qty(
    *,
    on_hand: float,
    avg_daily_demand: float | None,
    lead_time_days: int,
    safety_stock: float = 0,
    par_level_max: float | None = None,
    review_days: int = 14,
    max_days_supply: int = 120,
) -> int:
    """Order-up-to replenishment quantity in integer units.

    target  = demand × (lead_time + review_days) + safety_stock
    qty     = max(0, ceil(target - on_hand))
    Then cap so on_hand + qty never exceeds:
      * par_level_max (if provided), and
      * max_days_supply × demand  (expiry-aware — don't overstock perishables).
    """
    demand = float(avg_daily_demand or 0.0)
    on_hand = float(on_hand or 0.0)
    if demand <= 0:
        return 0

    target = demand * (lead_time_days + review_days) + float(safety_stock or 0.0)
    qty = max(0.0, target - on_hand)

    # Expiry-aware ceiling: resulting on-hand cannot exceed max_days_supply of demand.
    supply_ceiling = max_days_supply * demand
    caps = [supply_ceiling]
    if par_level_max is not None:
        caps.append(float(par_level_max))
    ceiling = min(caps)
    qty = min(qty, max(0.0, ceiling - on_hand))

    return int(ceil(qty)) if qty > 0 else 0


def order_by_date(
    *,
    today: date,
    on_hand: float,
    avg_daily_demand: float | None,
    lead_time_days: int,
) -> date | None:
    """Latest date to place the order so it arrives before stockout.

    today + (days_of_stock - lead_time_days). Clamped to >= today (order now if
    we're already inside the lead-time window). None when demand is zero."""
    dos = days_of_stock(on_hand, avg_daily_demand)
    if dos is None:
        return None
    offset = int(dos) - lead_time_days
    if offset < 0:
        offset = 0
    return today + timedelta(days=offset)


def urgency(*, days_of_stock: float | None, lead_time_days: int) -> str:
    """Procurement urgency band relative to lead time.

    critical: runway <= lead time | high: <= 2× lead time | normal: otherwise.
    No demand (days_of_stock None) -> normal."""
    if days_of_stock is None:
        return "normal"
    if days_of_stock <= lead_time_days:
        return "critical"
    if days_of_stock <= lead_time_days * HIGH_LEAD_MULTIPLIER:
        return "high"
    return "normal"


# Sort order: critical first, then high, then normal.
_URGENCY_RANK = {"critical": 0, "high": 1, "normal": 2}


def _rationale(*, dos: float | None, lead_time_days: int, qty: int,
               order_by: date | None) -> str:
    by = f" by {order_by.isoformat()}" if order_by is not None else ""
    if dos is None:
        return (f"No recent demand — order {qty} units{by} "
                f"to reach target stock level.")
    return (f"{dos}d of stock vs {lead_time_days}d lead time — "
            f"order {qty} units{by} to avoid stockout.")


def build_recommendations(
    items: list[dict],
    *,
    today: date,
    budget: float | None = None,
    lead_time_default: int | None = None,
) -> dict:
    """Build the procurement recommendation payload.

    item: {ndc11, on_hand, avg_daily_demand, unit_cost, reorder_point,
           safety_stock, par_level_max, lead_time_days(optional)}

    Only items needing units (recommended_order_qty > 0) are included.
    Sorted by urgency (critical first) then days_of_stock ascending (None last).
    """
    # One source for the assumption, shared with the forecaster. These used to
    # be two unrelated constants — 7 here and 2 there — so the same item could
    # read critical on one screen and comfortable on the other.
    from .lead_time import DECLARED_DEFAULT_DAYS
    fallback = DECLARED_DEFAULT_DAYS if lead_time_default is None else lead_time_default

    recs: list[dict] = []
    for it in items:
        on_hand = float(it.get("on_hand") or 0.0)
        demand = it.get("avg_daily_demand")
        lead_time = int(it.get("lead_time_days") or fallback)
        # Whether that lead time was measured, so the recommendation can say so.
        lead_basis = it.get("lead_time_basis") or (
            "observed" if it.get("lead_time_days") else "declared_default")
        safety = it.get("safety_stock") or 0.0
        par_max = it.get("par_level_max")

        qty = recommend_order_qty(
            on_hand=on_hand,
            avg_daily_demand=demand,
            lead_time_days=lead_time,
            safety_stock=safety,
            par_level_max=par_max,
        )
        if qty <= 0:
            continue

        dos = days_of_stock(on_hand, demand)
        obd = order_by_date(
            today=today, on_hand=on_hand,
            avg_daily_demand=demand, lead_time_days=lead_time,
        )
        urg = urgency(days_of_stock=dos, lead_time_days=lead_time)
        unit_cost = it.get("unit_cost")
        est_cost = round(qty * float(unit_cost if unit_cost is not None else 0.0), 2)

        recs.append({
            "ndc11": it.get("ndc11"),
            "on_hand": on_hand,
            "avg_daily_demand": float(demand or 0.0),
            "days_of_stock": dos,
            "reorder_point": it.get("reorder_point"),
            "lead_time_days": lead_time,
            "lead_time_basis": lead_basis,
            "recommended_order_qty": qty,
            "order_by_date": obd.isoformat() if obd is not None else None,
            "urgency": urg,
            "est_cost": est_cost,
            "rationale": _rationale(
                dos=dos, lead_time_days=lead_time, qty=qty, order_by=obd,
            ),
        })

    # critical first, then days_of_stock asc (None -> last via +inf).
    recs.sort(key=lambda r: (
        _URGENCY_RANK.get(r["urgency"], 99),
        r["days_of_stock"] if r["days_of_stock"] is not None else float("inf"),
    ))

    total_est_cost = round(sum(r["est_cost"] for r in recs), 2)
    critical_count = sum(1 for r in recs if r["urgency"] == "critical")
    within_budget = True if budget is None else total_est_cost <= budget

    return {
        "recommendations": recs,
        "summary": {
            "total_skus": len(recs),
            "total_est_cost": total_est_cost,
            "critical_count": critical_count,
            "budget": budget,
            "within_budget": within_budget,
        },
    }
