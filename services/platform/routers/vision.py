"""Zones, and the policy that attaches to them.

Tenancy comes from the authenticated staff row and never from the request. A
cross-tenant miss returns 404 rather than 403, because 403 confirms the row
exists.

Two permissions only — `vision:read` and `vision:write`. The design doc names
six more (clip, clip:elevated, export, audit, ingest), and every one of them
guards a surface that does not exist yet. `audio:read` is already granted to
two roles and required by zero routes; a declared-but-unenforced permission is
that same rot, so they are added when the routes are.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.core.vision import zones as Z
from services.platform.auth import require_permission
from services.platform.database import get_db
from shared.models.auth import Staff
from shared.models.vision import SITES, ZONE_CLASSES, VisionZone

router = APIRouter(tags=["vision"])


class ZoneIn(BaseModel):
    site: str
    code: str = Field(min_length=1, max_length=40)
    name_fa: str = Field(min_length=1, max_length=200)
    zone_class: str
    # Both NULL-able on purpose. Nothing purges yet and no schedule evaluator
    # exists, so a value here would be a policy claim we cannot honour.
    polygon: Optional[list[list[float]]] = None
    retention_days: Optional[int] = None
    armed_schedule: Optional[dict] = None


def _basis(value) -> str:
    """Say whether a policy number was set or was never decided.

    A NULL that renders as 0 is how an unenforced policy comes to look
    enforced. The caller gets the distinction explicitly.
    """
    return "declared" if value is not None else "not_set"


@router.get("/zones")
async def list_zones(
    site: Optional[str] = Query(None),
    staff: Staff = Depends(require_permission("vision:read")),
    db: AsyncSession = Depends(get_db),
):
    """Registered zones for this pharmacy, with their policy and its basis."""
    q = select(VisionZone).where(
        VisionZone.pharmacy_id == staff.pharmacy_id,
        VisionZone.is_deleted.is_(False))
    if site is not None:
        q = q.where(VisionZone.site == site)
    rows = (await db.execute(
        q.order_by(VisionZone.site, VisionZone.code))).scalars().all()
    return {"zones": [
        {"id": str(z.id), "site": z.site, "code": z.code,
         "name_fa": z.name_fa, "zone_class": z.zone_class,
         "polygon": z.polygon, "active": z.active,
         "retention_days": z.retention_days,
         "retention_basis": _basis(z.retention_days),
         "armed_schedule": z.armed_schedule,
         "schedule_basis": _basis(z.armed_schedule)}
        for z in rows]}


@router.post("/zones", status_code=201)
async def create_zone(
    body: ZoneIn,
    staff: Staff = Depends(require_permission("vision:write")),
    db: AsyncSession = Depends(get_db),
):
    """Register a zone. The code is immutable once observations reference it."""
    if body.site not in SITES:
        raise HTTPException(400, f"site must be one of {SITES}")
    if body.zone_class not in ZONE_CLASSES:
        raise HTTPException(400, f"zone_class must be one of {ZONE_CLASSES}")

    code = body.code.strip()
    if not code:
        raise HTTPException(400, "code must not be blank")

    # Checked here as well as by uq_zone_code_per_site, so the caller gets a
    # 409 naming the code rather than a 500 from an IntegrityError.
    existing = (await db.execute(select(VisionZone).where(
        VisionZone.pharmacy_id == staff.pharmacy_id,
        VisionZone.site == body.site,
        VisionZone.code == code))).scalars().first()
    if existing is not None:
        raise HTTPException(409, f"zone {code!r} already exists at {body.site}")

    zone = VisionZone(
        pharmacy_id=staff.pharmacy_id, site=body.site, code=code,
        name_fa=body.name_fa, zone_class=body.zone_class,
        polygon={"points": body.polygon} if body.polygon else None,
        retention_days=body.retention_days,
        armed_schedule=body.armed_schedule, created_by=staff.id)
    db.add(zone)
    await db.commit()
    # The recorder reads this set on every observation; a zone registered but
    # not visible until the next restart would refuse its own observations.
    Z.invalidate(staff.pharmacy_id, body.site)
    return {"id": str(zone.id), "code": zone.code, "site": zone.site}
