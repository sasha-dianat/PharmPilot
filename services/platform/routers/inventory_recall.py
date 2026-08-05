"""Recall cases — the pharmacy's response to a recall, not the notice of one.

`RecallAlertBanner` already shows what the world has recalled. This answers the
questions that follow: do we hold it, where is it, who did we give it to, and
what is left to do. The banner is the trigger; this is the workflow.

The dispense hook made the central question answerable for the first time by
putting a real foreign key on the fill. Before it, `PrescriptionFill.lot_number`
was free text and usually blank — which is why every result here carries a
completeness figure. Run against this pharmacy's current data the trace is 0%:
all 46 existing fills predate the link. A recall that reported "no patients
affected" from that would be confidently, dangerously wrong, so it reports the
blind spot instead.

Rules live in `services.core.inventory.recall`; this layer resolves and persists.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.core.inventory import recall as RC
from services.platform.auth import require_permission
from services.platform.database import get_db
from shared.models.auth import Staff

router = APIRouter()
log = logging.getLogger(__name__)

# How far back to look for dispenses of the product when measuring the blind
# spot. A recall notice normally names a manufacture window; absent one, a year
# is the conservative default — too short and untraceable dispenses look fewer
# than they are.
DEFAULT_LOOKBACK_DAYS = 365


class OpenRecall(BaseModel):
    reference: str = Field(..., min_length=2, max_length=80)
    scope_type: str = Field(..., description="lot_number | irc | gtin | supplier_batch")
    scope_value: str = Field(..., min_length=1, max_length=120)
    severity: str = Field("II", description="I | II | III")
    reason: str = Field(..., min_length=3)
    source: Optional[str] = Field(None, max_length=80)
    lookback_days: int = Field(DEFAULT_LOOKBACK_DAYS, ge=1, le=3650)


async def _resolve(db: AsyncSession, pharmacy_id, scope_type: str,
                   scope_value: str, lookback_days: int) -> tuple[list, list, int, dict]:
    """Find the affected lots, their dispenses, and the size of the blind spot."""
    if scope_type not in RC.SCOPES:
        raise HTTPException(422, f"unknown scope {scope_type!r}; "
                                 f"expected one of {list(RC.SCOPES)}")

    where = {"lot_number": "UPPER(il.lot_number) = UPPER(:v)",
             "irc": "il.irc = :v",
             "gtin": "dc.gtin = :v",
             "supplier_batch": "UPPER(il.lot_number) = UPPER(:v)"}[scope_type]

    lots = [dict(r) for r in (await db.execute(text(f"""
        SELECT il.id, il.lot_number, il.irc, il.ndc11, il.expiry_date,
               il.quantity_on_hand, il.storage_location, il.is_quarantined,
               il.is_recalled, il.cold_chain_breach
        FROM inventory_lots il
        LEFT JOIN drug_catalog dc ON dc.irc = il.irc
        WHERE il.pharmacy_id = :pid AND il.is_deleted = false AND {where}"""),
        {"pid": pharmacy_id, "v": scope_value})).mappings().all()]

    lot_ids = [l["id"] for l in lots]
    ndcs = sorted({l["ndc11"] for l in lots if l.get("ndc11")})

    dispenses: list[dict] = []
    if lot_ids:
        dispenses = [dict(r) for r in (await db.execute(text("""
            SELECT m.inventory_lot_id AS lot_id, il.lot_number,
                   ABS(m.quantity_delta) AS quantity,
                   m.prescription_fill_id AS fill_id,
                   p.patient_id, m.created_at AS dispensed_at
            FROM inventory_movements m
            JOIN inventory_lots il ON il.id = m.inventory_lot_id
            LEFT JOIN prescription_fills pf ON pf.id = m.prescription_fill_id
            LEFT JOIN prescriptions p ON p.id = pf.prescription_id
            WHERE m.pharmacy_id = :pid AND m.movement_type = 'DISPENSE'
              AND m.inventory_lot_id = ANY(:ids)"""),
            {"pid": pharmacy_id, "ids": lot_ids})).mappings().all()]

    # Every dispense of the product in the window, whether or not it names a
    # lot. The gap against the list above is what we cannot see.
    total = 0
    if ndcs:
        total = (await db.execute(text("""
            SELECT COUNT(*) FROM prescription_fills pf
            WHERE pf.is_deleted = false AND pf.ndc_dispensed = ANY(:ndcs)
              AND pf.created_at >= NOW() - make_interval(days => CAST(:d AS integer))"""),
            {"ndcs": ndcs, "d": lookback_days})).scalar() or 0

    meta = {"ndcs": ndcs, "lookback_days": lookback_days}
    return lots, dispenses, int(total), meta


@router.post("/recalls", status_code=201)
async def open_recall(
    body: OpenRecall,
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    """Open a case and resolve its impact immediately.

    Resolution happens on open, not on demand: the first thing anyone needs is
    the number of sellable units still on the shelf, and making that a separate
    call invites a recall that is recorded but never assessed.
    """
    if body.severity not in RC.CLASSES:
        raise HTTPException(422, f"unknown recall class {body.severity!r}")

    dup = (await db.execute(text(
        "SELECT id FROM recall_cases WHERE pharmacy_id = :pid AND reference = :r "
        "AND is_deleted = false"),
        {"pid": staff.pharmacy_id, "r": body.reference})).scalar()
    if dup:
        raise HTTPException(409, f"recall {body.reference!r} already exists "
                                 f"({dup}); reopen it rather than duplicating")

    lots, dispenses, total, meta = await _resolve(
        db, staff.pharmacy_id, body.scope_type, body.scope_value, body.lookback_days)
    impact = RC.build_impact(lots, dispenses, severity=body.severity,
                             product_dispenses_total=total)
    s = impact.summary()

    rid = uuid4()
    now = datetime.now(timezone.utc)
    await db.execute(text("""
        INSERT INTO recall_cases
            (id, pharmacy_id, reference, severity, scope_type, scope_value,
             irc, ndc11, reason, source, status, opened_at, opened_by_id,
             impact, units_on_shelf, units_blocked, units_dispensed,
             lots_affected, patients_identified, patients_notified,
             traceable_pct, trace_complete,
             created_at, updated_at, created_by, is_deleted)
        VALUES (:id,:pid,:ref,:sev,:st,:sv,:irc,:ndc,:rsn,:src,'open',:now,:by,
                CAST(:imp AS jsonb),:on,:bl,:di,:la,:pi,0,:tp,:tc,
                NOW(),NOW(),:by,false)"""),
        {"id": rid, "pid": staff.pharmacy_id, "ref": body.reference,
         "sev": body.severity, "st": body.scope_type, "sv": body.scope_value,
         "irc": next((l["irc"] for l in lots if l.get("irc")), None),
         "ndc": (meta["ndcs"] or [None])[0], "rsn": body.reason,
         "src": body.source, "now": now, "by": staff.id,
         "imp": json.dumps(s, ensure_ascii=False, default=str),
         "on": s["on_shelf"], "bl": s["blocked"], "di": s["dispensed"],
         "la": s["lots_affected"], "pi": s["patients_identified"],
         "tp": impact.completeness.traceable_pct,
         "tc": impact.completeness.complete})

    for u in impact.units:
        await db.execute(text("""
            INSERT INTO recall_case_lines
                (id, recall_case_id, state, quantity, inventory_lot_id, lot_number,
                 storage_location, prescription_fill_id, patient_id, dispensed_at,
                 action_required, created_at, updated_at)
            VALUES (:id,:rc,:s,:q,:lot,:ln,:loc,:fill,:pat,:at,:act,NOW(),NOW())"""),
            {"id": uuid4(), "rc": rid, "s": u.state, "q": float(u.quantity),
             "lot": UUID(u.lot_id) if u.lot_id else None, "ln": u.lot_number,
             "loc": u.location,
             "fill": UUID(u.fill_id) if u.fill_id else None,
             "pat": UUID(u.patient_id) if u.patient_id else None,
             "at": u.dispensed_at, "act": u.action[:160]})
    await db.commit()

    return {"recall_id": str(rid), "reference": body.reference,
            "status": RC.OPEN, **s,
            "next_step": s["urgent_action"] or
                         "no sellable stock remains; notify the identified patients"}


@router.get("/recalls")
async def list_recalls(
    status: Optional[str] = Query(None),
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    where = ["pharmacy_id = :pid", "is_deleted = false"]
    p: dict[str, Any] = {"pid": staff.pharmacy_id}
    if status:
        if status not in RC.RECALL_STATUSES:
            raise HTTPException(422, f"unknown status {status!r}")
        where.append("status = :st")
        p["st"] = status
    rows = (await db.execute(text(f"""
        SELECT * FROM recall_cases WHERE {' AND '.join(where)}
        ORDER BY (status IN ('closed','cancelled')),
                 CASE severity WHEN 'I' THEN 0 WHEN 'II' THEN 1 ELSE 2 END,
                 opened_at DESC"""), p)).mappings().all()
    return {"recalls": [_case(r) for r in rows], "count": len(rows)}


def _case(r) -> dict:
    return {
        "id": str(r["id"]), "reference": r["reference"], "severity": r["severity"],
        "scope": f'{r["scope_type"]}={r["scope_value"]}', "status": r["status"],
        "reason": r["reason"], "source": r["source"],
        "units_on_shelf": float(r["units_on_shelf"]),
        "units_blocked": float(r["units_blocked"]),
        "units_dispensed": float(r["units_dispensed"]),
        "lots_affected": int(r["lots_affected"]),
        "patients_identified": int(r["patients_identified"]),
        "patients_notified": int(r["patients_notified"]),
        "traceable_pct": float(r["traceable_pct"]) if r["traceable_pct"] is not None else None,
        "trace_complete": bool(r["trace_complete"]),
        "opened_at": r["opened_at"].isoformat() if r["opened_at"] else None,
        "closed_at": r["closed_at"].isoformat() if r["closed_at"] else None,
        "closed_over_blockers": bool(r["closed_over_blockers"]),
        "closure_reason": r["closure_reason"],
        "impact": r["impact"],
    }


@router.get("/recalls/{recall_id}")
async def get_recall(
    recall_id: UUID,
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    """The case, every affected line, and what is left to do."""
    row = (await db.execute(text(
        "SELECT * FROM recall_cases WHERE id = :id AND pharmacy_id = :pid "
        "AND is_deleted = false"),
        {"id": recall_id, "pid": staff.pharmacy_id})).mappings().first()
    if row is None:
        raise HTTPException(404, "recall not found at this pharmacy")

    lines = (await db.execute(text("""
        SELECT l.*, pt.first_name, pt.last_name
        FROM recall_case_lines l
        LEFT JOIN patients pt ON pt.id = l.patient_id
        WHERE l.recall_case_id = :id
        ORDER BY CASE l.state WHEN 'on_shelf' THEN 0 WHEN 'blocked' THEN 1
                              WHEN 'dispensed' THEN 2 ELSE 3 END,
                 l.quantity DESC"""), {"id": recall_id})).mappings().all()

    out = _case(row)
    out["lines"] = [{
        "id": str(l["id"]), "state": l["state"], "quantity": float(l["quantity"]),
        "lot_number": l["lot_number"], "location": l["storage_location"],
        "lot_id": str(l["inventory_lot_id"]) if l["inventory_lot_id"] else None,
        "fill_id": str(l["prescription_fill_id"]) if l["prescription_fill_id"] else None,
        "patient_id": str(l["patient_id"]) if l["patient_id"] else None,
        "patient_name": (f'{l["first_name"] or ""} {l["last_name"] or ""}'.strip()
                         or None),
        "dispensed_at": l["dispensed_at"].isoformat() if l["dispensed_at"] else None,
        "action_required": l["action_required"], "action_taken": l["action_taken"],
        "action_taken_at": l["action_taken_at"].isoformat() if l["action_taken_at"] else None,
        "note": l["note"],
    } for l in lines]
    out["outstanding"] = {
        "lots_to_quarantine": sum(1 for l in lines
                                  if l["state"] == RC.ON_SHELF and not l["action_taken"]),
        "patients_to_notify": sum(1 for l in lines
                                  if l["state"] == RC.DISPENSED and not l["action_taken"]),
    }
    return out


@router.post("/recalls/{recall_id}/quarantine")
async def quarantine_affected(
    recall_id: UUID,
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    """Take every still-sellable affected lot off the shelf, in one action.

    Blocking stock never waits for a signature — that asymmetry is deliberate
    and matches the admin panel's edit policy: taking stock out of use is always
    immediate, releasing it always needs approval. Destroying it is a separate,
    approved write-off.
    """
    case = (await db.execute(text(
        "SELECT reference, status FROM recall_cases WHERE id = :id "
        "AND pharmacy_id = :pid AND is_deleted = false"),
        {"id": recall_id, "pid": staff.pharmacy_id})).mappings().first()
    if case is None:
        raise HTTPException(404, "recall not found at this pharmacy")
    if case["status"] in (RC.CLOSED, RC.CANCELLED):
        raise HTTPException(409, f"recall is {case['status']}")

    lines = (await db.execute(text("""
        SELECT id, inventory_lot_id FROM recall_case_lines
        WHERE recall_case_id = :id AND state = 'on_shelf'
          AND action_taken IS NULL AND inventory_lot_id IS NOT NULL"""),
        {"id": recall_id})).mappings().all()

    now = datetime.now(timezone.utc)
    for l in lines:
        await db.execute(text("""
            UPDATE inventory_lots
               SET is_quarantined = true, is_recalled = true,
                   recall_reference = :ref, updated_by = :by, updated_at = NOW()
             WHERE id = :lot AND pharmacy_id = :pid"""),
            {"ref": case["reference"][:100], "by": staff.id,
             "lot": l["inventory_lot_id"], "pid": staff.pharmacy_id})
        await db.execute(text("""
            UPDATE recall_case_lines
               SET action_taken = 'quarantined', action_taken_at = :now,
                   action_taken_by_id = :by, state = 'blocked', updated_at = NOW()
             WHERE id = :id"""), {"now": now, "by": staff.id, "id": l["id"]})

    await db.execute(text("""
        UPDATE recall_cases SET units_on_shelf = 0, updated_at = NOW(),
               status = CASE WHEN status = 'open' THEN 'contained' ELSE status END,
               contained_at = COALESCE(contained_at, :now)
         WHERE id = :id"""), {"id": recall_id, "now": now})
    await db.commit()
    return {"recall_id": str(recall_id), "lots_quarantined": len(lines),
            "status": RC.CONTAINED if lines else None,
            "note": "quarantined and flagged; destruction is a separate "
                    "write-off requiring approval"}


class LineAction(BaseModel):
    action: str = Field(..., description="notified | quarantined | written_off | no_action")
    note: Optional[str] = None


@router.post("/recalls/{recall_id}/lines/{line_id}/action")
async def record_line_action(
    recall_id: UUID, line_id: UUID, body: LineAction,
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    """Record what was done about one affected unit — a patient told, a lot
    pulled. Per-line, so a half-finished recall shows exactly which half."""
    owned = (await db.execute(text(
        "SELECT 1 FROM recall_case_lines l JOIN recall_cases c ON c.id = l.recall_case_id "
        "WHERE l.id = :lid AND c.id = :rid AND c.pharmacy_id = :pid"),
        {"lid": line_id, "rid": recall_id, "pid": staff.pharmacy_id})).scalar()
    if not owned:
        raise HTTPException(404, "recall line not found at this pharmacy")

    await db.execute(text("""
        UPDATE recall_case_lines
           SET action_taken = :a, action_taken_at = :now, action_taken_by_id = :by,
               note = :n, updated_at = NOW()
         WHERE id = :id"""),
        {"a": body.action[:32], "now": datetime.now(timezone.utc), "by": staff.id,
         "n": body.note, "id": line_id})

    notified = (await db.execute(text("""
        SELECT COUNT(DISTINCT patient_id) FROM recall_case_lines
        WHERE recall_case_id = :id AND state = 'dispensed'
          AND action_taken = 'notified' AND patient_id IS NOT NULL"""),
        {"id": recall_id})).scalar() or 0
    await db.execute(text(
        "UPDATE recall_cases SET patients_notified = :n, updated_at = NOW() "
        "WHERE id = :id"), {"n": int(notified), "id": recall_id})
    await db.commit()
    return {"line_id": str(line_id), "action": body.action,
            "patients_notified": int(notified)}


@router.get("/recalls/{recall_id}/patients")
async def affected_patients(
    recall_id: UUID,
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    """Who received the recalled stock — and how much of the picture this is.

    The warning is returned alongside the list, never separately: a short list
    read without it invites exactly the wrong conclusion.
    """
    case = (await db.execute(text(
        "SELECT severity, traceable_pct, trace_complete, impact FROM recall_cases "
        "WHERE id = :id AND pharmacy_id = :pid AND is_deleted = false"),
        {"id": recall_id, "pid": staff.pharmacy_id})).mappings().first()
    if case is None:
        raise HTTPException(404, "recall not found at this pharmacy")

    rows = (await db.execute(text("""
        SELECT l.patient_id, pt.first_name, pt.last_name, pt.phone_primary,
               SUM(l.quantity) AS units, MIN(l.dispensed_at) AS first_dispensed,
               MAX(l.dispensed_at) AS last_dispensed,
               BOOL_OR(l.action_taken = 'notified') AS notified
        FROM recall_case_lines l
        LEFT JOIN patients pt ON pt.id = l.patient_id
        WHERE l.recall_case_id = :id AND l.state = 'dispensed'
        GROUP BY l.patient_id, pt.first_name, pt.last_name, pt.phone_primary
        ORDER BY notified, units DESC"""), {"id": recall_id})).mappings().all()

    impact = case["impact"] or {}
    return {
        "patients": [{
            "patient_id": str(r["patient_id"]) if r["patient_id"] else None,
            "name": (f'{r["first_name"] or ""} {r["last_name"] or ""}'.strip() or None),
            "phone": r["phone_primary"],
            "units": float(r["units"]),
            "first_dispensed": r["first_dispensed"].isoformat() if r["first_dispensed"] else None,
            "last_dispensed": r["last_dispensed"].isoformat() if r["last_dispensed"] else None,
            "notified": bool(r["notified"]),
        } for r in rows],
        "count": len(rows),
        "traceable_pct": float(case["traceable_pct"]) if case["traceable_pct"] is not None else None,
        "trace_complete": bool(case["trace_complete"]),
        "warning": impact.get("warning"),
    }


class CloseRecall(BaseModel):
    force_reason: Optional[str] = Field(
        None, description="required only to close over outstanding blockers")


@router.post("/recalls/{recall_id}/close")
async def close_recall(
    recall_id: UUID, body: CloseRecall,
    staff: Staff = Depends(require_permission("inventory:approve")),
    db: AsyncSession = Depends(get_db),
):
    """Close a recall, or explain why it cannot close yet.

    Requires `inventory:approve`. Closing over outstanding blockers is possible
    — sometimes the stock genuinely was destroyed on site — but it is never
    silent: the blockers are kept on the record beside the reason.
    """
    row = (await db.execute(text(
        "SELECT * FROM recall_cases WHERE id = :id AND pharmacy_id = :pid "
        "AND is_deleted = false"),
        {"id": recall_id, "pid": staff.pharmacy_id})).mappings().first()
    if row is None:
        raise HTTPException(404, "recall not found at this pharmacy")
    if row["status"] in (RC.CLOSED, RC.CANCELLED):
        raise HTTPException(409, f"recall is already {row['status']}")

    # Re-resolve rather than trusting the snapshot: stock moves while a recall
    # is worked, and closing on a stale picture is the failure this guards.
    lots, dispenses, total, _ = await _resolve(
        db, staff.pharmacy_id, row["scope_type"], row["scope_value"],
        DEFAULT_LOOKBACK_DAYS)
    impact = RC.build_impact(lots, dispenses, severity=row["severity"],
                             product_dispenses_total=total)

    try:
        verdict = RC.closure_check(impact,
                                   patients_notified=int(row["patients_notified"]),
                                   force_reason=body.force_reason)
    except RC.RecallError as e:
        raise HTTPException(422, str(e))

    if not verdict["may_close"]:
        return {"recall_id": str(recall_id), "closed": False,
                "status": verdict["next_status"], "blockers": verdict["blockers"],
                "note": "supply force_reason to close over these"}

    now = datetime.now(timezone.utc)
    await db.execute(text("""
        UPDATE recall_cases
           SET status = 'closed', closed_at = :now, closed_by_id = :by,
               closure_reason = :r, closed_over_blockers = :forced,
               units_on_shelf = :on, updated_at = NOW()
         WHERE id = :id"""),
        {"now": now, "by": staff.id, "r": body.force_reason,
         "forced": bool(verdict.get("forced")),
         "on": float(impact.sellable_remaining), "id": recall_id})
    await db.commit()
    return {"recall_id": str(recall_id), "closed": True, "status": RC.CLOSED,
            "closed_over_blockers": bool(verdict.get("forced")),
            "blockers_at_closure": verdict["blockers"]}
