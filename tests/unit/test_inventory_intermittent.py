"""E6 — two items with the same rate and nothing else in common.

    Item A:  2 units a day, most days.            2.0/day
    Item B:  40 units once every three weeks.     1.9/day

A reorder point of "about twelve units" serves the first perfectly and is useless
for the second, where demand arrives as one event wanting forty at once. The rate
cannot tell them apart; that is what this module is for.

The quadrant that matters most is `lumpy`, because the correct output there is an
admission: rare events of wildly varying size cannot be forecast well by any
method, and a confident number for one is a claim about accuracy nobody can
support.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from services.core.inventory import intermittent as IM

TODAY = date(2026, 8, 17)
WINDOW = 84


def fills(pattern: dict[int, float]) -> list[dict]:
    """{days_ago: units} → fill rows."""
    return [{"fill_date": TODAY - timedelta(days=d), "quantity": Decimal(str(u))}
            for d, u in pattern.items()]


def steady(qty=2, every=1, window=WINDOW):
    """Spanning the WHOLE window on purpose. A series that starts a third of the
    way in is not steady — it is an item that began selling, and the drift
    detector is right to say so."""
    return fills({i * every: qty for i in range(window // every)})


def bursty(qty=40, every=21, n=4):
    return fills({i * every: qty for i in range(n)})


def go(f, window=WINDOW):
    return IM.assess("N1", f, window_days=window, as_of=TODAY)


# ── the case the whole module exists for ──────────────────────────────────
def test_two_items_with_the_same_rate_are_not_the_same_item():
    a = go(steady(qty=2, every=1))
    b = go(bursty(qty=40, every=21, n=4))
    assert abs(float(a.mean_rate) - float(b.mean_rate)) < 0.6   # same-ish rate
    assert a.demand_class == "smooth"
    assert b.demand_class in ("intermittent", "lumpy")
    assert a.forecastable is True and b.forecastable is False


def test_a_bursty_item_reports_the_size_of_one_event():
    """"Hold enough to serve one event" is a rule that works where a rate does
    not."""
    b = go(bursty(qty=40, every=21, n=4))
    assert b.typical_event == Decimal("40.000")
    assert IM.cover_floor(b) == Decimal("40.000")
    assert "not 1.905/day" in " ".join(b.concerns) or "One event is" in " ".join(b.concerns)


def test_a_smooth_item_gets_no_cover_floor():
    """The rate already describes it, and a floor would only inflate holdings."""
    assert IM.cover_floor(go(steady())) is None


# ── the four quadrants ────────────────────────────────────────────────────
def test_daily_and_even_is_smooth():
    assert go(steady(qty=5, every=1)).demand_class == "smooth"


def test_daily_but_wildly_uneven_is_erratic():
    sizes = [1, 50, 2, 80, 3, 60, 1, 70] * 8
    assert go(fills({i: sizes[i] for i in range(64)})).demand_class == "erratic"


def test_regular_bursts_of_a_similar_size_are_intermittent():
    assert go(fills({i * 7: 30 for i in range(11)})).demand_class == "intermittent"


def test_rare_and_wildly_uneven_is_lumpy():
    assert go(fills({0: 5, 14: 200, 33: 12, 55: 340, 76: 8})).demand_class == "lumpy"


def test_lumpy_is_reported_as_unforecastable_rather_than_given_a_number():
    p = go(fills({0: 5, 14: 200, 33: 12, 55: 340, 76: 8}))
    assert p.forecastable is False
    assert any("no method forecasts this well" in c for c in p.concerns)
    assert p.mean_rate is not None          # the rate is still reported
    assert IM.cover_floor(p) is not None    # and so is the rule that works


def test_the_classification_boundaries_are_the_published_ones():
    """Inventing house cut-offs would make the classification unfalsifiable
    against the scheme it comes from."""
    assert IM.classify(IM.ADI_CUT - Decimal("0.01"), Decimal("0.1")) == "smooth"
    assert IM.classify(IM.ADI_CUT, Decimal("0.1")) == "intermittent"
    assert IM.classify(Decimal("1.0"), IM.CV2_CUT) == "erratic"
    assert IM.classify(IM.ADI_CUT, IM.CV2_CUT) == "lumpy"
    assert IM.classify(None, None) == "unknown"


# ── the zeros are the distribution ────────────────────────────────────────
def test_the_series_keeps_the_days_nothing_moved():
    """A series built only from the days something moved describes a different
    drug entirely."""
    s = IM.daily_series(fills({0: 10, 5: 10}), window_days=10, as_of=TODAY)
    assert len(s) == 10
    assert sum(1 for v in s if v == 0) == 8


def test_fills_outside_the_window_are_not_counted_at_the_boundary():
    s = IM.daily_series(fills({0: 10, 200: 999}), window_days=30, as_of=TODAY)
    assert sum(s, Decimal("0")) == Decimal("10.000")


def test_an_undated_fill_cannot_be_placed_in_time_and_is_dropped():
    s = IM.daily_series([{"quantity": 50}], window_days=30, as_of=TODAY)
    assert sum(s, Decimal("0")) == Decimal("0")


def test_the_spread_is_measured_over_every_day_not_only_active_ones():
    """Averaging only the active days would understate safety stock badly for an
    intermittent drug."""
    p = go(bursty(qty=40, every=21, n=4))
    assert p.stdev_daily is not None and p.stdev_daily > Decimal("5")


# ── refusing to classify ──────────────────────────────────────────────────
def test_nothing_dispensed_yields_no_shape_and_no_rate():
    p = go([])
    assert (p.basis, p.demand_class) == ("no_history", "unknown")
    assert p.mean_rate is None
    assert "no rate assumed" in p.explanation


def test_two_events_are_not_a_pattern():
    """One interval is not a distribution of intervals."""
    p = go(fills({0: 10, 20: 10}))
    assert p.basis == "sparse"
    assert p.demand_class == "unknown"
    assert p.adi is None and p.cv2 is None
    assert any("an interval needs two" in c for c in p.concerns)


def test_a_sparse_item_still_reports_what_one_event_looks_like():
    """It is the only useful thing available from two deliveries, and it is a
    measurement rather than an extrapolation."""
    p = go(fills({0: 10, 20: 30}))
    assert p.typical_event == Decimal("30.000")
    assert p.mean_rate is not None


# ── the recency-weighted rate, and what it is for ─────────────────────────
def test_croston_needs_intervals():
    assert IM.croston([Decimal("5")] + [Decimal("0")] * 30) is None


def test_a_steady_series_gives_croston_and_the_mean_the_same_answer():
    p = go(steady(qty=4, every=2))
    assert p.sba_rate is not None
    assert abs(float(p.sba_rate) - float(p.mean_rate)) < 0.6


def test_a_rate_that_is_climbing_is_flagged_as_drifting():
    """The window average is smearing across a change, and the difference between
    the two estimates is the finding."""
    quiet = {70 - i * 7: 2 for i in range(6)}       # small, long ago
    recent = {12 - i * 3: 60 for i in range(5)}     # large, lately
    p = go(fills({**quiet, **recent}))
    assert p.drifting is True
    assert any("the rate is moving" in c for c in p.concerns)


def test_a_stable_rate_is_not_flagged_as_drifting():
    assert go(steady(qty=3, every=2)).drifting is False


def test_the_bias_correction_pulls_croston_down():
    """The ratio of two smoothed quantities is not the smoothed ratio, and plain
    Croston runs high."""
    s = IM.daily_series(bursty(qty=30, every=10, n=8), window_days=WINDOW,
                        as_of=TODAY)
    plain = IM.croston(s, bias_corrected=False)
    corrected = IM.croston(s)
    assert plain is not None and corrected is not None
    assert corrected < plain


# ── what reaches a person ─────────────────────────────────────────────────
def test_a_smooth_item_needs_nobody():
    assert IM.worth_raising(go(steady())) is False


def test_an_unclassifiable_item_is_not_filed():
    assert IM.worth_raising(go(fills({0: 10, 20: 10}))) is False


def test_a_lumpy_item_is_raised_because_the_planning_rule_is_wrong_for_it():
    assert IM.worth_raising(go(fills({0: 5, 14: 200, 33: 12, 55: 340, 76: 8}))) is True


def test_a_drifting_item_is_raised():
    quiet = {70 - i * 7: 2 for i in range(6)}
    recent = {12 - i * 3: 60 for i in range(5)}
    assert IM.worth_raising(go(fills({**quiet, **recent}))) is True
