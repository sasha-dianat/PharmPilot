"""Decision crosswalk: the owner-owned layer that imports may never overwrite.

CrosswalkEntry answers "this insurer row IS this product" once; the matcher
consults it before doing any fuzzy work, so a confirmed decision is never
re-litigated. FieldOverride answers "this NFI field is wrong" durably — it
re-applies after every crawl instead of being erased by the source.
"""
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Float, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import TimestampedBase


class CrosswalkEntry(TimestampedBase):
    __tablename__ = "crosswalk_entries"

    insurer: Mapped[str] = mapped_column(String(20), nullable=False)
    # the insurer's own stable identifier when it publishes one (tamin drug_code)
    source_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_key: Mapped[str] = mapped_column(String(300), nullable=False)
    raw_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    irc: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False)   # confirmed|rejected
    reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # "owner" = a person ruled on this pair; "auto" = the engine's own belief,
    # recorded so the next import reuses THIS answer instead of re-deriving one.
    # An auto row may be replaced by a better engine; an owner row never is.
    origin: Mapped[str] = mapped_column(String(8), default="owner", nullable=False, index=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    revised_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revised_from_irc: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decided_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FieldOverride(TimestampedBase):
    __tablename__ = "field_overrides"

    irc: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    field: Mapped[str] = mapped_column(String(40), nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)    # null = force blank
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    decided_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
