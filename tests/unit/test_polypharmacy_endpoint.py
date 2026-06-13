from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.platform.routers import polypharmacy
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
    app.include_router(polypharmacy.router, prefix="/polypharmacy")

    async def override_db():
        return db

    async def override_staff():
        return staff or StaffStub()

    app.dependency_overrides[polypharmacy.get_db] = override_db
    app.dependency_overrides[polypharmacy.get_current_staff] = override_staff
    return TestClient(app)


def _patient(patient_id):
    return SimpleNamespace(
        id=patient_id,
        pharmacy_id=PHARMACY_A,
        date_of_birth=date(1945, 1, 1),
        conditions=["atrial_fibrillation"],
        is_deleted=False,
    )


def test_same_tenant_review_writes_one_audit_log():
    patient_id = uuid4()
    db = StubDb([
        ResultStub(row=_patient(patient_id)),
        ResultStub(rows=[]),
        ResultStub(rows=[]),
    ])

    response = _client(db).post(
        "/polypharmacy/review",
        json={
            "patient_id": str(patient_id),
            "message_format": "concise",
            "medications": [
                {"drug_name": "diphenhydramine", "indication": "sleep"},
                {"drug_name": "oxybutynin", "indication": "overactive bladder"},
                {"drug_name": "amitriptyline", "indication": "neuropathy"},
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["patient_id"] == str(patient_id)
    assert body["model_version"] == "polypharmacy-rules-v1"
    assert body["anticholinergic_burden"]["score"] >= 6
    assert body["physician_message_draft"]
    assert db.flushed is True
    audits = [row for row in db.added if isinstance(row, ClinicalAuditLog)]
    assert len(audits) == 1
    assert audits[0].module == "polypharmacy"
    assert audits[0].model_version == "polypharmacy-rules-v1"
    assert "anticholinergic_burden" in audits[0].rules_triggered


def test_cross_tenant_or_unknown_patient_returns_404():
    patient_id = uuid4()
    db = StubDb([ResultStub(row=None)])

    response = _client(db, StaffStub(pharmacy_id=PHARMACY_B)).post(
        "/polypharmacy/review",
        json={"patient_id": str(patient_id)},
    )

    assert response.status_code == 404
    assert db.added == []
