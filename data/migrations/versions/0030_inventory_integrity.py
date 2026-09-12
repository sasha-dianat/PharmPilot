"""Bind inventory to the real formulary and make the stock ledger conserve.

Four defects this closes, each verified against the live database before the
migration was written:

1. **The inventory subsystem could not hold a real drug.** Every stock table
   keys on `ndc11` — a US National Drug Code — against `drug_products`, which
   holds 16 demo rows (Lipitor, Glucophage…). The authoritative catalogue is
   `drug_catalog.irc` with 39,184 Iranian rows. `irc` columns bind the two so
   stock can be priced, adjudicated against insurer coverage, and matched to a
   script written from the formulary.

2. **Dispensing never decremented stock.** 46 `prescription_fills` existed
   against 8 `inventory_movements`; no writer of `quantity_on_hand` appears
   anywhere in the prescription path. On-hand only ever went up, so turnover,
   dead-stock, reorder points and stockout risk were all computed from a number
   that no longer described the shelf. ROADMAP line 34 records the same gap.

3. **A recall could not be answered.** `prescription_fills.lot_number` is free
   text with no key, so "which patients received lot X" had no reliable answer.
   `inventory_lot_id` makes the link real.

4. **"Append-only" was a comment.** Nothing stopped an UPDATE or DELETE on
   `inventory_movements`, and the table inherits a soft-delete flag from
   `AuditedBase`. A hash chain plus a trigger makes tampering detectable and
   blocked rather than merely discouraged.

Additive and reversible: no column is dropped, no existing value is rewritten,
and every new column is nullable. `downgrade()` removes only what this
migration created.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Formulary binding ──────────────────────────────────────────────
    for table in ("inventory_lots", "stock_levels", "purchase_order_lines",
                  "inventory_movements"):
        op.add_column(table, sa.Column("irc", sa.String(32), nullable=True))
        op.create_index(f"ix_{table}_irc", table, ["irc"])

    # ── 2. Traceability + tamper evidence on the ledger ───────────────────
    op.add_column("inventory_movements",
                  sa.Column("prescription_fill_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_movement_fill", "inventory_movements",
                          "prescription_fills", ["prescription_fill_id"], ["id"])
    op.create_index("ix_inventory_movements_fill", "inventory_movements",
                    ["prescription_fill_id"])
    op.add_column("inventory_movements", sa.Column("prev_hash", sa.String(64), nullable=True))
    op.add_column("inventory_movements", sa.Column("event_hash", sa.String(64), nullable=True))
    op.create_index("ix_inventory_movements_hash", "inventory_movements", ["event_hash"])

    # Which lot a dispense actually consumed — the recall link.
    op.add_column("prescription_fills",
                  sa.Column("inventory_lot_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_fill_lot", "prescription_fills",
                          "inventory_lots", ["inventory_lot_id"], ["id"])
    op.create_index("ix_prescription_fills_lot", "prescription_fills", ["inventory_lot_id"])

    # ── 3. Maker-checker ──────────────────────────────────────────────────
    op.create_table(
        "inventory_approvals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("pharmacy_id", UUID(as_uuid=True),
                  sa.ForeignKey("pharmacies.id"), nullable=False, index=True),
        sa.Column("irc", sa.String(32), nullable=True, index=True),
        sa.Column("ndc11", sa.String(11), nullable=True),
        sa.Column("inventory_lot_id", UUID(as_uuid=True),
                  sa.ForeignKey("inventory_lots.id"), nullable=True),
        sa.Column("movement_type", sa.String(24), nullable=False),
        sa.Column("quantity", sa.Numeric(10, 3), nullable=False),
        sa.Column("reason", sa.String(240), nullable=False),
        sa.Column("is_controlled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(12), nullable=False, server_default="pending", index=True),
        sa.Column("requested_by_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("decided_by_id", UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text, nullable=True),
        sa.Column("witness_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        # Self-approval defeats the entire control. Enforced in the database so
        # it cannot be lost in a refactor of the service layer.
        sa.CheckConstraint("decided_by_id IS NULL OR decided_by_id <> requested_by_id",
                           name="ck_approval_not_self"),
        sa.CheckConstraint("witness_id IS NULL OR witness_id <> requested_by_id",
                           name="ck_witness_not_requester"),
    )
    op.add_column("inventory_movements",
                  sa.Column("approval_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_movement_approval", "inventory_movements",
                          "inventory_approvals", ["approval_id"], ["id"])

    # ── 4. Physical counts ────────────────────────────────────────────────
    op.create_table(
        "stock_counts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("pharmacy_id", UUID(as_uuid=True),
                  sa.ForeignKey("pharmacies.id"), nullable=False, index=True),
        sa.Column("count_type", sa.String(16), nullable=False, server_default="CYCLE"),
        sa.Column("scope", JSONB, server_default="{}"),
        sa.Column("status", sa.String(12), nullable=False, server_default="open", index=True),
        sa.Column("blind", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("posted_by_id", UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "stock_count_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("stock_count_id", UUID(as_uuid=True),
                  sa.ForeignKey("stock_counts.id"), nullable=False, index=True),
        sa.Column("inventory_lot_id", UUID(as_uuid=True),
                  sa.ForeignKey("inventory_lots.id"), nullable=False, index=True),
        sa.Column("irc", sa.String(32), nullable=True),
        sa.Column("ndc11", sa.String(11), nullable=True),
        sa.Column("lot_number", sa.String(50), nullable=True),
        sa.Column("expected_quantity", sa.Numeric(10, 3), nullable=False),
        sa.Column("counted_quantity", sa.Numeric(10, 3), nullable=True),
        sa.Column("variance", sa.Numeric(10, 3), nullable=True),
        sa.Column("counted_by_id", UUID(as_uuid=True), nullable=True),
        sa.Column("counted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recount_of_id", UUID(as_uuid=True), nullable=True),
        sa.Column("movement_id", UUID(as_uuid=True), nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("stock_count_id", "inventory_lot_id",
                            name="uq_count_line_per_lot"),
    )

    # ── 5. Make the ledger genuinely append-only ──────────────────────────
    # A correction is a new offsetting row, never an edit. The trigger blocks
    # UPDATE and DELETE outright; without it the hash chain would only tell us
    # afterwards that someone had already succeeded.
    op.execute("""
        CREATE OR REPLACE FUNCTION inventory_movements_append_only()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
              'inventory_movements is append-only: % rejected. Post an offsetting '
              'movement instead of editing history.', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_inventory_movements_append_only
        BEFORE UPDATE OR DELETE ON inventory_movements
        FOR EACH ROW EXECUTE FUNCTION inventory_movements_append_only();
    """)

    # A lot may not hold a negative quantity. The old aggregate update used
    # max(0, …), which converted shrinkage into a silent zero.
    op.create_check_constraint("ck_lot_qty_non_negative", "inventory_lots",
                               "quantity_on_hand >= 0")


def downgrade() -> None:
    op.drop_constraint("ck_lot_qty_non_negative", "inventory_lots", type_="check")
    op.execute("DROP TRIGGER IF EXISTS trg_inventory_movements_append_only ON inventory_movements")
    op.execute("DROP FUNCTION IF EXISTS inventory_movements_append_only()")

    op.drop_table("stock_count_lines")
    op.drop_table("stock_counts")

    op.drop_constraint("fk_movement_approval", "inventory_movements", type_="foreignkey")
    op.drop_column("inventory_movements", "approval_id")
    op.drop_table("inventory_approvals")

    op.drop_index("ix_prescription_fills_lot", table_name="prescription_fills")
    op.drop_constraint("fk_fill_lot", "prescription_fills", type_="foreignkey")
    op.drop_column("prescription_fills", "inventory_lot_id")

    op.drop_index("ix_inventory_movements_hash", table_name="inventory_movements")
    op.drop_column("inventory_movements", "event_hash")
    op.drop_column("inventory_movements", "prev_hash")
    op.drop_index("ix_inventory_movements_fill", table_name="inventory_movements")
    op.drop_constraint("fk_movement_fill", "inventory_movements", type_="foreignkey")
    op.drop_column("inventory_movements", "prescription_fill_id")

    for table in ("inventory_movements", "purchase_order_lines", "stock_levels",
                  "inventory_lots"):
        op.drop_index(f"ix_{table}_irc", table_name=table)
        op.drop_column(table, "irc")
