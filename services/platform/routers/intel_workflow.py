"""
Intelligence · Workflow — #2 Queue, #16 Trajectory, #15 Rx Copilot
===================================================================
Mounted at /api/v1/intelligence/workflow.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.database import get_db
from services.platform.auth import get_current_user, get_current_staff, require_permission
from services.ai.intelligence_core import Tier
from services.ai.intelligence_services import (
    queue_prioritizer, patient_trajectory, rx_copilot,
)
from shared.models.auth import Staff

router = APIRouter(tags=["intelligence: workflow"])


def _tier(ft: Optional[str]) -> Optional[Tier]:
    return Tier(ft) if ft in ("local", "cloud") else None


# ─── #2 Predictive Queue Prioritization ───────────────────────────────────────

@router.get("/queue/ranking")
async def queue_ranking(
    force_tier: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current: dict = Depends(get_current_user),
):
    pharmacy_id = current.get("pharmacy_id") or ""
    return await queue_prioritizer.rank(db, pharmacy_id, force_tier=_tier(force_tier))


@router.get("/queue/forecast")
async def queue_forecast(
    horizon_hours: int = Query(4, ge=1, le=12),
    force_tier: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current: dict = Depends(get_current_user),
):
    pharmacy_id = current.get("pharmacy_id") or ""
    return await queue_prioritizer.forecast(
        db, pharmacy_id, horizon_hours=horizon_hours, force_tier=_tier(force_tier))


# ─── #16 Patient Lifetime Trajectory ──────────────────────────────────────────

@router.get("/patient/{patient_id}/trajectory")
async def patient_trajectory_route(
    patient_id: str,
    force_tier: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    _current: dict = Depends(get_current_user),
):
    return await patient_trajectory.analyze(db, patient_id, force_tier=_tier(force_tier))


# ─── #15 Rx Workflow Copilot ──────────────────────────────────────────────────

@router.get("/rx/{rx_id}/copilot")
async def rx_copilot_evaluate(
    rx_id: str,
    force_tier: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    _current: dict = Depends(get_current_user),
):
    return await rx_copilot.evaluate(db, rx_id, force_tier=_tier(force_tier))


class AutoAdvanceRequest(BaseModel):
    step:       str
    thresholds: Optional[dict] = None


@router.post("/rx/{rx_id}/auto-advance")
async def rx_auto_advance(
    rx_id: str,
    body: AutoAdvanceRequest,
    db: AsyncSession = Depends(get_db),
    staff: Staff = Depends(get_current_staff),
):
    """
    NOTE on permissions: the only step this can ever actually EXECUTE (pilot
    phase) is `adjudication` -> READY_TO_FILL — a transition that, when done
    manually via POST /prescriptions/{rx_id}/transition, is explicitly gated
    behind `staff.has_permission("rx:verify")` (pharmacist verification). An AI
    shortcut must never grant a staff member an outcome they couldn't reach by
    hand, so we apply the identical gate here before allowing an auto-executable
    step to proceed. (`dur`/`verification` remain manual decision-aids and never
    transition anything, so no extra gate is needed for them — any authenticated
    staff member may ask the copilot for its read.)
    """
    if body.step in rx_copilot.AUTO_EXECUTABLE_STEPS and not staff.has_permission("rx:verify"):
        raise HTTPException(
            status_code=403,
            detail=f"Pharmacist verification required to auto-advance '{body.step}'. "
                   f"Your role: {staff.role}",
        )
    return await rx_copilot.auto_advance(
        db, rx_id, body.step, staff_id=staff.id, thresholds=body.thresholds)


class UndoAutoAdvanceRequest(BaseModel):
    action_id: str


@router.post("/rx/{rx_id}/auto-advance/undo")
async def rx_auto_advance_undo(
    rx_id: str,
    body: UndoAutoAdvanceRequest,
    db: AsyncSession = Depends(get_db),
    staff: Staff = Depends(require_permission("rx:verify")),
):
    """
    Pharmacist-initiated undo of a prior pilot-phase auto-advance. Gated behind
    the same `rx:verify` permission as the action it reverses — undoing a
    pharmacist-grade decision is itself a pharmacist-grade decision.
    """
    return await rx_copilot.undo_auto_advance(
        db, rx_id, body.action_id, staff_id=staff.id)
