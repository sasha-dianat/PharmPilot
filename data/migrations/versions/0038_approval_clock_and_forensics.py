"""A clock on approvals, and the forensic columns a movement could not answer.

**Approvals had no clock.** A write-off waits for a second signature, and
nothing recorded when it was due or noticed when it was not given. Maker-checker
without a deadline is a queue that stalls silently: the stock stays on the books
because the movement never applies, the requester assumes it is handled, and
nobody is told. `due_at` makes it answerable and `escalated_at` records that
someone was told.

**Movements could not say where they came from.** `created_by` names a person,
which is enough for an ordinary audit and not enough for an investigation. When
a pattern of write-offs turns up, the questions are which terminal, which
session, what role the actor held *at the time* (staff change roles; joining to
their current one back-dates a promotion), and whether the change came from the
workstation, an API client, or a backfill script. None of those could be
answered, and none can be reconstructed later.

`source_system` earns its place immediately: this session's backfill wrote 31
movements that a reader cannot otherwise distinguish from movements recorded at
the counter.
"""
import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

FORENSIC = ("session_id", "device_id", "actor_role", "source_system")


def upgrade() -> None:
    # ── the clock on an approval ──────────────────────────────────────────
    op.add_column("inventory_approvals",
                  sa.Column("due_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("inventory_approvals",
                  sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("inventory_approvals",
                  sa.Column("escalation_level", sa.Integer(), nullable=False,
                            server_default="0"))
    op.create_check_constraint(
        "ck_approval_escalation_level_sane", "inventory_approvals",
        "escalation_level >= 0 AND escalation_level <= 3")
    # The overdue sweep reads only what is still waiting.
    op.create_index("ix_approval_pending_due", "inventory_approvals",
                    ["pharmacy_id", "due_at"],
                    postgresql_where=sa.text("status = 'pending'"))

    # ── forensics on a movement ───────────────────────────────────────────
    for col in FORENSIC:
        op.add_column("inventory_movements",
                      sa.Column(col, sa.String(64), nullable=True))
    op.create_index("ix_movement_source_system", "inventory_movements",
                    ["pharmacy_id", "source_system"])

    # Existing rows are deliberately left NULL. The first draft of this
    # migration back-filled them — 'backfill_script' where `reason` matched, and
    # 'workstation' for the rest — and the append-only trigger rejected the
    # UPDATE. It was right to, and not only on the letter: nobody observed where
    # those eight legacy movements came from, and inferring an origin for them
    # would be the same fabrication this whole workstream has been removing.
    # NULL reads as "recorded before provenance was captured", which is true.

    # ── unit of measure at goods receipt ──────────────────────────────────
    # A receipt recorded a bare number. "3" of a 30-count pack is 3 units or 90
    # depending on what the person at the bench meant, and nothing recorded
    # which — the 30x error that check_unit_conversion can only find after it
    # has already misstated the shelf.
    op.add_column("inventory_lots",
                  sa.Column("received_uom", sa.String(8), nullable=True))
    op.add_column("inventory_lots",
                  sa.Column("received_packs", sa.Numeric(10, 3), nullable=True))
    op.add_column("inventory_lots",
                  sa.Column("units_per_pack", sa.Numeric(10, 3), nullable=True))
    op.create_check_constraint(
        "ck_lot_received_uom_known", "inventory_lots",
        "received_uom IS NULL OR received_uom IN ('each','pack')")


def downgrade() -> None:
    op.drop_constraint("ck_lot_received_uom_known", "inventory_lots", type_="check")
    for col in ("units_per_pack", "received_packs", "received_uom"):
        op.drop_column("inventory_lots", col)

    op.drop_index("ix_movement_source_system", table_name="inventory_movements")
    for col in FORENSIC:
        op.drop_column("inventory_movements", col)

    op.drop_index("ix_approval_pending_due", table_name="inventory_approvals")
    op.drop_constraint("ck_approval_escalation_level_sane", "inventory_approvals",
                       type_="check")
    for col in ("escalation_level", "escalated_at", "due_at"):
        op.drop_column("inventory_approvals", col)
