"""Cycle counting — the schedule that decides where the counting hours go.

The claim being made is that counting effort should follow value and risk
rather than being spread flat. These tests pin the parts of that claim which
could quietly stop being true: that A-class is decided by turnover and not by
what happens to be sitting on the shelf, that risk tightens the interval, that
nothing is either counted daily or left uncounted for years, and that the
planner says out loud what it could not fit.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from services.core.inventory import cycle_count as CC

TODAY = date(2026, 8, 9)


def item(ndc, *, demand=None, stdev=None, cost=10, on_hand=100,
         controlled=False, expiry=None, variance=None, max_lot_value=None) -> dict:
    return {"ndc11": ndc, "avg_daily_demand": demand, "stdev_daily": stdev,
            "unit_cost": cost, "on_hand": on_hand, "is_controlled": controlled,
            "next_expiry": expiry, "last_variance_at": variance,
            "max_lot_value": max_lot_value}


def by_ndc(rows):
    return {c.ndc11: c for c in rows}


# ── ABC follows turnover, not what is sitting on the shelf ────────────────
def test_annual_value_is_consumption_not_stock_on_hand():
    """An item turning over twenty times a year has had twenty times the chance
    for the book to drift, however little of it is on the shelf right now."""
    fast = CC.annual_value(avg_daily_demand=10, unit_cost=5, on_hand=1)
    slow = CC.annual_value(avg_daily_demand=None, unit_cost=5, on_hand=1000)
    assert fast == Decimal("18250.000")
    assert slow == Decimal("5000.000")
    assert fast > slow


def test_an_unmeasured_item_falls_back_to_value_on_hand():
    """It still has to be classified, and 'we hold this much and do not know how
    fast it moves' is itself a reason to look."""
    assert CC.annual_value(avg_daily_demand=None, unit_cost=4,
                           on_hand=25) == Decimal("100.000")


def test_the_money_concentrates_into_a_class():
    rows = CC.classify(
        [item("BIG", demand=100, cost=50)]
        + [item(f"S{i}", demand=1, cost=1) for i in range(20)], as_of=TODAY)
    got = by_ndc(rows)
    assert got["BIG"].abc == "A"
    assert got["S19"].abc == "C"


def test_a_class_is_counted_far_more_often_than_c_class():
    rows = by_ndc(CC.classify(
        [item("BIG", demand=100, cost=50)]
        + [item(f"S{i}", demand=1, cost=1) for i in range(20)], as_of=TODAY))
    assert rows["BIG"].interval_days <= 30
    assert rows["S19"].interval_days >= 180


def test_an_empty_catalogue_does_not_divide_by_zero():
    assert CC.classify([], as_of=TODAY) == []


def test_items_with_no_value_at_all_still_classify():
    rows = CC.classify([item("Z", demand=None, cost=0, on_hand=0)], as_of=TODAY)
    assert rows[0].abc == "C"


# ── XYZ is about whether the book figure can be trusted between counts ────
def test_a_steady_item_is_x():
    rows = CC.classify([item("A", demand=10, stdev=1)], as_of=TODAY)
    assert rows[0].xyz == "X"


def test_an_erratic_item_is_z_and_gets_counted_sooner():
    steady = CC.classify([item("A", demand=10, stdev=1)], as_of=TODAY)[0]
    erratic = CC.classify([item("A", demand=10, stdev=30)], as_of=TODAY)[0]
    assert (steady.xyz, erratic.xyz) == ("X", "Z")
    assert erratic.interval_days < steady.interval_days
    assert "unpredictable" in erratic.risks


def test_an_item_that_never_moves_is_not_called_unpredictable():
    """Nothing moving, nothing to be unpredictable about — calling it Z would
    put the whole dead-stock tail on a tight cycle for no reason."""
    assert CC.classify([item("A", demand=0, stdev=None)], as_of=TODAY)[0].xyz == "X"


def test_missing_variability_is_neither_trusted_nor_condemned():
    assert CC.classify([item("A", demand=10, stdev=None)], as_of=TODAY)[0].xyz == "Y"


# ── risk tightens the interval ────────────────────────────────────────────
def test_a_controlled_substance_is_counted_four_times_as_often():
    plain = CC.classify([item("A", demand=1, cost=1)], as_of=TODAY)[0]
    ctrl = CC.classify([item("A", demand=1, cost=1, controlled=True)],
                       as_of=TODAY)[0]
    assert ctrl.interval_days < plain.interval_days
    assert "controlled" in ctrl.risks
    assert "diversion risk" in ctrl.explanation


def test_a_previous_discrepancy_is_the_best_predictor_of_the_next_one():
    base = CC.classify([item("A", demand=1)], as_of=TODAY)[0]
    again = CC.classify([item("A", demand=1, variance=TODAY - timedelta(days=5))],
                        as_of=TODAY)[0]
    assert again.interval_days < base.interval_days
    assert "recent_variance" in again.risks


