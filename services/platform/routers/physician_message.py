from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.ai.physician_message.builder import build_baseline
from services.ai.physician_message.narrator import render
from services.ai.physician_message.schema import (
    FORMATS,
    LANGUAGES,
    MODEL_VERSION,
    PHARMACIST_VERIFICATION_NOTICE,
    URGENCIES,
    LLMMeta,
    MessageContent,
    MessageInput,
)
from services.platform.auth import get_current_staff, require_permission
from services.platform.database import get_db
from shared.models.auth import Staff
from shared.models.clinical import ClinicalAuditLog
from shared.models.patient import Patient

router = APIRouter()


class PhysicianMessageGenerateRequest(BaseModel):
    patient_id: UUID | None = None
    prescriber_name: str | None = None
    patient_context: str | None = None
    medication_issue: str = Field(min_length=1)
    clinical_rationale: str | None = None
    recommendation_or_question: str = Field(min_length=1)
    urgency: str = "routine"
    supporting_data: list[str] = Field(default_factory=list)
    pharmacist_name: str | None = None
    format: str = "sbar"
    language: str = "en"

    @model_validator(mode="after")
    def validate_request(self):
        self.medication_issue = self.medication_issue.strip()
        self.recommendation_or_question = self.recommendation_or_question.strip()
        if not self.medication_issue:
            raise ValueError("medication_issue is required")
        if not self.recommendation_or_question:
            raise ValueError("recommendation_or_question is required")
        if self.format not in FORMATS:
            raise ValueError(f"format must be one of: {', '.join(FORMATS)}")
        if self.language not in LANGUAGES:
            raise ValueError(f"language must be one of: {', '.join(LANGUAGES)}")
        if self.urgency not in URGENCIES:
            raise ValueError(f"urgency must be one of: {', '.join(URGENCIES)}")
        self.supporting_data = [item.strip() for item in self.supporting_data if item and item.strip()]
        for field in ("prescriber_name", "patient_context", "clinical_rationale", "pharmacist_name"):
            value = getattr(self, field)
            if value is not None:
                setattr(self, field, value.strip() or None)
        return self


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


def _message_to_dict(content: MessageContent) -> dict[str, Any]:
    return {
        "subject": content.subject,
        "body": content.body,
        "sections": content.sections,
    }


def _age_from_dob(dob: date | None, today: date | None = None) -> int | None:
    if not dob:
        return None
    today = today or date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


async def _tenant_scope_patient(patient_id: UUID | None, staff: Staff, db: AsyncSession) -> Patient | None:
    if patient_id is None:
        return None
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
    return patient


def _minimal_patient_context(patient: Patient | None) -> str | None:
    if patient is None:
        return None
    parts: list[str] = []
    age = _age_from_dob(getattr(patient, "date_of_birth", None))
    if age is not None:
        parts.append(f"{age}-year-old patient")
    conditions = getattr(patient, "conditions", None) or []
    if conditions:
        parts.append(f"conditions: {', '.join(str(item) for item in conditions[:5])}")
    return "; ".join(parts) or None


async def _write_audit(
    *,
    db: AsyncSession,
    staff: Staff,
    patient_id: UUID | None,
    input_snapshot: dict[str, Any],
    output_snapshot: dict[str, Any],
) -> None:
    db.add(
        ClinicalAuditLog(
            user_id=staff.id,
            patient_id=patient_id,
            module="physician_message",
            input_snapshot=_jsonable(input_snapshot),
            output_snapshot=_jsonable(output_snapshot),
            rules_triggered=[],
            model_version=MODEL_VERSION,
            created_by=staff.id,
            updated_by=staff.id,
        )
    )
    await db.flush()


@router.post("/generate")
async def generate_physician_message(
    body: PhysicianMessageGenerateRequest,
    staff: Staff = Depends(require_permission("clinical:read")),
    db: AsyncSession = Depends(get_db),
):
    patient = await _tenant_scope_patient(body.patient_id, staff, db) if body.patient_id else None
    patient_context = body.patient_context or _minimal_patient_context(patient)
    message_input = MessageInput(
        prescriber_name=body.prescriber_name,
        patient_context=patient_context,
        medication_issue=body.medication_issue,
        clinical_rationale=body.clinical_rationale,
        recommendation_or_question=body.recommendation_or_question,
        urgency=body.urgency,
        supporting_data=body.supporting_data,
        pharmacist_name=body.pharmacist_name,
    )
    input_snapshot = {
        "patient_id": body.patient_id,
        "prescriber_name": body.prescriber_name,
        "patient_context": patient_context,
        "medication_issue": body.medication_issue,
        "clinical_rationale": body.clinical_rationale,
        "recommendation_or_question": body.recommendation_or_question,
        "urgency": body.urgency,
        "supporting_data": body.supporting_data,
        "pharmacist_name": body.pharmacist_name,
        "format": body.format,
        "language": body.language,
    }

    note: str | None = None
    try:
        baseline = build_baseline(message_input, body.format)
        content, meta = await render(baseline, message_input, body.format, body.language)
        note = meta.note
    except Exception as exc:  # pragma: no cover - defensive fail-safe
        baseline = build_baseline(message_input, body.format)
        content = baseline
        meta = LLMMeta(llm_used=False, provider="none", degraded=True)
        note = f"Physician message generation fell back to deterministic English content: {type(exc).__name__}."

    output_snapshot = {
        "message": _message_to_dict(content),
        "format": content.format,
        "language": content.language,
        "urgency": content.urgency,
        "llm_meta": asdict(meta),
        "pharmacist_verification_notice": PHARMACIST_VERIFICATION_NOTICE,
    }
    if note:
        output_snapshot["note"] = note

    await _write_audit(
        db=db,
        staff=staff,
        patient_id=patient.id if patient else None,
        input_snapshot=input_snapshot,
        output_snapshot=output_snapshot,
    )

    return {
        "format": content.format,
        "language": content.language,
        "urgency": content.urgency,
        "message": _message_to_dict(content),
        "llm_used": meta.llm_used,
        "degraded": meta.degraded,
        "pharmacist_verification_notice": PHARMACIST_VERIFICATION_NOTICE,
        **({"note": note} if note else {}),
    }

