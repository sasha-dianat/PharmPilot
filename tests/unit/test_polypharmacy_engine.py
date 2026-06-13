from services.ai.clinical_decision_support.normalizer import classes_of, normalize
from services.ai.polypharmacy.engine import review
from services.ai.polypharmacy.schema import PolyContext, PolyMedication


def med(name: str, indication: str | None = "documented", status: str = "active") -> PolyMedication:
    normalized = normalize(name)
    return PolyMedication(
        drug_name=name,
        normalized_name=normalized,
        classes=classes_of(normalized),
        indication=indication,
        status=status,
    )


def finding(result, category: str):
    return next(item for item in result.findings if item.category == category)


def test_high_anticholinergic_burden_lists_contributors():
    result = review(PolyContext(medications=[
        med("diphenhydramine"),
        med("oxybutynin"),
        med("amitriptyline"),
    ]))

    assert result.anticholinergic_burden.score >= 6
    alert = finding(result, "anticholinergic_burden")
    assert alert.priority == "high"
    assert "diphenhydramine" in " ".join(alert.drugs_involved).lower()
    assert "oxybutynin" in " ".join(alert.drugs_involved).lower()
    assert "amitriptyline" in " ".join(alert.drugs_involved).lower()


def test_anticholinergic_burden_with_strength_suffixes():
    # Real input carries strengths (e.g. "diphenhydramine 25mg"); normalization
    # must strip them so the ACB table still matches the generic name.
    result = review(PolyContext(medications=[
        med("diphenhydramine 25mg"),
        med("oxybutynin 5mg"),
        med("amitriptyline 10mg tablet"),
    ]))

    assert result.anticholinergic_burden.score >= 6
    assert finding(result, "anticholinergic_burden").priority == "high"


def test_sedative_fall_risk_burden():
    result = review(PolyContext(medications=[
        med("lorazepam"),
        med("zolpidem"),
        med("oxycodone"),
    ]))

    alert = finding(result, "sedative_fall_risk_burden")
    assert alert.priority == "high"
    assert set(alert.drugs_involved) == {"lorazepam", "zolpidem", "oxycodone"}


def test_duplicate_therapy_two_ssris():
    result = review(PolyContext(medications=[
        med("sertraline"),
        med("fluoxetine"),
    ]))

    alert = finding(result, "therapeutic_duplication")
    assert alert.priority == "moderate"
    assert set(alert.drugs_involved) == {"sertraline", "fluoxetine"}


def test_prescribing_cascade_amlodipine_furosemide():
    result = review(PolyContext(medications=[
        med("amlodipine"),
        med("furosemide"),
    ]))

    alert = finding(result, "prescribing_cascade")
    assert "peripheral edema" in alert.explanation
    assert set(alert.drugs_involved) == {"amlodipine", "furosemide"}


def test_bleeding_combo_nsaid_anticoagulant_high_priority():
    result = review(PolyContext(medications=[
        med("ibuprofen"),
        med("warfarin"),
    ]))

    alert = finding(result, "bleeding_risk_combination")
    assert alert.priority == "high"
    assert set(alert.drugs_involved) == {"ibuprofen", "warfarin"}


def test_pim_age_gating_and_unknown_age_missing_information():
    elderly = review(PolyContext(age=80, medications=[med("diazepam")]))
    assert finding(elderly, "potentially_inappropriate_medication").priority == "high"

    younger = review(PolyContext(age=50, medications=[med("diazepam")]))
    assert not [item for item in younger.findings if item.category == "potentially_inappropriate_medication"]

    unknown = review(PolyContext(medications=[med("diazepam")]))
    assert not [item for item in unknown.findings if item.category == "potentially_inappropriate_medication"]
    assert "patient age/date of birth" in " ".join(unknown.missing_information)


def test_taper_safety_wording_for_benzodiazepine():
    result = review(PolyContext(age=80, medications=[med("diazepam")]))
    alert = finding(result, "potentially_inappropriate_medication")

    assert alert.tapering_caution
    assert "discuss a taper plan with the prescriber" in alert.tapering_caution
    discussion = alert.suggested_pharmacist_discussion.lower()
    assert "stop" not in discussion
    assert "discontinue" not in discussion


def test_underprescribing_atrial_fibrillation_anticoagulant():
    result = review(PolyContext(
        conditions=["atrial_fibrillation"],
        medications=[med("metoprolol", indication="rate control")],
    ))

    alert = finding(result, "possible_underprescribing")
    assert alert.priority == "low"
    assert "considering anticoagulation" in alert.explanation
    assert "prescriber" in alert.suggested_pharmacist_discussion


def test_clean_documented_two_drug_list_no_high_or_moderate_findings():
    result = review(PolyContext(
        age=50,
        conditions=["hypertension", "hyperlipidemia"],
        medications=[
            med("lisinopril", indication="hypertension"),
            med("atorvastatin", indication="hyperlipidemia"),
        ],
    ))

    assert [item for item in result.findings if item.priority in {"high", "moderate"}] == []
