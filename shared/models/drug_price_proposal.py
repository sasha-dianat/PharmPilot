"""Pending price-change proposals from a daily sync — manager-approved before
catalog prices move (reference data, not PHI)."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import TimestampedBase


class DrugPriceProposal(TimestampedBase):
    __tablename__ = "drug_price_proposals"

    irc: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    name_fa: Mapped[str] = mapped_column(String(300), nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)        # new | increase | decrease
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True, nullable=False)
    source: Mapped[str | None] = mapped_column(String(40), nullable=True)  # nfi | invoice | sync feed id

    current_announced: Mapped[float | None] = mapped_column(Numeric(14, 0), nullable=True)
    proposed_announced: Mapped[float | None] = mapped_column(Numeric(14, 0), nullable=True)
    current_invoice: Mapped[float | None] = mapped_column(Numeric(14, 0), nullable=True)
    proposed_invoice: Mapped[float | None] = mapped_column(Numeric(14, 0), nullable=True)
    current_effective: Mapped[float] = mapped_column(Numeric(14, 0), default=0, nullable=False)
    proposed_effective: Mapped[float] = mapped_column(Numeric(14, 0), default=0, nullable=False)
    delta: Mapped[float] = mapped_column(Numeric(14, 0), default=0, nullable=False)
    pct_change: Mapped[float] = mapped_column(Numeric(8, 2), default=0, nullable=False)

    decided_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
