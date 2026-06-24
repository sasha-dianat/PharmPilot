"""physician responsibility letters

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "physician_letters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rx_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("prescriber_name", sa.String(200), nullable=False),
        sa.Column("prescriber_council_id", sa.String(40), nullable=True),
        sa.Column("pharmacist_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pharmacist_name", sa.String(200), nullable=False),
        sa.Column("pharmacist_license", sa.String(50), nullable=True),
        sa.Column("language", sa.String(8), nullable=False),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("model_version", sa.String(40), nullable=False),
        sa.Column("letter_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_physician_letters_patient_id", "physician_letters", ["patient_id"])
    op.create_index("ix_physician_letters_rx_id", "physician_letters", ["rx_id"])
    op.create_index("ix_physician_letters_pharmacy_id", "physician_letters", ["pharmacy_id"])


def downgrade() -> None:
    op.drop_table("physician_letters")
