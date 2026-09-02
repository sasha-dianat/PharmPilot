"""The vision namespace: zones, and the policy that attaches to them.

**Foreign keys point OUT of this namespace and never into it.** A vision table
may reference pharmacies, staff, pharmacy_shelves or inventory_movements; no
clinical or inventory table may reference a vision table. That one-way rule is
what keeps the namespace droppable and what makes zone-scoped retention a purge
rather than a negotiation.

The single deliberate exception is `surveillance_observations`, which already
has a zone_id column and predates this registry. It gains a composite foreign
key into vision_zone in migration 0052, because the alternative is a free-text
column proven to accept the empty string.

Zones live in the `public` schema rather than the dedicated one the design doc
asks for. Both schema-parity guards in this repo hardcode `schema='public'`, so
a separate schema would silently remove the only column-parity check we have.
The consequence is recorded honestly in the plan: design-doc invariant I-2 (the
vision role holds zero write grants on clinical tables) is NOT implemented.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import (Boolean, CheckConstraint, ForeignKey, Integer, String,
                        UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared.models.base import AuditedBase

# The two sites the rest of the system already enforces, via three live CHECK
# constraints (ck_surv_obs_site, ck_rf_ap_site, ck_rf_fp_site). A zone belongs
# to one of them; inventing a sites table here would break those joins.
SITES = ("pharmacy", "depot")

# From design doc 3.1. 'credentialed' (Z-STAFFDOOR) and 'prohibited' (toilets,
# changing rooms, prayer room, any framing where a screen or prescription is
# legible) are in the doc's own table and are the two most commonly dropped.
ZONE_CLASSES = ("public", "service", "restricted", "high_risk", "safety",
                "credentialed", "prohibited")

# Classes whose rules run per-occurrence rather than in a daily batch. WH-03
# exists because controlled substances justify the false-positive cost that
# WH-02 deliberately refuses to pay in a general aisle.
REAL_TIME_CLASSES = ("high_risk",)


class VisionZone(AuditedBase):
    """A zone: the unit of policy. Rules, retention and access attach here.

    AuditedBase rather than TimestampedBase: there are a handful of rows per
    pharmacy, changing retention_days is a compliance-relevant act that must
    name a person, and is_deleted lets a decommissioned zone be retired in
    place so last month's observations still resolve their zone. The
    high-volume vision tables in phase 5b use TimestampedBase instead, because
    soft-delete purges nothing and a retention proof reporting bytes_purged
    against tombstones would be a lie about data still on disk.
    """

    __tablename__ = "vision_zone"

    pharmacy_id: Mapped[UUID] = mapped_column(
        ForeignKey("pharmacies.id"), nullable=False, index=True)
    site: Mapped[str] = mapped_column(String(20), nullable=False)
    # The public identity. surveillance_observations.zone_id references this
    # triple, so it is varchar(40) to match that column exactly.
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name_fa: Mapped[str] = mapped_column(String(200), nullable=False)
    zone_class: Mapped[str] = mapped_column(String(16), nullable=False)

    # Floor-plan polygon as {"points": [[x, y], ...]} in metres, the same frame
    # as the RF access-point layout. NULL until the coverage survey reaches
    # this zone — a fabricated rectangle would be a measurement nobody took.
    polygon: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # NULL, deliberately. Nothing purges yet — verified: no partitioning, no
    # pg_cron, no purge job anywhere in the repo — so a number here would be a
    # compliance claim the system cannot honour. Phase 7 enforces it.
    retention_days: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True)

    # {"tz": "...", "windows": [{"dow": 0-6, "from": "HH:MM", "to": "HH:MM"}]}
    # NULL until measured. No schedule evaluator exists anywhere in this repo
    # (no business-hours, holiday, weekend or shift primitive), so a rule whose
    # firing depends on this must REFUSE TO FIRE while it is NULL rather than
    # fall back to an invented window.
    armed_schedule: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True)

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # A rename is a new row pointing back at the old one. Zone codes are
    # immutable once observed: ON UPDATE CASCADE was measured to silently
    # rewrite the zone of historical observations.
    superseded_by: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("vision_zone.id"), nullable=True)

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "site", "code",
                         name="uq_zone_code_per_site"),
        # Spelled out rather than interpolated from SITES: the repr of a
        # one-element tuple is ('pharmacy',) which is not valid SQL, and this
        # constraint must not become fragile if the vocabulary ever shrinks.
        CheckConstraint("site IN ('pharmacy', 'depot')", name="ck_zone_site"),
        CheckConstraint(
            "zone_class IN ('public', 'service', 'restricted', 'high_risk', "
            "'safety', 'credentialed', 'prohibited')",
            name="ck_zone_class"),
        # An empty-string zone is not NULL and would defeat every
        # `zone_id IS NULL` "unzoned" branch downstream.
        CheckConstraint("code <> '' AND code = btrim(code)",
                        name="ck_zone_code_shape"),
        CheckConstraint("retention_days IS NULL OR retention_days > 0",
                        name="ck_zone_retention_positive"),
    )
