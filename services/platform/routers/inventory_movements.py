"""Inventory movements — manual adjustments, returns, damage/expiry/recall
write-offs, with a full append-only audit trail.

Every change records who (staff), when, where (lot), why (type + reason), and the
quantity before/after/delta. The lot's on-hand and the NDC-level StockLevel
aggregate are both updated in the same transaction so the books stay consistent.
Quantity math + validation live in `services.core.inventory.movements` (pure,
unit-tested); this layer is persistence + authorization only.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

from sqlalchemy.ext.asyncio import AsyncSession

from services.core.inventory import movements as M
from services.platform.auth import require_permission
from services.platform.database import get_db
from shared.models.auth import Staff
from shared.models.inventory import InventoryLot, InventoryMovement, StockLevel

router = APIRouter()


class MovementRequest(BaseModel):
    inventory_lot_id: UUID
    movement_type: str                 # ADJUSTMENT|CORRECTION|RETURN|DAMAGE|EXPIRY_REMOVAL|RECALL_REMOVAL
    reason: str
    new_quantity: Optional[float] = None  # required for ADJUSTMENT / CORRECTION
    quantity: Optional[float] = None      # required for removal types
    reference: Optional[str] = None       # RMA / PO / recall ref
    notes: Optional[str] = None


def _serialize(m: InventoryMovement) -> dict:
    return {
        "id": str(m.id),
        "ndc11": m.ndc11,
        "inventory_lot_id": str(m.inventory_lot_id) if m.inventory_lot_id else None,
        "movement_type": m.movement_type,
        "reason": m.reason,
        "quantity_before": float(m.quantity_before),
        "quantity_after": float(m.quantity_after),
        "quantity_delta": float(m.quantity_delta),
        "reference": m.reference,
        "notes": m.notes,
        "performed_by": str(m.created_by) if m.created_by else None,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


@router.post("/movements")
async def create_movement(
    body: MovementRequest,
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    """Record a stock movement against a lot and update on-hand + aggregate."""
    lot = (await db.execute(select(InventoryLot).where(
        InventoryLot.id == body.inventory_lot_id,
        InventoryLot.pharmacy_id == staff.pharmacy_id,
        InventoryLot.is_deleted == False,  # noqa: E712
    ))).scalar_one_or_none()
    if lot is None:
        raise HTTPException(404, "inventory lot not found")

    before = float(lot.quantity_on_hand or 0.0)
    try:
        plan = M.plan_movement(
            movement_type=body.movement_type,
            before=before,
            new_quantity=body.new_quantity,
            quantity=body.quantity,
        )
    except M.MovementError as e:
        raise HTTPException(422, str(e))

    # Apply to the lot.
    lot.quantity_on_hand = plan["quantity_after"]
    lot.updated_by = staff.id
    if body.movement_type == "RECALL_REMOVAL":
        lot.is_recalled = True

    # Apply the signed delta to the NDC-level aggregate (spans all lots).
    stock = (await db.execute(select(StockLevel).where(
        StockLevel.pharmacy_id == staff.pharmacy_id,
        StockLevel.ndc11 == lot.ndc11,
    ))).scalar_one_or_none()
    if stock is not None:
        stock.quantity_on_hand = max(0.0, float(stock.quantity_on_hand or 0.0) + plan["quantity_delta"])

    movement = InventoryMovement(
        pharmacy_id=staff.pharmacy_id,
        ndc11=lot.ndc11,
        inventory_lot_id=lot.id,
        movement_type=body.movement_type,
        reason=body.reason,
        quantity_before=plan["quantity_before"],
        quantity_after=plan["quantity_after"],
        quantity_delta=plan["quantity_delta"],
        reference=body.reference,
        notes=body.notes,
        created_by=staff.id,
        updated_by=staff.id,
    )
    db.add(movement)
    await db.commit()
    await db.refresh(movement)
    return _serialize(movement)


@router.get("/movements")
async def list_movements(
    ndc11: Optional[str] = Query(None),
    movement_type: Optional[str] = Query(None),
    inventory_lot_id: Optional[UUID] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    """Audit trail of stock movements, newest first. Filterable by NDC / type / lot."""
    q = select(InventoryMovement).where(InventoryMovement.pharmacy_id == staff.pharmacy_id)
    if ndc11:
        q = q.where(InventoryMovement.ndc11 == ndc11)
    if movement_type:
        q = q.where(InventoryMovement.movement_type == movement_type)
    if inventory_lot_id:
        q = q.where(InventoryMovement.inventory_lot_id == inventory_lot_id)
    q = q.order_by(InventoryMovement.created_at.desc()).limit(limit)
    rows = (await db.execute(q)).scalars().all()
    return {"movements": [_serialize(m) for m in rows], "count": len(rows)}
