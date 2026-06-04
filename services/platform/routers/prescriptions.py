"""Prescription workflow router — intake, queue management, state transitions."""
from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.auth import get_current_staff, require_permission, require_pharmacist
from services.platform.database import get_db
from services.core.pharmacy_workflow.state_machine import (
    EPCSRequiredError, InvalidTransitionError, QueueOwnershipConflict, RxStateMachine
)
from services.core.pharmacy_workflow.intake_precompute import precompute_in_background
from shared.models.auth import Staff
from shared.models.prescription import DURAlert, Prescription, PrescriptionFill, RxStatus, RxStateEvent

router = APIRouter()


class RxIntakeRequest(BaseModel):
    patient_id: UUID
    prescriber_id: UUID
    ndc: str
    drug_name: str
    drug_strength: Optional[str] = None
    sig_text: str
    quantity_prescribed: float
    days_supply: int
    refills_authorized: int = 0
    written_date: date
    source: str = "paper"
    daw_code: str = "0"
    dea_schedule: Optional[str] = None


class TransitionRequest(BaseModel):
    to_status: str
    reason: Optional[str] = None


class DUROverrideRequest(BaseModel):
    alert_id: UUID
    override_reason: str


def rx_to_dict(rx: Prescription) -> dict:
    return {
        "id": str(rx.id),
        "rx_number": rx.rx_number,
        "patient_id": str(rx.patient_id),
        "prescriber_id": str(rx.prescriber_id),
        "ndc": rx.ndc,
        "drug_name": rx.drug_name,
        "drug_strength": rx.drug_strength,
        "sig_text": rx.sig_text,
        "sig_structured": rx.sig_structured,
        "quantity_prescribed": float(rx.quantity_prescribed),
        "days_supply": rx.days_supply,
        "refills_authorized": rx.refills_authorized,
        "refills_remaining": rx.refills_remaining,
        "dea_schedule": rx.dea_schedule,
        "is_controlled": rx.is_controlled,
        "status": rx.status,
        "source": rx.source,
        "written_date": rx.written_date.isoformat(),
        "fill_date": rx.fill_date.isoformat() if rx.fill_date else None,
        "daw_code": rx.daw_code,
        "acb_safety_report": rx.acb_safety_report,
        "ai_risk_score": float(rx.ai_risk_score) if rx.ai_risk_score else None,
        "claimed_by_staff_id": str(rx.claimed_by_staff_id) if rx.claimed_by_staff_id else None,
        # Precomputed at intake — ready before pharmacist opens the Rx
        "triage_lane": getattr(rx, "triage_lane", None),
        "triage_result": getattr(rx, "triage_result", None),
        "council_cache": getattr(rx, "council_cache", None),
        "council_computed_at": getattr(rx, "council_computed_at", None).isoformat()
                               if getattr(rx, "council_computed_at", None) else None,
        "intake_analysis_status": getattr(rx, "intake_analysis_status", "pending"),
        "created_at": rx.created_at.isoformat(),
        "updated_at": rx.updated_at.isoformat(),
    }


@router.post("", status_code=201)
async def intake_prescription(
    body: RxIntakeRequest,
    background_tasks: BackgroundTasks,
    staff: Staff = Depends(require_permission("rx:write")),
    db: AsyncSession = Depends(get_db),
):
    """
    Intake a new prescription into the system.
    Automatically triggers: SIG NLP parse, ACB pre-scan, DUR check.
    """
    sm = RxStateMachine(db)
    rx_number = await sm.generate_rx_number(staff.pharmacy_id)

    # Detect if controlled based on DEA schedule
    is_controlled = body.dea_schedule in ("CI", "CII", "CIII", "CIV", "CV") if body.dea_schedule else False

    rx = Prescription(
        pharmacy_id=staff.pharmacy_id,
        rx_number=rx_number,
        status=RxStatus.INTAKE.value,
        refills_remaining=body.refills_authorized,
        is_controlled=is_controlled,
        created_by=staff.id,
        **body.model_dump(),
    )
    db.add(rx)
    await db.flush()

    # Auto-advance to DUR queue
    await sm.transition(
        prescription_id=rx.id,
        to_status=RxStatus.PENDING_DUR,
        triggered_by_id=staff.id,
        triggered_by_type="system",
        reason="Auto-advanced to DUR queue",
    )

    # ── Fire precompute via FastAPI BackgroundTasks (reliable, post-response) ──
    # Council + triage are computed after the HTTP response is returned; cached on
    # the Rx so the pharmacist's review screen renders analysis instantly (no spinner).
    rx_id_str = str(rx.id)
    background_tasks.add_task(precompute_in_background, rx_id_str)

    return rx_to_dict(rx)


