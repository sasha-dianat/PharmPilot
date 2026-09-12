"""admit identify_manually as a fusion decision

The escalation ladder replaces fuse()'s NO_MATCH dead end with an outcome that
mints a provisional identity and asks the counter for the name and national
code. Two things had to change together, and missing either leaves the column
unusable:

  - ck_surv_obs_fusion_decision admitted only auto|review|no_match, so Postgres
    would reject the new value on the constraint;
  - fusion_decision was VARCHAR(12) and 'identify_manually' is 17 characters, so
    it would be rejected on width even with the constraint widened. Widening the
    constraint alone produced a column that admits a value it cannot store.

Revision ID: 0046
Revises: 0045
"""
import sqlalchemy as sa
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None

_OLD = "fusion_decision IS NULL OR fusion_decision IN ('auto', 'review', 'no_match')"
_NEW = ("fusion_decision IS NULL OR fusion_decision IN "
        "('auto', 'review', 'no_match', 'identify_manually')")


def upgrade() -> None:
    # Drop the constraint first: it references the column being altered.
    op.drop_constraint("ck_surv_obs_fusion_decision",
                       "surveillance_observations", type_="check")
    op.alter_column("surveillance_observations", "fusion_decision",
                    existing_type=sa.String(12), type_=sa.String(24),
                    existing_nullable=True)
    op.create_check_constraint(
        "ck_surv_obs_fusion_decision", "surveillance_observations", _NEW)


def downgrade() -> None:
    op.drop_constraint("ck_surv_obs_fusion_decision",
                       "surveillance_observations", type_="check")
    # Any identify_manually rows would not fit the narrower column; clear them
    # rather than fail the downgrade half-way through.
    op.execute("UPDATE surveillance_observations SET fusion_decision = NULL "
               "WHERE fusion_decision = 'identify_manually'")
    op.alter_column("surveillance_observations", "fusion_decision",
                    existing_type=sa.String(24), type_=sa.String(12),
                    existing_nullable=True)
    op.create_check_constraint(
        "ck_surv_obs_fusion_decision", "surveillance_observations", _OLD)
