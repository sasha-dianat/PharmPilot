from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.platform.routers import label_engine, prescriptions


PHARMACY_A = str(uuid4())
STAFF_ID = str(uuid4())


class StubDb:
    def __init__(self, rows: list[dict | None]):
        self.rows = list(rows)
        self.calls: list[dict] = []
        self.committed = False

    async def execute(self, query, params=None):
        self.calls.append({"sql": str(query), "params": params or {}})
        row = self.rows.pop(0) if self.rows else None
        result = MagicMock()
        result.mappings.return_value.first.return_value = row
        return result

    async def commit(self):
        self.committed = True


class StaffStub:
    def __init__(self, pharmacy_id: str = PHARMACY_A):
        self.id = STAFF_ID
        self.pharmacy_id = pharmacy_id
        self.role = "pharmacist"

    def has_permission(self, _permission: str) -> bool:
        return True


class RecordingExecutorLoop:
    def __init__(self):
        self.calls = 0

    async def run_in_executor(self, _executor, func):
        self.calls += 1
        self.func = func
        return None


def _label_user(pharmacy_id: str = PHARMACY_A, staff_id: str = STAFF_ID) -> dict:
    return {
        "sub": staff_id,
        "username": "tenant-a-pharmacist",
        "role": "pharmacist",
        "pharmacy_id": pharmacy_id,
        "staff_id": staff_id,
    }


