"""
Security Events API
===================
Receives behavioral detections from the edge node (Jetson),
persists security events, broadcasts real-time alerts to pharmacist UI,
and exposes duress activation endpoints.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.auth import get_current_staff, require_permission
from services.platform.database import get_db
from services.biometric.behavioral_analysis.detector import BehaviorDetection, BehaviorType, AlertSeverity
from services.biometric.behavioral_analysis.duress_protocol import (
    DuressProtocolEngine, DuressIncident, DuressTriggerMethod, PharmacySecurityConfig
)
from shared.models.auth import Staff
from shared.models.biometric import SecurityEvent

router = APIRouter()
logger = logging.getLogger(__name__)

# Active WebSocket connections per pharmacy — for real-time alert push
_security_ws_connections: dict[str, list[WebSocket]] = {}


# ── Behavioral detection ingest (called by edge node) ─────────────────────

class BehaviorDetectionPayload(BaseModel):
    pharmacy_id: UUID
    camera_zone: str
    track_id: int
    behavior_type: str
    severity: str
    confidence: float
    description: str
    biometric_identity_id: Optional[UUID] = None
    evidence_frame_timestamps: list[float] = []
    auto_response_triggered: bool = False


@router.post("/behavior-detection", status_code=202)
async def ingest_behavior_detection(
    payload: BehaviorDetectionPayload,
    db: AsyncSession = Depends(get_db),
):
    """
    Called by the Jetson edge node when a confirmed behavioral detection occurs.
    Persists the event and broadcasts to all connected pharmacist workstations.
    """
    # Persist to security_events table
    event = SecurityEvent(
        pharmacy_id=payload.pharmacy_id,
        biometric_identity_id=payload.biometric_identity_id,
        event_type=payload.behavior_type,
        severity=payload.severity,
        description=payload.description,
        detected_at=datetime.now(timezone.utc),
        camera_footage_retained=(payload.severity == "critical"),
        event_metadata={
            "track_id": payload.track_id,
            "camera_zone": payload.camera_zone,
            "confidence": payload.confidence,
            "auto_response": payload.auto_response_triggered,
            "evidence_timestamps": payload.evidence_frame_timestamps,
        },
    )
    db.add(event)
    await db.flush()

    # Broadcast to connected pharmacist workstations
    await _broadcast_security_alert(
        pharmacy_id=str(payload.pharmacy_id),
        event_data={
            "event_id": str(event.id),
            "type": "behavior_detection",
            "behavior": payload.behavior_type,
            "severity": payload.severity,
            "zone": payload.camera_zone,
            "description": payload.description,
            "confidence": payload.confidence,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "requires_attention": payload.severity in ("high", "critical"),
        }
    )

    logger.info(
        "Security event: pharmacy=%s type=%s severity=%s confidence=%.0f%%",
        str(payload.pharmacy_id)[:8], payload.behavior_type,
        payload.severity, payload.confidence * 100
    )
    return {"event_id": str(event.id), "status": "recorded"}


# ── Duress activation ──────────────────────────────────────────────────────

class DuressActivationRequest(BaseModel):
    trigger_method: str = "manual_staff"
    biometric_ids_present: list[UUID] = []


@router.post("/duress/activate")
async def activate_duress_protocol(
    body: DuressActivationRequest,
    staff: Staff = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """
    Activate the duress protocol. All staff roles can trigger this —
    the endpoint is also called automatically by the behavioral AI on CRITICAL detections.
    """
    # Load pharmacy config
    from shared.models.pharmacy import Pharmacy
    pharmacy_result = await db.execute(select(Pharmacy).where(Pharmacy.id == staff.pharmacy_id))
    pharmacy = pharmacy_result.scalar_one_or_none()

    if not pharmacy:
        raise HTTPException(404, "Pharmacy not found")

    config = PharmacySecurityConfig(
        pharmacy_id=staff.pharmacy_id,
        address=f"{pharmacy.address_line1}, {pharmacy.city}, {pharmacy.state} {pharmacy.zip_code}",
        phone=pharmacy.phone or "",
        lat=0.0,  # Would be stored in pharmacy.config in production
        lng=0.0,
        chain_security_webhook=pharmacy.config.get("chain_security_webhook"),
        vault_controller_api=pharmacy.config.get("vault_controller_api"),
        camera_controller_api=pharmacy.config.get("camera_controller_api"),
    )

    engine = DuressProtocolEngine(config=config, db=db)
    incident = await engine.activate(
        trigger_method=DuressTriggerMethod(body.trigger_method),
        triggered_by_staff_id=staff.id,
        biometric_ids_present=body.biometric_ids_present,
    )

    return {
        "incident_id": str(incident.incident_id),
        "status": "activated",
        "actions_completed": incident.actions_completed,
        "actions_failed": incident.actions_failed,
        "message": "Duress protocol activated. Emergency services notified. Vault locked.",
    }


@router.post("/duress/resolve/{incident_id}")
async def resolve_duress_incident(
    incident_id: UUID,
    resolution_notes: str,
    staff: Staff = Depends(require_permission("rx:write")),
    db: AsyncSession = Depends(get_db),
):
    """Mark a duress incident as resolved (police cleared, staff safe)."""
    await db.execute(text("""
        UPDATE security_events
        SET resolved = true,
            resolved_at = :now,
            resolved_by_id = :staff_id,
            resolution_notes = :notes
        WHERE id = :id
    """), {
        "id": str(incident_id),
        "now": datetime.now(timezone.utc),
        "staff_id": str(staff.id),
        "notes": resolution_notes,
    })

    await _broadcast_security_alert(
        pharmacy_id=str(staff.pharmacy_id),
        event_data={
            "type": "duress_resolved",
            "incident_id": str(incident_id),
            "resolved_by": f"{staff.first_name} {staff.last_name}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
    return {"status": "resolved", "incident_id": str(incident_id)}


# ── Security event query ───────────────────────────────────────────────────

@router.get("/events")
async def get_security_events(
    resolved: Optional[bool] = None,
    severity: Optional[str] = None,
    limit: int = 50,
    staff: Staff = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """Get security events for this pharmacy, newest first."""
    stmt = (
        select(SecurityEvent)
        .where(SecurityEvent.pharmacy_id == staff.pharmacy_id)
        .order_by(SecurityEvent.detected_at.desc())
        .limit(limit)
    )
    if resolved is not None:
        stmt = stmt.where(SecurityEvent.resolved == resolved)
    if severity:
        stmt = stmt.where(SecurityEvent.severity == severity)

    result = await db.execute(stmt)
    events = result.scalars().all()

    return [
        {
            "event_id": str(e.id),
            "event_type": e.event_type,
            "severity": e.severity,
            "description": e.description,
            "detected_at": e.detected_at.isoformat(),
            "resolved": e.resolved,
            "camera_footage_retained": e.camera_footage_retained,
            "metadata": e.event_metadata,
        }
        for e in events
    ]


@router.get("/summary")
async def get_security_summary(
    staff: Staff = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """Real-time security dashboard summary for the pharmacist workstation."""
    result = await db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE resolved = false AND severity = 'critical') AS critical_unresolved,
            COUNT(*) FILTER (WHERE resolved = false AND severity = 'high')     AS high_unresolved,
            COUNT(*) FILTER (WHERE resolved = false AND severity = 'warning')  AS warnings,
            COUNT(*) FILTER (WHERE detected_at >= NOW() - INTERVAL '24 hours') AS last_24h,
            COUNT(*) FILTER (WHERE event_type = 'duress_incident'
                AND detected_at >= NOW() - INTERVAL '30 days')                AS duress_incidents_30d
        FROM security_events
        WHERE pharmacy_id = :pharmacy_id
    """), {"pharmacy_id": str(staff.pharmacy_id)})

    row = result.mappings().one_or_none()
    return {
        "critical_unresolved":   int(row["critical_unresolved"] or 0),
        "high_unresolved":       int(row["high_unresolved"] or 0),
        "warnings_unresolved":   int(row["warnings"] or 0),
        "events_last_24h":       int(row["last_24h"] or 0),
        "duress_incidents_30d":  int(row["duress_incidents_30d"] or 0),
        "status": "alert" if (row["critical_unresolved"] or 0) > 0 else "normal",
    }