@router.get("")
async def get_queue(
    status: Optional[str] = None,
    pharmacy_id: Optional[UUID] = None,
    limit: int = 50,
    staff: Staff = Depends(require_permission("rx:read")),
    db: AsyncSession = Depends(get_db),
):
    """Get the Rx queue for this pharmacy, optionally filtered by status."""
    stmt = select(Prescription).where(
        Prescription.pharmacy_id == staff.pharmacy_id,
        Prescription.is_deleted == False,  # noqa: E712
    )
    if status:
        stmt = stmt.where(Prescription.status == status)
    else:
        # Default: show active workflow states (not terminal)
        active_statuses = [
            s.value for s in RxStatus
            if s not in (RxStatus.DISPENSED, RxStatus.CANCELLED, RxStatus.RETURNED_TO_STOCK, RxStatus.TRANSFERRED_OUT)
        ]
        stmt = stmt.where(Prescription.status.in_(active_statuses))

    stmt = stmt.order_by(Prescription.created_at).limit(limit)
    result = await db.execute(stmt)
    return [rx_to_dict(rx) for rx in result.scalars().all()]


@router.get("/{rx_id}")
async def get_prescription(
    rx_id: UUID,
    staff: Staff = Depends(require_permission("rx:read")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Prescription).where(
            Prescription.id == rx_id,
            Prescription.pharmacy_id == staff.pharmacy_id,
        )
    )
    rx = result.scalar_one_or_none()
    if not rx:
        raise HTTPException(404, "Prescription not found")
    return rx_to_dict(rx)


@router.post("/{rx_id}/claim")
async def claim_prescription(
    rx_id: UUID,
    staff: Staff = Depends(require_permission("rx:verify")),
    db: AsyncSession = Depends(get_db),
):
    """Claim an Rx for verification — locks it to this pharmacist's workstation."""
    sm = RxStateMachine(db)
    try:
        rx = await sm.claim_for_verification(prescription_id=rx_id, staff_id=staff.id)
        return {"status": "claimed", "rx": rx_to_dict(rx)}
    except QueueOwnershipConflict as e:
        raise HTTPException(409, str(e))
    except InvalidTransitionError as e:
        raise HTTPException(422, str(e))


@router.post("/{rx_id}/release")
async def release_prescription(
    rx_id: UUID,
    reason: Optional[str] = "Released",
    staff: Staff = Depends(require_permission("rx:verify")),
    db: AsyncSession = Depends(get_db),
):
    """Release a claimed Rx back to the queue."""
    sm = RxStateMachine(db)
    rx = await sm.release_from_verification(prescription_id=rx_id, staff_id=staff.id, reason=reason)
    return {"status": "released", "rx": rx_to_dict(rx)}


