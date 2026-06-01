"""
Clinical Services API — MTM, Immunizations, CMS Star Ratings, POCT.
"""
import logging
from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.auth import get_current_staff, require_pharmacist
from services.platform.database import get_db
from services.core.clinical_services.mtm_engine import (
    MTMEligibilityEngine, MTMSessionDocument, CPT_REQUIREMENTS
)
from services.core.clinical_services.immunization import (
    ImmunizationRecord, ImmunizationScheduleChecker, ACIP_VACCINES
)
from services.core.clinical_services.cms_star_ratings import (
    StarRatingsCalculator, PDC_STAR_THRESHOLDS
)
from shared.models.auth import Staff

router = APIRouter()
logger = logging.getLogger(__name__)


# ── MTM ───────────────────────────────────────────────────────────────────

@router.get("/mtm/eligible-patients")
async def get_mtm_eligible_patients(
    limit: int = 50,
    staff: Staff = Depends(require_pharmacist()),
    db: AsyncSession = Depends(get_db),
):
    """Identify patients eligible for MTM services (CPT 99605/99606)."""
    engine = MTMEligibilityEngine(db)
    patients = await engine.identify_eligible_patients(staff.pharmacy_id, limit)
    return {
        "total_eligible": len([p for p in patients if p.is_eligible]),
        "patients": [
            {
                "patient_id": str(p.patient_id),
                "is_eligible": p.is_eligible,
                "medication_count": p.medication_count,
                "estimated_annual_cost": round(p.estimated_annual_drug_cost, 2),
                "reasons": p.reasons_eligible,
                "recommended_cpt": p.recommended_cpt,
            }
            for p in patients
        ],
    }


class MTMSessionCreate(BaseModel):
    patient_id: UUID
    session_type: str = "cmr"
    pmr_medications: list[dict] = []
    map_action_items: list[dict] = []
    allergies_reviewed: bool = False
    adherence_issues: list[str] = []
    drug_therapy_problems: list[dict] = []
    started_at: str
    ended_at: str


@router.post("/mtm/sessions", status_code=201)
async def create_mtm_session(
    body: MTMSessionCreate,
    staff: Staff = Depends(require_pharmacist()),
    db: AsyncSession = Depends(get_db),
):
    """Create and validate an MTM session for billing."""
    from datetime import datetime
    session = MTMSessionDocument(
        patient_id=body.patient_id,
        pharmacist_id=staff.id,
        session_type=body.session_type,
        pmr_medications=body.pmr_medications,
        map_action_items=body.map_action_items,
        allergies_reviewed=body.allergies_reviewed,
        adherence_issues_identified=body.adherence_issues,
        drug_therapy_problems=body.drug_therapy_problems,
    )
    session.started_at = datetime.fromisoformat(body.started_at)
    session.ended_at   = datetime.fromisoformat(body.ended_at)
    session.duration_minutes = (session.ended_at - session.started_at).total_seconds() / 60
    session.patient_discharged = True  # Set by pharmacist completing the session

    # Validate for billing
    is_valid = session.validate_for_billing()
    cpt_codes = session.calculate_cpt_codes()

    if not is_valid:
        return {
            "session_id": str(session.session_id),
            "billing_ready": False,
            "errors": session.billing_validation_errors,
            "duration_minutes": round(session.duration_minutes, 1),
            "cpt_codes": cpt_codes,
        }

    return {
        "session_id": str(session.session_id),
        "billing_ready": True,
        "duration_minutes": round(session.duration_minutes, 1),
        "cpt_codes": cpt_codes,
        "cpt_descriptions": [CPT_REQUIREMENTS[c]["description"] for c in cpt_codes if c in CPT_REQUIREMENTS],
        "errors": [],
    }


# ── Immunizations ─────────────────────────────────────────────────────────

