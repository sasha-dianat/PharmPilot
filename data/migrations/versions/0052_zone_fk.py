"""a zone id that names something

surveillance_observations.zone_id has been varchar(40) with no CHECK and no
foreign key since migration 0032. Proven live in a rolled-back transaction:
it accepted 'Z-CAGE', 'totally made up zone' and the EMPTY STRING. Only
length >= 41 was refused, and that is varchar truncation rather than a domain
check. Its writer validated `site` and `fusion_decision` and passed zone_id
straight through.

The foreign key is composite — (pharmacy_id, site, zone_id) referencing
vision_zone (pharmacy_id, site, code) — because zone_id is varchar(40) and
cannot reference a uuid primary key. MATCH SIMPLE, the default, keeps a NULL
zone_id legal, which is what "unzoned" must stay.

Deliberately NO ON UPDATE CASCADE. Measured: with CASCADE, renaming a zone
silently rewrites the zone of every historical observation, and where the child
is append-only the UPDATE fails while naming the wrong table. Zone codes are
immutable once observed; a rename is a new row with superseded_by.

This is free exactly once. surveillance_observations has 0 rows today, so
there is nothing to backfill and nothing to reject. It is never free again.

Revision ID: 0052
Revises: 0051
"""
from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The CHECK goes on first, so that a stray '' surfaces as a violation
    # naming the SHAPE rather than as a foreign-key error that would send
    # someone looking for a missing zone row.
    op.create_check_constraint(
        "ck_surv_obs_zone_shape", "surveillance_observations",
        "zone_id IS NULL OR (zone_id <> '' AND zone_id = btrim(zone_id))")
    op.create_foreign_key(
        "fk_surv_obs_zone", "surveillance_observations", "vision_zone",
        ["pharmacy_id", "site", "zone_id"], ["pharmacy_id", "site", "code"])


def downgrade() -> None:
    op.drop_constraint("fk_surv_obs_zone", "surveillance_observations",
                       type_="foreignkey")
    op.drop_constraint("ck_surv_obs_zone_shape", "surveillance_observations",
                       type_="check")
