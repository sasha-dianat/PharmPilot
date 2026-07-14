"""drug_enrichments — owner-approved web-researched drug details reference"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "drug_enrichments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("key", sa.String(300), nullable=False),
        sa.Column("raw_name", sa.String(300), nullable=False),
        sa.Column("irc", sa.String(32), nullable=True),
        sa.Column("generic_name", sa.String(200), nullable=True),
        sa.Column("brand_name", sa.String(200), nullable=True),
        sa.Column("manufacturer", sa.String(200), nullable=True),
        sa.Column("country", sa.String(80), nullable=True),
        sa.Column("dosage_form", sa.String(80), nullable=True),
        sa.Column("strengths", JSONB, nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("sources", JSONB, nullable=True),
        sa.Column("researched_by", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("decided_by", UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_drug_enrichments_key", "drug_enrichments", ["key"], unique=True)
    op.create_index("ix_drug_enrichments_irc", "drug_enrichments", ["irc"])
    op.create_index("ix_drug_enrichments_status", "drug_enrichments", ["status"])


def downgrade() -> None:
    op.drop_index("ix_drug_enrichments_status", table_name="drug_enrichments")
    op.drop_index("ix_drug_enrichments_irc", table_name="drug_enrichments")
    op.drop_index("ix_drug_enrichments_key", table_name="drug_enrichments")
    op.drop_table("drug_enrichments")
