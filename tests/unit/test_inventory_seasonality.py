"""E7 — one cold season is an anecdote.

A system that mistakes an unusual month for an annual pattern is worth less than
nothing: it buys for a season that never comes, and the stock expires on the
shelf. So most of what is pinned here is the refusal — two complete cycles before
any seasonal claim, a strength statistic that has to beat the noise, and a month
observed once whose index is withheld rather than quoted.

The Jalali bucketing is the other half. Nowruz is 1 Farvardin every year and
drifts across 20-21 March, so a Gregorian March bucket splits the new-year peak
in two and halves it.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from services.core.inventory import seasonality as SE
from services.core.localization.jalali import jalali_to_gregorian

TODAY = date(2026, 8, 18)          # 27 Mordad 1405


def on(jy: int, jm: int, jd: int, units: float) -> dict:
    """A fill on a given Jalali date."""
    g = jalali_to_gregorian(jy, jm, jd)
    return {"fill_date": date(*g), "quantity": Decimal(str(units))}


def year(jy: int, per_month: dict[int, float], day: int = 15) -> list[dict]:
    return [on(jy, m, day, u) for m, u in per_month.items()]


FLAT = {m: 100.0 for m in range(1, 13)}
# Cold remedies: Azar (9), Dey (10), Bahman (11) are the Iranian winter.
WINTER = {**{m: 40.0 for m in range(1, 13)}, 9: 200.0, 10: 260.0, 11: 180.0}


def go(fills):
    return SE.assess("N1", fills, as_of=TODAY)


# ── the refusal that matters most ─────────────────────────────────────────
def test_one_cycle_is_never_enough_for_a_seasonal_claim():
    """With one turn of the year there is no second observation of any month, so
    a real peak and one unusual month are the same data."""
    s = go(year(1404, WINTER))
    assert s.verdict == "insufficient_cycles"
    assert s.strength is None and s.peak is None
    assert "are the same data" in s.explanation


def test_two_cycles_of_a_real_pattern_are_enough():
    s = go(year(1403, WINTER) + year(1404, WINTER))
    assert s.verdict == "seasonal"
    assert s.cycles >= 2
    assert s.peak is not None and s.peak.name == "Dey"
    assert s.trough is not None


def test_noise_across_two_cycles_is_reported_as_no_pattern():
    """Not a flat line with a shape drawn on it."""
    noisy = {m: v for m, v in zip(range(1, 13),
                                  [90, 110, 95, 105, 88, 112, 97, 103, 91, 109, 94, 106])}
    other = {m: v for m, v in zip(range(1, 13),
                                  [108, 92, 106, 94, 111, 89, 104, 96, 110, 90, 107, 93])}
    s = go(year(1403, noisy) + year(1404, other))
    assert s.verdict == "no_detectable_seasonality"
    assert s.strength is not None and s.strength < SE.STRENGTH_FLOOR
    assert "the rest is noise" in s.explanation


def test_a_perfectly_flat_item_claims_nothing():
    s = go(year(1403, FLAT) + year(1404, FLAT))
    assert s.verdict == "no_detectable_seasonality"


def test_nothing_dispensed_asserts_nothing():
    s = go([])
    assert s.verdict == "no_history"
    assert "none is" in s.explanation


# ── the Jalali buckets ────────────────────────────────────────────────────
def test_nowruz_lands_in_one_month_not_split_across_two():
    """1 Farvardin is 21 March in one year and 20 March in another. A Gregorian
    March bucket splits the new-year peak; a Jalali one does not."""
    assert SE.jalali_month_of(date(2026, 3, 21))[1] == 1     # Farvardin
    assert SE.jalali_month_of(date(2025, 3, 21))[1] == 1
    assert SE.jalali_month_of(date(2026, 3, 20))[1] == 12    # still Esfand


def test_the_school_year_turns_in_mehr_not_in_september():
    assert SE.jalali_month_of(date(2026, 9, 23))[1] == 7     # 1 Mehr
    assert SE.jalali_month_of(date(2026, 9, 22))[1] == 6     # last of Shahrivar


def test_a_nowruz_peak_is_found_where_a_gregorian_model_would_halve_it():
    spring = {**{m: 30.0 for m in range(1, 13)}, 1: 300.0}
    s = go(year(1403, spring) + year(1404, spring))
    assert s.verdict == "seasonal"
    assert s.peak.name == "Farvardin"
    assert s.peak.index > Decimal("3")


# ── what is deliberately not captured ─────────────────────────────────────
def test_ramadan_is_declared_uncaptured_rather_than_silently_missing():
    """Lunar, so it moves against the Jalali calendar too — exactly the way
    Nowruz moves against the Gregorian one."""
    s = go(year(1403, WINTER) + year(1404, WINTER))
    assert any("Ramadan is lunar" in c for c in s.concerns)
    assert any("understates seasonality rather than inventing it" in c
               for c in s.concerns)


def test_a_month_seen_once_has_its_index_withheld():
    """Quoting a month's index from a single year is the one-cycle error, one
    month at a time."""
    fills = year(1403, WINTER) + year(1404, WINTER) + [on(1405, 5, 15, 500)]
    s = go(fills)
    mordad = next(m for m in s.months if m.month == 5)
    assert mordad.observations >= 2      # Mordad appears in all three
    sparse = go(year(1403, {m: 50.0 for m in range(1, 13)})
                + year(1404, {m: 50.0 for m in range(1, 12)}))
    esfand = next(m for m in sparse.months if m.month == 12)
    assert esfand.observations == 1
    assert esfand.index is None and esfand.basis == "thin"


# ── what a planner gets ───────────────────────────────────────────────────
def test_a_seasonal_item_offers_a_month_factor():
    s = go(year(1403, WINTER) + year(1404, WINTER))
    dey = SE.month_factor(s, 10)
    assert dey is not None and dey > Decimal("2")


def test_an_unseasonal_item_offers_none_so_the_flat_rate_is_used():
    """1.0 would be a claim; None is an absence, and a planner receiving None
    correctly falls back to the measured rate."""
    s = go(year(1403, FLAT) + year(1404, FLAT))
    assert SE.month_factor(s, 10) is None


def test_a_one_cycle_item_offers_no_factor_either():
    assert SE.month_factor(go(year(1404, WINTER)), 10) is None


def test_a_strong_pattern_is_worth_telling_somebody_about():
    assert SE.worth_raising(go(year(1403, WINTER) + year(1404, WINTER))) is True


def test_a_pattern_too_small_to_change_an_order_is_not_raised():
    mild = {**{m: 100.0 for m in range(1, 13)}, 10: 112.0, 4: 90.0}
    s = go(year(1403, mild) + year(1404, mild))
    assert SE.worth_raising(s) is False


def test_an_unmeasurable_item_is_never_raised():
    assert SE.worth_raising(go(year(1404, WINTER))) is False
    assert SE.worth_raising(go([])) is False
