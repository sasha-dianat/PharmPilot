"""Inventory management router — drug catalog, stock levels, orders, receiving."""
from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.auth import get_current_staff, require_permission
from services.platform.database import get_db
from shared.models.auth import Staff
from shared.models.inventory import (
    DrugProduct, InventoryLot, PurchaseOrder, PurchaseOrderLine,
    ReceivingRecord, StockLevel
)

router = APIRouter()


# ── Drug catalog ─────────────────────────────────────────────────────────────

@router.get("/drugs/search")
async def search_drugs(
    q: str = Query(..., min_length=2, description="Drug name or NDC"),
    include_discontinued: bool = False,
    limit: int = Query(20, le=100),
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    """Search drug catalog by name (fuzzy) or exact NDC-11."""
    stmt = select(DrugProduct).where(DrugProduct.is_active == True)  # noqa: E712

    if not include_discontinued:
        stmt = stmt.where(DrugProduct.discontinued == False)  # noqa: E712

    # NDC search
    clean_q = q.replace("-", "")
    if clean_q.isdigit() and len(clean_q) >= 9:
        stmt = stmt.where(DrugProduct.ndc11.ilike(f"%{clean_q}%"))
    else:
        stmt = stmt.where(
            or_(
                DrugProduct.generic_name.ilike(f"%{q}%"),
                DrugProduct.brand_name.ilike(f"%{q}%"),
            )
        )

    stmt = stmt.order_by(DrugProduct.generic_name).limit(limit)
    result = await db.execute(stmt)
    drugs = result.scalars().all()

    return [
        {
            "id": str(d.id),
            "ndc11": d.ndc11,
            "generic_name": d.generic_name,
            "brand_name": d.brand_name,
            "strength": d.strength,
            "dosage_form": d.dosage_form,
            "route": d.route,
            "dea_schedule": d.dea_schedule,
            "is_controlled": d.is_controlled,
            "requires_refrigeration": d.requires_refrigeration,
            "awp_unit_price": float(d.awp_unit_price) if d.awp_unit_price else None,
        }
        for d in drugs
    ]


@router.get("/drugs/{ndc11}")
async def get_drug(
    ndc11: str,
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DrugProduct).where(DrugProduct.ndc11 == ndc11.replace("-", ""))
    )
    drug = result.scalar_one_or_none()
    if not drug:
        raise HTTPException(404, "Drug not found")
    return {
        "id": str(drug.id),
        "ndc11": drug.ndc11,
        "generic_name": drug.generic_name,
        "brand_name": drug.brand_name,
        "strength": drug.strength,
        "dosage_form": drug.dosage_form,
        "route": drug.route,
        "labeler_name": drug.labeler_name,
        "package_size": drug.package_size,
        "dea_schedule": drug.dea_schedule,
        "is_controlled": drug.is_controlled,
        "is_hazardous": drug.is_hazardous,
        "requires_refrigeration": drug.requires_refrigeration,
        "awp_unit_price": float(drug.awp_unit_price) if drug.awp_unit_price else None,
        "wac_price": float(drug.wac_price) if drug.wac_price else None,
    }


# ── Stock levels ─────────────────────────────────────────────────────────────

