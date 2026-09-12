"""The Exception Register API — the board an operator works from.

`/reconciliation` answers "what is wrong right now". This answers the questions
that actually drive a shift: what is worst, who owns it, is it new or the same
one as last week, what exactly is affected, and what happens if I fix it.

The run is idempotent by construction. A scheduled reconciliation that opened
duplicates every night would turn the board into noise within a week, so
identity comes from a fingerprint over (check, entity) that excludes quantity
and timestamp, and the database enforces one open row per fingerprint.

Rules live in `services.core.inventory.exceptions`; this layer only fetches and
persists.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.core.inventory import exceptions as X
from services.core.inventory import ledger as L
from services.core.inventory import reconciliation as R
from services.platform.auth import require_permission
from services.platform.database import get_db
from services.platform.routers.inventory_integrity import _gather
from shared.models.auth import Staff

router = APIRouter()
log = logging.getLogger(__name__)

MAX_ROWS_STORED = 5_000       # a finding larger than this is a systemic fault, not a list


async def _record_event(db: AsyncSession, exception_id, event: str, *,
                        actor_id=None, actor_role: str | None = None,
                        from_status: str | None = None, to_status: str | None = None,
                        reason: str | None = None, payload: dict | None = None) -> None:
    await db.execute(text("""
        INSERT INTO inventory_exception_events
            (id, exception_id, event, from_status, to_status, actor_id,
             actor_role, reason, payload, created_at)
        VALUES (:i, :e, :ev, :fs, :ts, :a, :ar, :r, CAST(:p AS jsonb), NOW())"""),
        {"i": uuid4(), "e": exception_id, "ev": event, "fs": from_status,
         "ts": to_status, "a": actor_id, "ar": actor_role, "r": reason,
         "p": __import__("json").dumps(payload or {})})


# ── the run ───────────────────────────────────────────────────────────────

@router.post("/reconciliation/run")
async def run_reconciliation(
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    """Run every check and fold the result into the register.

    Idempotent: running twice with nothing changed opens nothing and resolves
    nothing. Findings that stopped firing resolve themselves — an operator
    should not have to close what the pharmacy already fixed.
    """
    findings = await _gather(db, staff.pharmacy_id)
    candidates = X.candidates_from(findings)

    existing = [dict(r) for r in (await db.execute(text("""
        SELECT id, fingerprint, status, first_seen_at, occurrences
        FROM inventory_exceptions
        WHERE pharmacy_id = :pid AND is_deleted = false"""),
        {"pid": staff.pharmacy_id})).mappings().all()]

    delta = X.diff(candidates, existing)
    now = datetime.now(timezone.utc)

    for c in delta.opened:
        imp = X.score(check=c.check, severity=c.severity,
                      financial_impact=c.financial_impact,
                      confidence=c.confidence, is_controlled=c.is_controlled)
        eid = uuid4()
        await db.execute(text("""
            INSERT INTO inventory_exceptions
                (id, pharmacy_id, fingerprint, check_code, severity, entity_type,
                 entity_key, title_fa, detail, remediation, score, score_breakdown,
                 financial_impact, confidence, is_controlled, status,
                 first_seen_at, last_seen_at, occurrences, row_count,
                 created_at, updated_at, created_by, is_deleted)
            VALUES (:id,:pid,:fp,:ck,:sev,:et,:ek,:t,:d,:rem,:sc,CAST(:sb AS jsonb),
                    :fi,:cf,:ctl,'open',:now,:now,1,:rc,NOW(),NOW(),:by,false)"""),
            {"id": eid, "pid": staff.pharmacy_id, "fp": c.fingerprint,
             "ck": c.check, "sev": c.severity, "et": c.entity_type,
             "ek": c.entity_key[:240], "t": c.title_fa[:160], "d": c.detail,
             "rem": c.remediation, "sc": imp.score,
             "sb": __import__("json").dumps(imp.as_dict()),
             "fi": c.financial_impact, "cf": c.confidence, "ctl": c.is_controlled,
             "now": now, "rc": c.row_count, "by": staff.id})
        await _store_rows(db, eid, c)
        await _record_event(db, eid, "opened", actor_id=staff.id,
                            to_status=X.OPEN,
                            payload={"score": imp.score, "rows": c.row_count})

    for c, prior in delta.recurred:
        age = X.age_days(prior["first_seen_at"], now)
        occ = int(prior["occurrences"]) + 1
        imp = X.score(check=c.check, severity=c.severity,
                      financial_impact=c.financial_impact, confidence=c.confidence,
                      occurrences=occ, age_days=age, is_controlled=c.is_controlled)
        await db.execute(text("""
            UPDATE inventory_exceptions
               SET last_seen_at = :now, occurrences = :occ, row_count = :rc,
                   score = :sc, score_breakdown = CAST(:sb AS jsonb),
                   financial_impact = :fi, updated_at = NOW()
             WHERE id = :id"""),
            {"now": now, "occ": occ, "rc": c.row_count, "sc": imp.score,
             "sb": __import__("json").dumps(imp.as_dict()),
             "fi": c.financial_impact, "id": prior["id"]})
        # Replace the evidence with what the check just saw; the event log keeps
        # the history of how it changed.
        await db.execute(text("DELETE FROM inventory_exception_rows "
                              "WHERE exception_id = :e"), {"e": prior["id"]})
        await _store_rows(db, prior["id"], c)

    for e in delta.resolved:
        await db.execute(text("""
            UPDATE inventory_exceptions
               SET status = 'resolved', resolved_at = :now,
                   resolved_by = 'system', updated_at = NOW()
             WHERE id = :id"""), {"now": now, "id": e["id"]})
        await _record_event(db, e["id"], "resolved", from_status=e["status"],
                            to_status=X.RESOLVED,
                            reason="the check no longer fires")

    await db.commit()
    out = delta.summary()
    out["ran_at"] = now.isoformat()
    out["checks_run"] = len(findings)
    return out


async def _store_rows(db: AsyncSession, exception_id, c: X.Candidate) -> None:
    """Every affected row, not a preview — the register is where an operator
    goes precisely to see all 46."""
    import json
    rows = c.rows[:MAX_ROWS_STORED]
    for r in rows:
        await db.execute(text("""
            INSERT INTO inventory_exception_rows
                (id, exception_id, entity_type, entity_id, snapshot, created_at)
            VALUES (:i,:e,:et,:eid,CAST(:s AS jsonb),NOW())"""),
            {"i": uuid4(), "e": exception_id, "et": c.entity_type,
             "eid": str(r.get("lot_id") or r.get("fill_id") or
                        r.get("irc") or r.get("ndc11") or "")[:120],
             "s": json.dumps(r, ensure_ascii=False, default=str)})
    if len(c.rows) > MAX_ROWS_STORED:
        log.warning("exception %s truncated at %d of %d rows",
                    exception_id, MAX_ROWS_STORED, len(c.rows))


# ── the board ─────────────────────────────────────────────────────────────

@router.get("/exceptions")
async def list_exceptions(
    status: Optional[str] = Query("active", description="active | open | assigned "
                                                        "| accepted | suppressed | resolved | all"),
    check: Optional[str] = Query(None),
    assigned_to_me: bool = Query(False),
    controlled_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    """The board, ranked by impact rather than severity alone."""
    # Qualified with the alias: `staff` also carries pharmacy_id and is_deleted,
    # so an unqualified predicate is ambiguous once the join is added.
    where = ["e.pharmacy_id = :pid", "e.is_deleted = false"]
    p: dict[str, Any] = {"pid": staff.pharmacy_id, "lim": limit, "off": offset}
    if status == "active":
        where.append("e.status <> 'resolved'")
    elif status and status != "all":
        if status not in X.STATUSES:
            raise HTTPException(422, f"unknown status {status!r}")
        where.append("e.status = :st")
        p["st"] = status
    if check:
        where.append("e.check_code = :ck")
        p["ck"] = check
    if assigned_to_me:
        where.append("e.assigned_to_id = :me")
        p["me"] = staff.id
    if controlled_only:
        where.append("e.is_controlled = true")

    rows = (await db.execute(text(f"""
        SELECT e.*, COUNT(*) OVER () AS total_rows,
               st.username AS assignee_name
        FROM inventory_exceptions e
        LEFT JOIN staff st ON st.id = e.assigned_to_id
        WHERE {' AND '.join(where)}
        ORDER BY (e.status = 'resolved'), e.score DESC, e.last_seen_at DESC
        LIMIT :lim OFFSET :off"""), p)).mappings().all()

    counts = (await db.execute(text("""
        SELECT status, COUNT(*) n,
               COUNT(*) FILTER (WHERE severity = 'critical') crit
        FROM inventory_exceptions
        WHERE pharmacy_id = :pid AND is_deleted = false
        GROUP BY status"""), {"pid": staff.pharmacy_id})).mappings().all()

    return {
        "exceptions": [_serialize(r) for r in rows],
        "total": rows[0]["total_rows"] if rows else 0,
        "by_status": {c["status"]: {"count": int(c["n"]), "critical": int(c["crit"])}
                      for c in counts},
        "limit": limit, "offset": offset,
    }


def _serialize(r) -> dict:
    return {
        "id": str(r["id"]), "fingerprint": r["fingerprint"],
        "check": r["check_code"], "severity": r["severity"],
        "title_fa": r["title_fa"], "entity_type": r["entity_type"],
        "entity_key": r["entity_key"], "status": r["status"],
        "score": float(r["score"]), "score_breakdown": r["score_breakdown"],
        "financial_impact": float(r["financial_impact"]),
        "confidence": float(r["confidence"]), "is_controlled": bool(r["is_controlled"]),
        "row_count": int(r["row_count"]), "occurrences": int(r["occurrences"]),
        "first_seen_at": r["first_seen_at"].isoformat() if r["first_seen_at"] else None,
        "last_seen_at": r["last_seen_at"].isoformat() if r["last_seen_at"] else None,
        "age_days": round(X.age_days(r["first_seen_at"]), 1) if r["first_seen_at"] else None,
        "assigned_to": str(r["assigned_to_id"]) if r["assigned_to_id"] else None,
        "assignee_name": r.get("assignee_name"),
        "disposition": r["disposition"], "disposition_reason": r["disposition_reason"],
        "resolved_at": r["resolved_at"].isoformat() if r["resolved_at"] else None,
        "resolved_by": r["resolved_by"],
    }


@router.get("/exceptions/{exception_id}")
async def get_exception(
    exception_id: UUID,
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    """One finding in full: every affected row and the whole response history."""
    row = (await db.execute(text("""
        SELECT e.*, st.username AS assignee_name
        FROM inventory_exceptions e
        LEFT JOIN staff st ON st.id = e.assigned_to_id
        WHERE e.id = :id AND e.pharmacy_id = :pid AND e.is_deleted = false"""),
        {"id": exception_id, "pid": staff.pharmacy_id})).mappings().first()
    if row is None:
        raise HTTPException(404, "exception not found at this pharmacy")

    rows = (await db.execute(text("""
        SELECT entity_type, entity_id, snapshot, created_at
        FROM inventory_exception_rows WHERE exception_id = :e
        ORDER BY created_at"""), {"e": exception_id})).mappings().all()

    events = (await db.execute(text("""
        SELECT ev.event, ev.from_status, ev.to_status, ev.reason, ev.payload,
               ev.created_at, st.username AS actor_name, ev.actor_id
        FROM inventory_exception_events ev
        LEFT JOIN staff st ON st.id = ev.actor_id
        WHERE ev.exception_id = :e ORDER BY ev.created_at"""),
        {"e": exception_id})).mappings().all()

    out = _serialize(row)
    out["detail"] = row["detail"]
    out["remediation"] = row["remediation"]
    out["affected_rows"] = [dict(r["snapshot"]) for r in rows]
    out["history"] = [{
        "event": e["event"], "from": e["from_status"], "to": e["to_status"],
        "reason": e["reason"], "payload": e["payload"],
        "actor": e["actor_name"] or (str(e["actor_id"])[:8] if e["actor_id"] else "system"),
        "at": e["created_at"].isoformat() if e["created_at"] else None,
    } for e in events]
    return out


# ── acting on one ─────────────────────────────────────────────────────────

class Assign(BaseModel):
    assignee_id: Optional[UUID] = Field(None, description="null to unassign")
    note: Optional[str] = None


@router.post("/exceptions/{exception_id}/assign")
async def assign_exception(
    exception_id: UUID, body: Assign,
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    current = await _load_status(db, exception_id, staff.pharmacy_id)
    target = X.ASSIGNED if body.assignee_id else X.OPEN
    try:
        X.check_transition(current, target)
    except X.ExceptionError as e:
        raise HTTPException(409, str(e))
    await db.execute(text("""
        UPDATE inventory_exceptions
           SET assigned_to_id = :a, assigned_at = :now, status = :st, updated_at = NOW()
         WHERE id = :id"""),
        {"a": body.assignee_id, "now": datetime.now(timezone.utc),
         "st": target, "id": exception_id})
    await _record_event(db, exception_id, "assigned", actor_id=staff.id,
                        from_status=current, to_status=target, reason=body.note,
                        payload={"assignee": str(body.assignee_id) if body.assignee_id else None})
    await db.commit()
    return {"id": str(exception_id), "status": target,
            "assigned_to": str(body.assignee_id) if body.assignee_id else None}


class Disposition(BaseModel):
    disposition: str = Field(..., description="accepted | suppressed | resolved")
    reason: str = Field(..., min_length=3, max_length=1000)


@router.post("/exceptions/{exception_id}/disposition")
async def dispose_exception(
    exception_id: UUID, body: Disposition,
    staff: Staff = Depends(require_permission("inventory:approve")),
    db: AsyncSession = Depends(get_db),
):
    """Rule on a finding.

    Requires `inventory:approve`, not merely write: deciding a real discrepancy
    is tolerable is a judgement with the same weight as approving a write-off,
    and the reason is mandatory because without it the record is later
    indistinguishable from someone clearing their queue.
    """
    current = await _load_status(db, exception_id, staff.pharmacy_id)
    try:
        X.check_transition(current, body.disposition, reason=body.reason)
    except X.ExceptionError as e:
        raise HTTPException(409, str(e))

    now = datetime.now(timezone.utc)
    # The status parameter is cast explicitly: it is used both as a column value
    # and inside a CASE comparison, and Postgres otherwise deduces two different
    # types for the same placeholder.
    resolving = body.disposition == X.RESOLVED
    await db.execute(text("""
        UPDATE inventory_exceptions
           SET status = CAST(:st AS varchar), disposition = CAST(:st AS varchar),
               disposition_reason = :r, disposed_by_id = :by, disposed_at = :now,
               resolved_at = CASE WHEN :resolving THEN :now ELSE resolved_at END,
               resolved_by = CASE WHEN :resolving THEN 'human' ELSE resolved_by END,
               updated_at = NOW()
         WHERE id = :id"""),
        {"st": body.disposition, "r": body.reason, "by": staff.id,
         "now": now, "id": exception_id, "resolving": resolving})
    await _record_event(db, exception_id, "disposition", actor_id=staff.id,
                        from_status=current, to_status=body.disposition,
                        reason=body.reason)
    await db.commit()
    return {"id": str(exception_id), "status": body.disposition,
            "note": "a re-run will not re-raise this"
                    if body.disposition in (X.ACCEPTED, X.SUPPRESSED)
                    else "a re-run will reopen this if the check fires again"}


async def _load_status(db: AsyncSession, exception_id: UUID, pharmacy_id) -> str:
    st = (await db.execute(text(
        "SELECT status FROM inventory_exceptions WHERE id = :id "
        "AND pharmacy_id = :pid AND is_deleted = false"),
        {"id": exception_id, "pid": pharmacy_id})).scalar()
    if st is None:
        raise HTTPException(404, "exception not found at this pharmacy")
    return st


@router.post("/exceptions/{exception_id}/simulate")
async def simulate_correction(
    exception_id: UUID,
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    """Dry-run the corrective action this finding implies.

    Computed by the same ledger functions that would apply it, so the preview
    cannot drift from the real thing — a preview produced by separate code is a
    second implementation that will eventually disagree with the first.
    Changes nothing.
    """
    row = (await db.execute(text("""
        SELECT check_code, entity_type, entity_key FROM inventory_exceptions
        WHERE id = :id AND pharmacy_id = :pid AND is_deleted = false"""),
        {"id": exception_id, "pid": staff.pharmacy_id})).mappings().first()
    if row is None:
        raise HTTPException(404, "exception not found at this pharmacy")

    rows = [dict(r["snapshot"]) for r in (await db.execute(text(
        "SELECT snapshot FROM inventory_exception_rows WHERE exception_id = :e"),
        {"e": exception_id})).mappings().all()]

    check = row["check_code"]
    if check == "expired_on_hand":
        plan = []
        for r in rows:
            lot = (await db.execute(text("""
                SELECT id, lot_number, expiry_date, quantity_on_hand, is_recalled,
                       is_quarantined, cold_chain_breach
                FROM inventory_lots
                WHERE pharmacy_id = :pid AND lot_number = :ln AND is_deleted = false"""),
                {"pid": staff.pharmacy_id, "ln": r.get("lot_number")})).mappings().first()
            if lot is None:
                plan.append({"lot_number": r.get("lot_number"),
                             "action": "none", "why": "lot no longer exists"})
                continue
            view = L.Lot(lot_id=str(lot["id"]), lot_number=lot["lot_number"],
                         expiry_date=lot["expiry_date"],
                         quantity_on_hand=L.q(lot["quantity_on_hand"]),
                         is_recalled=bool(lot["is_recalled"]),
                         is_quarantined=bool(lot["is_quarantined"]),
                         cold_chain_breach=bool(lot["cold_chain_breach"]))
            try:
                planned = L.plan_issue([view], lot["quantity_on_hand"],
                                       movement_type="EXPIRY_REMOVAL",
                                       reason=f"expiry pull for {exception_id}")
                plan.append({"lot_number": lot["lot_number"],
                             "action": "EXPIRY_REMOVAL",
                             "quantity": float(abs(planned[0].quantity_delta)),
                             "on_hand_after": float(planned[0].quantity_after),
                             "requires_approval": planned[0].requires_approval})
            except L.LedgerError as e:
                plan.append({"lot_number": lot["lot_number"], "action": "blocked",
                             "why": str(e)})
        return {"exception_id": str(exception_id), "check": check,
                "applied": False, "plan": plan,
                "note": "nothing was changed; each line becomes an approval request"}

    return {"exception_id": str(exception_id), "check": check, "applied": False,
            "plan": [], "note": f"no automated correction is defined for {check}; "
                                f"see the remediation text and act in the workbench"}
