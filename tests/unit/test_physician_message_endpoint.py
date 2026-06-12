from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.platform.routers import physician_message
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
    app.include_router(physician_message.router, prefix="/physician-message")

    async def override_db():
        return db

    async def override_staff():
        return StaffStub()

    app.dependency_overrides[physician_message.get_db] = override_db
    app.dependency_overrides[physician_message.get_current_staff] = override_staff
    return TestClient(app)


def test_valid_sbar_en_returns_message_and_writes_one_audit_log(monkeypatch):
    async def degraded_generate(*_args, **_kwargs):
        return SimpleNamespace(text="", provider="none", degraded=True)

    monkeypatch.setattr("services.ai.physician_message.narrator.local_llm.generate", degraded_generate)
    db = StubDb()

    response = _client(db).post(
        "/physician-message/generate",
        json={
            "medication_issue": "Metformin intolerance reported after dose increase",
            "recommendation_or_question": "Would you consider reassessing tolerability and next steps?",
            "urgency": "routine",
            "format": "sbar",
            "language": "en",
            "supporting_data": ["patient reports GI upset"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["format"] == "sbar"
    assert body["language"] == "en"
    assert body["llm_used"] is False
    assert body["degraded"] is False
    assert "Situation:" in body["message"]["body"]
    assert body["pharmacist_verification_notice"]
    assert db.flushed is True
    audits = [row for row in db.added if isinstance(row, ClinicalAuditLog)]
    assert len(audits) == 1
    assert audits[0].module == "physician_message"
    assert audits[0].model_version == "physmsg-v1"


def test_missing_required_field_returns_422():
    db = StubDb()

    response = _client(db).post(
        "/physician-message/generate",
        json={
            "medication_issue": "Missing recommendation",
            "format": "sbar",
            "language": "en",
        },
    )

    assert response.status_code == 422
    assert db.added == []


def test_invalid_format_language_or_urgency_returns_422():
    db = StubDb()

    invalid_format = _client(db).post(
        "/physician-message/generate",
        json={
            "medication_issue": "Issue",
            "recommendation_or_question": "Question",
            "format": "memo",
            "language": "en",
            "urgency": "routine",
        },
    )
    invalid_language = _client(db).post(
        "/physician-message/generate",
        json={
            "medication_issue": "Issue",
            "recommendation_or_question": "Question",
            "format": "sbar",
            "language": "de",
            "urgency": "routine",
        },
    )
    invalid_urgency = _client(db).post(
        "/physician-message/generate",
        json={
            "medication_issue": "Issue",
            "recommendation_or_question": "Question",
            "format": "sbar",
            "language": "en",
            "urgency": "stat",
        },
    )

    assert invalid_format.status_code == 422
    assert invalid_language.status_code == 422
    assert invalid_urgency.status_code == 422
    assert db.added == []

