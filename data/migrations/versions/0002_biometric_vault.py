"""biometric_vault

Revision ID: 0002
Revises: 0001
Create Date: 2025-06-01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── biometric_vault_objects ────────────────────────────────────────────
    # Stores AES-256-GCM encrypted raw biometric evidence.
    # The encrypted_blob column is entirely opaque without the VMK.
    op.create_table(
        "biometric_vault_objects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("identity_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("biometric_identities.id"), nullable=False, index=True),
        sa.Column("object_type", sa.String(30), nullable=False),
        # encrypted_blob: AES-256-GCM(nonce||ciphertext||tag)
        sa.Column("encrypted_blob", sa.LargeBinary, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),   # SHA-256 of plaintext
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("camera_zone", sa.String(50), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        # Legal hold — prevents deletion
        sa.Column("legal_hold", sa.Boolean, server_default="false", nullable=False),
        sa.Column("legal_hold_reason", sa.Text, nullable=True),
        sa.Column("legal_hold_set_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_vault_identity_type", "biometric_vault_objects",
                    ["identity_id", "object_type"])

    # ── vault_access_log (append-only, tamper-evident) ────────────────────
    op.create_table(
        "vault_access_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("identity_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("vault_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(50), nullable=False),
        # Who accessed
        sa.Column("accessed_by_staff_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("accessed_by_role", sa.String(50), nullable=False),
        sa.Column("legal_reason", sa.Text, nullable=False),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("event_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False, index=True),
        sa.Column("object_types", postgresql.JSONB, nullable=False),
        sa.Column("export_reference", sa.String(200), nullable=True),
        # Tamper-evidence: HMAC-SHA256 of key event fields
        sa.Column("signature_hex", sa.String(64), nullable=False),
    )

    # Append-only enforcement: deny UPDATE and DELETE on vault_access_log
    op.execute("""
        CREATE RULE vault_access_log_no_update AS
            ON UPDATE TO vault_access_log DO INSTEAD NOTHING;
        CREATE RULE vault_access_log_no_delete AS
            ON DELETE TO vault_access_log DO INSTEAD NOTHING;
    """)

    # Legal-hold DELETE prevention on vault objects
    op.execute("""
        CREATE OR REPLACE FUNCTION prevent_vault_delete_on_hold()
        RETURNS TRIGGER AS $$
        BEGIN
            IF OLD.legal_hold = true THEN
                RAISE EXCEPTION 'Cannot delete biometric vault object under legal hold (id=%). Contact legal counsel.', OLD.id;
            END IF;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER vault_legal_hold_guard
            BEFORE DELETE ON biometric_vault_objects
            FOR EACH ROW EXECUTE FUNCTION prevent_vault_delete_on_hold();
    """)

    # ── customer_identities (separates customer from patient) ─────────────
    # Phase 32: a customer who walks in may map to 0, 1, or multiple patients
    op.create_table(
        "customer_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("pharmacies.id"), nullable=False, index=True),
        sa.Column("biometric_identity_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("biometric_identities.id"), nullable=True),
        # Temporary UUID customers who haven't been identified yet
        sa.Column("is_temporary", sa.Boolean, server_default="true"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )

    # Customer ↔ Patient links (one customer may be caregiver for multiple patients)
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


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS vault_legal_hold_guard ON biometric_vault_objects")
    op.execute("DROP FUNCTION IF EXISTS prevent_vault_delete_on_hold()")
    op.execute("DROP RULE IF EXISTS vault_access_log_no_update ON vault_access_log")
    op.execute("DROP RULE IF EXISTS vault_access_log_no_delete ON vault_access_log")
    op.drop_table("customer_patient_links")
    op.drop_table("customer_identities")
    op.drop_table("vault_access_log")
    op.drop_table("biometric_vault_objects")
