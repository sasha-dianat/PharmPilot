from datetime import date, datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import (Boolean, Date, DateTime, ForeignKey, Integer, Numeric,
                        SmallInteger, String, Text, UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import AuditedBase


class RxStatus(str, Enum):
    INTAKE = "intake"
    PENDING_DUR = "pending_dur"
    DUR_HOLD = "dur_hold"
    PENDING_VERIFICATION = "pending_verification"
    VERIFICATION_IN_PROGRESS = "verification_in_progress"
    PENDING_ADJUDICATION = "pending_adjudication"
    ADJUDICATION_REJECTED = "adjudication_rejected"
    PENDING_PA = "pending_pa"
    READY_TO_FILL = "ready_to_fill"
    FILLING = "filling"
    FILLED = "filled"
    WILL_CALL = "will_call"
    DISPENSED = "dispensed"
    RETURNED_TO_STOCK = "returned_to_stock"
    CANCELLED = "cancelled"
    TRANSFERRED_OUT = "transferred_out"
    ON_HOLD = "on_hold"


class RxSource(str, Enum):
    PAPER = "paper"
    FAX = "fax"
    EPRESCRIBE = "eprescribe"
    TELEPHONE = "telephone"
    TRANSFER_IN = "transfer_in"
    REFILL_REQUEST = "refill_request"


class CancellationReason(str, Enum):
    """
    Canonical values expected in RxStateEvent.reason when to_status == CANCELLED.

    The reason column remains free text for flexibility; API/frontend callers
    should pass one of these strings for cancellations so analytics can group
    and filter cancellation patterns reliably.
    """

    CUSTOMER_DECLINED = "customer_declined"
    PRESCRIBER_CANCELLED = "prescriber_cancelled"
    DUPLICATE = "duplicate"
    EXPIRED = "expired"
    INSURANCE_ISSUE = "insurance_issue"
    OTHER = "other"


class Prescription(AuditedBase):
    __tablename__ = "prescriptions"

    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    prescriber_id: Mapped[UUID] = mapped_column(ForeignKey("prescribers.id"), nullable=False)

    # Rx identifiers
    rx_number: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    ndc: Mapped[str] = mapped_column(String(11), nullable=False, index=True)
    drug_name: Mapped[str] = mapped_column(String(255), nullable=False)
    drug_strength: Mapped[str | None] = mapped_column(String(100), nullable=True)
    drug_form: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Sig and dosing
    sig_text: Mapped[str] = mapped_column(Text, nullable=False)
    sig_structured: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # NLP-parsed sig

    # Quantities
    quantity_prescribed: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)
    quantity_dispensed: Mapped[float | None] = mapped_column(Numeric(10, 3), nullable=True)
    days_supply: Mapped[int] = mapped_column(Integer, nullable=False)
    refills_authorized: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    refills_remaining: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Drug schedule
    dea_schedule: Mapped[str | None] = mapped_column(String(5), nullable=True)  # CI, CII, CIII, CIV, CV
    is_controlled: Mapped[bool] = mapped_column(default=False, nullable=False)

    # Dates
    written_date: Mapped[date] = mapped_column(Date, nullable=False)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    fill_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_fill_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Source
    source: Mapped[RxSource] = mapped_column(String(20), nullable=False)
    eprescribe_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Workflow state
    status: Mapped[RxStatus] = mapped_column(String(50), default=RxStatus.INTAKE, nullable=False, index=True)
    claimed_by_staff_id: Mapped[UUID | None] = mapped_column(nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Clinical
    daw_code: Mapped[str] = mapped_column(String(1), default="0", nullable=False)
    dispense_as_written: Mapped[bool] = mapped_column(default=False)
    is_partial_fill: Mapped[bool] = mapped_column(default=False)
    clinical_override_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # AI clinical brain outputs
    acb_safety_report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    acb_consultation: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ai_risk_score: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)

    # EPCS
    epcs_signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    epcs_signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Precomputed council + triage (migration 0004) ───────────────────────
    # "pending" | "ready" | "failed"
    intake_analysis_status: Mapped[str] = mapped_column(String(20), default="pending")
    # "green" | "amber" | "red"
    triage_lane: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # JSON result blob from ReviewTriageEngine
    triage_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # JSON result blob from SpecialistCouncil
    council_cache: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    council_computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    patient: Mapped["Patient"] = relationship(back_populates="prescriptions")
    fills: Mapped[list["PrescriptionFill"]] = relationship(back_populates="prescription")
    dur_alerts: Mapped[list["DURAlert"]] = relationship(back_populates="prescription")
    state_events: Mapped[list["RxStateEvent"]] = relationship(back_populates="prescription")


