"""Unit tests for the pure procurement recommender logic.

Imports the pure functions directly — no DB, no FastAPI app. `today` is fixed so
order_by_date assertions are deterministic.
"""
from __future__ import annotations

from datetime import date

import pytest

from services.core.inventory import procurement

TODAY = date(2026, 6, 16)


# --------------------------------------------------------------------------- #
# days_of_stock
# --------------------------------------------------------------------------- #
def test_days_of_stock_normal():
    assert procurement.days_of_stock(100, 10) == 10.0


def test_days_of_stock_rounds_two_dp():
    assert procurement.days_of_stock(100, 3) == 33.33


def test_days_of_stock_zero_demand_is_none():
    assert procurement.days_of_stock(100, 0) is None


def test_days_of_stock_none_demand_is_none():
    assert procurement.days_of_stock(100, None) is None


def test_days_of_stock_negative_demand_is_none():
    assert procurement.days_of_stock(100, -5) is None


# --------------------------------------------------------------------------- #
# recommend_order_qty
# --------------------------------------------------------------------------- #
def test_recommend_order_up_to_math():
    # target = 10 * (7 + 14) + 0 = 210; on_hand 60 -> 150
    qty = procurement.recommend_order_qty(
        on_hand=60, avg_daily_demand=10, lead_time_days=7,
    )
    assert qty == 150


def test_recommend_includes_safety_stock_and_ceils():
    # target = 3.5 * (7 + 14) + 5 = 78.5; on_hand 10 -> 68.5 -> ceil 69
    qty = procurement.recommend_order_qty(
        on_hand=10, avg_daily_demand=3.5, lead_time_days=7, safety_stock=5,
    )
    assert qty == 69


def test_recommend_zero_when_well_stocked():
    # target = 10 * 21 = 210; on_hand already 300 -> no order
    qty = procurement.recommend_order_qty(
        on_hand=300, avg_daily_demand=10, lead_time_days=7,
    )
    assert qty == 0


def test_recommend_zero_when_no_demand():
    assert procurement.recommend_order_qty(
        on_hand=0, avg_daily_demand=0, lead_time_days=7,
    ) == 0


def test_recommend_par_level_cap():
    # target wants 210 on hand, but par_level_max caps resulting on_hand at 100.
    # on_hand 60 -> can only add 40.
    qty = procurement.recommend_order_qty(
        on_hand=60, avg_daily_demand=10, lead_time_days=7, par_level_max=100,
    )
    assert qty == 40


def test_recommend_expiry_cap():
    # demand 10, max_days_supply default 120 -> supply ceiling 1200.
    # target with huge lead/review would exceed; cap so on_hand+qty <= 1200.
    qty = procurement.recommend_order_qty(
        on_hand=200, avg_daily_demand=10, lead_time_days=90, review_days=60,
    )
    # uncapped target = 10*(90+60)=1500; ceiling=1200; on_hand 200 -> 1000
    assert qty == 1000


def test_recommend_par_beats_expiry_when_lower():
    qty = procurement.recommend_order_qty(
        on_hand=0, avg_daily_demand=10, lead_time_days=90, review_days=60,
        par_level_max=50,
    )
    assert qty == 50


# --------------------------------------------------------------------------- #
# order_by_date
# --------------------------------------------------------------------------- #
def test_order_by_date_math():
    # days_of_stock = 200/10 = 20; offset = 20 - 7 = 13 days from today
    obd = procurement.order_by_date(
        today=TODAY, on_hand=200, avg_daily_demand=10, lead_time_days=7,
    )
    assert obd == date(2026, 6, 29)


def test_order_by_date_clamped_to_today():
    # runway 5d, lead 7d -> already inside window -> clamp to today
    obd = procurement.order_by_date(
        today=TODAY, on_hand=50, avg_daily_demand=10, lead_time_days=7,
    )
    assert obd == TODAY


def test_order_by_date_none_when_no_demand():
    assert procurement.order_by_date(
        today=TODAY, on_hand=50, avg_daily_demand=0, lead_time_days=7,
    ) is None


# --------------------------------------------------------------------------- #
# urgency
# --------------------------------------------------------------------------- #
def test_urgency_critical_at_or_below_lead():
    assert procurement.urgency(days_of_stock=7, lead_time_days=7) == "critical"
    assert procurement.urgency(days_of_stock=3, lead_time_days=7) == "critical"


def test_urgency_high_within_double_lead():
    assert procurement.urgency(days_of_stock=10, lead_time_days=7) == "high"
    assert procurement.urgency(days_of_stock=14, lead_time_days=7) == "high"


def test_urgency_normal_beyond_double_lead():
    assert procurement.urgency(days_of_stock=30, lead_time_days=7) == "normal"


