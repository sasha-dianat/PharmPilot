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

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from services.platform.database import get_db
from services.platform.auth import get_current_user
from services.ai.intelligence_core import Tier, outbox
from services.ai.intelligence_services import expiry_prevention, supply_warning
from services.core.inventory import stock_intelligence
from shared.models.inventory import StockLevel, InventoryLot, InventoryMovement, DrugProduct

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


@router.get("/forecast")
async def demand_forecast(
    ndc11:   str          = Query(..., description="drug NDC to forecast"),
    db:      AsyncSession = Depends(get_db),
    current: dict         = Depends(get_current_user),
):
    """Real demand forecast for one drug via the ML DemandForecaster (Prophet
    ensemble with graceful fallback to the dispense-history average)."""
    from services.ai.inventory_intelligence.forecaster import DemandForecaster
    pharmacy_id = current.get("pharmacy_id") or ""
    try:
        f = await DemandForecaster(db=db).forecast(ndc11, pharmacy_id)
        adq = float(f.avg_daily_demand)
        std = float(f.std_dev_daily)
        f7, f30 = float(f.forecast_7d), float(f.forecast_30d)
        # When dispense history is too sparse for the ensemble, the forecaster
        # falls back to a flat rate — enrich it with the precomputed stock-level
        # demand so the forecast reflects this pharmacy's actual velocity.
        stock_adq = (await db.execute(select(StockLevel.avg_daily_demand).where(
            StockLevel.pharmacy_id == pharmacy_id, StockLevel.ndc11 == ndc11))).scalar_one_or_none()
        if stock_adq and float(stock_adq) > adq:
            adq = float(stock_adq)
            std = round(adq * 0.25, 3)
            f7, f30 = adq * 7, adq * 30
        return {
            "ndc11": f.ndc11,
            "avg_daily_demand": round(adq, 3),
            "std_dev_daily": round(std, 3),
            "forecast_7d": round(f7, 2),
            "forecast_30d": round(f30, 2),
            "stockout_probability_7d": round(float(f.stockout_probability_7d), 4),
            "trend": f.trend,
            "seasonal_factor": round(float(f.seasonal_factor), 3),
            "degraded": False,
        }
    except Exception:  # never fail the dashboard — fall back to flat estimate
        return {"ndc11": ndc11, "avg_daily_demand": 0.0, "std_dev_daily": 0.0,
                "forecast_7d": 0.0, "forecast_30d": 0.0, "stockout_probability_7d": 0.0,
                "trend": "stable", "seasonal_factor": 1.0, "degraded": True}


@router.get("/shrinkage")
async def shrinkage_anomalies(
    days:    int          = Query(60, ge=7, le=365),
    db:      AsyncSession = Depends(get_db),
    current: dict         = Depends(get_current_user),
):
    """Real shrinkage / stock-loss events from the inventory_movements ledger —
    damage, expiry/recall removals, and downward count corrections (qty lost)."""
    pharmacy_id = current.get("pharmacy_id") or ""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (await db.execute(
        select(InventoryMovement)
        .where(InventoryMovement.pharmacy_id == pharmacy_id,
               InventoryMovement.quantity_delta < 0,
               InventoryMovement.movement_type != "RETURN",
               InventoryMovement.created_at >= cutoff)
        .order_by(InventoryMovement.created_at.desc())
        .limit(50)
    )).scalars().all()

    # drug names for display
    ndcs = list({m.ndc11 for m in rows})
    names: dict[str, str] = {}
    if ndcs:
        for ndc, gen, brand in (await db.execute(
            select(DrugProduct.ndc11, DrugProduct.generic_name, DrugProduct.brand_name)
            .where(DrugProduct.ndc11.in_(ndcs))
        )).all():
            names[ndc] = brand or gen or ndc

    events = [{
        "ndc11": m.ndc11,
        "drug_name": names.get(m.ndc11, m.ndc11),
        "discrepancy": abs(float(m.quantity_delta)),
        "movement_type": m.movement_type,
        "reason": m.reason,
        "date": m.created_at.date().isoformat() if m.created_at else None,
    } for m in rows]
    total_units = round(sum(e["discrepancy"] for e in events), 2)
    return {"period_days": days, "events": events,
            "total_units_lost": total_units, "event_count": len(events)}