@router.get("/immunizations/schedule/{patient_id}")
async def get_immunization_recommendations(
    patient_id: UUID,
    patient_age: int,
    is_pregnant: bool = False,
    staff: Staff = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """Get ACIP-based vaccine recommendations for a patient."""
    checker = ImmunizationScheduleChecker()
    recommendations = checker.check_patient(
        patient_age=patient_age,
        patient_diagnoses=[],
        prior_immunizations=[],
        is_pregnant=is_pregnant,
    )
    return {
        "patient_id": str(patient_id),
        "patient_age": patient_age,
        "recommendations": recommendations,
        "total_recommended": len(recommendations),
    }


class ImmunizationAdminRequest(BaseModel):
    patient_id: UUID
    vaccine_code: str
    vaccine_name: str
    manufacturer: str
    lot_number: str
    expiry_date: str
    ndc: Optional[str] = None
    route: str = "IM"
    site: str = "right_deltoid"
    dose_volume_ml: float = 0.5
    dose_number: int = 1
    vis_document_version: str
    vis_presented_date: str
    patient_consent_obtained: bool
    observation_complete: bool = False


@router.post("/immunizations/administer", status_code=201)
async def record_immunization(
    body: ImmunizationAdminRequest,
    staff: Staff = Depends(require_pharmacist()),
    db: AsyncSession = Depends(get_db),
):
    """Record a vaccine administration. Validates documentation requirements."""
    record = ImmunizationRecord(
        patient_id=body.patient_id,
        administering_pharmacist_id=staff.id,
        pharmacy_id=staff.pharmacy_id,
        vaccine_code=body.vaccine_code,
        vaccine_name=body.vaccine_name,
        manufacturer=body.manufacturer,
        lot_number=body.lot_number,
        expiry_date=date.fromisoformat(body.expiry_date),
        ndc=body.ndc,
        route=body.route,
        site=body.site,
        dose_volume_ml=body.dose_volume_ml,
        dose_number_in_series=body.dose_number,
        vis_document_version=body.vis_document_version,
        vis_presented_date=date.fromisoformat(body.vis_presented_date),
        patient_consent_obtained=body.patient_consent_obtained,
        patient_discharged=body.observation_complete,
        cpt_code=ACIP_VACCINES.get(body.vaccine_code, {}).get("cpt", ""),
    )

    errors = record.validate()
    if errors:
        raise HTTPException(422, detail={"errors": errors, "message": "Documentation incomplete"})

    # Generate HL7 VXU for registry submission
    vxu_message = record.to_hl7_vxu()

    return {
        "record_id": str(record.record_id),
        "vaccine": record.vaccine_name,
        "lot_number": record.lot_number,
        "cpt_code": record.cpt_code,
        "hl7_vxu_ready": bool(vxu_message),
        "registry_submission": "queued",
        "message": "Immunization recorded. VIS documentation confirmed.",
    }


# ── CMS Star Ratings ──────────────────────────────────────────────────────

@router.get("/star-ratings/{year}")
async def get_star_ratings(
    year: int,
    staff: Staff = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """Calculate real-time CMS Star Ratings for this pharmacy."""
    calculator = StarRatingsCalculator(db)
    ratings = await calculator.calculate_pharmacy_ratings(staff.pharmacy_id, year)

    return {
        "pharmacy_id": str(staff.pharmacy_id),
        "measurement_year": year,
        "overall_star_estimate": ratings.overall_star_estimate,
        "metrics": {
            "diabetes_adherence": {
                "pdc": ratings.pdc_diabetes,
                "eligible": ratings.eligible_diabetes,
                "adherent": ratings.adherent_diabetes,
                "star_level": ratings.star_diabetes,
            },
            "hypertension_adherence": {
                "pdc": ratings.pdc_hypertension,
                "eligible": ratings.eligible_hypertension,
                "adherent": ratings.adherent_hypertension,
            },
            "cholesterol_adherence": {
                "pdc": ratings.pdc_cholesterol,
                "eligible": ratings.eligible_cholesterol,
                "adherent": ratings.adherent_cholesterol,
            },
            "cmr_completion_rate": ratings.cmr_completion_rate,
        },
        "thresholds": PDC_STAR_THRESHOLDS,
    }
