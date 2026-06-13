from __future__ import annotations

import inspect
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.ai.clinical_decision_support.normalizer import normalize
from services.ai.drug_intelligence import engine
from services.ai.drug_intelligence.schema import (
    MODEL_VERSION,
    PHARMACIST_VERIFICATION_NOTICE,
    SECTION_BY_KEY,
    SECTIONS,
    TRAINABLE_NOTE,
    DrugMonograph,
    MonographSection,
)
from services.ai.drug_intelligence.synthesizer import synthesize_local
from services.ai.knowledge_engine.vector_store import ClinicalVectorStore
from services.ai.second_brain.schema import SCORE_THRESHOLD, VECTOR_STORE_UNAVAILABLE_TEXT
from services.platform.auth import get_current_staff, require_permission
from services.platform.config import settings
from services.platform.database import get_db
from shared.models.auth import Staff
from shared.models.clinical import ClinicalAuditLog
from shared.models.prescription import Prescription

router = APIRouter()


class DrugMonographRequest(BaseModel):
    drug_name: str | None = None
    rx_id: UUID | None = None
    sections: list[str] | None = None
    top_k: int = Field(6, ge=1, le=12)

    @field_validator("drug_name")
    @classmethod
    def drug_name_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("drug_name must not be empty")
        return stripped

    @field_validator("sections")
    @classmethod
    def known_sections_only(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [section.strip() for section in value if section.strip()]
        unknown = sorted(set(cleaned) - set(SECTION_BY_KEY))
        if unknown:
            raise ValueError(f"Unknown section key(s): {', '.join(unknown)}")
        return cleaned

    @model_validator(mode="after")
    def drug_name_or_rx_required(self) -> "DrugMonographRequest":
        if not self.drug_name and not self.rx_id:
            raise ValueError("One of drug_name or rx_id is required")
        return self


class SourceResponse(BaseModel):
    source_id: str
    source_title: str
    source_type: str
    snippet: str
    similarity_score: float
    evidence_grade: str | None = None
    url: str | None = None


class MonographSectionResponse(BaseModel):
    key: str
    label: str
    answer: str
    sources: list[SourceResponse]
    confidence: str
    refused: bool
    unsupported: bool
    llm_used: bool


class DrugMonographResponse(BaseModel):
    drug_name: str
    normalized_name: str
    model_version: str
    sections: list[MonographSectionResponse]
    any_evidence: bool
    llm_used: bool
    degraded: bool
    pharmacist_verification_notice: str
    trainable_note: str


def _get_vector_store() -> ClinicalVectorStore:
    try:
        from services.platform.routers.second_brain import _get_vector_store as second_brain_vector_store

        return second_brain_vector_store()
    except Exception:
        return ClinicalVectorStore(
            qdrant_url=getattr(settings, "QDRANT_URL", "http://localhost:6333"),
        )


async def _resolve_drug_name(
    body: DrugMonographRequest,
    staff: Staff,
    db: AsyncSession,
) -> str:
    if body.drug_name:
        return body.drug_name
    result = await db.execute(
        select(Prescription).where(
            Prescription.id == body.rx_id,
            Prescription.pharmacy_id == staff.pharmacy_id,
            Prescription.is_deleted == False,  # noqa: E712
        )
    )
    rx = result.scalar_one_or_none()
    if not rx:
        raise HTTPException(404, "Prescription not found")
    return rx.drug_name


def _make_retrieve(store: ClinicalVectorStore, normalized_drug: str):
    supports_filter_drugs = _supports_kwarg(store.search, "filter_drugs")

    def retrieve(question: str, top_k: int):
        kwargs: dict[str, Any] = {
            "top_k": top_k,
            "score_threshold": SCORE_THRESHOLD,
        }
        if supports_filter_drugs and normalized_drug:
            kwargs["filter_drugs"] = [normalized_drug]
        return store.search(question, **kwargs)

    return retrieve


def _supports_kwarg(fn: Any, name: str) -> bool:
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return True
    return name in signature.parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )


def _unavailable_monograph(drug_name: str, section_keys: list[str] | None) -> DrugMonograph:
    normalized_name = normalize(drug_name)
    selected = [SECTION_BY_KEY[key] for key in section_keys] if section_keys else list(SECTIONS)
    return DrugMonograph(
        drug_name=drug_name,
        normalized_name=normalized_name,
        sections=[
            MonographSection(
                key=section.key,
                label=section.label,
                answer=VECTOR_STORE_UNAVAILABLE_TEXT,
                sources=[],
                confidence="none",
                refused=True,
                unsupported=False,
                llm_used=False,
            )
            for section in selected
        ],
        any_evidence=False,
        llm_used=False,
        degraded=True,
    )


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
    body: DrugMonographRequest,
    monograph: DrugMonograph,
) -> None:
    output_snapshot = {
        section.key: {
            "answer": section.answer,
            "source_ids": [source.source_id for source in section.sources],
            "confidence": section.confidence,
            "refused": section.refused,
            "llm_used": section.llm_used,
        }
        for section in monograph.sections
    }
    db.add(
        ClinicalAuditLog(
            user_id=staff.id,
            patient_id=None,
            module="drug_intelligence",
            input_snapshot=_jsonable(
                {
                    "drug": monograph.drug_name,
                    "normalized_name": monograph.normalized_name,
                    "rx_id": body.rx_id,
                    "sections": [section.key for section in monograph.sections],
                    "top_k": body.top_k,
                }
            ),
            output_snapshot=_jsonable(output_snapshot),
            rules_triggered=[],
            model_version=MODEL_VERSION,
            created_by=staff.id,
            updated_by=staff.id,
        )
    )
    await db.flush()


@router.post("/monograph", response_model=DrugMonographResponse)
async def build_drug_monograph(
    body: DrugMonographRequest,
    staff: Staff = Depends(require_permission("clinical:read")),
    db: AsyncSession = Depends(get_db),
):
    drug_name = await _resolve_drug_name(body, staff, db)
    normalized_drug = normalize(drug_name)
    try:
        store = _get_vector_store()
        monograph = await engine.build_monograph(
            drug_name,
            retrieve=_make_retrieve(store, normalized_drug),
            synthesize=synthesize_local,
            sections=body.sections,
            top_k=body.top_k,
        )
    except Exception:
        monograph = _unavailable_monograph(drug_name, body.sections)

    await _write_audit(db=db, staff=staff, body=body, monograph=monograph)

    return {
        "drug_name": monograph.drug_name,
        "normalized_name": monograph.normalized_name,
        "model_version": MODEL_VERSION,
        "sections": [
            {
                "key": section.key,
                "label": section.label,
                "answer": section.answer,
                "sources": [asdict(source) for source in section.sources],
                "confidence": section.confidence,
                "refused": section.refused,
                "unsupported": section.unsupported,
                "llm_used": section.llm_used,
            }
            for section in monograph.sections
        ],
        "any_evidence": monograph.any_evidence,
        "llm_used": monograph.llm_used,
        "degraded": monograph.degraded,
        "pharmacist_verification_notice": PHARMACIST_VERIFICATION_NOTICE,
        "trainable_note": TRAINABLE_NOTE,
    }
