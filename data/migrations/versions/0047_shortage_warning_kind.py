"""A drug about to become unobtainable is advice somebody has to decide on.

Widens `ck_recommendation_kind_known` for `shortage_warning` (E13). One kind, not
two: the negotiation mentor (⑳) is deliberately **not** registered here.

The acceptance-rate machinery exists to measure *unsolicited* advice — a detector
that fires forty times a week and is accepted twice is being ignored, and the
ledger is how that becomes visible. A negotiation brief is pulled, not pushed:
the owner asks for it before a conversation, so it has already been accepted by
being requested. Filing one would inflate the denominator with a document nobody
needed to decide about, which is the exact measurement error the fingerprint was
introduced to prevent.

E13 is different. It raises itself, unasked, about molecules the pharmacy has not
noticed yet, and the two verdicts it produces call for opposite actions — buffer
stock against a market shortage, or move the order when one supplier is rationing
something another has in stock. Whether a pharmacist agrees is exactly what needs
recording, because service ⑰ recommended buffer stock for both cases and nobody
could show how often that was wrong.

TTL 21 days and cooldown 30: a shortage picture goes stale faster than a
supplier's standing but slower than a reorder, and re-raising a shortage somebody
already decided about is how an alert queue teaches people to ignore it.

Downgrade narrows the constraint and **deletes** rows of the new kind. A CHECK
ignores `is_deleted`, so they cannot be retired, and that destroys real
pharmacist decisions — a genuine cost of rolling this back, recorded here rather
than discovered from a row count afterwards.
"""
import sqlalchemy as sa
from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None

OLD_KINDS = ("reorder", "write_off", "cycle_count", "formulary_binding",
             "anomaly", "expiry_risk", "demand_refresh", "supplier_reliability")
NEW_KINDS = OLD_KINDS + ("shortage_warning",)


def upgrade() -> None:
    op.drop_constraint("ck_recommendation_kind_known",
                       "inventory_recommendations", type_="check")
    op.create_check_constraint(
        "ck_recommendation_kind_known", "inventory_recommendations",
        "kind IN " + str(NEW_KINDS))


def downgrade() -> None:
    op.execute(sa.text(
        "DELETE FROM inventory_recommendations WHERE kind = 'shortage_warning'"))
    op.drop_constraint("ck_recommendation_kind_known",
                       "inventory_recommendations", type_="check")
    op.create_check_constraint(
        "ck_recommendation_kind_known", "inventory_recommendations",
        "kind IN " + str(OLD_KINDS))
