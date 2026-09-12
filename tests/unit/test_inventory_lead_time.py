"""Lead time — measured where it can be, labelled as an assumption where it cannot.

The defect these pin down is not a wrong number, it is an unlabelled one: 7 days
in the procurement engine and 2 in the forecaster, neither measured, so the same
item could read critical in one screen and comfortable in the other.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from services.core.inventory import lead_time as LT

TODAY = date(2026, 8, 9)


def po(ordered_days_ago: int | None, took: int | None, wholesaler="acme") -> dict:
    if ordered_days_ago is None:
        return {"wholesaler": wholesaler, "ordered_at": None, "received_at": None}
    o = date.fromordinal(TODAY.toordinal() - ordered_days_ago)
    return {"wholesaler": wholesaler, "ordered_at": o,
            "received_at": None if took is None
                           else date.fromordinal(o.toordinal() + took)}


# ── measurement ───────────────────────────────────────────────────────────
def test_no_delivered_orders_gives_a_labelled_assumption_not_a_silent_constant():
    lt = LT.estimate([])
    assert lt.basis == "declared_default"
    assert lt.days == 7
    assert lt.confidence == 0.0
    assert "an assumption, not an observation" in lt.explanation


def test_deliveries_produce_a_measured_lead_time():
    lt = LT.estimate([po(60, 3), po(50, 4), po(40, 3), po(30, 5)])
    assert lt.basis == "observed"
    assert lt.samples == 4
    assert lt.median_days == 3.5
    assert lt.days == 4          # median rounded, never below 1
    assert lt.confidence > 0.3


def test_one_or_two_deliveries_are_anecdotes():
    lt = LT.estimate([po(30, 3), po(20, 4)])
    assert lt.basis == "sparse"
    assert lt.confidence == 0.35
    assert "provisional" in lt.explanation


def test_an_order_never_received_is_not_a_lead_time():
    lt = LT.estimate([po(30, None), po(20, None)])
    assert lt.basis == "declared_default"
    assert lt.samples == 0


def test_planning_uses_the_median_so_one_stuck_order_does_not_move_everything():
    """Lead-time distributions are right-skewed: a delivery can be arbitrarily
    late and only minimally early."""
    lt = LT.estimate([po(90, 3), po(80, 3), po(70, 3), po(60, 3), po(50, 45)])
    assert lt.median_days == 3.0
    assert lt.days == 3
    assert lt.mean_days > 11      # the mean would have tripled every reorder point


def test_an_implausible_delivery_is_excluded_and_counted():
    """A clerk closing a forgotten order is not a supplier taking four months."""
    lt = LT.estimate([po(400, 200), po(30, 3), po(25, 4), po(20, 3)])
    assert lt.excluded == 1
    assert lt.samples == 3
    assert lt.days == 3


def test_a_delivery_before_its_order_is_dropped_as_a_data_error():
    rows = [{"wholesaler": "acme", "ordered_at": date(2026, 8, 1),
             "received_at": date(2026, 7, 20)}]
    assert LT.estimate(rows).basis == "declared_default"


def test_same_day_delivery_still_plans_at_one_day():
    """Zero would make the reorder point ignore lead time entirely."""
    lt = LT.estimate([po(9, 0), po(8, 0), po(7, 0)])
    assert lt.median_days == 0.0
    assert lt.days == 1


def test_a_scattered_supplier_scores_lower_than_a_consistent_one():
    steady = LT.estimate([po(50 - i, 3) for i in range(6)])
    erratic = LT.estimate([po(50 - i, d) for i, d in enumerate([1, 9, 2, 11, 3, 8])])
    assert steady.basis == erratic.basis == "observed"
    assert steady.confidence > erratic.confidence
    assert erratic.stdev_days > steady.stdev_days


def test_datetimes_are_accepted_as_well_as_dates():
    rows = [{"wholesaler": "a",
             "ordered_at": datetime(2026, 8, 1, 9, tzinfo=timezone.utc),
             "received_at": datetime(2026, 8, 4, 17, tzinfo=timezone.utc)}]
    assert LT.estimate(rows).median_days == 3.0


def test_suppliers_are_measured_separately():
    rows = [po(30, 2, "fast"), po(28, 2, "fast"), po(26, 2, "fast"),
            po(30, 12, "slow"), po(28, 14, "slow"), po(26, 13, "slow")]
    out = LT.by_supplier(rows)
    assert out["fast"].days == 2
    assert out["slow"].days == 13


# ── what lead time is for ─────────────────────────────────────────────────
def test_no_measured_demand_yields_no_reorder_point():
    """A reorder point built on an invented demand rate is the defect Phase 1
    removed, rebuilt one layer down."""
    s = LT.reorder_signals(avg_daily_demand=None, demand_basis="no_history",
                           lead=LT.declared_default())
    assert s.reorder_point is None and s.safety_stock is None
    assert "restatement of the assumption" in s.explanation


def test_measured_demand_and_lead_time_give_a_reorder_point():
    lead = LT.estimate([po(50 - i, 4) for i in range(6)])
    s = LT.reorder_signals(avg_daily_demand=Decimal("5"), demand_basis="observed",
                           demand_stdev=Decimal("1"), lead=lead)
    assert s.reorder_point is not None
    # 5/day x 4 days = 20 of cycle stock, plus safety stock on top.
    assert s.reorder_point > Decimal("20")
    assert s.safety_stock > 0
    assert "4-day lead time (observed)" in s.explanation


def test_an_erratic_supplier_needs_more_safety_stock_than_a_reliable_one():
    """The lead-time variance term is the one usually dropped, and it is the one
    that matters: a supplier who is sometimes very late puts as much stock at
    risk as a drug whose demand swings."""
    steady = LT.estimate([po(50 - i, 5) for i in range(6)])
    erratic = LT.estimate([po(50 - i, d) for i, d in enumerate([5, 1, 9, 5, 2, 10])])
    a = LT.reorder_signals(avg_daily_demand=Decimal("5"), demand_basis="observed",
                           demand_stdev=Decimal("1"), lead=steady)
    b = LT.reorder_signals(avg_daily_demand=Decimal("5"), demand_basis="observed",
                           demand_stdev=Decimal("1"), lead=erratic)
    assert b.safety_stock > a.safety_stock


def test_a_reorder_point_built_on_an_assumed_lead_time_says_so_and_scores_low():
    s = LT.reorder_signals(avg_daily_demand=Decimal("5"), demand_basis="observed",
                           demand_stdev=Decimal("1"), lead=LT.declared_default())
    assert s.lead_time_basis == "declared_default"
    assert s.confidence == 0.3
    assert "(declared_default)" in s.explanation


def test_an_unmeasured_spread_yields_no_cover_rather_than_an_assumed_one():
    """This used to become `demand x 0.5` — an invented coefficient of variation
    presented as a safety stock. For the lumpy items that most need cover it
    understated the real spread several-fold."""
    s = LT.reorder_signals(avg_daily_demand=Decimal("5"), demand_basis="observed",
                           demand_stdev=None, lead=LT.declared_default())
    assert s.reorder_point is None and s.safety_stock is None
    assert "derived from an assumed variability" in s.explanation


# ── E6: demand that arrives all at once ───────────────────────────────────
def test_an_intermittent_item_is_covered_for_one_event_not_for_an_average():
    """The arithmetic is sound and the shelf is still empty on the one day
    somebody is standing at the counter."""
    lead = LT.estimate([po(50 - i, 4) for i in range(6)])
    plain = LT.reorder_signals(avg_daily_demand=Decimal("2"), demand_basis="observed",
                               demand_stdev=Decimal("1"), lead=lead)
    floored = LT.reorder_signals(avg_daily_demand=Decimal("2"), demand_basis="observed",
                                 demand_stdev=Decimal("1"), lead=lead,
                                 demand_class="lumpy", event_floor=Decimal("40"))
    assert plain.safety_stock < Decimal("40")
    assert floored.safety_stock == Decimal("40.000")
    assert floored.floor_applied is True
    assert "arrives all at once" in floored.explanation


def test_a_smooth_item_is_not_inflated_by_a_floor_it_does_not_need():
    lead = LT.estimate([po(50 - i, 4) for i in range(6)])
    s = LT.reorder_signals(avg_daily_demand=Decimal("5"), demand_basis="observed",
                           demand_stdev=Decimal("2"), lead=lead,
                           demand_class="smooth", event_floor=None)
    assert s.floor_applied is False


def test_the_floor_only_wins_when_it_is_the_larger_number():
    """It is a floor, not an override: where the sigma figure already covers an
    event, raising nothing is correct."""
    lead = LT.estimate([po(50 - i, 4) for i in range(6)])
    s = LT.reorder_signals(avg_daily_demand=Decimal("50"), demand_basis="observed",
                           demand_stdev=Decimal("30"), lead=lead,
                           demand_class="lumpy", event_floor=Decimal("5"))
    assert s.floor_applied is False
    assert s.safety_stock > Decimal("5")


def test_the_two_old_constants_are_now_one():
    """7 in procurement, 2 in the forecaster. One named default replaces both."""
    assert LT.DECLARED_DEFAULT_DAYS == 7
