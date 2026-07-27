"""دارونامه acquisition — per-insurer source configs and staged harvest runs.

A CoverageSource is a repeatable pointer at wherever an insurer publishes its
formulary (URL + strategy + parse settings). A CoverageRun is one harvest's
staged output: parsed coverage, diff vs the live catalog, and the review queue.
Nothing touches drug_catalog.coverage until an admin approves the run.
"""
from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import TimestampedBase


class CoverageSource(TimestampedBase):
    __tablename__ = "coverage_sources"

    insurer: Mapped[str] = mapped_column(String(40), index=True, nullable=False)   # "tamin" | "salamat" | ...
    name: Mapped[str] = mapped_column(String(120), nullable=False)                  # display, e.g. "دارونامه تأمین اجتماعی"
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # "auto" | "file_url" | "html_table" | "paginated_html" | "json_api"
    strategy: Mapped[str] = mapped_column(String(20), default="auto", nullable=False)
    # {page_param, page_start, max_pages, table_index, record_path, delay_sec,
    #  proxy, encoding, column_overrides: {header: role|"ignore"}}
    settings: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    check_interval_days: Mapped[int] = mapped_column(Integer, default=7, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_status: Mapped[str | None] = mapped_column(String(20), nullable=True)


class CoverageRun(TimestampedBase):
    __tablename__ = "coverage_runs"

    source_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("coverage_sources.id"), index=True, nullable=False)
    insurer: Mapped[str] = mapped_column(String(40), nullable=False)   # denormalized for display
    # "running" | "parsed" | "approved" | "rejected" | "failed"
    status: Mapped[str] = mapped_column(String(20), default="running", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stats: Mapped[dict | None] = mapped_column(JSONB, nullable=True)      # {rows, applied, review, unmatched, skipped, columns}
    diff: Mapped[dict | None] = mapped_column(JSONB, nullable=True)       # {added, changed, removed, samples}
    staged: Mapped[dict | None] = mapped_column(JSONB, nullable=True)     # irc → {insurer: entry}
    review: Mapped[list | None] = mapped_column(JSONB, nullable=True)     # [{id, row, irc, name, confidence, entry}]
    unmatched: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # capped sample
    diagnostics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)   # {attempts:[…], summary:{…}}
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