@router.post("/{rx_id}/transition")
async def transition_prescription(
    rx_id: UUID,
    body: TransitionRequest,
    staff: Staff = Depends(require_permission("rx:write")),
    db: AsyncSession = Depends(get_db),
):
    """
    Manually transition an Rx to a new status.
    Validates against the state machine; enforces EPCS for controlled substances.
    """
    try:
        to_status = RxStatus(body.to_status)
    except ValueError:
        raise HTTPException(400, f"Invalid status: {body.to_status}")

    # Verify requires pharmacist role
    if to_status in (RxStatus.PENDING_ADJUDICATION, RxStatus.READY_TO_FILL, RxStatus.DISPENSED):
        if not staff.has_permission("rx:verify"):
            raise HTTPException(403, f"Pharmacist verification required for {to_status.value}")

    sm = RxStateMachine(db)
    try:
        rx = await sm.transition(
            prescription_id=rx_id,
            to_status=to_status,
            triggered_by_id=staff.id,
            reason=body.reason,
        )

        # Auto-create a PrescriptionFill record when entering adjudication —
        # the NCPDP D.0 claim engine requires a fill record to adjudicate against.
        if to_status == RxStatus.PENDING_ADJUDICATION:
            from datetime import date as date_type
            from uuid import uuid4 as _uuid4
            from sqlalchemy import text as _text
            # Use raw SQL to avoid ORM greenlet issues after state machine transaction
            check = await db.execute(
                _text("SELECT id FROM prescription_fills WHERE prescription_id = :pid LIMIT 1"),
                {"pid": str(rx.id)}
            )
            if not check.scalar():
                fill_id = str(_uuid4())
                await db.execute(
                    _text("""
                        INSERT INTO prescription_fills
                            (id, prescription_id, fill_number, ndc_dispensed,
                             quantity_dispensed, days_supply, fill_date,
                             dispensing_pharmacist_id, verifying_pharmacist_id,
                             created_at, updated_at)
                        VALUES
                            (:id, :rx_id, 1, :ndc,
                             :qty, :days, :today,
                             :pharm_id, :pharm_id,
                             NOW(), NOW())
                    """),
                    {
                        "id":       fill_id,
                        "rx_id":    str(rx.id),
                        "ndc":      rx.ndc,
                        "qty":      float(rx.quantity_prescribed),
                        "days":     rx.days_supply,
                        "today":    date_type.today(),
                        "pharm_id": str(staff.id),
                    }
                )

        return {"status": "transitioned", "rx": rx_to_dict(rx)}
    except InvalidTransitionError as e:
        raise HTTPException(422, str(e))
    except EPCSRequiredError as e:
        raise HTTPException(403, str(e))


@router.post("/{rx_id}/dur-override")
async def override_dur_alert(
    rx_id: UUID,
    body: DUROverrideRequest,
    staff: Staff = Depends(require_permission("rx:override_dur")),
    db: AsyncSession = Depends(get_db),
):
    """
    Override a DUR alert with mandatory documented reason.
    Pharmacist only. Hard-stop alerts require override reason of > 10 characters.
    """
    from datetime import datetime, timezone
    result = await db.execute(
        select(DURAlert).where(
            DURAlert.id == body.alert_id,
            DURAlert.prescription_id == rx_id,
        )
    )
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(404, "DUR alert not found")

    if alert.is_hard_stop and len(body.override_reason) < 10:
        raise HTTPException(422, "Hard-stop override requires a detailed reason (minimum 10 characters)")

    alert.was_overridden = True
    alert.override_reason = body.override_reason
    alert.overridden_by_id = staff.id
    alert.overridden_at = datetime.now(timezone.utc)

    return {"status": "overridden", "alert_id": str(alert.id)}


@router.get("/{rx_id}/history")
async def get_rx_history(
    rx_id: UUID,
    staff: Staff = Depends(require_permission("rx:read")),
    db: AsyncSession = Depends(get_db),
):
    """Full immutable state event history for an Rx."""
    result = await db.execute(
        select(RxStateEvent)
        .where(RxStateEvent.prescription_id == rx_id)
        .order_by(RxStateEvent.created_at)
    )
    events = result.scalars().all()
    return [
        {
            "id": str(e.id),
            "from_status": e.from_status,
            "to_status": e.to_status,
            "triggered_by_id": str(e.triggered_by_id) if e.triggered_by_id else None,
            "triggered_by_type": e.triggered_by_type,
            "reason": e.reason,
            "event_hash": e.event_hash,
            "created_at": e.created_at.isoformat(),
        }
        for e in events
    ]


@router.get("/{rx_id}/dur-alerts")
async def get_dur_alerts(
    rx_id: UUID,
    staff: Staff = Depends(require_permission("rx:read")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DURAlert).where(DURAlert.prescription_id == rx_id)
    )
    alerts = result.scalars().all()
    return [
        {
            "id": str(a.id),
            "alert_type": a.alert_type,
            "severity": a.severity,
            "source": a.source,
            "description": a.description,
            "interacting_drug_name": a.interacting_drug_name,
            "is_hard_stop": a.is_hard_stop,
            "was_shown": a.was_shown,
            "was_overridden": a.was_overridden,
            "override_reason": a.override_reason,
            "evidence_grade": a.evidence_grade,
        }
        for a in alerts
    ]