def _label_client(db: StubDb | None = None, user: dict | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(label_engine.router, prefix="/labels")

    async def override_db():
        return db

    async def override_user():
        return user or _label_user()

    app.dependency_overrides[label_engine.get_db] = override_db
    app.dependency_overrides[label_engine.get_current_user] = override_user
    return TestClient(app)


def _prescriptions_client(db: StubDb, staff: StaffStub | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(prescriptions.router, prefix="/prescriptions")

    async def override_db():
        return db

    async def override_staff():
        return staff or StaffStub()

    app.dependency_overrides[prescriptions.get_db] = override_db
    app.dependency_overrides[prescriptions.get_current_staff] = override_staff
    return TestClient(app)


def _rx_label_row(**overrides) -> dict:
    row = {
        "id": str(uuid4()),
        "rx_number": "RX-HTTP-1001",
        "fill_date": None,
        "drug_name": "Amoxicillin",
        "drug_strength": "500mg",
        "drug_form": "capsule",
        "ndc": "00093310905",
        "quantity_prescribed": 21,
        "days_supply": 7,
        "refills_remaining": 0,
        "sig_text": "Take one capsule by mouth three times daily until gone",
        "is_controlled": False,
        "dea_schedule": None,
        "patient_first": "Jane",
        "patient_last": "Tenant",
        "date_of_birth": "1984-04-12",
        "presc_first": "Avery",
        "presc_last": "Prescriber",
        "presc_npi": "1234567890",
        "ph_name": "Tenant A Pharmacy",
        "address_line1": "10 Main St",
        "city": "Springfield",
        "state": "IL",
        "zip_code": "62704",
        "phone": "555-0100",
        "ph_npi": "9999999999",
        "latest_fill_number": 1,
    }
    row.update(overrides)
    return row


def test_generate_label_404s_cross_tenant_and_succeeds_same_tenant():
    rx_id = str(uuid4())
    cross_db = StubDb([None])
    cross_response = _label_client(cross_db).post(f"/labels/{rx_id}/generate", json={})

    assert cross_response.status_code == 404
    assert cross_db.calls[0]["params"] == {"rx_id": rx_id, "pharmacy_id": PHARMACY_A}

    same_db = StubDb([_rx_label_row()])
    same_response = _label_client(same_db).post(f"/labels/{rx_id}/generate", json={})

    assert same_response.status_code == 200
    body = same_response.json()
    assert body["rx_id"] == rx_id
    assert body["label"]["rx_number"] == "RX-HTTP-1001"
    assert body["label"]["drug_name"] == "Amoxicillin"
    assert same_db.calls[0]["params"]["pharmacy_id"] == PHARMACY_A


def test_print_label_404s_cross_tenant_before_socket_and_succeeds_same_tenant():
    rx_id = str(uuid4())
    cross_db = StubDb([None])
    cross_loop = RecordingExecutorLoop()
    with patch("services.platform.routers.label_engine.asyncio.get_event_loop", return_value=cross_loop):
        cross_response = _label_client(cross_db).post(
            f"/labels/{rx_id}/print",
            json={"printer_ip": "127.0.0.1", "include_aux": False},
        )

    assert cross_response.status_code == 404
    assert cross_loop.calls == 0

    same_db = StubDb([_rx_label_row()])
    same_loop = RecordingExecutorLoop()
    with patch("services.platform.routers.label_engine.asyncio.get_event_loop", return_value=same_loop):
        same_response = _label_client(same_db).post(
            f"/labels/{rx_id}/print",
            json={"printer_ip": "127.0.0.1", "include_aux": False},
        )

    assert same_response.status_code == 200
    assert same_response.json()["success"] is True
    assert same_loop.calls == 1
    assert same_db.calls[0]["params"]["pharmacy_id"] == PHARMACY_A


def test_handwritten_label_404s_cross_tenant_and_records_authenticated_staff():
    rx_id = str(uuid4())
    cross_db = StubDb([None])
    cross_response = _label_client(cross_db).post(
        f"/labels/{rx_id}/handwritten",
        json={"notes": "printer offline", "staff_id": str(uuid4())},
    )

    assert cross_response.status_code == 404
    assert cross_db.committed is False

    authenticated_staff_id = str(uuid4())
    client_supplied_staff_id = str(uuid4())
    same_db = StubDb([{"rx_number": "RX-HAND-1002"}, None])
    same_response = _label_client(
        same_db,
        user=_label_user(staff_id=authenticated_staff_id),
    ).post(
        f"/labels/{rx_id}/handwritten",
        json={"notes": "patient requested handwritten", "staff_id": client_supplied_staff_id},
    )

    assert same_response.status_code == 200
    body = same_response.json()
    assert body["success"] is True
    assert body["recorded_by"] == authenticated_staff_id
    assert body["recorded_by"] != client_supplied_staff_id

    insert_call = next(c for c in same_db.calls if "insert into label_events" in c["sql"].lower())
    assert insert_call["params"]["rx_id"] == rx_id
    assert insert_call["params"]["staff_id"] == authenticated_staff_id
    assert insert_call["params"]["staff_id"] != client_supplied_staff_id
    assert insert_call["params"]["notes"] == "patient requested handwritten"
    assert same_db.committed is True


def test_auxiliary_static_route_is_not_shadowed_by_dynamic_rx_routes():
    response = _label_client().get("/labels/auxiliary")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"total", "labels"}
    assert body["total"] == len(body["labels"])
    assert body["total"] > 0
    assert {"code", "text", "color_hex", "icon"} <= set(body["labels"][0])
    assert "rx_id" not in body
    assert "label" not in body


def test_rx_analysis_404s_cross_tenant_and_succeeds_same_tenant():
    rx_id = uuid4()
    cross_db = StubDb([None])
    cross_response = _prescriptions_client(cross_db).get(f"/prescriptions/{rx_id}/analysis")

    assert cross_response.status_code == 404
    assert cross_db.calls[0]["params"] == {"id": str(rx_id), "pharmacy_id": PHARMACY_A}

    computed_at = datetime(2026, 6, 7, 12, 30, tzinfo=timezone.utc)
    same_db = StubDb([
        {
            "id": rx_id,
            "intake_analysis_status": "ready",
            "triage_lane": "standard",
            "triage_result": {"risk": "low"},
            "council_cache": {"summary": "ok"},
            "council_computed_at": computed_at,
        }
    ])
    same_response = _prescriptions_client(same_db).get(f"/prescriptions/{rx_id}/analysis")

    assert same_response.status_code == 200
    body = same_response.json()
    assert body == {
        "rx_id": str(rx_id),
        "status": "ready",
        "triage_lane": "standard",
        "triage_result": {"risk": "low"},
        "council_cache": {"summary": "ok"},
        "council_computed_at": computed_at.isoformat(),
    }
    assert same_db.calls[0]["params"]["pharmacy_id"] == PHARMACY_A
