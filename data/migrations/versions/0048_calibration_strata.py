"""calibration per occlusion stratum, and a shadow flag on templates

Two changes, both required before a stratified calibration can be stored.

biometric_score_stats was unique on (pharmacy_id, modality, model_version), so
a second stratum's calibration would overwrite the first and the table would
silently hold one row where it should hold four. The stratum joins the key, and
'all' remains as the pooled calibration that existed before strata.

Why this matters clinically: a single pooled ImpostorStats averages veiled
faces in with unoccluded ones, and the resulting threshold is too LOW for the
veiled subset — so the FPIR guarantee fails silently for exactly the group it
most affects.

biometric_templates gains `shadow`: a candidate model must be enrollable and
measurable without its templates entering the voting index.

Revision ID: 0048
Revises: 0047
"""
import sqlalchemy as sa
from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None

# Verified against the live database rather than assumed — the constraint is
# named uq_score_stats_per_model, not the pattern the other tables use.
_OLD_KEY = "uq_score_stats_per_model"
_NEW_KEY = "uq_score_stats_per_model_stratum"


def upgrade() -> None:
    op.add_column("biometric_score_stats",
                  sa.Column("stratum", sa.String(16), nullable=False,
                            server_default="all"))
    op.drop_constraint(_OLD_KEY, "biometric_score_stats", type_="unique")
    op.create_unique_constraint(
        _NEW_KEY, "biometric_score_stats",
        ["pharmacy_id", "modality", "model_version", "stratum"])

    op.add_column("biometric_templates",
                  sa.Column("shadow", sa.Boolean, nullable=False,
                            server_default=sa.false()))
    op.create_index("ix_biometric_templates_shadow", "biometric_templates",
                    ["pharmacy_id", "modality", "shadow"])


def downgrade() -> None:
    op.drop_index("ix_biometric_templates_shadow",
                  table_name="biometric_templates")
    op.drop_column("biometric_templates", "shadow")
    op.drop_constraint(_NEW_KEY, "biometric_score_stats", type_="unique")
    # Collapsing back to one row per model: keep the pooled calibration and drop
    # the stratified ones, which have nowhere to live under the narrower key.
    op.execute("DELETE FROM biometric_score_stats WHERE stratum <> 'all'")
    op.drop_column("biometric_score_stats", "stratum")
    op.create_unique_constraint(
        _OLD_KEY, "biometric_score_stats",
        ["pharmacy_id", "modality", "model_version"])
