"""
Biometric identification API endpoints.
Handles face enrollment, real-time identification frames,
and patient profile pre-loading on detection.
"""
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, WebSocket
from fastapi import WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.database import get_db
from services.biometric.identity_resolution.engine import IdentityResolutionEngine

logger = logging.getLogger(__name__)
router = APIRouter()

# Singleton engine — shared across requests
_resolution_engine = IdentityResolutionEngine()


class IdentificationResponse(BaseModel):
    identity_id: Optional[UUID]
    # similarity is the raw cosine; confidence is the calibrated probability that
    # no enrolled impostor would score this high. They are NOT interchangeable —
    # conflating them is what let a cosine be read as a percentage.
    similarity: float
    confidence: float
    margin: float
    threshold_used: Optional[float] = None
    expected_false_matches: Optional[float] = None
    requires_review: bool = True
    explanation: str = ""
    match_level: str
    identity_class: str
    patient_id: Optional[UUID] = None
    staff_id: Optional[UUID] = None
    is_new_identity: bool = False
    patient_profile_preloaded: bool = False
    clinical_prescan_available: bool = False
    pending_rxs: list[dict] = []
    active_alerts: list[dict] = []


@router.post("/identify", response_model=IdentificationResponse)
async def identify_individual(
    face_image: UploadFile = File(...),
    pharmacy_id: UUID = None,
    has_depth_map: bool = False,
    depth_map: Optional[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Identify an individual from a face image captured at pharmacy entry.
    Returns identity match and pre-loads patient profile if matched.
    Called by edge AI node on each detection event.
    """
    import numpy as np
    import cv2

    image_bytes = await face_image.read()
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    if img is None:
        raise HTTPException(status_code=400, detail="Invalid image data")

    depth_arr = None
    if has_depth_map and depth_map:
        depth_bytes = await depth_map.read()
        depth_nparr = np.frombuffer(depth_bytes, np.uint16)
        depth_arr = depth_nparr.reshape((img.shape[0], img.shape[1]))

    match = _resolution_engine.identify(img, depth_arr)

    response = IdentificationResponse(
        identity_id=match.identity_id,
        similarity=match.similarity,
        confidence=match.confidence,
        margin=match.margin,
        threshold_used=match.threshold_used,
        expected_false_matches=match.expected_false_matches,
        requires_review=match.requires_review,
        explanation=match.explanation,
        match_level=match.match_level,
        identity_class=match.identity_class,
        patient_id=match.patient_id,
        is_new_identity=match.is_new_identity,
    )

    # Pre-loading a profile is a convenience, not an identity assertion: it puts
    # a candidate on screen for the pharmacist to accept or reject. Only an
    # auto-level match pre-loads; anything under review stays unopened.
    if match.match_level == "auto" and match.patient_id:
        try:
            profile_data = await _preload_patient_profile(match.patient_id, db)
            response.patient_profile_preloaded = True
            response.pending_rxs = profile_data.get("pending_rxs", [])
            response.active_alerts = profile_data.get("active_alerts", [])
            response.clinical_prescan_available = bool(profile_data.get("acb_prescan"))
        except Exception as exc:
            logger.error("Failed to pre-load patient profile: %s", exc)

    return response


@router.post("/enroll")
async def enroll_individual(
    identity_id: UUID,
    face_images: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Enroll a new individual's face into the biometric index.
    Requires minimum 3 face images from different angles.
    """
    import numpy as np
    import cv2

    if len(face_images) < 3:
        raise HTTPException(
            status_code=400,
            detail="Minimum 3 face images required for enrollment",
        )

    vector_ids: list[int] = []
    for img_file in face_images:
        img_bytes = await img_file.read()
        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is not None:
            vid = _resolution_engine.enroll(identity_id, img)
            if vid is not None:
                vector_ids.append(vid)

    if not vector_ids:
        raise HTTPException(status_code=422, detail="No valid face images could be enrolled")

    # The caller must persist these against the identity row: the gallery's
    # in-memory map is a cache, and the database is what lets it be rebuilt
    # after a restart. Returning them is the seam; the write path is still to
    # be built (see the identity-lifecycle design).
    return {
        "identity_id": identity_id,
        "enrolled_images": len(vector_ids),
        "vector_ids": vector_ids,
        "status": "enrolled",
    }


@router.websocket("/stream/{pharmacy_id}/{zone}")
async def biometric_stream(
    websocket: WebSocket,
    pharmacy_id: UUID,
    zone: str,
    db: AsyncSession = Depends(get_db),
):
    """
    WebSocket endpoint for real-time biometric event streaming to pharmacist UI.
    Edge node sends detection events; we resolve and push profile data back.
    """
    await websocket.accept()
    logger.info("Biometric stream connected: pharmacy=%s zone=%s", pharmacy_id, zone)

    try:
        while True:
            data = await websocket.receive_json()
            event_type = data.get("event_type")

            if event_type == "face_detected":
                # Edge has already done primary matching — send enriched response
                identity_id = data.get("identity_id")
                confidence = data.get("confidence", 0.0)
                patient_id = data.get("patient_id")

                enriched = {"event_type": "identity_resolved", "confidence": confidence}

                if patient_id and confidence >= 0.95:
                    profile = await _preload_patient_profile(UUID(patient_id), db)
                    enriched.update(profile)

                await websocket.send_json(enriched)

            elif event_type == "security_event":
                # Security alert — forward to security monitoring
                logger.warning(
                    "Security event from pharmacy %s zone %s: %s",
                    pharmacy_id, zone, data.get("description"),
                )
                await websocket.send_json({"event_type": "security_ack", "received": True})

    except WebSocketDisconnect:
        logger.info("Biometric stream disconnected: pharmacy=%s", pharmacy_id)


async def _preload_patient_profile(patient_id: UUID, db: AsyncSession) -> dict:
    """
    Query pending Rxs, active DUR alerts, refill dues, and ACB pre-scan
    for display at pharmacist counter before patient reaches the window.
    """
    from sqlalchemy import select
    from shared.models.prescription import Prescription, RxStatus

    pending_rxs_result = await db.execute(
        select(Prescription).where(
            Prescription.patient_id == patient_id,
            Prescription.status.in_([
                RxStatus.WILL_CALL,
                RxStatus.FILLED,
                RxStatus.ADJUDICATION_REJECTED,
                RxStatus.PENDING_PA,
            ]),
        )
    )
    pending_rxs = pending_rxs_result.scalars().all()

    return {
        "patient_id": str(patient_id),
        "pending_rxs": [
            {
                "rx_number": rx.rx_number,
                "drug_name": rx.drug_name,
                "status": rx.status,
                "copay": None,   # Populated from adjudication cache
                "alerts": rx.acb_safety_report.get("critical_alerts", []) if rx.acb_safety_report else [],
            }
            for rx in pending_rxs
        ],
        "active_alerts": [],     # Populated by clinical brain pre-scan
        "acb_prescan": None,     # Populated by clinical brain service
    }