class PrescriptionFill(AuditedBase):
    __tablename__ = "prescription_fills"

    prescription_id: Mapped[UUID] = mapped_column(
        ForeignKey("prescriptions.id"), nullable=False, index=True
    )
    fill_number: Mapped[int] = mapped_column(Integer, nullable=False)
    ndc_dispensed: Mapped[str] = mapped_column(String(11), nullable=False)
    quantity_dispensed: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)
    days_supply: Mapped[int] = mapped_column(Integer, nullable=False)
    fill_date: Mapped[date] = mapped_column(Date, nullable=False)
    dispensed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispensing_pharmacist_id: Mapped[UUID] = mapped_column(nullable=False)
    verifying_pharmacist_id: Mapped[UUID] = mapped_column(nullable=False)
    lot_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # The recall link (migration 0030). `lot_number` alone is free text and
    # cannot answer "which patients received lot X" reliably; this can.
    inventory_lot_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("inventory_lots.id"), nullable=True, index=True
    )
    # Jalali fill date (migration 0003). In the database since 0003 but never
    # mapped — a Persian-calendar deployment could not read its own fill dates
    # through the ORM.
    fill_date_jalali: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # Patient pickup
    pickup_confirmed_by_biometric: Mapped[bool] = mapped_column(default=False)
    pickup_biometric_identity_id: Mapped[UUID | None] = mapped_column(nullable=True)
    pickup_signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    picked_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    prescription: Mapped["Prescription"] = relationship(back_populates="fills")
    claim_transactions: Mapped[list["ClaimTransaction"]] = relationship(back_populates="fill")


class DURAlert(AuditedBase):
    __tablename__ = "dur_alerts"

    prescription_id: Mapped[UUID] = mapped_column(
        ForeignKey("prescriptions.id"), nullable=False, index=True
    )
    alert_type: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)  # critical, high, moderate, informational
    source: Mapped[str] = mapped_column(String(50), nullable=False)  # fdb, medi_span, acb, beers, stopp_start
    description: Mapped[str] = mapped_column(Text, nullable=False)
    interacting_drug_ndc: Mapped[str | None] = mapped_column(String(11), nullable=True)
    interacting_drug_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_hard_stop: Mapped[bool] = mapped_column(default=False)
    was_shown: Mapped[bool] = mapped_column(default=False)
    was_overridden: Mapped[bool] = mapped_column(default=False)
    override_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    overridden_by_id: Mapped[UUID | None] = mapped_column(nullable=True)
    overridden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evidence_grade: Mapped[str | None] = mapped_column(String(5), nullable=True)
    citations: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    prescription: Mapped["Prescription"] = relationship(back_populates="dur_alerts")


class RxStateEvent(AuditedBase):
    """Immutable event log — every prescription state transition recorded here."""
    __tablename__ = "rx_state_events"

    prescription_id: Mapped[UUID] = mapped_column(
        ForeignKey("prescriptions.id"), nullable=False, index=True
    )
    from_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    to_status: Mapped[str] = mapped_column(String(50), nullable=False)
    triggered_by_id: Mapped[UUID | None] = mapped_column(nullable=True)
    triggered_by_type: Mapped[str] = mapped_column(String(30), default="staff")  # staff, system, ai
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    event_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA-256 for tamper evidence

    # ── what makes the hash above verifiable by someone other than the writer ──
    # (migration 0053). Until it landed, the chain could not be recomputed from
    # this table by anyone: the writer hashed `datetime.now(timezone.utc)` and
    # the row took `created_at` from `server_default=func.now()`, a different
    # clock, so every link failed on replay. Both inputs the digest consumes —
    # the instant and the predecessor's hash — were discarded after use.
    #
    # `hashed_at` is the instant that actually went into the digest. It is kept
    # separate from `created_at` rather than overwriting it, so the two remain
    # independently readable and their drift stays visible.
    hashed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The predecessor's `event_hash`, stored rather than recomputed. NULL means
    # "first event for this prescription", which is a statement, not a gap.
    previous_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Monotonic per prescription, starting at 1. `created_at` cannot order the
    # chain: Postgres now() is transaction-start time, so two transitions
    # committed together carry byte-identical timestamps and the predecessor
    # became whichever row the planner returned. A unique constraint on
    # (prescription_id, sequence_number) makes a concurrent double-write fail
    # loudly instead of silently producing two events at the same position.
    sequence_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Which digest definition this row's `event_hash` was built under. Rows
    # written before 0053 are version 1 and stay verifiable under the narrow
    # six-field digest; version 2 spans the fields an auditor actually reads.
    # Rehashing the old rows would have destroyed the evidence it protects — a
    # row already altered would have been re-blessed as valid.
    digest_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="1"
    )

    prescription: Mapped["Prescription"] = relationship(back_populates="state_events")

    __table_args__ = (
        UniqueConstraint("prescription_id", "sequence_number",
                         name="uq_rx_state_events_seq"),
    )


class LabelEvent(AuditedBase):
    """Audit trail for label-engine actions (handwritten overrides, prints, etc.)."""
    __tablename__ = "label_events"

    rx_id: Mapped[UUID] = mapped_column(
        ForeignKey("prescriptions.id"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)  # e.g. "handwritten", "printed"
    staff_id: Mapped[UUID] = mapped_column(ForeignKey("staff.id"), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
