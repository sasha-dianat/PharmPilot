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

from services.ai.counselling import engine
from services.ai.counselling.knowledge import resolve_facts
from services.ai.counselling.narrator import render
from services.ai.counselling.schema import (
    LANGUAGES,
    LEVELS,
    MODEL_VERSION,
    PHARMACIST_VERIFICATION_NOTICE,
    CounsellingContent,
    LLMMeta,
)
from services.platform.auth import get_current_staff, require_permission
from services.platform.database import get_db
from shared.models.auth import Staff
from shared.models.clinical import ClinicalAuditLog
from shared.models.patient import Patient
from shared.models.prescription import Prescription

router = APIRouter()

UNAVAILABLE_NOTE = "Structured counselling not available for this drug; pharmacist to counsel directly."


class CounsellingGenerateRequest(BaseModel):
    drug_name: str | None = Field(default=None)
    rx_id: UUID | None = None
    patient_id: UUID | None = None
    level: str = "standard"
    language: str = "en"

    @model_validator(mode="after")
    def validate_request(self):
        if not (self.drug_name and self.drug_name.strip()) and self.rx_id is None:
            raise ValueError("drug_name or rx_id is required")
        if self.level not in LEVELS:
            raise ValueError(f"level must be one of: {', '.join(LEVELS)}")
        if self.language not in LANGUAGES:
            raise ValueError(f"language must be one of: {', '.join(LANGUAGES)}")
        if self.drug_name:
            self.drug_name = self.drug_name.strip()
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


def _content_to_dict(content: CounsellingContent) -> dict[str, Any]:
    return {
        "what_for": content.what_for,
        "how_to_take": content.how_to_take,
        "what_to_avoid": content.what_to_avoid,
        "common_side_effects": content.common_side_effects,
        "serious_red_flags": content.serious_red_flags,
        "missed_dose": content.missed_dose,
        "adherence_tips": content.adherence_tips,
        "teach_back_questions": content.teach_back_questions,
        "level": content.level,
        "language": content.language,
    }


async def _resolve_drug_name(body: CounsellingGenerateRequest, staff: Staff, db: AsyncSession) -> tuple[str, UUID | None]:
    if body.rx_id is None:
        return body.drug_name or "", body.patient_id
    result = await db.execute(
        select(Prescription).where(
            Prescription.id == body.rx_id,
            Prescription.pharmacy_id == staff.pharmacy_id,
        )
    )
    prescription = result.scalar_one_or_none()
    if not prescription:
        raise HTTPException(404, "Prescription not found")
    return prescription.drug_name, prescription.patient_id


async def _tenant_scope_patient(patient_id: UUID | None, staff: Staff, db: AsyncSession) -> UUID | None:
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
    return patient.id


async def _write_audit(
    *,
    db: AsyncSession,
    staff: Staff,
    patient_id: UUID | None,
    input_snapshot: dict[str, Any],
    output_snapshot: dict[str, Any],
    rules_triggered: list[str],
) -> None:
    db.add(
        ClinicalAuditLog(
            user_id=staff.id,
            patient_id=patient_id,
            module="counselling",
            input_snapshot=_jsonable(input_snapshot),
            output_snapshot=_jsonable(output_snapshot),
            rules_triggered=rules_triggered,
            model_version=MODEL_VERSION,
            created_by=staff.id,
            updated_by=staff.id,
        )
    )
    await db.flush()


@router.post("/generate")
async def generate_counselling(
    body: CounsellingGenerateRequest,
    staff: Staff = Depends(require_permission("clinical:read")),
    db: AsyncSession = Depends(get_db),
):
    drug_name, resolved_patient_id = await _resolve_drug_name(body, staff, db)
    patient_id = await _tenant_scope_patient(resolved_patient_id, staff, db) if resolved_patient_id else None
    normalized, facts = resolve_facts(drug_name)
    input_snapshot = {
        "drug": drug_name,
        "normalized_drug": normalized,
        "rx_id": body.rx_id,
        "patient_id": patient_id,
        "level": body.level,
        "language": body.language,
    }

    if facts is None:
        llm_meta = LLMMeta(llm_used=False, provider="none", degraded=False)
        output_snapshot = {
            "available": False,
            "content": None,
            "llm_meta": asdict(llm_meta),
            "note": UNAVAILABLE_NOTE,
        }
        await _write_audit(
            db=db,
            staff=staff,
            patient_id=patient_id,
            input_snapshot=input_snapshot,
            output_snapshot=output_snapshot,
            rules_triggered=[],
        )
        return {
            "available": False,
            "drug_name": drug_name,
            "level": body.level,
            "language": body.language,
            "content": None,
            "llm_used": False,
            "degraded": False,
            "pharmacist_verification_notice": PHARMACIST_VERIFICATION_NOTICE,
            "note": UNAVAILABLE_NOTE,
        }

    try:
        baseline = engine.build_baseline(drug_name, level="standard")
        if baseline is None:
            raise RuntimeError("resolved facts did not produce baseline content")
        content, meta = await render(baseline, facts, body.level, body.language)
        note = meta.note
    except Exception as exc:  # pragma: no cover - defensive fail-safe
        baseline = engine.build_baseline(drug_name, level="standard")
        content = baseline
        meta = LLMMeta(llm_used=False, provider="none", degraded=True)
        note = f"Counselling generation fell back to deterministic English content: {type(exc).__name__}."

    content_dict = _content_to_dict(content) if content else None
    output_snapshot = {
        "available": content is not None,
        "content": content_dict,
        "llm_meta": asdict(meta),
        "evidence_source": facts.evidence_source,
    }
    if note:
        output_snapshot["note"] = note

    await _write_audit(
        db=db,
        staff=staff,
        patient_id=patient_id,
        input_snapshot=input_snapshot,
        output_snapshot=output_snapshot,
        rules_triggered=[normalized] if normalized else [],
    )

    return {
        "available": content is not None,
        "drug_name": drug_name,
        "level": content.level if content else body.level,
        "language": content.language if content else body.language,
        "content": content_dict,
        "llm_used": meta.llm_used,
        "degraded": meta.degraded,
        "pharmacist_verification_notice": PHARMACIST_VERIFICATION_NOTICE,
        **({"note": note} if note else {}),
    }
