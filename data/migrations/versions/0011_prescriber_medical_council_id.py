"""prescriber medical_council_id

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-14
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("prescribers", sa.Column("medical_council_id", sa.String(30), nullable=True))
    op.create_index("ix_prescribers_medical_council_id", "prescribers", ["medical_council_id"])


def downgrade() -> None:
    op.drop_index("ix_prescribers_medical_council_id", table_name="prescribers")
    op.drop_column("prescribers", "medical_council_id")
