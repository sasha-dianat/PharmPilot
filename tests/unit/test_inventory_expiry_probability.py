"""E9 — the middle that E1 cannot express.

E1 says a lot either clears or does not. Most real stock sits between: the lot
that will probably clear, and the lot that clears only if the next two months are
as good as the last two. A 5% chance of expiring is not worth discounting; the
same lot at 60% is worth discounting today, while there is still a customer.

The refusals carry as much weight as the arithmetic. A normal approximation is
the central limit theorem doing the work, and the theorem needs a sum of many
things — an item that sells twice a quarter has three demand events before expiry
and its total is not remotely normal.
"""
from __future__ import annotations

from decimal import Decimal

from services.core.inventory import expiry_probability as EP


def odds(**kw):
    base = dict(lot_id="L1", ndc11="N1", units=Decimal("100"), days_left=180,
                avg_daily_demand=Decimal("1"), stdev_daily=Decimal("0.5"),
                unit_cost=Decimal("10"), demand_class="smooth",
                adi=Decimal("1"))
    base.update(kw)
    return EP.assess_lot(**base)


# ── the middle ────────────────────────────────────────────────────────────
def test_a_lot_that_will_comfortably_clear_is_unlikely_to_expire():
    o = odds(units=Decimal("50"), days_left=180, avg_daily_demand=Decimal("1"))
    assert o.probability is not None and o.probability < Decimal("0.05")
    assert o.band == "unlikely"


def test_a_lot_far_larger_than_the_demand_will_almost_certainly_expire():
    o = odds(units=Decimal("500"), days_left=100, avg_daily_demand=Decimal("1"))
    assert o.probability > Decimal("0.95")
    assert o.band == "likely"


def test_a_lot_priced_exactly_at_the_expected_demand_is_a_coin_toss():
    """The case E1 has no vocabulary for."""
    o = odds(units=Decimal("180"), days_left=180, avg_daily_demand=Decimal("1"))
    assert Decimal("0.45") < o.probability < Decimal("0.55")


def test_more_variable_demand_makes_a_marginal_lot_riskier():
    tight = odds(units=Decimal("220"), days_left=180, stdev_daily=Decimal("0.2"))
    loose = odds(units=Decimal("220"), days_left=180, stdev_daily=Decimal("2"))
    assert loose.probability < tight.probability   # loose demand may still clear it


def test_the_money_is_the_point():
    o = odds(units=Decimal("500"), days_left=100, unit_cost=Decimal("10"))
    assert o.expected_loss is not None and o.expected_loss > 0


def test_without_a_unit_cost_the_exposure_stays_in_units():
    o = odds(units=Decimal("500"), days_left=100, unit_cost=None)
    assert o.expected_loss is None
    assert any("in units and not in money" in c for c in o.concerns)


# ── the FEFO trap E1 was rebuilt to fix, which would reappear here ────────
def test_demand_already_spoken_for_by_an_earlier_lot_is_not_counted_twice():
    """Charging every lot the item's whole demand made five lots of a slow mover
    each look safe."""
    alone = odds(units=Decimal("150"), days_left=180)
    behind = odds(units=Decimal("150"), days_left=180,
                  consumed_by_earlier=Decimal("150"))
    assert behind.probability > alone.probability
    assert any("already spoken for" in c for c in behind.concerns)


# ── the refusals ──────────────────────────────────────────────────────────
def test_too_few_demand_events_before_expiry_yields_no_probability():
    """Three events is not a sum of many things, and the theorem does not apply
    to it however carefully the arithmetic is done."""
    o = odds(days_left=60, adi=Decimal("21"))       # ~3 events
    assert o.probability is None
    assert o.basis == "too_few_events"
    assert "central limit theorem" in o.explanation


def test_enough_events_over_a_long_horizon_is_fine():
    o = odds(days_left=365, adi=Decimal("21"))      # ~17 events
    assert o.probability is not None
    assert o.basis == "observed"


def test_lumpy_demand_is_refused_outright():
    """Event sizes vary so much that even ten of them do not converge usefully.
    E6 declines to forecast these; declining a probability is the same call."""
    o = odds(demand_class="lumpy", adi=Decimal("1"))
    assert o.probability is None and o.basis == "unmodellable"
    assert "describes a different drug" in o.explanation


def test_no_measured_spread_means_no_probability():
    o = odds(stdev_daily=None)
    assert o.probability is None and o.basis == "no_spread"
    assert "confident number about nothing" in o.explanation


def test_no_measured_demand_leaves_it_to_the_deterministic_verdict():
    o = odds(avg_daily_demand=None)
    assert o.probability is None and o.basis == "no_demand"
    assert "E1's deterministic verdict stands" in o.explanation


def test_an_already_expired_lot_is_a_write_off_not_a_forecast():
    o = odds(days_left=0)
    assert o.probability is None
    assert "write-off, not a forecast" in o.explanation


def test_perfectly_regular_demand_gives_a_certain_answer_not_an_undefined_one():
    """Zero spread is degenerate but real, and dividing by it would be a crash
    where the answer is actually known."""
    sure = odds(units=Decimal("50"), days_left=180, stdev_daily=Decimal("0"))
    doomed = odds(units=Decimal("500"), days_left=180, stdev_daily=Decimal("0"))
    assert sure.probability == Decimal("0.000")
    assert doomed.probability == Decimal("1.000")


# ── the maths itself ──────────────────────────────────────────────────────
def test_the_normal_cdf_is_right_at_the_points_everyone_knows():
    assert abs(EP.normal_cdf(0.0) - 0.5) < 1e-9
    assert abs(EP.normal_cdf(1.6448536) - 0.95) < 1e-6
    assert abs(EP.normal_cdf(-1.6448536) - 0.05) < 1e-6


# ── what reaches a person ─────────────────────────────────────────────────
def test_a_lot_that_will_clear_needs_no_intervention():
    assert EP.worth_discounting(odds(units=Decimal("50"), days_left=180)) is False


def test_a_lot_at_real_risk_is_worth_discounting_while_a_customer_exists():
    assert EP.worth_discounting(odds(units=Decimal("400"), days_left=100)) is True


def test_a_refusal_is_not_a_reason_to_discount():
    """Acting on the absence of information is how an unmeasured item gets sold
    at a loss for no reason."""
    assert EP.worth_discounting(odds(stdev_daily=None)) is False
    assert EP.worth_discounting(odds(demand_class="lumpy")) is False


def test_an_expired_lot_is_not_labelled_as_an_observation():
    """Nothing was observed about its demand; it simply stopped being a
    forecasting question."""
    o = odds(days_left=0)
    assert o.basis == "already_expired"
    assert o.basis in EP.BASES
