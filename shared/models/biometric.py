from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import (Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, Numeric,
                        String, Text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import AuditedBase, TimestampedBase


class IdentityClass(str, Enum):
    PHARMACIST = "pharmacist"
    PHARMACY_TECHNICIAN = "pharmacy_technician"
    CASHIER = "cashier"
    PHARMACY_MANAGER = "pharmacy_manager"
    INVENTORY_STAFF = "inventory_staff"
    REGISTERED_PATIENT = "registered_patient"
    REGISTERED_PATIENT_CAREGIVER = "registered_patient_caregiver"
    PHARMACEUTICAL_REP = "pharmaceutical_rep"
    WHOLESALER_DELIVERY = "wholesaler_delivery"
    SERVICE_TECHNICIAN = "service_technician"
    UNKNOWN_VISITOR = "unknown_visitor"
    REPEAT_UNKNOWN = "repeat_unknown"
    WATCHLIST_MATCH = "watchlist_match"
    BEHAVIORAL_ALERT = "behavioral_alert"
    MINOR_UNACCOMPANIED = "minor_unaccompanied"


class BiometricIdentity(AuditedBase):
    """
    One record per unique individual detected in the pharmacy.
    Links biometric templates to pharmacy records (patient/staff).
    Templates stored as encrypted numpy vector bytes — never raw images.
    """
    __tablename__ = "biometric_identities"

    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)

    # Encrypted biometric templates
    face_embedding_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    gait_signature_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    voice_print_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    fingerprint_template_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    # FAISS index reference
    faiss_index_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    # Identity classification
    identity_class: Mapped[IdentityClass] = mapped_column(
        String(50), default=IdentityClass.UNKNOWN_VISITOR, nullable=False
    )
    confidence_score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=0.0)

    # Linkage to pharmacy records
    patient_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("patients.id"), nullable=True, index=True
    )
    staff_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)

    # Visit tracking
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total_visits: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Security flags
    is_watchlist_match: Mapped[bool] = mapped_column(default=False)
    watchlist_source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    security_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Consent tracking (jurisdiction-configurable — can be disabled per deployment)
    consent_security_monitoring: Mapped[bool] = mapped_column(default=False)
    consent_patient_services: Mapped[bool] = mapped_column(default=False)
    consent_recorded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consent_method: Mapped[str | None] = mapped_column(String(50), nullable=True)

    visits: Mapped[list["PharmacyVisit"]] = relationship(back_populates="biometric_identity")


class PharmacyVisit(TimestampedBase):
    """Every detected entry/presence event in the pharmacy."""
    __tablename__ = "pharmacy_visits"

    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    biometric_identity_id: Mapped[UUID] = mapped_column(
        ForeignKey("biometric_identities.id"), nullable=False, index=True
    )
    patient_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("patients.id"), nullable=True, index=True
    )

    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    identity_class: Mapped[IdentityClass] = mapped_column(String(50), nullable=False)
    match_confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)

    # Clinical pre-load triggered
    profile_preloaded: Mapped[bool] = mapped_column(default=False)
    acb_prescan_completed: Mapped[bool] = mapped_column(default=False)

    # Security events during this visit
    security_events: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Audio transcript linkage
    has_transcripts: Mapped[bool] = mapped_column(default=False)

    biometric_identity: Mapped["BiometricIdentity"] = relationship(back_populates="visits")
    patient: Mapped["Patient | None"] = relationship(back_populates="visit_records")
    audio_transcripts: Mapped[list["AudioTranscript"]] = relationship(back_populates="visit")


class SecurityEvent(TimestampedBase):
    """Security alerts generated by behavioral analysis."""
    __tablename__ = "security_events"

    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    visit_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("pharmacy_visits.id"), nullable=True
    )
    biometric_identity_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("biometric_identities.id"), nullable=True
    )

    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)  # info, warning, critical
    description: Mapped[str] = mapped_column(Text, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved: Mapped[bool] = mapped_column(default=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_id: Mapped[UUID | None] = mapped_column(nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    camera_footage_retained: Mapped[bool] = mapped_column(default=False)
    footage_retention_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # DB column is event_metadata (see migration); the prior "metadata" override
    # pointed at a nonexistent column → every ORM SELECT of SecurityEvent 500'd.
    event_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class BiometricTemplate(AuditedBase):
    """One enrolled biometric sample. Many per person, per modality.

    `BiometricIdentity` keeps a single embedding column per modality, which
    caps accuracy: enrolment cannot accumulate, and one poor capture degrades
    that person permanently. Here a template is an individual, retirable record
    with its own quality, provenance and model version.
    """
    __tablename__ = "biometric_templates"

    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    identity_id: Mapped[UUID] = mapped_column(
        ForeignKey("biometric_identities.id", ondelete="CASCADE"),
        nullable=False, index=True)
    modality: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    embedding: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    # Comparing vectors from two extractor versions compares different spaces.
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    quality: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    capture_context: Mapped[dict] = mapped_column(JSONB, default=dict)
    enrolled_by_id: Mapped[UUID | None] = mapped_column(nullable=True)
    # Retired, not deleted: an identification made last year stays explainable
    # from the templates that existed when it was made.
    retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    retired_reason: Mapped[str | None] = mapped_column(String(240), nullable=True)
    # A candidate model's templates are enrolled and measured without entering
    # the index that decides identifications. Excluded from load_index by
    # default; opting in is explicit and lands in a separate cache slot.
    shadow: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class BiometricScoreStats(TimestampedBase):
    """Measured genuine/impostor score distributions, per modality and model.

    Fusion turns a raw similarity into a likelihood ratio, and that conversion
    is only meaningful against distributions measured on this population and
    these cameras. Storing them makes the numbers behind an identification
    auditable instead of hard-coded.
    """
    __tablename__ = "biometric_score_stats"

    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    modality: Mapped[str] = mapped_column(String(16), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    genuine_mean: Mapped[float] = mapped_column(Float, nullable=False)
    genuine_std: Mapped[float] = mapped_column(Float, nullable=False)
    impostor_mean: Mapped[float] = mapped_column(Float, nullable=False)
    impostor_std: Mapped[float] = mapped_column(Float, nullable=False)
    samples: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Which occlusion subset this was measured on. A single pooled distribution
    # averages veiled faces in with unoccluded ones and yields a threshold too
    # LOW for the veiled subset, so the FPIR guarantee fails for exactly the
    # group it most affects. 'all' is the pooled row that predates strata.
    stratum: Mapped[str] = mapped_column(String(16), nullable=False, default="all")
    # The active model version this row is a candidate to replace. NULL means
    # this row IS active, which is why the live lookup filters on IS NULL — an
    # unpromoted model must never set live thresholds.
    shadow_of: Mapped[str | None] = mapped_column(String(64), nullable=True)
    measured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
