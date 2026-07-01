"""drug catalog (IRC-keyed priced product list)

Revision ID: 0016
Revises: 0015
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "drug_catalog",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("irc", sa.String(32), nullable=False),
        sa.Column("erx_code", sa.String(32), nullable=True),
        sa.Column("gtin", sa.String(20), nullable=True),
        sa.Column("name_fa", sa.String(300), nullable=False),
        sa.Column("name_en", sa.String(300), nullable=True),
        sa.Column("generic_name", sa.String(200), nullable=False),
        sa.Column("ingredient_key", sa.String(300), nullable=False),
        sa.Column("dosage_form", sa.String(80), nullable=True),
        sa.Column("strength", sa.String(80), nullable=True),
        sa.Column("brand_name", sa.String(200), nullable=True),
        sa.Column("manufacturer", sa.String(200), nullable=True),
        sa.Column("atc", sa.String(16), nullable=True),
        sa.Column("package_count", sa.Integer(), nullable=True),
        sa.Column("is_generic", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_otc", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("category", sa.String(20), nullable=False, server_default="drug"),
        sa.Column("announced_price", sa.Numeric(14, 0), nullable=True),
        sa.Column("announced_price_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_invoice_price", sa.Numeric(14, 0), nullable=True),
        sa.Column("last_invoice_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("coverage", postgresql.JSONB(), nullable=True),
        sa.Column("source", sa.String(40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_drug_catalog_irc", "drug_catalog", ["irc"], unique=True)
    op.create_index("ix_drug_catalog_erx_code", "drug_catalog", ["erx_code"])
    op.create_index("ix_drug_catalog_ingredient_key", "drug_catalog", ["ingredient_key"])
    op.create_index("ix_drug_catalog_atc", "drug_catalog", ["atc"])


def downgrade() -> None:
    op.drop_table("drug_catalog")
