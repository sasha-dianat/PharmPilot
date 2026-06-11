from __future__ import annotations

import re
from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.ai.clinical_decision_support.normalizer import classes_of, normalize
from services.ai.polypharmacy import engine
from services.ai.polypharmacy.drafts import build_message
from services.ai.polypharmacy.schema import (
    MODEL_VERSION,
    PHARMACIST_VERIFICATION_NOTICE,
    BurdenScore,
    LabValue,
    PolyContext,
    PolyMedication,
    ReviewResult,
)
from services.platform.auth import get_current_staff, require_permission
from services.platform.database import get_db
from shared.models.auth import Staff
from shared.models.clinical import ClinicalAuditLog, Medication
from shared.models.patient import LabResult, Patient, PatientAllergy

router = APIRouter()


class MedicationInput(BaseModel):
    drug_name: str
    indication: str | None = None
    status: str | None = "active"
    source: str | None = "request"


class PolypharmacyReviewRequest(BaseModel):
    patient_id: UUID
    medications: list[MedicationInput] | None = None
    message_format: Literal["sbar", "concise"] = "sbar"


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
    indication: str | None = None,
    status: str | None = "active",
    source: str | None = None,
) -> PolyMedication:
    normalized = normalize(drug_name)
    return PolyMedication(
        drug_name=drug_name,
        normalized_name=normalized,
        classes=classes_of(normalized),
        indication=indication,
        status=status,
        source=source,
    )


def _lab_key(test_name: str) -> str:
    return re.sub(r"\s+", " ", test_name.strip().lower())


async def _load_context(
    *,
    db: AsyncSession,
    patient: Patient,
    staff: Staff,
    body: PolypharmacyReviewRequest,
) -> PolyContext:
    if body.medications is not None:
        medications = [
            _medication_entry(
                med.drug_name,
                indication=med.indication,
                status=med.status,
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
                indication=getattr(row, "indication", None),
                status=getattr(row, "status", "active"),
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

    return PolyContext(
        age=_age_from_dob(getattr(patient, "date_of_birth", None)),
        conditions=getattr(patient, "conditions", None) or [],
        allergies=allergies,
        medications=medications,
        labs=labs,
    )


def _context_snapshot(patient: Patient, context: PolyContext) -> dict:
    return {
        "patient_id": str(patient.id),
        "age": context.age,
        "conditions": context.conditions,
        "allergies": context.allergies,
        "medications": [
            {
                "drug_name": med.drug_name,
                "normalized_name": med.normalized_name,
                "classes": sorted(med.classes),
                "indication": med.indication,
                "status": med.status,
                "source": med.source,
            }
            for med in context.medications
        ],
        "labs": {
            key: {"value": lab.value, "unit": lab.unit, "collected_at": lab.collected_at}
            for key, lab in context.labs.items()
        },
    }


def _finding_to_dict(finding) -> dict:
    return asdict(finding)


@router.post("/review")
async def review_polypharmacy(
    body: PolypharmacyReviewRequest,
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
    reviewed_at = datetime.now(timezone.utc)

    fail_safe_note: str | None = None
    try:
        review_result = engine.review(context)
        physician_message_draft = build_message(review_result.findings, context, body.message_format)
    except Exception as exc:  # pragma: no cover - defensive fail-safe for clinical endpoint behavior
        fail_safe_note = f"Polypharmacy review could not be completed deterministically: {type(exc).__name__}."
        review_result = ReviewResult(
            anticholinergic_burden=BurdenScore(score=0, drugs=[]),
            sedative_fall_risk=BurdenScore(score=0, drugs=[]),
            findings=[],
            missing_information=[fail_safe_note],
        )
        physician_message_draft = (
            "Medication review draft\n"
            "Deterministic review could not be completed. Please verify the medication profile and rerun the review."
        )

    findings = [_finding_to_dict(finding) for finding in review_result.findings]
    output_snapshot = {
        "anticholinergic_burden": asdict(review_result.anticholinergic_burden),
        "sedative_fall_risk": asdict(review_result.sedative_fall_risk),
        "findings": findings,
        "missing_information_summary": review_result.missing_information,
        "physician_message_draft": physician_message_draft,
    }

    db.add(
        ClinicalAuditLog(
            user_id=staff.id,
            patient_id=patient.id,
            module="polypharmacy",
            input_snapshot=_jsonable(_context_snapshot(patient, context)),
            output_snapshot=_jsonable(output_snapshot),
            rules_triggered=[finding["category"] for finding in findings],
            model_version=MODEL_VERSION,
            created_by=staff.id,
            updated_by=staff.id,
        )
    )
    await db.flush()

    response = {
        "patient_id": str(patient.id),
        "reviewed_at": reviewed_at.isoformat(),
        "model_version": MODEL_VERSION,
        "anticholinergic_burden": output_snapshot["anticholinergic_burden"],
        "sedative_fall_risk": output_snapshot["sedative_fall_risk"],
        "findings": findings,
        "physician_message_draft": physician_message_draft,
        "missing_information_summary": review_result.missing_information,
        "pharmacist_verification_notice": PHARMACIST_VERIFICATION_NOTICE,
    }
    if not context.medications:
        response["note"] = "No active medications available for polypharmacy review."
    elif fail_safe_note:
        response["note"] = fail_safe_note
    return response