def test_urgency_none_is_normal():
    assert procurement.urgency(days_of_stock=None, lead_time_days=7) == "normal"


# --------------------------------------------------------------------------- #
# build_recommendations
# --------------------------------------------------------------------------- #
def _item(ndc, on_hand, demand, **kw):
    base = {
        "ndc11": ndc, "on_hand": on_hand, "avg_daily_demand": demand,
        "unit_cost": 2.0, "reorder_point": 50, "safety_stock": 0,
        "par_level_max": None,
    }
    base.update(kw)
    return base


def test_build_contract_shape():
    out = procurement.build_recommendations(
        [_item("00001", 50, 10)], today=TODAY, lead_time_default=7,
    )
    assert set(out.keys()) == {"recommendations", "summary"}
    rec = out["recommendations"][0]
    assert set(rec.keys()) == {
        "ndc11", "on_hand", "avg_daily_demand", "days_of_stock",
        "reorder_point", "lead_time_days", "recommended_order_qty",
        "order_by_date", "urgency", "est_cost", "rationale",
    }
    summary = out["summary"]
    assert set(summary.keys()) == {
        "total_skus", "total_est_cost", "critical_count",
        "budget", "within_budget",
    }
    assert isinstance(rec["recommended_order_qty"], int)
    assert isinstance(summary["total_skus"], int)


def test_build_filters_zero_qty():
    # well-stocked item should be excluded entirely
    out = procurement.build_recommendations(
        [_item("WELL", 500, 10), _item("NEED", 50, 10)],
        today=TODAY, lead_time_default=7,
    )
    ndcs = [r["ndc11"] for r in out["recommendations"]]
    assert ndcs == ["NEED"]
    assert out["summary"]["total_skus"] == 1


def test_build_sorted_critical_first_then_dos():
    items = [
        _item("NORMAL", 280, 10),   # ~28d -> normal (needs order: target 210? no)
        _item("CRIT", 30, 10),      # 3d -> critical
        _item("HIGH", 100, 10),     # 10d -> high
    ]
    out = procurement.build_recommendations(
        items, today=TODAY, lead_time_default=7,
    )
    order = [(r["ndc11"], r["urgency"]) for r in out["recommendations"]]
    # critical must come before high; both before any normal present
    urgencies = [u for _, u in order]
    rank = {"critical": 0, "high": 1, "normal": 2}
    assert urgencies == sorted(urgencies, key=lambda u: rank[u])
    assert order[0][1] == "critical"


def test_build_dos_ascending_within_same_urgency():
    items = [
        _item("A", 60, 10),   # 6d critical
        _item("B", 20, 10),   # 2d critical
    ]
    out = procurement.build_recommendations(
        items, today=TODAY, lead_time_default=7,
    )
    dos_seq = [r["days_of_stock"] for r in out["recommendations"]]
    assert dos_seq == sorted(dos_seq)
    assert out["recommendations"][0]["ndc11"] == "B"


def test_build_est_cost_and_none_unit_cost():
    out = procurement.build_recommendations(
        [_item("NOCOST", 50, 10, unit_cost=None)],
        today=TODAY, lead_time_default=7,
    )
    assert out["recommendations"][0]["est_cost"] == 0.0


def test_build_budget_within():
    out = procurement.build_recommendations(
        [_item("X", 50, 10, unit_cost=1.0)],
        today=TODAY, budget=10_000.0, lead_time_default=7,
    )
    assert out["summary"]["within_budget"] is True
    assert out["summary"]["budget"] == 10_000.0


def test_build_budget_over():
    out = procurement.build_recommendations(
        [_item("X", 50, 10, unit_cost=100.0)],
        today=TODAY, budget=1.0, lead_time_default=7,
    )
    assert out["summary"]["within_budget"] is False
    assert out["summary"]["total_est_cost"] > 1.0


def test_build_budget_none_is_within():
    out = procurement.build_recommendations(
        [_item("X", 50, 10)], today=TODAY, budget=None, lead_time_default=7,
    )
    assert out["summary"]["within_budget"] is True
    assert out["summary"]["budget"] is None


def test_build_per_item_lead_time_override():
    out = procurement.build_recommendations(
        [_item("OVR", 50, 10, lead_time_days=30)],
        today=TODAY, lead_time_default=7,
    )
    assert out["recommendations"][0]["lead_time_days"] == 30


def test_build_critical_count():
    items = [_item("C", 30, 10), _item("N2", 200, 10)]
    out = procurement.build_recommendations(
        items, today=TODAY, lead_time_default=7,
    )
    assert out["summary"]["critical_count"] == sum(
        1 for r in out["recommendations"] if r["urgency"] == "critical"
    )
