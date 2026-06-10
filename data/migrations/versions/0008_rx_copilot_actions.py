"""rx_copilot_actions — schema-managed Rx Copilot audit log

Formalizes the Rx Copilot audit table previously created via ad-hoc runtime
DDL inside rx_copilot.auto_advance(), bringing it under migration management
per the project's established Alembic convention.

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-07
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rx_copilot_actions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("rx_id", sa.Text(), nullable=False),
        sa.Column("step", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column("staff_id", sa.Text()),
        sa.Column("reverted", sa.Boolean(), server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("rx_copilot_actions")
