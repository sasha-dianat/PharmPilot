"""harvest_failures — retry only the pages a crawl actually lost.

A full NFI sweep is ~70,000 pages; re-running it to recover the few thousand the
proxy dropped wastes hours. Observed dns_fail counts per run: 997, 3,493, 164, 0.
"""
import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "harvest_failures",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("crawler", sa.String(20), nullable=False, index=True),
        sa.Column("page_id", sa.Integer, nullable=False, index=True),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("http_status", sa.Integer, nullable=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="1"),
        sa.Column("last_error", sa.String(300), nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("crawler", "page_id", name="uq_harvest_failure_page"),
    )


def downgrade() -> None:
    op.drop_table("harvest_failures")
