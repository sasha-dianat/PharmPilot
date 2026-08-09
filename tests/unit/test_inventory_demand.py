"""Demand signal — measurement, and the falsification test that catches a
fabricated one.

The production cases at the bottom are not hypotheticals: they are the two rows
from `stock_levels` that motivated this module. If either of them ever stops
being caught, the purchasing engine is back to running on invented numbers.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from services.core.inventory import demand as D

TODAY = date(2026, 8, 9)
W = 28


def fill(day_offset: int, qty: float, *, key: str = "fill_date") -> dict:
    return {key: date.fromordinal(TODAY.toordinal() - day_offset),
            "quantity_dispensed": qty}


# ── measurement ───────────────────────────────────────────────────────────
def test_no_fills_yields_no_rate_rather_than_a_guess():
    """The defect this module exists to prevent: inventing a demand rate.

    `FALLBACK_DEMAND_RATE = 1.0` in the forecaster meant an item with no history
    reported a full unit per day, which a purchasing engine reads as real."""
    e = D.estimate("00000000001", [], window_days=W, as_of=TODAY)
    assert e.basis == "no_history"
    assert e.avg_daily_demand is None
    assert e.confidence == 0.0
    assert "none is assumed" in e.explanation


def test_a_steady_item_gets_a_rate_and_high_confidence():
    fills = [fill(d, 10) for d in range(0, 28, 2)]   # 14 fills, 140 units
    e = D.estimate("x", fills, window_days=W, as_of=TODAY)
    assert e.basis == "observed"
    assert e.avg_daily_demand == Decimal("5.000")
    assert e.events == 14
    assert e.active_days == 14
    assert e.confidence > 0.9


def test_one_fill_is_an_anecdote_not_a_rate():
    e = D.estimate("x", [fill(3, 90)], window_days=W, as_of=TODAY)
    assert e.basis == "sparse"
    assert e.confidence == 0.35
    assert "provisional" in e.explanation
    # The rate is still reported — refusing to compute it would hide the units.
    assert e.avg_daily_demand == Decimal("3.214")


def test_a_short_window_cannot_establish_a_rate_however_many_fills():
    """A fortnight cannot tell a weekly drug from a daily one."""
    fills = [fill(d, 10) for d in range(0, 10)]
    e = D.estimate("x", fills, window_days=10, as_of=TODAY)
    assert e.basis == "sparse"


def test_units_arriving_in_one_burst_score_lower_than_the_same_units_spread():
    burst = D.estimate("x", [fill(1, 40), fill(1, 40), fill(1, 40)],
                       window_days=W, as_of=TODAY)
    spread = D.estimate("x", [fill(d, 30) for d in (2, 9, 16, 23)],
                        window_days=W, as_of=TODAY)
    assert burst.basis == spread.basis == "observed"
    assert burst.confidence < spread.confidence


def test_fills_outside_the_window_are_excluded():
    e = D.estimate("x", [fill(5, 10), fill(400, 999)], window_days=W, as_of=TODAY)
    assert e.units == Decimal("10.000")
    assert e.events == 1


def test_a_future_dated_fill_is_excluded():
    """A fill dated tomorrow is a data error, not demand."""
    e = D.estimate("x", [fill(-3, 500), fill(5, 10)], window_days=W, as_of=TODAY)
    assert e.units == Decimal("10.000")


def test_an_undated_fill_is_ignored_rather_than_counted_at_the_boundary():
    e = D.estimate("x", [{"quantity_dispensed": 999}, fill(5, 10)],
                   window_days=W, as_of=TODAY)
    assert e.units == Decimal("10.000")


def test_alternate_date_and_quantity_column_names_are_accepted():
    rows = [{"dispensed_at": datetime(2026, 8, 5, 9, tzinfo=timezone.utc), "quantity": 7}]
    assert D.estimate("x", rows, window_days=W, as_of=TODAY).units == Decimal("7.000")


def test_a_zero_quantity_fill_still_counts_as_an_event():
    """A dispense of zero is a data problem worth surfacing, not an absence."""
    e = D.estimate("x", [fill(1, 0), fill(2, 0), fill(3, 0)], window_days=W, as_of=TODAY)
    assert (e.events, e.units) == (3, Decimal("0.000"))
    assert e.basis == "observed"


def test_a_nonpositive_window_is_rejected():
    with pytest.raises(ValueError):
        D.estimate("x", [], window_days=0, as_of=TODAY)


# ── falsification ─────────────────────────────────────────────────────────
def obs(units: float, events: int = 5) -> D.DemandEstimate:
    return D.estimate("x", [fill(i + 1, units / events) for i in range(events)],
                      window_days=W, as_of=TODAY)


def test_a_stored_rate_close_to_observed_agrees():
    d = D.divergence(ndc11="x", stored_adq=Decimal("5"), observed=obs(140))
    assert d.verdict == "agrees"
    assert d.severity == "info"
    assert not d.disagrees


def test_a_rate_predicting_hundreds_of_units_against_zero_dispensing_is_contradicted():
    """Not a bad forecast — evidence the number never came from this pharmacy."""
    d = D.divergence(ndc11="x", stored_adq=Decimal("14"), observed=obs(0, events=0))
    assert d.verdict == "contradicted"
    assert d.severity == "high"
    assert "not derived from this pharmacy" in d.explanation


def test_small_numbers_are_not_judged_in_either_direction():
    """0.2/day predicts 5.6 units in 28 days. Observing 0 proves nothing."""
    d = D.divergence(ndc11="x", stored_adq=Decimal("0.2"), observed=obs(0, events=0))
    assert d.verdict == "indeterminate"
    assert d.severity == "info"


def test_understatement_is_high_severity_because_it_risks_stockout():
    d = D.divergence(ndc11="x", stored_adq=Decimal("4"), observed=obs(360))
    assert d.verdict == "understated"
    assert d.severity == "high"
    assert "stockout risk" in d.explanation


def test_overstatement_is_medium_because_it_costs_money_not_patients():
    d = D.divergence(ndc11="x", stored_adq=Decimal("20"), observed=obs(100))
    assert d.verdict == "overstated"
    assert d.severity == "medium"
    assert "over-purchasing" in d.explanation


def test_a_stored_zero_against_real_dispensing_is_understated():
    """This item will never be reordered, however fast it moves."""
    d = D.divergence(ndc11="x", stored_adq=Decimal("0"), observed=obs(280))
    assert d.verdict == "understated"
    assert "never reorder" in d.explanation


def test_no_stored_signal_is_indeterminate_not_a_failure():
    d = D.divergence(ndc11="x", stored_adq=None, observed=obs(280))
    assert d.verdict == "indeterminate"
    assert d.stored_adq is None


def test_lumpy_demand_within_two_times_is_tolerated():
    """Demand is genuinely uneven; a check that fires on 1.8x gets switched off."""
    d = D.divergence(ndc11="x", stored_adq=Decimal("5"), observed=obs(250))
    assert d.verdict == "agrees"


def test_just_past_two_times_is_reported():
    d = D.divergence(ndc11="x", stored_adq=Decimal("5"), observed=obs(300))
    assert d.verdict == "understated"


# ── staleness ─────────────────────────────────────────────────────────────
def test_a_signal_that_was_never_computed_is_stale():
    assert D.staleness_days(None, TODAY) is None
    assert D.is_stale(None, TODAY) is True


def test_a_fresh_signal_is_not_stale():
    assert D.is_stale(datetime(2026, 8, 8, tzinfo=timezone.utc), TODAY) is False


def test_the_production_signal_age_is_stale():
    """forecast_updated_at was 2026-06-16 for all 16 rows, never moved."""
    assert D.staleness_days(datetime(2026, 6, 16, tzinfo=timezone.utc), TODAY) == 54
    assert D.is_stale(datetime(2026, 6, 16, tzinfo=timezone.utc), TODAY) is True


# ── the two production rows that motivated this module ────────────────────
def test_production_row_00093310705_is_caught():
    """Stored 14.0/day, 0 units ever dispensed, reorder point 150 vs 90 on hand.

    Left alone this item generates a purchase recommendation forever for a drug
    nobody takes."""
    d = D.divergence(ndc11="00093310705", stored_adq=Decimal("14.000"),
                     observed=D.estimate("00093310705", [], window_days=W, as_of=TODAY))
    assert d.verdict == "contradicted"
    assert d.expected_units == Decimal("392.000")
    assert d.observed_units == Decimal("0.000")


def test_production_row_00781182901_is_caught():
    """Stored 4.0/day against 360 units in 28 days = 12.86/day, 3.2x understated.

    This is the metoprolol whose expired lot was quarantined — the item most at
    risk of a real stockout is the one the engine believed was slowest."""
    fills = [fill(i * 2 + 1, 30) for i in range(12)]     # 360 units, 12 fills
    e = D.estimate("00781182901", fills, window_days=W, as_of=TODAY)
    assert e.avg_daily_demand == Decimal("12.857")
    d = D.divergence(ndc11="00781182901", stored_adq=Decimal("4.000"), observed=e)
    assert d.verdict == "understated"
    assert d.severity == "high"
    assert "3.2x" in d.explanation


# ── refresh planning ──────────────────────────────────────────────────────
def test_a_refresh_reports_the_old_value_standing_not_just_the_new_one():
    """An operator approving a refresh must see which numbers were wrong."""
    stock = [{"ndc11": "A", "avg_daily_demand": Decimal("14")}]
    plan = D.plan_refresh(stock, {}, window_days=W, as_of=TODAY)
    assert len(plan) == 1
    r = plan[0]
    assert r.verdict == "contradicted"
    assert r.new_adq is None and r.basis == "no_history"
    assert r.changed is True


def test_a_refresh_writes_null_rather_than_zero_when_there_is_no_history():
    """NULL means unknown and suppresses purchasing. Zero means 'measured as
    zero', which is a different and unsupported claim."""
    plan = D.plan_refresh([{"ndc11": "A", "avg_daily_demand": None}], {},
                          window_days=W, as_of=TODAY)
    assert plan[0].new_adq is None
    assert plan[0].changed is False       # NULL -> NULL is not a change


