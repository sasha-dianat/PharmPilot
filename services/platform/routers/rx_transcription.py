"""
Phase 32+ — Rx Transcription Router
=====================================
Transcribes scanned prescription documents and enriches the patient/prescriber
database with extracted information.

Flow
----
  1. POST /rx-transcription/transcribe
     ├── Reads the stored image file from rx_documents
     ├── Runs PrescriptionOCR
     └── Returns TranscribedRx as JSON + DB-matched patient/prescriber candidates

  2. POST /rx-transcription/confirm
     ├── Staff has reviewed/corrected the extracted fields
     ├── Upserts prescriber record (name + medical council number)
     ├── Runs PatientNameMatcher → may create a person_link
     └── Saves the full transcription to rx_transcription_events

  3. GET /rx-transcription/events?rx_id=…  — audit trail of all transcriptions
  4. GET /rx-transcription/prescribers      — search known prescribers (for auto-fill)
"""
from __future__ import annotations

import base64
import uuid
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.database import get_db
from services.platform.auth import get_current_user
from services.ai.rx_transcription import (
    PrescriptionOCR,
    TranscribedRx,
    PatientNameMatcher,
)

logger  = logging.getLogger(__name__)
router  = APIRouter(tags=["rx transcription"])
_ocr    = PrescriptionOCR(multi_image_strategy="concat")

# ─── Pydantic schemas ──────────────────────────────────────────────────────────

class TranscribeRequest(BaseModel):
    """Transcribe from a saved rx_documents record."""
    doc_id:             str
    visitor_patient_id: Optional[str] = None  # person who brought the Rx

class TranscribeBase64Request(BaseModel):
    """Transcribe directly from base64 images (webcam captures not yet saved)."""
    images_base64:      List[str]
    visitor_patient_id: Optional[str] = None
    pharmacy_id:        Optional[str] = None

class MedicationModel(BaseModel):
    drug_name:   str
    strength:    Optional[str]  = None
    dosage_form: Optional[str]  = None
    sig:         Optional[str]  = None
    quantity:    Optional[str]  = None
    refills:     Optional[int]  = None
    confidence:  float           = 0.0

class PrescriberModel(BaseModel):
    full_name:          str
    suffix:             Optional[str] = None
    medical_council_no: Optional[str] = None
    council_authority:  Optional[str] = None
    confidence:         float          = 0.0

class PatientCandidateModel(BaseModel):
    patient_id:     str
    full_name:      str
    national_id:    Optional[str]
    similarity:     float
    classification: str   # same | likely | possible | different

class TranscribeResponse(BaseModel):
    doc_id:               Optional[str]
    patient_name:         Optional[str]
    patient_name_conf:    float
    patient_match:        Optional[str]            # classification vs visitor
    patient_similarity:   Optional[float]
    patient_candidates:   List[PatientCandidateModel]
    prescriber:           Optional[PrescriberModel]
    medications:          List[MedicationModel]
    rx_date:              Optional[str]
    rx_number:            Optional[str]
    overall_confidence:   float
    raw_text:             str

class ConfirmRequest(BaseModel):
    doc_id:             Optional[str]   = None
    rx_id:              Optional[str]   = None
    patient_name:       Optional[str]   = None
    rx_patient_id:      Optional[str]   = None       # DB patient the Rx belongs to
    visitor_patient_id: Optional[str]   = None       # DB patient who brought Rx
    relationship_hint:  Optional[str]   = None       # optional: parent/child/spouse/caregiver
    prescriber_full_name:         Optional[str] = None
    prescriber_suffix:            Optional[str] = None
    prescriber_medical_council_no:Optional[str] = None
    prescriber_council_authority: Optional[str] = None
    medications:        List[MedicationModel] = []
    rx_date:            Optional[str]   = None
    pharmacy_id:        Optional[str]   = None
    confirmed_by:       str             = "staff"

class ConfirmResponse(BaseModel):
    transcription_id:  str
    prescriber_id:     Optional[str]
    link_created:      bool
    link_relationship: Optional[str]
    message:           str

# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _ensure_tables(db: AsyncSession) -> None:
    # Transcription audit table
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS rx_transcription_events (
            id                      TEXT PRIMARY KEY,
            doc_id                  TEXT,
            rx_id                   TEXT,
            patient_name_extracted  TEXT,
            patient_name_conf       FLOAT DEFAULT 0,
            rx_patient_id           TEXT,
            visitor_patient_id      TEXT,
            link_created            BOOLEAN DEFAULT FALSE,
            link_relationship       TEXT,
            prescriber_name         TEXT,
            prescriber_suffix       TEXT,
            medical_council_no      TEXT,
            council_authority       TEXT,
            medications_json        TEXT,
            rx_date                 TEXT,
            rx_number               TEXT,
            raw_text                TEXT,
            overall_confidence      FLOAT DEFAULT 0,
            confirmed_by            TEXT,
            created_at              TIMESTAMPTZ DEFAULT now()
        )
    """))
    # Prescriber medical registrations (one prescriber may hold several council nos.)
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS prescriber_medical_registrations (
            id               TEXT PRIMARY KEY,
            prescriber_id    TEXT,
            full_name        TEXT NOT NULL,
            suffix           TEXT,
            council_no       TEXT NOT NULL,
            council_authority TEXT,
            verified         BOOLEAN DEFAULT FALSE,
            source           TEXT DEFAULT 'rx_scan',
            created_at       TIMESTAMPTZ DEFAULT now(),
            UNIQUE (full_name, council_no)
        )
    """))
    await db.commit()


async def _get_doc_path(db: AsyncSession, doc_id: str) -> str:
    result = await db.execute(text("""
        SELECT file_path FROM rx_documents WHERE id = :id AND deleted = FALSE
    """), {"id": doc_id})
    row = result.mappings().first()
    if not row:
        raise HTTPException(404, f"Document {doc_id} not found")
    return row["file_path"]


async def _upsert_prescriber_registration(
    db:        AsyncSession,
    full_name: str,
    suffix:    Optional[str],
    council_no: str,
    authority:  Optional[str],
) -> Optional[str]:
    """
    Insert or update prescriber_medical_registrations.
    Also attempts to match / upsert into the prescribers table.
    Returns the prescribers.id if found/created, else None.
    """
    reg_id = str(uuid.uuid4())
    await db.execute(text("""
        INSERT INTO prescriber_medical_registrations
            (id, full_name, suffix, council_no, council_authority, source)
        VALUES (:id, :name, :suffix, :cno, :auth, 'rx_scan')
        ON CONFLICT (full_name, council_no) DO UPDATE
            SET council_authority = COALESCE(EXCLUDED.council_authority, prescriber_medical_registrations.council_authority),
                suffix            = COALESCE(EXCLUDED.suffix,            prescriber_medical_registrations.suffix)
    """), dict(id=reg_id, name=full_name, suffix=suffix, cno=council_no, auth=authority))

    # Try to match existing prescriber by last_name (rough match)
    parts     = full_name.strip().split()
    last_name = parts[-1] if parts else full_name
    res       = await db.execute(text("""
        SELECT id FROM prescribers
        WHERE  LOWER(last_name) = LOWER(:ln)
        LIMIT  1
    """), {"ln": last_name})
    row = res.mappings().first()

    if row:
        # Upsert council number into prescriber metadata
        prescriber_id = str(row["id"])
        await db.execute(text("""
            UPDATE prescribers
            SET    metadata_ = jsonb_set(
                       COALESCE(metadata_, '{}'),
                       '{medical_council_no}',
                       to_jsonb(:cno::text)
                   )
            WHERE  id = :pid
        """), {"cno": council_no, "pid": prescriber_id})
        return prescriber_id

    return None


def _build_response(
    result:      TranscribedRx,
    doc_id:      Optional[str],
    match_result = None,
) -> TranscribeResponse:
    prescriber_model = None
    if result.prescriber:
        p = result.prescriber
        prescriber_model = PrescriberModel(
            full_name=p.full_name, suffix=p.suffix,
            medical_council_no=p.medical_council_no,
            council_authority=p.council_authority,
            confidence=p.confidence,
        )

    medications = [
        MedicationModel(
            drug_name=m.drug_name, strength=m.strength,
            dosage_form=m.dosage_form, sig=m.sig,
            quantity=m.quantity, refills=m.refills,
            confidence=m.confidence,
        )
        for m in result.medications
    ]

    candidates = []
    if match_result:
        for c in match_result.db_candidates:
            candidates.append(PatientCandidateModel(
                patient_id=c.patient_id, full_name=c.full_name,
                national_id=c.national_id,
                similarity=c.similarity, classification=c.classification,
            ))

    return TranscribeResponse(
        doc_id=doc_id,
        patient_name=result.patient_name,
        patient_name_conf=result.patient_name_conf,
        patient_match=match_result.classification if match_result else None,
        patient_similarity=match_result.similarity if match_result else None,
        patient_candidates=candidates,
        prescriber=prescriber_model,
        medications=medications,
        rx_date=result.rx_date,
        rx_number=result.rx_number,
        overall_confidence=result.overall_confidence,
        raw_text=result.raw_text,
    )

