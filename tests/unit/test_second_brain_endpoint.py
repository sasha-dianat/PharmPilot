from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.platform.routers import second_brain
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
    def __init__(self, results: list[ResultStub] | None = None):
        self.results = list(results or [])
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


class StoreStub:
    def __init__(self, chunks=None):
        self.chunks = chunks or []

    def search(self, query: str, top_k: int = 8, score_threshold: float = 0.55):
        return self.chunks[:top_k]


def chunk(source_id: str):
    return SimpleNamespace(
        chunk_id=f"chunk-{source_id}",
        source_id=source_id,
        content="Evidence snippet for renal dosing.",
        source_title="Renal Dosing Guideline",
        source_type="guideline",
        url="https://example.test/guideline",
        doi=None,
        evidence_grade="A",
        similarity_score=0.83,
        drugs_mentioned=[],
        specialty_tags=[],
    )


def _client(db: StubDb, staff: StaffStub | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(second_brain.router, prefix="/second-brain")

    async def override_db():
        return db

    async def override_staff():
        return staff or StaffStub()

    app.dependency_overrides[second_brain.get_db] = override_db
    app.dependency_overrides[second_brain.get_current_staff] = override_staff
    return TestClient(app)


def test_query_returns_sources_and_writes_one_audit_log(monkeypatch):
    db = StubDb()
    monkeypatch.setattr(second_brain, "_get_vector_store", lambda: StoreStub([chunk("S1")]))
    monkeypatch.setattr(second_brain, "_get_synthesizer", lambda: None)

    response = _client(db).post(
        "/second-brain/query",
        json={"question": "What evidence supports renal dose adjustment?", "top_k": 4},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["sources"][0]["source_id"] == "S1"
    assert body["llm_used"] is False
    assert body["refused"] is False
    assert body["pharmacist_verification_notice"]
    assert db.flushed is True
    audits = [row for row in db.added if isinstance(row, ClinicalAuditLog)]
    assert len(audits) == 1
    assert audits[0].module == "second_brain"
    assert audits[0].model_version == "second-brain-v1"
    assert audits[0].output_snapshot["source_ids"] == ["S1"]


def test_empty_question_returns_422(monkeypatch):
    db = StubDb()
    monkeypatch.setattr(second_brain, "_get_vector_store", lambda: StoreStub([chunk("S1")]))

    response = _client(db).post("/second-brain/query", json={"question": "   "})

    assert response.status_code == 422
    assert db.added == []


def test_unknown_or_cross_tenant_patient_returns_404(monkeypatch):
    patient_id = uuid4()
    db = StubDb([ResultStub(row=None)])
    monkeypatch.setattr(second_brain, "_get_vector_store", lambda: StoreStub([chunk("S1")]))

    response = _client(db, StaffStub(pharmacy_id=PHARMACY_B)).post(
        "/second-brain/query",
        json={"question": "What should I verify?", "patient_id": str(patient_id)},
    )

    assert response.status_code == 404
    assert db.added == []


def test_patient_context_returned_separately_without_name_or_exact_dob(monkeypatch):
    patient_id = uuid4()
    patient = SimpleNamespace(
        id=patient_id,
        pharmacy_id=PHARMACY_A,
        first_name="Pat",
        last_name="Example",
        date_of_birth=date(1950, 1, 1),
        national_id="1234567890",
        ssn_last4="1234",
        conditions=["CKD"],
        is_deleted=False,
    )
    medication = SimpleNamespace(drug_name="Metformin")
    allergy = SimpleNamespace(allergen_name="Penicillin")
    lab = SimpleNamespace(test_name="eGFR", value="42", unit="mL/min", result_date=date(2026, 6, 1))
    db = StubDb([
        ResultStub(row=patient),
        ResultStub(rows=[medication]),
        ResultStub(rows=[allergy]),
        ResultStub(rows=[lab]),
    ])
    monkeypatch.setattr(second_brain, "_get_vector_store", lambda: StoreStub([chunk("S1")]))
    monkeypatch.setattr(second_brain, "_get_synthesizer", lambda: None)

    response = _client(db).post(
        "/second-brain/query",
        json={"question": "What should I verify?", "patient_id": str(patient_id)},
    )

    assert response.status_code == 200
    context = response.json()["patient_context"]
    assert "Metformin" in context
    assert "Penicillin" in context
    assert "eGFR" in context
    assert "Pat" not in context
    assert "Example" not in context
    assert "1950-01-01" not in context
    assert "1234567890" not in context
    assert "1234" not in context
