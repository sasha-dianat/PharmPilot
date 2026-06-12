from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from services.ai.clinical_decision_support.normalizer import classes_of, normalize
from services.ai.lab_safety.engine import assess
from services.ai.lab_safety.schema import LabSafetyContext
from services.platform.routers import lab_safety
from shared.models.clinical import ClinicalAuditLog


PHARMACY_ID = uuid4()
STAFF_ID = uuid4()


def med(name: str) -> dict:
    normalized = normalize(name)
    return {
        "drug_name": name,
        "normalized_name": normalized,
        "classes": sorted(classes_of(normalized)),
    }


def lab(test_name: str, value: str, unit: str | None = None, days_old: int = 5) -> dict:
    return {
        "test_name": test_name,
        "value": value,
        "unit": unit,
        "result_date": datetime.now(timezone.utc) - timedelta(days=days_old),
    }


def run(meds: list[dict], labs: list[dict]) -> LabSafetyContext:
    return LabSafetyContext(patient_id="patient-1", medications=meds, lab_results=labs)


def test_warfarin_supratherapeutic():
    result = assess(run([med("warfarin")], [lab("INR", "4.5")]))

    finding = next(item for item in result.findings if item.rule_id == "warfarin_inr")
    assert finding.severity == "high"
    assert finding.lab_name == "inr"


def test_warfarin_critical():
    result = assess(run([med("warfarin")], [lab("INR", "6.2")]))

    finding = next(item for item in result.findings if item.rule_id == "warfarin_inr")
    assert finding.severity == "critical"


def test_warfarin_subtherapeutic():
    result = assess(run([med("warfarin")], [lab("INR", "1.4")]))

    finding = next(item for item in result.findings if item.rule_id == "warfarin_inr")
    assert finding.severity == "moderate"


def test_warfarin_missing_inr():
    result = assess(run([med("warfarin")], []))

    assert any(item.lab_name == "inr" and item.drug == "warfarin" for item in result.missing_labs)


def test_acei_hyperkalemia_high():
    result = assess(run([med("lisinopril")], [lab("K+", "5.8", "mEq/L")]))

    finding = next(item for item in result.findings if item.rule_id == "acei_arb_hyperkalemia")
    assert finding.severity == "high"


def test_acei_hyperkalemia_critical():
    result = assess(run([med("enalapril")], [lab("serum potassium", "6.2", "mEq/L")]))

    finding = next(item for item in result.findings if item.rule_id == "acei_arb_hyperkalemia")
    assert finding.severity == "critical"


def test_metformin_egfr_contraindicated():
    result = assess(run([med("metformin")], [lab("eGFR", "22", "mL/min/1.73m2")]))

    finding = next(item for item in result.findings if item.rule_id == "metformin_renal")
    assert finding.severity == "high"


def test_metformin_egfr_caution():
    result = assess(run([med("metformin")], [lab("estimated GFR", "38", "mL/min/1.73m2")]))

    finding = next(item for item in result.findings if item.rule_id == "metformin_renal")
    assert finding.severity == "moderate"


def test_metformin_creatinine_does_not_match_egfr_requirement():
    result = assess(run([med("metformin")], [lab("Creatinine", "1.1", "mg/dL")]))

    assert not [item for item in result.findings if item.rule_id == "metformin_renal"]
    assert any(
        item.drug == "metformin"
        and item.lab_name == "egfr"
        and item.reason == "no result on record"
        for item in result.missing_labs
    )


def test_amiodarone_less_than_tsh_threshold_triggers_suppressed_tsh():
    result = assess(run([med("amiodarone")], [lab("TSH", "<0.1", "mIU/L")]))

    finding = next(item for item in result.findings if item.rule_id == "amiodarone_thyroid_liver")
    assert finding.severity == "high"
    assert finding.lab_name == "tsh"
    assert finding.lab_value == "<0.1"


def test_electrolytes_panel_value_does_not_match_potassium():
    result = assess(run([med("lisinopril")], [lab("Electrolytes", "140")]))

    assert not [item for item in result.findings if item.rule_id == "acei_arb_hyperkalemia"]


