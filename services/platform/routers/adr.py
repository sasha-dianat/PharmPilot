from __future__ import annotations

import re
from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.ai.adr_detective import engine
from services.ai.adr_detective.narrator import narrate
from services.ai.adr_detective.schema import (
    ADRContext,
    ADRMedication,
    LabValue,
    MODEL_VERSION,
    NOT_A_DIAGNOSIS_NOTICE,
    PHARMACIST_VERIFICATION_NOTICE,
    SuspectedCause,
)
from services.ai.clinical_decision_support.normalizer import classes_of, normalize
from services.platform.auth import get_current_staff, require_permission
from services.platform.database import get_db
from shared.models.auth import Staff
from shared.models.clinical import ClinicalAuditLog, Medication
from shared.models.patient import LabResult, Patient, PatientAllergy

router = APIRouter()


class MedicationInput(BaseModel):
    drug_name: str
    strength: str | None = None
    dose: str | None = None
    route: str | None = None
    frequency: str | None = None
    start_date: date | None = None
    stop_date: date | None = None
    recent_dose_increase: bool = False
    source: str | None = "request"


class ADRAssessRequest(BaseModel):
    patient_id: UUID
    complaint: str = Field(min_length=1)
    onset_date: date | None = None
    medications: list[MedicationInput] | None = None

    @field_validator("complaint")
    @classmethod
    def complaint_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("complaint is required")
        return value.strip()


def _age_from_dob(dob: date | None, today: date | None = None) -> int | None:
    if not dob:
        return None
    today = today or date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(match.group(0)) if match else None


def _jsonable(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return value


def _medication_entry(
    drug_name: str,
    *,
    start_date: date | None = None,
    stop_date: date | None = None,
    recent_dose_increase: bool = False,
    source: str | None = None,
) -> ADRMedication:
    normalized = normalize(drug_name)
    return ADRMedication(
        drug_name=drug_name,
        normalized_name=normalized,
        classes=classes_of(normalized),
        start_date=start_date,
        stop_date=stop_date,
        recent_dose_increase=recent_dose_increase,
        source=source,
    )


def _lab_key(test_name: str) -> str:
    return re.sub(r"\s+", " ", test_name.strip().lower())


async def _load_context(
    *,
    db: AsyncSession,
    patient: Patient,
    staff: Staff,
    body: ADRAssessRequest,
) -> ADRContext:
    if body.medications is not None:
        medications = [
            _medication_entry(
                med.drug_name,
                start_date=med.start_date,
                stop_date=med.stop_date,
                recent_dose_increase=med.recent_dose_increase,
                source=med.source or "request",
            )
            for med in body.medications
            if med.drug_name.strip()
        ]
    else:
        med_result = await db.execute(
            select(Medication).where(
                Medication.patient_id == patient.id,
                Medication.pharmacy_id == staff.pharmacy_id,
                Medication.status == "active",
                Medication.is_deleted == False,  # noqa: E712
            )
        )
        medications = [
            _medication_entry(
                row.normalized_name or row.drug_name,
                start_date=getattr(row, "start_date", None),
                stop_date=getattr(row, "stop_date", None),
                source=getattr(row, "source", None),
            )
            for row in med_result.scalars().all()
        ]

    allergy_result = await db.execute(
        select(PatientAllergy).where(
            PatientAllergy.patient_id == patient.id,
            PatientAllergy.is_deleted == False,  # noqa: E712
        )
    )
    allergies = [allergy.allergen_name for allergy in allergy_result.scalars().all()]

    lab_result = await db.execute(
        select(LabResult)
        .where(LabResult.patient_id == patient.id, LabResult.is_deleted == False)  # noqa: E712
        .order_by(LabResult.result_date.desc())
    )
    labs: dict[str, LabValue] = {}
    for lab in lab_result.scalars().all():
        key = _lab_key(lab.test_name)
        if key in labs:
            continue
        labs[key] = LabValue(
            value=_to_float(lab.value),
            unit=lab.unit,
            collected_at=lab.result_date.isoformat() if lab.result_date else None,
        )

    return ADRContext(
        complaint=body.complaint,
        onset_date=body.onset_date,
        medications=medications,
        labs=labs,
        age=_age_from_dob(getattr(patient, "date_of_birth", None)),
        conditions=getattr(patient, "conditions", None) or [],
        allergies=allergies,
        pharmacy_id=str(staff.pharmacy_id) if staff.pharmacy_id else None,
        staff_id=str(staff.id) if staff.id else None,
    )


def _context_snapshot(patient: Patient, context: ADRContext) -> dict:
    return {
        "patient_id": str(patient.id),
        "complaint": context.complaint,
        "onset_date": context.onset_date,
        "age": context.age,
        "conditions": context.conditions,
        "allergies": context.allergies,
        "medications": [
            {
                "drug_name": med.drug_name,
                "normalized_name": med.normalized_name,
                "classes": sorted(med.classes),
                "start_date": med.start_date,
                "stop_date": med.stop_date,
                "recent_dose_increase": med.recent_dose_increase,
                "source": med.source,
            }
            for med in context.medications
        ],
        "labs": {
            key: {"value": lab.value, "unit": lab.unit, "collected_at": lab.collected_at}
            for key, lab in context.labs.items()
        },
    }


def _cause_to_dict(cause: SuspectedCause) -> dict:
    data = asdict(cause)
    data.pop("signals", None)
    return data


@router.post("/assess")
async def assess_adr(
    body: ADRAssessRequest,
    staff: Staff = Depends(require_permission("clinical:read")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Patient).where(
            Patient.id == body.patient_id,
            Patient.pharmacy_id == staff.pharmacy_id,
            Patient.is_deleted == False,  # noqa: E712
        )
    )
    patient = result.scalar_one_or_none()
    if not patient:
        raise HTTPException(404, "Patient not found")

    context = await _load_context(db=db, patient=patient, staff=staff, body=body)
    assessed_at = datetime.now(timezone.utc)
    llm_meta = {"llm_used": False, "provider": "none", "degraded": False}

    if context.medications:
        deterministic_causes = engine.assess(context)
        causes, meta = await narrate(deterministic_causes, context)
        llm_meta = asdict(meta)
    else:
        causes = []

    output_snapshot = {
        "suspected_causes": [_cause_to_dict(cause) for cause in causes],
        "llm_meta": llm_meta,
    }
    db.add(
        ClinicalAuditLog(
            user_id=staff.id,
            patient_id=patient.id,
            module="adr",
            input_snapshot=_jsonable(_context_snapshot(patient, context)),
            output_snapshot=_jsonable(output_snapshot),
            rules_triggered=[cause.normalized_name for cause in causes if cause.normalized_name],
            model_version=MODEL_VERSION,
            created_by=staff.id,
            updated_by=staff.id,
        )
    )
    await db.flush()

    response = {
        "patient_id": str(patient.id),
        "complaint": context.complaint,
        "assessed_at": assessed_at.isoformat(),
        "model_version": MODEL_VERSION,
        "suspected_causes": output_snapshot["suspected_causes"],
        "llm_used": llm_meta["llm_used"],
        "degraded": llm_meta["degraded"],
        "pharmacist_verification_notice": PHARMACIST_VERIFICATION_NOTICE,
        "not_a_diagnosis_notice": NOT_A_DIAGNOSIS_NOTICE,
    }
    if not context.medications:
        response["note"] = "No active medications available for ADR assessment."
    return response
