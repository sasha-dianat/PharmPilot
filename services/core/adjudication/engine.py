"""
NCPDP D.0 Adjudication Engine.
Submits claims to PBM switches, parses responses, handles rejects.
Sub-200ms SLA target with circuit breaker and retry logic.
"""
import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional
from uuid import UUID

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from services.core.adjudication.ncpdp_builder import NCPDPBuilder, NCPDPClaim, NCPDPParser
from services.core.adjudication.reject_resolver import RejectResolver

logger = logging.getLogger(__name__)

# PBM switch routing — BIN → endpoint
BIN_ROUTING_TABLE: dict[str, dict] = {
    # Change Healthcare / Emdeon (most retail PBMs route through here)
    "default": {
        "url": "https://claims.changehealthcare.com/ncpdp/d0",
        "switch": "change_healthcare",
        "timeout_ms": 180,
    },
    # Argus
    "610415": {"url": "https://adjudication.arguspci.com/ncpdp", "switch": "argus"},
    # RelayHealth
    "610165": {"url": "https://rx.relayhealth.com/claims", "switch": "relay_health"},
    # Direct PBM connections (example)
    "004336": {"url": "https://direct.expressscripts.com/ncpdp", "switch": "express_scripts"},
    "610494": {"url": "https://caremark-adj.cvshealth.com/ncpdp", "switch": "cvs_caremark"},
}


class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing — reject immediately
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class AdjudicationResult:
    claim_id: UUID
    fill_id: UUID
    status: str                   # approved, rejected, reversed, error
    response_status: Optional[str] = None  # A, R, P
    reject_codes: list[str] = field(default_factory=list)
    reject_messages: list[str] = field(default_factory=list)
    ingredient_cost_paid: Optional[float] = None
    dispensing_fee_paid: Optional[float] = None
    total_amount_paid: Optional[float] = None
    patient_pay_amount: Optional[float] = None
    authorization_number: Optional[str] = None
    response_time_ms: Optional[int] = None
    raw_request: Optional[str] = None
    raw_response: Optional[str] = None
    auto_resolution_attempted: bool = False
    auto_resolution_action: Optional[str] = None


class PBMCircuitBreaker:
    """Per-BIN circuit breaker to prevent cascading failures."""

    FAILURE_THRESHOLD = 5      # Failures before opening
    RECOVERY_TIMEOUT_S = 60    # Seconds before trying HALF_OPEN

    def __init__(self):
        self._states: dict[str, CircuitState] = {}
        self._failure_counts: dict[str, int] = {}
        self._last_failure_time: dict[str, float] = {}

    def get_state(self, bin_number: str) -> CircuitState:
        state = self._states.get(bin_number, CircuitState.CLOSED)
        if state == CircuitState.OPEN:
            last = self._last_failure_time.get(bin_number, 0)
            if time.monotonic() - last > self.RECOVERY_TIMEOUT_S:
                self._states[bin_number] = CircuitState.HALF_OPEN
                return CircuitState.HALF_OPEN
        return state

    def record_success(self, bin_number: str) -> None:
        self._states[bin_number] = CircuitState.CLOSED
        self._failure_counts[bin_number] = 0

    def record_failure(self, bin_number: str) -> None:
        count = self._failure_counts.get(bin_number, 0) + 1
        self._failure_counts[bin_number] = count
        self._last_failure_time[bin_number] = time.monotonic()
        if count >= self.FAILURE_THRESHOLD:
            self._states[bin_number] = CircuitState.OPEN
            logger.warning("Circuit OPEN for BIN %s after %d failures", bin_number, count)


_circuit_breaker = PBMCircuitBreaker()
_builder = NCPDPBuilder()
_parser = NCPDPParser()
_resolver = RejectResolver()


