"""The Exception Register's rules: identity, granularity, ranking, lifecycle.

The property the whole register rests on is idempotence — a scheduled run that
opens duplicates every time is worse than no register at all, because the board
becomes noise and people stop reading it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.core.inventory import exceptions as X
from services.core.inventory import reconciliation as R

NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)


# ── fingerprint: identity that survives the next run ──────────────────────
def test_the_same_problem_fingerprints_the_same_twice():
    a = X.fingerprint("expired_on_hand", "lot", "CEF250-OLD")
    b = X.fingerprint("expired_on_hand", "lot", "CEF250-OLD")
    assert a == b


def test_quantity_and_time_are_not_part_of_identity():
    """A lot that was 60 units expired yesterday and 60 today is one problem
    seen twice, not two problems."""
    rows = [{"lot_number": "L1", "irc": "1", "quantity": 60, "days_expired": 1},
            {"lot_number": "L1", "irc": "1", "quantity": 55, "days_expired": 2}]
    fps = {X.fingerprint("expired_on_hand", *X.entity_of("expired_on_hand", r))
           for r in rows}
    assert len(fps) == 1


def test_different_entities_fingerprint_differently():
    assert (X.fingerprint("expired_on_hand", "lot", "A")
            != X.fingerprint("expired_on_hand", "lot", "B"))


def test_the_same_entity_under_different_checks_is_a_different_problem():
    assert (X.fingerprint("expired_on_hand", "lot", "A")
            != X.fingerprint("negative_stock", "lot", "A"))


# ── granularity: actionable things vs systemic situations ─────────────────
def test_a_lot_level_check_produces_one_exception_per_lot():
    f = R.check_expired_on_hand([
        {"lot_number": "A", "expiry_date": "2026-07-01", "quantity_on_hand": 60,
         "irc": "1", "value": 117000},
        {"lot_number": "B", "expiry_date": "2026-07-06", "quantity_on_hand": 300,
         "irc": "2", "value": 12600}], as_of=NOW.date())
    cands = X.candidates_from([f])
    assert len(cands) == 2
    assert {c.entity_type for c in cands} == {"lot"}


def test_a_systemic_check_produces_one_exception_however_many_rows():
    """46 legacy fills are one situation. Splitting them into 46 alerts buries
    the two expired lots that need a pharmacist this morning."""
    fills = [{"id": f"f{i}", "ndc_dispensed": "N", "quantity_dispensed": 30}
             for i in range(46)]
    f = R.check_fills_without_movements(fills, movements_by_fill=set())
    cands = X.candidates_from([f])
    assert len(cands) == 1
    assert cands[0].row_count == 46          # all of them, not samples[:10]
    assert cands[0].entity_type == "check"


def test_a_check_with_no_rows_produces_nothing():
    """The absence of a problem is not a problem with zero rows."""
    assert X.candidates_from([R.check_negative_stock([])]) == []


def test_every_affected_row_is_carried_not_a_preview():
    rows = [{"irc": "1", "quantity_on_hand": -1} for _ in range(30)]
    f = R.check_negative_stock(rows)
    total = sum(c.row_count for c in X.candidates_from([f]))
    assert total == 30


# ── scoring: ranking that can be interrogated ─────────────────────────────
def test_score_returns_its_own_arithmetic():
    imp = X.score(check="expired_on_hand", severity="critical",
                  financial_impact=129_000)
    assert 0 < imp.score <= 100
    assert "urgency" in imp.explanation and "safety" in imp.explanation


def test_a_controlled_substance_outranks_a_larger_ordinary_finding():
    """The signal is the discrepancy existing at all, not its size."""
    controlled = X.score(check="suspicious_adjustment", severity="high",
                         financial_impact=500, is_controlled=True)
    ordinary = X.score(check="unbound_from_formulary", severity="high",
                       financial_impact=5_000_000)
    assert controlled.score > ordinary.score


def test_patient_safety_outranks_a_data_defect_of_equal_severity():
    expired = X.score(check="expired_on_hand", severity="critical")
    drift = X.score(check="aggregate_drift", severity="critical")
    assert expired.score > drift.score


def test_uncertainty_multiplies_rather_than_nudges():
    sure = X.score(check="expired_on_hand", severity="critical", confidence=1.0)
    unsure = X.score(check="expired_on_hand", severity="critical", confidence=0.5)
    assert unsure.score == pytest.approx(sure.score * 0.5, rel=1e-6)


def test_financial_impact_saturates_instead_of_dominating():
    """Otherwise one large write-off permanently outranks every safety finding."""
    small = X.score(check="aggregate_drift", severity="medium", financial_impact=1_000)
    huge = X.score(check="aggregate_drift", severity="medium",
                   financial_impact=5_000_000_000)
    assert huge.score > small.score
    assert huge.financial <= 1.0


def test_an_unresolved_finding_climbs_but_never_runs_away():
    fresh = X.score(check="expired_on_hand", severity="critical", age_days=0)
    old = X.score(check="expired_on_hand", severity="critical", age_days=400,
                  occurrences=50)
    assert old.score > fresh.score
    assert old.age_multiplier <= 1.5


def test_an_unknown_severity_is_rejected_not_defaulted():
    with pytest.raises(X.ExceptionError):
        X.score(check="expired_on_hand", severity="catastrophic")


# ── diff: the idempotence the schedule depends on ─────────────────────────
def _cand(fp="fp1", check="expired_on_hand", severity="critical"):
    return X.Candidate(fingerprint=fp, check=check, severity=severity,
                       entity_type="lot", entity_key="L1", title_fa="t",
                       detail="d", remediation="r", rows=[{"lot_number": "L1"}])


def test_a_first_run_opens_everything():
    out = X.diff([_cand()], existing=[])
    assert out.summary() == {"opened": 1, "recurred": 0, "unchanged": 0, "resolved": 0}


def test_running_twice_with_no_change_opens_nothing():
    """The property that makes a scheduled run safe."""
    existing = [{"fingerprint": "fp1", "status": X.OPEN}]
    out = X.diff([_cand()], existing)
    assert out.summary() == {"opened": 0, "recurred": 1, "unchanged": 0, "resolved": 0}


def test_a_finding_that_stops_firing_resolves_itself():
    """The operator should not have to close what the pharmacy already fixed."""
    existing = [{"fingerprint": "fp1", "status": X.OPEN}]
    out = X.diff([], existing)
    assert len(out.resolved) == 1 and out.summary()["opened"] == 0


def test_an_accepted_finding_is_not_re_raised():
    """Re-opening it every night would undo the ruling."""
    existing = [{"fingerprint": "fp1", "status": X.ACCEPTED}]
    out = X.diff([_cand()], existing)
    assert out.summary()["recurred"] == 0 and out.summary()["unchanged"] == 1


def test_a_suppressed_finding_is_neither_re_raised_nor_auto_resolved():
    """Suppression hides a finding deliberately; auto-closing it would make the
    suppression silently permanent."""
    existing = [{"fingerprint": "fp1", "status": X.SUPPRESSED}]
    assert X.diff([_cand()], existing).summary()["unchanged"] == 1
    assert X.diff([], existing).resolved == []


def test_a_previously_resolved_problem_that_returns_is_opened_again():
    existing = [{"fingerprint": "fp1", "status": X.RESOLVED}]
    out = X.diff([_cand()], existing)
    assert out.summary()["opened"] == 1


# ── lifecycle ─────────────────────────────────────────────────────────────
def test_ordinary_transitions_are_allowed():
    for target in (X.ASSIGNED, X.RESOLVED):
        assert X.check_transition(X.OPEN, target) is None


def test_ruling_a_finding_tolerable_demands_a_reason():
    """Without one it is indistinguishable later from someone clearing their queue."""
    for target in (X.ACCEPTED, X.SUPPRESSED):
        with pytest.raises(X.ExceptionError, match="reason"):
            X.check_transition(X.OPEN, target)
        assert X.check_transition(X.OPEN, target, reason="known legacy batch") is None


def test_a_resolved_exception_cannot_be_hand_edited_back_into_the_queue():
    """Only a re-run reopens: the register must reflect what the checks say."""
    with pytest.raises(X.ExceptionError):
        X.check_transition(X.RESOLVED, X.ASSIGNED)
    assert X.check_transition(X.RESOLVED, X.OPEN) is None


def test_unknown_statuses_are_rejected():
    with pytest.raises(X.ExceptionError):
        X.check_transition(X.OPEN, "ignored_forever")
    with pytest.raises(X.ExceptionError):
        X.check_transition("mystery", X.OPEN)


def test_age_is_measured_from_first_sighting():
    assert X.age_days(NOW - timedelta(days=3), now=NOW) == pytest.approx(3.0)
    assert X.age_days(NOW + timedelta(days=1), now=NOW) == 0.0   # never negative


# ── the real report shape ─────────────────────────────────────────────────
def test_a_whole_report_converts_without_special_casing():
    findings = [
        R.check_expired_on_hand([{"lot_number": "A", "expiry_date": "2026-07-01",
                                  "quantity_on_hand": 60, "value": 117000}],
                                as_of=NOW.date()),
        R.check_fills_without_movements([{"id": "f1"}], movements_by_fill=set()),
        R.check_negative_stock([]),
        R.check_chain({"intact": True, "verified": 10}),
    ]
    cands = X.candidates_from(findings)
    checks = {c.check for c in cands}
    assert checks == {"expired_on_hand", "fill_without_movement"}
    expired = next(c for c in cands if c.check == "expired_on_hand")
    assert expired.financial_impact == 117000.0
    ranked = sorted(cands, key=lambda c: -X.score(
        check=c.check, severity=c.severity,
        financial_impact=c.financial_impact).score)
    assert ranked[0].check == "expired_on_hand"    # patient safety leads
