"""Drug enrichment reference — web-researched details, owner-approved before use."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Float, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import TimestampedBase


class DrugEnrichment(TimestampedBase):
    __tablename__ = "drug_enrichments"

    key: Mapped[str] = mapped_column(String(300), unique=True, index=True, nullable=False)
    raw_name: Mapped[str] = mapped_column(String(300), nullable=False)
    irc: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    generic_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    brand_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    country: Mapped[str | None] = mapped_column(String(80), nullable=True)
    dosage_form: Mapped[str | None] = mapped_column(String(80), nullable=True)
    strengths: Mapped[list | None] = mapped_column(JSONB, nullable=True)   # display strings
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    sources: Mapped[list | None] = mapped_column(JSONB, nullable=True)     # urls
    researched_by: Mapped[str] = mapped_column(String(20), nullable=False)  # mistral|claude|manual
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # suggested | approved | rejected
    status: Mapped[str] = mapped_column(String(20), default="suggested", index=True, nullable=False)
    decided_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
