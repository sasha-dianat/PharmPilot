from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.ai.second_brain import engine
from services.ai.second_brain.schema import (
    DEFAULT_TOP_K,
    MAX_TOP_K,
    MODEL_VERSION,
    PHARMACIST_VERIFICATION_NOTICE,
    SCORE_THRESHOLD,
    VECTOR_STORE_UNAVAILABLE_TEXT,
    LLMMeta,
    SecondBrainResult,
)
from services.ai.second_brain.synthesizer import synthesize
from services.ai.knowledge_engine.vector_store import ClinicalVectorStore
from services.platform.auth import get_current_staff, require_permission
from services.platform.config import settings
from services.platform.database import get_db
from shared.models.auth import Staff
from shared.models.clinical import ClinicalAuditLog, Medication
from shared.models.patient import LabResult, Patient, PatientAllergy

router = APIRouter()


class SecondBrainQueryRequest(BaseModel):
    question: str = Field(..., min_length=1)
    patient_id: UUID | None = None
    top_k: int = Field(DEFAULT_TOP_K, ge=1, le=MAX_TOP_K)

    @field_validator("question")
    @classmethod
    def question_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Question must not be empty")
        return stripped


class SourceResponse(BaseModel):
    source_id: str
    source_title: str
    source_type: str
    snippet: str
    similarity_score: float
    evidence_grade: str | None = None
    url: str | None = None
    full_text: str = ""


class SecondBrainQueryResponse(BaseModel):
    question: str
    answer: str
    sources: list[SourceResponse]
    patient_context: str | None = None
    confidence: str
    refused: bool
    unsupported: bool
    llm_used: bool
    degraded: bool
    pharmacist_verification_notice: str


def _get_vector_store() -> ClinicalVectorStore:
    try:
        from services.platform.routers.knowledge import _get_vector_store as knowledge_vector_store

        return knowledge_vector_store()
    except Exception:
        return ClinicalVectorStore(
            qdrant_url=getattr(settings, "QDRANT_URL", "http://localhost:6334"),
        )


def _get_synthesizer():
    return synthesize


async def _tenant_scope_patient(
    patient_id: UUID,
    staff: Staff,
    db: AsyncSession,
) -> Patient:
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


async def _assemble_patient_context(
    *,
    db: AsyncSession,
    patient: Patient,
    staff: Staff,
) -> str:
    meds_result = await db.execute(
        select(Medication).where(
            Medication.patient_id == patient.id,
            Medication.pharmacy_id == staff.pharmacy_id,
            Medication.status == "active",
            Medication.is_deleted == False,  # noqa: E712
        )
    )
    medications = [
        row.drug_name
        for row in meds_result.scalars().all()
        if getattr(row, "drug_name", None)
    ]

    allergy_result = await db.execute(
        select(PatientAllergy).where(
            PatientAllergy.patient_id == patient.id,
            PatientAllergy.is_deleted == False,  # noqa: E712
        )
    )
    allergies = [
        row.allergen_name
        for row in allergy_result.scalars().all()
        if getattr(row, "allergen_name", None)
    ]

    lab_result = await db.execute(
        select(LabResult)
        .where(LabResult.patient_id == patient.id, LabResult.is_deleted == False)  # noqa: E712
        .order_by(LabResult.result_date.desc())
    )
    labs = []
    for row in lab_result.scalars().all()[:5]:
        value = f"{row.value}{f' {row.unit}' if getattr(row, 'unit', None) else ''}"
        result_date = getattr(row, "result_date", None)
        if isinstance(result_date, datetime):
            date_text = result_date.date().isoformat()
        elif isinstance(result_date, date):
            date_text = result_date.isoformat()
        else:
            date_text = "date unknown"
        labs.append(f"{row.test_name}: {value} ({date_text})")

    lines = [
        "PATIENT CONTEXT (deterministic, not sent to synthesis LLM):",
        f"Age: {_age_from_dob(getattr(patient, 'date_of_birth', None)) or 'Unknown'}",
        f"Conditions: {_join_or_none(getattr(patient, 'conditions', None) or [])}",
        f"Active medications: {_join_or_none(medications)}",
        f"Key labs: {_join_or_none(labs)}",
        f"Allergies: {_join_or_none(allergies)}",
    ]
    return "\n".join(lines)


def _age_from_dob(dob: date | None, today: date | None = None) -> int | None:
    if not dob:
        return None
    today = today or date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _join_or_none(items: list[str]) -> str:
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    return ", ".join(cleaned) if cleaned else "None recorded"


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


async def _write_audit(
    *,
    db: AsyncSession,
    staff: Staff,
    patient_id: UUID | None,
    input_snapshot: dict[str, Any],
    result: SecondBrainResult,
    llm_meta: LLMMeta,
) -> None:
    output_snapshot = {
        "answer": result.answer,
        "source_ids": [source.source_id for source in result.sources],
        "confidence": result.confidence,
        "refused": result.refused,
        "unsupported": result.unsupported,
        "llm_meta": asdict(llm_meta),
    }
    db.add(
        ClinicalAuditLog(
            user_id=staff.id,
            patient_id=patient_id,
            module="second_brain",
            input_snapshot=_jsonable(input_snapshot),
            output_snapshot=_jsonable(output_snapshot),
            rules_triggered=[],
            model_version=MODEL_VERSION,
            created_by=staff.id,
            updated_by=staff.id,
        )
    )
    await db.flush()


@router.post("/query", response_model=SecondBrainQueryResponse)
async def query_second_brain(
    body: SecondBrainQueryRequest,
    staff: Staff = Depends(require_permission("clinical:read")),
    db: AsyncSession = Depends(get_db),
):
    patient: Patient | None = None
    patient_context: str | None = None
    if body.patient_id:
        patient = await _tenant_scope_patient(body.patient_id, staff, db)
        patient_context = await _assemble_patient_context(db=db, patient=patient, staff=staff)

    store = _get_vector_store()

    def retrieve(question: str, top_k: int):
        return store.search(question, top_k=top_k, score_threshold=SCORE_THRESHOLD)

    try:
        result, llm_meta = await engine.answer(
            body.question,
            retrieve=retrieve,
            synthesize=_get_synthesizer(),
            patient_context=patient_context,
            top_k=body.top_k,
        )
    except Exception:
        result = SecondBrainResult(
            answer=VECTOR_STORE_UNAVAILABLE_TEXT,
            sources=[],
            patient_context=patient_context,
            confidence="none",
            refused=True,
            unsupported=False,
        )
        llm_meta = LLMMeta(llm_used=False, degraded=True)

    await _write_audit(
        db=db,
        staff=staff,
        patient_id=patient.id if patient else None,
        input_snapshot={
            "question": body.question,
            "top_k": body.top_k,
            "patient_id": body.patient_id,
        },
        result=result,
        llm_meta=llm_meta,
    )

    return {
        "question": body.question,
        "answer": result.answer,
        "sources": [asdict(source) for source in result.sources],
        "patient_context": result.patient_context,
        "confidence": result.confidence,
        "refused": result.refused,
        "unsupported": result.unsupported,
        "llm_used": llm_meta.llm_used,
        "degraded": llm_meta.degraded,
        "pharmacist_verification_notice": PHARMACIST_VERIFICATION_NOTICE,
    }
