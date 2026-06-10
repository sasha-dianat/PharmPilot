"""
0005 – Back-office agent columns on claim_transactions
======================================================
Adds two columns required by AutoRebillAgent and AutoPAAgent:
  • auto_rebill_count  – how many times this claim has been auto-resubmitted
  • pa_initiated       – whether AutoPAAgent has started a PA for this claim

Revision: 0005
Depends on: 0004_intake_precompute
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE claim_transactions
            ADD COLUMN IF NOT EXISTS auto_rebill_count INTEGER NOT NULL DEFAULT 0
    """)
    op.execute("""
        ALTER TABLE claim_transactions
            ADD COLUMN IF NOT EXISTS pa_initiated BOOLEAN NOT NULL DEFAULT false
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_claim_transactions_auto_rebill
            ON claim_transactions (auto_rebill_count)
            WHERE status = 'rejected'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_claim_transactions_auto_rebill")
    op.execute("ALTER TABLE claim_transactions DROP COLUMN IF EXISTS pa_initiated")
    op.execute("ALTER TABLE claim_transactions DROP COLUMN IF EXISTS auto_rebill_count")
