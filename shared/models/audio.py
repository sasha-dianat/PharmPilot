from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import AuditedBase, TimestampedBase


class TranscriptStatus(str, Enum):
    RECORDING = "recording"
    PROCESSING = "processing"
    TRANSCRIBED = "transcribed"
    ENRICHED = "enriched"        # AI profile enrichment complete
    REVIEWED = "reviewed"        # Pharmacist reviewed
    ARCHIVED = "archived"


class SpeakerRole(str, Enum):
    PHARMACIST = "pharmacist"
    PHARMACY_TECHNICIAN = "pharmacy_technician"
    CASHIER = "cashier"
    PATIENT = "patient"
    CAREGIVER = "caregiver"
    PHARMACEUTICAL_REP = "pharmaceutical_rep"
    UNKNOWN = "unknown"


class AudioTranscript(AuditedBase):
    """
    Full conversation transcript with speaker diarization.
    Stored per conversation session (single visit interaction).
    """
    __tablename__ = "audio_transcripts"

    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    visit_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("pharmacy_visits.id"), nullable=True, index=True
    )
    patient_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("patients.id"), nullable=True, index=True
    )

    # Recording metadata
    audio_zone: Mapped[str] = mapped_column(String(50), nullable=False)  # counter, waiting_area, counseling_room
    recording_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recording_ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Audio processing metadata
    noise_cancellation_applied: Mapped[bool] = mapped_column(default=True)
    noise_reduction_db: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    audio_quality_score: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    whisper_model_used: Mapped[str] = mapped_column(String(50), default="medium.en")
    transcription_confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)

    # Transcript content
    full_transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    diarized_segments: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Format: [{"speaker": "SPEAKER_0", "role": "pharmacist", "start": 0.0, "end": 3.2, "text": "..."}]

    # Speaker identification
    identified_speakers: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Format: {"SPEAKER_0": {"biometric_identity_id": "...", "role": "pharmacist", "confidence": 0.92}}

    # AI extraction results
    status: Mapped[TranscriptStatus] = mapped_column(
        String(20), default=TranscriptStatus.RECORDING, nullable=False
    )
    extracted_clinical_info: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    extracted_medications: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    extracted_allergies: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    extracted_conditions: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    extracted_concerns: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    clinical_action_items: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    sentiment_score: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)

    # Review
    pharmacist_reviewed: Mapped[bool] = mapped_column(default=False)
    pharmacist_id: Mapped[UUID | None] = mapped_column(nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    profile_updates_applied: Mapped[bool] = mapped_column(default=False)

    # Encrypted audio file reference (stored in encrypted S3/blob)
    audio_file_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    audio_encrypted: Mapped[bool] = mapped_column(default=True)

    visit: Mapped["PharmacyVisit | None"] = relationship(back_populates="audio_transcripts")
    patient: Mapped["Patient | None"] = relationship(back_populates="audio_transcripts")
    enrichment_actions: Mapped[list["ProfileEnrichmentAction"]] = relationship(
        back_populates="transcript"
    )


class ProfileEnrichmentAction(TimestampedBase):
    """
    Records every AI-driven profile update sourced from audio transcription.
    Pharmacist must approve before data is written to patient record.
    """
    __tablename__ = "profile_enrichment_actions"

    transcript_id: Mapped[UUID] = mapped_column(
        ForeignKey("audio_transcripts.id"), nullable=False, index=True
    )
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)

    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # allergy_add, medication_add, condition_add, note_add, lab_result_add,
    # contact_update, preference_update, concern_flag

    field_path: Mapped[str] = mapped_column(String(200), nullable=False)
    extracted_value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)  # The transcript excerpt
    source_timestamp_seconds: Mapped[float | None] = mapped_column(Numeric(10, 3), nullable=True)

    # Approval workflow
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, approved, rejected
    reviewed_by_id: Mapped[UUID | None] = mapped_column(nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    transcript: Mapped["AudioTranscript"] = relationship(back_populates="enrichment_actions")
