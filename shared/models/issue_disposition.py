"""Decided layer for data-quality issues: the owner's ruling on an incompatibility.

Without this the ناسازگاری‌ها list can never converge — every run re-derives the
same ~45,000 item-issues, including the ones that are structurally NORMAL (a
compounding raw material has no finished-product IRC; a device is not a drug; a
drug not marketed in Iran is legitimately absent from NFI). Recording a
disposition lets those leave the open list permanently while staying auditable,
exactly as crosswalk_entries and field_overrides do for matching and fields.

Append-only in spirit: a re-ruling updates the row and re-stamps who/when.
"""
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import TimestampedBase


class IssueDisposition(TimestampedBase):
    __tablename__ = "issue_dispositions"
    __table_args__ = (
        UniqueConstraint("issue_type", "subject_key", name="uq_issue_subject"),
    )

    # what kind of incompatibility (issue_registry.ISSUE_TYPES)
    issue_type: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    # WHAT it is about: an IRC, an "insurer|code:x" key, or "*" for a whole class
    subject_key: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    # accepted | wont_fix | resolved | deferred  (see issue_registry.DISPOSITIONS)
    disposition: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # free context: counts at ruling time, the rule that proposed it, etc.
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    decided_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
