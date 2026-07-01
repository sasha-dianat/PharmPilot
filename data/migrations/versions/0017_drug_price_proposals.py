"""drug price proposals (daily-sync manager approval)

Revision ID: 0017
Revises: 0016
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "drug_price_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("irc", sa.String(32), nullable=False),
        sa.Column("name_fa", sa.String(300), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
        sa.Column("source", sa.String(40), nullable=True),
        sa.Column("current_announced", sa.Numeric(14, 0), nullable=True),
        sa.Column("proposed_announced", sa.Numeric(14, 0), nullable=True),
        sa.Column("current_invoice", sa.Numeric(14, 0), nullable=True),
        sa.Column("proposed_invoice", sa.Numeric(14, 0), nullable=True),
        sa.Column("current_effective", sa.Numeric(14, 0), nullable=False, server_default="0"),
        sa.Column("proposed_effective", sa.Numeric(14, 0), nullable=False, server_default="0"),
        sa.Column("delta", sa.Numeric(14, 0), nullable=False, server_default="0"),
        sa.Column("pct_change", sa.Numeric(8, 2), nullable=False, server_default="0"),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_drug_price_proposals_irc", "drug_price_proposals", ["irc"])
    op.create_index("ix_drug_price_proposals_status", "drug_price_proposals", ["status"])


def downgrade() -> None:
    op.drop_table("drug_price_proposals")
