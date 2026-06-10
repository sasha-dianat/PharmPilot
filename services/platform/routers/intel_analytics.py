"""
Intelligence · Analytics — #3 Natural Language Analytics ("Ask Your Data")
==========================================================================
Mounted at /api/v1/intelligence/analytics.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.database import get_db
from services.platform.auth import get_current_user
from services.ai.intelligence_core import Tier
from services.ai.intelligence_services import analytics_qa

router = APIRouter(tags=["intelligence: analytics"])


class AskRequest(BaseModel):
    question: str


@router.post("/ask")
async def ask(
    body:    AskRequest,
    force_tier: Optional[str] = Query(None),
    db:      AsyncSession = Depends(get_db),
    current: dict         = Depends(get_current_user),
):
    """Plain-language question → guarded SQL → narrative answer (+ chart)."""
    pharmacy_id = current.get("pharmacy_id") or ""
    ft = Tier(force_tier) if force_tier in ("local", "cloud") else None
    return await analytics_qa.ask(db, pharmacy_id, body.question, force_tier=ft)
