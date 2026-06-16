"""Procurement · AI Recommender — what/when/how-much-to-order.
=============================================================
Deterministic order-up-to replenishment over stock_levels (+ lot unit cost):
lead-time aware (avoid stockout) and expiry aware (don't overstock). Pure rules
live in `services.core.inventory.procurement`; this router only fetches rows and
assembles the items list. Local-complete — complements turnover/expiry/supply.
"""
from __future__ import annotations

from typing import Optional

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.database import get_db
from services.platform.auth import get_current_user
from services.core.inventory import procurement
from shared.models.inventory import StockLevel, InventoryLot

router = APIRouter(tags=["procurement: recommender"])


@router.get("/recommendations")
async def recommendations(
    budget:         Optional[float] = Query(None),
    lead_time_days: int             = Query(7, ge=1, le=90),
    db:             AsyncSession     = Depends(get_db),
    current:        dict             = Depends(get_current_user),
):
    """Deterministic procurement recommendations: what/when/how-much to order.

    Order-up-to logic with lead-time and expiry-aware caps. Returns per-SKU
    recommendations (only items needing units) sorted by urgency, plus a budget
    summary. Local-complete."""
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

    items = []
    for s in rows:
        items.append({
            "ndc11":            s.ndc11,
            "on_hand":          float(s.quantity_on_hand or 0.0),
            "avg_daily_demand": float(s.avg_daily_demand or 0.0),
            "unit_cost":        cost.get(s.ndc11),
            "reorder_point":    getattr(s, "reorder_point", None),
            "safety_stock":     getattr(s, "safety_stock", None) or 0.0,
            "par_level_max":    getattr(s, "par_level_max", None),
        })

    return procurement.build_recommendations(
        items,
        today=datetime.now(timezone.utc).date(),
        budget=budget,
        lead_time_default=lead_time_days,
    )
