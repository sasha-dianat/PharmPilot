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

from datetime import datetime, timezone

from sqlalchemy import select

from services.platform.database import get_db
from services.platform.auth import get_current_user
from services.ai.intelligence_core import Tier, outbox
from services.ai.intelligence_services import expiry_prevention, supply_warning
from services.core.inventory import stock_intelligence
from shared.models.inventory import StockLevel, InventoryLot

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


@router.get("/turnover")
async def turnover(
    db:      AsyncSession = Depends(get_db),
    current: dict         = Depends(get_current_user),
):
    """Turnover / dead-stock / slow-fast-mover scores + inventory health.
    Deterministic analytics over stock_levels (+ lot unit cost). Local-complete —
    complements expiry-risk and supply-risk with the capital-efficiency view."""
    pharmacy_id = current.get("pharmacy_id") or ""
    rows = (await db.execute(
        select(StockLevel).where(StockLevel.pharmacy_id == pharmacy_id)
    )).scalars().all()

    # Representative unit cost per NDC (latest non-null lot cost).
    cost: dict[str, float] = {}
    lot_rows = (await db.execute(
        select(InventoryLot.ndc11, InventoryLot.unit_cost, InventoryLot.received_at)
        .where(InventoryLot.pharmacy_id == pharmacy_id)
        .order_by(InventoryLot.received_at.asc())
    )).all()
    for ndc, uc, _ in lot_rows:
        if uc is not None:
            cost[ndc] = float(uc)  # last write wins → most recent received

    now = datetime.now(timezone.utc)
    items = []
    for s in rows:
        ld = s.last_dispensed_at
        items.append({
            "ndc11": s.ndc11,
            "on_hand": float(s.quantity_on_hand or 0.0),
            "unit_cost": cost.get(s.ndc11),
            "avg_daily_demand": float(s.avg_daily_demand or 0.0),
            "last_dispensed_days": (now - ld).days if ld else None,
        })
    return stock_intelligence.summarize(items)
