from datetime import date
from uuid import UUID

from sqlalchemy import Date, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import AuditedBase


class Medication(AuditedBase):
    __tablename__ = "medications"

    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    drug_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    strength: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dose: Mapped[str | None] = mapped_column(String(100), nullable=True)
    route: Mapped[str | None] = mapped_column(String(50), nullable=True)
    frequency: Mapped[str | None] = mapped_column(String(100), nullable=True)
    indication: Mapped[str | None] = mapped_column(String(255), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    stop_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    source: Mapped[str] = mapped_column(String(40), default="patient_reported", nullable=False)
    confidence_score: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)

    patient: Mapped["Patient"] = relationship(back_populates="medications")


class ClinicalAlert(AuditedBase):
    __tablename__ = "clinical_alerts"

    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    module: Mapped[str] = mapped_column(String(40), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(12), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    patient_specific_factors: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    missing_data: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    suggested_actions: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    evidence_sources: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)


class ClinicalAuditLog(AuditedBase):
    __tablename__ = "clinical_audit_logs"

    user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    patient_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)
    module: Mapped[str] = mapped_column(String(40), nullable=False)
    input_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    output_snapshot: Mapped[dict | list] = mapped_column(JSONB, nullable=False)
    rules_triggered: Mapped[list] = mapped_column(JSONB, nullable=False)
    model_version: Mapped[str] = mapped_column(String(40), nullable=False)
