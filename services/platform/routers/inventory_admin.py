"""Inventory administration — search, drill-down, correction, bulk edit, receiving.

The working surface an administrator spends the day in. It is deliberately
powerful, and it is safe for exactly one reason: **it cannot change a quantity**.

Quantities move only through `services.core.inventory.ledger` — a receipt, a
dispense, a counted variance, or an approved write-off — so every unit that ever
entered or left has a movement row explaining it, chained and append-only. Field
corrections (location, cost, par levels, formulary binding) apply immediately
and are audited; corrections that could hide something (extending an expiry,
releasing a recalled or quarantined lot) become approval requests in the same
queue as write-offs.

`services.core.inventory.admin_rules` holds that policy as pure functions, so
each rule is provable by a test rather than being a branch inside a handler.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from services.core.inventory import admin_rules as A
from services.core.inventory import approvals_sla as SLA
from services.core.inventory import clock as CLK
from services.core.inventory import ledger as L
from services.core.inventory import receipt_anomaly as RA
from services.core.inventory import receiving as RCV
from services.core.inventory import reservation_service as RS
from services.core.inventory import shelf_price as SP
from services.platform.auth import require_permission
from services.platform.database import get_db
from services.platform.routers.inventory_integrity import append_movement, iso_utc
from shared.models.auth import Staff
from shared.models.inventory import (
    DrugProduct, InventoryApproval, InventoryLot, PurchaseOrder,
    PurchaseOrderLine, StockLevel,
)

router = APIRouter()
log = logging.getLogger(__name__)

DEAD_STOCK_DAYS = 180
EXPIRING_SOON_DAYS = 90


# ── Search ────────────────────────────────────────────────────────────────

def _filter_sql(f: str) -> str:
    """SQL predicate for a named view. Kept beside `A.FILTERS` so a filter can
    never appear in the UI without a definition here."""
    today = "CURRENT_DATE"
    return {
        "all": "TRUE",
        "below_par": "s.par_level_min IS NOT NULL AND agg.on_hand < s.par_level_min",
        "out_of_stock": "COALESCE(agg.on_hand, 0) <= 0",
        "expiring_soon": f"agg.next_expiry IS NOT NULL "
                         f"AND agg.next_expiry <= {today} + CAST(:soon AS integer) "
                         f"AND agg.next_expiry >= {today} AND agg.on_hand > 0",
        "expired": f"agg.next_expiry IS NOT NULL AND agg.next_expiry < {today} AND agg.on_hand > 0",
        "quarantined": "agg.quarantined_lots > 0",
        "recalled": "agg.recalled_lots > 0",
        "controlled": "dp.is_controlled = true",
        "unbound": "s.irc IS NULL",
        "dead_stock": "agg.on_hand > 0 AND (s.last_dispensed_at IS NULL "
                      f"OR s.last_dispensed_at < NOW() - make_interval(days => CAST(:dead AS integer)))",
        "cold_chain": "agg.breached_lots > 0",
        "overstocked": "s.avg_daily_demand > 0 AND agg.on_hand / s.avg_daily_demand > 365",
    }.get(f, "TRUE")


_SORT_SQL = {
    "name": "drug_name ASC",
    "quantity": "COALESCE(agg.on_hand,0) DESC",
    "expiry": "agg.next_expiry ASC NULLS LAST",
    "value": "COALESCE(agg.value,0) DESC",
    "days_supply": "days_supply ASC NULLS LAST",
    "last_dispensed": "s.last_dispensed_at DESC NULLS LAST",
}


@router.get("/admin/items")
async def search_items(
    q: Optional[str] = Query(None, description="name, IRC, NDC, GTIN or lot number"),
    filter: str = Query("all"),
    sort: str = Query("name"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    """One search box over every identifier an administrator might have in hand,
    plus the named views that match the questions they actually ask."""
    if filter not in A.FILTERS:
        raise HTTPException(422, f"unknown filter {filter!r}")
    if sort not in A.SORTS:
        raise HTTPException(422, f"unknown sort {sort!r}")

    parsed = A.normalize_query(q)
    params: dict[str, Any] = {"pid": staff.pharmacy_id, "lim": limit, "off": offset,
                              "soon": EXPIRING_SOON_DAYS, "dead": DEAD_STOCK_DAYS}

    where = [_filter_sql(filter)]
    if parsed["kind"] == "name":
        where.append("(dp.generic_name ILIKE :q OR dp.brand_name ILIKE :q "
                     "OR dc.name_fa ILIKE :q OR dc.brand_name ILIKE :q)")
        params["q"] = f"%{parsed['value']}%"
    elif parsed["kind"] in ("irc_or_ndc", "numeric"):
        where.append("(s.ndc11 LIKE :q OR s.irc LIKE :q)")
        params["q"] = f"%{parsed['value']}%"
    elif parsed["kind"] == "gtin":
        where.append("(dc.gtin = :qexact OR s.irc = :qexact)")
        params["qexact"] = parsed["value"]
    elif parsed["kind"] == "lot":
        where.append("EXISTS (SELECT 1 FROM inventory_lots xl WHERE xl.ndc11 = s.ndc11 "
                     "AND xl.pharmacy_id = s.pharmacy_id AND UPPER(xl.lot_number) LIKE :q)")
        params["q"] = f"%{parsed['value']}%"

    sql = f"""
        WITH agg AS (
            SELECT ndc11, pharmacy_id,
                   SUM(quantity_on_hand)                              AS on_hand,
                   SUM(quantity_on_hand * COALESCE(unit_cost, 0))     AS value,
                   MIN(CASE WHEN quantity_on_hand > 0 THEN expiry_date END) AS next_expiry,
                   COUNT(*)                                           AS lot_count,
                   COUNT(*) FILTER (WHERE is_quarantined)             AS quarantined_lots,
                   COUNT(*) FILTER (WHERE is_recalled)                AS recalled_lots,
                   COUNT(*) FILTER (WHERE cold_chain_breach)          AS breached_lots
            FROM inventory_lots
            WHERE pharmacy_id = :pid AND is_deleted = false
            GROUP BY ndc11, pharmacy_id
        )
        SELECT s.ndc11, s.irc,
               COALESCE(dc.name_fa, dp.brand_name, dp.generic_name, s.ndc11) AS drug_name,
               dp.generic_name, dp.strength, dp.dosage_form, dp.is_controlled,
               dp.requires_refrigeration, dp.storage_condition, dp.lasa_group,
               dc.package_count, dc.announced_price,
               COALESCE(agg.on_hand, 0) AS on_hand, s.quantity_reserved,
               s.quantity_on_order, s.par_level_min, s.par_level_max,
               s.reorder_point, s.avg_daily_demand, s.last_dispensed_at,
               COALESCE(agg.value, 0) AS value, agg.next_expiry,
               COALESCE(agg.lot_count, 0) AS lot_count,
               COALESCE(agg.quarantined_lots, 0) AS quarantined_lots,
               COALESCE(agg.recalled_lots, 0) AS recalled_lots,
               COALESCE(agg.breached_lots, 0) AS breached_lots,
               CASE WHEN s.avg_daily_demand > 0
                    THEN ROUND(COALESCE(agg.on_hand,0) / s.avg_daily_demand, 1) END AS days_supply,
               COUNT(*) OVER () AS total_rows
        FROM stock_levels s
        LEFT JOIN agg ON agg.ndc11 = s.ndc11 AND agg.pharmacy_id = s.pharmacy_id
        LEFT JOIN drug_products dp ON dp.id = s.drug_product_id
        LEFT JOIN drug_catalog  dc ON dc.irc = s.irc
        WHERE s.pharmacy_id = :pid AND {' AND '.join(where)}
        ORDER BY {_SORT_SQL[sort]}
        LIMIT :lim OFFSET :off"""

    rows = (await db.execute(text(sql), params)).mappings().all()
    total = rows[0]["total_rows"] if rows else 0

    def out(r):
        d = {k: v for k, v in dict(r).items() if k != "total_rows"}
        for k in ("on_hand", "quantity_reserved", "quantity_on_order", "value",
                  "par_level_min", "par_level_max", "reorder_point",
                  "avg_daily_demand", "days_supply", "announced_price"):
            if d.get(k) is not None:
                d[k] = float(d[k])
        for k in ("next_expiry", "last_dispensed_at"):
            if d.get(k) is not None:
                d[k] = d[k].isoformat()
        d["expired"] = bool(d["next_expiry"] and
                            date.fromisoformat(d["next_expiry"][:10]) < date.today())
        return d

    return {"items": [out(r) for r in rows], "total": total,
            "limit": limit, "offset": offset,
            "query": parsed, "filter": filter, "sort": sort}


@router.get("/admin/filters")
async def filter_counts(
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    """How many items each named view holds, so the panel's chips carry live
    counts instead of the administrator having to click each one to find out."""
    params = {"pid": staff.pharmacy_id, "soon": EXPIRING_SOON_DAYS,
              "dead": DEAD_STOCK_DAYS}
    parts = [f"COUNT(*) FILTER (WHERE {_filter_sql(f)}) AS \"{f}\"" for f in A.FILTERS]
    sql = f"""
        WITH agg AS (
            SELECT ndc11, pharmacy_id, SUM(quantity_on_hand) AS on_hand,
                   MIN(CASE WHEN quantity_on_hand > 0 THEN expiry_date END) AS next_expiry,
                   COUNT(*) FILTER (WHERE is_quarantined) AS quarantined_lots,
                   COUNT(*) FILTER (WHERE is_recalled) AS recalled_lots,
                   COUNT(*) FILTER (WHERE cold_chain_breach) AS breached_lots
            FROM inventory_lots WHERE pharmacy_id = :pid AND is_deleted = false
            GROUP BY ndc11, pharmacy_id)
        SELECT {', '.join(parts)}
        FROM stock_levels s
        LEFT JOIN agg ON agg.ndc11 = s.ndc11 AND agg.pharmacy_id = s.pharmacy_id
        LEFT JOIN drug_products dp ON dp.id = s.drug_product_id
        WHERE s.pharmacy_id = :pid"""
    row = (await db.execute(text(sql), params)).mappings().one()
    return {"counts": {k: int(v) for k, v in row.items()},
            "labels": A.FILTERS, "sorts": sorted(A.SORTS)}


# ── Item detail: lots + full movement history ─────────────────────────────

@router.get("/admin/items/{ndc11}")
async def item_detail(
    ndc11: str,
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    """Everything known about one item: its lots in FEFO order, its complete
    movement history, and its formulary record."""
    p = {"pid": staff.pharmacy_id, "ndc": ndc11}
    head = (await db.execute(text("""
        SELECT s.ndc11, s.irc, s.par_level_min, s.par_level_max, s.reorder_point,
               s.avg_daily_demand, s.quantity_reserved, s.quantity_on_order,
               s.last_dispensed_at, s.last_received_at,
               dp.id AS drug_product_id, dp.generic_name, dp.brand_name, dp.strength,
               dp.dosage_form, dp.is_controlled, dp.dea_schedule,
               dp.requires_refrigeration, dp.storage_condition, dp.lasa_group,
               dc.name_fa, dc.announced_price, dc.package_count, dc.manufacturer,
               dc.gtin, dc.atc, dc.coverage
        FROM stock_levels s
        LEFT JOIN drug_products dp ON dp.id = s.drug_product_id
        LEFT JOIN drug_catalog  dc ON dc.irc = s.irc
        WHERE s.pharmacy_id = :pid AND s.ndc11 = :ndc"""), p)).mappings().first()
    if head is None:
        raise HTTPException(404, "item not stocked at this pharmacy")

    lots = (await db.execute(text("""
        SELECT id, lot_number, irc, expiry_date, quantity_on_hand, quantity_reserved,
               quantity_received, unit_cost, storage_location, received_at,
               is_quarantined, is_recalled, recall_reference, cold_chain_breach,
               split_pack_open, serial_number
        FROM inventory_lots
        WHERE pharmacy_id = :pid AND ndc11 = :ndc AND is_deleted = false
        ORDER BY expiry_date ASC NULLS LAST, lot_number"""), p)).mappings().all()

    movements = (await db.execute(text("""
        SELECT m.id, m.movement_type, m.reason, m.quantity_before, m.quantity_after,
               m.quantity_delta, m.reference, m.notes, m.created_at, m.created_by,
               m.inventory_lot_id, m.prescription_fill_id, m.approval_id,
               m.event_hash, st.role AS actor_role,
               NULLIF(TRIM(COALESCE(st.first_name,'') || ' ' || COALESCE(st.last_name,'')), '')
                 AS actor_name
        FROM inventory_movements m
        LEFT JOIN staff st ON st.id = m.created_by
        WHERE m.pharmacy_id = :pid AND m.ndc11 = :ndc
        ORDER BY m.created_at DESC LIMIT 200"""), p)).mappings().all()

    today = date.today()

    def lot_out(l):
        d = dict(l)
        exp = d["expiry_date"]
        d["id"] = str(d["id"])
        d["days_to_expiry"] = (exp - today).days if exp else None
        d["blocked_reason"] = ("recalled" if d["is_recalled"]
                               else "cold_chain_breach" if d["cold_chain_breach"]
                               else "quarantined" if d["is_quarantined"]
                               else "expired" if exp and exp < today else None)
        for k in ("quantity_on_hand", "quantity_reserved", "quantity_received", "unit_cost"):
            d[k] = float(d[k]) if d[k] is not None else None
        for k in ("expiry_date", "received_at"):
            d[k] = d[k].isoformat() if d[k] else None
        return d

    return {
        "item": {k: (float(v) if k in ("par_level_min", "par_level_max", "reorder_point",
                                       "avg_daily_demand", "quantity_reserved",
                                       "quantity_on_order", "announced_price")
                     and v is not None else
                     v.isoformat() if hasattr(v, "isoformat") else
                     str(v) if k == "drug_product_id" and v else v)
                 for k, v in dict(head).items()},
        "lots": [lot_out(l) for l in lots],
        "movements": [{
            "id": str(m["id"]), "movement_type": m["movement_type"],
            "reason": m["reason"], "delta": float(m["quantity_delta"]),
            "before": float(m["quantity_before"]), "after": float(m["quantity_after"]),
            "reference": m["reference"], "notes": m["notes"],
            "at": iso_utc(m["created_at"]) if m["created_at"] else None,
            "actor": m["actor_name"] or (str(m["created_by"])[:8] if m["created_by"] else None),
            "actor_role": m["actor_role"],
            "lot_id": str(m["inventory_lot_id"]) if m["inventory_lot_id"] else None,
            "fill_id": str(m["prescription_fill_id"]) if m["prescription_fill_id"] else None,
            "approval_id": str(m["approval_id"]) if m["approval_id"] else None,
            "chained": bool(m["event_hash"]),
        } for m in movements],
        "editable_fields": {"lot_direct": sorted(A.LOT_DIRECT_FIELDS),
                            "lot_sensitive": sorted(A.LOT_SENSITIVE_FIELDS),
                            "ledger_only": sorted(A.LOT_LEDGER_ONLY_FIELDS),
                            "stock_direct": sorted(A.STOCK_DIRECT_FIELDS)},
    }


# ── Edit one lot ──────────────────────────────────────────────────────────

class LotEdit(BaseModel):
    field: str
    value: Any = None
    reason: str = Field(..., min_length=3)


@router.patch("/admin/lots/{lot_id}")
async def edit_lot(
    lot_id: UUID, body: LotEdit,
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    """Correct one field on one lot.

    Quantities are refused outright. Corrections that could put blocked stock
    back on the shelf become an approval request rather than an edit.
    """
    lot = (await db.execute(select(InventoryLot).where(
        InventoryLot.id == lot_id,
        InventoryLot.pharmacy_id == staff.pharmacy_id,
        InventoryLot.is_deleted == False,  # noqa: E712
    ))).scalar_one_or_none()
    if lot is None:
        raise HTTPException(404, "lot not found")

    drug = (await db.execute(select(DrugProduct).where(
        DrugProduct.id == lot.drug_product_id))).scalar_one_or_none()
    is_controlled = bool(drug and drug.is_controlled)

    old = getattr(lot, body.field, None)
    try:
        plan = A.validate_lot_edit(body.field, old, body.value,
                                   reason=body.reason, is_controlled=is_controlled)
    except A.AdminEditError as e:
        raise HTTPException(422, str(e))

    if plan.requires_approval:
        appr = InventoryApproval(
            pharmacy_id=staff.pharmacy_id, irc=lot.irc, ndc11=lot.ndc11,
            inventory_lot_id=lot.id, movement_type="FIELD_EDIT", quantity=0,
            is_controlled=is_controlled, status="pending",
            reason=f"{body.field}: {old!r} → {body.value!r} — {body.reason}"[:240],
            payload={"table": "inventory_lots", "row_id": str(lot.id),
                     "field": body.field, "old": _jsonable(old),
                     "new": _jsonable(body.value), "reason": body.reason},
            requested_by_id=staff.id, created_by=staff.id, updated_by=staff.id,
            due_at=SLA.deadline("FIELD_EDIT", requested_at=datetime.now(timezone.utc),
                                is_controlled=is_controlled).due_at)
        db.add(appr)
        await db.commit()
        return {"applied": False, "approval_id": str(appr.id),
                "message": "این تغییر نیازمند تأیید نفر دوم است و در صف تأیید قرار گرفت."}

    _assign(lot, body.field, body.value)
    lot.updated_by = staff.id
    await db.commit()
    log.info("lot %s field %s changed by %s: %r → %r (%s)",
             lot_id, body.field, staff.id, old, body.value, body.reason)
    return {"applied": True, "field": body.field, "old": _jsonable(old),
            "new": _jsonable(body.value), "sensitive": plan.sensitive}


def _jsonable(v):
    return v.isoformat() if hasattr(v, "isoformat") else (
        float(v) if hasattr(v, "quantize") else v)


def _assign(obj, field: str, value):
    if field == "expiry_date" and isinstance(value, str):
        value = date.fromisoformat(value[:10])
    setattr(obj, field, value)


# ── Bulk edit ─────────────────────────────────────────────────────────────

class BulkEdit(BaseModel):
    lot_ids: list[UUID]
    field: str
    value: Any = None
    reason: str = Field(..., min_length=3)


@router.post("/admin/lots/bulk")
async def bulk_edit(
    body: BulkEdit,
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    """Apply one safe field to many lots. Bounded, and never a bulk *release* of
    blocked stock — that direction must be justified row by row."""
    try:
        A.validate_bulk(body.field, body.value, body.lot_ids, reason=body.reason)
    except A.AdminEditError as e:
        raise HTTPException(422, str(e))

    lots = (await db.execute(select(InventoryLot).where(
        InventoryLot.id.in_(body.lot_ids),
        InventoryLot.pharmacy_id == staff.pharmacy_id,
        InventoryLot.is_deleted == False,  # noqa: E712
    ))).scalars().all()
    missing = len(body.lot_ids) - len(lots)

    changed, skipped = 0, []
    for lot in lots:
        old = getattr(lot, body.field, None)
        if old == body.value:
            skipped.append({"lot_id": str(lot.id), "why": "already set"})
            continue
        _assign(lot, body.field, body.value)
        lot.updated_by = staff.id
        changed += 1
    await db.commit()
    log.info("bulk %s=%r on %d lots by %s (%s)", body.field, body.value,
             changed, staff.id, body.reason)
    return {"changed": changed, "unchanged": len(skipped),
            "not_found_or_other_pharmacy": missing, "skipped": skipped[:20]}


class StockEdit(BaseModel):
    field: str
    value: Any = None
    reason: str = Field(..., min_length=3)


@router.patch("/admin/stock/{ndc11}")
async def edit_stock_level(
    ndc11: str, body: StockEdit,
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    """Set par levels or the formulary binding on the item aggregate. The
    quantity columns on this row are ledger-derived and not editable here."""
    if body.field not in A.STOCK_DIRECT_FIELDS:
        raise HTTPException(422,
            f"{body.field} is not editable on the stock aggregate; "
            f"allowed: {sorted(A.STOCK_DIRECT_FIELDS)}")
    row = (await db.execute(select(StockLevel).where(
        StockLevel.pharmacy_id == staff.pharmacy_id,
        StockLevel.ndc11 == ndc11))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "item not stocked at this pharmacy")
    old = getattr(row, body.field)
    setattr(row, body.field, body.value)
    if body.field == "irc" and body.value:
        # Keep the lots in step, or the item and its own lots disagree about
        # which formulary product they are.
        await db.execute(text(
            "UPDATE inventory_lots SET irc = :irc "
            "WHERE pharmacy_id = :pid AND ndc11 = :ndc AND is_deleted = false"),
            {"irc": body.value, "pid": staff.pharmacy_id, "ndc": ndc11})
    await db.commit()
    return {"applied": True, "field": body.field,
            "old": _jsonable(old), "new": _jsonable(body.value)}


# ── Receiving: the only way stock enters ──────────────────────────────────

async def _reconcile_purchase_order(
    db: AsyncSession, *, pharmacy_id, po_id, ndc11: str, units: float,
    now: datetime, actor_id,
) -> dict:
    """Close the loop from this delivery back to the order that asked for it.

    `ordered_at` has always been stamped on submit; `received_at` and
    `quantity_received` never were, so lead time and fill rate — the two facts
    every supplier engine stands on — have never once been recorded. This is
    where they start.

    The order is scoped to the receiving pharmacy: reconciling a delivery
    against someone else's order would corrupt their supplier history with
    stock they never took in.
    """
    po = (await db.execute(select(PurchaseOrder).where(
        PurchaseOrder.id == po_id,
        PurchaseOrder.pharmacy_id == pharmacy_id))).scalar_one_or_none()
    if po is None:
        raise HTTPException(404,
            f"purchase order {po_id} does not belong to this pharmacy — "
            f"receive without a purchase order rather than against that one")

    rows = (await db.execute(select(PurchaseOrderLine).where(
        PurchaseOrderLine.order_id == po.id))).scalars().all()
    by_id = {str(r.id): r for r in rows}
    lines = [{"id": str(r.id), "ndc11": r.ndc11,
              "quantity_ordered": r.quantity_ordered,
              "quantity_received": r.quantity_received,
              "status": r.status, "created_at": r.created_at} for r in rows]

    target = RCV.match_line(lines, ndc11)
    if target is None:
        # Not a reason to refuse the goods — they are physically here. But it is
        # not this supplier's fill rate either, and quietly attributing it would
        # flatter whoever actually did deliver short.
        return {"purchase_order_id": str(po.id), "matched": False,
                "order_status": po.status,
                "explanation": (
                    f"no open line on this order is expecting {ndc11}; the "
                    f"delivery is recorded, but it closes nothing and is not "
                    f"counted towards the supplier's fill rate")}

    match = RCV.apply_receipt(target, units)
    row = by_id[match.line_id]
    row.quantity_received = float(match.total_received)
    row.status = match.status
    row.updated_by = actor_id

    # Re-derive the order from the refreshed lines rather than patching the
    # status by hand — the same reason the stock aggregate is re-summed.
    for line in lines:
        if line["id"] == match.line_id:
            line["quantity_received"] = match.total_received
            line["status"] = match.status
    po.status = RCV.order_status(lines)
    stamped = RCV.completion(lines, now=now)
    if stamped is not None and po.received_at is None:
        po.received_at = stamped
    po.updated_by = actor_id

    out = {"purchase_order_id": str(po.id), "matched": True,
           "order_status": po.status,
           "received_at": iso_utc(po.received_at) if po.received_at else None}
    out.update(match.as_dict())
    return out


@router.get("/admin/open-orders")
async def open_orders(
    ndc11: Optional[str] = Query(None, description="only orders still expecting this NDC"),
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    """Orders that have been placed and are still owed stock.

    The receiving bench needs this to attribute a delivery to the order that
    asked for it. Without the attribution nothing measures lead time or fill
    rate, and every supplier looks equally good — which is the same as having no
    supplier information at all.

    Outstanding quantity is the ordered figure minus what has arrived, floored
    at zero: an over-delivery on one line does not mean the supplier owes a
    negative amount.
    """
    rows = (await db.execute(text("""
        SELECT o.id, o.po_number, o.wholesaler, o.status, o.ordered_at,
               o.expected_delivery,
               l.id AS line_id, l.ndc11, l.quantity_ordered, l.quantity_received,
               l.status AS line_status, d.generic_name, d.brand_name
        FROM purchase_orders o
        JOIN purchase_order_lines l ON l.order_id = o.id AND l.is_deleted = false
        LEFT JOIN drug_products d ON d.ndc11 = l.ndc11
        WHERE o.pharmacy_id = :pid AND o.is_deleted = false
          AND o.status IN ('submitted', 'acknowledged', 'partial')
          AND l.status NOT IN ('complete', 'cancelled')
          AND (CAST(:ndc AS text) IS NULL OR l.ndc11 = CAST(:ndc AS text))
        ORDER BY o.ordered_at NULLS LAST, l.created_at"""),
        {"pid": staff.pharmacy_id, "ndc": ndc11})).mappings().all()

    orders: dict[str, dict] = {}
    for r in rows:
        oid = str(r["id"])
        o = orders.setdefault(oid, {
            "purchase_order_id": oid, "po_number": r["po_number"],
            "wholesaler": r["wholesaler"], "status": r["status"],
            "ordered_at": iso_utc(r["ordered_at"]) if r["ordered_at"] else None,
            "expected_delivery": r["expected_delivery"].isoformat()
                                 if r["expected_delivery"] else None,
            "days_outstanding": (
                (datetime.now(timezone.utc) - r["ordered_at"]).days
                if r["ordered_at"] else None),
            "lines": []})
        outstanding = max(0.0, float(r["quantity_ordered"] or 0)
                          - float(r["quantity_received"] or 0))
        o["lines"].append({
            "line_id": str(r["line_id"]), "ndc11": r["ndc11"],
            "name": r["generic_name"] or r["brand_name"] or r["ndc11"],
            "quantity_ordered": float(r["quantity_ordered"] or 0),
            "quantity_received": float(r["quantity_received"] or 0),
            "outstanding": outstanding, "status": r["line_status"]})
    return {"orders": list(orders.values()), "count": len(orders)}


class CloseOrder(BaseModel):
    reason: str = Field(..., min_length=3, max_length=240)


@router.post("/admin/orders/{po_id}/close-short")
async def close_order_short(
    po_id: UUID,
    body: CloseOrder,
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    """Accept that the rest of this order is not coming.

    Someone has to be able to say it. Until they can, a short-shipped order
    stays open for ever — and the consequence is not untidy bookkeeping but a
    blind spot: the order never completes, so `received_at` is never stamped, so
    the supplier never acquires a lead time, and a supplier that *always*
    short-ships becomes indistinguishable from one that has never delivered at
    all. E12 goes blind to exactly the behaviour it exists to catch.

    The outstanding units close as `backordered`, not `cancelled`. Closing the
    order must not erase the failure that made closing it necessary.
    """
    po = (await db.execute(select(PurchaseOrder).where(
        PurchaseOrder.id == po_id,
        PurchaseOrder.pharmacy_id == staff.pharmacy_id))).scalar_one_or_none()
    if po is None:
        raise HTTPException(404, "purchase order not found at this pharmacy")

    rows = (await db.execute(select(PurchaseOrderLine).where(
        PurchaseOrderLine.order_id == po.id))).scalars().all()
    lines = [{"id": str(r.id), "ndc11": r.ndc11,
              "quantity_ordered": r.quantity_ordered,
              "quantity_received": r.quantity_received,
              "status": r.status, "created_at": r.created_at} for r in rows]

    closed = RCV.close_short(lines)
    if not closed:
        raise HTTPException(422,
            "nothing on this order is outstanding — there is nothing to close")

    by_id = {str(r.id): r for r in rows}
    for m in closed:
        row = by_id[m.line_id]
        row.status = m.status
        row.updated_by = staff.id
        for line in lines:
            if line["id"] == m.line_id:
                line["status"] = m.status

    now = datetime.now(timezone.utc)
    po.status = RCV.order_status(lines)
    stamped = RCV.completion(lines, now=now)
    if stamped is not None and po.received_at is None:
        po.received_at = stamped
    po.notes = ((po.notes or "") + f"\nclosed short: {body.reason}").strip()
    po.updated_by = staff.id
    await db.commit()

    log.info("PO %s closed short by %s: %d line(s)", po.po_number,
             str(staff.id)[:8], len(closed))
    return {"purchase_order_id": str(po.id), "order_status": po.status,
            "received_at": iso_utc(po.received_at) if po.received_at else None,
            "closed": [m.as_dict() for m in closed]}


class ReceiveLot(BaseModel):
    ndc11: str
    lot_number: str = Field(..., min_length=1)
    expiry_date: date
    quantity: float = Field(..., gt=0)
    # What was counted: individual units, or packs to be multiplied by the
    # product's pack size. Defaults to "each" because every existing caller
    # means that; reinterpreting their receipts as packs would rewrite the
    # shelf by a factor of the pack size.
    uom: Optional[str] = Field(default="each", pattern="^(each|pack)$")
    unit_cost: Optional[float] = None
    # The other half of the invoice. Almost every فاکتور names the consumer price
    # beside the purchase price, so it is transcribed rather than computed, and
    # the margin is derived from the pair. `margin_pct` is the fallback for the
    # documents that stay silent — supply one or the other, not a house default:
    # the right markup depends on whether the carton holds a generic tablet or a
    # cosmetic, and no single percentage is correct for both.
    sell_price: Optional[float] = None
    margin_pct: Optional[float] = None
    irc: Optional[str] = None
    storage_location: Optional[str] = None
    serial_number: Optional[str] = None
    purchase_order_id: Optional[UUID] = None
    reason: str = "goods receipt"


@router.post("/admin/receive", status_code=201)
async def receive_stock(
    body: ReceiveLot,
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    """Take delivery of stock.

    Creates the lot if the lot number is new, tops it up if it already exists,
    and in both cases writes a RECEIPT movement through the ledger — so the
    arriving units are as traceable as the leaving ones. An expiry date in the
    past is refused: receiving expired stock into sellable inventory is how
    expired stock ends up dispensed.
    """
    if body.expiry_date < date.today():
        raise HTTPException(422,
            f"expiry {body.expiry_date.isoformat()} is in the past — quarantine "
            f"the delivery instead of receiving it as sellable stock")

    drug = (await db.execute(select(DrugProduct).where(
        DrugProduct.ndc11 == body.ndc11))).scalar_one_or_none()
    if drug is None:
        raise HTTPException(404, f"NDC {body.ndc11} is not in the product catalog")

    if body.irc:
        known = (await db.execute(text("SELECT 1 FROM drug_catalog WHERE irc = :i"),
                                  {"i": body.irc})).scalar()
        if not known:
            raise HTTPException(422, f"IRC {body.irc} is not in the formulary")

    # Convert what was counted into units on the shelf, and record which it was.
    # A bare number could not distinguish 3 boxes from 3 tablets, which is the
    # 30x error `check_unit_conversion` can only report after the fact.
    try:
        recv = A.receipt_units(body.quantity, uom=body.uom,
                              units_per_pack=drug.package_quantity)
    except A.ReceiptUnitError as exc:
        raise HTTPException(422, str(exc))
    units = float(recv["units"])

    # E2: sanity-check the delivery against the national formulary and this
    # item's own history, at the door. The receipt is never refused on this —
    # goods physically arrived, and refusing to record them makes the books
    # describe a shelf that does not exist. It is recorded with the findings
    # attached so the person at the bench sees them while the carton is still
    # in front of them, rather than in a report a fortnight later.
    irc = body.irc or (await db.execute(text(
        "SELECT irc FROM stock_levels WHERE pharmacy_id = :pid AND ndc11 = :ndc"),
        {"pid": staff.pharmacy_id, "ndc": body.ndc11})).scalar()
    ref = (await db.execute(text(
        "SELECT announced_price, package_count FROM drug_catalog WHERE irc = :irc"),
        {"irc": irc})).mappings().first() if irc else None

    hist = (await db.execute(text("""
        SELECT unit_cost, quantity_received, lot_number, expiry_date
        FROM inventory_lots
        WHERE pharmacy_id = :pid AND ndc11 = :ndc AND is_deleted = false"""),
        {"pid": staff.pharmacy_id, "ndc": body.ndc11})).mappings().all()

    today_r = CLK.pharmacy_today((await db.execute(text(
        "SELECT timezone FROM pharmacies WHERE id = :p"),
        {"p": staff.pharmacy_id})).scalar())
    live_expiries = [h["expiry_date"] for h in hist if h["expiry_date"]]
    verdict = RA.check_receipt(
        ndc11=body.ndc11, lot_number=body.lot_number,
        unit_cost=body.unit_cost, quantity=units,
        expiry_date=body.expiry_date, as_of=today_r,
        announced_price=(ref or {}).get("announced_price"),
        package_count=(ref or {}).get("package_count"),
        cost_history=[h["unit_cost"] for h in hist],
        quantity_history=[h["quantity_received"] for h in hist],
        prior_lot_numbers={h["lot_number"] for h in hist},
        shortest_existing_expiry=min(live_expiries) if live_expiries else None)
    if not verdict.clean:
        log.info("receipt %s/%s flagged: %s", body.ndc11, body.lot_number,
                 [f.check for f in verdict.findings])

    # The second half of the invoice: what this lot is to be sold for. Captured
    # here rather than derived later, because the delivery document is the only
    # moment both figures are in front of the same person. Where it is silent a
    # declared margin computes the price instead, and `basis` keeps the two
    # apart — an observed price and a computed one must not be interchangeable
    # once they are in the money path.
    sell, margin, basis = SP.price_and_margin(
        body.unit_cost, body.sell_price, body.margin_pct)
    # Selling below what the lot cost is a real thing a pharmacy sometimes does
    # and more often a mistyped figure or a pack/unit mix-up. Reported, never
    # refused — on the same principle as E2, the goods arrived and the books
    # must say so.
    sells_at_a_loss = bool(margin is not None and margin < 0)

    lot = (await db.execute(select(InventoryLot).where(
        InventoryLot.pharmacy_id == staff.pharmacy_id,
        InventoryLot.ndc11 == body.ndc11,
        InventoryLot.lot_number == body.lot_number,
        InventoryLot.is_deleted == False,  # noqa: E712
    ))).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    created = lot is None
    if created:
        lot = InventoryLot(
            pharmacy_id=staff.pharmacy_id, drug_product_id=drug.id, ndc11=body.ndc11,
            irc=body.irc, lot_number=body.lot_number, expiry_date=body.expiry_date,
            quantity_received=0, quantity_on_hand=0, quantity_reserved=0,
            unit_cost=body.unit_cost, sell_price=sell, margin_pct=margin,
            sell_price_basis=basis, storage_location=body.storage_location,
            received_uom=recv["uom"], received_packs=recv["packs"],
            units_per_pack=recv["units_per_pack"],
            serial_number=body.serial_number, received_at=now,
            purchase_order_id=body.purchase_order_id,
            created_by=staff.id, updated_by=staff.id)
        db.add(lot)
        await db.flush()
    elif lot.expiry_date != body.expiry_date:
        raise HTTPException(422,
            f"lot {body.lot_number} already exists with expiry "
            f"{lot.expiry_date.isoformat()}; two different expiries cannot share "
            f"a lot number — check the carton")

    view = L.Lot(lot_id=str(lot.id), lot_number=lot.lot_number,
                 expiry_date=lot.expiry_date,
                 quantity_on_hand=L.q(lot.quantity_on_hand),
                 is_recalled=bool(lot.is_recalled))
    try:
        plan = L.plan_receipt(view, units, movement_type="RECEIPT",
                              reason=body.reason, is_controlled=bool(drug.is_controlled))
    except L.LedgerError as e:
        raise HTTPException(422, str(e))

    lot.quantity_on_hand = plan.quantity_after
    lot.quantity_received = L.q(float(lot.quantity_received or 0) + units)
    lot.updated_by = staff.id
    if body.unit_cost is not None:
        lot.unit_cost = body.unit_cost
    # A top-up under the same lot number: the newer document wins, because it is
    # what the pharmacy has just agreed to pay and charge. Silence leaves the
    # existing pair alone rather than blanking a price that is still good.
    if sell is not None:
        lot.sell_price = sell
        lot.margin_pct = margin
        lot.sell_price_basis = basis

    stock = (await db.execute(select(StockLevel).where(
        StockLevel.pharmacy_id == staff.pharmacy_id,
        StockLevel.ndc11 == body.ndc11))).scalar_one_or_none()
    if stock is None:
        stock = StockLevel(pharmacy_id=staff.pharmacy_id, ndc11=body.ndc11,
                           irc=body.irc, drug_product_id=drug.id,
                           quantity_on_hand=0, quantity_reserved=0, quantity_on_order=0)
        db.add(stock)
        await db.flush()
    # The per-lot cap in the ledger does not bound the aggregate: enough lots
    # of legitimate size still sum past what the column holds. Checked here,
    # before the write, so the caller gets an explanation rather than a numeric
    # overflow raised from inside the flush.
    new_aggregate = L.q(float(stock.quantity_on_hand or 0) + units)
    if new_aggregate > L.MAX_QUANTITY:
        raise HTTPException(422,
            f"receiving {units} would take total stock of {body.ndc11} to "
            f"{new_aggregate}, beyond the {L.MAX_QUANTITY} a quantity column "
            f"can hold — check the figure before receiving it")
    stock.quantity_on_hand = new_aggregate
    stock.last_received_at = now
    if body.irc and not stock.irc:
        stock.irc = body.irc

    po_result = None
    if body.purchase_order_id is not None:
        po_result = await _reconcile_purchase_order(
            db, pharmacy_id=staff.pharmacy_id, po_id=body.purchase_order_id,
            ndc11=body.ndc11, units=units, now=now, actor_id=staff.id)

    movement = await append_movement(
        db, pharmacy_id=staff.pharmacy_id, ndc11=body.ndc11,
        irc=body.irc or lot.irc, lot_id=lot.id, plan=plan, actor_id=staff.id)
    await db.commit()
    return {"lot_id": str(lot.id), "lot_created": created,
            "quantity_received": units,
            "counted": recv["explanation"],
            "checks": verdict.as_dict(),
            "pricing": {
                "sell_price": float(sell) if sell is not None else None,
                "margin_pct": float(margin) if margin is not None else None,
                "basis": basis,
                "sells_at_a_loss": sells_at_a_loss,
                "explanation": (
                    "قیمت مصرف‌کننده از فاکتور ثبت شد؛ درصد سود از همان دو عدد محاسبه شد."
                    if basis == "invoice" else
                    "فاکتور قیمت مصرف‌کننده نداشت؛ قیمت از درصد سود اعلام‌شده محاسبه شد."
                    if basis == "margin" else
                    "قیمت فروش ثبت نشد — این بچ قیمت قفسه تعیین نمی‌کند.")},
            "purchase_order": po_result,
            "lot_on_hand": float(plan.quantity_after),
            "movement_id": str(movement.id), "event_hash": movement.event_hash}


# ── Damage ────────────────────────────────────────────────────────────────

class RecordDamage(BaseModel):
    lot_id: UUID
    quantity: float = Field(..., gt=0)
    reason: str = Field(..., min_length=3, max_length=240)
    # DAMAGE | TRANSFER_OUT | RETURN_TO_SUPPLIER — all three take stock out of
    # use without removing it from the books, so all three apply immediately.
    movement_type: str = "DAMAGE"
    from_bucket: Optional[str] = Field(
        None, description="move between buckets, e.g. damaged → returned")


@router.post("/admin/damage", status_code=201)
async def record_damage(
    body: RecordDamage,
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    """Move units out of sellable stock and into the damaged bucket.

    Applies immediately and needs no approval, on the same principle as
    quarantine: taking stock *out of use* must never wait for a signature, and
    a technician holding a crushed carton should not have to keep it on the
    shelf until someone countersigns.

    The units are not gone. They stay on the lot, countable and valuable,
    awaiting either a supplier claim or an approved write-off — which is the
    step that does need two people.
    """
    lot = (await db.execute(select(InventoryLot).where(
        InventoryLot.id == body.lot_id,
        InventoryLot.pharmacy_id == staff.pharmacy_id,
        InventoryLot.is_deleted == False,  # noqa: E712
    ))).scalar_one_or_none()
    if lot is None:
        raise HTTPException(404, "lot not found")

    view = L.Lot(lot_id=str(lot.id), lot_number=lot.lot_number,
                 expiry_date=lot.expiry_date,
                 quantity_on_hand=L.q(lot.quantity_on_hand),
                 quantity_damaged=L.q(lot.quantity_damaged or 0),
                 quantity_returned=L.q(lot.quantity_returned or 0),
                 quantity_in_transit=L.q(lot.quantity_in_transit or 0))
    if body.from_bucket and body.from_bucket not in L.BUCKETS:
        raise HTTPException(422, f"unknown bucket {body.from_bucket!r}")
    try:
        plan = L.plan_bucket_transfer(view, body.quantity,
                                      movement_type=body.movement_type,
                                      reason=body.reason,
                                      from_bucket=body.from_bucket)
    except L.LedgerError as e:
        raise HTTPException(422, str(e))

    lot.quantity_on_hand = plan.quantity_after
    setattr(lot, L.BUCKETS[plan.to_bucket], plan.bucket_after)
    if plan.from_bucket:
        # A bucket-to-bucket move debits the source as well.
        source_attr = L.BUCKETS[plan.from_bucket]
        setattr(lot, source_attr,
                L.q(getattr(lot, source_attr) or 0) - L.q(abs(plan.quantity_delta)))
    lot.updated_by = staff.id

    # The aggregate tracks sellable stock, so it moves only when on-hand does.
    if plan.quantity_after != plan.quantity_before:
        await db.execute(text("""
            UPDATE stock_levels SET quantity_on_hand = quantity_on_hand + :d,
                                    updated_at = NOW()
            WHERE pharmacy_id = :pid AND ndc11 = :ndc"""),
            {"d": float(plan.quantity_after - plan.quantity_before),
             "pid": staff.pharmacy_id, "ndc": lot.ndc11})

    movement = await append_movement(
        db, pharmacy_id=staff.pharmacy_id, ndc11=lot.ndc11, irc=lot.irc,
        lot_id=lot.id, plan=plan, actor_id=staff.id)

    # Units just left sellable stock, and some of them may have been promised to
    # a patient. The movement is a fact and is not refused; the promises it can
    # no longer back are released here, newest first, so `reserved` cannot end
    # up above `on_hand` — a reservation against stock that does not exist.
    freed = await RS.shrink_to_capacity(db, lot.id, pharmacy_id=staff.pharmacy_id)
    await db.commit()
    return {"lot_id": str(lot.id), "moved": float(abs(plan.quantity_delta)),
            "movement_type": plan.movement_type,
            "on_hand": float(plan.quantity_after),
            "bucket": plan.to_bucket, "bucket_quantity": float(plan.bucket_after),
            "from_bucket": plan.from_bucket,
            "damaged": float(plan.bucket_after) if plan.to_bucket == "damaged" else None,
            "movement_id": str(movement.id), "event_hash": movement.event_hash,
            "reservations_released": freed.get("released", 0),
            "affected_prescriptions": freed.get("prescriptions", []),
            "note": "units are held, not written off — they remain the "
                    "pharmacy's asset until an approved write-off or release"}


# ── Write-off request ─────────────────────────────────────────────────────

class WriteOffRequest(BaseModel):
    lot_id: UUID
    movement_type: str          # WASTE | EXPIRY_REMOVAL | RECALL_REMOVAL | SUPPLIER_CREDIT
    quantity: float = Field(..., gt=0)
    reason: str = Field(..., min_length=3)
    from_bucket: Optional[str] = Field(
        None, description="damaged | returned | in_transit — draw from a holding "
                          "bucket instead of sellable stock")


class ReleaseRequest(BaseModel):
    lot_id: UUID
    quantity: float = Field(..., gt=0)
    from_bucket: str = Field(..., description="damaged | returned | in_transit")
    reason: str = Field(..., min_length=3)


@router.post("/admin/release", status_code=201)
async def request_release(
    body: ReleaseRequest,
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    """Ask for held stock to be put back on sale.

    Units could enter a holding bucket and never leave it except by being
    destroyed. `plan_bucket_release` existed, and `_apply_movement` knew how to
    apply one, but nothing ever created the approval that would reach it — the
    releasing branch was unreachable, so a carton marked damaged by mistake
    could only be written off. Found by the simulator, which could move stock
    into a bucket and had no way to move it back.

    Approved rather than immediate, and deliberately the opposite way round
    from `record_damage`: taking stock out of use is a safety action and applies
    at once, while putting blocked stock back on sale is the direction that can
    hurt a patient, so it needs a second person.
    """
    if body.from_bucket not in L.BUCKETS:
        raise HTTPException(422, f"unknown bucket {body.from_bucket!r}; "
                                 f"allowed: {sorted(L.BUCKETS)}")

    lot = (await db.execute(select(InventoryLot).where(
        InventoryLot.id == body.lot_id,
        InventoryLot.pharmacy_id == staff.pharmacy_id,
        InventoryLot.is_deleted == False,  # noqa: E712
    ))).scalar_one_or_none()
    if lot is None:
        raise HTTPException(404, "lot not found")

    held = L.q(getattr(lot, L.BUCKETS[body.from_bucket], 0) or 0)
    if L.q(body.quantity) > held:
        raise HTTPException(422,
            f"cannot release {body.quantity} from {body.from_bucket} — only "
            f"{float(held)} is held there")
    if lot.is_recalled:
        raise HTTPException(422,
            "a recalled lot is never released back to sale, whatever bucket it "
            "is sitting in")
    if lot.expiry_date and lot.expiry_date < date.today():
        raise HTTPException(422,
            f"lot {lot.lot_number} expired on {lot.expiry_date.isoformat()}; "
            f"releasing it would put expired stock back on the shelf")

    drug = (await db.execute(select(DrugProduct).where(
        DrugProduct.id == lot.drug_product_id))).scalar_one_or_none()

    appr = InventoryApproval(
        pharmacy_id=staff.pharmacy_id, irc=lot.irc, ndc11=lot.ndc11,
        inventory_lot_id=lot.id, movement_type="TRANSFER_IN",
        quantity=body.quantity, is_controlled=bool(drug and drug.is_controlled),
        status="pending", reason=body.reason[:240],
        payload={"from_bucket": body.from_bucket, "release": True},
        due_at=SLA.deadline("TRANSFER_IN", requested_at=datetime.now(timezone.utc),
                            is_controlled=bool(drug and drug.is_controlled)).due_at,
        requested_by_id=staff.id, created_by=staff.id, updated_by=staff.id)
    db.add(appr)
    await db.commit()
    return {"approval_id": str(appr.id), "status": "pending", "stock_changed": False,
            "from_bucket": body.from_bucket, "quantity": body.quantity,
            "requires_witness": appr.is_controlled,
            "message": "درخواست بازگرداندن به موجودی قابل فروش ثبت شد؛ "
                       "تا تأیید نفر دوم تغییری اعمال نمی‌شود."}


@router.post("/admin/write-off", status_code=201)
async def request_write_off(
    body: WriteOffRequest,
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    """Ask for stock to be written off. Never applies it.

    The request lands in the same approval queue as a count variance, and a
    different person applies it. That is the whole point: the person who
    discovers the loss is not the person who signs it off.
    """
    if body.movement_type not in L.BUCKET_WRITEOFF_TYPES:
        raise HTTPException(422,
            f"{body.movement_type} is not a write-off; allowed: "
            f"{sorted(L.BUCKET_WRITEOFF_TYPES - {'COUNT_LOSS'})}")

    lot = (await db.execute(select(InventoryLot).where(
        InventoryLot.id == body.lot_id,
        InventoryLot.pharmacy_id == staff.pharmacy_id,
        InventoryLot.is_deleted == False,  # noqa: E712
    ))).scalar_one_or_none()
    if lot is None:
        raise HTTPException(404, "lot not found")
    if body.from_bucket:
        if body.from_bucket not in L.BUCKETS:
            raise HTTPException(422, f"unknown bucket {body.from_bucket!r}")
        held = L.q(getattr(lot, L.BUCKETS[body.from_bucket], 0) or 0)
        if L.q(body.quantity) > held:
            raise HTTPException(422,
                f"cannot write off {body.quantity} from {body.from_bucket} — "
                f"only {float(held)} held there")
    elif L.q(body.quantity) > L.q(lot.quantity_on_hand):
        raise HTTPException(422,
            f"cannot write off {body.quantity} — only {float(lot.quantity_on_hand)} on hand")

    drug = (await db.execute(select(DrugProduct).where(
        DrugProduct.id == lot.drug_product_id))).scalar_one_or_none()

    appr = InventoryApproval(
        pharmacy_id=staff.pharmacy_id, irc=lot.irc, ndc11=lot.ndc11,
        inventory_lot_id=lot.id, movement_type=body.movement_type,
        quantity=body.quantity, is_controlled=bool(drug and drug.is_controlled),
        status="pending", reason=body.reason[:240],
        payload={"from_bucket": body.from_bucket} if body.from_bucket else None,
        requested_by_id=staff.id, created_by=staff.id, updated_by=staff.id)
    db.add(appr)
    await db.commit()
    return {"approval_id": str(appr.id), "status": "pending", "stock_changed": False,
            "requires_witness": appr.is_controlled,
            "message": "درخواست ثبت شد؛ تا تأیید نفر دوم موجودی تغییری نمی‌کند."}
