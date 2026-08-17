"""Surveillance ingest and query.

What crosses this boundary is EVENTS ONLY — no frames, no crops, no audio, no
embeddings. The edge node does detection, embedding and matching; what arrives
here is a decision with its working. `ObservationIn` deliberately has no field
that raw media could arrive in, and a test enforces that.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.core.rf_mapping import (AccessPoint, RadioMap, RssiSample,
                                      locate, occupancy_heatmap)
from services.core.surveillance.recorder import record
from services.platform.auth import require_permission
from services.platform.database import get_db
from shared.models.auth import Staff
from shared.models.surveillance_log import SurveillanceObservation

router = APIRouter(tags=["surveillance"])


class ContributionIn(BaseModel):
    modality: str
    similarity: float
    calibrated: float = 0.0
    quality: float = 0.0
    weight: float = 0.0
    log_lambda: float = 0.0


class ExclusionIn(BaseModel):
    modality: str
    reason: str


class ObservationIn(BaseModel):
    """A decision and its working. No media fields, by design."""
    site: str
    action_type: str
    observed_at: datetime
    zone_id: Optional[str] = None
    camera_id: Optional[str] = None
    identity_id: Optional[UUID] = None
    decision: Optional[str] = None
    confidence: Optional[float] = None
    margin: Optional[float] = None
    occlusion_stratum: Optional[str] = None
    contributions: list[ContributionIn] = Field(default_factory=list)
    excluded: list[ExclusionIn] = Field(default_factory=list)
    explanation: Optional[str] = None


class RssiIn(BaseModel):
    ap_id: str
    rssi_dbm: float


class RfBatchIn(BaseModel):
    site: str
    device_ref: str
    observed_at: datetime
    zone_id: Optional[str] = None
    samples: list[RssiIn]
    access_points: list[dict] = Field(default_factory=list)


@router.post("/observations", status_code=201)
async def ingest_observation(
    body: ObservationIn,
    staff: Staff = Depends(require_permission("clinical:write")),
    db: AsyncSession = Depends(get_db),
):
    """Record one capture observation from the edge."""
    from services.biometric.fusion import Contribution, Exclusion, FusedIdentity
    from services.biometric.occlusion import OcclusionStratum

    fused = None
    if body.decision is not None:
        fused = FusedIdentity(
            identity_id=body.identity_id,
            confidence=body.confidence or 0.0,
            decision=body.decision,
            margin=body.margin or 0.0,
            contributions=[Contribution(**c.model_dump())
                           for c in body.contributions],
            excluded=[Exclusion(**e.model_dump()) for e in body.excluded],
            explanation=body.explanation or "")

    stratum = None
    if body.occlusion_stratum:
        try:
            stratum = OcclusionStratum(body.occlusion_stratum)
        except ValueError:
            raise HTTPException(400, f"unknown occlusion stratum "
                                     f"{body.occlusion_stratum!r}")

    try:
        obs_id = await record(
            db, pharmacy_id=staff.pharmacy_id, site=body.site,
            action_type=body.action_type, observed_at=body.observed_at,
            fused=fused, zone_id=body.zone_id, camera_id=body.camera_id,
            stratum=stratum)
    except ValueError as e:
        raise HTTPException(400, str(e))
    await db.commit()

    # A failed identification is not the end of the interaction: tell the
    # counter what to do about it.
    action = None
    if fused is not None:
        from services.core.surveillance.escalation import escalate
        staff_action = escalate(fused, stratum=stratum)
        if staff_action is not None:
            action = {"prompt_fa": staff_action.prompt_fa,
                      "needs_provisional": staff_action.needs_provisional,
                      "reason": staff_action.reason}
    return {"observation_id": str(obs_id), "staff_action": action}


@router.post("/rf/batch", status_code=201)
async def ingest_rf_batch(
    body: RfBatchIn,
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    """Locate a device from an RSSI batch and record the fix.

    This locates a DEVICE, not a person: it is for staff handsets in the depot
    and feeds nothing in the biometric path.
    """
    aps = {a["ap_id"]: AccessPoint(**a) for a in body.access_points}
    samples = [RssiSample(s.ap_id, s.rssi_dbm) for s in body.samples]
    fix = locate(samples, aps, RadioMap([]))
    if fix is None:
        return {"observation_id": None,
                "reason": "not enough access points for a fix"}

    obs_id = await record(
        db, pharmacy_id=staff.pharmacy_id, site=body.site,
        action_type="movement", observed_at=body.observed_at,
        fix=fix, zone_id=body.zone_id, rf_device_ref=body.device_ref)
    await db.commit()
    return {"observation_id": str(obs_id), "x": fix.x, "y": fix.y,
            "uncertainty_m": fix.uncertainty_m, "method": fix.method}


@router.get("/heatmap")
async def heatmap(
    site: str = Query("depot"),
    width_m: float = Query(20.0),
    height_m: float = Query(15.0),
    cell_m: float = Query(2.0),
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    """Occupancy density from recorded RF fixes."""
    rows = (await db.execute(
        select(SurveillanceObservation.rf_x, SurveillanceObservation.rf_y)
        .where(SurveillanceObservation.pharmacy_id == staff.pharmacy_id,
               SurveillanceObservation.site == site,
               SurveillanceObservation.rf_x.isnot(None)))).all()
    points = [(float(x), float(y)) for x, y in rows]
    return {"site": site, "cell_m": cell_m, "points": len(points),
            "grid": occupancy_heatmap(points, width_m, height_m, cell_m)}
