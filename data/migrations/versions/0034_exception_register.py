"""Give a reconciliation finding a life beyond the report that made it.

`/inventory/reconciliation` recomputes twelve checks per request and returns
`samples[:10]`. That is the right shape for a check and the wrong one for
operations: a finding with no identity cannot be assigned, dispositioned,
deduplicated or measured, so "is this new or yesterday's?" has no answer,
time-to-resolution cannot be computed, and 46 orphan fills display as 10.

The drug-catalog side of this codebase already solved the same problem with
`issue_dispositions`. This is its inventory counterpart, and it is the
dependency that alert ranking, drill-down, corrective action from an alert,
audit of the response, and recommendation-acceptance tracking all assume.

`fingerprint` is the deduplication key — a hash over (check, entity) that
excludes quantity and timestamp, so a lot that was expired yesterday and is
still expired today is one problem seen twice. The partial unique index makes
duplicate open rows impossible at the database level rather than by convention:
a scheduled run that opens twins every night turns the board into noise.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inventory_exceptions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("pharmacy_id", UUID(as_uuid=True),
                  sa.ForeignKey("pharmacies.id"), nullable=False, index=True),
        sa.Column("fingerprint", sa.String(32), nullable=False, index=True),
        sa.Column("check_code", sa.String(48), nullable=False, index=True),
        sa.Column("severity", sa.String(12), nullable=False),
        sa.Column("entity_type", sa.String(24), nullable=False),
        sa.Column("entity_key", sa.String(240), nullable=False),
        sa.Column("title_fa", sa.String(160), nullable=False),
        sa.Column("detail", sa.Text, nullable=True),
        sa.Column("remediation", sa.Text, nullable=True),

        # ranking, with its own arithmetic kept beside it so the board can
        # answer "why is this first?" without recomputing
        sa.Column("score", sa.Numeric(6, 2), nullable=False, server_default="0"),
        sa.Column("score_breakdown", JSONB, nullable=True),
        sa.Column("financial_impact", sa.Numeric(16, 2), nullable=False,
                  server_default="0"),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False, server_default="1"),
        sa.Column("is_controlled", sa.Boolean, nullable=False, server_default=sa.false()),

        # lifecycle
        sa.Column("status", sa.String(12), nullable=False,
                  server_default="open", index=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occurrences", sa.Integer, nullable=False, server_default="1"),
        sa.Column("row_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("assigned_to_id", UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disposition", sa.String(16), nullable=True),
        sa.Column("disposition_reason", sa.Text, nullable=True),
        sa.Column("disposed_by_id", UUID(as_uuid=True), nullable=True),
        sa.Column("disposed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        # How the finding ended: the pharmacy fixed it, or a person ruled on it.
        # Conflating the two would make "did our corrections hold?" unanswerable.
        sa.Column("resolved_by", sa.String(12), nullable=True),

        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    # One OPEN exception per fingerprint per pharmacy. Resolved rows are kept
    # and may repeat — the history of a recurring problem is itself evidence.
    op.execute("""
        CREATE UNIQUE INDEX uq_exception_open_fingerprint
        ON inventory_exceptions (pharmacy_id, fingerprint)
        WHERE status <> 'resolved' AND is_deleted = false
    """)
    op.create_index("ix_exception_board", "inventory_exceptions",
                    ["pharmacy_id", "status", "score"])

    op.create_table(
        "inventory_exception_rows",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("exception_id", UUID(as_uuid=True),
                  sa.ForeignKey("inventory_exceptions.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("entity_type", sa.String(24), nullable=True),
        sa.Column("entity_id", sa.String(120), nullable=True, index=True),
        # The evidence as it stood when the finding fired. Kept verbatim so a
        # reviewer months later sees what the check saw, not what the row says
        # now — the row may since have been corrected.
        sa.Column("snapshot", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "inventory_exception_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("exception_id", UUID(as_uuid=True),
                  sa.ForeignKey("inventory_exceptions.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("event", sa.String(24), nullable=False),
        sa.Column("from_status", sa.String(12), nullable=True),
        sa.Column("to_status", sa.String(12), nullable=True),
        sa.Column("actor_id", UUID(as_uuid=True), nullable=True),
        sa.Column("actor_role", sa.String(40), nullable=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("payload", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_exception_events_time", "inventory_exception_events",
                    ["exception_id", "created_at"])

    # The response history is audit evidence, so it is append-only for the same
    # reason the movement ledger is: a correction is a new event, never an edit
    # of the old one.
    op.execute("""
        CREATE OR REPLACE FUNCTION exception_events_append_only()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
              'inventory_exception_events is append-only: % rejected.', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_exception_events_append_only
        BEFORE UPDATE OR DELETE ON inventory_exception_events
        FOR EACH ROW EXECUTE FUNCTION exception_events_append_only();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_exception_events_append_only "
               "ON inventory_exception_events")
    op.execute("DROP FUNCTION IF EXISTS exception_events_append_only()")
    op.drop_table("inventory_exception_events")
    op.drop_table("inventory_exception_rows")
    op.drop_index("ix_exception_board", table_name="inventory_exceptions")
    op.execute("DROP INDEX IF EXISTS uq_exception_open_fingerprint")
    op.drop_table("inventory_exceptions")
