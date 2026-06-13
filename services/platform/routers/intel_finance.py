"""
Intelligence · Finance — #19 Margin Optimization endpoints
===========================================================
Mounted at /api/v1/intelligence/finance.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.database import get_db
from services.platform.auth import get_current_user
from services.ai.intelligence_core import Tier
from services.ai.intelligence_services import margin_optimizer

router = APIRouter(tags=["intelligence: finance"])


@router.get("/margin-insights")
async def margin_insights(
    window_days: int = Query(90, ge=7, le=365),
    force_tier:  Optional[str] = Query(None, description="local|cloud (debug/offline test)"),
    db:          AsyncSession = Depends(get_db),
    current:     dict         = Depends(get_current_user),
):
    """
    Margin accounting + below-cost flags + generic-substitution opportunities +
    DIR exposure projection. Local-complete on cached pricing; cloud-enriched online.
    """
    pharmacy_id = current.get("pharmacy_id") or ""
    ft = Tier(force_tier) if force_tier in ("local", "cloud") else None
    return await margin_optimizer.analyze(
        db, pharmacy_id, window_days=window_days, force_tier=ft,
    )
