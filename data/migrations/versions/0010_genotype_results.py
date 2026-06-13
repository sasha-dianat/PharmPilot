"""genotype_results

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def _audit_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "genotype_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("gene", sa.String(40), nullable=False),
        sa.Column("diplotype", sa.String(60), nullable=True),
        sa.Column("phenotype", sa.String(80), nullable=True),
        sa.Column("source", sa.String(40), nullable=False),
        *_audit_columns(),
    )
    op.create_index("ix_genotype_results_pharmacy_id", "genotype_results", ["pharmacy_id"])
    op.create_index("ix_genotype_results_patient_id", "genotype_results", ["patient_id"])


def downgrade() -> None:
    op.drop_index("ix_genotype_results_patient_id", table_name="genotype_results")
    op.drop_index("ix_genotype_results_pharmacy_id", table_name="genotype_results")
    op.drop_table("genotype_results")