def test_a_refresh_marks_an_unchanged_rate_as_unchanged():
    fills = [fill(d, 10) for d in range(0, 28, 2)]      # 140 units -> 5.000/day
    plan = D.plan_refresh([{"ndc11": "A", "avg_daily_demand": Decimal("5.000")}],
                          {"A": fills}, window_days=W, as_of=TODAY)
    assert plan[0].changed is False
    assert plan[0].verdict == "agrees"


def test_a_refresh_covers_every_stocked_item_including_ones_with_no_fills():
    stock = [{"ndc11": "A", "avg_daily_demand": None},
             {"ndc11": "B", "avg_daily_demand": Decimal("2")}]
    plan = D.plan_refresh(stock, {"B": [fill(1, 100)]}, window_days=W, as_of=TODAY)
    assert [p.ndc11 for p in plan] == ["A", "B"]
    assert plan[0].basis == "no_history"
    assert plan[1].basis == "sparse"


# ── variability, which is what safety stock is computed from ──────────────
def test_variability_counts_the_days_with_no_dispensing():
    """For an intermittent drug the zeros are most of the distribution. Taking
    stdev over active days only would understate safety stock exactly where
    cover matters most."""
    steady = D.estimate("x", [fill(d, 5) for d in range(28)],
                        window_days=W, as_of=TODAY)
    lumpy = D.estimate("x", [fill(3, 140)], window_days=W, as_of=TODAY)
    assert steady.units == lumpy.units == Decimal("140.000")
    assert steady.stdev_daily == Decimal("0.000")
    assert lumpy.stdev_daily > Decimal("20")


def test_no_history_reports_no_variability_rather_than_zero_variability():
    """Zero would read as 'perfectly predictable' and suppress safety stock."""
    assert D.estimate("x", [], window_days=W, as_of=TODAY).stdev_daily is None