@router.get("/stock")
async def get_stock_levels(
    search: Optional[str] = None,
    below_par: bool = False,
    expiring_days: Optional[int] = None,
    limit: int = Query(100, le=500),
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    """Get current stock levels with optional filters."""
    # Join the drug catalog so the UI has real names + controlled/schedule flags.
    stmt = (select(StockLevel, DrugProduct)
            .join(DrugProduct, DrugProduct.ndc11 == StockLevel.ndc11, isouter=True)
            .where(StockLevel.pharmacy_id == staff.pharmacy_id))

    if below_par:
        stmt = stmt.where(
            StockLevel.par_level_min.isnot(None),
            StockLevel.quantity_on_hand < StockLevel.par_level_min,
        )

    if expiring_days is not None:
        # The parameter was accepted and silently ignored, so a caller asking
        # for "items expiring within 30 days" got the whole stock list back and
        # had no way to tell. Restrict to NDCs holding a lot that expires in the
        # window.
        from datetime import timedelta
        cutoff = date.today() + timedelta(days=expiring_days)
        stmt = stmt.where(StockLevel.ndc11.in_(
            select(InventoryLot.ndc11).where(
                InventoryLot.pharmacy_id == staff.pharmacy_id,
                InventoryLot.expiry_date <= cutoff,
                InventoryLot.quantity_on_hand > 0,
            )
        ))

    stmt = stmt.order_by(StockLevel.quantity_on_hand.desc()).limit(limit)
    rows = (await db.execute(stmt)).all()

    out = []
    for s, dp in rows:
        adq = float(s.avg_daily_demand) if s.avg_daily_demand else 0.0
        base = (dp.brand_name or dp.generic_name) if dp else None
        name = f"{base} {dp.strength}".strip() if (dp and base) else s.ndc11
        out.append({
            "ndc11": s.ndc11,
            "drug_name": name,
            "quantity_on_hand": float(s.quantity_on_hand),
            "quantity_reserved": float(s.quantity_reserved),
            "quantity_on_order": float(s.quantity_on_order),
            "par_level_min": float(s.par_level_min) if s.par_level_min else None,
            "par_level_max": float(s.par_level_max) if s.par_level_max else None,
            "reorder_point": float(s.reorder_point) if s.reorder_point else None,
            "reorder_quantity": float(s.reorder_quantity) if s.reorder_quantity else None,
            "avg_daily_demand": adq or None,
            "stockout_probability_7d": float(s.stockout_probability_7d) if s.stockout_probability_7d else None,
            "days_supply": round(float(s.quantity_on_hand) / adq, 1) if adq > 0 else None,
            "is_controlled": bool(dp.is_controlled) if dp else False,
            "dea_schedule": dp.dea_schedule if dp else None,
            "last_dispensed_at": s.last_dispensed_at.isoformat() if s.last_dispensed_at else None,
        })
    return out


@router.get("/stock/{ndc11}")
async def get_stock_for_ndc(
    ndc11: str,
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(StockLevel).where(
            StockLevel.pharmacy_id == staff.pharmacy_id,
            StockLevel.ndc11 == ndc11,
        )
    )
    stock = result.scalar_one_or_none()
    if not stock:
        return {"ndc11": ndc11, "quantity_on_hand": 0.0, "message": "No stock record found"}

    # Also get lot detail
    lots_result = await db.execute(
        select(InventoryLot).where(
            InventoryLot.pharmacy_id == staff.pharmacy_id,
            InventoryLot.ndc11 == ndc11,
            InventoryLot.quantity_on_hand > 0,
            InventoryLot.is_recalled == False,  # noqa: E712
            InventoryLot.is_quarantined == False,  # noqa: E712
        ).order_by(InventoryLot.expiry_date)
    )
    lots = lots_result.scalars().all()

    return {
        "ndc11": ndc11,
        "quantity_on_hand": float(stock.quantity_on_hand),
        "quantity_reserved": float(stock.quantity_reserved),
        "available": float(stock.quantity_on_hand) - float(stock.quantity_reserved),
        "reorder_point": float(stock.reorder_point) if stock.reorder_point else None,
        "stockout_risk": float(stock.stockout_probability_7d) if stock.stockout_probability_7d else None,
        "lots": [
            {
                "lot_number": l.lot_number,
                "expiry_date": l.expiry_date.isoformat(),
                "quantity": float(l.quantity_on_hand),
                "location": l.storage_location,
            }
            for l in lots
        ],
    }


# ── Expiry management ────────────────────────────────────────────────────────

@router.get("/expiring")
async def get_expiring_drugs(
    days: int = Query(90, description="Alert for lots expiring within N days"),
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    from datetime import timedelta, datetime, timezone
    cutoff = date.today() + timedelta(days=days)

    result = await db.execute(
        select(InventoryLot).where(
            InventoryLot.pharmacy_id == staff.pharmacy_id,
            InventoryLot.expiry_date <= cutoff,
            InventoryLot.quantity_on_hand > 0,
            InventoryLot.is_recalled == False,  # noqa: E712
        ).order_by(InventoryLot.expiry_date)
    )
    lots = result.scalars().all()

    return [
        {
            "ndc11": l.ndc11,
            "lot_number": l.lot_number,
            "expiry_date": l.expiry_date.isoformat(),
            "days_until_expiry": (l.expiry_date - date.today()).days,
            "quantity_on_hand": float(l.quantity_on_hand),
            "location": l.storage_location,
            "urgency": (
                "immediate" if (l.expiry_date - date.today()).days <= 14 else
                "high" if (l.expiry_date - date.today()).days <= 30 else
                "moderate" if (l.expiry_date - date.today()).days <= 60 else
                "low"
            ),
        }
        for l in lots
    ]


# ── Purchase orders ──────────────────────────────────────────────────────────

class PurchaseOrderCreate(BaseModel):
    wholesaler: str
    lines: list[dict]  # [{ndc11, quantity_ordered, unit_cost}]
    notes: Optional[str] = None


@router.post("/orders", status_code=201)
async def create_purchase_order(
    body: PurchaseOrderCreate,
    staff: Staff = Depends(require_permission("inventory:order")),
    db: AsyncSession = Depends(get_db),
):
    """Create a purchase order (can be AI-generated or manual)."""
    import secrets
    po_number = f"PO{date.today().strftime('%Y%m%d')}{secrets.token_hex(3).upper()}"

    po = PurchaseOrder(
        pharmacy_id=staff.pharmacy_id,
        wholesaler=body.wholesaler,
        po_number=po_number,
        status="draft",
        notes=body.notes,
        created_by=staff.id,
    )
    db.add(po)
    await db.flush()

    total = 0.0
    for line_data in body.lines:
        ndc = line_data["ndc11"]
        drug_result = await db.execute(
            select(DrugProduct).where(DrugProduct.ndc11 == ndc)
        )
        drug = drug_result.scalar_one_or_none()
        if not drug:
            raise HTTPException(404, f"Drug NDC {ndc} not in catalog")

        unit_cost = line_data.get("unit_cost") or (float(drug.wac_price) if drug.wac_price else 0.0)
        qty = float(line_data["quantity_ordered"])
        total += qty * unit_cost

        line = PurchaseOrderLine(
            order_id=po.id,
            drug_product_id=drug.id,
            ndc11=ndc,
            quantity_ordered=qty,
            unit_cost=unit_cost,
            created_by=staff.id,
        )
        db.add(line)

    po.total_cost = total
    return {"po_number": po_number, "po_id": str(po.id), "total_cost": total, "status": "draft"}


@router.post("/orders/{po_id}/submit")
async def submit_purchase_order(
    po_id: UUID,
    staff: Staff = Depends(require_permission("inventory:order")),
    db: AsyncSession = Depends(get_db),
):
    """Submit a draft PO to the wholesaler (EDI 850)."""
    from datetime import datetime, timezone
    # Tenant filter is part of the lookup, not a later check: without it, staff
    # at one pharmacy could submit another pharmacy's draft order to a
    # wholesaler, and the 404 below would never fire.
    result = await db.execute(select(PurchaseOrder).where(
        PurchaseOrder.id == po_id,
        PurchaseOrder.pharmacy_id == staff.pharmacy_id,
    ))
    po = result.scalar_one_or_none()
    if not po:
        raise HTTPException(404, "Purchase order not found")
    if po.status != "draft":
        raise HTTPException(422, f"Cannot submit PO in status: {po.status}")

    po.status = "submitted"
    po.ordered_at = datetime.now(timezone.utc)
    # In production: send EDI 850 to wholesaler
    logger.info("PO %s submitted to %s", po.po_number, po.wholesaler)
    return {"status": "submitted", "po_number": po.po_number}


import logging
logger = logging.getLogger(__name__)
