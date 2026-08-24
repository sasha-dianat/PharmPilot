"""E12 — which supplier can be relied on, and the ways that question goes wrong.

Two failure modes dominate a supplier scorecard, and both produce a table that
looks authoritative:

  * the supplier with two orders and a spotless record topping the ranking, and
    the pharmacy moving its business to something it has not measured;
  * a *slow* supplier scored as an *unreliable* one, when eleven predictable
    days is a number the reorder point already handles and four-days-usually-but-
    sometimes-fifteen is the one nothing can be planned around.

Most of what is pinned here is about refusing to answer.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from services.core.inventory import supplier_reliability as SR

START = datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc)


def orders(days: list[int], supplier="acme"):
    return [{"wholesaler": supplier,
             "ordered_at": START + timedelta(days=7 * i),
             "received_at": START + timedelta(days=7 * i + d)}
            for i, d in enumerate(days)]


def lines(n, *, ordered=100, received=100, supplier="acme", ndc=None,
          status="complete"):
    return [{"wholesaler": supplier, "ndc11": ndc or f"N{i}",
             "quantity_ordered": Decimal(str(ordered)),
             "quantity_received": Decimal(str(received)),
             "status": status} for i in range(n)]


STEADY = [11] * 8               # slow, never varies
ERRATIC = [2, 2, 3, 15, 3, 14, 2, 16]


# ── the claim the engine is built on ──────────────────────────────────────
def test_slow_and_steady_beats_fast_and_erratic():
    """Eleven predictable days is already in the reorder point and the shelf is
    covered. Four-days-usually-but-sometimes-fifteen is the expensive one,
    because nothing can be planned around it."""
    slow = SR.score_supplier(orders(STEADY), lines(8), supplier="steady")
    fast = SR.score_supplier(orders(ERRATIC), lines(8), supplier="erratic")
    assert slow.lead_days > fast.lead_days          # it really is the slower one
    assert slow.score is not None and fast.score is not None
    assert slow.score > fast.score


def test_duration_alone_does_not_move_the_score():
    """Two suppliers, both perfectly predictable, one three times slower. The
    difference belongs in the reorder point, not in a reliability grade."""
    quick = SR.score_supplier(orders([3] * 8), lines(8), supplier="quick")
    slow = SR.score_supplier(orders([9] * 8), lines(8), supplier="slow")
    assert quick.score == slow.score
    assert quick.lead_days != slow.lead_days


# ── unmeasured must not look unblemished ──────────────────────────────────
def test_a_supplier_with_one_delivery_is_not_scored():
    """Its standard deviation is zero, which would read as perfect
    predictability and hand a brand-new supplier the top of the table."""
    s = SR.score_supplier(orders([4]), lines(8), supplier="new")
    assert s.score is None
    assert s.basis == "insufficient_history"
    assert s.consistency is None
    assert "one delivery has none" in s.explanation


def test_too_few_lines_withholds_the_score_even_with_good_lead_times():
    s = SR.score_supplier(orders([4] * 8), lines(3), supplier="thin")
    assert s.score is None
    assert "fill rate" in s.explanation


def test_an_unscored_supplier_sorts_last_not_first():
    """An unknown quantity at the top of a table gets read as a recommendation."""
    rows = (orders([4] * 8, supplier="known")
            + orders([1], supplier="unknown"))
    ls = lines(8, supplier="known") + lines(8, supplier="unknown")
    table = SR.rank(rows, ls)
    assert [s.supplier for s in table] == ["known", "unknown"]
    assert table[-1].score is None


def test_a_score_from_three_deliveries_is_shown_without_a_grade():
    """The number is real; the label is not earned. A grade outlives the caveat
    printed beside it."""
    s = SR.score_supplier(orders([5, 5, 6]), lines(8), supplier="young")
    assert s.score is not None
    assert s.basis == "provisional"
    assert s.grade is None


def test_a_long_record_earns_a_grade():
    s = SR.score_supplier(orders([5] * 8), lines(8), supplier="old")
    assert s.basis == "observed"
    assert s.grade == "dependable"


def test_metronomic_regularity_cannot_rescue_a_supplier_that_does_not_deliver():
    """Filling 40% of every order with perfect predictability scores 0.64 on the
    blend alone — "mixed", which is far too kind. Being reliably absent is not a
    virtue, so the fill rate caps the grade."""
    s = SR.score_supplier(orders([5] * 8),
                          lines(8, ordered=100, received=40), supplier="absent")
    assert s.consistency == Decimal("1.000")
    assert s.score > Decimal("0.60")
    assert s.grade == "poor"


# ── what the volume fill rate hides ───────────────────────────────────────
def test_frequent_short_lines_are_raised_even_when_the_volume_rate_is_high():
    """One large line filled in full offsets many small ones that were not — and
    the pharmacy is still chasing every one of them."""
    ls = (lines(1, ordered=10_000, received=10_000, ndc="BIG")
          + lines(5, ordered=100, received=40))
    s = SR.score_supplier(orders([5] * 8), ls, supplier="patchy")
    assert s.fill_rate is not None and s.fill_rate > Decimal("0.95")
    assert s.short_line_rate is not None and s.short_line_rate > SR.SHORT_LINE_CONCERN
    assert any("closed short" in c for c in s.concerns)


def test_an_erratic_lead_time_is_named_as_a_carried_cost():
    s = SR.score_supplier(orders(ERRATIC), lines(8), supplier="erratic")
    assert any("varies by" in c for c in s.concerns)


def test_over_delivery_does_not_lift_the_fill_rate_past_one():
    ls = lines(4) + lines(4, ordered=100, received=300, ndc="OVER", status="over")
    s = SR.score_supplier(orders([5] * 8), ls, supplier="generous")
    assert s.fill_rate == Decimal("1.000")


# ── absence of evidence ───────────────────────────────────────────────────
def test_zero_substitutions_from_a_supplier_with_history_means_none_seen():
    """Receiving can record a substitution now, so a zero against a supplier that
    has settled lines is an observation rather than a gap. It was `not_captured`
    for everyone while nothing could produce the status at all."""
    s = SR.score_supplier(orders([5] * 8), lines(8), supplier="acme")
    assert s.substitutions == 0
    assert s.substitution_basis == "none_seen"


def test_zero_substitutions_from_a_supplier_with_no_history_still_means_unmeasured():
    """The distinction is the whole point of carrying a basis."""
    s = SR.score_supplier([], [], supplier="stranger")
    assert s.substitution_basis == "not_captured"
    assert any("unmeasured rather than never" in c for c in s.concerns)


def test_a_substituted_line_does_not_count_towards_the_fill_rate():
    """What was ordered did not arrive. Counting the replacement would let a
    supplier who never once sent the right molecule score a perfect record."""
    ls = ([{"wholesaler": "acme", "ndc11": f"N{i}", "status": "substituted",
            "quantity_ordered": Decimal("100"),
            "quantity_received": Decimal("0")} for i in range(3)]
          + lines(5))
    s = SR.score_supplier(orders([5] * 8), ls, supplier="acme")
    assert s.substitutions == 3
    assert s.fill_rate is not None and s.fill_rate < Decimal("0.7")
    assert any("does not arrive" in c or "did not arrive" in c for c in s.concerns)


# ── keeping the date they gave, which is not being quick ──────────────────
def promised(days_late: list[int], supplier="acme"):
    """Delivered orders that carried a promised date."""
    return [{"wholesaler": supplier,
             "ordered_at": START + timedelta(days=7 * i),
             "expected_delivery": (START + timedelta(days=7 * i + 5)).date(),
             "received_at": START + timedelta(days=7 * i + 5 + d)}
            for i, d in enumerate(days_late)]


def test_a_supplier_that_misses_its_own_dates_is_named():
    s = SR.score_supplier(promised([0, 4, 0, 5, 3, 0]), lines(8), supplier="acme")
    assert s.on_time_rate is not None and s.on_time_rate < Decimal("0.9")
    assert s.on_time_basis == "observed"
    assert any("held to its promise, not to its average" in c for c in s.concerns)


def test_a_supplier_that_keeps_them_is_not():
    s = SR.score_supplier(promised([0, 0, 1, 0, 0, 1]), lines(8), supplier="acme")
    assert s.on_time_rate == Decimal("1.000")
    assert not any("misses the date" in c for c in s.concerns)


def test_slow_but_punctual_is_not_scored_as_unreliable():
    """Eleven promised days, delivered on the eleventh day, every time. The
    reorder point handles the eleven days; there is nothing else to report."""
    slow = [{"wholesaler": "acme", "ordered_at": START + timedelta(days=14 * i),
             "expected_delivery": (START + timedelta(days=14 * i + 11)).date(),
             "received_at": START + timedelta(days=14 * i + 11)} for i in range(6)]
    s = SR.score_supplier(slow, lines(8), supplier="acme")
    assert s.on_time_rate == Decimal("1.000")
    assert s.lead_days == 11


def test_no_promised_dates_is_not_a_perfect_record():
    """Nothing wrote `expected_delivery` until recently. Treating an absent
    promise as a kept one would have scored every supplier perfect on an empty
    column."""
    s = SR.score_supplier(orders([5] * 8), lines(8), supplier="acme")
    assert s.on_time_rate is None
    assert s.on_time_basis == "no_promises"


def test_a_recorded_substitution_is_counted_as_observed():
    ls = lines(7) + lines(1, ndc="SUB", status="substituted")
    s = SR.score_supplier(orders([5] * 8), ls, supplier="acme")
    assert (s.substitutions, s.substitution_basis) == (1, "observed")


# ── the blind spot a chronic short-shipper would otherwise hide in ────────
def stalled(n=6):
    """A supplier that always ships short, and nobody ever closed the orders."""
    return [{"wholesaler": "stalled", "ndc11": f"S{i}",
             "quantity_ordered": Decimal("100"),
             "quantity_received": Decimal("40"), "status": "partial"}
            for i in range(n)]


def test_a_chronic_short_shipper_has_no_lead_time_at_all():
    """Its orders never complete, so `received_at` is never stamped. Left alone
    this is worse than a wrong score: the worst supplier on the roster looks
    exactly like one that has never delivered."""
    s = SR.score_supplier([], stalled(), supplier="stalled")
    assert s.lead_basis == "declared_default"
    assert s.score is None


def test_and_it_is_raised_anyway_rather_than_waiting_for_a_score():
    s = SR.score_supplier([], stalled(), supplier="stalled")
    assert SR.worth_raising(s) is True
    assert any("never closed out" in c for c in s.concerns)


def test_one_delivery_still_in_flight_is_not_a_stalled_supplier():
    """Nagging about every open order is how a queue gets ignored."""
    ls = lines(8) + [{"wholesaler": "acme", "ndc11": "INFLIGHT",
                      "quantity_ordered": Decimal("100"),
                      "quantity_received": Decimal("50"), "status": "partial"}]
    s = SR.score_supplier(orders([5] * 8), ls, supplier="acme")
    assert s.outstanding_lines == 1
    assert not any("never closed out" in c for c in s.concerns)
    assert SR.worth_raising(s) is False


def test_closing_those_orders_is_what_makes_the_supplier_measurable():
    """The same six deliveries, once someone has recorded that the rest is not
    coming: now they settle, the orders complete, and the score appears."""
    settled = [{**l, "status": "backordered"} for l in stalled()]
    s = SR.score_supplier(orders([5] * 6), settled, supplier="stalled")
    assert s.score is not None
    assert s.fill_rate == Decimal("0.400")
    assert s.grade == "poor"


# ── head to head ──────────────────────────────────────────────────────────
def shared(name, days, *, received=100):
    """Two suppliers over the same four products."""
    ls = [{"wholesaler": name, "ndc11": f"P{i}",
           "quantity_ordered": Decimal("100"),
           "quantity_received": Decimal(str(received)), "status": "complete"}
          for i in range(6)]
    return SR.score_supplier(orders(days, supplier=name), ls, supplier=name)


def test_suppliers_who_sell_different_things_are_not_compared():
    """The one carrying the scarce items always looks worse, and the difference
    measures the catalogue rather than the supplier."""
    a = SR.score_supplier(orders([4] * 8), lines(8, supplier="a"), supplier="a")
    b = SR.score_supplier(orders([9] * 8),
                          [{**l, "ndc11": f"Z{i}"} for i, l in
                           enumerate(lines(8, supplier="b"))], supplier="b")
    c = SR.comparable(a, b)
    assert c.verdict == "not_comparable"
    assert "catalogues" in c.explanation


def test_a_small_difference_is_reported_as_too_close_to_call():
    a = shared("a", [4] * 8)
    b = shared("b", [4, 4, 4, 4, 4, 4, 4, 5])
    c = SR.comparable(a, b)
    assert c.verdict == "too_close"
    assert "inside the noise" in c.explanation


def test_a_real_difference_names_the_winner():
    a = shared("a", [4] * 8)
    b = shared("b", ERRATIC)
    c = SR.comparable(a, b)
    assert c.verdict == "a"
    assert c.shared_products == 6


def test_an_unmeasured_supplier_cannot_be_compared():
    a = shared("a", [4] * 8)
    b = shared("b", [4])
    assert SR.comparable(a, b).verdict == "not_comparable"


# ── what reaches a person ─────────────────────────────────────────────────
def test_a_new_supplier_is_not_filed_as_a_finding():
    """Every supplier starts with no history. Filing that would bury the ones
    that have earned a look."""
    assert SR.worth_raising(SR.score_supplier(orders([4]), lines(8),
                                              supplier="new")) is False


def test_a_dependable_supplier_needs_no_decision():
    assert SR.worth_raising(shared("a", [4] * 8)) is False


def test_a_poor_record_is_raised():
    poor = SR.score_supplier(orders(ERRATIC),
                             lines(8, ordered=100, received=40),
                             supplier="poor")
    assert poor.grade in ("mixed", "poor")
    assert SR.worth_raising(poor) is True


# ── one definition of "short", across three modules ───────────────────────
def test_a_rounding_gap_is_not_a_short_line_here_either():
    """`receiving` calls 999 of 1000 an EXACT delivery, deliberately: whole packs
    do not always split evenly and scoring it as a failure makes every reliable
    supplier look unreliable. This module compared exactly, so the most reliable
    supplier on the roster scored 100% short lines and got a concern raised
    against it — the same failure, one module over."""
    from services.core.inventory import receiving as RCV
    line = {"quantity_ordered": Decimal("1000"), "quantity_received": Decimal("999")}
    assert RCV.apply_receipt({**line, "quantity_received": Decimal("0"),
                              "id": "L", "ndc11": "N", "status": "ordered"},
                             999).shape == "exact"

    ls = [{"wholesaler": "acme", "ndc11": f"N{i}", "status": "complete", **line}
          for i in range(8)]
    s = SR.score_supplier(orders([5] * 8), ls, supplier="acme")
    assert s.short_lines == 0
    assert not any("closed short" in c for c in s.concerns)


def test_a_real_shortfall_is_still_counted():
    ls = [{"wholesaler": "acme", "ndc11": f"N{i}", "status": "complete",
           "quantity_ordered": Decimal("1000"),
           "quantity_received": Decimal("900")} for i in range(8)]
    assert SR.score_supplier(orders([5] * 8), ls, supplier="acme").short_lines == 8
