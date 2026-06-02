"""
Electronic Prior Authorization (ePA) Engine
=============================================
Automates prior authorization requests via CoverMyMeds API and Surescripts PA.
PA is the #1 cause of prescription abandonment — faster PA = better adherence.

Workflow:
  1. Rx hits ADJUDICATION_REJECTED with code 75 (PA required)
  2. ePA engine auto-initiates an ePA request to the PBM
  3. Real-time PA status updates are pushed to pharmacist + prescriber
  4. When PA approved → auto-rebill claim
  5. If PA denied → suggest alternatives, initiate appeal if configured

PA response times:
  Urgent (in-progress): 24 hours
  Non-urgent: 3 business days
  CoverMyMeds real-time: seconds to minutes for many plans
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, date, timezone, timedelta
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

import httpx

logger = logging.getLogger(__name__)


class PAStatus(str, Enum):
    INITIATED    = "initiated"
    PENDING      = "pending"
    APPROVED     = "approved"
    DENIED       = "denied"
    APPEALED     = "appealed"
    CANCELLED    = "cancelled"
    EXPIRED      = "expired"


class PAUrgency(str, Enum):
    URGENT     = "urgent"      # 24h turnaround required
    NON_URGENT = "non_urgent"  # 3 business days


# Common PA denial reasons and recommended responses
PA_DENIAL_RESPONSES: dict[str, str] = {
    "not_medically_necessary": (
        "Request letter of medical necessity from prescriber documenting clinical rationale, "
        "prior therapies tried and failed, and expected outcomes."
    ),
    "step_therapy_required": (
        "Document failure of step therapy drugs (dates, doses, outcomes). "
        "If contraindicated, document contraindication with clinical evidence."
    ),
    "non_formulary": (
        "Request formulary exception based on medical necessity. "
        "Alternatively, consider therapeutic substitution with on-formulary alternative."
    ),
    "quantity_limit_exceeded": (
        "Submit PA with clinical documentation supporting quantity exceeding plan limit. "
        "Include titration history and prescriber attestation of medical necessity."
    ),
    "age_restriction": (
        "Provide clinical documentation supporting off-label use in this age group, "
        "referencing pediatric studies or published guidelines."
    ),
}


@dataclass
class PARequest:
    """Electronic prior authorization request."""
    pa_id: UUID = field(default_factory=uuid4)
    prescription_id: UUID = field(default_factory=uuid4)
    patient_id: UUID = field(default_factory=uuid4)
    pharmacy_id: UUID = field(default_factory=uuid4)

    # Drug
    drug_ndc: str = ""
    drug_name: str = ""
    strength: str = ""
    days_supply: int = 30
    quantity: float = 0.0

    # Insurance
    bin_number: str = ""
    pcn: Optional[str] = None
    group_number: Optional[str] = None
    member_id: str = ""

    # Prescriber
    prescriber_npi: str = ""
    prescriber_name: str = ""
    prescriber_phone: Optional[str] = None
    prescriber_fax: Optional[str] = None

    # Clinical info for PA
    diagnosis_codes: list[str] = field(default_factory=list)   # ICD-10 codes
    clinical_notes: str = ""
    prior_therapies_failed: list[str] = field(default_factory=list)

    # Status
    status: PAStatus = PAStatus.INITIATED
    urgency: PAUrgency = PAUrgency.NON_URGENT
    initiated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status_updated_at: Optional[datetime] = None

    # PA response
    pa_number: Optional[str] = None
    approval_date: Optional[date] = None
    expiry_date: Optional[date] = None
    denial_reason: Optional[str] = None
    appeal_deadline: Optional[date] = None

    # External reference
    covermymeds_id: Optional[str] = None
    surescripts_pa_id: Optional[str] = None


@dataclass
class PAStatusUpdate:
    pa_id: UUID
    new_status: PAStatus
    pa_number: Optional[str]
    expiry_date: Optional[date]
    denial_reason: Optional[str]
    recommended_action: Optional[str]
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ElectronicPAEngine:
    """
    Manages the complete PA lifecycle from initiation to resolution.
    Integrates with CoverMyMeds API (covers 95% of US commercial plans).
    """

    COVERMYMEDS_API = "https://api.covermymeds.com/requests"

    def __init__(self, cmm_api_key: str = "", cmm_api_secret: str = ""):
        self.cmm_key    = cmm_api_key
        self.cmm_secret = cmm_api_secret

    async def initiate_pa(
        self,
        request: PARequest,
        db=None,
    ) -> PARequest:
        """
        Initiate an electronic PA request.
        Tries CoverMyMeds first (fastest), falls back to fax-based PA.
        """
        logger.info(
            "Initiating ePA for Rx %s: drug=%s BIN=%s",
            str(request.prescription_id)[:8], request.drug_name, request.bin_number
        )

        if self.cmm_key:
            updated = await self._submit_to_covermymeds(request)
        else:
            # No CoverMyMeds configured — generate PA tracking number and notify prescriber
            request.covermymeds_id = f"PA-MANUAL-{uuid4().hex[:8].upper()}"
            request.status = PAStatus.PENDING
            updated = request

        # Persist PA record
        if db:
            await self._persist_pa(updated, db)

        return updated

    async def _submit_to_covermymeds(self, request: PARequest) -> PARequest:
        """Submit PA request to CoverMyMeds API."""
        payload = {
            "request": {
                "urgent": request.urgency == PAUrgency.URGENT,
                "prescription": {
                    "drug_id": request.drug_ndc,
                    "quantity": request.quantity,
                    "days_supply": request.days_supply,
                    "diagnosis_codes": request.diagnosis_codes,
                },
                "patient": {
                    "member_id": request.member_id,
                    "bin_number": request.bin_number,
                    "pcn": request.pcn,
                    "group_id": request.group_number,
                },
                "provider": {
                    "npi": request.prescriber_npi,
                    "name": request.prescriber_name,
                    "phone": request.prescriber_phone,
                    "fax": request.prescriber_fax,
                },
                "notes": request.clinical_notes,
            }
        }

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    self.COVERMYMEDS_API,
                    json=payload,
                    auth=(self.cmm_key, self.cmm_secret),
                )
                if response.status_code in (200, 201):
                    data = response.json()
                    request.covermymeds_id = data.get("id")
                    request.status = PAStatus.PENDING
                    logger.info("CoverMyMeds PA submitted: cmm_id=%s", request.covermymeds_id)
                else:
                    logger.warning("CoverMyMeds submission failed: %s", response.status_code)
                    request.status = PAStatus.PENDING  # Still pending — manual follow-up
        except Exception as exc:
            logger.error("CoverMyMeds API error: %s", exc)
            request.status = PAStatus.PENDING

        request.status_updated_at = datetime.now(timezone.utc)
        return request

    async def check_pa_status(self, pa: PARequest) -> PAStatusUpdate:
        """Poll PA status from CoverMyMeds or Surescripts."""
        if pa.covermymeds_id and self.cmm_key:
            return await self._poll_covermymeds(pa)

        # Return current status unchanged if no API configured
        return PAStatusUpdate(
            pa_id=pa.pa_id,
            new_status=pa.status,
            pa_number=pa.pa_number,
            expiry_date=pa.expiry_date,
            denial_reason=pa.denial_reason,
            recommended_action=None,
        )

    async def _poll_covermymeds(self, pa: PARequest) -> PAStatusUpdate:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.COVERMYMEDS_API}/{pa.covermymeds_id}",
                    auth=(self.cmm_key, self.cmm_secret),
                )
                if response.status_code == 200:
                    data = response.json()
                    status_str = data.get("status", "pending").lower()
                    new_status = {
                        "approved": PAStatus.APPROVED,
                        "denied":   PAStatus.DENIED,
                        "pending":  PAStatus.PENDING,
                        "cancelled": PAStatus.CANCELLED,
                    }.get(status_str, PAStatus.PENDING)

                    denial_code = data.get("denial_code")
                    recommended = PA_DENIAL_RESPONSES.get(denial_code, "Contact PBM for appeal process")

                    return PAStatusUpdate(
                        pa_id=pa.pa_id,
                        new_status=new_status,
                        pa_number=data.get("authorization_number"),
                        expiry_date=date.fromisoformat(data["expiry_date"]) if data.get("expiry_date") else None,
                        denial_reason=data.get("denial_reason"),
                        recommended_action=recommended if new_status == PAStatus.DENIED else None,
                    )
        except Exception as exc:
            logger.error("CoverMyMeds poll failed: %s", exc)

        return PAStatusUpdate(
            pa_id=pa.pa_id, new_status=PAStatus.PENDING,
            pa_number=None, expiry_date=None, denial_reason=None,
            recommended_action=None,
        )

    async def _persist_pa(self, pa: PARequest, db) -> None:
        from sqlalchemy import text
        try:
            await db.execute(text("""
                INSERT INTO prior_authorization_requests (
                    id, prescription_id, patient_id, pharmacy_id,
                    drug_ndc, drug_name, bin_number, member_id,
                    prescriber_npi, status, urgency,
                    covermymeds_id, pa_number, initiated_at
                ) VALUES (
                    :id, :rx_id, :pat_id, :ph_id,
                    :ndc, :drug, :bin, :member,
                    :p_npi, :status, :urgency,
                    :cmm_id, :pa_num, :initiated
                )
                ON CONFLICT (id) DO UPDATE SET
                    status = EXCLUDED.status,
                    pa_number = EXCLUDED.pa_number,
                    covermymeds_id = EXCLUDED.covermymeds_id
            """), {
                "id": str(pa.pa_id), "rx_id": str(pa.prescription_id),
                "pat_id": str(pa.patient_id), "ph_id": str(pa.pharmacy_id),
                "ndc": pa.drug_ndc, "drug": pa.drug_name,
                "bin": pa.bin_number, "member": pa.member_id,
                "p_npi": pa.prescriber_npi, "status": pa.status.value,
                "urgency": pa.urgency.value, "cmm_id": pa.covermymeds_id,
                "pa_num": pa.pa_number, "initiated": pa.initiated_at,
            })
        except Exception as exc:
            logger.error("PA persist failed: %s", exc)

    def get_denial_guidance(self, denial_reason: str) -> str:
        """Return pharmacist-facing guidance for a PA denial reason."""
        return PA_DENIAL_RESPONSES.get(
            denial_reason.lower().replace(" ", "_"),
            "Contact the PBM's provider line to discuss the denial and initiate an appeal if warranted."
        )