@router.get("/{rx_id}/analysis")
async def get_rx_analysis(
    rx_id: UUID,
    staff: Staff = Depends(require_permission("rx:read")),
    db: AsyncSession = Depends(get_db),
):
    """
    Return the precomputed council + triage for an Rx.
    status=ready  → council_cache and triage_result are populated.
    status=pending → still computing (retry in ~2 s).
    status=failed  → precompute did not succeed; fall back to SSE council stream.
    """
    from sqlalchemy import text as _text
    row = (await db.execute(
        _text("""SELECT id, intake_analysis_status, triage_lane, triage_result,
                        council_cache, council_computed_at
                 FROM prescriptions WHERE id = :id"""),
        {"id": str(rx_id)})).mappings().first()
    if not row:
        raise HTTPException(404, "Prescription not found")
    return {
        "rx_id": str(row["id"]),
        "status": row["intake_analysis_status"],
        "triage_lane": row["triage_lane"],
        "triage_result": row["triage_result"],
        "council_cache": row["council_cache"],
        "council_computed_at": row["council_computed_at"].isoformat()
                               if row["council_computed_at"] else None,
    }


@router.post("/{rx_id}/reanalyze")
async def reanalyze_prescription(
    rx_id: UUID,
    background_tasks: BackgroundTasks,
    staff: Staff = Depends(require_permission("rx:write")),
    db: AsyncSession = Depends(get_db),
):
    """
    Force a fresh precompute — e.g. after new labs, allergy, or family data arrive.
    Returns immediately; call GET /{rx_id}/analysis to poll for the result.
    """
    background_tasks.add_task(precompute_in_background, str(rx_id))
    return {"status": "triggered", "rx_id": str(rx_id)}


@router.get("/{rx_id}/fills")
async def get_rx_fills(
    rx_id: UUID,
    staff: Staff = Depends(require_permission("rx:read")),
    db: AsyncSession = Depends(get_db),
):
    """Return all fill records for a prescription (used by adjudication to get fill_id)."""
    result = await db.execute(
        select(PrescriptionFill)
        .where(PrescriptionFill.prescription_id == rx_id)
        .order_by(PrescriptionFill.fill_number)
    )
    fills = result.scalars().all()
    return [
        {
            "id": str(f.id),
            "prescription_id": str(f.prescription_id),
            "fill_number": f.fill_number,
            "ndc_dispensed": f.ndc_dispensed,
            "quantity_dispensed": float(f.quantity_dispensed),
            "days_supply": f.days_supply,
            "fill_date": str(f.fill_date),
        }
        for f in fills
    ]


@router.websocket("/queue/ws/{pharmacy_id}")
async def rx_queue_websocket(
    websocket: WebSocket,
    pharmacy_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Real-time Rx queue updates for pharmacist workstations.
    Broadcasts queue changes to all connected workstations.
    """
    await websocket.accept()
    try:
        while True:
            # In production: subscribe to Kafka topic rx.queue.{pharmacy_id}
            # For now: poll every 3 seconds
            import asyncio
            await asyncio.sleep(3)
            result = await db.execute(
                select(Prescription).where(
                    Prescription.pharmacy_id == pharmacy_id,
                    Prescription.status.in_([
                        RxStatus.PENDING_VERIFICATION.value,
                        RxStatus.VERIFICATION_IN_PROGRESS.value,
                        RxStatus.PENDING_ADJUDICATION.value,
                        RxStatus.ADJUDICATION_REJECTED.value,
                        RxStatus.READY_TO_FILL.value,
                        RxStatus.FILLING.value,
                        RxStatus.FILLED.value,
                        RxStatus.WILL_CALL.value,
                    ]),
                    Prescription.is_deleted == False,  # noqa: E712
                ).order_by(Prescription.created_at).limit(100)
            )
            rxs = result.scalars().all()
            await websocket.send_json({
                "event": "queue_update",
                "count": len(rxs),
                "items": [rx_to_dict(r) for r in rxs],
            })
    except WebSocketDisconnect:
        pass
