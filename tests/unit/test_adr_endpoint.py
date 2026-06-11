from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.platform.routers import adr
from shared.models.clinical import ClinicalAuditLog


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

    async def execute(self, query):
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
    app.include_router(adr.router, prefix="/adr")

    async def override_db():
        return db

    async def override_staff():
        return staff or StaffStub()

    app.dependency_overrides[adr.get_db] = override_db
    app.dependency_overrides[adr.get_current_staff] = override_staff
    return TestClient(app)


def _patient(patient_id):
    return SimpleNamespace(
        id=patient_id,
        pharmacy_id=PHARMACY_A,
        date_of_birth=date(1950, 1, 1),
        conditions=[],
        is_deleted=False,
    )


def _med(name: str, start_date: date | None = None):
    return SimpleNamespace(
        drug_name=name,
        normalized_name=name.lower(),
        source="patient_reported",
        start_date=start_date,
        stop_date=None,
    )


def _lab(name: str, value: str):
    return SimpleNamespace(
        test_name=name,
        value=value,
        unit="mmol/L",
        result_date=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )


def test_same_tenant_assess_returns_causes_and_writes_one_audit_log(monkeypatch):
    async def degraded_generate(*_args, **_kwargs):
        return SimpleNamespace(text="", provider="none", degraded=True)

    monkeypatch.setattr("services.ai.adr_detective.narrator.generate", degraded_generate)
    patient_id = uuid4()
    db = StubDb([
        ResultStub(row=_patient(patient_id)),
        ResultStub(rows=[_med("lisinopril", date(2026, 5, 11))]),
        ResultStub(rows=[]),
        ResultStub(rows=[_lab("serum sodium", "140")]),
    ])

    response = _client(db).post(
        "/adr/assess",
        json={
            "patient_id": str(patient_id),
            "complaint": "dry cough",
            "onset_date": "2026-06-01",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["patient_id"] == str(patient_id)
    assert body["suspected_causes"][0]["drug"] == "lisinopril"
    assert body["suspected_causes"][0]["reaction"] == "cough"
    assert body["suspected_causes"][0]["causality"] == "probable"
    assert body["llm_used"] is False
    assert body["degraded"] is True
    assert body["not_a_diagnosis_notice"]
    assert db.flushed is True
    audits = [row for row in db.added if isinstance(row, ClinicalAuditLog)]
    assert len(audits) == 1
    assert audits[0].module == "adr"
    assert audits[0].model_version == "adr-rules-v1"
    assert audits[0].rules_triggered == ["lisinopril"]


def test_cross_tenant_or_unknown_patient_returns_404():
    patient_id = uuid4()
    db = StubDb([ResultStub(row=None)])

    response = _client(db, StaffStub(pharmacy_id=PHARMACY_B)).post(
        "/adr/assess",
        json={"patient_id": str(patient_id), "complaint": "dry cough"},
    )

    assert response.status_code == 404
    assert db.added == []


def test_empty_complaint_returns_422():
    patient_id = uuid4()
    db = StubDb([])

    response = _client(db).post(
        "/adr/assess",
        json={"patient_id": str(patient_id), "complaint": "   "},
    )

    assert response.status_code == 422
    assert db.added == []
