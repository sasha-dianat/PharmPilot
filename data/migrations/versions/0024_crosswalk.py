"""Decision crosswalk — owner decisions that survive every re-import.

crosswalk_entries: (insurer row) → (our catalog product), confirmed once and
never re-litigated by the matcher. Keyed by the insurer's own stable code when
it publishes one (tamin's drug_code), else by the spelling-proof name key.

field_overrides: owner corrections to NFI rows that RE-APPLY after every crawl
instead of being overwritten by the source.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crosswalk_entries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("insurer", sa.String(20), nullable=False),
        sa.Column("source_code", sa.String(64), nullable=True),   # insurer's own code
        sa.Column("raw_key", sa.String(300), nullable=False),     # spelling-proof name key
        sa.Column("raw_name", sa.String(300), nullable=True),     # audit: original text
        sa.Column("irc", sa.String(32), nullable=True),           # resolved product
        sa.Column("status", sa.String(12), nullable=False),       # confirmed | rejected
        sa.Column("reason", sa.String(40), nullable=True),
        sa.Column("decided_by", UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_crosswalk_lookup", "crosswalk_entries",
                    ["insurer", "source_code", "raw_key"])
    op.create_index("ix_crosswalk_irc", "crosswalk_entries", ["irc"])
    op.create_unique_constraint("uq_crosswalk_insurer_rawkey", "crosswalk_entries",
                                ["insurer", "raw_key"])

    op.create_table(
        "field_overrides",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("irc", sa.String(32), nullable=False),
        sa.Column("field", sa.String(40), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),             # null = force blank
        sa.Column("reason", sa.String(200), nullable=True),
        sa.Column("decided_by", UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_unique_constraint("uq_field_override", "field_overrides", ["irc", "field"])
    op.create_index("ix_field_overrides_irc", "field_overrides", ["irc"])


def downgrade() -> None:
    op.drop_table("field_overrides")
    op.drop_table("crosswalk_entries")
