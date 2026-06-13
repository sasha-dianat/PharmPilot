"""drop orphaned customer_patient_links

customer_patient_links was introduced in 0002_biometric_vault as the typed
customer-to-patient predecessor to the Phase 32 linking model. Migration
0003_iranian_identity superseded that design with the untyped person_links
graph, and this table was never wired to any durable code path. Drop it so the
migrated schema contains only live ORM tables plus intentional raw-SQL-only
tables.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS customer_patient_links")


def downgrade() -> None:
    op.create_table(
        "customer_patient_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("customer_identities.id"), nullable=False, index=True),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("patients.id"), nullable=False, index=True),
        sa.Column("relationship", sa.String(50), default="self"),
        # self | caregiver | guardian | family_member
        sa.Column("confidence", sa.Numeric(5, 4), default=1.0),
        sa.Column("linked_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("linked_by", sa.String(30), default="manual"),
        # manual | biometric | prescription | insurance | transcript
    )
