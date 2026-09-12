"""What the models proposed, and what the humans did about it.

Every advisory component in this platform — the procurement recommender, the
anomaly detectors, the formulary binding resolver, the cycle-count planner —
produces suggestions and then forgets them. Nothing records whether a
recommendation was taken, so nothing can answer the only questions that matter
about an advisory system:

  * Is it any good? Precision needs outcomes, and outcomes need decisions.
  * Which thresholds should move? A detector firing 40 times a week that is
    accepted twice is not tuned, it is ignored, and nobody can show that.
  * When it is overruled, why? The reason is the training signal, and it is
    the one thing that is never recoverable after the fact.

This table is deliberately built *before* the models that will need it. The
alternative — ship detectors now, add measurement later — arrives in three
months with a working detector, no labels, and no way to tune it except by
asking someone to guess, which is where the fabricated demand signal came from.

`features` stores the inputs the recommendation was computed from, so a decision
made today remains interpretable after the model that produced it has changed.
Without it a rejected recommendation is a row saying somebody once disagreed
with a number nobody can reconstruct.

Nothing here decides anything. A recommendation is advice with a name attached;
applying it still goes through the ordinary approval and ledger paths, and the
deterministic-first rule is unchanged — rules decide, models advise.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None

KINDS = ("reorder", "write_off", "cycle_count", "formulary_binding",
         "anomaly", "expiry_risk", "demand_refresh")
STATUSES = ("open", "accepted", "rejected", "superseded", "expired")


def upgrade() -> None:
    op.create_table(
        "inventory_recommendations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("pharmacy_id", UUID(as_uuid=True),
                  sa.ForeignKey("pharmacies.id"), nullable=False, index=True),
        sa.Column("kind", sa.String(24), nullable=False, index=True),
        # What it is about. ndc11 for most, lot for a write-off or a recall.
        sa.Column("ndc11", sa.String(11), nullable=True, index=True),
        sa.Column("irc", sa.String(32), nullable=True, index=True),
        sa.Column("inventory_lot_id", UUID(as_uuid=True),
                  sa.ForeignKey("inventory_lots.id"), nullable=True),

        # The advice, the inputs behind it, and how sure the producer was.
        sa.Column("proposal", JSONB, nullable=False),
        sa.Column("features", JSONB, nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(12), nullable=True),

        # Who produced it, and which version — so a change in acceptance can be
        # attributed to a change in the model rather than a change in the staff.
        sa.Column("produced_by", sa.String(64), nullable=False),
        sa.Column("model_version", sa.String(32), nullable=True),

        # Identity across runs. A nightly job must recognise the recommendation
        # it made yesterday instead of raising it again, or the acceptance rate
        # measures how often the job ran rather than how often it was right.
        sa.Column("fingerprint", sa.String(64), nullable=False),

        sa.Column("status", sa.String(12), nullable=False, server_default="open"),
        sa.Column("decided_by_id", UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        # What actually happened afterwards, when it can be observed — the
        # difference between "the pharmacist agreed" and "it was right".
        sa.Column("outcome", JSONB, nullable=True),
        sa.Column("outcome_at", sa.DateTime(timezone=True), nullable=True),

        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_check_constraint(
        "ck_recommendation_kind_known", "inventory_recommendations",
        "kind IN " + str(KINDS))
    op.create_check_constraint(
        "ck_recommendation_status_known", "inventory_recommendations",
        "status IN " + str(STATUSES))
    op.create_check_constraint(
        "ck_recommendation_confidence_range", "inventory_recommendations",
        "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)")
    # A decided recommendation must say who and when. Without that the row is a
    # status change nobody is accountable for, which is not a label.
    op.create_check_constraint(
        "ck_recommendation_decision_is_attributed", "inventory_recommendations",
        "status IN ('open','superseded','expired') OR "
        "(decided_by_id IS NOT NULL AND decided_at IS NOT NULL)")

    # One open recommendation per fingerprint. This is what stops a nightly job
    # re-raising the same advice and inflating its own denominator.
    op.create_index("uq_recommendation_open_fingerprint",
                    "inventory_recommendations",
                    ["pharmacy_id", "fingerprint"], unique=True,
                    postgresql_where=sa.text("status = 'open'"))
    op.create_index("ix_recommendation_scoreboard", "inventory_recommendations",
                    ["pharmacy_id", "kind", "status"])


def downgrade() -> None:
    op.drop_index("ix_recommendation_scoreboard",
                  table_name="inventory_recommendations")
    op.drop_index("uq_recommendation_open_fingerprint",
                  table_name="inventory_recommendations")
    op.drop_table("inventory_recommendations")
