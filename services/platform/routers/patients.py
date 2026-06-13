"""Patient management router — full CRUD + insurance + allergies."""
from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.auth import get_current_staff, require_permission
from services.platform.database import get_db
from shared.models.auth import Staff
from shared.models.patient import (
    ClinicalNote, LabResult, Patient, PatientAllergy, PatientStatus
)
from shared.models.insurance import InsurancePlan, PatientInsurance

router = APIRouter()


# ── Pydantic schemas ─────────────────────────────────────────────────────────

class PatientCreate(BaseModel):
    first_name: str
    last_name: str
    date_of_birth: date
    gender: str
    phone_primary: Optional[str] = None
    phone_secondary: Optional[str] = None
    email: Optional[EmailStr] = None
    address_line1: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    preferred_language: str = "en"
    preferred_contact_method: str = "phone"


class PatientUpdate(BaseModel):
    phone_primary: Optional[str] = None
    phone_secondary: Optional[str] = None
    email: Optional[EmailStr] = None
    address_line1: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    preferred_language: Optional[str] = None
    preferred_contact_method: Optional[str] = None
    status: Optional[PatientStatus] = None


class AllergyCreate(BaseModel):
    allergen_type: str = "drug"
    allergen_name: str
    allergen_ndc: Optional[str] = None
    reaction: Optional[str] = None
    severity: str = "unknown"
    onset_date: Optional[date] = None


class InsuranceCreate(BaseModel):
    bin_number: str
    pcn: Optional[str] = None
    group_number: Optional[str] = None
    member_id: str
    person_code: str = "01"
    cardholder_name: Optional[str] = None
    effective_date: Optional[date] = None
    termination_date: Optional[date] = None
    priority: int = 1


def patient_to_dict(p: Patient) -> dict:
    return {
        "id": str(p.id),
        "first_name": p.first_name,
        "last_name": p.last_name,
        "date_of_birth": p.date_of_birth.isoformat(),
        # Iranian identity fields (migration 0003)
        "date_of_birth_jalali": getattr(p, "date_of_birth_jalali", None),
        "national_id": getattr(p, "national_id", None),
        "identity_system": getattr(p, "identity_system", "american"),
        "gender": p.gender,
        "phone_primary": p.phone_primary,
        "phone_secondary": getattr(p, "phone_secondary", None),
        "email": p.email,
        "address_line1": getattr(p, "address_line1", None),
        "city": p.city,
        "state": p.state,
        "zip_code": p.zip_code,
        "preferred_language": p.preferred_language,
        "weight_kg": float(p.weight_kg) if getattr(p, "weight_kg", None) is not None else None,
        "pregnancy_status": getattr(p, "pregnancy_status", None),
        "renal_function": getattr(p, "renal_function", None),
        "hepatic_status": getattr(p, "hepatic_status", None),
        "conditions": getattr(p, "conditions", None) or [],
        "status": p.status,
        "biometric_enrolled": p.biometric_enrolled,
        "created_at": p.created_at.isoformat(),
    }


# ── Routes ───────────────────────────────────────────────────────────────────

@router.post("", status_code=201)
async def create_patient(
    body: PatientCreate,
    staff: Staff = Depends(require_permission("patient:write")),
    db: AsyncSession = Depends(get_db),
):
    patient = Patient(
        pharmacy_id=staff.pharmacy_id,
        **body.model_dump(),
    )
    db.add(patient)
    await db.flush()
    return patient_to_dict(patient)