# ── Rx-shopping detection ──────────────────────────────────────────────────

@router.post("/rx-shopping/analyze")
async def analyze_rx_shopping_risk(
    patient_id: UUID,
    prescription_id: Optional[UUID] = None,
    biometric_identity_id: Optional[UUID] = None,
    staff: Staff = Depends(require_permission("rx:read")),
    db: AsyncSession = Depends(get_db),
):
    """Run Rx-shopping risk analysis before dispensing a controlled substance."""
    from services.biometric.behavioral_analysis.rx_shopping_detector import RxShoppingDetector
    detector = RxShoppingDetector(db=db)
    profile = await detector.analyze_patient(
        patient_id=patient_id,
        biometric_identity_id=biometric_identity_id,
        pharmacy_id=staff.pharmacy_id,
    )
    return {
        "patient_id": str(patient_id),
        "risk_score": profile.risk_score,
        "risk_level": profile.risk_level,
        "flags": [
            {
                "type": f.flag_type,
                "severity": f.severity,
                "description": f.description,
            }
            for f in profile.flags
        ],
        "counseling_recommended": profile.counseling_recommended,
        "prescriber_contact_recommended": profile.prescriber_contact_recommended,
        "pdmp_pharmacy_count_30d": profile.pdmp_pharmacy_count_30d,
        "biometric_pharmacy_visits_24h": profile.biometric_pharmacy_visits_24h,
    }


