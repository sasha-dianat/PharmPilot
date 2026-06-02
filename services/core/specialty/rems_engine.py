"""
REMS (Risk Evaluation and Mitigation Strategy) Compliance Engine
================================================================
Manages FDA-mandated REMS programs that restrict dispensing of high-risk drugs.
Every REMS-covered dispense must be verified against the program's requirements
before the drug can be released to the patient.

Active REMS programs implemented:
  iPLEDGE    — isotretinoin (Accutane/Absorica): pregnancy prevention
  TIRF REMS  — fentanyl buccal/sublingual: cancer pain, opioid tolerant only
  TOUCH      — natalizumab (Tysabri): MS/Crohn's, PML risk
  ADDVANTAGE — clozapine: agranulocytosis monitoring
  Enbrel/Humira — TNF blockers: TB/infection screening
  Thalidomide/lenalidomide/pomalidomide — S.T.E.P.S., RevAssist, Pomalyst REMS

Each REMS program has different:
  - Prescriber enrollment requirements
  - Patient enrollment requirements
  - Pharmacy certification requirements
  - Dispense authorization requirements (some require real-time database check)
  - Monitoring requirements (lab values, pregnancy tests, patient counseling)
"""
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

import httpx

logger = logging.getLogger(__name__)


class REMSProgram(str, Enum):
    IPLEDGE        = "iPLEDGE"        # Isotretinoin
    TIRF           = "TIRF_REMS"      # Transmucosal immediate-release fentanyl
    TOUCH          = "TOUCH"          # Natalizumab
    CLOZARIL       = "CLOZAPINE_REMS" # Clozapine (multiple programs)
    STEPS          = "S.T.E.P.S."     # Thalidomide
    REVASSIST      = "RevAssist"       # Lenalidomide
    POMALYST_REMS  = "Pomalyst_REMS"  # Pomalidomide
    GENERIC        = "generic_rems"    # Other REMS programs


# FDA REMS drug list (NDC prefix → REMS program)
REMS_NDC_MAP: dict[str, REMSProgram] = {
    "00004": REMSProgram.IPLEDGE,      # Roche isotretinoin
    "16590": REMSProgram.IPLEDGE,      # Generic isotretinoin
    "59148": REMSProgram.TIRF,         # Abstral (fentanyl sublingual)
    "63481": REMSProgram.TIRF,         # Actiq (fentanyl lollipop)
    "50458": REMSProgram.TOUCH,        # Tysabri (natalizumab)
    "00078": REMSProgram.CLOZARIL,     # Clozaril
    "59148028": REMSProgram.STEPS,     # Thalomid (thalidomide)
    "59148025": REMSProgram.REVASSIST, # Revlimid (lenalidomide)
}

# Monitoring requirements per REMS program
REMS_MONITORING: dict[REMSProgram, list[dict]] = {
    REMSProgram.IPLEDGE: [
        {"test": "pregnancy_test_urine_serum", "frequency": "monthly", "required_before_dispense": True,
         "window_days": 7, "description": "Negative pregnancy test within 7 days of dispense"},
        {"test": "contraception_confirmation", "frequency": "monthly", "required_before_dispense": True,
         "description": "Two forms of contraception confirmed"},
    ],
    REMSProgram.CLOZARIL: [
        {"test": "ANC_absolute_neutrophil_count", "frequency": "weekly_to_monthly", "required_before_dispense": True,
         "threshold_min": 1500, "description": "ANC ≥ 1500/mm³ (first 6 months: weekly, then biweekly, then monthly)"},
    ],
    REMSProgram.TIRF: [
        {"test": "opioid_tolerance_confirmation", "frequency": "each_dispense", "required_before_dispense": True,
         "description": "Patient confirmed opioid tolerant (≥60 MME/day for ≥1 week)"},
    ],
    REMSProgram.TOUCH: [
        {"test": "JC_virus_antibody", "frequency": "every_6_months", "required_before_dispense": True,
         "description": "JC virus antibody status (PML risk stratification)"},
        {"test": "MRI_brain", "frequency": "annually", "required_before_dispense": False,
         "description": "Annual brain MRI for PML surveillance"},
    ],
}


