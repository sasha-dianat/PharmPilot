"""Clinical Brain API — AI-powered clinical consultation endpoints."""
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from services.core.pharmacy_workflow.patient_context import load_active_medications_and_diagnoses
from services.platform.config import settings
from services.platform.database import get_db
from services.platform.auth import require_permission
from shared.models.auth import Staff

logger = logging.getLogger(__name__)
router = APIRouter()


class RxReviewRequest(BaseModel):
    prescription_id: UUID
    patient_id: UUID
    pharmacy_id: UUID


class RxReviewResponse(BaseModel):
    consultation_id: UUID
    severity: str
    primary_finding: str
    requires_pharmacist_action: bool
    prescriber_contact_recommended: bool
    prescriber_contact_reason: str | None
    raw_analysis: str
    confidence: float


@router.post("/rx-review", response_model=RxReviewResponse)
async def review_prescription(
    request: RxReviewRequest,
    staff: Staff = Depends(require_permission("clinical:read")),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select
    from shared.models.prescription import Prescription
    from shared.models.patient import Patient, PatientAllergy, LabResult
    from services.ai.clinical_brain.orchestrator import ClinicalBrainOrchestrator, PatientContextSummary
    from datetime import date

    rx_result = await db.execute(select(Prescription).where(Prescription.id == request.prescription_id))
    rx = rx_result.scalar_one_or_none()
    if not rx:
        raise HTTPException(status_code=404, detail="Prescription not found")

    patient_result = await db.execute(select(Patient).where(Patient.id == request.patient_id))
    patient = patient_result.scalar_one_or_none()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    allergies_result = await db.execute(select(PatientAllergy).where(PatientAllergy.patient_id == request.patient_id))
    allergies = [{"allergen_name": a.allergen_name, "reaction": a.reaction} for a in allergies_result.scalars().all()]

    labs_result = await db.execute(
        select(LabResult).where(LabResult.patient_id == request.patient_id).order_by(LabResult.result_date.desc()).limit(20)
    )
    labs = {lab.test_name: lab.value for lab in labs_result.scalars().all()}

    age = (date.today() - patient.date_of_birth).days // 365
    egfr = next((float(labs[k]) for k in ("eGFR", "egfr", "GFR") if k in labs), None)

    clinical_context = await load_active_medications_and_diagnoses(db, request.patient_id)
    patient_context = PatientContextSummary(
        patient_id=request.patient_id, age=age, gender=patient.gender,
        weight_kg=None, egfr=egfr, hepatic_function=None,
        active_medications=clinical_context["active_medications"], allergies=allergies,
        diagnoses=clinical_context["diagnoses"], recent_labs=labs,
        pregnancy_status=None, pharmacogenomics=None,
    )

    new_rx_dict = {
        "drug_name": rx.drug_name, "ndc": rx.ndc, "strength": rx.drug_strength,
        "sig_text": rx.sig_text, "days_supply": rx.days_supply,
        "quantity_prescribed": float(rx.quantity_prescribed), "dea_schedule": rx.dea_schedule,
    }

    if not settings.ANTHROPIC_API_KEY:
        import uuid
        return RxReviewResponse(
            consultation_id=uuid.uuid4(), severity="informational",
            primary_finding="Set ANTHROPIC_API_KEY to enable AI clinical review.",
            requires_pharmacist_action=False, prescriber_contact_recommended=False,
            prescriber_contact_reason=None, raw_analysis="Not configured.", confidence=0.0,
        )

    orchestrator = ClinicalBrainOrchestrator(anthropic_api_key=settings.ANTHROPIC_API_KEY)
    result = await orchestrator.comprehensive_rx_review(new_prescription=new_rx_dict, patient=patient_context)

    rx.acb_safety_report = {"consultation_id": str(result.consultation_id), "severity": result.severity, "primary_finding": result.primary_finding}
    rx.acb_consultation = {"raw_response": result.raw_llm_response}

    return RxReviewResponse(
        consultation_id=result.consultation_id, severity=result.severity,
        primary_finding=result.primary_finding,
        requires_pharmacist_action=result.requires_pharmacist_action,
        prescriber_contact_recommended=result.prescriber_contact_recommended,
        prescriber_contact_reason=result.prescriber_contact_reason,
        raw_analysis=result.raw_llm_response or "", confidence=result.confidence,
    )
