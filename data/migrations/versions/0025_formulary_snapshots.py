"""formulary_snapshots — the observed layer (X4).

Every staged harvest keeps its FULL row set (uncapped, unlike run.unmatched's
200-sample) with the raw source row. Snapshots are append-only observations;
the decided layer (crosswalk/overrides) never lives here, so re-imports can be
replayed and diffed against decisions at any time.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "formulary_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("run_id", UUID(as_uuid=True), index=True, nullable=True),
        sa.Column("insurer", sa.String(20), nullable=False),
        sa.Column("source_code", sa.String(64), nullable=True),
        sa.Column("raw_name", sa.String(300), nullable=True),
        sa.Column("reference_price", sa.BigInteger(), nullable=True),
        sa.Column("share_pct", sa.Numeric(6, 2), nullable=True),
        sa.Column("covered", sa.Boolean(), nullable=True),
        sa.Column("row", JSONB, nullable=True),          # full raw source row
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_fsnap_insurer_run", "formulary_snapshots", ["insurer", "run_id"])


def downgrade() -> None:
    op.drop_table("formulary_snapshots")
