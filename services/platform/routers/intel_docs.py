"""
Intelligence · Docs — #18 Automated Clinical Documentation
===========================================================
Mounted at /api/v1/intelligence/docs.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.database import get_db
from services.platform.auth import get_current_user
from services.ai.intelligence_core import Tier
from services.ai.intelligence_services import clinical_docs


def _tier(force_tier: Optional[str]) -> Optional[Tier]:
    return Tier(force_tier) if force_tier in ("local", "cloud") else None


router = APIRouter(tags=["intelligence: docs"])


class SOAPRequest(BaseModel):
    transcript_id:   Optional[str] = None
    transcript_text: Optional[str] = None
    patient_context: Optional[str] = None


@router.post("/draft-soap")
async def draft_soap(
    body:    SOAPRequest,
    force_tier: Optional[str] = Query(None),
    db:      AsyncSession = Depends(get_db),
    _current: dict        = Depends(get_current_user),
):
    """Draft a SOAP note from a transcript. Pharmacist reviews and signs."""
    return await clinical_docs.draft_soap(
        db, transcript_id=body.transcript_id, transcript_text=body.transcript_text,
        patient_context=body.patient_context, force_tier=_tier(force_tier),
    )


class MTMRequest(BaseModel):
    mtm_type:        str = "CMR"
    transcript_id:   Optional[str] = None
    transcript_text: Optional[str] = None
    medication_list: Optional[list] = None


@router.post("/draft-mtm")
async def draft_mtm(
    body:    MTMRequest,
    force_tier: Optional[str] = Query(None),
    db:      AsyncSession = Depends(get_db),
    _current: dict        = Depends(get_current_user),
):
    """Draft an MTM (CMR/TMR/MAP) document. Pharmacist reviews and signs."""
    return await clinical_docs.draft_mtm(
        db, mtm_type=body.mtm_type, transcript_id=body.transcript_id,
        transcript_text=body.transcript_text, medication_list=body.medication_list,
        force_tier=_tier(force_tier),
    )
