"""drug_enrichments variant fan-out — variants JSONB + item_kind classification"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # variants: [{dosage_form, strength, brand_name, manufacturer, notes}] —
    # one entry per registrable strength×form×brand combination.
    op.add_column("drug_enrichments", sa.Column("variants", JSONB, nullable=True))
    # item_kind: drug | supply (empty bottles/containers for compounding) |
    # supplement | other — supply items never enter drug matching.
    op.add_column("drug_enrichments",
                  sa.Column("item_kind", sa.String(20), nullable=False,
                            server_default="drug"))


def downgrade() -> None:
    op.drop_column("drug_enrichments", "item_kind")
    op.drop_column("drug_enrichments", "variants")
