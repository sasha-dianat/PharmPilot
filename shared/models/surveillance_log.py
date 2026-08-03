"""Surveillance observation log — fused biometric identity and RF position.

One row per observation. Deliberately an OBSERVATION log, not an identity
record: it stores what a sensor reported at a moment, with the confidence and
the reason behind it, and it never asserts who someone is. Promotion of an
observation into an identity decision happens elsewhere, through the assurance
levels, and requires corroboration this table cannot supply.

Three properties are load-bearing:

**Fused results carry their working.** `fusion_detail` holds the per-modality
contributions and every exclusion with its reason, so an observation can be
re-examined months later without re-running the pipeline. A confidence with no
provenance is not reviewable, and an unreviewable surveillance record is one
that cannot be defended.

**RF position is a DEVICE fix.** `rf_device_ref` is a device reference, never a
person, and `rf_uncertainty_m` is mandatory — a coordinate without its error
bar invites false precision, which is how a positioning system ends up cited as
evidence it cannot support.

**Biometric identity and RF position are separate columns that are never
joined into a claim by this table.** A row may carry both because both were
observed in the same zone at the same time; that co-occurrence is a hypothesis
for a human, not a conclusion.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (Boolean, DateTime, ForeignKey, Index, Integer, Numeric,
                        String, Text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from shared.models.base import TimestampedBase


class SurveillanceObservation(TimestampedBase):
    """A single sensor observation: who a fusion proposed, and/or where a device was."""

    __tablename__ = "surveillance_observations"

    pharmacy_id: Mapped[UUID] = mapped_column(
        ForeignKey("pharmacies.id"), nullable=False, index=True)

    # ── when and where ──────────────────────────────────────────────────
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True)
    site: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # pharmacy | depot
    zone_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    action_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # entry | exit | movement | dwell | pick | access_denied

    # ── biometric side (may be absent) ──────────────────────────────────
    biometric_identity_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("biometric_identities.id"), nullable=True, index=True)
    fusion_decision: Mapped[str | None] = mapped_column(String(12), nullable=True)
    # auto | review | no_match
    fusion_confidence: Mapped[float | None] = mapped_column(
        Numeric(6, 5), nullable=True)
    fusion_margin: Mapped[float | None] = mapped_column(Numeric(6, 5), nullable=True)
    # Per-modality contributions AND exclusions with their reasons. This is what
    # makes an observation reviewable long after the fact.
    fusion_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    modalities_used: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    fusion_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── RF side (may be absent) — a DEVICE fix, never a person ──────────
    rf_device_ref: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    rf_x: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    rf_y: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    # Mandatory alongside a coordinate: see the module docstring.
    rf_uncertainty_m: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    rf_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # fingerprint | trilateration
    rf_ap_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── provenance and review ───────────────────────────────────────────
    camera_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="edge")
    reviewed_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    # Set when this observation has been superseded or corrected. Observations
    # are never deleted — a correction is a new row plus this flag.
    superseded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        Index("ix_surv_obs_site_time", "site", "observed_at"),
        Index("ix_surv_obs_zone_time", "zone_id", "observed_at"),
        Index("ix_surv_obs_identity_time", "biometric_identity_id", "observed_at"),
    )