def test_clozapine_anc_critical():
    result = assess(run([med("clozapine")], [lab("ANC", "800", "/uL")]))

    finding = next(item for item in result.findings if item.rule_id == "clozapine_anc")
    assert finding.severity == "critical"


def test_clozapine_anc_high():
    result = assess(run([med("clozapine")], [lab("absolute neutrophil count", "1200", "/uL")]))

    finding = next(item for item in result.findings if item.rule_id == "clozapine_anc")
    assert finding.severity == "high"


def test_ssri_hyponatremia():
    result = assess(run([med("sertraline")], [lab("serum sodium", "128", "mEq/L")]))

    finding = next(item for item in result.findings if item.rule_id == "ssri_snri_hyponatremia")
    assert finding.severity == "high"


def test_statin_ck_rhabdo():
    result = assess(run([med("atorvastatin")], [lab("CK", "12000", "U/L")]))

    finding = next(item for item in result.findings if item.rule_id == "statin_myopathy")
    assert finding.severity == "critical"


def test_loop_diuretic_hypokalemia():
    result = assess(run([med("furosemide")], [lab("potassium", "2.8", "mEq/L")]))

    finding = next(item for item in result.findings if item.rule_id == "loop_diuretic_electrolytes")
    assert finding.severity == "high"


def test_no_matching_drugs():
    result = assess(run([med("amoxicillin")], [lab("INR", "6.2")]))

    assert result.findings == []
    assert result.missing_labs == []


def test_stale_lab_ignored():
    result = assess(run([med("warfarin")], [lab("INR", "4.5", days_old=120)]))

    assert not [item for item in result.findings if item.rule_id == "warfarin_inr"]
    assert any(item.lab_name == "inr" and item.reason == "no result in last 90 days" for item in result.missing_labs)


def test_multiple_findings():
    result = assess(run(
        [med("warfarin"), med("lisinopril")],
        [lab("INR", "4.5"), lab("serum potassium", "5.9", "mEq/L")],
    ))

    rule_ids = {item.rule_id for item in result.findings}
    assert {"warfarin_inr", "acei_arb_hyperkalemia"} <= rule_ids
    severities = [item.severity for item in result.findings]
    assert severities == sorted(severities, key={"critical": 0, "high": 1, "moderate": 2, "low": 3}.get)


class ResultStub:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._row

    def scalars(self):
        result = MagicMock()
        result.all.return_value = self._rows
        return result


class StubDb:
    def __init__(self, results: list[ResultStub]):
        self.results = list(results)
        self.added = []
        self.flushed = False

    async def execute(self, query):
        return self.results.pop(0)

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        self.flushed = True


class StaffStub:
    def __init__(self):
        self.id = STAFF_ID
        self.pharmacy_id = PHARMACY_ID
        self.role = "pharmacist"

    def has_permission(self, permission: str) -> bool:
        return permission == "clinical:read"


def patient(patient_id):
    return SimpleNamespace(id=patient_id, pharmacy_id=PHARMACY_ID, is_deleted=False)


@pytest.mark.asyncio
async def test_endpoint_unknown_patient():
    patient_id = uuid4()
    db = StubDb([ResultStub(row=None)])

    with pytest.raises(HTTPException) as exc:
        await lab_safety.assess_lab_safety(
            lab_safety.LabSafetyAssessRequest(patient_id=patient_id),
            staff=StaffStub(),
            db=db,
        )

    assert exc.value.status_code == 404
    assert db.added == []


@pytest.mark.asyncio
async def test_endpoint_no_labs():
    patient_id = uuid4()
    db = StubDb([
        ResultStub(row=patient(patient_id)),
        ResultStub(rows=[]),
        ResultStub(rows=[]),
    ])

    body = await lab_safety.assess_lab_safety(
        lab_safety.LabSafetyAssessRequest(patient_id=patient_id),
        staff=StaffStub(),
        db=db,
    )

    assert body["patient_id"] == str(patient_id)
    assert body["findings"] == []
    assert body["missing_labs"] == []
    assert db.flushed is True
    audits = [row for row in db.added if isinstance(row, ClinicalAuditLog)]
    assert len(audits) == 1
    assert audits[0].module == "lab_safety"
