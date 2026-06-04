"""intake_precompute — cache council + triage on the prescription at intake

Lets the council/DUR/triage run asynchronously the moment an Rx enters the queue
so the pharmacist's review screen renders pre-computed analysis with zero wait.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-04
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE prescriptions ADD COLUMN IF NOT EXISTS triage_lane VARCHAR(10)")
    op.execute("ALTER TABLE prescriptions ADD COLUMN IF NOT EXISTS triage_result JSONB")
    op.execute("ALTER TABLE prescriptions ADD COLUMN IF NOT EXISTS council_cache JSONB")
    op.execute("ALTER TABLE prescriptions ADD COLUMN IF NOT EXISTS council_computed_at TIMESTAMPTZ")
    op.execute("ALTER TABLE prescriptions ADD COLUMN IF NOT EXISTS intake_analysis_status VARCHAR(20) NOT NULL DEFAULT 'pending'")
    op.execute("CREATE INDEX IF NOT EXISTS ix_prescriptions_triage_lane ON prescriptions (triage_lane)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_prescriptions_analysis_status ON prescriptions (intake_analysis_status)")


def downgrade() -> None:
    for col in ("triage_lane", "triage_result", "council_cache",
                "council_computed_at", "intake_analysis_status"):
        op.execute(f"ALTER TABLE prescriptions DROP COLUMN IF EXISTS {col}")
