from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.platform.routers import drug_intelligence
from shared.models.clinical import ClinicalAuditLog


PHARMACY_ID = uuid4()
STAFF_ID = uuid4()


class ResultStub:
    def __init__(self, row=None):
        self._row = row

    def scalar_one_or_none(self):
        return self._row

    def scalars(self):
        result = MagicMock()
        result.all.return_value = []
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
    id = STAFF_ID
    pharmacy_id = PHARMACY_ID
    role = "pharmacist"

    def has_permission(self, permission: str) -> bool:
        return permission == "clinical:read"


class StoreStub:
    def __init__(self, chunks=None):
        self.chunks = chunks or []
        self.filter_drugs = []

    def search(self, query: str, top_k: int = 6, score_threshold: float = 0.55, filter_drugs=None):
        self.filter_drugs.append(filter_drugs)
        return self.chunks[:top_k]


class ErrorStore:
    def search(self, query: str, top_k: int = 6, score_threshold: float = 0.55, filter_drugs=None):
        raise RuntimeError("vector store unavailable")


def chunk(source_id: str = "S1"):
    return SimpleNamespace(
        chunk_id=f"chunk-{source_id}",
        source_id=source_id,
        content="Evidence snippet for drug dosing and cautions.",
        source_title="Drug Reference",
        source_type="guideline",
        url="https://example.test/reference",
        doi=None,
        evidence_grade="A",
        similarity_score=0.83,
        drugs_mentioned=[],
        specialty_tags=[],
    )


def _client(db: StubDb) -> TestClient:
    app = FastAPI()
    app.include_router(drug_intelligence.router, prefix="/drug-intelligence")

    async def override_db():
        return db

    async def override_staff():
        return StaffStub()

    app.dependency_overrides[drug_intelligence.get_db] = override_db
    app.dependency_overrides[drug_intelligence.get_current_staff] = override_staff
    return TestClient(app)


def test_monograph_returns_200_and_writes_one_audit(monkeypatch):
    db = StubDb()
    store = StoreStub([chunk("S1")])
    monkeypatch.setattr(drug_intelligence, "_get_vector_store", lambda: store)
    monkeypatch.setattr(drug_intelligence, "synthesize_local", None)

    response = _client(db).post(
        "/drug-intelligence/monograph",
        json={"drug_name": "Metformin", "sections": ["dosing", "monitoring"], "top_k": 4},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["drug_name"] == "Metformin"
    assert body["model_version"] == "drug-intel-v1"
    assert body["sections"][0]["sources"][0]["source_id"] == "S1"
    assert body["sections"][0]["llm_used"] is False
    assert body["pharmacist_verification_notice"]
    assert body["trainable_note"]
    assert store.filter_drugs == [["metformin"], ["metformin"]]
    assert db.flushed is True
    audits = [row for row in db.added if isinstance(row, ClinicalAuditLog)]
    assert len(audits) == 1
    assert audits[0].module == "drug_intelligence"
    assert audits[0].model_version == "drug-intel-v1"
    assert audits[0].output_snapshot["dosing"]["source_ids"] == ["S1"]


def test_missing_drug_or_rx_returns_422():
    db = StubDb()

    response = _client(db).post("/drug-intelligence/monograph", json={})

    assert response.status_code == 422
    assert db.added == []


def test_bad_section_returns_422():
    db = StubDb()

    response = _client(db).post(
        "/drug-intelligence/monograph",
        json={"drug_name": "Metformin", "sections": ["not-a-section"]},
    )

    assert response.status_code == 422
    assert db.added == []


def test_vector_store_error_refuses_sections_without_500(monkeypatch):
    db = StubDb()
    monkeypatch.setattr(drug_intelligence, "_get_vector_store", lambda: ErrorStore())
    monkeypatch.setattr(drug_intelligence, "synthesize_local", None)

    response = _client(db).post(
        "/drug-intelligence/monograph",
        json={"drug_name": "Metformin", "sections": ["dosing", "monitoring"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert [section["refused"] for section in body["sections"]] == [True, True]
    assert all(section["answer"] == "Knowledge base is temporarily unavailable." for section in body["sections"])
    audits = [row for row in db.added if isinstance(row, ClinicalAuditLog)]
    assert len(audits) == 1
