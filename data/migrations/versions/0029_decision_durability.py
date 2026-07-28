"""Make every match a durable, revisable decision.

Three gaps this closes:

1. Only reviewed rows became crosswalk decisions — 39,183 catalog rows carried
   coverage but just 707 confirmed entries existed. Everything else was
   re-derived by the matcher on each import, so a threshold change could flip a
   link silently. `origin` lets an auto-recorded belief be stored alongside an
   owner ruling without ever outranking it.
2. A product re-registered under a new IRC lost its overrides and coverage.
   `catalog_succession` records old→new as a reviewable proposal.
3. Snapshots kept the raw row but not what the engine decided about it, so a
   past run's behaviour could not be compared with today's.
"""
import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── decided layer: who decided, how sure, and whether it was ever revised ──
    op.add_column("crosswalk_entries",
                  sa.Column("origin", sa.String(8), nullable=False, server_default="owner"))
    op.add_column("crosswalk_entries", sa.Column("confidence", sa.Float, nullable=True))
    op.add_column("crosswalk_entries", sa.Column("method", sa.String(32), nullable=True))
    op.add_column("crosswalk_entries",
                  sa.Column("revised_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("crosswalk_entries", sa.Column("revised_from_irc", sa.String(32), nullable=True))
    op.create_index("ix_crosswalk_entries_origin", "crosswalk_entries", ["origin"])
    # everything recorded before this migration came from the review queue
    op.execute("UPDATE crosswalk_entries SET origin = 'owner'")

    # ── observed layer: what the engine believed about each row, at the time ──
    op.add_column("formulary_snapshots", sa.Column("matched_irc", sa.String(32), nullable=True))
    op.add_column("formulary_snapshots", sa.Column("match_confidence", sa.Float, nullable=True))
    op.add_column("formulary_snapshots", sa.Column("match_method", sa.String(32), nullable=True))
    op.create_index("ix_formulary_snapshots_matched_irc", "formulary_snapshots", ["matched_irc"])

    # ── IRC succession: a re-registered product must carry its decisions over ──
    op.create_table(
        "catalog_succession",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("page_id", sa.Integer, nullable=True, index=True),
        sa.Column("old_irc", sa.String(32), nullable=False, index=True),
        sa.Column("new_irc", sa.String(32), nullable=False, index=True),
        # proposed | applied | dismissed
        sa.Column("status", sa.String(12), nullable=False, server_default="proposed"),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("evidence", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("carried", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("decided_by", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("old_irc", "new_irc", name="uq_succession_pair"),
    )


def downgrade() -> None:
    op.drop_table("catalog_succession")
    op.drop_index("ix_formulary_snapshots_matched_irc", table_name="formulary_snapshots")
    for col in ("match_method", "match_confidence", "matched_irc"):
        op.drop_column("formulary_snapshots", col)
    op.drop_index("ix_crosswalk_entries_origin", table_name="crosswalk_entries")
    for col in ("revised_from_irc", "revised_at", "method", "confidence", "origin"):
        op.drop_column("crosswalk_entries", col)
