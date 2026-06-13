from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.platform.routers import prescriptions
from shared.models.prescription import RxStatus


class StubDb:
    def __init__(self, rows, events: list):
        self.rows = rows
        self.events = events

    async def execute(self, _query):
        self.events.append("execute")
        return StubResult(self.rows)


class StubResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class StubWebSocket:
    def __init__(self, events: list):
        self.events = events
        self.sent = []

    async def accept(self):
        self.events.append("accept")

    async def send_json(self, payload):
        self.events.append("send_json")
        self.sent.append(payload)


def _rx(pharmacy_id):
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=uuid4(),
        rx_number="RX-WS-1001",
        pharmacy_id=pharmacy_id,
        patient_id=uuid4(),
        prescriber_id=uuid4(),
        ndc="00093310905",
        drug_name="Amoxicillin",
        drug_strength="500mg",
        sig_text="Take one capsule by mouth three times daily",
        sig_structured=None,
        quantity_prescribed=21,
        days_supply=7,
        refills_authorized=0,
        refills_remaining=0,
        dea_schedule=None,
        is_controlled=False,
        status=RxStatus.PENDING_VERIFICATION.value,
        source="paper",
        written_date=date(2026, 1, 15),
        fill_date=None,
        daw_code="0",
        acb_safety_report=None,
        ai_risk_score=None,
        claimed_by_staff_id=None,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_rx_queue_websocket_sends_queue_update_before_first_sleep(monkeypatch):
    events = []
    pharmacy_id = uuid4()
    websocket = StubWebSocket(events)
    db = StubDb([_rx(pharmacy_id)], events)

    async def sleep_stub(seconds):
        events.append(f"sleep:{seconds}")
        raise prescriptions.WebSocketDisconnect()

    monkeypatch.setattr(prescriptions.asyncio, "sleep", sleep_stub)

    await prescriptions.rx_queue_websocket(websocket, pharmacy_id, db)

    assert websocket.sent[0]["event"] == "queue_update"
    assert websocket.sent[0]["count"] == 1
    assert events.index("send_json") < events.index("sleep:3")
