"""
Clinical-validation tests for the review-by-exception triage engine.
The CRITICAL invariant: GREEN can never bypass a safety gate.
"""
import itertools

import pytest

from services.core.pharmacy_workflow.triage import (
    ReviewTriageEngine, TriageInput, ReviewLane,
)


def _eng():
    return ReviewTriageEngine()


# ── Happy path: stable chronic refill → GREEN ─────────────────────────────────

def test_stable_chronic_refill_is_green():
    t = TriageInput(
        drug_name="Amlodipine 5mg", is_controlled=False, is_first_fill=False,
        is_refill=True, is_stable_chronic=True, known_patient=True,
        dose_in_range=True, claim_status="approved",
    )
    r = _eng().classify(t)
    assert r.lane == ReviewLane.GREEN
    assert r.one_tap_confirm is True
    assert r.requires_visual_verification is True   # still mandatory


# ── Hard gates can NEVER be green ─────────────────────────────────────────────

def test_controlled_substance_never_green():
    t = TriageInput(drug_name="Oxycodone 5mg", is_controlled=True, dea_schedule="CII",
                    is_refill=True, is_stable_chronic=True, known_patient=True,
                    dose_in_range=True, claim_status="approved")
    r = _eng().classify(t)
    assert r.lane == ReviewLane.RED
    assert "controlled_substance" in r.hard_gates


def test_hard_stop_dur_never_green():
    t = TriageInput(drug_name="Amlodipine", is_refill=True, is_stable_chronic=True,
                    known_patient=True, claim_status="approved",
                    dur_alerts=[{"severity": "critical", "is_hard_stop": True, "was_overridden": False}])
    r = _eng().classify(t)
    assert r.lane == ReviewLane.RED
    assert "unresolved_hard_stop_dur" in r.hard_gates


def test_council_blocker_never_green():
    t = TriageInput(drug_name="Nitrofurantoin", is_refill=True, is_stable_chronic=True,
                    known_patient=True, claim_status="approved",
                    council_findings=[{"severity": "blocker"}])
    r = _eng().classify(t)
    assert r.lane == ReviewLane.RED
    assert "council_blocker" in r.hard_gates


def test_pdmp_high_risk_never_green():
    t = TriageInput(drug_name="Tramadol", is_refill=True, is_stable_chronic=True,
                    known_patient=True, claim_status="approved", pdmp_risk="high")
    r = _eng().classify(t)
    assert r.lane == ReviewLane.RED


def test_first_fill_high_alert_never_green():
    t = TriageInput(drug_name="Warfarin 5mg", is_first_fill=True, is_refill=False,
                    known_patient=True, claim_status="approved")
    r = _eng().classify(t)
    assert r.lane == ReviewLane.RED
    assert "first_fill_high_alert" in r.hard_gates


# ── Amber cases ───────────────────────────────────────────────────────────────

def test_unconfirmed_identity_is_amber():
    t = TriageInput(drug_name="Amlodipine", is_refill=True, is_stable_chronic=True,
                    known_patient=False, claim_status="approved")
    r = _eng().classify(t)
    assert r.lane == ReviewLane.AMBER


def test_rejected_claim_is_amber():
    t = TriageInput(drug_name="Amlodipine", is_refill=True, is_stable_chronic=True,
                    known_patient=True, claim_status="rejected")
    r = _eng().classify(t)
    assert r.lane == ReviewLane.AMBER


# ── INVARIANT: exhaustive — green ⇒ no hard gate, ever ────────────────────────

def test_green_never_coexists_with_any_hard_gate():
    eng = _eng()
    bools = [True, False]
    pdmp_opts = [None, "low", "moderate", "high", "critical"]
    claim_opts = ["approved", "paid", "cash_pay", "rejected", None]
    checked = 0
    for (ctrl, first, chronic, known, dose) in itertools.product(bools, repeat=5):
        for pdmp in pdmp_opts:
            for claim in claim_opts:
                for hardstop in bools:
                    for blocker in bools:
                        t = TriageInput(
                            drug_name="Warfarin" if first else "Amlodipine",
                            is_controlled=ctrl, dea_schedule="CII" if ctrl else None,
                            is_first_fill=first, is_refill=not first,
                            is_stable_chronic=chronic, known_patient=known,
                            dose_in_range=dose, pdmp_risk=pdmp, claim_status=claim,
                            dur_alerts=[{"severity": "critical", "is_hard_stop": True,
                                         "was_overridden": False}] if hardstop else [],
                            council_findings=[{"severity": "blocker"}] if blocker else [],
                        )
                        r = eng.classify(t)
                        checked += 1
                        if r.lane == ReviewLane.GREEN:
                            assert r.hard_gates == [], f"GREEN with gates: {r.hard_gates}"
                            assert not ctrl and not hardstop and not blocker
                            assert pdmp not in ("high", "critical")
    assert checked > 1000  # exhaustive coverage actually ran