@router.get("")
async def search_patients(
    q: Optional[str] = Query(None, description="Name, DOB (YYYY-MM-DD), or phone"),
    dob: Optional[date] = None,
    phone: Optional[str] = None,
    limit: int = Query(20, le=100),
    offset: int = 0,
    staff: Staff = Depends(require_permission("patient:read")),
    db: AsyncSession = Depends(get_db),
):
    """
    Fuzzy search by name, exact DOB, or phone.
    Uses pg_trgm index for name matching.
    """
    stmt = select(Patient).where(
        Patient.pharmacy_id == staff.pharmacy_id,
        Patient.is_deleted == False,  # noqa: E712
    )

    if q:
        # Try to parse as date
        try:
            dob_from_q = date.fromisoformat(q)
            stmt = stmt.where(Patient.date_of_birth == dob_from_q)
        except ValueError:
            # Name search via ilike (pg_trgm index used automatically)
            parts = q.strip().split()
            if len(parts) >= 2:
                stmt = stmt.where(
                    Patient.last_name.ilike(f"%{parts[-1]}%"),
                    Patient.first_name.ilike(f"%{parts[0]}%"),
                )
            else:
                stmt = stmt.where(
                    or_(
                        Patient.last_name.ilike(f"%{q}%"),
                        Patient.first_name.ilike(f"%{q}%"),
                    )
                )

    if dob:
        stmt = stmt.where(Patient.date_of_birth == dob)
    if phone:
        stmt = stmt.where(
            or_(
                Patient.phone_primary.ilike(f"%{phone}%"),
                Patient.phone_secondary.ilike(f"%{phone}%"),
            )
        )

    stmt = stmt.order_by(Patient.last_name, Patient.first_name).limit(limit).offset(offset)
    result = await db.execute(stmt)
    patients = result.scalars().all()
    return [patient_to_dict(p) for p in patients]


@router.get("/{patient_id}")
async def get_patient(
    patient_id: UUID,
    staff: Staff = Depends(require_permission("patient:read")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Patient).where(
            Patient.id == patient_id,
            Patient.pharmacy_id == staff.pharmacy_id,
            Patient.is_deleted == False,  # noqa: E712
        )
    )
    patient = result.scalar_one_or_none()
    if not patient:
        raise HTTPException(404, "Patient not found")
    return patient_to_dict(patient)


@router.patch("/{patient_id}")
async def update_patient(
    patient_id: UUID,
    body: PatientUpdate,
    staff: Staff = Depends(require_permission("patient:write")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Patient).where(Patient.id == patient_id, Patient.pharmacy_id == staff.pharmacy_id)
    )
    patient = result.scalar_one_or_none()
    if not patient:
        raise HTTPException(404, "Patient not found")

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(patient, field, value)

    patient.updated_by = staff.id
    return patient_to_dict(patient)


# ── Allergies ────────────────────────────────────────────────────────────────

@router.get("/{patient_id}/allergies")
async def get_allergies(
    patient_id: UUID,
    staff: Staff = Depends(require_permission("patient:read")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PatientAllergy).where(
            PatientAllergy.patient_id == patient_id,
            PatientAllergy.is_deleted == False,  # noqa: E712
        )
    )
    return [
        {
            "id": str(a.id),
            "allergen_type": a.allergen_type,
            "allergen_name": a.allergen_name,
            "reaction": a.reaction,
            "severity": a.severity,
            "onset_date": a.onset_date.isoformat() if a.onset_date else None,
            "source": a.source,
        }
        for a in result.scalars().all()
    ]


@router.post("/{patient_id}/allergies", status_code=201)
async def add_allergy(
    patient_id: UUID,
    body: AllergyCreate,
    staff: Staff = Depends(require_permission("patient:write")),
    db: AsyncSession = Depends(get_db),
):
    allergy = PatientAllergy(
        patient_id=patient_id,
        source="pharmacist",
        created_by=staff.id,
        **body.model_dump(),
    )
    db.add(allergy)
    await db.flush()
    return {"id": str(allergy.id), "status": "created"}