class AdjudicationEngine:
    """
    Main adjudication engine — submits NCPDP D.0 claims to PBM switches.
    """

    def __init__(self, db=None, integrations_sandbox: bool = True):
        self.db = db
        self.integrations_sandbox = integrations_sandbox

    async def submit_claim(
        self,
        fill_id: UUID,
        claim_data: dict,
        insurance: dict,
        patient: dict,
        prescriber_npi: str,
    ) -> AdjudicationResult:
        """
        Build and submit a B1 billing claim.
        Handles retry on transient failures, circuit breaker per BIN.
        """
        from uuid import uuid4
        claim_id = uuid4()
        bin_number = insurance["bin_number"]
        start = time.monotonic()

        # Check circuit breaker
        circuit_state = _circuit_breaker.get_state(bin_number)
        if circuit_state == CircuitState.OPEN:
            logger.warning("Circuit OPEN for BIN %s — claim queued", bin_number)
            return AdjudicationResult(
                claim_id=claim_id,
                fill_id=fill_id,
                status="queued_circuit_open",
            )

        # Build NCPDP claim
        ncpdp_claim = NCPDPClaim(
            bin_number=bin_number,
            pcn=insurance.get("pcn", ""),
            group_id=insurance.get("group_number", ""),
            cardholder_id=insurance["member_id"],
            person_code=insurance.get("person_code", "01"),
            patient_dob=patient.get("date_of_birth", "").replace("-", ""),
            patient_first_name=patient.get("first_name", "")[:12],
            patient_last_name=patient.get("last_name", "")[:15],
            date_of_service=claim_data["date_of_service"].replace("-", ""),
            rx_number=claim_data["rx_number"],
            fill_number=str(claim_data.get("fill_number", 0)),
            days_supply=str(claim_data["days_supply"]).zfill(3),
            ndc=claim_data["ndc"].replace("-", ""),
            quantity=str(claim_data["quantity"]),
            daw_code=claim_data.get("daw_code", "0"),
            prescriber_id=prescriber_npi,
            ingredient_cost_submitted=str(claim_data.get("ingredient_cost", 0)),
            dispensing_fee_submitted=str(claim_data.get("dispensing_fee", 1.00)),
            usual_and_customary_charge=str(claim_data.get("usual_and_customary", 0)),
            prior_auth_number_submitted=claim_data.get("prior_auth_number", ""),
            prior_auth_type_code=claim_data.get("prior_auth_type_code", ""),
            submission_clarification_code=claim_data.get("submission_clarification_code", ""),
        )

        raw_request = _builder.build_billing_claim(ncpdp_claim)

        # Submit to PBM switch
        try:
            raw_response = await self._submit_to_switch(bin_number, raw_request)
            _circuit_breaker.record_success(bin_number)
        except Exception as exc:
            _circuit_breaker.record_failure(bin_number)
            logger.error("Claim submission failed for BIN %s: %s", bin_number, exc)
            return AdjudicationResult(
                claim_id=claim_id,
                fill_id=fill_id,
                status="submission_error",
                raw_request=raw_request,
            )

        response_time_ms = int((time.monotonic() - start) * 1000)
        parsed = _parser.parse_response(raw_response)

        result = AdjudicationResult(
            claim_id=claim_id,
            fill_id=fill_id,
            status="pending",
            response_time_ms=response_time_ms,
            raw_request=raw_request,
            raw_response=raw_response,
            response_status=parsed.get("transaction_response_status"),
            reject_codes=parsed.get("reject_codes", []),
            reject_messages=parsed.get("reject_messages", []),
            ingredient_cost_paid=parsed.get("ingredient_cost_paid"),
            dispensing_fee_paid=parsed.get("dispensing_fee_paid"),
            total_amount_paid=parsed.get("total_amount_paid"),
            patient_pay_amount=parsed.get("patient_pay_amount"),
            authorization_number=parsed.get("authorization_number"),
        )

        if parsed.get("approved"):
            result.status = "approved"
            logger.info(
                "Claim APPROVED in %dms — patient pays $%.2f",
                response_time_ms, result.patient_pay_amount or 0
            )
        else:
            result.status = "rejected"
            # Attempt automatic resolution
            resolution = await _resolver.attempt_auto_resolve(
                reject_codes=result.reject_codes,
                claim_data=claim_data,
                insurance=insurance,
            )
            if resolution:
                result.auto_resolution_attempted = True
                result.auto_resolution_action = resolution.get("action")
                logger.info(
                    "Auto-resolution for codes %s: %s",
                    result.reject_codes, resolution.get("action")
                )

        return result

    async def reverse_claim(
        self,
        original_claim_data: dict,
        insurance: dict,
    ) -> AdjudicationResult:
        """Submit a B2 reversal for a previously approved claim."""
        from uuid import uuid4
        claim_id = uuid4()
        bin_number = insurance["bin_number"]

        reversal_claim = NCPDPClaim(
            bin_number=bin_number,
            pcn=insurance.get("pcn", ""),
            transaction_code="B2",
            cardholder_id=insurance["member_id"],
            date_of_service=original_claim_data["date_of_service"].replace("-", ""),
            rx_number=original_claim_data["rx_number"],
            fill_number=str(original_claim_data.get("fill_number", 0)),
            ndc=original_claim_data["ndc"].replace("-", ""),
            quantity=str(original_claim_data["quantity"]),
        )

        raw_request = _builder.build_reversal(reversal_claim)

        try:
            raw_response = await self._submit_to_switch(bin_number, raw_request)
        except Exception as exc:
            return AdjudicationResult(
                claim_id=claim_id,
                fill_id=UUID(original_claim_data["fill_id"]),
                status="reversal_error",
            )

        parsed = _parser.parse_response(raw_response)
        return AdjudicationResult(
            claim_id=claim_id,
            fill_id=UUID(original_claim_data["fill_id"]),
            status="reversed" if parsed.get("approved") else "reversal_rejected",
            response_status=parsed.get("transaction_response_status"),
            raw_request=raw_request,
            raw_response=raw_response,
        )

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.1, min=0.1, max=1.0),
    )
    async def _submit_to_switch(self, bin_number: str, raw_claim: str) -> str:
        """Submit raw NCPDP string to the appropriate PBM switch."""
        if self.integrations_sandbox:
            return self._submit_to_sandbox_switch(bin_number, raw_claim)

        routing = BIN_ROUTING_TABLE.get(bin_number, BIN_ROUTING_TABLE["default"])
        url = routing["url"]
        timeout_ms = routing.get("timeout_ms", 200)

        async with httpx.AsyncClient(timeout=timeout_ms / 1000) as client:
            response = await client.post(
                url,
                content=raw_claim.encode("ascii"),
                headers={
                    "Content-Type": "application/ncpdp-d0",
                    "Accept": "application/ncpdp-d0",
                },
            )
            response.raise_for_status()
            return response.text

    def _submit_to_sandbox_switch(self, bin_number: str, raw_claim: str) -> str:
        """Return a deterministic NCPDP D.0 response without network access."""
        fields = self._parse_ncpdp_fields(raw_claim)
        transaction_code = raw_claim[8:10].strip() or "B1"
        pcn = raw_claim[10:20] if len(raw_claim) >= 20 else ""
        header = f"{bin_number.ljust(6)[:6]}D0{transaction_code}{pcn}"
        fs = NCPDPBuilder.FIELD_SEPARATOR
        gs = NCPDPBuilder.GROUP_SEPARATOR
        seed_source = "|".join([
            bin_number,
            transaction_code,
            fields.get("C2", ""),
            fields.get("D2", "").strip(),
            fields.get("D7", ""),
            fields.get("BZ", ""),
            fields.get("E7", ""),
        ])
        digest = hashlib.sha256(seed_source.encode("utf-8")).hexdigest()
        seed = int(digest, 16)

        if transaction_code == "B2":
            auth = f"SBXREV{digest[:8].upper()}"
            logger.info(
                "NCPDP sandbox reversal for BIN %s rx=%s auth=%s",
                bin_number,
                fields.get("D2", "").strip(),
                auth,
            )
            return (
                f"{header}{gs}AM21{fs}ANA{fs}F3{auth}"
                f"{gs}AM22{fs}FQSANDBOX reversal accepted"
            )

        if seed % 5 == 0:
            logger.info(
                "NCPDP sandbox reject for BIN %s rx=%s code=75",
                bin_number,
                fields.get("D2", "").strip(),
            )
            return (
                f"{header}{gs}AM21{fs}ANR"
                f"{gs}AM23{fs}FA75{fs}FBPrior Authorization Required"
                f"{gs}AM22{fs}FQSANDBOX deterministic reject"
            )

        ingredient_submitted = self._ncpdp_amount_to_float(fields.get("D9", "0"))
        dispensing_fee_submitted = self._ncpdp_amount_to_float(fields.get("DC", "0"))
        if ingredient_submitted <= 0:
            ingredient_submitted = 25.00 + (seed % 7500) / 100
        if dispensing_fee_submitted <= 0:
            dispensing_fee_submitted = 1.50 + ((seed >> 8) % 450) / 100

        ingredient_paid = round(ingredient_submitted * (0.72 + ((seed >> 4) % 16) / 100), 2)
        dispensing_fee_paid = round(min(dispensing_fee_submitted, 1.00 + ((seed >> 12) % 500) / 100), 2)
        patient_pay = round(5.00 + ((seed >> 20) % 2000) / 100, 2)
        total_paid = round(max(0.01, ingredient_paid + dispensing_fee_paid - patient_pay), 2)
        auth = f"SBX{digest[:10].upper()}"

        logger.info(
            "NCPDP sandbox approval for BIN %s rx=%s auth=%s patient_pay=%.2f",
            bin_number,
            fields.get("D2", "").strip(),
            auth,
            patient_pay,
        )
        return (
            f"{header}{gs}AM21{fs}ANA{fs}F3{auth}"
            f"{gs}AM25"
            f"{fs}D9{self._format_ncpdp_cents(ingredient_paid)}"
            f"{fs}DC{self._format_ncpdp_cents(dispensing_fee_paid)}"
            f"{fs}DX{self._format_ncpdp_cents(total_paid)}"
            f"{fs}DY{self._format_ncpdp_cents(patient_pay)}"
            f"{gs}AM22{fs}FQSANDBOX deterministic approval"
        )

    @staticmethod
    def _parse_ncpdp_fields(raw_claim: str) -> dict[str, str]:
        fields: dict[str, str] = {}
        for group in raw_claim.split(NCPDPBuilder.GROUP_SEPARATOR):
            for field in group.split(NCPDPBuilder.FIELD_SEPARATOR)[1:]:
                if len(field) >= 2:
                    fields[field[:2]] = field[2:]
        return fields

    @staticmethod
    def _ncpdp_amount_to_float(value: str) -> float:
        try:
            return int(value or "0") / 100
        except ValueError:
            return 0.0

    @staticmethod
    def _format_ncpdp_cents(amount: float) -> str:
        return str(int(round(amount * 100))).zfill(10)
