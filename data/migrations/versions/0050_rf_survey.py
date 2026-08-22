"""access-point registry and surveyed radio map

The RF engine has had weighted-kNN fingerprinting since phase 1 and it has never
run: the live endpoint passes RadioMap([]), so locate() always falls through to
trilateration, whose free-space assumption fails indoors at 5-15m error. These
two tables are the data it has been missing.

RSSI is JSONB rather than one row per AP because a fingerprint is read as a
whole vector — the kNN distance spans every AP the probe and the fingerprint
share — so splitting it costs a join on the hot path and buys nothing.

Revision ID: 0050
Revises: 0049
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID


revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rf_access_points",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("pharmacy_id", UUID(as_uuid=True),
                  sa.ForeignKey("pharmacies.id"), nullable=False, index=True),
        sa.Column("site", sa.String(20), nullable=False),
        sa.Column("ap_id", sa.String(64), nullable=False),
        sa.Column("x", sa.Numeric(8, 2), nullable=False),
        sa.Column("y", sa.Numeric(8, 2), nullable=False),
        sa.Column("tx_power_dbm", sa.Numeric(6, 2), nullable=False,
                  server_default="-40"),
        # Retired rather than deleted: a fix computed last month stays
        # explainable from the layout that existed when it was computed.
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        # The same BSSID may legitimately appear at two sites; a duplicate
        # within one site would give trilateration two contradictory positions.
        sa.UniqueConstraint("pharmacy_id", "site", "ap_id",
                            name="uq_rf_ap_per_site"),
        sa.CheckConstraint("site IN ('pharmacy', 'depot')", name="ck_rf_ap_site"),
    )

    op.create_table(
        "rf_fingerprints",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("pharmacy_id", UUID(as_uuid=True),
                  sa.ForeignKey("pharmacies.id"), nullable=False, index=True),
        sa.Column("site", sa.String(20), nullable=False),
        sa.Column("x", sa.Numeric(8, 2), nullable=False),
        sa.Column("y", sa.Numeric(8, 2), nullable=False),
        sa.Column("rssi", JSONB, nullable=False),
        # A survey point that heard one AP cannot constrain a position. Stored
        # so a thin fingerprint is filterable rather than silently weightless.
        sa.Column("ap_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("surveyed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("surveyed_by", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("site IN ('pharmacy', 'depot')",
                           name="ck_rf_fp_site"),
        sa.CheckConstraint("ap_count >= 0", name="ck_rf_fp_ap_count"),
    )
    op.create_index("ix_rf_fingerprints_site", "rf_fingerprints",
                    ["pharmacy_id", "site"])


def downgrade() -> None:
    op.drop_index("ix_rf_fingerprints_site", table_name="rf_fingerprints")
    op.drop_table("rf_fingerprints")
    op.drop_table("rf_access_points")
