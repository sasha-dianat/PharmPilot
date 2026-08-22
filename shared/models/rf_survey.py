"""Access-point registry and surveyed radio map.

Both exist so `locate()` can use fingerprinting, which has been implemented
since phase 1 and unreachable in production because the live endpoint passed an
empty RadioMap. Trilateration's free-space path-loss assumption does not survive
a depot's shelving; fingerprinting learns the multipath instead of assuming it
away, which is why it is the default — when it has data.

Nothing here identifies a person. An access point knows where it is; a
fingerprint knows what the radio looked like at a spot on the floor.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (Boolean, CheckConstraint, DateTime, ForeignKey, Index,
                        Integer, Numeric, String, UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from shared.models.base import TimestampedBase


class RfAccessPoint(TimestampedBase):
    """One AP at a known position on a site's floor plan."""

    __tablename__ = "rf_access_points"

    pharmacy_id: Mapped[UUID] = mapped_column(
        ForeignKey("pharmacies.id"), nullable=False, index=True)
    site: Mapped[str] = mapped_column(String(20), nullable=False)
    ap_id: Mapped[str] = mapped_column(String(64), nullable=False)
    x: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)
    y: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)
    # Calibrated RSSI at 1 m. The path-loss model is only as good as this.
    tx_power_dbm: Mapped[float] = mapped_column(
        Numeric(6, 2), nullable=False, default=-40.0)
    # Retired rather than deleted: a fix computed last month stays explainable
    # from the layout that existed when it was computed.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "site", "ap_id", name="uq_rf_ap_per_site"),
        CheckConstraint("site IN ('pharmacy', 'depot')", name="ck_rf_ap_site"),
    )


class RfFingerprint(TimestampedBase):
    """What the radio looked like at one surveyed point on the floor."""

    __tablename__ = "rf_fingerprints"

    pharmacy_id: Mapped[UUID] = mapped_column(
        ForeignKey("pharmacies.id"), nullable=False, index=True)
    site: Mapped[str] = mapped_column(String(20), nullable=False)
    x: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)
    y: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)
    # {ap_id: rssi_dbm}. Read as a whole vector by the kNN distance, so one
    # row per AP would cost a join on the hot path and buy nothing.
    rssi: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # A survey point that heard one AP cannot constrain a position.
    ap_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # A radio map is a photograph of a building's RF environment. Move a
    # shelving run and it is wrong in ways that produce confident bad fixes
    # rather than obvious failures, so staleness has to be visible.
    surveyed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)
    surveyed_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True)

    __table_args__ = (
        Index("ix_rf_fingerprints_site", "pharmacy_id", "site"),
        CheckConstraint("site IN ('pharmacy', 'depot')", name="ck_rf_fp_site"),
        CheckConstraint("ap_count >= 0", name="ck_rf_fp_ap_count"),
    )
