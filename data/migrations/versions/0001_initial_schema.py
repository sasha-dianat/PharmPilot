"""initial_schema

Revision ID: 0001
Revises:
Create Date: 2025-06-01 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Extensions ─────────────────────────────────────────────────
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "pg_trgm"')

    # ── pharmacies ─────────────────────────────────────────────────
    op.create_table(
        "pharmacies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("npi", sa.String(10), nullable=True, unique=True),
        sa.Column("ncpdp_id", sa.String(10), nullable=True, unique=True),
        sa.Column("dea_number", sa.String(15), nullable=True),
        sa.Column("nabp_number", sa.String(10), nullable=True),
        sa.Column("address_line1", sa.String(255), nullable=True),
        sa.Column("address_line2", sa.String(100), nullable=True),
        sa.Column("city", sa.String(100), nullable=True),
        sa.Column("state", sa.String(2), nullable=True),
        sa.Column("zip_code", sa.String(10), nullable=True),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("fax", sa.String(20), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("timezone", sa.String(50), nullable=False, server_default="America/New_York"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── prescribers ────────────────────────────────────────────────
    op.create_table(
        "prescribers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("npi", sa.String(10), nullable=False, unique=True),
        sa.Column("dea_number", sa.String(15), nullable=True),
        sa.Column("dea_schedule_auth", sa.String(20), nullable=True),
        sa.Column("first_name", sa.String(100), nullable=False),
        sa.Column("last_name", sa.String(100), nullable=False),
        sa.Column("suffix", sa.String(20), nullable=True),
        sa.Column("specialty", sa.String(100), nullable=True),
        sa.Column("specialty_code", sa.String(10), nullable=True),
        sa.Column("address_line1", sa.String(255), nullable=True),
        sa.Column("city", sa.String(100), nullable=True),
        sa.Column("state", sa.String(2), nullable=True),
        sa.Column("zip_code", sa.String(10), nullable=True),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("fax", sa.String(20), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("npi_verified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("dea_verified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("dea_check_digit_valid", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_prescribers_npi", "prescribers", ["npi"])
    op.create_index("ix_prescribers_last_name", "prescribers", ["last_name"])
    op.create_index("ix_prescribers_dea_number", "prescribers", ["dea_number"])
    op.execute("CREATE INDEX ix_prescribers_last_name_trgm ON prescribers USING gin(last_name gin_trgm_ops)")

    # ── staff ──────────────────────────────────────────────────────
    op.create_table(
        "staff",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("username", sa.String(100), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("first_name", sa.String(100), nullable=False),
        sa.Column("last_name", sa.String(100), nullable=False),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column("pharmacist_license_number", sa.String(50), nullable=True),
        sa.Column("pharmacist_license_state", sa.String(2), nullable=True),
        sa.Column("npi", sa.String(10), nullable=True),
        sa.Column("dea_number", sa.String(15), nullable=True),
        sa.Column("epcs_enrolled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("epcs_identity_proofed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("epcs_biometric_identity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("totp_secret", sa.String(64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extra_permissions", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_staff_pharmacy_id", "staff", ["pharmacy_id"])
    op.create_index("ix_staff_email", "staff", ["email"])

    # ── staff_sessions ─────────────────────────────────────────────
    op.create_table(
        "staff_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("staff_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("token_jti", sa.String(64), nullable=False, unique=True),
        sa.Column("refresh_token_hash", sa.String(64), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("workstation_id", sa.String(100), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_staff_sessions_staff_id", "staff_sessions", ["staff_id"])
    op.create_index("ix_staff_sessions_token_jti", "staff_sessions", ["token_jti"])

    # ── patients ───────────────────────────────────────────────────
    op.create_table(
        "patients",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("first_name", sa.String(100), nullable=False),
        sa.Column("last_name", sa.String(100), nullable=False),
        sa.Column("date_of_birth", sa.Date(), nullable=False),
        sa.Column("gender", sa.String(1), nullable=False),
        sa.Column("ssn_last4", sa.String(4), nullable=True),
        sa.Column("phone_primary", sa.String(20), nullable=True),
        sa.Column("phone_secondary", sa.String(20), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("preferred_contact_method", sa.String(20), nullable=False, server_default="phone"),
        sa.Column("preferred_language", sa.String(10), nullable=False, server_default="en"),
        sa.Column("address_line1", sa.String(255), nullable=True),
        sa.Column("address_line2", sa.String(100), nullable=True),
        sa.Column("city", sa.String(100), nullable=True),
        sa.Column("state", sa.String(2), nullable=True),
        sa.Column("zip_code", sa.String(10), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("is_caregiver_account", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("primary_patient_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("patients.id"), nullable=True),
        sa.Column("biometric_identity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("biometric_enrolled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("ai_profile_notes", postgresql.JSONB(), nullable=True),
        sa.Column("communication_preferences", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_patients_pharmacy_id", "patients", ["pharmacy_id"])
    op.create_index("ix_patients_biometric_identity_id", "patients", ["biometric_identity_id"])
    op.execute("CREATE INDEX ix_patients_last_name_trgm ON patients USING gin(last_name gin_trgm_ops)")
    op.execute("CREATE INDEX ix_patients_dob ON patients (date_of_birth)")

    # ── patient_allergies ──────────────────────────────────────────
    op.create_table(
        "patient_allergies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("allergen_type", sa.String(20), nullable=False),
        sa.Column("allergen_name", sa.String(255), nullable=False),
        sa.Column("allergen_ndc", sa.String(11), nullable=True),
        sa.Column("allergen_rxcui", sa.String(20), nullable=True),
        sa.Column("reaction", sa.String(500), nullable=True),
        sa.Column("severity", sa.String(20), nullable=False, server_default="unknown"),
        sa.Column("onset_date", sa.Date(), nullable=True),
        sa.Column("source", sa.String(50), nullable=False, server_default="patient_reported"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_patient_allergies_patient_id", "patient_allergies", ["patient_id"])

    # ── lab_results ────────────────────────────────────────────────
    op.create_table(
        "lab_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("test_name", sa.String(255), nullable=False),
        sa.Column("loinc_code", sa.String(20), nullable=True),
        sa.Column("value", sa.String(100), nullable=False),
        sa.Column("unit", sa.String(50), nullable=True),
        sa.Column("reference_range", sa.String(100), nullable=True),
        sa.Column("abnormal_flag", sa.String(10), nullable=True),
        sa.Column("result_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ordering_provider_npi", sa.String(10), nullable=True),
        sa.Column("source", sa.String(50), nullable=False, server_default="hl7_import"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_lab_results_patient_id", "lab_results", ["patient_id"])

    # ── clinical_notes ─────────────────────────────────────────────
    op.create_table(
        "clinical_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("note_type", sa.String(50), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source", sa.String(50), nullable=False, server_default="pharmacist"),
        sa.Column("ai_generated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("pharmacist_reviewed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("pharmacist_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_clinical_notes_patient_id", "clinical_notes", ["patient_id"])

    # ── insurance_plans ────────────────────────────────────────────
    op.create_table(
        "insurance_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("bin_number", sa.String(6), nullable=False),
        sa.Column("pcn", sa.String(10), nullable=True),
        sa.Column("plan_name", sa.String(255), nullable=False),
        sa.Column("payer_name", sa.String(255), nullable=False),
        sa.Column("plan_type", sa.String(30), nullable=False, server_default="commercial"),
        sa.Column("processor_control_number", sa.String(20), nullable=True),
        sa.Column("switch_id", sa.String(10), nullable=True),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_insurance_plans_bin_number", "insurance_plans", ["bin_number"])

    # ── patient_insurances ─────────────────────────────────────────
    op.create_table(
        "patient_insurances",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("insurance_plan_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("insurance_plans.id"), nullable=True),
        sa.Column("bin_number", sa.String(6), nullable=False),
        sa.Column("pcn", sa.String(10), nullable=True),
        sa.Column("group_number", sa.String(20), nullable=True),
        sa.Column("member_id", sa.String(50), nullable=False),
        sa.Column("person_code", sa.String(3), nullable=False, server_default="01"),
        sa.Column("cardholder_name", sa.String(255), nullable=True),
        sa.Column("cardholder_id", sa.String(50), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("termination_date", sa.Date(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_primary_eob", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_patient_insurances_patient_id", "patient_insurances", ["patient_id"])

    # ── prescriptions ──────────────────────────────────────────────
    op.create_table(
        "prescriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("prescriber_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("prescribers.id"), nullable=False),
        sa.Column("rx_number", sa.String(20), nullable=False, unique=True),
        sa.Column("ndc", sa.String(11), nullable=False),
        sa.Column("drug_name", sa.String(255), nullable=False),
        sa.Column("drug_strength", sa.String(100), nullable=True),
        sa.Column("drug_form", sa.String(100), nullable=True),
        sa.Column("sig_text", sa.Text(), nullable=False),
        sa.Column("sig_structured", postgresql.JSONB(), nullable=True),
        sa.Column("quantity_prescribed", sa.Numeric(10, 3), nullable=False),
        sa.Column("quantity_dispensed", sa.Numeric(10, 3), nullable=True),
        sa.Column("days_supply", sa.Integer(), nullable=False),
        sa.Column("refills_authorized", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("refills_remaining", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dea_schedule", sa.String(5), nullable=True),
        sa.Column("is_controlled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("written_date", sa.Date(), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("fill_date", sa.Date(), nullable=True),
        sa.Column("last_fill_date", sa.Date(), nullable=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("eprescribe_message_id", sa.String(100), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="intake"),
        sa.Column("claimed_by_staff_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("daw_code", sa.String(1), nullable=False, server_default="0"),
        sa.Column("dispense_as_written", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_partial_fill", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("clinical_override_reason", sa.String(500), nullable=True),
        sa.Column("acb_safety_report", postgresql.JSONB(), nullable=True),
        sa.Column("acb_consultation", postgresql.JSONB(), nullable=True),
        sa.Column("ai_risk_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("epcs_signature", sa.Text(), nullable=True),
        sa.Column("epcs_signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_prescriptions_pharmacy_id", "prescriptions", ["pharmacy_id"])
    op.create_index("ix_prescriptions_patient_id", "prescriptions", ["patient_id"])
    op.create_index("ix_prescriptions_rx_number", "prescriptions", ["rx_number"])
    op.create_index("ix_prescriptions_status", "prescriptions", ["status"])
    op.create_index("ix_prescriptions_ndc", "prescriptions", ["ndc"])

    # ── prescription_fills ─────────────────────────────────────────
    op.create_table(
        "prescription_fills",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("prescription_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("prescriptions.id"), nullable=False),
        sa.Column("fill_number", sa.Integer(), nullable=False),
        sa.Column("ndc_dispensed", sa.String(11), nullable=False),
        sa.Column("quantity_dispensed", sa.Numeric(10, 3), nullable=False),
        sa.Column("days_supply", sa.Integer(), nullable=False),
        sa.Column("fill_date", sa.Date(), nullable=False),
        sa.Column("dispensed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispensing_pharmacist_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("verifying_pharmacist_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lot_number", sa.String(50), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("pickup_confirmed_by_biometric", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("pickup_biometric_identity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pickup_signature", sa.Text(), nullable=True),
        sa.Column("picked_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_prescription_fills_prescription_id", "prescription_fills", ["prescription_id"])

    # ── dur_alerts ─────────────────────────────────────────────────
    op.create_table(
        "dur_alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("prescription_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("prescriptions.id"), nullable=False),
        sa.Column("alert_type", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("interacting_drug_ndc", sa.String(11), nullable=True),
        sa.Column("interacting_drug_name", sa.String(255), nullable=True),
        sa.Column("is_hard_stop", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("was_shown", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("was_overridden", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("override_reason", sa.String(500), nullable=True),
        sa.Column("overridden_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("overridden_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_grade", sa.String(5), nullable=True),
        sa.Column("citations", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_dur_alerts_prescription_id", "dur_alerts", ["prescription_id"])

    # ── rx_state_events (immutable audit) ──────────────────────────
    op.create_table(
        "rx_state_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("prescription_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("prescriptions.id"), nullable=False),
        sa.Column("from_status", sa.String(50), nullable=True),
        sa.Column("to_status", sa.String(50), nullable=False),
        sa.Column("triggered_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("triggered_by_type", sa.String(30), nullable=False, server_default="staff"),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("event_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_rx_state_events_prescription_id", "rx_state_events", ["prescription_id"])

    # ── claim_transactions ─────────────────────────────────────────
    op.create_table(
        "claim_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("fill_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("prescription_fills.id"), nullable=False),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("patient_insurance_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("patient_insurances.id"), nullable=True),
        sa.Column("sequence_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("transaction_type", sa.String(2), nullable=False, server_default="B1"),
        sa.Column("bin_number", sa.String(6), nullable=False),
        sa.Column("pcn", sa.String(10), nullable=True),
        sa.Column("group_number", sa.String(20), nullable=True),
        sa.Column("member_id", sa.String(50), nullable=False),
        sa.Column("person_code", sa.String(3), nullable=False, server_default="01"),
        sa.Column("ndc", sa.String(11), nullable=False),
        sa.Column("quantity", sa.Numeric(10, 3), nullable=False),
        sa.Column("days_supply", sa.Integer(), nullable=False),
        sa.Column("daw_code", sa.String(1), nullable=False, server_default="0"),
        sa.Column("date_of_service", sa.Date(), nullable=False),
        sa.Column("submission_clarification_code", sa.String(2), nullable=True),
        sa.Column("prior_auth_number", sa.String(20), nullable=True),
        sa.Column("prior_auth_type_code", sa.String(2), nullable=True),
        sa.Column("ingredient_cost_submitted", sa.Numeric(12, 2), nullable=False),
        sa.Column("dispensing_fee_submitted", sa.Numeric(8, 2), nullable=False, server_default="0.0"),
        sa.Column("usual_and_customary", sa.Numeric(12, 2), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("response_status", sa.String(1), nullable=True),
        sa.Column("reject_codes", postgresql.JSONB(), nullable=True),
        sa.Column("reject_messages", postgresql.JSONB(), nullable=True),
        sa.Column("ingredient_cost_paid", sa.Numeric(12, 2), nullable=True),
        sa.Column("dispensing_fee_paid", sa.Numeric(8, 2), nullable=True),
        sa.Column("total_amount_paid", sa.Numeric(12, 2), nullable=True),
        sa.Column("patient_pay_amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("sales_tax_paid", sa.Numeric(8, 2), nullable=True),
        sa.Column("basis_of_reimbursement", sa.String(2), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_time_ms", sa.Integer(), nullable=True),
        sa.Column("raw_request", sa.Text(), nullable=True),
        sa.Column("raw_response", sa.Text(), nullable=True),
        sa.Column("is_cob", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("cob_priority", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("other_payer_amount_paid", sa.Numeric(12, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_claim_transactions_fill_id", "claim_transactions", ["fill_id"])
    op.create_index("ix_claim_transactions_pharmacy_id", "claim_transactions", ["pharmacy_id"])
    op.create_index("ix_claim_transactions_status", "claim_transactions", ["status"])

    # ── era835_records ─────────────────────────────────────────────
    op.create_table(
        "era835_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("check_number", sa.String(50), nullable=True),
        sa.Column("check_date", sa.Date(), nullable=True),
        sa.Column("payment_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("payer_name", sa.String(255), nullable=True),
        sa.Column("claim_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("raw_835", sa.Text(), nullable=True),
        sa.Column("processed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("line_items", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── dir_fee_adjustments ────────────────────────────────────────
    op.create_table(
        "dir_fee_adjustments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("claim_transaction_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("claim_transactions.id"), nullable=True),
        sa.Column("adjustment_type", sa.String(50), nullable=False),
        sa.Column("adjustment_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("performance_period_start", sa.Date(), nullable=True),
        sa.Column("performance_period_end", sa.Date(), nullable=True),
        sa.Column("payer_name", sa.String(255), nullable=True),
        sa.Column("adjustment_reason", sa.String(500), nullable=True),
        sa.Column("posted_date", sa.Date(), nullable=False),
        sa.Column("era_record_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("era835_records.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_dir_fee_adjustments_pharmacy_id", "dir_fee_adjustments", ["pharmacy_id"])

    # ── drug_products ──────────────────────────────────────────────
    op.create_table(
        "drug_products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("ndc11", sa.String(11), nullable=False, unique=True),
        sa.Column("ndc10", sa.String(10), nullable=True),
        sa.Column("brand_name", sa.String(255), nullable=True),
        sa.Column("generic_name", sa.String(255), nullable=False),
        sa.Column("labeler_name", sa.String(255), nullable=True),
        sa.Column("strength", sa.String(100), nullable=True),
        sa.Column("dosage_form", sa.String(100), nullable=True),
        sa.Column("route", sa.String(100), nullable=True),
        sa.Column("package_size", sa.String(100), nullable=True),
        sa.Column("package_quantity", sa.Numeric(10, 3), nullable=True),
        sa.Column("gpi", sa.String(14), nullable=True),
        sa.Column("rxcui", sa.String(20), nullable=True),
        sa.Column("dea_schedule", sa.String(5), nullable=True),
        sa.Column("is_controlled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_hazardous", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("requires_refrigeration", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_generic", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_otc", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("awp_unit_price", sa.Numeric(12, 4), nullable=True),
        sa.Column("awp_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("wac_price", sa.Numeric(12, 4), nullable=True),
        sa.Column("fdb_drug_id", sa.String(50), nullable=True),
        sa.Column("medspan_drug_id", sa.String(50), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("discontinued", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("drug_db_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_drug_products_ndc11", "drug_products", ["ndc11"])
    op.create_index("ix_drug_products_generic_name", "drug_products", ["generic_name"])
    op.create_index("ix_drug_products_gpi", "drug_products", ["gpi"])
    op.create_index("ix_drug_products_rxcui", "drug_products", ["rxcui"])
    op.create_index("ix_drug_products_dea_schedule", "drug_products", ["dea_schedule"])
    op.execute("CREATE INDEX ix_drug_products_generic_name_trgm ON drug_products USING gin(generic_name gin_trgm_ops)")

    # ── purchase_orders ────────────────────────────────────────────
    op.create_table(
        "purchase_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("wholesaler", sa.String(50), nullable=False),
        sa.Column("po_number", sa.String(50), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("ordered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expected_delivery", sa.Date(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_cost", sa.Numeric(14, 2), nullable=True),
        sa.Column("ai_generated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── purchase_order_lines ───────────────────────────────────────
    op.create_table(
        "purchase_order_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("purchase_orders.id"), nullable=False),
        sa.Column("drug_product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("drug_products.id"), nullable=False),
        sa.Column("ndc11", sa.String(11), nullable=False),
        sa.Column("quantity_ordered", sa.Numeric(10, 3), nullable=False),
        sa.Column("quantity_received", sa.Numeric(10, 3), nullable=False, server_default="0.0"),
        sa.Column("unit_cost", sa.Numeric(12, 4), nullable=True),
        sa.Column("wholesaler_item_number", sa.String(50), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="ordered"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_purchase_order_lines_order_id", "purchase_order_lines", ["order_id"])

    # ── inventory_lots ─────────────────────────────────────────────
    op.create_table(
        "inventory_lots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("drug_product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("drug_products.id"), nullable=False),
        sa.Column("ndc11", sa.String(11), nullable=False),
        sa.Column("lot_number", sa.String(50), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("quantity_received", sa.Numeric(10, 3), nullable=False),
        sa.Column("quantity_on_hand", sa.Numeric(10, 3), nullable=False),
        sa.Column("quantity_reserved", sa.Numeric(10, 3), nullable=False, server_default="0.0"),
        sa.Column("unit_cost", sa.Numeric(12, 4), nullable=True),
        sa.Column("storage_location", sa.String(100), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purchase_order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("purchase_orders.id"), nullable=True),
        sa.Column("is_recalled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("recall_reference", sa.String(100), nullable=True),
        sa.Column("is_quarantined", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("serial_number", sa.String(100), nullable=True),
        sa.Column("transaction_history", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_inventory_lots_pharmacy_id", "inventory_lots", ["pharmacy_id"])
    op.create_index("ix_inventory_lots_ndc11", "inventory_lots", ["ndc11"])
    op.create_index("ix_inventory_lots_expiry_date", "inventory_lots", ["expiry_date"])

    # ── stock_levels ───────────────────────────────────────────────
    op.create_table(
        "stock_levels",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("ndc11", sa.String(11), nullable=False),
        sa.Column("drug_product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("drug_products.id"), nullable=False),
        sa.Column("quantity_on_hand", sa.Numeric(10, 3), nullable=False, server_default="0.0"),
        sa.Column("quantity_reserved", sa.Numeric(10, 3), nullable=False, server_default="0.0"),
        sa.Column("quantity_on_order", sa.Numeric(10, 3), nullable=False, server_default="0.0"),
        sa.Column("par_level_min", sa.Numeric(10, 3), nullable=True),
        sa.Column("par_level_max", sa.Numeric(10, 3), nullable=True),
        sa.Column("reorder_point", sa.Numeric(10, 3), nullable=True),
        sa.Column("reorder_quantity", sa.Numeric(10, 3), nullable=True),
        sa.Column("safety_stock", sa.Numeric(10, 3), nullable=True),
        sa.Column("avg_daily_demand", sa.Numeric(10, 4), nullable=True),
        sa.Column("stockout_probability_7d", sa.Numeric(5, 4), nullable=True),
        sa.Column("forecast_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_dispensed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_stock_levels_pharmacy_ndc", "stock_levels", ["pharmacy_id", "ndc11"])

    # ── receiving_records ──────────────────────────────────────────
    op.create_table(
        "receiving_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("purchase_order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("purchase_orders.id"), nullable=True),
        sa.Column("received_by_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invoice_number", sa.String(50), nullable=True),
        sa.Column("discrepancies", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── biometric_identities ───────────────────────────────────────
    op.create_table(
        "biometric_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("face_embedding_enc", sa.LargeBinary(), nullable=True),
        sa.Column("gait_signature_enc", sa.LargeBinary(), nullable=True),
        sa.Column("voice_print_enc", sa.LargeBinary(), nullable=True),
        sa.Column("fingerprint_template_enc", sa.LargeBinary(), nullable=True),
        sa.Column("faiss_index_id", sa.Integer(), nullable=True),
        sa.Column("identity_class", sa.String(50), nullable=False, server_default="unknown_visitor"),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=False, server_default="0.0"),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("patients.id"), nullable=True),
        sa.Column("staff_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_visits", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_watchlist_match", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("watchlist_source", sa.String(100), nullable=True),
        sa.Column("security_notes", sa.Text(), nullable=True),
        sa.Column("consent_security_monitoring", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("consent_patient_services", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("consent_recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consent_method", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_biometric_identities_pharmacy_id", "biometric_identities", ["pharmacy_id"])
    op.create_index("ix_biometric_identities_patient_id", "biometric_identities", ["patient_id"])
    op.create_index("ix_biometric_identities_faiss_index_id", "biometric_identities", ["faiss_index_id"])

    # ── pharmacy_visits ────────────────────────────────────────────
    op.create_table(
        "pharmacy_visits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("biometric_identity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("biometric_identities.id"), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("patients.id"), nullable=True),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("identity_class", sa.String(50), nullable=False),
        sa.Column("match_confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("profile_preloaded", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("acb_prescan_completed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("security_events", postgresql.JSONB(), nullable=True),
        sa.Column("has_transcripts", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_pharmacy_visits_pharmacy_id", "pharmacy_visits", ["pharmacy_id"])
    op.create_index("ix_pharmacy_visits_patient_id", "pharmacy_visits", ["patient_id"])

    # ── security_events ────────────────────────────────────────────
    op.create_table(
        "security_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("visit_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacy_visits.id"), nullable=True),
        sa.Column("biometric_identity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("biometric_identities.id"), nullable=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.Column("camera_footage_retained", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("footage_retention_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # ── audio_transcripts ──────────────────────────────────────────
    op.create_table(
        "audio_transcripts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("visit_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacy_visits.id"), nullable=True),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("patients.id"), nullable=True),
        sa.Column("audio_zone", sa.String(50), nullable=False),
        sa.Column("recording_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recording_ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("noise_cancellation_applied", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("noise_reduction_db", sa.Numeric(5, 2), nullable=True),
        sa.Column("audio_quality_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("whisper_model_used", sa.String(50), nullable=False, server_default="medium.en"),
        sa.Column("transcription_confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("full_transcript", sa.Text(), nullable=True),
        sa.Column("diarized_segments", postgresql.JSONB(), nullable=True),
        sa.Column("identified_speakers", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="recording"),
        sa.Column("extracted_clinical_info", postgresql.JSONB(), nullable=True),
        sa.Column("extracted_medications", postgresql.JSONB(), nullable=True),
        sa.Column("extracted_allergies", postgresql.JSONB(), nullable=True),
        sa.Column("extracted_conditions", postgresql.JSONB(), nullable=True),
        sa.Column("extracted_concerns", postgresql.JSONB(), nullable=True),
        sa.Column("clinical_action_items", postgresql.JSONB(), nullable=True),
        sa.Column("sentiment_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("pharmacist_reviewed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("pharmacist_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("profile_updates_applied", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("audio_file_key", sa.String(500), nullable=True),
        sa.Column("audio_encrypted", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_audio_transcripts_pharmacy_id", "audio_transcripts", ["pharmacy_id"])
    op.create_index("ix_audio_transcripts_patient_id", "audio_transcripts", ["patient_id"])
    op.create_index("ix_audio_transcripts_visit_id", "audio_transcripts", ["visit_id"])

    # ── profile_enrichment_actions ─────────────────────────────────
    op.create_table(
        "profile_enrichment_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("audio_transcripts.id"), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("action_type", sa.String(50), nullable=False),
        sa.Column("field_path", sa.String(200), nullable=False),
        sa.Column("extracted_value", postgresql.JSONB(), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("source_timestamp_seconds", sa.Numeric(10, 3), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("reviewed_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_profile_enrichment_actions_transcript_id", "profile_enrichment_actions", ["transcript_id"])
    op.create_index("ix_profile_enrichment_actions_patient_id", "profile_enrichment_actions", ["patient_id"])

    # ── Append-only audit log ──────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS phi_access_log (
            id              UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
            event_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            event_type      VARCHAR(100) NOT NULL,
            actor_id        UUID,
            actor_type      VARCHAR(30),
            patient_id      UUID,
            resource_type   VARCHAR(100),
            resource_id     UUID,
            action          VARCHAR(50),
            ip_address      INET,
            session_id      UUID,
            data_hash       VARCHAR(64),
            metadata        JSONB
        )
    """)


def downgrade() -> None:
    for table in [
        "profile_enrichment_actions", "audio_transcripts",
        "security_events", "pharmacy_visits", "biometric_identities",
        "receiving_records", "stock_levels", "inventory_lots",
        "purchase_order_lines", "purchase_orders", "drug_products",
        "dir_fee_adjustments", "era835_records", "claim_transactions",
        "rx_state_events", "dur_alerts", "prescription_fills", "prescriptions",
        "patient_insurances", "insurance_plans", "clinical_notes",
        "lab_results", "patient_allergies", "patients",
        "staff_sessions", "staff", "prescribers", "pharmacies",
        "phi_access_log",
    ]:
        op.drop_table(table)
