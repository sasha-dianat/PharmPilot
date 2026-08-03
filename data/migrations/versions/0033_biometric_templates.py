"""Many biometric templates per person, per modality — the durable vector store.

`biometric_identities` holds one `face_embedding_enc` and one
`gait_signature_enc` per person: a single template per modality. That is the
binding limit on identification accuracy. The cheapest reliable way to improve
recognition is to enrol the same person several times — different day, distance,
lighting, head angle — and take the best match across their templates. With one
slot each new capture must overwrite the last, so enrolment can never improve,
and one bad capture degrades that person permanently with no way back.

This table makes enrolment cumulative and adds gait as a first-class modality
rather than a single opaque column. Each template carries its own quality,
provenance and model version, so a bad capture can be retired individually and a
model upgrade can be rolled through without discarding the gallery.

The old columns are deliberately left in place: nothing is migrated or dropped,
so the existing resolver keeps working untouched while the new store is filled
alongside it.

Stacks on 0032 (surveillance observations). This table stores the templates;
`services.biometric.fusion` decides what a match across modalities means. The
two are deliberately separate — storage should not know about decision policy.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "biometric_templates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("pharmacy_id", UUID(as_uuid=True),
                  sa.ForeignKey("pharmacies.id"), nullable=False, index=True),
        sa.Column("identity_id", UUID(as_uuid=True),
                  sa.ForeignKey("biometric_identities.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        # face | gait — more may follow (voice, iris) without a schema change.
        sa.Column("modality", sa.String(16), nullable=False, index=True),
        sa.Column("embedding", sa.LargeBinary, nullable=False),
        sa.Column("dim", sa.Integer, nullable=False),
        # Which extractor produced it. A gallery mixing model versions silently
        # compares vectors from different spaces, which is worse than useless.
        sa.Column("model_version", sa.String(64), nullable=False),
        # Capture quality in [0,1] — a weak template should not be discarded,
        # but a reviewer must be able to see it was weak.
        sa.Column("quality", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("capture_context", JSONB, server_default="{}"),
        sa.Column("enrolled_by_id", UUID(as_uuid=True), nullable=True),
        # Retirement instead of deletion: an identification made last year should
        # still be explainable from the templates that existed at the time.
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_reason", sa.String(240), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("dim > 0 AND dim <= 4096", name="ck_template_dim"),
        sa.CheckConstraint("quality >= 0 AND quality <= 1", name="ck_template_quality"),
        sa.CheckConstraint("modality IN ('face','gait','voice','iris')",
                           name="ck_template_modality"),
    )
    # The load path is "every live template for this pharmacy and modality".
    op.create_index("ix_bio_templates_active", "biometric_templates",
                    ["pharmacy_id", "modality", "retired_at"])

    # Measured genuine/impostor score distributions, per modality and model.
    # Fusion converts a raw cosine into a likelihood ratio, and that conversion
    # is meaningless without distributions measured on this population and these
    # cameras — so they are stored, versioned and auditable rather than hard-coded.
    op.create_table(
        "biometric_score_stats",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("pharmacy_id", UUID(as_uuid=True),
                  sa.ForeignKey("pharmacies.id"), nullable=False, index=True),
        sa.Column("modality", sa.String(16), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("genuine_mean", sa.Float, nullable=False),
        sa.Column("genuine_std", sa.Float, nullable=False),
        sa.Column("impostor_mean", sa.Float, nullable=False),
        sa.Column("impostor_std", sa.Float, nullable=False),
        sa.Column("samples", sa.Integer, nullable=False, server_default="0"),
        sa.Column("measured_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("pharmacy_id", "modality", "model_version",
                            name="uq_score_stats_per_model"),
        sa.CheckConstraint("genuine_std > 0 AND impostor_std > 0",
                           name="ck_stats_positive_spread"),
    )


def downgrade() -> None:
    op.drop_table("biometric_score_stats")
    op.drop_index("ix_bio_templates_active", table_name="biometric_templates")
    op.drop_table("biometric_templates")
