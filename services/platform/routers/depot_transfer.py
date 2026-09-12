"""Depot → shelf dual-verification replenishment endpoints.

Two reconciled checkpoints (depot barcode+count, shelf barcode+AI), with all
safety enforcement applied server-side in `_enforce_finalize_guards` — including
the regulated pharmacist-attestation gate for high-risk/controlled/LASA drugs,
which is rejected at the API layer (not just the UI). The AI shelf-verify is the
§1.2-enveloped vision stub (graceful degradation). The ShelfTransferEvent ledger
is itself the audit trail (who/when/where/qty + verification blobs).
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from services.core.inventory import replenishment as R
from services.ai.shelf_vision.verifier import shelf_verify
from services.platform.auth import get_current_staff, require_permission  # noqa: F401
from services.platform.database import get_db
from shared.models.auth import Staff
from shared.models.depot import (
    PharmacyShelf, ReplenishmentSession, ShelfPlacement, ShelfTransferEvent,
    SurveillanceEvent, ShiftHandoverReport,
)
from shared.models.inventory import DrugProduct, InventoryLot
from services.core.inventory import ledger as L
from services.platform.routers.inventory_integrity import append_movement

router = APIRouter()


# ── Request models ────────────────────────────────────────────────────────────

class BarcodeScan(BaseModel):
    ndc11: str
    lot_number: str
    expiry_date: date
    serial: str | None = None


class SessionCreateRequest(BaseModel):
    shelf_id: UUID
    ndc11s: list[str] = []  # NDCs assigned to the shelf to replenish


class DepotCollectRequest(BaseModel):
    scans: list[BarcodeScan] = []
    counted_units: int
    inventory_lot_id: UUID
    staged: BarcodeScan


class ShelfVerifyRequest(BaseModel):
    image_base64: str | None = None
    staged_ndc: str
    staged_lot: str
    staged_quantity: int
    expected_drug_name: str
    expected_drug_form: str


class ShelfPlaceRequest(BaseModel):
    session_id: UUID
    shelf_id: UUID
    inventory_lot_id: UUID
    ndc11: str
    quantity: int
    barcode_scans: list[BarcodeScan] = []
    ai_verification: dict[str, Any] = {}
    temperature_logged_c: float | None = None
    near_expiry_placement_confirmed: bool = False
    override_reason: str | None = None
    override_by: UUID | None = None
    pharmacist_attestation_by: UUID | None = None
    pharmacist_attestation_pin: str | None = None


class SurveillanceEventRequest(BaseModel):
    camera_id: str | None = None
    session_id: UUID | None = None
    event_type: str
    severity: str = "low"
    clip_ref: str | None = None
    ai_result: dict[str, Any] | None = None


# ── Pure finalize guard (server-side enforcement; unit-tested) ─────────────────

def _enforce_finalize_guards(body: "ShelfPlaceRequest", *, drug: dict, shelf: dict) -> dict:
    """Raise HTTPException(422) on any hard block; return a dict of non-blocking
    warnings/flags to persist. Pure (no I/O) so it is unit-testable."""
    flags: dict[str, Any] = {}

    # AI verdict: block requires an explicit override + supervisor
    ai_verdict = (body.ai_verification or {}).get("count_verdict")
    if ai_verdict == "block" and not (body.override_reason and body.override_by):
        raise HTTPException(422, "AI count verdict 'block' requires supervisor override + reason")

    # Cold chain: blocks on missing/out-of-range temperature for cold-chain drugs
    cc = R.cold_chain_check(drug.get("storage_condition"), body.temperature_logged_c)
    if cc["verdict"] == "block":
        raise HTTPException(422, f"Cold chain: {cc['reason']}")

    # Capacity: non-blocking warn (recorded)
    # `current_units` is Numeric(10,3) since migration 0054, so it can carry a
    # fraction left by a fractional dispense. int() here rounded it down and made
    # the capacity warning fire slightly late; capacity itself stays whole.
    cap = R.capacity_check(staged=body.quantity,
                           current=Decimal(str(shelf.get("current_units", 0) or 0)),
                           capacity=int(shelf.get("capacity_units", 0)))
    if cap["verdict"] == "warn":
        flags["capacity_warning"] = cap["reason"]

    # Pharmacist attestation for high-risk/controlled/LASA — enforced at the API layer
    if R.requires_pharmacist_attestation(drug):
        if not (body.pharmacist_attestation_by and body.pharmacist_attestation_pin):
            raise HTTPException(422, "Pharmacist attestation (PIN) required for high-risk/controlled/LASA drug")

    return flags


def _jsonable(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/replenishment/session")
async def create_session(
    body: SessionCreateRequest,
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    # Build a FEFO-ordered pick list from depot lots for the requested NDCs.
    pick_list: list[dict] = []
    for ndc in body.ndc11s:
        rows = (await db.execute(
            select(InventoryLot).where(
                InventoryLot.pharmacy_id == staff.pharmacy_id,
                InventoryLot.ndc11 == ndc,
                InventoryLot.is_deleted == False,  # noqa: E712
            )
        )).scalars().all()
        lots = [{"id": str(l.id), "ndc11": l.ndc11, "lot_number": l.lot_number,
                 "expiry_date": l.expiry_date, "quantity_on_hand": float(l.quantity_on_hand)} for l in rows]
        for l in R.fefo_order(lots):
            l["expiry_date"] = l["expiry_date"].isoformat()
            pick_list.append(l)

    sess = ReplenishmentSession(
        pharmacy_id=staff.pharmacy_id, status="PICK_LIST", pick_list=pick_list,
        started_by=staff.id, started_at=datetime.now(timezone.utc),
        created_by=staff.id, updated_by=staff.id,
    )
    db.add(sess)
    await db.flush()
    return {"id": str(sess.id), "status": sess.status, "pick_list": pick_list}


@router.post("/replenishment/{session_id}/depot-collect")
async def depot_collect(
    session_id: UUID,
    body: DepotCollectRequest,
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    sess = (await db.execute(
        select(ReplenishmentSession).where(
            ReplenishmentSession.id == session_id,
            ReplenishmentSession.pharmacy_id == staff.pharmacy_id,
        )
    )).scalar_one_or_none()
    if not sess:
        raise HTTPException(404, "Session not found")

    today = datetime.now(timezone.utc).date()
    seen: set[str] = set()
    results = []
    for scan in body.scans:
        v = R.barcode_gate(body.staged.model_dump(), scan.model_dump(), today=today, seen_serials=seen)
        if scan.serial:
            seen.add(scan.serial)
        results.append({"serial": scan.serial, **v})
    # Units picked off the depot shelf are no longer available to dispense —
    # they are on a trolley. Until now this endpoint recorded the checkpoint and
    # moved no stock at all, so a carried carton stayed sellable in two places
    # at once. TRANSFER_OUT holds them in `in_transit` for the duration.
    moved = await _collect_to_transit(db, sess, body, staff)

    sess.depot_checkpoint = _jsonable({
        "counted_units": body.counted_units,
        "inventory_lot_id": body.inventory_lot_id,
        "scan_results": results,
        "at": datetime.now(timezone.utc),
        "by": staff.id,
        "stock_moved_to_transit": moved,
    })
    sess.status = "IN_TRANSIT"
    sess.updated_by = staff.id
    await db.flush()
    return {"id": str(sess.id), "status": sess.status,
            "depot_checkpoint": sess.depot_checkpoint,
            "moved_to_transit": moved}


def _lot_view(lot: InventoryLot) -> "L.Lot":
    return L.Lot(lot_id=str(lot.id), lot_number=lot.lot_number,
                 expiry_date=lot.expiry_date,
                 quantity_on_hand=L.q(lot.quantity_on_hand),
                 quantity_reserved=L.q(lot.quantity_reserved or 0),
                 quantity_damaged=L.q(lot.quantity_damaged or 0),
                 quantity_returned=L.q(lot.quantity_returned or 0),
                 quantity_in_transit=L.q(lot.quantity_in_transit or 0),
                 is_quarantined=bool(lot.is_quarantined),
                 is_recalled=bool(lot.is_recalled),
                 cold_chain_breach=bool(lot.cold_chain_breach),
                 storage_location=lot.storage_location)


async def _collect_to_transit(db, sess, body, staff) -> float:
    """Move the counted units from the depot lot into `in_transit`.

    Idempotent on the session: a re-posted collection must not carry the stock
    twice, and the checkpoint records whether the move already happened.
    """
    if (sess.depot_checkpoint or {}).get("stock_moved_to_transit"):
        return 0.0
    if not body.inventory_lot_id or not body.counted_units:
        return 0.0

    lot = (await db.execute(select(InventoryLot).where(
        InventoryLot.id == body.inventory_lot_id,
        InventoryLot.pharmacy_id == staff.pharmacy_id,
        InventoryLot.is_deleted == False,  # noqa: E712
    ))).scalar_one_or_none()
    if lot is None:
        raise HTTPException(404, "inventory lot not found")

    try:
        plan = L.plan_bucket_transfer(
            _lot_view(lot), body.counted_units, movement_type="TRANSFER_OUT",
            reason=f"depot collection for session {sess.id}")
    except L.LedgerError as e:
        # The depot cannot hand over stock it does not have. Refusing here is
        # right: the alternative is a session that claims to carry units the
        # shelf will never receive.
        raise HTTPException(422, str(e))

    lot.quantity_on_hand = plan.quantity_after
    lot.quantity_in_transit = plan.bucket_after
    lot.updated_by = staff.id
    await db.execute(text("""
        UPDATE stock_levels SET quantity_on_hand = quantity_on_hand + :d,
                                updated_at = NOW()
        WHERE pharmacy_id = :pid AND ndc11 = :ndc"""),
        {"d": float(plan.quantity_delta), "pid": staff.pharmacy_id,
         "ndc": lot.ndc11})
    await append_movement(db, pharmacy_id=staff.pharmacy_id, ndc11=lot.ndc11,
                          irc=lot.irc, lot_id=lot.id, plan=plan,
                          actor_id=staff.id)
    return float(abs(plan.quantity_delta))


@router.post("/ai/shelf-verify")
async def ai_shelf_verify(
    body: ShelfVerifyRequest,
    staff: Staff = Depends(require_permission("inventory:write")),
):
    return shelf_verify(
        image_base64=body.image_base64, staged_ndc=body.staged_ndc, staged_lot=body.staged_lot,
        staged_quantity=body.staged_quantity, expected_drug_name=body.expected_drug_name,
        expected_drug_form=body.expected_drug_form,
    )


@router.post("/replenishment/{session_id}/shelf-place")
async def shelf_place(
    session_id: UUID,
    body: ShelfPlaceRequest,
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    sess = (await db.execute(
        select(ReplenishmentSession).where(
            ReplenishmentSession.id == session_id,
            ReplenishmentSession.pharmacy_id == staff.pharmacy_id,
        )
    )).scalar_one_or_none()
    if not sess:
        raise HTTPException(404, "Session not found")

    lot = (await db.execute(select(InventoryLot).where(
        InventoryLot.id == body.inventory_lot_id, InventoryLot.pharmacy_id == staff.pharmacy_id,
    ))).scalar_one_or_none()
    shelf = (await db.execute(select(PharmacyShelf).where(
        PharmacyShelf.id == body.shelf_id, PharmacyShelf.pharmacy_id == staff.pharmacy_id,
    ))).scalar_one_or_none()
    if not lot or not shelf:
        raise HTTPException(404, "Lot or shelf not found")
    drug = (await db.execute(select(DrugProduct).where(DrugProduct.id == lot.drug_product_id))).scalar_one_or_none()

    drug_dict = {
        "storage_condition": getattr(drug, "storage_condition", None),
        "high_risk_flag": bool(getattr(drug, "high_risk_flag", False)),
        "is_controlled": bool(getattr(drug, "is_controlled", False)),
        "lasa_group": getattr(drug, "lasa_group", None),
    }
    shelf_dict = {"current_units": shelf.current_units, "capacity_units": shelf.capacity_units}

    # Server-side safety gate — raises 422 on any hard block (incl. attestation).
    flags = _enforce_finalize_guards(body, drug=drug_dict, shelf=shelf_dict)

    # Reconcile depot-out vs shelf-in.
    depot_out = int((sess.depot_checkpoint or {}).get("counted_units", body.quantity))
    recon = R.reconcile(depot_out=depot_out, shelf_in=body.quantity)

    now = datetime.now(timezone.utc)

    # The units arrive: `in_transit` → sellable. This is the releasing
    # direction, which the ledger normally gates behind an approval — the
    # barcode scan, the count check and the pharmacist attestation enforced
    # above are that gate, recorded on the movement rather than repeated as a
    # second signature after the fact.
    released = await _release_from_transit(db, lot, body, staff, sess)

    # Commit: placement (+units), shelf cache, ledger.
    placement = ShelfPlacement(
        pharmacy_id=staff.pharmacy_id, inventory_lot_id=lot.id, shelf_id=shelf.id,
        ndc11=body.ndc11, units=body.quantity, placed_by=staff.id, placed_at=now,
        created_by=staff.id, updated_by=staff.id,
    )
    db.add(placement)
    # Decimal, not int. `current_units` is Numeric(10,3) since migration 0054 and
    # a fractional dispense legitimately leaves it fractional; int() here would
    # round the cached total away on the very next placement and re-open the
    # drift 0054 closed. The placement itself is a scanned, whole-unit act, so
    # `body.quantity` stays an integer — it is the running total that is not.
    shelf.current_units = Decimal(str(shelf.current_units)) + Decimal(str(body.quantity))
    shelf.updated_by = staff.id

    event = ShelfTransferEvent(
        pharmacy_id=staff.pharmacy_id, session_id=sess.id, inventory_lot_id=lot.id, shelf_id=shelf.id,
        ndc11=body.ndc11, quantity_delta=body.quantity, performed_by=staff.id,
        barcode_verification_result=_jsonable([s.model_dump() for s in body.barcode_scans]),
        ai_verification_result=_jsonable(body.ai_verification),
        depot_checkpoint_result=_jsonable(sess.depot_checkpoint),
        override_reason=body.override_reason, override_by=body.override_by,
        pharmacist_attestation_by=body.pharmacist_attestation_by,
        pharmacist_attestation_at=now if body.pharmacist_attestation_by else None,
        temperature_logged_c=Decimal(str(body.temperature_logged_c)) if body.temperature_logged_c is not None else None,
        near_expiry_placement_confirmed=body.near_expiry_placement_confirmed,
        created_by=staff.id, updated_by=staff.id,
    )
    db.add(event)

    sess.reconciliation = _jsonable(recon)
    sess.status = "COMPLETE"
    sess.completed_at = now
    sess.updated_by = staff.id
    await db.flush()

    primary_prompt = None
    if drug is not None and getattr(drug, "primary_shelf_id", None) != shelf.id:
        primary_prompt = {"drug": getattr(drug, "generic_name", None), "suggest_shelf_id": str(shelf.id)}

    return {
        "transfer_event_id": str(event.id), "session_status": sess.status,
        "reconciliation": recon, "flags": flags, "shelf_current_units": shelf.current_units,
        "primary_location_prompt": primary_prompt,
        "stock": released,
    }


async def _release_from_transit(db, lot, body, staff, sess) -> dict:
    """Put the placed units back into sellable stock.

    Whatever was collected but not placed stays in `in_transit` rather than
    being quietly forgiven. That residue is the reconciliation discrepancy
    expressed as a real, countable quantity instead of a note in a JSON blob —
    `R.reconcile` already computed the number, but nothing had ever held the
    stock it referred to.
    """
    in_transit = L.q(lot.quantity_in_transit or 0)
    if in_transit <= 0:
        # Nothing was carried under the ledger — a session begun before the
        # depot checkpoint moved stock. Place the units without inventing a
        # release that has no source.
        return {"released": 0.0, "in_transit_remaining": 0.0,
                "note": "no stock was held in transit for this session"}

    place = min(L.q(body.quantity), in_transit)
    try:
        plan = L.plan_bucket_release(
            _lot_view(lot), place, from_bucket="in_transit",
            reason=f"shelf placement for session {sess.id}",
            verified_by="depot dual-verification: barcode + count check"
                        + (" + pharmacist attestation"
                           if body.pharmacist_attestation_by else ""))
    except L.LedgerError as e:
        raise HTTPException(422, str(e))

    lot.quantity_on_hand = plan.quantity_after
    lot.quantity_in_transit = plan.bucket_after
    lot.updated_by = staff.id
    await db.execute(text("""
        UPDATE stock_levels SET quantity_on_hand = quantity_on_hand + :d,
                                updated_at = NOW()
        WHERE pharmacy_id = :pid AND ndc11 = :ndc"""),
        {"d": float(plan.quantity_delta), "pid": staff.pharmacy_id,
         "ndc": lot.ndc11})
    await append_movement(db, pharmacy_id=staff.pharmacy_id, ndc11=lot.ndc11,
                          irc=lot.irc, lot_id=lot.id, plan=plan,
                          actor_id=staff.id)

    stranded = float(plan.bucket_after)
    return {"released": float(place), "in_transit_remaining": stranded,
            "note": (f"{stranded} units collected but not placed remain in "
                     f"transit and are not sellable until reconciled")
                    if stranded else None}


@router.get("/replenishment/{session_id}")
async def get_session(
    session_id: UUID,
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    sess = (await db.execute(select(ReplenishmentSession).where(
        ReplenishmentSession.id == session_id, ReplenishmentSession.pharmacy_id == staff.pharmacy_id,
    ))).scalar_one_or_none()
    if not sess:
        raise HTTPException(404, "Session not found")
    return {"id": str(sess.id), "status": sess.status, "pick_list": sess.pick_list,
            "depot_checkpoint": sess.depot_checkpoint, "reconciliation": sess.reconciliation}


@router.get("/shelves")
async def list_shelves(
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(select(PharmacyShelf).where(
        PharmacyShelf.pharmacy_id == staff.pharmacy_id, PharmacyShelf.is_deleted == False,  # noqa: E712
    ))).scalars().all()
    return [{"id": str(s.id), "label": s.label, "zone": s.zone, "capacity_units": s.capacity_units,
             "current_units": s.current_units, "storage_condition": s.storage_condition} for s in rows]


@router.post("/surveillance/events")
async def create_surveillance_event(
    body: SurveillanceEventRequest,
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    ev = SurveillanceEvent(
        pharmacy_id=staff.pharmacy_id, camera_id=body.camera_id, session_id=body.session_id,
        event_type=body.event_type, severity=body.severity, detected_at=datetime.now(timezone.utc),
        clip_ref=body.clip_ref, ai_result=_jsonable(body.ai_result) if body.ai_result else None,
        owner_notified=body.severity == "high", created_by=staff.id, updated_by=staff.id,
    )
    db.add(ev)
    await db.flush()
    return {"id": str(ev.id), "event_type": ev.event_type, "severity": ev.severity, "owner_notified": ev.owner_notified}


@router.get("/surveillance/events")
async def list_surveillance_events(
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(select(SurveillanceEvent).where(
        SurveillanceEvent.pharmacy_id == staff.pharmacy_id, SurveillanceEvent.is_deleted == False,  # noqa: E712
    ).order_by(SurveillanceEvent.detected_at.desc()).limit(200))).scalars().all()
    return [{"id": str(e.id), "camera_id": e.camera_id, "event_type": e.event_type, "severity": e.severity,
             "detected_at": e.detected_at.isoformat(), "owner_notified": e.owner_notified,
             "reviewed_by": str(e.reviewed_by) if e.reviewed_by else None} for e in rows]


@router.post("/shift-report")
async def create_shift_report(
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    events = (await db.execute(select(ShelfTransferEvent).where(
        ShelfTransferEvent.pharmacy_id == staff.pharmacy_id,
        ShelfTransferEvent.created_at >= day_start,
    ))).scalars().all()
    split_lots = (await db.execute(select(InventoryLot).where(
        InventoryLot.pharmacy_id == staff.pharmacy_id, InventoryLot.split_pack_open == True,  # noqa: E712
    ))).scalars().all()

    summary = R.build_shift_summary(
        transfer_events=[{"ndc11": e.ndc11, "quantity_delta": e.quantity_delta,
                          "shelf_id": str(e.shelf_id), "override_reason": e.override_reason} for e in events],
        open_split_packs=[{"lot": l.lot_number, "remaining": l.split_pack_remaining_blisters} for l in split_lots],
        cold_chain_events=[{"ndc11": e.ndc11, "temp": float(e.temperature_logged_c)}
                           for e in events if e.temperature_logged_c is not None],
    )
    report = ShiftHandoverReport(
        pharmacy_id=staff.pharmacy_id, shift_start=day_start, shift_end=now, performed_by=staff.id,
        transfers_completed=summary["transfers_completed"], fefo_overrides=summary["fefo_overrides"],
        anomaly_signals=summary["anomaly_signals"], open_split_packs=summary["open_split_packs"],
        cold_chain_events=summary["cold_chain_events"], created_by=staff.id, updated_by=staff.id,
    )
    db.add(report)
    await db.flush()
    return {"id": str(report.id), **summary}
