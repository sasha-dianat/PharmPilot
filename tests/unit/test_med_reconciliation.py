from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from services.ai.clinical_decision_support.normalizer import normalize
from services.ai.med_reconciliation import engine
from services.ai.med_reconciliation.schema import MedEntry, ReconciliationContext
from services.platform.routers import med_reconciliation


PHARMACY_A = uuid4()
PHARMACY_B = uuid4()
STAFF_ID = uuid4()


def med(
    name: str,
    *,
    strength: str | None = None,
    dose: str | None = None,
    route: str | None = None,
    frequency: str | None = None,
    status: str | None = "active",
    source: str = "request",
) -> MedEntry:
    normalized = normalize(name)
    return MedEntry(
        drug_name=name,
        normalized_name=normalized,
        strength=strength,
        dose=dose,
        route=route,
        frequency=frequency,
        status=status,
        source=source,
        classes=sorted(engine.classes_of(normalized)),
    )


def reconcile(source_a: list[MedEntry], source_b: list[MedEntry]):
    return engine.reconcile(
        ReconciliationContext(
            patient_id="patient-1",
            source_a_label="Home Medications",
            source_b_label="Admission Medications",
            source_a_meds=source_a,
            source_b_meds=source_b,
        )
    )


def types(result):
    return [item.discrepancy_type for item in result.discrepancies]


def test_omitted_high_risk():
    result = reconcile([med("warfarin")], [])

    finding = result.discrepancies[0]
    assert finding.discrepancy_type == "OMITTED"
    assert finding.severity == "high"


def test_omitted_moderate():
    result = reconcile([med("amoxicillin")], [])

    finding = result.discrepancies[0]
    assert finding.discrepancy_type == "OMITTED"
    assert finding.severity == "moderate"


def test_added():
    result = reconcile([], [med("metformin")])

    finding = result.discrepancies[0]
    assert finding.discrepancy_type == "ADDED"
    assert finding.severity == "moderate"


def test_dose_change_high_risk():
    result = reconcile([med("warfarin", strength="5mg")], [med("warfarin", strength="2.5mg")])

    finding = result.discrepancies[0]
    assert finding.discrepancy_type == "DOSE_CHANGE"
    assert finding.severity == "high"


def test_dose_change_moderate():
    result = reconcile([med("amoxicillin", strength="500mg")], [med("amoxicillin", strength="250mg")])

    finding = result.discrepancies[0]
    assert finding.discrepancy_type == "DOSE_CHANGE"
    assert finding.severity == "moderate"


def test_frequency_change():
    result = reconcile([med("lisinopril", frequency="BID")], [med("lisinopril", frequency="daily")])

    assert "FREQUENCY_CHANGE" in types(result)


def test_route_change_and_status_conflict():
    result = reconcile(
        [med("metoprolol", route="oral", status="active")],
        [med("metoprolol", route="IV", status="discontinued")],
    )

    assert "ROUTE_CHANGE" in types(result)
    assert "STATUS_CONFLICT" in types(result)


def test_duplicate_within_source():
    result = reconcile([med("warfarin"), med("warfarin")], [med("warfarin")])

    finding = next(item for item in result.discrepancies if item.discrepancy_type == "DUPLICATE")
    assert finding.severity == "moderate"
    assert finding.source_a_entry is not None


def test_therapeutic_duplicate():
    result = reconcile([med("lisinopril")], [med("losartan")])

    finding = next(item for item in result.discrepancies if item.discrepancy_type == "THERAPEUTIC_DUPLICATE")
    assert finding.severity == "low"
    assert set(finding.drugs_involved) == {"lisinopril", "losartan"}


def test_no_discrepancy():
    result = reconcile(
        [med("warfarin", strength="5mg", route="oral", frequency="daily")],
        [med("warfarin", strength="5mg", route="oral", frequency="daily")],
    )

    assert result.discrepancies == []
    assert result.reconciled_count == 1


def test_multiple_discrepancies_sorted():
    result = reconcile(
        [med("warfarin"), med("amoxicillin", strength="500mg")],
        [med("amoxicillin", strength="250mg")],
    )

    assert result.discrepancies[0].discrepancy_type == "OMITTED"
    assert "DOSE_CHANGE" in types(result)


def test_brand_to_generic_normalized():
    result = reconcile([med("Coumadin")], [med("warfarin")])

    assert "OMITTED" not in types(result)
    assert "ADDED" not in types(result)
    assert result.reconciled_count == 1


def test_empty_both_sources():
    result = reconcile([], [])

    assert result.discrepancies == []
    assert result.reconciled_count == 0
    assert result.assessment_date


def test_same_class_no_false_positive():
    result = reconcile([med("lisinopril")], [med("lisinopril")])

    assert "THERAPEUTIC_DUPLICATE" not in types(result)
    assert result.reconciled_count == 1


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

    async def execute(self, query):
        return self.results.pop(0)

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        return None


class StaffStub:
    def __init__(self, pharmacy_id=PHARMACY_A):
        self.id = STAFF_ID
        self.pharmacy_id = pharmacy_id
        self.role = "pharmacist"

    def has_permission(self, permission: str) -> bool:
        return permission == "clinical:read"


@pytest.mark.asyncio
async def test_endpoint_invalid_patient():
    patient_id = uuid4()
    db = StubDb([ResultStub(row=None)])

    with pytest.raises(HTTPException) as exc:
        await med_reconciliation.reconcile_medications(
            med_reconciliation.MedReconcileRequest(
                patient_id=patient_id,
                source_a_meds=[],
                source_b_meds=[],
            ),
            staff=StaffStub(pharmacy_id=PHARMACY_B),
            db=db,
        )

    assert exc.value.status_code == 404
    assert db.added == []


def test_collapse_by_status_drops_inactive_duplicate_of_active_drug():
    # A drug with both an active and a historical discontinued row in one source
    # must collapse to the active row only, so it is not flagged as a within-source
    # DUPLICATE or surfaced as OMITTED noise.
    entries = [
        med("warfarin", strength="5mg", status="active"),
        med("warfarin", strength="2mg", status="discontinued"),
    ]
    collapsed = med_reconciliation._collapse_by_status(entries)
    assert len(collapsed) == 1
    assert collapsed[0].strength == "5mg"
    assert collapsed[0].status == "active"


def test_collapse_by_status_keeps_genuine_active_duplicates():
    # Two ACTIVE rows of the same drug are a real duplicate and must be preserved
    # so the engine can still flag DUPLICATE.
    entries = [
        med("warfarin", strength="5mg", status="active"),
        med("warfarin", strength="5mg", status="active"),
    ]
    collapsed = med_reconciliation._collapse_by_status(entries)
    assert len(collapsed) == 2


def test_collapse_by_status_keeps_discontinued_only_drug():
    # A drug present only as discontinued in this source is kept, so a cross-source
    # STATUS_CONFLICT against an active row in the other source remains reachable.
    entries = [med("warfarin", status="discontinued")]
    collapsed = med_reconciliation._collapse_by_status(entries)
    assert len(collapsed) == 1
    assert collapsed[0].status == "discontinued"