# ── WebSocket — real-time security alerts ─────────────────────────────────

@router.websocket("/stream/{pharmacy_id}")
async def security_event_stream(
    websocket: WebSocket,
    pharmacy_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    WebSocket endpoint for real-time security event streaming.
    Pharmacist workstation connects here to receive instant alerts.
    """
    await websocket.accept()
    pid = str(pharmacy_id)
    if pid not in _security_ws_connections:
        _security_ws_connections[pid] = []
    _security_ws_connections[pid].append(websocket)
    logger.info("Security WebSocket connected: pharmacy=%s", pid[:8])

    try:
        # Send current unresolved summary on connect
        result = await db.execute(text("""
            SELECT COUNT(*) FILTER (WHERE severity IN ('critical','high') AND resolved = false) AS active_alerts
            FROM security_events WHERE pharmacy_id = :pid
        """), {"pid": pid})
        row = result.one_or_none()
        await websocket.send_json({
            "type": "connection_established",
            "active_alerts": int(row[0]) if row else 0,
        })

        # Keep alive, waiting for broadcast messages
        while True:
            await websocket.receive_text()  # Ping/keep-alive

    except WebSocketDisconnect:
        if pid in _security_ws_connections:
            _security_ws_connections[pid] = [
                ws for ws in _security_ws_connections[pid] if ws != websocket
            ]
        logger.info("Security WebSocket disconnected: pharmacy=%s", pid[:8])


async def _broadcast_security_alert(pharmacy_id: str, event_data: dict) -> None:
    """Push a security event to all connected pharmacist workstations."""
    connections = _security_ws_connections.get(pharmacy_id, [])
    dead = []
    for ws in connections:
        try:
            await ws.send_json(event_data)
        except Exception:
            dead.append(ws)
    # Clean up dead connections
    if dead and pharmacy_id in _security_ws_connections:
        _security_ws_connections[pharmacy_id] = [
            ws for ws in connections if ws not in dead
        ]
