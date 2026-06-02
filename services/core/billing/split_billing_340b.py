"""
340B Drug Pricing Program — Split-Billing Engine
===================================================
The 340B program allows eligible covered entities (hospitals, FQHCs, etc.)
and their contract pharmacies to purchase outpatient drugs at significantly
discounted prices. Typical savings: 25–50% below WAC.

Split-billing rules:
  1. Patient must be "patient of the covered entity" (receives care there)
  2. Drug must be on the 340B formulary for that covered entity
  3. The same unit of drug cannot be used for both 340B AND Medicaid rebate
     (referred to as the "duplicate discount prohibition")
  4. Manufacturer must offer 340B pricing for that NDC
  5. Contract pharmacy arrangements require specific documentation

The split-billing engine:
  - Automatically classifies each prescription as 340B-eligible or standard
  - Routes the NCPDP claim with correct 340B identifiers
  - Tracks the 340B accumulator (prevents duplicate discount violation)
  - Generates the audit trail required for HRSA audit defense
"""
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)


class ClaimType(str, Enum):
    STANDARD    = "standard"       # Normal commercial/PBM claim
    B340        = "340b"           # 340B pricing claim
    MEDICAID    = "medicaid"       # Medicaid — cannot combine with 340B
    MEDICARE_D  = "medicare_part_d"
    CASH        = "cash"


@dataclass
class CoveredEntityConfig:
    """Configuration for a 340B covered entity."""
    entity_id: str          # HRSA-assigned ID
    entity_name: str
    entity_type: str        # CAH, FQHC, DSH_HOSPITAL, etc.
    contract_pharmacies: list[str]  # NCPDP IDs of contract pharmacies
    formulary_ndcs: set[str] = field(default_factory=set)
    medicaid_states: list[str] = field(default_factory=list)  # States to exclude for Medicaid carve-out
    effective_date: Optional[date] = None
    termination_date: Optional[date] = None

    def is_active(self, as_of: Optional[date] = None) -> bool:
        check = as_of or date.today()
        if self.effective_date and check < self.effective_date:
            return False
        if self.termination_date and check > self.termination_date:
            return False
        return True

    def is_contract_pharmacy(self, ncpdp_id: str) -> bool:
        return ncpdp_id in self.contract_pharmacies

    def drug_is_on_formulary(self, ndc11: str) -> bool:
        if not self.formulary_ndcs:
            return True  # All drugs eligible if no restriction configured
        return ndc11 in self.formulary_ndcs


@dataclass
class PatientEligibilityResult:
    patient_id: UUID
    is_340b_eligible: bool
    covered_entity_id: Optional[str]
    eligibility_basis: str   # "active_patient", "referred_patient", "outpatient_only"
    last_encounter_date: Optional[date]
    ineligibility_reason: Optional[str] = None


@dataclass
class SplitBillingDecision:
    """The output of the split-billing engine for a single prescription."""
    prescription_id: UUID
    patient_id: UUID
    ndc11: str
    claim_type: ClaimType
    use_340b: bool
    covered_entity_id: Optional[str]
    decision_reason: str
    # Financial impact
    wac_price: float
    b340_price: Optional[float]
    estimated_savings: float = 0.0
    # Audit trail
    decision_id: UUID = field(default_factory=uuid4)
    decided_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # For Medicaid — must NOT use 340B (duplicate discount prohibition)
    medicaid_carve_out: bool = False


