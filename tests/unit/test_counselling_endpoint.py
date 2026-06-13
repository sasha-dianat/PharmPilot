from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.platform.routers import counselling
from shared.models.clinical import ClinicalAuditLog


PHARMACY_A = uuid4()
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
    def __init__(self):
        self.id = STAFF_ID
        self.pharmacy_id = PHARMACY_A
        self.role = "pharmacist"

    def has_permission(self, permission: str) -> bool:
        return permission == "clinical:read"


def _client(db: StubDb) -> TestClient:
    app = FastAPI()
    app.include_router(counselling.router, prefix="/counselling")

    async def override_db():
        return db

    async def override_staff():
        return StaffStub()

    app.dependency_overrides[counselling.get_db] = override_db
    app.dependency_overrides[counselling.get_current_staff] = override_staff
    return TestClient(app)


def test_generate_en_standard_returns_content_and_writes_one_audit_log(monkeypatch):
    async def degraded_generate(*_args, **_kwargs):
        return SimpleNamespace(text="", provider="none", degraded=True)

    monkeypatch.setattr("services.ai.counselling.narrator.local_llm.generate", degraded_generate)
    db = StubDb()

    response = _client(db).post(
        "/counselling/generate",
        json={"drug_name": "lisinopril", "level": "standard", "language": "en"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["content"]["serious_red_flags"]
    assert body["llm_used"] is False
    assert body["degraded"] is False
    assert body["pharmacist_verification_notice"]
    assert db.flushed is True
    audits = [row for row in db.added if isinstance(row, ClinicalAuditLog)]
    assert len(audits) == 1
    assert audits[0].module == "counselling"
    assert audits[0].model_version == "counselling-v1"
    assert audits[0].rules_triggered == ["lisinopril"]


def test_generate_degraded_llm_falls_back_to_english_baseline(monkeypatch):
    async def degraded_generate(*_args, **_kwargs):
        return SimpleNamespace(text="", provider="none", degraded=True)

    monkeypatch.setattr("services.ai.counselling.narrator.local_llm.generate", degraded_generate)
    db = StubDb()

    response = _client(db).post(
        "/counselling/generate",
        json={"drug_name": "sertraline", "level": "elderly", "language": "es"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["language"] == "en"
    assert body["level"] == "standard"
    assert body["llm_used"] is False
    assert body["degraded"] is True
    assert "English standard-level" in body["note"]


def test_invalid_level_or_language_returns_422():
    db = StubDb()

    bad_level = _client(db).post(
        "/counselling/generate",
        json={"drug_name": "lisinopril", "level": "advanced", "language": "en"},
    )
    bad_language = _client(db).post(
        "/counselling/generate",
        json={"drug_name": "lisinopril", "level": "standard", "language": "de"},
    )

    assert bad_level.status_code == 422
    assert bad_language.status_code == 422
    assert db.added == []


def test_neither_drug_name_nor_rx_id_returns_422():
    db = StubDb()

    response = _client(db).post(
        "/counselling/generate",
        json={"level": "standard", "language": "en"},
    )

    assert response.status_code == 422
    assert db.added == []
