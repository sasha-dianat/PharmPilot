"""coverage_runs.diagnostics — per-run fetch diagnostics (attempts + summary)"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("coverage_runs", sa.Column("diagnostics", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("coverage_runs", "diagnostics")
