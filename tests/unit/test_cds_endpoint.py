from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.platform.routers import cds
from shared.models.clinical import ClinicalAlert, ClinicalAuditLog


PHARMACY_A = uuid4()
PHARMACY_B = uuid4()
STAFF_ID = uuid4()


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
        self.calls = []

    async def execute(self, query):
        self.calls.append(str(query))
        return self.results.pop(0)

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        self.flushed = True


class StaffStub:
    def __init__(self, pharmacy_id=PHARMACY_A):
        self.id = STAFF_ID
        self.pharmacy_id = pharmacy_id
        self.role = "pharmacist"

    def has_permission(self, permission: str) -> bool:
        return permission == "clinical:read"


def _client(db: StubDb, staff: StaffStub | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(cds.router, prefix="/cds")

    async def override_db():
        return db

    async def override_staff():
        return staff or StaffStub()

    app.dependency_overrides[cds.get_db] = override_db
    app.dependency_overrides[cds.get_current_staff] = override_staff
    return TestClient(app)


def _patient(patient_id):
    return SimpleNamespace(
        id=patient_id,
        pharmacy_id=PHARMACY_A,
        date_of_birth=date(1950, 1, 1),
        weight_kg=None,
        pregnancy_status=None,
        renal_function=None,
        hepatic_status=None,
        conditions=[],
        is_deleted=False,
    )


def _med(name: str):
    return SimpleNamespace(
        drug_name=name,
        normalized_name=name.lower(),
        source="patient_reported",
    )


def _lab(name: str, value: str):
    return SimpleNamespace(
        test_name=name,
        value=value,
        unit="mmol/L",
        result_date=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )


def test_same_tenant_evaluate_persists_alerts_and_audit_log():
    patient_id = uuid4()
    db = StubDb([
        ResultStub(row=_patient(patient_id)),
        ResultStub(rows=[_med("lisinopril"), _med("spironolactone")]),
        ResultStub(rows=[]),
        ResultStub(rows=[]),
        ResultStub(rows=[_lab("serum potassium", "5.8")]),
    ])

    response = _client(db).post("/cds/evaluate", json={"patient_id": str(patient_id)})

    assert response.status_code == 200
    body = response.json()
    assert body["patient_id"] == str(patient_id)
    assert body["alerts"][0]["rule_id"] == "acei_spironolactone_potassium"
    assert body["alerts"][0]["severity"] == "CRITICAL"
    assert body["pharmacist_verification_notice"]
    assert db.flushed is True
    assert any(isinstance(row, ClinicalAlert) for row in db.added)
    audit = next(row for row in db.added if isinstance(row, ClinicalAuditLog))
    assert audit.model_version == "cds-rules-v1"
    assert audit.rules_triggered == ["acei_spironolactone_potassium"]


def test_cross_tenant_or_unknown_patient_returns_404():
    patient_id = uuid4()
    db = StubDb([ResultStub(row=None)])

    response = _client(db, StaffStub(pharmacy_id=PHARMACY_B)).post(
        "/cds/evaluate",
        json={"patient_id": str(patient_id)},
    )

    assert response.status_code == 404
    assert db.added == []
