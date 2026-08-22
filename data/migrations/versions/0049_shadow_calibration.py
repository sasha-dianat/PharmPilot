"""a calibration may shadow an active model without replacing it

Without this the only way to evaluate a candidate model is to promote it, so the
first evidence that it is worse arrives as wrong identifications against real
people.

`shadow_of` names the active model version a row is a candidate to replace.
NULL means the row IS the active calibration — so the ordinary lookup filters
on `shadow_of IS NULL` and an unpromoted model can never set live thresholds.

Revision ID: 0049
Revises: 0048
"""
import sqlalchemy as sa
from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("biometric_score_stats",
                  sa.Column("shadow_of", sa.String(64), nullable=True))
    # Partial index: the active lookup is the hot path and it only ever wants
    # rows where shadow_of IS NULL.
    op.create_index("ix_score_stats_active", "biometric_score_stats",
                    ["pharmacy_id", "modality", "stratum"],
                    postgresql_where=sa.text("shadow_of IS NULL"))


def downgrade() -> None:
    op.drop_index("ix_score_stats_active", table_name="biometric_score_stats")
    # Shadow rows have nowhere to live once the column is gone, and leaving them
    # would silently promote every candidate to active.
    op.execute("DELETE FROM biometric_score_stats WHERE shadow_of IS NOT NULL")
    op.drop_column("biometric_score_stats", "shadow_of")
