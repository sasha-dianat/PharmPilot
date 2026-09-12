"""Recall cases, and the three stock states that had nowhere to live.

**Recall.** The dispense hook made "which patients received lot X" answerable
for the first time by putting a real foreign key on the fill. This is the
workflow that asks the question: resolve the affected lots, follow their
dispense movements to the fills and the patients, quarantine what is still
sellable, and refuse to close while anything remains.

`traceable_pct` is stored on the case rather than recomputed for display,
because it is the number that decides whether the patient list may be treated
as complete — and it must reflect what was true when the recall was worked, not
what the records look like after they were tidied up. Measured on this pharmacy
today it would be 0%: all 46 existing fills predate the lot link.

**Stock states.** Eleven states were specified; eight were representable.
`DAMAGE` existed as a movement type but merely decremented, so damaged stock
vanished — it could not be reported, valued, or returned to a supplier.
`in_transit` and `returned` had the same problem. Three columns give those
quantities somewhere to be, alongside the on-hand figure rather than instead
of it, so the ledger's conservation still holds.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── the three missing stock states ────────────────────────────────────
    for col in ("quantity_in_transit", "quantity_damaged", "quantity_returned"):
        op.add_column("inventory_lots",
                      sa.Column(col, sa.Numeric(10, 3), nullable=False,
                                server_default="0"))
        op.create_check_constraint(f"ck_lot_{col}_non_negative",
                                   "inventory_lots", f"{col} >= 0")

    # ── recall cases ──────────────────────────────────────────────────────
    op.create_table(
        "recall_cases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("pharmacy_id", UUID(as_uuid=True),
                  sa.ForeignKey("pharmacies.id"), nullable=False, index=True),
        sa.Column("reference", sa.String(80), nullable=False, index=True),
        sa.Column("severity", sa.String(4), nullable=False, server_default="II"),
        sa.Column("scope_type", sa.String(24), nullable=False),
        sa.Column("scope_value", sa.String(120), nullable=False),
        sa.Column("irc", sa.String(32), nullable=True, index=True),
        sa.Column("ndc11", sa.String(11), nullable=True, index=True),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("source", sa.String(80), nullable=True),   # manufacturer, regulator…

        sa.Column("status", sa.String(12), nullable=False,
                  server_default="open", index=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opened_by_id", UUID(as_uuid=True), nullable=True),
        sa.Column("contained_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by_id", UUID(as_uuid=True), nullable=True),
        sa.Column("closure_reason", sa.Text, nullable=True),
        sa.Column("closed_over_blockers", sa.Boolean, nullable=False,
                  server_default=sa.false()),

        # The impact as it stood when the recall was worked.
        sa.Column("impact", JSONB, nullable=True),
        sa.Column("units_on_shelf", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("units_blocked", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("units_dispensed", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("lots_affected", sa.Integer, nullable=False, server_default="0"),
        sa.Column("patients_identified", sa.Integer, nullable=False, server_default="0"),
        sa.Column("patients_notified", sa.Integer, nullable=False, server_default="0"),
        sa.Column("traceable_pct", sa.Numeric(5, 1), nullable=True),
        sa.Column("trace_complete", sa.Boolean, nullable=False, server_default=sa.true()),

        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("pharmacy_id", "reference", name="uq_recall_reference"),
    )

    op.create_table(
        "recall_case_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("recall_case_id", UUID(as_uuid=True),
                  sa.ForeignKey("recall_cases.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        # on_shelf | blocked | dispensed | already_gone | untraceable
        sa.Column("state", sa.String(16), nullable=False, index=True),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("inventory_lot_id", UUID(as_uuid=True),
                  sa.ForeignKey("inventory_lots.id"), nullable=True, index=True),
        sa.Column("lot_number", sa.String(50), nullable=True),
        sa.Column("storage_location", sa.String(100), nullable=True),
        sa.Column("prescription_fill_id", UUID(as_uuid=True),
                  sa.ForeignKey("prescription_fills.id"), nullable=True, index=True),
        sa.Column("patient_id", UUID(as_uuid=True),
                  sa.ForeignKey("patients.id"), nullable=True, index=True),
        sa.Column("dispensed_at", sa.DateTime(timezone=True), nullable=True),

        # Per-line disposition: a patient is notified, a lot is quarantined and
        # then written off. Tracked per line so a half-finished recall shows
        # exactly which half.
        sa.Column("action_required", sa.String(160), nullable=True),
        sa.Column("action_taken", sa.String(32), nullable=True),
        sa.Column("action_taken_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("action_taken_by_id", UUID(as_uuid=True), nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_recall_line_work", "recall_case_lines",
                    ["recall_case_id", "state", "action_taken"])


def downgrade() -> None:
    op.drop_index("ix_recall_line_work", table_name="recall_case_lines")
    op.drop_table("recall_case_lines")
    op.drop_table("recall_cases")
    for col in ("quantity_returned", "quantity_damaged", "quantity_in_transit"):
        op.drop_constraint(f"ck_lot_{col}_non_negative", "inventory_lots",
                           type_="check")
        op.drop_column("inventory_lots", col)
