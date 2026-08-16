"""Expiry risk — which stock will still be here when it expires.

The shipped engine (service ⑫, `expiry_prevention._score_lot`) charged every lot
the *full* demand independently. Two lots of 100 units selling 1/day, both
expiring in 100 days, both came back `projected_waste=0, band=ok` — when only
100 units of demand exists and 100 units are certain to be destroyed. The first
test here is that exact case.

The second thing these pin is the degradation. An item with no measured demand
cannot be projected at all. Reporting it as zero risk reads as safe; reporting
it as total risk floods the list with dead stock nobody can act on. It is
reported as *unknown*, separately, and kept out of the headline total.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from services.core.inventory import expiry_risk as ER

TODAY = date(2026, 8, 15)


def lot(lot_id, days_to_expiry, qty, cost=10, ndc="N1", number=None):
    return {"lot_id": lot_id, "ndc11": ndc, "lot_number": number or lot_id,
            "expiry_date": TODAY + timedelta(days=days_to_expiry),
            "quantity_on_hand": Decimal(str(qty)),
            "unit_cost": None if cost is None else Decimal(str(cost))}


# ── the defect this engine was rebuilt for ────────────────────────────────
def test_demand_is_shared_between_lots_not_given_to_each_one():
    """Two lots, 100 units each, 1/day, both expiring in 100 days.

    Only 100 units can sell in that window. The first lot absorbs it and the
    second is entirely wasted. Charging both the full demand — as the previous
    engine did — reports zero waste on a certain £1,000 loss.
    """
    risks = ER.assess_item(
        [lot("A", 100, 100), lot("B", 100, 100)],
        avg_daily_demand=Decimal("1"), basis="observed", as_of=TODAY)

    by_id = {r.lot_id: r for r in risks}
    assert by_id["A"].at_risk_units == Decimal("0.000")
    assert by_id["B"].at_risk_units == Decimal("100.000")
    assert by_id["B"].at_risk_value == Decimal("1000.000")


def test_the_soonest_expiring_lot_is_served_first():
    """FEFO decides who is at risk, so the order of assessment is the order the
    allocator will actually reach them — not the order they were received."""
    risks = ER.assess_item(
        [lot("LATE", 300, 50), lot("SOON", 30, 50)],
        avg_daily_demand=Decimal("1"), basis="observed", as_of=TODAY)
    assert [r.lot_id for r in risks] == ["SOON", "LATE"]
    # 30 days of demand covers 30 of SOON's 50; the rest of SOON is at risk.
    assert risks[0].at_risk_units == Decimal("20.000")


def test_a_single_fast_moving_lot_is_not_at_risk():
    risks = ER.assess_item([lot("A", 100, 50)], avg_daily_demand=Decimal("5"),
                           basis="observed", as_of=TODAY)
    assert risks[0].at_risk_units == Decimal("0.000")
    assert risks[0].action == "watch"


# ── degradation: no demand is not zero demand ─────────────────────────────
def test_an_item_with_no_history_reports_unknown_not_safe():
    """Zero risk reads as safe. This says the exposure cannot be projected."""
    risks = ER.assess_item([lot("A", 60, 40)], avg_daily_demand=None,
                           basis="no_history", as_of=TODAY)
    r = risks[0]
    assert r.at_risk_units is None
    assert r.projected_sales is None
    assert r.action == "unknown"
    assert "cannot be projected" in r.explanation
    assert "40.000 units are exposed, not none" in r.explanation


def test_a_stored_rate_is_ignored_when_the_basis_says_no_history():
    """The basis is authoritative. A leftover number with `no_history` beside it
    is exactly the fabricated signal this project spent a week removing."""
    risks = ER.assess_item([lot("A", 60, 40)], avg_daily_demand=Decimal("99"),
                           basis="no_history", as_of=TODAY)
    assert risks[0].at_risk_units is None


def test_unknown_exposure_is_kept_out_of_the_headline_total():
    """Otherwise the total is a floor being reported as the figure."""
    exp = ER.assess([
        {"ndc11": "MEASURED", "avg_daily_demand": Decimal("1"),
         "demand_basis": "observed", "lots": [lot("M", 10, 100, ndc="MEASURED")]},
        {"ndc11": "UNKNOWN", "avg_daily_demand": None,
         "demand_basis": "no_history", "lots": [lot("U", 10, 500, cost=50,
                                                    ndc="UNKNOWN")]},
    ], as_of=TODAY)
    assert exp.unknown_lots == 1
    assert exp.unknown_units == Decimal("500.000")
    assert exp.value_at_risk == Decimal("900.000")      # only the measured lot
    assert "a floor" in exp.as_dict()["coverage_note"]


def test_everything_measured_says_so_plainly():
    exp = ER.assess([{"ndc11": "N1", "avg_daily_demand": Decimal("1"),
                      "demand_basis": "observed", "lots": [lot("A", 10, 5)]}],
                    as_of=TODAY)
    assert exp.unknown_lots == 0
    assert "Every lot" in exp.as_dict()["coverage_note"]


# ── the action depends on how long is left ────────────────────────────────
def test_stock_already_expired_is_a_write_off():
    risks = ER.assess_item([lot("A", -5, 20)], avg_daily_demand=Decimal("1"),
                           basis="observed", as_of=TODAY)
    assert risks[0].action == "write_off"
    assert risks[0].severity == "critical"
    assert "Expired 5 days ago" in risks[0].explanation


def test_close_to_expiry_within_the_return_window_is_a_supplier_return():
    risks = ER.assess_item([lot("A", 20, 100)], avg_daily_demand=Decimal("1"),
                           basis="observed", as_of=TODAY, return_window=90)
    assert risks[0].action == "return_to_supplier"
    assert risks[0].severity == "high"


def test_close_to_expiry_past_the_return_window_can_only_be_discounted():
    """A return that the supplier will not accept is not an action."""
    risks = ER.assess_item([lot("A", 20, 100)], avg_daily_demand=Decimal("1"),
                           basis="observed", as_of=TODAY, return_window=10)
    assert risks[0].action == "discount_or_promote"


def test_a_few_months_out_there_is_still_time_to_sell_it():
    risks = ER.assess_item([lot("A", 75, 200)], avg_daily_demand=Decimal("1"),
                           basis="observed", as_of=TODAY)
    assert risks[0].action == "discount_or_promote"
    assert risks[0].severity == "medium"


def test_far_out_but_over_stocked_is_a_transfer_candidate():
    risks = ER.assess_item([lot("A", 150, 500)], avg_daily_demand=Decimal("1"),
                           basis="observed", as_of=TODAY)
    assert risks[0].action == "transfer_out"


# ── what deserves a recommendation of its own ─────────────────────────────
def test_advice_is_only_raised_where_there_is_something_to_do():
    """A list of every lot expiring in two years is a list nobody reads."""
    safe = ER.assess_item([lot("A", 500, 10)], avg_daily_demand=Decimal("5"),
                          basis="observed", as_of=TODAY)[0]
    assert ER.worth_raising(safe) is False


def test_unknown_exposure_is_not_raised_as_advice():
    """It is reported in the exposure, but there is no action to recommend."""
    unknown = ER.assess_item([lot("A", 30, 100)], avg_daily_demand=None,
                             basis="no_history", as_of=TODAY)[0]
    assert unknown.action == "unknown"
    assert ER.worth_raising(unknown) is False


def test_trivial_value_is_not_worth_an_operators_attention():
    tiny = ER.assess_item([lot("A", 20, 1, cost=Decimal("0.10"))],
                          avg_daily_demand=Decimal("0.001"), basis="observed",
                          as_of=TODAY)[0]
    assert tiny.at_risk_units > 0
    assert ER.worth_raising(tiny, min_value=Decimal("1")) is False


def test_real_money_at_risk_is_raised():
    real = ER.assess_item([lot("A", 20, 500, cost=Decimal("40"))],
                          avg_daily_demand=Decimal("1"), basis="observed",
                          as_of=TODAY)[0]
    assert ER.worth_raising(real) is True


# ── arithmetic and edges ──────────────────────────────────────────────────
def test_a_lot_with_no_cost_reports_units_but_no_value():
    """Borrowing a sibling lot's price would invent a loss figure."""
    r = ER.assess_item([lot("A", 10, 50, cost=None)],
                       avg_daily_demand=Decimal("1"), basis="observed",
                       as_of=TODAY)[0]
    assert r.at_risk_units == Decimal("40.000")
    assert r.at_risk_value is None


