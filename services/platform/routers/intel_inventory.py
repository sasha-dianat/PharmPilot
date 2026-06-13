"""
Intelligence · Inventory — #12 Expiry Waste + #17 Supply-Chain Warning
=======================================================================
Mounted at /api/v1/intelligence/inventory. These power new sections inside the
EXISTING stock-ML panel (InventoryIntelligence), per the Master Prompt.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.database import get_db
from services.platform.auth import get_current_user
from services.ai.intelligence_core import Tier, outbox
from services.ai.intelligence_services import expiry_prevention, supply_warning

router = APIRouter(tags=["intelligence: inventory"])


def _tier(force_tier: Optional[str]) -> Optional[Tier]:
    return Tier(force_tier) if force_tier in ("local", "cloud") else None


@router.get("/expiry-risk")
async def expiry_risk(
    horizon_days: int = Query(180, ge=30, le=730),
    force_tier:   Optional[str] = Query(None),
    db:           AsyncSession = Depends(get_db),
    current:      dict         = Depends(get_current_user),
):
    """At-risk lots with FEFO + return recommendations. Local-complete."""
    pharmacy_id = current.get("pharmacy_id") or ""
    return await expiry_prevention.analyze(
        db, pharmacy_id, horizon_days=horizon_days, force_tier=_tier(force_tier),
    )


@router.get("/supply-risk")
async def supply_risk(
    window_days: int = Query(120, ge=30, le=365),
    force_tier:  Optional[str] = Query(None),
    db:          AsyncSession = Depends(get_db),
    current:     dict         = Depends(get_current_user),
):
    """NDC-level disruption risk + buffer suggestions. Local-complete."""
    pharmacy_id = current.get("pharmacy_id") or ""
    return await supply_warning.analyze(
        db, pharmacy_id, window_days=window_days, force_tier=_tier(force_tier),
    )


@router.post("/queue-buffer-order")
async def queue_buffer_order(
    ndc11:    str,
    quantity: float,
    db:       AsyncSession = Depends(get_db),
    current:  dict         = Depends(get_current_user),
):
    """
    Queue a buffer-stock order. When online this can submit immediately via EDI;
    when offline it is parked in the outbox and reconciled on reconnect — so the
    action never fails because of connectivity.
    """
    pharmacy_id = current.get("pharmacy_id") or ""
    entry_id = await outbox.enqueue(
        db, action_type="wholesaler_buffer_order",
        payload={"ndc11": ndc11, "quantity": quantity, "pharmacy_id": pharmacy_id,
                 "requested_by": current.get("sub")},
        dedup_key=f"buffer:{pharmacy_id}:{ndc11}",
        pharmacy_id=pharmacy_id,
    )
    # Best-effort immediate reconcile if online.
    report = await outbox.reconcile(db, limit=5)
    return {
        "queued":         True,
        "outbox_id":      entry_id,
        "submitted_now":  report.succeeded > 0,
        "still_offline":  report.still_offline,
    }
