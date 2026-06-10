"""
Intelligence · DUR — #5 Override Pattern Intelligence endpoints
================================================================
Mounted at /api/v1/intelligence/dur.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.database import get_db
from services.platform.auth import get_current_user
from services.ai.intelligence_core import Tier
from services.ai.intelligence_services import dur_intelligence

router = APIRouter(tags=["intelligence: dur"])


def _tier(force_tier: Optional[str]) -> Optional[Tier]:
    return Tier(force_tier) if force_tier in ("local", "cloud") else None


@router.get("/suggest")
async def suggest_reason(
    alert_type:  str = Query(..., description="DDI | ALLERGY | DUPLICATE | DOSE_HIGH | …"),
    drug_name:   str = Query("", description="Drug name for more specific suggestion"),
    top_k:       int = Query(3, ge=1, le=8),
    force_tier:  Optional[str] = Query(None),
    db:          AsyncSession = Depends(get_db),
    current:     dict         = Depends(get_current_user),
):
    """Most-likely override reason code(s) for this alert+drug — pre-fills the modal."""
    pharmacy_id = current.get("pharmacy_id") or ""
    return await dur_intelligence.suggest_reason(
        db, alert_type=alert_type, drug_name=drug_name, pharmacy_id=pharmacy_id,
        top_k=top_k, force_tier=_tier(force_tier),
    )


@router.get("/consistency-report")
async def consistency_report(
    force_tier: Optional[str] = Query(None),
    db:         AsyncSession = Depends(get_db),
    current:    dict         = Depends(get_current_user),
):
    """Per-pharmacist override-rate outlier report for QA review."""
    pharmacy_id = current.get("pharmacy_id") or ""
    return await dur_intelligence.consistency_report(
        db, pharmacy_id=pharmacy_id, force_tier=_tier(force_tier),
    )