# ─── Routes ───────────────────────────────────────────────────────────────────

@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe_document(
    body:     TranscribeRequest,
    db:       AsyncSession = Depends(get_db),
    current:  dict         = Depends(get_current_user),
):
    """
    OCR-transcribe a saved rx_document.
    Optionally compares the extracted patient name with the visitor's
    identity to flag if someone else is picking up.
    """
    await _ensure_tables(db)

    file_path = await _get_doc_path(db, body.doc_id)
    path_obj  = Path(file_path)
    if not path_obj.exists():
        raise HTTPException(404, "Image file not found on disk")

    # Load image
    try:
        import cv2
        img = cv2.imread(str(path_obj))
        if img is None:
            # Might be PDF — try converting first page
            raise ValueError("cv2.imread returned None — possibly a PDF")
    except Exception:
        # For PDFs: basic fallback — return empty transcription rather than crashing
        return TranscribeResponse(
            doc_id=body.doc_id, patient_name=None, patient_name_conf=0.0,
            patient_match=None, patient_similarity=None, patient_candidates=[],
            prescriber=None, medications=[], rx_date=None, rx_number=None,
            overall_confidence=0.0,
            raw_text="[PDF transcription not available in this build — use image scan]",
        )

    result = _ocr.extract([img])

    # Name matching (if visitor known)
    match_result = None
    if result.patient_name:
        pharmacy_id = current.get("pharmacy_id") or ""
        if pharmacy_id:
            matcher      = PatientNameMatcher(db)
            match_result = await matcher.match_and_enrich(
                extracted_rx_patient_name = result.patient_name,
                pharmacy_id               = pharmacy_id,
                visitor_patient_id        = body.visitor_patient_id,
            )

    return _build_response(result, body.doc_id, match_result)


@router.post("/transcribe-base64", response_model=TranscribeResponse)
async def transcribe_base64(
    body:    TranscribeBase64Request,
    db:      AsyncSession = Depends(get_db),
    current: dict         = Depends(get_current_user),
):
    """Transcribe one or more webcam frames sent as base64 (not yet stored as documents)."""
    await _ensure_tables(db)

    import cv2
    import numpy as np

    images = []
    for b64 in body.images_base64:
        raw = b64.split(",", 1)[-1]   # strip data-URL prefix if present
        try:
            data     = base64.b64decode(raw)
            arr      = np.frombuffer(data, np.uint8)
            img      = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is not None:
                images.append(img)
        except Exception:
            continue

    if not images:
        raise HTTPException(400, "No valid images provided")

    result = _ocr.extract(images)

    match_result = None
    if result.patient_name:
        pharmacy_id = body.pharmacy_id or current.get("pharmacy_id") or ""
        if pharmacy_id:
            matcher      = PatientNameMatcher(db)
            match_result = await matcher.match_and_enrich(
                extracted_rx_patient_name = result.patient_name,
                pharmacy_id               = pharmacy_id,
                visitor_patient_id        = body.visitor_patient_id,
            )

    return _build_response(result, None, match_result)


