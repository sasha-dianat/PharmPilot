"""
Intelligence · Clinical — #11 Counseling Quality, #6 Integrity, #10 Compounding
================================================================================
Mounted at /api/v1/intelligence/clinical.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.database import get_db
from services.platform.auth import get_current_user
from services.ai.intelligence_core import Tier
from services.ai.intelligence_services import (
    counseling_quality, integrity_detection, compounding_compat,
)

router = APIRouter(tags=["intelligence: clinical"])


def _tier(ft: Optional[str]) -> Optional[Tier]:
    return Tier(ft) if ft in ("local", "cloud") else None


# ─── #11 Counseling Quality ───────────────────────────────────────────────────

class CounselingRequest(BaseModel):
    transcript_id:     Optional[str] = None
    transcript_text:   Optional[str] = None
    diarized_segments: Optional[list] = None
    duration_seconds:  int = 0
    is_complex:        bool = False


@router.post("/counseling/assess")
async def counseling_assess(
    body: CounselingRequest,
    force_tier: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    _current: dict = Depends(get_current_user),
):
    return await counseling_quality.assess(
        db, transcript_id=body.transcript_id, transcript_text=body.transcript_text,
        diarized_segments=body.diarized_segments, duration_seconds=body.duration_seconds,
        is_complex=body.is_complex, force_tier=_tier(force_tier),
    )


# ─── #6 Controlled-Substance Integrity ────────────────────────────────────────

class IntegrityRequest(BaseModel):
    dea_schedule:  Optional[str] = None
    is_controlled: bool = False
    doc_id:        Optional[str] = None
    prescriber_id: Optional[str] = None
    ndc:           str = ""
    drug_name:     str = ""
    quantity:      float = 0.0


@router.post("/integrity/score")
async def integrity_score(
    body: IntegrityRequest,
    force_tier: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current: dict = Depends(get_current_user),
):
    pharmacy_id = current.get("pharmacy_id") or ""
    return await integrity_detection.score(
        db, pharmacy_id, dea_schedule=body.dea_schedule, is_controlled=body.is_controlled,
        doc_id=body.doc_id, prescriber_id=body.prescriber_id, ndc=body.ndc,
        drug_name=body.drug_name, quantity=body.quantity, force_tier=_tier(force_tier),
    )


# ─── #10 Compounding Compatibility ────────────────────────────────────────────

class CompoundRequest(BaseModel):
    ingredients:       list[dict]
    patient_weight_kg: Optional[float] = None
    formula_bud_days:  Optional[int] = None


@router.post("/compound/compatibility")
async def compound_compatibility(
    body: CompoundRequest,
    force_tier: Optional[str] = Query(None),
    _current: dict = Depends(get_current_user),
):
    return await compounding_compat.analyze(
        ingredients=body.ingredients, patient_weight_kg=body.patient_weight_kg,
        formula_bud_days=body.formula_bud_days, force_tier=_tier(force_tier),
    )
