"""
Audio transcription API — upload completed recordings or stream live audio.
Returns transcripts, speaker diarization, and clinical extraction results.
"""
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, WebSocket
from fastapi import WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.database import get_db
from services.platform.auth import require_pharmacist, require_permission
from shared.models.auth import Staff

logger = logging.getLogger(__name__)
router = APIRouter()


class TranscriptResponse(BaseModel):
    transcript_id: UUID
    full_text: str
    duration_seconds: float
    speaker_count: int
    transcription_confidence: float
    extracted_clinical_items: int
    action_items: list[dict]
    urgency_flag: bool
    urgency_reason: Optional[str] = None
    status: str


@router.post("/transcribe", response_model=TranscriptResponse)
async def transcribe_recording(
    audio_file: UploadFile = File(...),
    pharmacy_id: UUID = Form(...),
    zone: str = Form(...),
    visit_id: Optional[UUID] = Form(None),
    patient_id: Optional[UUID] = Form(None),
    staff: Staff = Depends(require_permission("clinical:write")),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a completed audio recording for full pipeline processing.
    Used for counseling room recordings processed after session ends.
    """
    from uuid import uuid4
    import numpy as np
    import soundfile as sf
    import io

    audio_bytes = await audio_file.read()

    try:
        audio_data, sample_rate = sf.read(io.BytesIO(audio_bytes), dtype="float32")
        if len(audio_data.shape) > 1:
            audio_data = np.mean(audio_data, axis=1)  # Mix to mono
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid audio file: {exc}")

    from services.audio.noise_cancellation.processor import PharmacyNoiseCanceller
    from services.audio.speaker_diarization.diarizer import PharmacySpeakerDiarizer
    from services.audio.transcription.engine import PharmacyTranscriptionEngine
    from services.audio.profile_enrichment.extractor import ClinicalInformationExtractor

    transcript_id = uuid4()

    # Process pipeline
    canceller = PharmacyNoiseCanceller()
    audio_bytes_int16 = (audio_data * 32768).astype("int16").tobytes()
    cleaned_bytes = canceller.process_stream(audio_bytes_int16, sample_rate)

    if not cleaned_bytes:
        raise HTTPException(status_code=422, detail="No speech detected in audio")

    cleaned_audio = np.frombuffer(cleaned_bytes, dtype="int16").astype("float32") / 32768.0

    diarizer = PharmacySpeakerDiarizer()
    diarized = diarizer.diarize(cleaned_audio, sample_rate, zone)

    # Default (large-v3, Persian). A hard-coded `.en` model here would emit
    # invented English for Persian speech rather than a worse transcript.
    transcriber = PharmacyTranscriptionEngine()
    transcription = transcriber.transcribe_batch(cleaned_audio, sample_rate)

    # Align transcription with diarization
    diarized_segments = []
    for t_seg in transcription.segments:
        best_speaker = "SPEAKER_00"
        best_overlap = 0.0
        for d_seg in diarized.segments:
            overlap = max(0.0, min(t_seg.end, d_seg.end_seconds) - max(t_seg.start, d_seg.start_seconds))
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = d_seg.speaker_label
        diarized_segments.append({
            "speaker_label": best_speaker,
            "role": next((d.speaker_role for d in diarized.segments if d.speaker_label == best_speaker), "unknown"),
            "text": t_seg.text,
            "start": t_seg.start,
            "end": t_seg.end,
        })

    extractor = ClinicalInformationExtractor()
    extraction = extractor.extract(
        full_transcript=transcription.full_text,
        diarized_segments=diarized_segments,
        patient_id=patient_id,
        transcript_id=transcript_id,
    )

    # Persist to DB
    from shared.models.audio import AudioTranscript, TranscriptStatus
    from datetime import datetime, timezone

    transcript = AudioTranscript(
        id=transcript_id,
        pharmacy_id=pharmacy_id,
        visit_id=visit_id,
        patient_id=patient_id,
        audio_zone=zone,
        recording_started_at=datetime.now(timezone.utc),
        duration_seconds=len(audio_data) / sample_rate,
        noise_cancellation_applied=True,
        whisper_model_used=transcriber.model_size,
        transcription_confidence=transcription.transcription_confidence,
        full_transcript=transcription.full_text,
        diarized_segments=diarized_segments,
        identified_speakers=diarized.speaker_map,
        status=TranscriptStatus.ENRICHED,
        extracted_clinical_info={
            "items": [
                {
                    "category": item.category,
                    "extracted_text": item.extracted_text,
                    "structured_value": item.structured_value,
                    "confidence": item.confidence,
                }
                for item in extraction.extracted_items
            ]
        },
        clinical_action_items=extraction.action_items,
        sentiment_score=extraction.sentiment,
    )

    db.add(transcript)
    await db.flush()

    return TranscriptResponse(
        transcript_id=transcript_id,
        full_text=transcription.full_text,
        duration_seconds=len(audio_data) / sample_rate,
        speaker_count=diarized.total_speakers,
        transcription_confidence=transcription.transcription_confidence,
        extracted_clinical_items=len(extraction.extracted_items),
        action_items=extraction.action_items,
        urgency_flag=extraction.urgency_flag,
        urgency_reason=extraction.urgency_reason,
        status="enriched",
    )


@router.post("/dictate")
async def dictate_note(
    audio_file: UploadFile = File(...),
    context: str = Form("note"),   # note | dur_override | counseling
    language: str = Form("fa"),    # fa (Persian) | en
    staff: Staff = Depends(require_permission("rx:write")),
):
    """
    Fast dictation endpoint for pharmacist voice notes.
    No diarization, no NLP extraction — returns raw transcript text in ~2–5 s
    so the pharmacist can review and confirm before committing to the record.

    context: 'note' | 'dur_override' | 'counseling' — informs the UI how to
             route the confirmed text (attach to Rx, DUR override reason, etc.)
    language: 'fa' for Persian (uses Whisper multilingual), 'en' for English.
    """
    import io
    import tempfile
    import os as _os

    audio_bytes = await audio_file.read()
    if not audio_bytes:
        raise HTTPException(400, "Empty audio file")

    # Save to temp file for Whisper (Whisper reads from path)
    suffix = ".webm" if "webm" in (audio_file.content_type or "") else ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        try:
            import whisper
            # Use small multilingual for Persian, base.en for English speed
            model_name = "small" if language == "fa" else "base.en"
            model = whisper.load_model(model_name)
            result = model.transcribe(tmp_path, language=language if language == "fa" else None)
            text = result.get("text", "").strip()
        except ImportError:
            # Whisper not installed locally — return a clear error so UI shows fallback
            raise HTTPException(503, "Whisper not installed on this server; install openai-whisper")
        except Exception as exc:
            raise HTTPException(422, f"Transcription failed: {exc}")

        if not text:
            return {"status": "empty", "text": "", "context": context}

        return {
            "status": "ok",
            "text": text,
            "language": language,
            "context": context,
            "word_count": len(text.split()),
            "requires_confirmation": True,
            "disclaimer": "For pharmacist review before saving — AI transcription may contain errors.",
        }
    finally:
        _os.unlink(tmp_path)


@router.get("/transcripts/{patient_id}")
async def get_patient_transcripts(
    patient_id: UUID,
    limit: int = 20,
    offset: int = 0,
    staff: Staff = Depends(require_permission("clinical:read")),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select, desc
    from shared.models.audio import AudioTranscript

    # Scoped to the caller's pharmacy: authentication alone would still let a
    # user at one pharmacy read another's conversations by guessing a patient id.
    result = await db.execute(
        select(AudioTranscript)
        .where(AudioTranscript.patient_id == patient_id,
               AudioTranscript.pharmacy_id == staff.pharmacy_id)
        .order_by(desc(AudioTranscript.recording_started_at))
        .limit(limit)
        .offset(offset)
    )
    transcripts = result.scalars().all()

    return [
        {
            "transcript_id": str(t.id),
            "zone": t.audio_zone,
            "recorded_at": t.recording_started_at.isoformat(),
            "duration_seconds": t.duration_seconds,
            "full_text": t.full_transcript,
            "action_items": t.clinical_action_items,
            "status": t.status,
        }
        for t in transcripts
    ]


@router.post("/transcripts/{transcript_id}/approve-enrichment/{action_id}")
async def approve_enrichment_action(
    transcript_id: UUID,
    action_id: UUID,
    staff: Staff = Depends(require_pharmacist()),
    db: AsyncSession = Depends(get_db),
):
    """
    Pharmacist approves an AI-extracted clinical item from a transcript,
    committing it to the patient's permanent profile.
    """
    from datetime import datetime, timezone
    from sqlalchemy import select
    from shared.models.audio import ProfileEnrichmentAction

    result = await db.execute(
        select(ProfileEnrichmentAction).where(ProfileEnrichmentAction.id == action_id)
    )
    action = result.scalar_one_or_none()
    if not action:
        raise HTTPException(status_code=404, detail="Enrichment action not found")

    action.status = "approved"
    # The approver IS the audit record for this clinical write, so it is taken
    # from the verified session — never from a caller-supplied field.
    action.reviewed_by_id = staff.id
    action.reviewed_at = datetime.now(timezone.utc)

    # Apply the enrichment to the patient record
    await _apply_enrichment_to_patient(action, db)

    return {"status": "approved", "action_id": str(action_id),
            "approved_by": str(staff.id)}


async def _apply_enrichment_to_patient(action, db: AsyncSession) -> None:
    """Write approved AI-extracted clinical data to the patient record."""
    from shared.models.patient import PatientAllergy, LabResult, ClinicalNote
    from datetime import datetime, timezone

    if action.action_type == "add_allergy":
        allergy = PatientAllergy(
            patient_id=action.patient_id,
            allergen_type=action.extracted_value.get("allergen_type", "drug"),
            allergen_name=action.extracted_value.get("allergen_name", ""),
            reaction=action.extracted_value.get("reaction"),
            source="ai_extracted_transcript",
        )
        db.add(allergy)

    elif action.action_type == "add_lab_result":
        lab = LabResult(
            patient_id=action.patient_id,
            test_name=action.extracted_value.get("test_name", ""),
            value=str(action.extracted_value.get("value", "")),
            result_date=datetime.now(timezone.utc),
            source="ai_extracted_transcript",
        )
        db.add(lab)

    elif action.action_type in ("add_condition", "update_medication_list", "adherence_concern"):
        note = ClinicalNote(
            patient_id=action.patient_id,
            note_type=action.action_type,
            content=f"AI extracted from transcript: {action.extracted_value}",
            source="ai_extracted_transcript",
            ai_generated=True,
            pharmacist_reviewed=True,
        )
        db.add(note)
