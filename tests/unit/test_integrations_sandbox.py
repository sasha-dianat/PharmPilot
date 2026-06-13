import logging
from uuid import uuid4
from unittest.mock import MagicMock, patch

import pytest

from services.core.adjudication.engine import AdjudicationEngine
from services.integrations.pdmp.client import PDMPClient, PDMPPatientQuery
from services.platform.notifications.push_service import (
    NotificationType,
    PushNotificationRequest,
    PushNotificationService,
)
from services.platform.notifications.sms_service import SMSService


def _claim_data(rx_number: str) -> dict:
    return {
        "date_of_service": "2026-06-10",
        "rx_number": rx_number,
        "fill_number": 0,
        "days_supply": 30,
        "ndc": "00071-0154-23",
        "quantity": 30,
        "daw_code": "0",
        "ingredient_cost": 45.99,
        "dispensing_fee": 1.50,
        "usual_and_customary": 47.49,
    }


def _insurance() -> dict:
    return {
        "bin_number": "004336",
        "pcn": "ADV",
        "group_number": "GRP001",
        "member_id": "MEM123456",
        "person_code": "01",
    }


def _patient() -> dict:
    return {
        "first_name": "Test",
        "last_name": "Patient",
        "date_of_birth": "1980-01-01",
    }


@pytest.mark.asyncio
async def test_adjudication_sandbox_approved_rejected_deterministic_and_no_httpx():
    engine = AdjudicationEngine(integrations_sandbox=True)

    with patch("services.core.adjudication.engine.httpx.AsyncClient") as async_client:
        approved = await engine.submit_claim(
            fill_id=uuid4(),
            claim_data=_claim_data("RX000000"),
            insurance=_insurance(),
            patient=_patient(),
            prescriber_npi="1234567890",
        )
        rejected = await engine.submit_claim(
            fill_id=uuid4(),
            claim_data=_claim_data("RX000004"),
            insurance=_insurance(),
            patient=_patient(),
            prescriber_npi="1234567890",
        )
        rejected_again = await engine.submit_claim(
            fill_id=uuid4(),
            claim_data=_claim_data("RX000004"),
            insurance=_insurance(),
            patient=_patient(),
            prescriber_npi="1234567890",
        )

    assert async_client.call_count == 0
    assert approved.status == "approved"
    assert approved.response_status == "A"
    assert approved.ingredient_cost_paid is not None
    assert approved.dispensing_fee_paid is not None
    assert approved.total_amount_paid is not None
    assert approved.patient_pay_amount is not None
    assert "SANDBOX" in approved.raw_response

    assert rejected.status == "rejected"
    assert rejected.response_status == "R"
    assert rejected.reject_codes == ["75"]
    assert "Prior Authorization Required" in rejected.reject_messages
    assert rejected.reject_codes == rejected_again.reject_codes
    assert rejected.raw_response == rejected_again.raw_response


@pytest.mark.asyncio
async def test_adjudication_sandbox_reversal_works():
    fill_id = uuid4()
    engine = AdjudicationEngine(integrations_sandbox=True)

    with patch("services.core.adjudication.engine.httpx.AsyncClient") as async_client:
        result = await engine.reverse_claim(
            original_claim_data={
                **_claim_data("RX000000"),
                "fill_id": str(fill_id),
            },
            insurance=_insurance(),
        )

    assert async_client.call_count == 0
    assert result.status == "reversed"
    assert result.response_status == "A"
    assert "SANDBOX" in result.raw_response


@pytest.mark.asyncio
async def test_pdmp_sandbox_clean_and_hot_patients_use_real_risk_flags():
    client = PDMPClient(integrations_sandbox=True)
    clean_patient = PDMPPatientQuery(
        first_name="Clean",
        last_name="Patient",
        date_of_birth="1980-01-01",
        state_of_residence="CA",
    )
    hot_patient = PDMPPatientQuery(
        first_name="Alex",
        last_name="Smith",
        date_of_birth="1960-01-15",
        state_of_residence="CA",
    )

    with patch("services.integrations.pdmp.client.httpx.AsyncClient") as async_client:
        clean = await client.query(clean_patient, "1881000000", "1992000000", "CII")
        hot = await client.query(hot_patient, "1881000000", "1992000000", "CII")

    assert async_client.call_count == 0
    assert clean.raw_response == '{"backend": "sandbox", "risk_profile": "clean"}'
    assert clean.multiple_providers_flag is False
    assert clean.multiple_pharmacies_flag is False
    assert clean.overlapping_controlled_flag is False

    assert hot.raw_response == '{"backend": "sandbox", "risk_profile": "hot"}'
    assert hot.multiple_providers_flag is True
    assert hot.multiple_pharmacies_flag is True
    assert hot.overlapping_controlled_flag is True
    assert hot.prescriber_count_30d == 4
    assert hot.pharmacy_count_30d == 4


@pytest.mark.asyncio
async def test_notifications_sandbox_success_shape_no_network_and_masked_logs(caplog):
    sms_service = SMSService(account_sid="AC123", integrations_sandbox=True)
    push_service = PushNotificationService(fcm_project_id="pilot", integrations_sandbox=True)
    patient_id = uuid4()

    caplog.set_level(logging.INFO)
    with (
        patch("services.platform.notifications.sms_service.httpx.AsyncClient") as sms_httpx,
        patch("services.platform.notifications.push_service.httpx.AsyncClient") as push_httpx,
    ):
        sms = await sms_service.send(
            to_phone="+1 (415) 555-1234",
            template_name="pickup_ready",
            template_vars={
                "pharmacy": "Pilot Pharmacy",
                "closing": "7 PM",
                "phone": "4155559999",
            },
        )
        push = await push_service.send(
            PushNotificationRequest(
                patient_id=patient_id,
                notification_type=NotificationType.PICKUP_READY,
                device_token="device-token",
                platform="ios",
                pharmacy_name="Pilot Pharmacy",
                closing_time="7 PM",
            )
        )

    assert sms_httpx.call_count == 0
    assert push_httpx.call_count == 0
    assert sms.success is True
    assert sms.to_phone == "+1 (415) 555-1234"
    assert sms.message_sid.startswith("sandbox-sms-")
    assert push.success is True
    assert push.patient_id == patient_id
    assert push.message_id == f"sandbox-push-{str(patient_id)[:8]}"

    log_text = caplog.text
    assert "SANDBOX" in log_text
    assert "+1 (415) 555-1234" not in log_text
    assert "4155559999" not in log_text
    assert "141****34" in log_text
    assert "415****99" in log_text


class _FakeResponse:
    status_code = 200
    text = "{}"

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "patient": {"matched": True},
            "prescriptionHistory": [],
            "narxScores": {"narcoticScore": 42, "sedativeScore": 12},
        }


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, *args, **kwargs):
        return _FakeResponse()


@pytest.mark.asyncio
async def test_sandbox_flag_false_with_credentials_attempts_live_path():
    client = PDMPClient(narxcare_api_key="live-key", integrations_sandbox=False)
    patient = PDMPPatientQuery(
        first_name="Clean",
        last_name="Patient",
        date_of_birth="1980-01-01",
        state_of_residence="CA",
    )
    async_client = MagicMock(side_effect=_FakeAsyncClient)

    with patch("services.integrations.pdmp.client.httpx.AsyncClient", async_client):
        result = await client.query(patient, "1881000000", "1992000000", "CII")

    assert async_client.call_count == 1
    assert result.raw_response != '{"backend": "sandbox", "risk_profile": "clean"}'
    assert result.patient_found is True
