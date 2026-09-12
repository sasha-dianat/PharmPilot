"""
End-to-end audio processing pipeline coordinator.
Orchestrates: capture → noise cancellation → diarization → transcription → enrichment
"""
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

import numpy as np

from services.audio.noise_cancellation.processor import PharmacyNoiseCanceller
from services.audio.speaker_diarization.diarizer import PharmacySpeakerDiarizer
from services.audio.transcription.engine import PharmacyTranscriptionEngine
from services.audio.profile_enrichment.extractor import ClinicalInformationExtractor

logger = logging.getLogger(__name__)


@dataclass
class AudioZoneConfig:
    """Per-zone audio capture settings.

    On `whisper_model`: never use an `.en` variant. Those models are
    English-ONLY and emit invented English for Persian speech rather than a
    worse transcript — a silent wrong answer instead of a visible bad one.
    Prefer a smaller multilingual model (`base`, `small`) over `base.en`.

    `zone_id` is a canonical vision_zone code (Z-COUNTER-1, Z-CONSULT, ...).
    The former ZONE_CONFIGS dict used its own spellings; those are mapped in
    services.core.vision.zones.LEGACY_ZONE_ALIAS.
    """

    zone_id: str
    zone_name: str
    mic_channels: int
    whisper_model: str       # Larger model for counseling room (more clinical detail)
    continuous_recording: bool
    vad_sensitivity: int     # 0-3