def test_empty_and_undated_lots_are_skipped():
    risks = ER.assess_item(
        [lot("EMPTY", 30, 0), {"lot_id": "NODATE", "ndc11": "N1",
                               "quantity_on_hand": 10, "expiry_date": None}],
        avg_daily_demand=Decimal("1"), basis="observed", as_of=TODAY)
    assert risks == []


def test_a_zero_demand_rate_is_treated_as_unprojectable():
    """Measured zero and unmeasured both mean the projection says nothing."""
    r = ER.assess_item([lot("A", 30, 10)], avg_daily_demand=Decimal("0"),
                       basis="observed", as_of=TODAY)[0]
    assert r.at_risk_units is None


def test_an_empty_shelf_assesses_to_nothing_rather_than_crashing():
    exp = ER.assess([], as_of=TODAY)
    assert exp.value_at_risk == Decimal("0.000")
    assert exp.lots == []


def test_the_worst_money_sorts_first():
    exp = ER.assess([
        {"ndc11": "SMALL", "avg_daily_demand": Decimal("0.1"),
         "demand_basis": "observed", "lots": [lot("S", 10, 5, 1, ndc="SMALL")]},
        {"ndc11": "BIG", "avg_daily_demand": Decimal("0.1"),
         "demand_basis": "observed", "lots": [lot("B", 10, 500, 90, ndc="BIG")]},
    ], as_of=TODAY)
    assert exp.lots[0].lot_id == "B"
