"""
Intelligence · Label — #8 Personalized Label Language Simplification
=====================================================================
Mounted at /api/v1/intelligence/label.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.database import get_db
from services.platform.auth import get_current_user
from services.ai.intelligence_core import Tier
from services.ai.intelligence_services import label_simplifier

router = APIRouter(tags=["intelligence: label"])


def _tier(force_tier: Optional[str]) -> Optional[Tier]:
    return Tier(force_tier) if force_tier in ("local", "cloud") else None


class SimplifyRequest(BaseModel):
    sig:         str
    drug_name:   str
    language:    str = "en"
    patient_age: Optional[int] = None


@router.post("/simplify")
async def simplify(
    body:    SimplifyRequest,
    force_tier: Optional[str] = Query(None),
    db:      AsyncSession = Depends(get_db),
    _current: dict        = Depends(get_current_user),
):
    """
    Returns patient-facing plain-language instructions (default label text) PLUS
    pharmacist-facing reference dosing retrieved from the trainable corpus.
    """
    return await label_simplifier.simplify(
        db, sig=body.sig, drug_name=body.drug_name, language=body.language,
        patient_age=body.patient_age, force_tier=_tier(force_tier),
    )


class AddReferenceRequest(BaseModel):
    drug_name:   str
    dosing_text: str
    source:      str = "pharmacist"


@router.post("/add-reference")
async def add_reference(
    body:    AddReferenceRequest,
    _current: dict = Depends(get_current_user),
):
    """Grow the trainable dosing-reference corpus (embedded locally → offline-ready)."""
    return await label_simplifier.add_reference(
        drug_name=body.drug_name, dosing_text=body.dosing_text, source=body.source,
    )
