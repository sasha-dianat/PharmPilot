"""
Phase 32 — Real Pharmacy Journey API
Orchestrates: customer entry → auto patient resolution → Rx intake → council report → handoff.
"""
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.auth import get_current_staff, get_current_staff_sse
from services.platform.config import settings
from services.platform.database import get_db
from services.biometric.identity_resolution.person_links import (
    PersonLinkGraph,
    customer_ref,
    patient_ref,
)
from services.biometric.patient_resolver.resolver import PatientResolver
from services.ai.clinical_brain.council.specialist_council import SpecialistCouncil
from services.core.pharmacy_workflow.patient_context import load_active_medications_and_diagnoses
from shared.models.auth import Staff

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Phase 32-B: Auto Patient Resolution ───────────────────────────────────

class PrescriptionResolveRequest(BaseModel):
    # Supply any/all available signals
    ocr_text: Optional[str] = None
    insurance_bin: Optional[str] = None
    insurance_member_id: Optional[str] = None
    insurance_group: Optional[str] = None
    biometric_identity_id: Optional[UUID] = None
    biometric_confidence: float = 0.0
    transcript_text: Optional[str] = None
    portal_patient_id: Optional[UUID] = None


@router.post("/resolve-patient")
async def resolve_patient_from_prescription(
    body: PrescriptionResolveRequest,
    staff: Staff = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """
    Auto-resolve which patient a prescription belongs to.
    No pharmacist search needed on high-confidence path.
    Returns the resolved patient ID + confidence tier + action guidance.
    """
    resolver = PatientResolver(db)
    result = await resolver.resolve_from_prescription(
        pharmacy_id=staff.pharmacy_id,
        ocr_text=body.ocr_text,
        insurance_bin=body.insurance_bin,
        insurance_member_id=body.insurance_member_id,
        insurance_group=body.insurance_group,
        biometric_identity_id=body.biometric_identity_id,
        biometric_confidence=body.biometric_confidence,
        transcript_text=body.transcript_text,
        portal_patient_id=body.portal_patient_id,
    )

    # Load patient profile if resolved
    patient_profile = None
    if result.resolved_patient_id:
        from sqlalchemy import select
        from shared.models.patient import Patient, PatientAllergy
        pat = await db.execute(select(Patient).where(Patient.id == result.resolved_patient_id))
        p = pat.scalar_one_or_none()
        allergies = await db.execute(select(PatientAllergy).where(
            PatientAllergy.patient_id == result.resolved_patient_id,
            PatientAllergy.is_deleted == False,
        ))
        if p:
            from datetime import date
            age = (date.today() - p.date_of_birth).days // 365
            patient_profile = {
                "id": str(p.id),
                "first_name": p.first_name,
                "last_name": p.last_name,
                "date_of_birth": p.date_of_birth.isoformat(),
                "age": age,
                "gender": p.gender,
                "phone_primary": p.phone_primary,
                "allergies": [
                    {"allergen_name": a.allergen_name, "severity": a.severity, "reaction": a.reaction}
                    for a in allergies.scalars().all()
                ],
                "biometric_enrolled": p.biometric_enrolled,
            }

    return {
        "resolved_patient_id": str(result.resolved_patient_id) if result.resolved_patient_id else None,
        "resolution_tier": result.resolution_tier,
        "confidence": round(result.overall_confidence, 3),
        "auto_loaded": result.auto_loaded,
        "requires_action": result.requires_pharmacist_action,
        "action_message": result.action_message,
        "best_candidates": result.best_candidates[:3],
        "signals_used": [
            {"source": s.source, "confidence": round(s.confidence, 3)}
            for s in result.signals
        ],
        "patient_profile": patient_profile,
    }


# ── Phase 32-C: Auto-trigger Specialist Council ────────────────────────────

class CouncilRequest(BaseModel):
    prescription_id: UUID
    patient_id: UUID


@router.post("/council")
async def get_council_report(
    body: CouncilRequest,
    staff: Staff = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """
    Convene the Specialist Clinical Council for a prescription.
    Auto-triggered when pharmacist opens the workbench.
    Returns the full council report with all specialist findings.
    """
    from sqlalchemy import select
    from shared.models.prescription import Prescription
    from shared.models.patient import Patient, PatientAllergy, LabResult

    # Load prescription
    rx_result = await db.execute(select(Prescription).where(Prescription.id == body.prescription_id))
    rx = rx_result.scalar_one_or_none()
    if not rx:
        raise HTTPException(404, "Prescription not found")

    # Load patient
    pat_result = await db.execute(select(Patient).where(Patient.id == body.patient_id))
    patient = pat_result.scalar_one_or_none()
    if not patient:
        raise HTTPException(404, "Patient not found")

    # Load allergies and labs
    allergies_result = await db.execute(
        select(PatientAllergy).where(PatientAllergy.patient_id == body.patient_id, PatientAllergy.is_deleted == False)
    )
    labs_result = await db.execute(
        select(LabResult).where(LabResult.patient_id == body.patient_id)
        .order_by(LabResult.result_date.desc()).limit(20)
    )

    from datetime import date
    age = (date.today() - patient.date_of_birth).days // 365
    egfr = None
    labs_dict = {}
    for lab in labs_result.scalars().all():
        labs_dict[lab.test_name] = lab.value
        if "egfr" in lab.test_name.lower() or "gfr" in lab.test_name.lower():
            try:
                egfr = float(lab.value)
            except ValueError:
                pass

    clinical_context = await load_active_medications_and_diagnoses(db, body.patient_id)
    patient_profile = {
        "age": age,
        "gender": patient.gender,
        "egfr": egfr,
        "weight_kg": None,
        "allergies": [{"allergen_name": a.allergen_name, "severity": a.severity, "reaction": a.reaction}
                      for a in allergies_result.scalars().all()],
        "active_medications": clinical_context["active_medications"],
        "diagnoses": clinical_context["diagnoses"],
        "recent_labs": labs_dict,
        "pharmacogenomics": None,
    }

    prescription_dict = {
        "drug_name": rx.drug_name,
        "drug_strength": rx.drug_strength,
        "ndc": rx.ndc,
        "sig_text": rx.sig_text,
        "quantity_prescribed": float(rx.quantity_prescribed),
        "days_supply": rx.days_supply,
        "dea_schedule": rx.dea_schedule,
        "is_controlled": rx.is_controlled,
        "rx_number": rx.rx_number,
    }

    council = SpecialistCouncil(db=db, anthropic_api_key=settings.ANTHROPIC_API_KEY)
    report = await council.convene(
        prescription_id=body.prescription_id,
        patient_id=body.patient_id,
        prescription=prescription_dict,
        patient_profile=patient_profile,
    )

    # Store council report on prescription
    from sqlalchemy import update
    import json
    await db.execute(
        update(Prescription).where(Prescription.id == body.prescription_id).values(
            acb_safety_report={
                "council_report": {
                    "severity": "critical" if report.has_blockers else "moderate" if report.cautions else "informational",
                    "primary_finding": report.council_summary,
                    "total_findings": report.total_findings,
                    "has_blockers": report.has_blockers,
                }
            }
        )
    )

    def findings_to_dict(findings):
        return [
            {
                "specialist": f.specialist,
                "severity": f.severity,
                "message": f.message,
                "drug_name": f.drug_name,
                "evidence_source": f.evidence_source,
                "evidence_grade": f.evidence_grade,
            }
            for f in findings
        ]

    return {
        "prescription_id": str(body.prescription_id),
        "patient_id": str(body.patient_id),
        "generated_at": report.generated_at,
        "council_summary": report.council_summary,
        "has_blockers": report.has_blockers,
        "total_findings": report.total_findings,
        "specialists_consulted": report.specialists_consulted,
        "blockers": findings_to_dict(report.blockers),
        "cautions": findings_to_dict(report.cautions),
        "counseling_points": findings_to_dict(report.counseling_points),
        "monitoring_parameters": findings_to_dict(report.monitoring_parameters),
        "clarification_prompts": findings_to_dict(report.clarification_prompts),
        "hereditary_flags": findings_to_dict(report.hereditary_flags),
    }


@router.get("/council/stream")
async def stream_council_report(
    prescription_id: UUID,
    patient_id: UUID,
    staff: Staff = Depends(get_current_staff_sse),
    db: AsyncSession = Depends(get_db),
):
    """
    Server-Sent Events stream of council findings as each specialist completes.
    The pharmacist UI displays findings progressively — no waiting for all specialists.

    Auth note: browsers' native EventSource cannot send an Authorization header,
    so this route accepts the JWT either via the standard Bearer header (for
    non-browser/test clients) or a `?token=` query parameter (for EventSource).
    See get_current_staff_sse in services/platform/auth.py.
    """
    import json
    from sqlalchemy import select
    from shared.models.prescription import Prescription
    from shared.models.patient import Patient, PatientAllergy, LabResult

    rx_result = await db.execute(select(Prescription).where(Prescription.id == prescription_id))
    rx = rx_result.scalar_one_or_none()
    if not rx:
        raise HTTPException(404, "Prescription not found")

    pat_result = await db.execute(select(Patient).where(Patient.id == patient_id))
    patient = pat_result.scalar_one_or_none()
    if not patient:
        raise HTTPException(404, "Patient not found")

    allergies_result = await db.execute(
        select(PatientAllergy).where(PatientAllergy.patient_id == patient_id, PatientAllergy.is_deleted == False)
    )

    from datetime import date
    age = (date.today() - patient.date_of_birth).days // 365

    clinical_context = await load_active_medications_and_diagnoses(db, patient_id)
    patient_profile = {
        "age": age,
        "gender": patient.gender,
        "egfr": None,
        "weight_kg": None,
        "allergies": [{"allergen_name": a.allergen_name} for a in allergies_result.scalars().all()],
        "active_medications": clinical_context["active_medications"],
        "diagnoses": clinical_context["diagnoses"],
    }

    prescription_dict = {
        "drug_name": rx.drug_name,
        "ndc": rx.ndc,
        "sig_text": rx.sig_text,
        "quantity_prescribed": float(rx.quantity_prescribed),
        "days_supply": rx.days_supply,
        "dea_schedule": rx.dea_schedule,
    }

    council = SpecialistCouncil(db=db)

    async def event_generator():
        async for event in council.stream_convene(
            prescription_id=prescription_id,
            patient_id=patient_id,
            prescription=prescription_dict,
            patient_profile=patient_profile,
        ):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Customer Entry (Phase 32 SOP) ──────────────────────────────────────────

@router.post("/customer/entry")
async def register_customer_entry(
    biometric_identity_id: Optional[UUID] = None,
    biometric_confidence: float = 0.0,
    staff: Staff = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """
    Register a customer entering the pharmacy.
    Creates a confirmed or temporary UUID customer identity.
    Links to possible patients if biometric match available.
    """
    from sqlalchemy import text
    from uuid import uuid4
    from datetime import datetime, timezone

    customer_id = uuid4()
    is_temporary = biometric_identity_id is None or biometric_confidence < 0.80
    now = datetime.now(timezone.utc)

    await db.execute(text("""
        INSERT INTO customer_identities
          (id, pharmacy_id, biometric_identity_id, is_temporary, first_seen_at, last_seen_at)
        VALUES (:id, :pharmacy_id, :bio_id, :is_temp, :now, :now)
    """), {
        "id": str(customer_id),
        "pharmacy_id": str(staff.pharmacy_id),
        "bio_id": str(biometric_identity_id) if biometric_identity_id else None,
        "is_temp": is_temporary,
        "now": now,
    })

    # If biometric match, link possible patients
    linked_patients = []
    if biometric_identity_id and biometric_confidence >= 0.80:
        patient_result = await db.execute(text("""
            SELECT id, first_name, last_name, date_of_birth
            FROM patients
            WHERE biometric_identity_id = :bio_id AND is_deleted = false
        """), {"bio_id": str(biometric_identity_id)})
        graph = PersonLinkGraph(db)
        for row in patient_result.mappings().all():
            await graph.link(
                staff.pharmacy_id,
                customer_ref(customer_id),
                patient_ref(row["id"]),
                relationship="self",
                confidence=biometric_confidence,
                source="biometric",
            )
            linked_patients.append({
                "patient_id": str(row["id"]),
                "name": f"{row['first_name']} {row['last_name']}",
            })

    return {
        "customer_id": str(customer_id),
        "is_temporary": is_temporary,
        "biometric_confidence": biometric_confidence,
        "linked_patients": linked_patients,
        "service_lanes": ["prescription_reception", "clinical_consultation", "otc_medications", "cosmetics_retail"],
    }