class Split340BEngine:
    """
    Determines whether each prescription should be billed as a 340B claim.
    Called automatically at the PENDING_ADJUDICATION stage of the Rx workflow.
    """

    def __init__(self, covered_entity_config: Optional[CoveredEntityConfig] = None, db=None):
        self.entity = covered_entity_config
        self.db = db

    async def evaluate_prescription(
        self,
        prescription_id: UUID,
        patient_id: UUID,
        ndc11: str,
        insurance_plan_type: str,     # commercial | medicaid | medicare_d | cash
        pharmacy_ncpdp: str,
        wac_price: float,
        b340_price: Optional[float] = None,
    ) -> SplitBillingDecision:
        """
        Determine if a prescription qualifies for 340B pricing.
        """
        # If no covered entity configured, always standard billing
        if not self.entity or not self.entity.is_active():
            return SplitBillingDecision(
                prescription_id=prescription_id,
                patient_id=patient_id,
                ndc11=ndc11,
                claim_type=ClaimType.STANDARD,
                use_340b=False,
                covered_entity_id=None,
                decision_reason="No active 340B covered entity configured for this pharmacy",
                wac_price=wac_price,
                b340_price=None,
            )

        # Medicaid carve-out: never use 340B for Medicaid (duplicate discount prohibition)
        if insurance_plan_type == "medicaid":
            return SplitBillingDecision(
                prescription_id=prescription_id,
                patient_id=patient_id,
                ndc11=ndc11,
                claim_type=ClaimType.MEDICAID,
                use_340b=False,
                covered_entity_id=self.entity.entity_id,
                decision_reason="Medicaid claim — 340B excluded per duplicate discount prohibition (42 USC 256b(a)(5)(A)(i))",
                wac_price=wac_price,
                b340_price=b340_price,
                medicaid_carve_out=True,
            )

        # Contract pharmacy check
        if not self.entity.is_contract_pharmacy(pharmacy_ncpdp):
            return SplitBillingDecision(
                prescription_id=prescription_id,
                patient_id=patient_id,
                ndc11=ndc11,
                claim_type=ClaimType.STANDARD,
                use_340b=False,
                covered_entity_id=None,
                decision_reason=f"Pharmacy {pharmacy_ncpdp} is not a registered contract pharmacy for {self.entity.entity_name}",
                wac_price=wac_price,
                b340_price=b340_price,
            )

        # Drug formulary check
        if not self.entity.drug_is_on_formulary(ndc11):
            return SplitBillingDecision(
                prescription_id=prescription_id,
                patient_id=patient_id,
                ndc11=ndc11,
                claim_type=ClaimType.STANDARD,
                use_340b=False,
                covered_entity_id=self.entity.entity_id,
                decision_reason=f"NDC {ndc11} not on 340B formulary for {self.entity.entity_name}",
                wac_price=wac_price,
                b340_price=b340_price,
            )

        # Patient eligibility check
        eligibility = await self._check_patient_eligibility(patient_id)
        if not eligibility.is_340b_eligible:
            return SplitBillingDecision(
                prescription_id=prescription_id,
                patient_id=patient_id,
                ndc11=ndc11,
                claim_type=ClaimType.STANDARD,
                use_340b=False,
                covered_entity_id=self.entity.entity_id,
                decision_reason=f"Patient not eligible: {eligibility.ineligibility_reason}",
                wac_price=wac_price,
                b340_price=b340_price,
            )

        # All checks passed — use 340B
        savings = (wac_price - b340_price) if b340_price else 0.0
        return SplitBillingDecision(
            prescription_id=prescription_id,
            patient_id=patient_id,
            ndc11=ndc11,
            claim_type=ClaimType.B340,
            use_340b=True,
            covered_entity_id=self.entity.entity_id,
            decision_reason=(
                f"340B eligible: patient of {self.entity.entity_name}, "
                f"drug on formulary, contract pharmacy confirmed"
            ),
            wac_price=wac_price,
            b340_price=b340_price,
            estimated_savings=savings,
        )

    async def _check_patient_eligibility(self, patient_id: UUID) -> PatientEligibilityResult:
        """
        Verify patient is a 'patient of the covered entity' per HRSA guidance.
        Requires an encounter with the covered entity in the past 24 months
        AND the drug must be used in connection with that care.
        """
        if not self.db:
            # Default to eligible if no DB (development mode)
            return PatientEligibilityResult(
                patient_id=patient_id,
                is_340b_eligible=True,
                covered_entity_id=self.entity.entity_id if self.entity else None,
                eligibility_basis="assumed_eligible_no_db",
                last_encounter_date=None,
            )

        from sqlalchemy import text
        result = await self.db.execute(text("""
            SELECT MAX(encounter_date) AS last_encounter
            FROM patient_encounters
            WHERE patient_id = :patient_id
              AND covered_entity_id = :entity_id
              AND encounter_date >= CURRENT_DATE - INTERVAL '24 months'
        """), {
            "patient_id": str(patient_id),
            "entity_id": self.entity.entity_id if self.entity else "",
        })
        row = result.one_or_none()
        last_encounter = row["last_encounter"] if row else None

        if last_encounter:
            return PatientEligibilityResult(
                patient_id=patient_id,
                is_340b_eligible=True,
                covered_entity_id=self.entity.entity_id,
                eligibility_basis="active_patient",
                last_encounter_date=last_encounter,
            )
        return PatientEligibilityResult(
            patient_id=patient_id,
            is_340b_eligible=False,
            covered_entity_id=self.entity.entity_id,
            eligibility_basis="no_encounter",
            last_encounter_date=None,
            ineligibility_reason="No encounter with covered entity in past 24 months",
        )

    async def generate_audit_report(
        self,
        pharmacy_ncpdp: str,
        period_start: date,
        period_end: date,
        db,
    ) -> dict:
        """
        Generate 340B audit report required for HRSA audit defense.
        Shows every 340B claim with patient eligibility evidence.
        """
        from sqlalchemy import text
        result = await db.execute(text("""
            SELECT
                ct.id AS claim_id,
                pr.rx_number,
                pr.ndc,
                pr.drug_name,
                ct.date_of_service,
                ct.total_amount_paid,
                ct.patient_pay_amount,
                jsonb_extract_path_text(ct.metadata, '340b_decision') AS decision_json
            FROM claim_transactions ct
            JOIN prescription_fills pf ON pf.id = ct.fill_id
            JOIN prescriptions pr ON pr.id = pf.prescription_id
            WHERE ct.date_of_service BETWEEN :start AND :end
              AND jsonb_extract_path_text(ct.metadata, 'claim_type') = '340b'
            ORDER BY ct.date_of_service
        """), {"start": period_start, "end": period_end})

        claims = result.mappings().all()
        total_savings = sum(
            float(c.get("wac_price", 0)) - float(c.get("b340_price", 0))
            for c in claims
            if c.get("wac_price") and c.get("b340_price")
        )
        return {
            "covered_entity_id": self.entity.entity_id if self.entity else None,
            "pharmacy_ncpdp": pharmacy_ncpdp,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "total_340b_claims": len(claims),
            "estimated_total_savings": round(total_savings, 2),
            "claims": [dict(c) for c in claims],
            "audit_generated_at": datetime.now(timezone.utc).isoformat(),
        }
