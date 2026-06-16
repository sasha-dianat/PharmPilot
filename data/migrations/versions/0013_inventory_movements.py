"""inventory movements audit ledger

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-16

Append-only ledger for non-dispense stock changes (adjustments, returns,
damage/expiry write-offs, recall removals). Chains 0012 (depot) on this branch.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inventory_movements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("ndc11", sa.String(11), nullable=False),
        sa.Column("inventory_lot_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("inventory_lots.id"), nullable=True),
        sa.Column("movement_type", sa.String(24), nullable=False),
        sa.Column("reason", sa.String(120), nullable=False),
        sa.Column("quantity_before", sa.Numeric(10, 3), nullable=False),
        sa.Column("quantity_after", sa.Numeric(10, 3), nullable=False),
        sa.Column("quantity_delta", sa.Numeric(10, 3), nullable=False),
        sa.Column("reference", sa.String(120), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_inventory_movements_pharmacy_id", "inventory_movements", ["pharmacy_id"])
    op.create_index("ix_inventory_movements_ndc11", "inventory_movements", ["ndc11"])
    op.create_index("ix_inventory_movements_inventory_lot_id", "inventory_movements", ["inventory_lot_id"])
    op.create_index("ix_inventory_movements_movement_type", "inventory_movements", ["movement_type"])


def downgrade() -> None:
    op.drop_index("ix_inventory_movements_movement_type", table_name="inventory_movements")
    op.drop_index("ix_inventory_movements_inventory_lot_id", table_name="inventory_movements")
    op.drop_index("ix_inventory_movements_ndc11", table_name="inventory_movements")
    op.drop_index("ix_inventory_movements_pharmacy_id", table_name="inventory_movements")
    op.drop_table("inventory_movements")
