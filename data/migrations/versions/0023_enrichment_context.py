"""drug_enrichments.context — the formulary side of each researched item.

Captured at worklist build: which insurer's formulary the row came from, its
reference price / coverage share / covered flag, and the raw row snapshot —
so the review GUI can show formulary vs NFI-candidate side by side.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("drug_enrichments", sa.Column("context", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("drug_enrichments", "context")