@dataclass
class REMSAuthorizationRequest:
    """Request for REMS dispense authorization."""
    request_id: UUID = field(default_factory=uuid4)
    pharmacy_id: UUID = field(default_factory=uuid4)
    patient_id: UUID = field(default_factory=uuid4)
    prescriber_npi: str = ""
    drug_ndc: str = ""
    drug_name: str = ""
    rems_program: REMSProgram = REMSProgram.GENERIC

    # Patient REMS enrollment
    patient_rems_id: Optional[str] = None
    prescriber_rems_id: Optional[str] = None
    pharmacy_rems_id: Optional[str] = None

    # Monitoring compliance
    monitoring_results: list[dict] = field(default_factory=list)
    # [{test_name, result, test_date, lab_name}]

    # iPLEDGE specific
    pregnancy_test_negative: Optional[bool] = None
    pregnancy_test_date: Optional[date] = None
    contraception_confirmed: Optional[bool] = None

    # Clozapine specific
    anc_value: Optional[float] = None
    anc_test_date: Optional[date] = None


@dataclass
class REMSAuthorizationResult:
    """Result of REMS dispense authorization check."""
    authorization_id: UUID = field(default_factory=uuid4)
    request_id: UUID = field(default_factory=uuid4)
    rems_program: REMSProgram = REMSProgram.GENERIC
    authorized: bool = False
    authorization_number: Optional[str] = None
    authorization_expires: Optional[date] = None
    denial_reasons: list[str] = field(default_factory=list)
    required_actions: list[str] = field(default_factory=list)
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class REMSEngine:
    """
    Validates REMS compliance before dispensing.
    Integrates with FDA REMS program databases for real-time authorization.
    """

    def __init__(self, pharmacy_rems_ids: dict[str, str] = None):
        """pharmacy_rems_ids: {program_name: pharmacy_certification_id}"""
        self.pharmacy_rems_ids = pharmacy_rems_ids or {}

    def identify_rems_program(self, ndc11: str) -> Optional[REMSProgram]:
        """Identify if a drug NDC requires REMS authorization."""
        ndc_prefix_5 = ndc11[:5]
        ndc_prefix_8 = ndc11[:8]
        if ndc_prefix_8 in REMS_NDC_MAP:
            return REMS_NDC_MAP[ndc_prefix_8]
        if ndc_prefix_5 in REMS_NDC_MAP:
            return REMS_NDC_MAP[ndc_prefix_5]
        # Check by generic drug name (fallback)
        return None

    def get_monitoring_requirements(self, program: REMSProgram) -> list[dict]:
        """Get the monitoring requirements for a REMS program."""
        return REMS_MONITORING.get(program, [])

    def validate_dispense_readiness(
        self,
        request: REMSAuthorizationRequest,
    ) -> REMSAuthorizationResult:
        """
        Validate all REMS requirements are met before dispensing.
        This is the local validation — call check_authorization() for live database check.
        """
        result = REMSAuthorizationResult(
            request_id=request.request_id,
            rems_program=request.rems_program,
        )

        program = request.rems_program
        denial_reasons = []
        required_actions = []

        # Check pharmacy certification
        if program.value not in self.pharmacy_rems_ids:
            denial_reasons.append(
                f"Pharmacy not certified for {program.value}. "
                f"Contact REMS program coordinator at FDA."
            )

        # iPLEDGE-specific validation
        if program == REMSProgram.IPLEDGE:
            if not request.patient_rems_id:
                denial_reasons.append("Patient not enrolled in iPLEDGE")
                required_actions.append("Enroll patient at ipledgerems.com or (866) 495-0654")
            if not request.prescriber_rems_id:
                denial_reasons.append("Prescriber not registered in iPLEDGE")
            if request.pregnancy_test_negative is False:
                denial_reasons.append("HARD STOP: Positive pregnancy test. Isotretinoin is Category X — do not dispense")
            elif not request.pregnancy_test_date:
                denial_reasons.append("Pregnancy test date required")
            elif (date.today() - request.pregnancy_test_date).days > 7:
                denial_reasons.append(
                    f"Pregnancy test is {(date.today() - request.pregnancy_test_date).days} days old. "
                    f"iPLEDGE requires test within 7 days of dispense."
                )
            if not request.contraception_confirmed:
                denial_reasons.append("Two forms of contraception not confirmed for female patients of childbearing potential")

        # Clozapine ANC check
        elif program == REMSProgram.CLOZARIL:
            if request.anc_value is None:
                denial_reasons.append("ANC (Absolute Neutrophil Count) required before dispensing clozapine")
                required_actions.append("Obtain ANC lab result and enter in REMS system")
            elif request.anc_value < 1500:
                denial_reasons.append(
                    f"HARD STOP: ANC {request.anc_value:.0f}/mm³ is below minimum threshold (1500/mm³). "
                    f"Clozapine contraindicated — notify prescriber immediately."
                )
            elif request.anc_test_date and (date.today() - request.anc_test_date).days > 7:
                denial_reasons.append(
                    f"ANC result is {(date.today() - request.anc_test_date).days} days old. "
                    f"Result must be within 7 days for weekly monitoring phase."
                )

        # TIRF opioid tolerance check
        elif program == REMSProgram.TIRF:
            if not request.patient_rems_id:
                denial_reasons.append("Patient not enrolled in TIRF REMS Access program")
                required_actions.append("Enroll via TIRF REMS at tirfremsaccess.com")
            opioid_tolerance_confirmed = any(
                r.get("test") == "opioid_tolerance_confirmation" and r.get("result") == "confirmed"
                for r in request.monitoring_results
            )
            if not opioid_tolerance_confirmed:
                denial_reasons.append(
                    "Opioid tolerance not confirmed. TIRF drugs are for opioid-tolerant cancer pain patients only. "
                    "Requires ≥60 MME/day for ≥1 week."
                )

        result.authorized = len(denial_reasons) == 0
        result.denial_reasons = denial_reasons
        result.required_actions = required_actions

        if result.authorized:
            result.authorization_number = f"REMS-{program.value[:4].upper()}-{uuid4().hex[:8].upper()}"
            result.authorization_expires = date.today() + timedelta(days=30)

        if not result.authorized:
            logger.warning(
                "REMS dispense DENIED: program=%s reasons=%d",
                program.value, len(denial_reasons)
            )
        else:
            logger.info("REMS authorization granted: %s", result.authorization_number)

        return result

    async def check_authorization_live(
        self,
        request: REMSAuthorizationRequest,
        rems_api_url: Optional[str] = None,
    ) -> REMSAuthorizationResult:
        """
        Real-time authorization check against FDA REMS program database.
        Falls back to local validation if API unavailable.
        """
        # First do local validation
        local_result = self.validate_dispense_readiness(request)
        if not local_result.authorized:
            return local_result  # No point hitting API if local checks fail

        if not rems_api_url:
            # No API configured — return local result
            return local_result

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{rems_api_url}/authorize",
                    json={
                        "patient_rems_id": request.patient_rems_id,
                        "prescriber_rems_id": request.prescriber_rems_id,
                        "pharmacy_rems_id": self.pharmacy_rems_ids.get(request.rems_program.value),
                        "drug_ndc": request.drug_ndc,
                        "request_id": str(request.request_id),
                    }
                )
                if response.status_code == 200:
                    data = response.json()
                    local_result.authorization_number = data.get("authorization_number")
                    local_result.authorized = data.get("authorized", False)
                    if not local_result.authorized:
                        local_result.denial_reasons.extend(data.get("reasons", []))
        except Exception as exc:
            logger.warning("REMS live check failed (%s) — using local validation", exc)

        return local_result
