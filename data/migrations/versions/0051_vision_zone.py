"""the zone registry

Nine zone vocabularies exist across this codebase and not one is validated.
Proven against the live schema in a rolled-back transaction: 'Z-CAGE', 'totally
made up zone' and the EMPTY STRING were all accepted into
surveillance_observations.zone_id; only length >= 41 was rejected, and that is
varchar truncation rather than a domain check.

This creates the registry. Migration 0052 makes surveillance_observations
reference it, which is free exactly once — that table has 0 rows today.

The key is the composite (pharmacy_id, site, code) rather than the uuid primary
key, because the column that must reference it is varchar(40) and a uuid
cannot. Retention and schedule are both NULL-able and both seeded NULL: nothing
in this repo purges, and no schedule evaluator exists anywhere, so a number in
either column would be a policy claim the system cannot honour.

Revision ID: 0051
Revises: 0050
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None

# Longest is 'credentialed' at 12; String(16) is the house headroom of
# max_len + 2 rounded up to a multiple of four. Migration 0046 is the worked
# example of getting this wrong: it widened a CHECK to admit a 17-character
# value while the column stayed VARCHAR(12).
_ZONE_CLASSES = ("public", "service", "restricted", "high_risk", "safety",
                 "credentialed", "prohibited")
_SITES = ("pharmacy", "depot")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    """Render an IN-list from the vocabulary, so the CHECK text and the width
    assertion above cannot drift apart by hand-typing."""
    quoted = ", ".join("'" + v + "'" for v in values)
    return f"{column} IN ({quoted})"


def upgrade() -> None:
    op.create_table(
        "vision_zone",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("pharmacy_id", UUID(as_uuid=True),
                  sa.ForeignKey("pharmacies.id"), nullable=False, index=True),
        sa.Column("site", sa.String(20), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name_fa", sa.String(200), nullable=False),
        sa.Column("zone_class", sa.String(16), nullable=False),
        sa.Column("polygon", JSONB, nullable=True),
        sa.Column("retention_days", sa.Integer, nullable=True),
        sa.Column("armed_schedule", JSONB, nullable=True),
        sa.Column("active", sa.Boolean, nullable=False,
                  server_default=sa.true()),
        sa.Column("superseded_by", UUID(as_uuid=True),
                  sa.ForeignKey("vision_zone.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
        # AuditedBase declares is_deleted with a PYTHON default only; without
        # this server_default a raw-SQL insert hits a NOT NULL violation. Two
        # live tables already carry that hole.
        sa.Column("is_deleted", sa.Boolean, nullable=False,
                  server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("pharmacy_id", "site", "code",
                            name="uq_zone_code_per_site"),
        sa.CheckConstraint(_in_list("site", _SITES), name="ck_zone_site"),
        sa.CheckConstraint(_in_list("zone_class", _ZONE_CLASSES),
                           name="ck_zone_class"),
        sa.CheckConstraint("code <> '' AND code = btrim(code)",
                           name="ck_zone_code_shape"),
        sa.CheckConstraint("retention_days IS NULL OR retention_days > 0",
                           name="ck_zone_retention_positive"),
    )


def downgrade() -> None:
    op.drop_table("vision_zone")
