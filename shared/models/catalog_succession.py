"""IRC succession: the same product, re-registered under a new IRC.

Every decided fact — field overrides, insurer coverage, crosswalk pointers — is
anchored to an IRC. When NFI re-registers a product the new row arrives empty
and the old one lingers with data nobody will ever see again. The /NFI/Detail
page id is the thing that stays constant across a re-registration, so a page
that once served IRC A and now serves IRC B is the evidence.

Never silent: a succession is a PROPOSAL until an owner applies it, and what it
carried across is recorded so it can be explained afterwards.
"""
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import TimestampedBase


class CatalogSuccession(TimestampedBase):
    __tablename__ = "catalog_succession"

    page_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    old_irc: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    new_irc: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="proposed", nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    carried: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    decided_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
