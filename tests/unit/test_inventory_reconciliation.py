"""Reconciliation checks — each test is the counterexample the check exists to
catch, plus proof it stays quiet on clean data (a check that always fires is
noise, and noise is what gets reports switched off).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from services.core.inventory import reconciliation as R

TODAY = date(2026, 8, 2)
NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


# ── C1 aggregate drift ────────────────────────────────────────────────────
def test_aggregate_matching_its_lots_is_silent():
    assert R.check_aggregate_drift([{"irc": "1", "aggregate": 10, "lot_sum": 10}]).count == 0


def test_aggregate_drift_is_critical_and_reports_the_gap():
    f = R.check_aggregate_drift([{"irc": "1", "aggregate": 10, "lot_sum": 7}])
    assert (f.count, f.severity) == (1, "critical")
    assert f.samples[0]["drift"] == 3.0


def test_tiny_float_noise_is_not_a_drift():
    """Reporting 1e-9 as a discrepancy would bury the real ones."""
    assert R.check_aggregate_drift([{"irc": "1", "aggregate": 10.0004, "lot_sum": 10}]).count == 0


# ── C2 negative stock ─────────────────────────────────────────────────────
def test_negative_stock_is_critical():
    f = R.check_negative_stock([{"irc": "1", "quantity_on_hand": -2},
                                {"irc": "2", "quantity_on_hand": 5}])
    assert (f.count, f.severity) == (1, "critical")


# ── C3/C4 dispensing ──────────────────────────────────────────────────────
def test_fill_without_a_movement_is_caught():
    fills = [{"id": "f1", "ndc_dispensed": "N1", "quantity_dispensed": 30},
             {"id": "f2", "ndc_dispensed": "N2", "quantity_dispensed": 10}]
    f = R.check_fills_without_movements(fills, movements_by_fill={"f2"})
    assert f.count == 1 and f.samples[0]["fill_id"] == "f1"
    assert f.severity == "critical"


def test_a_fill_naming_no_lot_cannot_be_recalled():
    fills = [{"id": "f1", "lot_number": None, "inventory_lot_id": None},
             {"id": "f2", "inventory_lot_id": "l9"}]
    f = R.check_untraceable_fills(fills)
    assert f.count == 1 and f.samples[0]["fill_id"] == "f1"


# ── C5 formulary binding ──────────────────────────────────────────────────
def test_stock_not_bound_to_the_formulary_is_reported():
    f = R.check_formulary_binding([{"ndc11": "N1", "irc": None, "quantity_on_hand": 5},
                                   {"ndc11": "N2", "irc": "123"}])
    assert f.count == 1 and f.samples[0]["ndc11"] == "N1"


# ── C6 expiry ─────────────────────────────────────────────────────────────
def test_expired_sellable_stock_is_critical():
    lots = [{"lot_number": "A", "expiry_date": TODAY - timedelta(days=10),
             "quantity_on_hand": 5},
            {"lot_number": "B", "expiry_date": TODAY + timedelta(days=10),
             "quantity_on_hand": 5}]
    f = R.check_expired_on_hand(lots, as_of=TODAY)
    assert (f.count, f.severity) == (1, "critical")
    assert f.samples[0]["days_expired"] == 10


def test_quarantined_expired_stock_is_already_handled():
    """It is off the shelf. Reporting it again trains people to ignore the check."""
    lots = [{"lot_number": "A", "expiry_date": TODAY - timedelta(days=10),
             "quantity_on_hand": 5, "is_quarantined": True}]
    assert R.check_expired_on_hand(lots, as_of=TODAY).count == 0


def test_expiry_accepts_iso_strings_from_json_payloads():
    lots = [{"lot_number": "A", "expiry_date": "2026-07-01", "quantity_on_hand": 1}]
    assert R.check_expired_on_hand(lots, as_of=TODAY).count == 1


# ── C7 suspicious adjustments ─────────────────────────────────────────────
def _mv(i, actor, pct_delta, before=100, controlled=False, mtype="ADJUSTMENT"):
    return {"id": f"m{i}", "created_by": actor, "irc": "IRC1", "movement_type": mtype,
            "quantity_before": before, "quantity_delta": -before * pct_delta / 100,
            "is_controlled": controlled, "reason": "count fix", "created_at": NOW}


def test_a_small_one_off_correction_is_not_suspicious():
    assert R.check_suspicious_adjustments([_mv(1, "s1", 2)], now=NOW).count == 0


def test_a_large_write_down_is_flagged():
    f = R.check_suspicious_adjustments([_mv(1, "s1", 40)], now=NOW)
    assert f.count == 1 and f.samples[0]["pattern"] == "large_write_down"


def test_any_controlled_write_down_is_flagged_however_small():
    f = R.check_suspicious_adjustments([_mv(1, "s1", 1, controlled=True)], now=NOW)
    assert f.count == 1 and f.samples[0]["pattern"] == "controlled_write_down"


def test_repeat_write_downs_by_one_person_on_one_item_form_a_pattern():
    ms = [_mv(i, "s1", 3) for i in range(3)]
    patterns = [s["pattern"] for s in R.check_suspicious_adjustments(ms, now=NOW).samples]
    assert "repeat_write_down" in patterns


def test_the_same_write_downs_spread_across_people_are_not_a_pattern():
    ms = [_mv(i, f"s{i}", 3) for i in range(3)]
    assert R.check_suspicious_adjustments(ms, now=NOW).count == 0


def test_old_movements_fall_outside_the_window():
    old = _mv(1, "s1", 90)
    old["created_at"] = NOW - timedelta(days=90)
    assert R.check_suspicious_adjustments([old], window_days=30, now=NOW).count == 0


def test_receipts_are_never_suspicious_write_downs():
    m = _mv(1, "s1", 40)
    m["quantity_delta"] = 40          # a gain
    assert R.check_suspicious_adjustments([m], now=NOW).count == 0


# ── C8/C9/C10 ─────────────────────────────────────────────────────────────
def test_duplicate_lot_numbers_are_reported():
    lots = [{"irc": "1", "lot_number": "ab-1", "lot_id": "x"},
            {"irc": "1", "lot_number": "AB-1", "lot_id": "y"},   # case/space variant
            {"irc": "1", "lot_number": "OTHER", "lot_id": "z"}]
    f = R.check_duplicate_lots(lots)
    assert f.count == 1 and f.samples[0]["rows"] == 2


def test_blank_lot_numbers_are_not_duplicates_of_each_other():
    lots = [{"irc": "1", "lot_number": "", "lot_id": "x"},
            {"irc": "1", "lot_number": None, "lot_id": "y"}]
    assert R.check_duplicate_lots(lots).count == 0


def test_packs_entered_as_units_are_suspected():
    rows = [{"irc": "1", "quantity_on_hand": 3000, "package_count": 30,
             "avg_daily_demand": 0.5}]
    f = R.check_unit_conversion(rows)
    assert f.count == 1 and f.samples[0]["hypothesis"] == "packs recorded as units"


def test_a_genuinely_fast_moving_item_is_not_a_conversion_error():
    rows = [{"irc": "1", "quantity_on_hand": 3000, "package_count": 30,
             "avg_daily_demand": 40}]
    assert R.check_unit_conversion(rows).count == 0


def test_reserving_more_than_exists_is_high_severity():
    f = R.check_over_reservation([{"irc": "1", "quantity_on_hand": 5, "quantity_reserved": 9}])
    assert (f.count, f.severity) == (1, "high")


# ── C11 chain + summary ───────────────────────────────────────────────────
def test_intact_chain_reports_zero():
    assert R.check_chain({"intact": True, "verified": 12}).count == 0


def test_broken_chain_refuses_to_suggest_repair():
    f = R.check_chain({"intact": False, "break_index": 3, "detail": "edited"})
    assert f.count == 1 and f.severity == "critical"
    assert "Do not repair" in f.remediation


def test_a_single_critical_finding_makes_the_whole_report_unhealthy():
    findings = [R.check_negative_stock([{"quantity_on_hand": -1}]),
                R.check_duplicate_lots([])]
    s = R.summarize(findings)
    assert s["healthy"] is False and s["blocking"] is True and s["trustworthy"] is False


def test_clean_data_is_reported_healthy():
    findings = [R.check_negative_stock([]), R.check_duplicate_lots([]),
                R.check_chain({"intact": True, "verified": 0})]
    s = R.summarize(findings)
    assert s["healthy"] is True and s["trustworthy"] is True and s["checks_firing"] == 0


def test_findings_are_ordered_most_severe_first():
    findings = [R.check_duplicate_lots([{"irc": "1", "lot_number": "A", "lot_id": "x"},
                                        {"irc": "1", "lot_number": "A", "lot_id": "y"}]),
                R.check_negative_stock([{"quantity_on_hand": -1}])]
    order = [f["severity"] for f in R.summarize(findings)["findings"] if f["count"]]
    assert order == ["critical", "medium"]


def test_medium_findings_alone_do_not_block():
    s = R.summarize([R.check_duplicate_lots(
        [{"irc": "1", "lot_number": "A", "lot_id": "x"},
         {"irc": "1", "lot_number": "A", "lot_id": "y"}])])
    # medium-only: not blocking, still trustworthy for ordering decisions, but
    # not "healthy" — something is firing and the report must say so
    assert s["blocking"] is False and s["trustworthy"] is True and s["healthy"] is False


# ── C12 demand signal ─────────────────────────────────────────────────────
from decimal import Decimal as _Dec  # noqa: E402

from services.core.inventory import demand as _D  # noqa: E402


def _div(stored, units, *, ndc="A"):
    """Five fills spread backwards from TODAY, totalling `units`."""
    fills = [{"fill_date": date.fromordinal(TODAY.toordinal() - (i * 3 + 1)),
              "quantity_dispensed": units / 5} for i in range(5)] if units else []
    est = _D.estimate(ndc, fills, window_days=28, as_of=TODAY)
    return _D.divergence(ndc11=ndc, stored_adq=stored, observed=est)


def test_demand_signal_agreeing_everywhere_is_silent():
    f = R.check_demand_signal([_div(_Dec("5"), 140)])
    assert f.count == 0
    assert f.severity == "info"


def test_a_contradicted_demand_signal_is_high_severity():
    """14/day against a nil fill record means the number is not a measurement."""
    f = R.check_demand_signal([_div(_Dec("14"), 0)])
    assert (f.count, f.severity) == (1, "high")
    assert "contradicted by a nil fill record" in f.detail


def test_an_understated_signal_is_high_because_it_hides_stockout_risk():
    f = R.check_demand_signal([_div(_Dec("4"), 360)])
    assert (f.count, f.severity) == (1, "high")


def test_only_overstated_signals_stay_medium():
    f = R.check_demand_signal([_div(_Dec("20"), 100)])
    assert (f.count, f.severity) == (1, "medium")


def test_a_stale_signal_is_reported_even_when_the_values_agree():
    f = R.check_demand_signal([_div(_Dec("5"), 140)],
                              stale=[{"ndc11": "A", "age_days": 54,
                                      "detail": "54 days old"}])
    assert f.count == 1
    assert f.severity == "medium"
    assert "stale or never computed" in f.detail
    assert f.samples[0]["verdict"] == "stale"


def test_the_demand_check_is_in_the_registry():
    """A check absent from ALL_CHECKS is invisible to the register's diff and
    would never open, recur, or resolve as an exception."""
    assert "demand_signal_unsupported" in R.ALL_CHECKS


def test_stock_held_with_no_demand_rate_is_reported_not_silent():
    """After a refresh legitimately finds no history the item goes quiet, but
    the capital is still on the shelf and can neither be reordered on evidence
    nor retired as dead stock."""
    f = R.check_demand_signal(
        [], blind=[{"ndc11": "A", "on_hand": 90.0, "basis": "no_history",
                    "detail": "stock held with no measurable demand"}])
    assert (f.count, f.severity) == (1, "medium")
    assert "no demand rate to reorder or retire it on" in f.detail
    assert f.samples[0]["verdict"] == "no_signal"


def test_a_wrong_signal_outranks_a_missing_one():
    """Both present: the high-severity disagreement must set the severity."""
    f = R.check_demand_signal(
        [_div(_Dec("14"), 0)],
        blind=[{"ndc11": "B", "on_hand": 10.0, "basis": "no_history"}])
    assert f.severity == "high"
    assert f.count == 2


def test_nothing_wrong_anywhere_stays_info():
    assert R.check_demand_signal([_div(_Dec("5"), 140)],
                                 stale=[], blind=[]).severity == "info"


# ── C13 reservation drift ─────────────────────────────────────────────────
def test_matching_reserved_counters_are_silent():
    assert R.check_reservation_drift([]).count == 0
    assert R.check_reservation_drift([]).severity == "info"


def test_a_counter_out_of_step_with_its_rows_is_high_severity():
    """available = on_hand - reserved, so a counter nobody claims quietly makes
    real stock unissuable."""
    f = R.check_reservation_drift(
        [{"lot_id": "L1", "counter": 10.0, "active_reservations": 0.0, "drift": 10.0}])
    assert (f.count, f.severity) == (1, "high")
    assert "disagree" in f.detail
    assert "Do not zero the counter by hand" in f.remediation


def test_a_lapsed_hold_still_withholding_stock_is_reported():
    f = R.check_reservation_drift([], [{"reservation_id": "r1", "quantity": 30.0}])
    assert (f.count, f.severity) == (1, "medium")
    assert "past their hold" in f.detail
    assert f.samples[0]["kind"] == "lapsed"


def test_the_reservation_check_is_in_the_registry():
    assert "reservation_drift" in R.ALL_CHECKS


# ── C5 binding: unresolved work vs a ruling nobody can automate ───────────
def test_an_unbound_row_with_no_ambiguity_is_unresolved_work():
    f = R.check_formulary_binding([{"ndc11": "N1", "irc": None, "quantity_on_hand": 5}])
    assert (f.count, f.severity) == (1, "high")
    assert "have not been resolved" in f.detail


def test_an_unbound_row_with_many_candidate_brands_is_not_work_it_is_a_ruling():
    """An IRC is a per-brand registration. Gabapentin 300mg capsules have 68 of
    them on the real formulary, so no matcher can pick one, and reporting it as
    outstanding work means the check never goes down however much is done."""
    f = R.check_formulary_binding(
        [{"ndc11": "N1", "irc": None, "quantity_on_hand": 5}], {"N1": 68})
    assert (f.count, f.severity) == (1, "medium")
    assert "awaiting a brand ruling" in f.detail
    assert f.samples[0]["candidates"] == 68
    assert "not a guess" in f.remediation


def test_unresolved_work_outranks_a_pending_ruling():
    f = R.check_formulary_binding(
        [{"ndc11": "A", "irc": None}, {"ndc11": "B", "irc": None}], {"B": 4})
    assert f.severity == "high"
    assert f.count == 2


def test_fully_bound_stock_is_silent():
    f = R.check_formulary_binding([{"ndc11": "A", "irc": "123"}])
    assert f.count == 0 and f.severity == "info"


def test_the_window_is_measured_from_an_injected_clock_not_the_wall_one():
    """These three checks passed for a month and then began failing because the
    calendar moved past their fixture data — which looks exactly like a
    regression and is not one. A rolling window read off the wall clock makes
    any test with a fixed date expire silently."""
    stale = _mv(1, "s1", 40)
    stale["created_at"] = NOW - timedelta(days=200)
    assert R.check_suspicious_adjustments([stale], now=NOW).count == 0
    # The same movement, judged from a clock that sits just after it.
    assert R.check_suspicious_adjustments(
        [stale], now=stale["created_at"] + timedelta(days=1)).count == 1