@router.post("/confirm", response_model=ConfirmResponse, status_code=201)
async def confirm_transcription(
    body:    ConfirmRequest,
    db:      AsyncSession = Depends(get_db),
    current: dict         = Depends(get_current_user),
):
    """
    Staff has reviewed the extracted data and clicks Confirm.
    1. Upserts prescriber + medical council number
    2. Creates person_link if visitor ≠ Rx patient
    3. Records the full transcription event for audit
    """
    await _ensure_tables(db)

    pharmacy_id   = body.pharmacy_id or current.get("pharmacy_id") or ""
    prescriber_id: Optional[str] = None

    # ── Prescriber upsert ──
    if body.prescriber_full_name and body.prescriber_medical_council_no:
        try:
            prescriber_id = await _upsert_prescriber_registration(
                db,
                full_name  = body.prescriber_full_name,
                suffix     = body.prescriber_suffix,
                council_no = body.prescriber_medical_council_no,
                authority  = body.prescriber_council_authority,
            )
        except Exception as e:
            logger.warning("[RxTranscription] Prescriber upsert failed: %s", e)

    # ── Patient name matching + link ──
    link_created      = False
    link_relationship = body.relationship_hint

    if body.patient_name and pharmacy_id:
        matcher = PatientNameMatcher(db)
        mr      = await matcher.match_and_enrich(
            extracted_rx_patient_name = body.patient_name,
            pharmacy_id               = pharmacy_id,
            visitor_patient_id        = body.visitor_patient_id,
            rx_patient_id             = body.rx_patient_id,
            relationship_hint         = body.relationship_hint,
        )
        link_created      = mr.link_created
        link_relationship = mr.link_relationship

    # ── Persist transcription event ──
    import json as _json
    event_id = str(uuid.uuid4())
    now      = datetime.now(timezone.utc)

    await db.execute(text("""
        INSERT INTO rx_transcription_events
            (id, doc_id, rx_id, patient_name_extracted, patient_name_conf,
             rx_patient_id, visitor_patient_id, link_created, link_relationship,
             prescriber_name, prescriber_suffix, medical_council_no, council_authority,
             medications_json, rx_date, rx_number, confirmed_by, created_at)
        VALUES
            (:id, :doc_id, :rx_id, :pname, 0,
             :rxpid, :vispid, :lc, :lrel,
             :pname2, :psuffix, :cno, :cauth,
             :meds, :rxdate, :rxnum, :by, :now)
    """), dict(
        id=event_id,
        doc_id=body.doc_id,
        rx_id=body.rx_id,
        pname=body.patient_name,
        rxpid=body.rx_patient_id,
        vispid=body.visitor_patient_id,
        lc=link_created,
        lrel=link_relationship,
        pname2=body.prescriber_full_name,
        psuffix=body.prescriber_suffix,
        cno=body.prescriber_medical_council_no,
        cauth=body.prescriber_council_authority,
        meds=_json.dumps([m.dict() for m in body.medications]),
        rxdate=body.rx_date,
        rxnum=None,
        by=body.confirmed_by or current.get("sub", "staff"),
        now=now,
    ))
    await db.commit()

    msg_parts = ["Transcription confirmed."]
    if prescriber_id:
        msg_parts.append(f"Prescriber record updated (ID {prescriber_id}).")
    elif body.prescriber_medical_council_no:
        msg_parts.append("Council number saved to prescriber registrations.")
    if link_created:
        msg_parts.append("Visitor linked to Rx patient in relationship tree.")

    return ConfirmResponse(
        transcription_id  = event_id,
        prescriber_id     = prescriber_id,
        link_created      = link_created,
        link_relationship = link_relationship,
        message           = " ".join(msg_parts),
    )


@router.get("/events")
async def list_events(
    rx_id:    Optional[str] = Query(None),
    doc_id:   Optional[str] = Query(None),
    limit:    int            = Query(20, ge=1, le=100),
    db:       AsyncSession   = Depends(get_db),
    _current: dict           = Depends(get_current_user),
):
    await _ensure_tables(db)

    filters, params = ["1=1"], {}
    if rx_id:  filters.append("rx_id = :rx_id");   params["rx_id"]  = rx_id
    if doc_id: filters.append("doc_id = :doc_id"); params["doc_id"] = doc_id
    params["limit"] = limit

    rows = await db.execute(text(f"""
        SELECT * FROM rx_transcription_events
        WHERE  {" AND ".join(filters)}
        ORDER  BY created_at DESC
        LIMIT  :limit
    """), params)

    return {"events": [dict(r) for r in rows.mappings()]}


@router.get("/prescribers/search")
async def search_prescribers(
    q:        str          = Query(..., min_length=2),
    limit:    int          = Query(10, ge=1, le=50),
    db:       AsyncSession = Depends(get_db),
    _current: dict         = Depends(get_current_user),
):
    """
    Search known prescribers by name — used for auto-fill in the frontend.
    Combines prescribers table + prescriber_medical_registrations.
    """
    await _ensure_tables(db)

    # From official prescribers table
    res = await db.execute(text("""
        SELECT p.id, p.first_name || ' ' || p.last_name AS full_name,
               p.suffix, p.specialty,
               p.metadata_->>'medical_council_no' AS council_no,
               'prescribers' AS source
        FROM   prescribers p
        WHERE  LOWER(p.first_name || ' ' || p.last_name) LIKE '%' || LOWER(:q) || '%'
        LIMIT  :lim
    """), {"q": q, "lim": limit})

    prescribers = [dict(r) for r in res.mappings()]

    # From scan-sourced registrations (not yet matched to a prescriber record)
    res2 = await db.execute(text("""
        SELECT NULL::text AS id, full_name, suffix, NULL AS specialty,
               council_no, council_authority AS source
        FROM   prescriber_medical_registrations
        WHERE  LOWER(full_name) LIKE '%' || LOWER(:q) || '%'
        LIMIT  :lim
    """), {"q": q, "lim": limit})

    scanned = [dict(r) for r in res2.mappings()]
    return {"results": prescribers + scanned}
