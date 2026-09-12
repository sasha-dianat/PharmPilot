"""surveillance observations — fused biometric identity + RF device position

Revision ID: 0032
Revises: 0031
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "surveillance_observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("pharmacies.id"), nullable=False, index=True),

        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("site", sa.String(20), nullable=False, index=True),
        sa.Column("zone_id", sa.String(40), nullable=True, index=True),
        sa.Column("action_type", sa.String(20), nullable=False, index=True),

        # Biometric side — a PROPOSAL with its working, never an assertion.
        sa.Column("biometric_identity_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("biometric_identities.id"), nullable=True, index=True),
        sa.Column("fusion_decision", sa.String(12), nullable=True),
        sa.Column("fusion_confidence", sa.Numeric(6, 5), nullable=True),
        sa.Column("fusion_margin", sa.Numeric(6, 5), nullable=True),
        sa.Column("fusion_detail", postgresql.JSONB, nullable=True),
        sa.Column("modalities_used", postgresql.JSONB, nullable=True),
        sa.Column("fusion_explanation", sa.Text, nullable=True),

        # RF side — a DEVICE fix. rf_uncertainty_m is required alongside a
        # coordinate; see the CHECK constraint below.
        sa.Column("rf_device_ref", sa.String(64), nullable=True, index=True),
        sa.Column("rf_x", sa.Numeric(8, 2), nullable=True),
        sa.Column("rf_y", sa.Numeric(8, 2), nullable=True),
        sa.Column("rf_uncertainty_m", sa.Numeric(6, 2), nullable=True),
        sa.Column("rf_method", sa.String(20), nullable=True),
        sa.Column("rf_ap_count", sa.Integer, nullable=True),

        sa.Column("camera_id", sa.String(40), nullable=True),
        sa.Column("source", sa.String(30), nullable=False, server_default="edge"),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded", sa.Boolean, nullable=False, server_default="false"),

        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),

        # A coordinate without its error bar invites false precision, which is
        # how a positioning system ends up cited as evidence it cannot support.
        sa.CheckConstraint(
            "(rf_x IS NULL AND rf_y IS NULL) OR rf_uncertainty_m IS NOT NULL",
            name="ck_surv_obs_rf_uncertainty_required"),
        sa.CheckConstraint(
            "site IN ('pharmacy', 'depot')", name="ck_surv_obs_site"),
        sa.CheckConstraint(
            "fusion_decision IS NULL OR fusion_decision IN "
            "('auto', 'review', 'no_match')", name="ck_surv_obs_fusion_decision"),
    )
    op.create_index("ix_surv_obs_site_time", "surveillance_observations",
                    ["site", "observed_at"])
    op.create_index("ix_surv_obs_zone_time", "surveillance_observations",
                    ["zone_id", "observed_at"])
    op.create_index("ix_surv_obs_identity_time", "surveillance_observations",
                    ["biometric_identity_id", "observed_at"])


def downgrade() -> None:
    op.drop_index("ix_surv_obs_identity_time", table_name="surveillance_observations")
    op.drop_index("ix_surv_obs_zone_time", table_name="surveillance_observations")
    op.drop_index("ix_surv_obs_site_time", table_name="surveillance_observations")
    op.drop_table("surveillance_observations")
