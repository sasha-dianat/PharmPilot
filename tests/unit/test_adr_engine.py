from __future__ import annotations

from datetime import date

from services.ai.adr_detective.engine import assess
from services.ai.adr_detective.schema import ADRContext, ADRMedication, LabValue
from services.ai.clinical_decision_support.normalizer import classes_of, normalize


ONSET = date(2026, 6, 1)


def med(name: str, start_date: date | None = None, recent_dose_increase: bool = False) -> ADRMedication:
    normalized = normalize(name)
    return ADRMedication(
        drug_name=name,
        normalized_name=normalized,
        classes=classes_of(normalized),
        start_date=start_date,
        recent_dose_increase=recent_dose_increase,
    )


def first_for(causes, normalized_name: str):
    return next(cause for cause in causes if cause.normalized_name == normalized_name)


def test_ace_inhibitor_dry_cough_probable_with_temporal_signal():
    causes = assess(ADRContext(
        complaint="Persistent dry cough",
        onset_date=ONSET,
        medications=[med("lisinopril", date(2026, 5, 11))],
    ))

    cause = first_for(causes, "lisinopril")
    assert cause.reaction == "cough"
    assert cause.causality == "probable"
    assert "FDA label: lisinopril" in cause.evidence_sources


def test_statin_muscle_aches_probable_with_temporal_signal():
    causes = assess(ADRContext(
        complaint="muscle aches in both legs",
        onset_date=ONSET,
        medications=[med("atorvastatin", date(2026, 5, 1))],
    ))

    cause = first_for(causes, "atorvastatin")
    assert cause.reaction == "myalgia"
    assert cause.causality == "probable"


def test_amlodipine_ankle_swelling_probable_with_temporal_signal():
    causes = assess(ADRContext(
        complaint="ankle swelling",
        onset_date=ONSET,
        medications=[med("amlodipine", date(2026, 5, 5))],
    ))

    cause = first_for(causes, "amlodipine")
    assert cause.reaction == "peripheral_edema"
    assert cause.causality == "probable"


def test_ssri_hyponatremia_probable_with_low_sodium():
    causes = assess(ADRContext(
        complaint="dizziness and nausea",
        onset_date=ONSET,
        medications=[med("sertraline")],
        labs={"serum sodium": LabValue(128, "mmol/L")},
    ))

    cause = first_for(causes, "sertraline")
    assert cause.reaction == "hyponatremia"
    assert cause.causality == "probable"
    assert cause.urgency == "high"


def test_anticholinergic_confusion_in_elderly_is_detected_conservatively():
    causes = assess(ADRContext(
        complaint="new confusion and memory problems",
        medications=[med("diphenhydramine")],
        age=81,
    ))

    cause = first_for(causes, "diphenhydramine")
    assert cause.reaction == "confusion"
    assert cause.causality == "possible"
    assert cause.seriousness == "serious"


def test_nsaid_anticoagulant_black_stools_serious_high_urgency():
    causes = assess(ADRContext(
        complaint="black stools and bruising",
        medications=[med("ibuprofen"), med("warfarin")],
    ))

    ibuprofen = first_for(causes, "ibuprofen")
    warfarin = first_for(causes, "warfarin")
    assert ibuprofen.reaction == "bleeding"
    assert warfarin.reaction == "bleeding"
    assert ibuprofen.causality == "possible"
    assert warfarin.causality == "possible"
    assert ibuprofen.seriousness == "serious"
    assert ibuprofen.urgency == "high"
    assert warfarin.alternative_explanations


def test_known_adr_without_temporal_lab_or_dose_signal_stays_possible():
    causes = assess(ADRContext(
        complaint="dry cough",
        medications=[med("lisinopril")],
    ))

    assert first_for(causes, "lisinopril").causality == "possible"


def test_unrecognized_complaint_returns_unclear_missing_information():
    causes = assess(ADRContext(
        complaint="patient feels strange",
        medications=[med("lisinopril")],
    ))

    assert len(causes) == 1
    assert causes[0].causality == "unclear"
    assert causes[0].reaction == "unclear"
    assert "specific symptom not recognized" in causes[0].missing_information[0]
