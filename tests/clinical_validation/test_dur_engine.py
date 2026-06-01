"""
Clinical Validation Tests — DUR Engine
All specialists use (self, rx: dict, patient: dict) -> list[SpecialistFinding]
"""
import pytest
from services.ai.clinical_brain.council.specialist_council import (
    CardiologySpecialist, NephrologySpecialist,
    GeriatricsPediatricsSpecialist, PainManagementSpecialist,
)
from services.ai.clinical_brain.specialties.drug_safety import DrugSafetyModule


def rx(**kw):
    base = {"drug_name": "TestDrug 10mg", "quantity_prescribed": 30, "days_supply": 30,
            "dea_schedule": None, "strength_mg": 10}
    base.update(kw); return base

def pt(**kw):
    base = {"age": 50, "active_medications": [], "diagnoses": [], "allergies": [], "egfr": None}
    base.update(kw); return base


class TestAllergyChecks:
    def test_exact_allergy_critical(self):
        ds = DrugSafetyModule()
        findings = ds.check_interactions(
            new_drug={"drug_name": "Amoxicillin 500mg", "ndc": "12345678901"},
            active_medications=[],
            allergies=[{"allergen_name": "amoxicillin", "reaction": "anaphylaxis", "severity": "severe"}])
        assert any(f.get("severity") == "critical" for f in findings), "Amoxicillin allergy must be critical"

    def test_no_false_positive_unrelated(self):
        ds = DrugSafetyModule()
        findings = ds.check_interactions(
            new_drug={"drug_name": "Atorvastatin 40mg", "ndc": "12345678902"},
            active_medications=[],
            allergies=[{"allergen_name": "lisinopril", "severity": "mild"}])
        assert not any(f.get("severity") == "critical" for f in findings), "Unrelated allergy must not fire"


class TestOpioidBenzoBlackBox:
    def test_opioid_benzo_critical(self):
        ds = DrugSafetyModule()
        findings = ds.check_interactions(
            new_drug={"drug_name": "Oxycodone 10mg", "ndc": "00555097202"},
            active_medications=[{"drug_name": "Alprazolam 1mg"}],
            allergies=[])
        assert any(f.get("severity") == "critical" for f in findings), "Opioid+benzo must be critical"

    def test_opioid_alone_no_critical_ddi(self):
        ds = DrugSafetyModule()
        findings = ds.check_interactions(
            new_drug={"drug_name": "Oxycodone 10mg", "ndc": "00555097202"},
            active_medications=[{"drug_name": "Lisinopril 10mg"}],
            allergies=[])
        interaction_criticals = [f for f in findings if f.get("type") == "interaction" and f.get("severity") == "critical"]
        assert len(interaction_criticals) == 0, "Opioid+lisinopril should not fire opioid+benzo DDI"

    def test_high_dose_opioid_pain_finding(self):
        pain = PainManagementSpecialist()
        findings = pain.evaluate(
            rx=rx(drug_name="Oxycodone 30mg", quantity_prescribed=120,
                  days_supply=30, dea_schedule="CII", strength_mg=30),
            patient=pt(age=45))
        assert len(findings) >= 1, "High-dose opioid must produce a finding"
        assert any("MME" in f.message.upper() or "opioid" in f.message.lower() for f in findings)

    def test_opioid_benzo_blocker_from_pain_specialist(self):
        pain = PainManagementSpecialist()
        findings = pain.evaluate(
            rx=rx(drug_name="Oxycodone 10mg", dea_schedule="CII", strength_mg=10,
                  quantity_prescribed=30, days_supply=30),
            patient=pt(age=45, active_medications=[{"drug_name": "alprazolam"}]))
        blockers = [f for f in findings if f.severity in ("blocker", "critical")]
        assert len(blockers) >= 1, "Opioid+alprazolam must trigger a blocker"


