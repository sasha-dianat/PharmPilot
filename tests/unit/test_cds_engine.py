from services.ai.clinical_decision_support.engine import evaluate
from services.ai.clinical_decision_support.normalizer import classes_of, normalize
from services.ai.clinical_decision_support.rules import EGFR_MISSING, POTASSIUM_MISSING
from services.ai.clinical_decision_support.schema import CDSContext, CDSMedication, LabValue


def med(name: str) -> CDSMedication:
    normalized = normalize(name)
    return CDSMedication(name, normalized, classes_of(normalized))


def assert_complete(alert: dict) -> None:
    assert alert["mechanism"]
    assert alert["suggested_pharmacist_actions"]
    assert alert["evidence_sources"]
    assert 0 < alert["confidence"] <= 1
    assert alert["pharmacist_verification_notice"]


def find(alerts: list[dict], rule_id: str) -> dict:
    return next(alert for alert in alerts if alert["rule_id"] == rule_id)


def test_clarithromycin_simvastatin_critical_rule():
    alerts = evaluate(CDSContext(medications=[med("Biaxin"), med("Zocor")]))

    alert = find(alerts, "clarithromycin_simvastatin")
    assert alert["severity"] == "CRITICAL"
    assert_complete(alert)


def test_acei_spironolactone_potassium_severity_and_missing_data():
    critical = evaluate(CDSContext(
        medications=[med("lisinopril"), med("spironolactone")],
        labs={"serum potassium": LabValue(5.8, "mmol/L")},
    ))
    assert find(critical, "acei_spironolactone_potassium")["severity"] == "CRITICAL"
    assert_complete(find(critical, "acei_spironolactone_potassium"))

    high = evaluate(CDSContext(
        medications=[med("lisinopril"), med("spironolactone")],
        labs={"serum potassium": LabValue(5.1, "mmol/L")},
    ))
    assert find(high, "acei_spironolactone_potassium")["severity"] == "HIGH"

    missing = evaluate(CDSContext(medications=[med("lisinopril"), med("spironolactone")]))
    alert = find(missing, "acei_spironolactone_potassium")
    assert alert["severity"] == "MODERATE"
    assert POTASSIUM_MISSING in alert["missing_information"]


def test_nsaid_anticoagulant_high_rule():
    alerts = evaluate(CDSContext(medications=[med("ibuprofen"), med("warfarin")]))

    alert = find(alerts, "nsaid_anticoagulant")
    assert alert["severity"] == "HIGH"
    assert_complete(alert)


def test_metformin_low_egfr_severity_and_missing_data():
    critical = evaluate(CDSContext(
        medications=[med("metformin")],
        labs={"egfr": LabValue(25, "mL/min/1.73m2")},
    ))
    assert find(critical, "metformin_low_egfr")["severity"] == "CRITICAL"
    assert_complete(find(critical, "metformin_low_egfr"))

    high = evaluate(CDSContext(
        medications=[med("metformin")],
        labs={"egfr": LabValue(40, "mL/min/1.73m2")},
    ))
    assert find(high, "metformin_low_egfr")["severity"] == "HIGH"

    missing = evaluate(CDSContext(medications=[med("metformin")]))
    alert = find(missing, "metformin_low_egfr")
    assert alert["severity"] == "MODERATE"
    assert EGFR_MISSING in alert["missing_information"]


def test_benzodiazepine_in_elderly_and_unknown_age():
    elderly = evaluate(CDSContext(age=80, medications=[med("diazepam")]))

    alert = find(elderly, "benzodiazepine_elderly")
    assert alert["severity"] == "HIGH"
    assert_complete(alert)

    unknown = evaluate(CDSContext(medications=[med("diazepam")]))
    unknown_alert = find(unknown, "benzodiazepine_elderly")
    assert unknown_alert["severity"] == "INFO"
    assert "patient age/date of birth" in " ".join(unknown_alert["missing_information"])


def test_ssri_tramadol_high_rule():
    alerts = evaluate(CDSContext(medications=[med("sertraline"), med("tramadol")]))

    alert = find(alerts, "ssri_tramadol")
    assert alert["severity"] == "HIGH"
    assert_complete(alert)


def test_unrelated_medications_do_not_trigger_alerts():
    alerts = evaluate(CDSContext(medications=[med("amoxicillin"), med("loratadine")]))

    assert alerts == []