class AudioProcessingPipeline:
    """
    Coordinates the complete audio pipeline for a single pharmacy zone.
    Runs as an async service, consuming audio from hardware and publishing
    transcript events to Kafka for downstream consumers (profile enrichment,
    pharmacist live display, audit log).
    """

    def __init__(
        self,
        zone_config: AudioZoneConfig,
        pharmacy_id: UUID,
        kafka_producer=None,
        db_session=None,
    ):
        self.zone = zone_config
        self.pharmacy_id = pharmacy_id
        self._kafka = kafka_producer
        self._db = db_session

        self.noise_canceller = PharmacyNoiseCanceller()
        self.diarizer = PharmacySpeakerDiarizer()
        self.transcriber = PharmacyTranscriptionEngine(model_size=zone_config.whisper_model)
        self.extractor = ClinicalInformationExtractor()

        self._active_session_id: Optional[UUID] = None
        self._session_buffer: list[np.ndarray] = []
        self._session_started_at: Optional[datetime] = None

    async def process_audio_chunk(
        self,
        raw_audio: bytes,
        sample_rate: int = 16000,
        current_visit_id: Optional[UUID] = None,
        current_patient_id: Optional[UUID] = None,
    ) -> None:
        """
        Called for each audio frame from the hardware capture layer.
        Buffers audio until a session boundary is detected, then processes.
        """
        # Stage 1: Noise cancellation
        cleaned_chunk = self.noise_canceller.process_chunk(raw_audio, sample_rate)

        if not cleaned_chunk.contains_speech:
            if self._active_session_id and self._session_buffer:
                # Check if we've had > 3 seconds of silence — end session
                silence_duration = len(raw_audio) / (sample_rate * 2)
                await self._maybe_end_session(
                    silence_seconds=silence_duration,
                    visit_id=current_visit_id,
                    patient_id=current_patient_id,
                )
            return

        # Start new session if none active
        if self._active_session_id is None:
            self._active_session_id = uuid4()
            self._session_started_at = datetime.now(timezone.utc)
            self._session_buffer = []
            logger.debug("New audio session started: %s in zone %s", self._active_session_id, self.zone.zone_id)

        self._session_buffer.append(cleaned_chunk.data)

    async def _maybe_end_session(
        self,
        silence_seconds: float,
        visit_id: Optional[UUID],
        patient_id: Optional[UUID],
        silence_threshold: float = 3.0,
    ) -> None:
        if silence_seconds < silence_threshold:
            return

        session_id = self._active_session_id
        buffer = self._session_buffer.copy()
        started_at = self._session_started_at

        self._active_session_id = None
        self._session_buffer = []
        self._session_started_at = None

        if not buffer:
            return

        # Process in background — don't block audio capture
        asyncio.create_task(
            self._process_completed_session(
                session_id=session_id,
                audio_buffer=buffer,
                started_at=started_at,
                visit_id=visit_id,
                patient_id=patient_id,
            )
        )

    async def _process_completed_session(
        self,
        session_id: UUID,
        audio_buffer: list[np.ndarray],
        started_at: datetime,
        visit_id: Optional[UUID],
        patient_id: Optional[UUID],
        sample_rate: int = 16000,
    ) -> None:
        """
        Full pipeline for a completed conversation session:
        diarize → transcribe → assign speakers → extract clinical info → publish
        """
        try:
            full_audio = np.concatenate(audio_buffer)
            duration = len(full_audio) / sample_rate
            logger.info("Processing session %s: %.1fs of audio", session_id, duration)

            # Stage 2: Speaker diarization
            diarized = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.diarizer.diarize(full_audio, sample_rate, self.zone.zone_id),
            )

            # Stage 3: Transcription with speaker alignment
            transcription = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.transcriber.transcribe_batch(full_audio, sample_rate),
            )

            # Stage 4: Merge diarization with transcription
            diarized_segments = self._align_transcription_with_diarization(
                transcription.segments, diarized.segments
            )

            # Stage 5: Clinical extraction (only from patient speech)
            extraction = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.extractor.extract(
                    full_transcript=transcription.full_text,
                    diarized_segments=[
                        {
                            "speaker": s.get("speaker_label", ""),
                            "role": s.get("role", "unknown"),
                            "text": s.get("text", ""),
                            "start": s.get("start", 0.0),
                        }
                        for s in diarized_segments
                    ],
                    patient_id=patient_id,
                    transcript_id=session_id,
                ),
            )

            # Stage 6: Publish to Kafka for DB persistence and pharmacist UI
            await self._publish_transcript_event(
                session_id=session_id,
                visit_id=visit_id,
                patient_id=patient_id,
                full_text=transcription.full_text,
                diarized_segments=diarized_segments,
                extraction=extraction,
                duration_seconds=duration,
                started_at=started_at,
            )

            # Stage 7: Urgency alert if flagged
            if extraction.urgency_flag:
                await self._publish_urgency_alert(
                    session_id=session_id,
                    patient_id=patient_id,
                    reason=extraction.urgency_reason,
                )

        except Exception as exc:
            logger.error("Audio pipeline failed for session %s: %s", session_id, exc, exc_info=True)

    def _align_transcription_with_diarization(
        self,
        transcription_segments: list,
        diarization_segments: list,
    ) -> list[dict]:
        """
        Merge Whisper word-level timestamps with pyannote speaker boundaries.
        Each output segment has: speaker, role, text, start, end.
        """
        aligned = []
        for t_seg in transcription_segments:
            # Find which diarization speaker was active during this segment
            best_speaker = "SPEAKER_00"
            best_overlap = 0.0

            for d_seg in diarization_segments:
                overlap_start = max(t_seg.start, d_seg.start_seconds)
                overlap_end = min(t_seg.end, d_seg.end_seconds)
                overlap = max(0.0, overlap_end - overlap_start)
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_speaker = d_seg.speaker_label

            aligned.append({
                "speaker_label": best_speaker,
                "role": next(
                    (d.speaker_role for d in diarization_segments if d.speaker_label == best_speaker),
                    "unknown",
                ),
                "text": t_seg.text,
                "start": t_seg.start,
                "end": t_seg.end,
                "confidence": t_seg.confidence,
            })

        return aligned

    async def _publish_transcript_event(self, **kwargs) -> None:
        if self._kafka:
            import json
            event = {
                "event_type": "audio_transcript_completed",
                "pharmacy_id": str(self.pharmacy_id),
                "zone": self.zone.zone_id,
                **{k: str(v) if hasattr(v, "__str__") and not isinstance(v, (str, int, float, list, dict)) else v
                   for k, v in kwargs.items()},
            }
            await self._kafka.send("audio.transcripts", json.dumps(event).encode())

    async def _publish_urgency_alert(
        self,
        session_id: UUID,
        patient_id: Optional[UUID],
        reason: Optional[str],
    ) -> None:
        if self._kafka:
            import json
            await self._kafka.send(
                "alerts.urgency",
                json.dumps({
                    "alert_type": "audio_urgency",
                    "pharmacy_id": str(self.pharmacy_id),
                    "session_id": str(session_id),
                    "patient_id": str(patient_id) if patient_id else None,
                    "reason": reason,
                    "zone": self.zone.zone_id,
                }).encode(),
            )
            logger.warning("URGENCY ALERT published for session %s: %s", session_id, reason)
