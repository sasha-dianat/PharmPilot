"""
MTM (Medication Therapy Management) Engine
==========================================
CPT billing codes: 99605 (new, 15 min), 99606 (follow-up, 15 min), 99607 (+15 min add-on).
CMS requires: eligible patient identification, session timer, MAP + PMR documentation,
and a minimum time requirement before the CPT code is valid.

Eligible patients (per CMS Star Ratings):
  - 2+ chronic conditions
  - 8+ medications
  - Annual drug cost likely to exceed the CMS threshold (~$5,000/year)
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)

# CPT code time requirements
CPT_REQUIREMENTS = {
    "99605": {"description": "MTM — Initial visit, 15 min minimum",      "min_minutes": 15, "is_addon": False},
    "99606": {"description": "MTM — Follow-up visit, 15 min minimum",    "min_minutes": 15, "is_addon": False},
    "99607": {"description": "MTM — Add-on, each additional 15 min",     "min_minutes": 15, "is_addon": True},
}

# CMS MTM eligibility criteria
MTM_ELIGIBILITY_MIN_MEDICATIONS     = 8
MTM_ELIGIBILITY_MIN_CONDITIONS      = 2
MTM_ELIGIBILITY_ANNUAL_COST_MINIMUM = 5_000.0


@dataclass
class MTMEligibilityResult:
    patient_id: UUID
    is_eligible: bool
    medication_count: int
    condition_count: int
    estimated_annual_drug_cost: float
    reasons_eligible: list[str]
    reasons_ineligible: list[str]
    recommended_cpt: str
    last_cmr_date: Optional[date] = None
    days_since_last_cmr: Optional[int] = None


@dataclass
class MTMSessionDocument:
    """
    The CMS-required documentation for a billable MTM session.
    Both MAP (Medication Action Plan) and PMR (Personal Medication Record)
    must be generated for a CMR (Comprehensive Medication Review).
    """
    session_id: UUID = field(default_factory=uuid4)
    patient_id: UUID = field(default_factory=uuid4)
    pharmacist_id: UUID = field(default_factory=uuid4)
    session_type: str = "cmr"           # cmr | targeted | follow_up
    cpt_code: str = "99606"

    # Timing (CMS requires documented duration)
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_minutes: Optional[float] = None

    # PMR — Personal Medication Record (given to patient)
    pmr_medications: list[dict] = field(default_factory=list)
    # [{name, dose, freq, indication, prescriber, start_date}]

    # MAP — Medication Action Plan (pharmacist recommendations)
    map_action_items: list[dict] = field(default_factory=list)
    # [{priority, description, patient_goal, pharmacist_action, timeline}]

    # Clinical findings
    allergies_reviewed: bool = False
    adherence_issues_identified: list[str] = field(default_factory=list)
    drug_therapy_problems: list[dict] = field(default_factory=list)
    # [{type, description, drug, recommendation}]

    # Billing validation
    billing_validated: bool = False
    billing_validation_errors: list[str] = field(default_factory=list)

    def validate_for_billing(self) -> bool:
        """CMS requires specific documentation before the session can be billed."""
        errors = []
        if not self.started_at or not self.ended_at:
            errors.append("Session start and end times must be documented")
        if self.duration_minutes and self.duration_minutes < 15:
            errors.append(f"Session duration ({self.duration_minutes:.0f} min) below 15-min CPT minimum")
        if not self.pmr_medications:
            errors.append("Personal Medication Record (PMR) must be completed")
        if not self.map_action_items:
            errors.append("Medication Action Plan (MAP) must have at least one action item")
        if not self.allergies_reviewed:
            errors.append("Allergy review must be documented")
        self.billing_validation_errors = errors
        self.billing_validated = len(errors) == 0
        return self.billing_validated

    def calculate_cpt_codes(self) -> list[str]:
        """Determine which CPT codes apply based on session type and duration."""
        if not self.duration_minutes:
            return []
        codes = []
        base_code = "99605" if self.session_type == "cmr" else "99606"
        codes.append(base_code)
        # Add-on: each additional 15 minutes
        additional_blocks = int((self.duration_minutes - 15) / 15)
        for _ in range(min(additional_blocks, 3)):  # Max 3 add-on codes
            codes.append("99607")
        return codes


class MTMEligibilityEngine:
    """Identifies patients eligible for MTM services."""

    def __init__(self, db=None):
        self.db = db

    async def identify_eligible_patients(
        self,
        pharmacy_id: UUID,
        limit: int = 50,
    ) -> list[MTMEligibilityResult]:
        """Find all MTM-eligible patients at this pharmacy."""
        if not self.db:
            return []

        from sqlalchemy import text
        result = await self.db.execute(text("""
            SELECT
                p.id AS patient_id,
                COUNT(DISTINCT pr.id) FILTER (WHERE pr.status = 'dispensed') AS rx_count,
                COUNT(DISTINCT pr.ndc) AS unique_drugs,
                COALESCE(SUM(ct.total_amount_paid), 0) AS annual_cost
            FROM patients p
            LEFT JOIN prescriptions pr ON pr.patient_id = p.id
                AND pr.fill_date >= CURRENT_DATE - INTERVAL '12 months'
            LEFT JOIN prescription_fills pf ON pf.prescription_id = pr.id
            LEFT JOIN claim_transactions ct ON ct.fill_id = pf.id
                AND ct.status = 'approved'
            WHERE p.pharmacy_id = :pharmacy_id AND p.is_deleted = false
            GROUP BY p.id
            HAVING COUNT(DISTINCT pr.ndc) >= :min_meds
            ORDER BY COUNT(DISTINCT pr.ndc) DESC
            LIMIT :limit
        """), {
            "pharmacy_id": str(pharmacy_id),
            "min_meds": MTM_ELIGIBILITY_MIN_MEDICATIONS,
            "limit": limit,
        })

        eligible = []
        for row in result.mappings().all():
            reasons_eligible = []
            reasons_ineligible = []
            is_eligible = True

            if row["unique_drugs"] >= MTM_ELIGIBILITY_MIN_MEDICATIONS:
                reasons_eligible.append(f"{row['unique_drugs']} medications (threshold: {MTM_ELIGIBILITY_MIN_MEDICATIONS})")
            else:
                reasons_ineligible.append(f"Only {row['unique_drugs']} medications (need {MTM_ELIGIBILITY_MIN_MEDICATIONS})")
                is_eligible = False

            if float(row["annual_cost"]) >= MTM_ELIGIBILITY_ANNUAL_COST_MINIMUM:
                reasons_eligible.append(f"Annual drug cost ~${row['annual_cost']:,.0f}")
            else:
                reasons_ineligible.append(f"Annual cost ${row['annual_cost']:,.0f} below ${MTM_ELIGIBILITY_ANNUAL_COST_MINIMUM:,.0f} threshold")

            eligible.append(MTMEligibilityResult(
                patient_id=row["patient_id"],
                is_eligible=is_eligible,
                medication_count=row["unique_drugs"],
                condition_count=0,  # Would need diagnoses table
                estimated_annual_drug_cost=float(row["annual_cost"]),
                reasons_eligible=reasons_eligible,
                reasons_ineligible=reasons_ineligible,
                recommended_cpt="99605",
            ))

        logger.info("MTM eligibility scan: %d eligible patients found at pharmacy %s",
                    len([e for e in eligible if e.is_eligible]), str(pharmacy_id)[:8])
        return eligible
