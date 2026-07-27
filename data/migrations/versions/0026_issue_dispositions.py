"""issue_dispositions — the decided layer for ناسازگاری‌ها.

The incompatibilities view reported ~45,000 item-issues re-derived on every run,
with no way to record that a group is structurally NORMAL (compounding raw
materials have no finished-product IRC; devices are not drugs; drugs not marketed
in Iran are legitimately absent from NFI). Without a persisted ruling the count
could never move. This table gives issues the same decided-layer treatment
crosswalk_entries and field_overrides give matching and fields.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "issue_dispositions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("issue_type", sa.String(60), nullable=False, index=True),
        sa.Column("subject_key", sa.String(200), nullable=False, index=True),
        sa.Column("disposition", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(300), nullable=True),
        sa.Column("details", JSONB, nullable=True),
        sa.Column("decided_by", UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("issue_type", "subject_key", name="uq_issue_subject"),
    )


def downgrade() -> None:
    op.drop_table("issue_dispositions")