def test_stock_near_expiry_is_counted_sooner_because_the_write_off_depends_on_it():
    soon = CC.classify([item("A", demand=1, expiry=TODAY + timedelta(days=30))],
                       as_of=TODAY)[0]
    assert "expiring" in soon.risks


def test_stock_expiring_far_out_is_not_a_risk_yet():
    far = CC.classify([item("A", demand=1, expiry=TODAY + timedelta(days=300))],
                      as_of=TODAY)[0]
    assert "expiring" not in far.risks


def test_a_lot_holding_an_unusual_share_of_value_earns_its_own_attention():
    rows = by_ndc(CC.classify(
        [item("BIG", demand=1, cost=10, on_hand=1000, max_lot_value=10000)]
        + [item(f"S{i}", demand=1, cost=1, on_hand=1) for i in range(9)],
        as_of=TODAY))
    assert "high_value_lot" in rows["BIG"].risks


def test_risks_compound_but_nothing_is_counted_more_often_than_weekly():
    """A daily count is a process failure, not a control."""
    worst = CC.classify([item("A", demand=10, stdev=50, cost=1000, controlled=True,
                              expiry=TODAY + timedelta(days=10),
                              variance=TODAY, max_lot_value=10**9)],
                        as_of=TODAY)[0]
    assert worst.interval_days == CC.MIN_INTERVAL_DAYS
    assert len(worst.risks) >= 4


def test_nothing_goes_uncounted_for_more_than_a_year():
    dull = CC.classify([item(f"S{i}", demand=None, cost=0, on_hand=0)
                        for i in range(5)], as_of=TODAY)
    assert all(c.interval_days <= CC.MAX_INTERVAL_DAYS for c in dull)


def test_the_schedule_does_not_consider_who_counted_it():
    """Counter accuracy belongs in a performance conversation. Building it into
    the schedule would turn a stock control into staff surveillance."""
    assert "counter" not in CC.RISK_FACTORS
    assert not any("counted_by" in f for f in CC.RISK_FACTORS)


# ── what is due ───────────────────────────────────────────────────────────
def test_an_item_counted_recently_is_not_due():
    c = CC.classify([item("A", demand=1)], as_of=TODAY)
    assert CC.due_for_count(c, {"A": TODAY - timedelta(days=1)}, as_of=TODAY) == []


def test_an_item_never_counted_is_due_rather_than_skipped():
    """'We have never checked this' is a stronger reason to count than 'we
    checked it a while ago'."""
    c = CC.classify([item("A", demand=1)], as_of=TODAY)
    due = CC.due_for_count(c, {}, as_of=TODAY)
    assert len(due) == 1
    assert due[0].reason == "never counted"


def test_priority_is_relative_to_the_items_own_cycle():
    """An A item ten days late on a thirty-day cycle outranks a C item sixty
    days late on a yearly one."""
    c = CC.classify([item("A", demand=100, cost=100)]
                    + [item("C", demand=1, cost=1)], as_of=TODAY)
    got = by_ndc(c)
    last = {"A": TODAY - timedelta(days=got["A"].interval_days + 10),
            "C": TODAY - timedelta(days=got["C"].interval_days + 60)}
    order = [d.ndc11 for d in CC.due_for_count(c, last, as_of=TODAY)]
    assert order[0] == "A"


# ── the session plan says what it left out ────────────────────────────────
def test_a_session_reports_what_it_could_not_fit():
    """Silent truncation reads as 'everything is covered'."""
    c = CC.classify([item(f"S{i}", demand=1, cost=i + 1) for i in range(10)],
                    as_of=TODAY)
    plan = CC.plan_session(CC.due_for_count(c, {}, as_of=TODAY), capacity=3)
    assert plan["counted"] == 3
    assert plan["deferred"] == 7
    assert plan["worst_deferred"] is not None
    assert "deferred to the next session" in plan["coverage_note"]


def test_a_session_that_fits_everything_says_so():
    c = CC.classify([item("A", demand=1)], as_of=TODAY)
    plan = CC.plan_session(CC.due_for_count(c, {}, as_of=TODAY), capacity=10)
    assert plan["deferred"] == 0
    assert plan["worst_deferred"] is None
    assert "All 1 due items" in plan["coverage_note"]


def test_a_zero_capacity_session_is_refused():
    with pytest.raises(ValueError):
        CC.plan_session([], capacity=0)


# ── does any of this actually save effort ─────────────────────────────────
def test_the_saving_is_measured_rather_than_asserted():
    """A schedule that tightens on risk can easily cost more than the flat one
    it replaced. If it does, that should be visible, not discovered a year on."""
    c = CC.classify([item("BIG", demand=100, cost=50)]
                    + [item(f"S{i}", demand=0.1, cost=1) for i in range(50)],
                    as_of=TODAY)
    out = CC.effort_saved(c, flat_interval_days=90)
    assert out["ranked_lines"] < out["flat_lines"]
    assert out["pct_change"] < 0
    # ...and the attention that was freed goes to the stock that carries value.
    assert out["a_class_coverage_gain"] > 0