class TestRenalDosing:
    def test_metformin_contraindicated_egfr_28(self):
        n = NephrologySpecialist()
        findings = n.evaluate(rx=rx(drug_name="Metformin 1000mg"), patient=pt(age=70, egfr=28.0))
        assert len(findings) >= 1, "Metformin+eGFR28 must produce finding"
        blockers = [f for f in findings if f.severity in ("blocker", "critical")]
        assert len(blockers) >= 1, "Must be a blocker, not just caution"

    def test_metformin_caution_egfr_38(self):
        n = NephrologySpecialist()
        findings = n.evaluate(rx=rx(drug_name="Metformin 500mg"), patient=pt(age=65, egfr=38.0))
        assert len(findings) >= 1, "Metformin+eGFR38 must produce a caution"

    def test_metformin_safe_egfr_75(self):
        n = NephrologySpecialist()
        findings = n.evaluate(rx=rx(drug_name="Metformin 500mg"), patient=pt(age=55, egfr=75.0))
        metformin_findings = [f for f in findings if "metformin" in f.message.lower()]
        assert len(metformin_findings) == 0, "Normal eGFR must not trigger metformin renal alert"

    def test_gabapentin_egfr_45_adjustment(self):
        n = NephrologySpecialist()
        findings = n.evaluate(rx=rx(drug_name="Gabapentin 600mg"), patient=pt(age=65, egfr=45.0))
        assert len(findings) >= 1, "Gabapentin+eGFR45 needs dose adjustment"

    def test_no_egfr_monitoring_prompt(self):
        n = NephrologySpecialist()
        findings = n.evaluate(rx=rx(drug_name="Gabapentin 600mg"), patient=pt(age=70, egfr=None))
        assert len(findings) >= 1, "Missing eGFR for renally-cleared drug should prompt monitoring"


class TestBeersCriteria:
    def test_diazepam_fires_age_70(self):
        g = GeriatricsPediatricsSpecialist()
        findings = g.evaluate(rx=rx(drug_name="Diazepam 5mg"), patient=pt(age=70))
        assert len(findings) >= 1, "Diazepam in age 70 must trigger Beers alert"
        msg = " ".join(f.message.lower() for f in findings)
        assert any(kw in msg for kw in ("beers", "fall", "cognitive", "elderly", "age"))

    def test_diphenhydramine_fires_age_68(self):
        g = GeriatricsPediatricsSpecialist()
        findings = g.evaluate(rx=rx(drug_name="Diphenhydramine 25mg"), patient=pt(age=68))
        assert len(findings) >= 1, "Diphenhydramine in age 68 must trigger Beers alert"

    def test_beers_not_for_age_35(self):
        g = GeriatricsPediatricsSpecialist()
        findings = g.evaluate(rx=rx(drug_name="Diazepam 5mg"), patient=pt(age=35))
        beers = [f for f in findings if "beers" in f.message.lower()]
        assert len(beers) == 0, "Beers must not fire for age 35"

    def test_beers_fires_exactly_at_65(self):
        g = GeriatricsPediatricsSpecialist()
        findings = g.evaluate(rx=rx(drug_name="Diazepam 5mg"), patient=pt(age=65))
        assert len(findings) >= 1, "Beers must fire at age exactly 65"


class TestQTcProlongation:
    def test_azithromycin_amiodarone_critical(self):
        c = CardiologySpecialist()
        findings = c.evaluate(
            rx=rx(drug_name="Azithromycin 500mg"),
            patient=pt(age=60, active_medications=[{"drug_name": "amiodarone"}]))
        critical = [f for f in findings if f.severity in ("blocker", "critical")]
        assert len(critical) >= 1, "Azithromycin+amiodarone must be critical"

    def test_azithromycin_alone_no_critical(self):
        c = CardiologySpecialist()
        findings = c.evaluate(
            rx=rx(drug_name="Azithromycin 500mg"),
            patient=pt(age=40, active_medications=[{"drug_name": "lisinopril"}]))
        critical = [f for f in findings if f.severity == "critical"]
        assert len(critical) == 0, "Azithromycin alone must not fire critical QTc alert"
