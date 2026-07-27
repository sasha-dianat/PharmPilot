"""Observed layer: one row per formulary line per staged harvest (append-only).
The full raw source row is kept so any import can be replayed and diffed
against the decided layer later."""
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import TimestampedBase


class FormularySnapshot(TimestampedBase):
    __tablename__ = "formulary_snapshots"

    run_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=True)
    insurer: Mapped[str] = mapped_column(String(20), nullable=False)
    source_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    reference_price: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    share_pct: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    covered: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    row: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
