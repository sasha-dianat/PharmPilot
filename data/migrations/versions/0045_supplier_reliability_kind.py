"""A supplier's record becomes a thing the ledger can hold an opinion about.

`inventory_recommendations.kind` is guarded by a CHECK constraint listing every
kind the platform knows how to raise. That is deliberate — an unknown kind is a
typo that would otherwise sit in the table forever, uncounted by the scoreboard
and invisible to whoever is meant to decide it.

E12 (supplier reliability) produces a new kind of advice: *this supplier fills
short a quarter of the time, or its delivery time cannot be planned around.*
Widening the constraint is the whole change; no column moves.

The kind is registered in `recommendations.KINDS` alongside a 90-day TTL and a
90-day cooldown. Both are the longest in the table on purpose. A supplier's
record is built from months of deliveries and does not change because a
fortnight passed, and the decision it prompts — switch, renegotiate, or accept —
takes longer to play out than any shorter window would allow.

Downgrade narrows the constraint back and **deletes** any rows of the new kind.
A CHECK applies to every row, deleted flag or not, so there is no way to keep
them and still recreate the narrower constraint. That destroys real human
decisions, which is a genuine cost of rolling this back rather than a detail —
stated here so nobody discovers it from the row count afterwards.
"""
import sqlalchemy as sa
from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None

OLD_KINDS = ("reorder", "write_off", "cycle_count", "formulary_binding",
             "anomaly", "expiry_risk", "demand_refresh")
NEW_KINDS = OLD_KINDS + ("supplier_reliability",)


def upgrade() -> None:
    op.drop_constraint("ck_recommendation_kind_known",
                       "inventory_recommendations", type_="check")
    op.create_check_constraint(
        "ck_recommendation_kind_known", "inventory_recommendations",
        "kind IN " + str(NEW_KINDS))


def downgrade() -> None:
    # A CHECK constraint does not care about `is_deleted`, so these rows cannot
    # be retired — they have to go, and with them any decision a human recorded
    # about a supplier.
    op.execute(sa.text(
        "DELETE FROM inventory_recommendations WHERE kind = 'supplier_reliability'"))
    op.drop_constraint("ck_recommendation_kind_known",
                       "inventory_recommendations", type_="check")
    op.create_check_constraint(
        "ck_recommendation_kind_known", "inventory_recommendations",
        "kind IN " + str(OLD_KINDS))
