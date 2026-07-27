"""price_history — dated price time-series (SCD type-2) for products & tariffs

Phase C of the medication-DB blueprint: prices become an append-only history
with validity dates instead of overwrites, so quotes pin to a date (receipt
audit) and stale prices become a QUERY, not a re-crawl heuristic.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "price_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("irc", sa.String(32), nullable=False),
        # announced | invoice | insurer_reference
        sa.Column("price_type", sa.String(20), nullable=False),
        sa.Column("insurer", sa.String(20), nullable=True),      # for insurer_reference
        sa.Column("value", sa.BigInteger(), nullable=False),     # Rial (integer)
        sa.Column("currency", sa.String(8), nullable=False, server_default="IRR"),
        sa.Column("source", sa.String(40), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),  # null = current
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    # current-price lookups + stale scan: open rows (valid_to IS NULL) per product/type
    op.create_index("ix_price_history_current", "price_history",
                    ["irc", "price_type", "insurer", "valid_to"])
    op.create_index("ix_price_history_irc", "price_history", ["irc"])


def downgrade() -> None:
    op.drop_index("ix_price_history_irc", table_name="price_history")
    op.drop_index("ix_price_history_current", table_name="price_history")
    op.drop_table("price_history")
