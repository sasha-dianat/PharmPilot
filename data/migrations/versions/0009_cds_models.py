"""cds_models

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
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
    op.add_column("patients", sa.Column("weight_kg", sa.Numeric(5, 2), nullable=True))
    op.add_column("patients", sa.Column("pregnancy_status", sa.String(30), nullable=True))
    op.add_column("patients", sa.Column("renal_function", sa.String(40), nullable=True))
    op.add_column("patients", sa.Column("hepatic_status", sa.String(40), nullable=True))
    op.add_column("patients", sa.Column("conditions", postgresql.JSONB(), nullable=True))

    op.create_table(
        "medications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("drug_name", sa.String(255), nullable=False),
        sa.Column("normalized_name", sa.String(255), nullable=False),
        sa.Column("strength", sa.String(100), nullable=True),
        sa.Column("dose", sa.String(100), nullable=True),
        sa.Column("route", sa.String(50), nullable=True),
        sa.Column("frequency", sa.String(100), nullable=True),
        sa.Column("indication", sa.String(255), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("stop_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("source", sa.String(40), nullable=False, server_default="patient_reported"),
        sa.Column("confidence_score", sa.Numeric(4, 3), nullable=True),
        *_audit_columns(),
    )
    op.create_index("ix_medications_pharmacy_id", "medications", ["pharmacy_id"])
    op.create_index("ix_medications_patient_id", "medications", ["patient_id"])

    op.create_table(
        "clinical_alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("module", sa.String(40), nullable=False),
        sa.Column("rule_id", sa.String(80), nullable=False),
        sa.Column("severity", sa.String(12), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("patient_specific_factors", postgresql.JSONB(), nullable=True),
        sa.Column("missing_data", postgresql.JSONB(), nullable=True),
        sa.Column("suggested_actions", postgresql.JSONB(), nullable=True),
        sa.Column("evidence_sources", postgresql.JSONB(), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        *_audit_columns(),
    )
    op.create_index("ix_clinical_alerts_patient_id", "clinical_alerts", ["patient_id"])

    op.create_table(
        "clinical_audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("module", sa.String(40), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("output_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("rules_triggered", postgresql.JSONB(), nullable=False),
        sa.Column("model_version", sa.String(40), nullable=False),
        *_audit_columns(),
    )
    op.create_index("ix_clinical_audit_logs_patient_id", "clinical_audit_logs", ["patient_id"])


def downgrade() -> None:
    op.drop_index("ix_clinical_audit_logs_patient_id", table_name="clinical_audit_logs")
    op.drop_table("clinical_audit_logs")

    op.drop_index("ix_clinical_alerts_patient_id", table_name="clinical_alerts")
    op.drop_table("clinical_alerts")

    op.drop_index("ix_medications_patient_id", table_name="medications")
    op.drop_index("ix_medications_pharmacy_id", table_name="medications")
    op.drop_table("medications")

    op.drop_column("patients", "conditions")
    op.drop_column("patients", "hepatic_status")
    op.drop_column("patients", "renal_function")
    op.drop_column("patients", "pregnancy_status")
    op.drop_column("patients", "weight_kg")
