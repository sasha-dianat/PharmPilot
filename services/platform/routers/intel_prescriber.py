"""
Intelligence · Prescriber — #13 Smart Prescriber Registry Enrichment endpoints
===============================================================================
Mounted at /api/v1/intelligence/prescriber.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.database import get_db
from services.platform.auth import get_current_user
from services.ai.intelligence_core import Tier
from services.ai.intelligence_services import prescriber_enrichment

router = APIRouter(tags=["intelligence: prescriber"])


def _tier(force_tier: Optional[str]) -> Optional[Tier]:
    return Tier(force_tier) if force_tier in ("local", "cloud") else None


@router.get("/{prescriber_id}/profile")
async def prescriber_profile(
    prescriber_id: str,
    force_tier:    Optional[str] = Query(None),
    db:            AsyncSession = Depends(get_db),
    current:       dict         = Depends(get_current_user),
):
    """Prescribing-distribution profile for this prescriber."""
    pharmacy_id = current.get("pharmacy_id") or ""
    return await prescriber_enrichment.get_profile(
        db, pharmacy_id, prescriber_id, force_tier=_tier(force_tier),
    )


class ScoreRxRequest(BaseModel):
    prescriber_id: str
    ndc:           str
    drug_name:     str = ""
    quantity:      float
    is_controlled: bool = False


@router.post("/score-rx")
async def score_rx(
    body:    ScoreRxRequest,
    force_tier: Optional[str] = Query(None),
    db:      AsyncSession = Depends(get_db),
    current: dict         = Depends(get_current_user),
):
    """Deviation score for a candidate Rx vs the prescriber's normal pattern."""
    pharmacy_id = current.get("pharmacy_id") or ""
    return await prescriber_enrichment.score_rx(
        db, pharmacy_id, body.prescriber_id,
        ndc=body.ndc, drug_name=body.drug_name, quantity=body.quantity,
        is_controlled=body.is_controlled, force_tier=_tier(force_tier),
    )


class EnrichRequest(BaseModel):
    prescriber_id: Optional[str] = None
    full_name:     str
    council_no:    str
    authority:     Optional[str] = None


@router.post("/enrich")
async def enrich_prescriber(
    body:    EnrichRequest,
    force_tier: Optional[str] = Query(None),
    db:      AsyncSession = Depends(get_db),
    current: dict         = Depends(get_current_user),
):
    """Validate council number (local) + queue external registry verification."""
    pharmacy_id = current.get("pharmacy_id") or ""
    return await prescriber_enrichment.enrich(
        db, prescriber_id=body.prescriber_id, full_name=body.full_name,
        council_no=body.council_no, authority=body.authority,
        pharmacy_id=pharmacy_id, force_tier=_tier(force_tier),
    )
