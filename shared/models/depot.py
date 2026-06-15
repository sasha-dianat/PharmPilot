"""Depot → shelf dual-verification replenishment models (migration 0012).

Model A: inventory_lots stays the batch source of truth. This module adds the
location/placement layer (shelves + placements), the replenishment session state
machine, the transfer-event ledger, the surveillance-event seam, and shift
handover reports. Depot back-stock qty is computed (lot.quantity_on_hand − Σ
placements), never double-stored.
"""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import AuditedBase

STORAGE_CONDITIONS = ("ROOM_TEMP", "REFRIGERATED", "FROZEN", "LIGHT_PROTECTED")
SESSION_STATES = ("PICK_LIST", "DEPOT_COLLECTING", "IN_TRANSIT", "SHELF_PLACING", "COMPLETE", "CANCELLED")
SURVEILLANCE_EVENT_TYPES = ("ANOMALY", "WRONG_BIN", "EXTRA_ITEM", "BEHAVIOR", "UNSCANNED_PICK", "AFTER_HOURS")


class PharmacyShelf(AuditedBase):
    __tablename__ = "pharmacy_shelves"

    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(40), nullable=False)        # e.g. "A-03-B-05"
    zone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    capacity_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # denormalized cache of Σ placements
    storage_condition: Mapped[str] = mapped_column(String(20), nullable=False, default="ROOM_TEMP")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ShelfPlacement(AuditedBase):
    __tablename__ = "shelf_placements"

    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    inventory_lot_id: Mapped[UUID] = mapped_column(ForeignKey("inventory_lots.id"), nullable=False, index=True)
    shelf_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacy_shelves.id"), nullable=False, index=True)
    ndc11: Mapped[str] = mapped_column(String(11), nullable=False, index=True)
    units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    placed_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReplenishmentSession(AuditedBase):
    __tablename__ = "replenishment_sessions"

    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PICK_LIST", index=True)
    pick_list: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    depot_checkpoint: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reconciliation: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    started_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ShelfTransferEvent(AuditedBase):
    __tablename__ = "shelf_transfer_events"

    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    session_id: Mapped[UUID | None] = mapped_column(ForeignKey("replenishment_sessions.id"), nullable=True, index=True)
    inventory_lot_id: Mapped[UUID] = mapped_column(ForeignKey("inventory_lots.id"), nullable=False)
    shelf_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacy_shelves.id"), nullable=False)
    ndc11: Mapped[str] = mapped_column(String(11), nullable=False)
    quantity_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    performed_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    barcode_verification_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ai_verification_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    depot_checkpoint_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    override_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    pharmacist_attestation_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    pharmacist_attestation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    temperature_logged_c: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), nullable=True)
    near_expiry_placement_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SurveillanceEvent(AuditedBase):
    __tablename__ = "surveillance_events"

    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    camera_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    session_id: Mapped[UUID | None] = mapped_column(ForeignKey("replenishment_sessions.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(10), nullable=False, default="low")
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    clip_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reviewed_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    owner_notified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ShiftHandoverReport(AuditedBase):
    __tablename__ = "shift_handover_reports"

    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    shift_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    shift_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    performed_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    transfers_completed: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    fefo_overrides: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    anomaly_signals: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    open_split_packs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    cold_chain_events: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