@router.delete("/{patient_id}/allergies/{allergy_id}")
async def remove_allergy(
    patient_id: UUID,
    allergy_id: UUID,
    staff: Staff = Depends(require_permission("patient:write")),
    db: AsyncSession = Depends(get_db),
):
    from datetime import datetime, timezone
    result = await db.execute(
        select(PatientAllergy).where(
            PatientAllergy.id == allergy_id,
            PatientAllergy.patient_id == patient_id,
        )
    )
    allergy = result.scalar_one_or_none()
    if not allergy:
        raise HTTPException(404, "Allergy not found")
    allergy.is_deleted = True
    allergy.deleted_at = datetime.now(timezone.utc)
    return {"status": "deleted"}


# ── Insurance ────────────────────────────────────────────────────────────────

@router.get("/{patient_id}/insurance")
async def get_insurance(
    patient_id: UUID,
    staff: Staff = Depends(require_permission("patient:read")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PatientInsurance).where(
            PatientInsurance.patient_id == patient_id,
            PatientInsurance.is_deleted == False,  # noqa: E712
        ).order_by(PatientInsurance.priority)
    )
    return [
        {
            "id": str(i.id),
            "bin_number": i.bin_number,
            "pcn": i.pcn,
            "group_number": i.group_number,
            "member_id": i.member_id,
            "person_code": i.person_code,
            "priority": i.priority,
            "is_active": i.is_active,
            "effective_date": i.effective_date.isoformat() if i.effective_date else None,
            "termination_date": i.termination_date.isoformat() if i.termination_date else None,
        }
        for i in result.scalars().all()
    ]


@router.post("/{patient_id}/insurance", status_code=201)
async def add_insurance(
    patient_id: UUID,
    body: InsuranceCreate,
    staff: Staff = Depends(require_permission("patient:write")),
    db: AsyncSession = Depends(get_db),
):
    insurance = PatientInsurance(
        patient_id=patient_id,
        created_by=staff.id,
        **body.model_dump(),
    )
    db.add(insurance)
    await db.flush()
    return {"id": str(insurance.id), "status": "created"}


# ── Lab results ──────────────────────────────────────────────────────────────

@router.get("/{patient_id}/labs")
async def get_labs(
    patient_id: UUID,
    limit: int = 50,
    staff: Staff = Depends(require_permission("patient:read")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(LabResult)
        .where(LabResult.patient_id == patient_id, LabResult.is_deleted == False)  # noqa: E712
        .order_by(LabResult.result_date.desc())
        .limit(limit)
    )
    return [
        {
            "id": str(l.id),
            "test_name": l.test_name,
            "loinc_code": l.loinc_code,
            "value": l.value,
            "unit": l.unit,
            "reference_range": l.reference_range,
            "abnormal_flag": l.abnormal_flag,
            "result_date": l.result_date.isoformat(),
            "source": l.source,
        }
        for l in result.scalars().all()
    ]


# ── Clinical notes ───────────────────────────────────────────────────────────

@router.get("/{patient_id}/notes")
async def get_notes(
    patient_id: UUID,
    staff: Staff = Depends(require_permission("patient:read")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ClinicalNote)
        .where(ClinicalNote.patient_id == patient_id, ClinicalNote.is_deleted == False)  # noqa: E712
        .order_by(ClinicalNote.created_at.desc())
        .limit(50)
    )
    return [
        {
            "id": str(n.id),
            "note_type": n.note_type,
            "content": n.content,
            "source": n.source,
            "ai_generated": n.ai_generated,
            "pharmacist_reviewed": n.pharmacist_reviewed,
            "created_at": n.created_at.isoformat(),
        }
        for n in result.scalars().all()
    ]


@router.post("/{patient_id}/notes", status_code=201)
async def add_note(
    patient_id: UUID,
    note_type: str,
    content: str,
    staff: Staff = Depends(require_permission("patient:write")),
    db: AsyncSession = Depends(get_db),
):
    note = ClinicalNote(
        patient_id=patient_id,
        note_type=note_type,
        content=content,
        source="pharmacist",
        pharmacist_reviewed=True,
        pharmacist_id=staff.id,
        created_by=staff.id,
    )
    db.add(note)
    await db.flush()
    return {"id": str(note.id), "status": "created"}
