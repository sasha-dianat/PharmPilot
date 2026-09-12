"""Reservations — the row behind the counter that nothing ever incremented.

`inventory_lots.quantity_reserved` and `stock_levels.quantity_reserved` have
existed since the first inventory migration. `Lot.available` subtracts them,
`pick_fefo` allocates against that, `check_over_reservation` looks for them
exceeding on-hand, and the dispense hook decrements them. Every one of those was
written as though something raised a reservation. Nothing did — measured on the
pilot books, no lot and no stock row has ever held a reserved quantity above
zero. So `available` has always equalled `on_hand`, the over-reservation check
could not fire, and two staff could each promise the same last box.

The counter alone cannot fix that, because a counter cannot say *who* holds the
units or *when* the hold lapses. This table is the record; the counters become a
denormalisation of it, in the same relationship `stock_levels.quantity_on_hand`
has to its lots — and, like that one, checkable, with drift as a finding rather
than a silent subtraction from what the allocator will hand out.

`expires_at` is not optional. A will-call nobody collects would otherwise hold
its units out of `available` permanently, leaving the shelf showing stock the
allocator refuses to give anyone.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inventory_reservations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("pharmacy_id", UUID(as_uuid=True),
                  sa.ForeignKey("pharmacies.id"), nullable=False, index=True),
        sa.Column("prescription_id", UUID(as_uuid=True),
                  sa.ForeignKey("prescriptions.id"), nullable=False, index=True),
        # Set when the hold is consumed, so a reservation can be followed to the
        # dispense that used it and back to the patient.
        sa.Column("prescription_fill_id", UUID(as_uuid=True),
                  sa.ForeignKey("prescription_fills.id"), nullable=True),
        sa.Column("inventory_lot_id", UUID(as_uuid=True),
                  sa.ForeignKey("inventory_lots.id"), nullable=False, index=True),
        sa.Column("ndc11", sa.String(11), nullable=False, index=True),
        sa.Column("irc", sa.String(32), nullable=True, index=True),
        sa.Column("quantity", sa.Numeric(10, 3), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="active"),
        sa.Column("reason", sa.String(240), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
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

    # A zero or negative hold is not a reservation; it is a bug that would
    # subtract from availability in the wrong direction.
    op.create_check_constraint(
        "ck_reservation_quantity_positive", "inventory_reservations",
        "quantity > 0")
    op.create_check_constraint(
        "ck_reservation_status_known", "inventory_reservations",
        "status IN ('active','consumed','released','expired')")
    # A terminal reservation must say when it ended; an active one must not
    # claim to have ended already.
    op.create_check_constraint(
        "ck_reservation_released_at_matches_status", "inventory_reservations",
        "(status = 'active' AND released_at IS NULL) OR "
        "(status <> 'active' AND released_at IS NOT NULL)")

    # The two hot reads: what is currently held (for drift and availability),
    # and what has lapsed (for the expiry sweep). Both only ever look at active
    # rows, so both indexes are partial.
    op.create_index("ix_reservation_active", "inventory_reservations",
                    ["pharmacy_id", "inventory_lot_id"],
                    postgresql_where=sa.text("status = 'active'"))
    op.create_index("ix_reservation_expiring", "inventory_reservations",
                    ["expires_at"],
                    postgresql_where=sa.text("status = 'active'"))


def downgrade() -> None:
    op.drop_index("ix_reservation_expiring", table_name="inventory_reservations")
    op.drop_index("ix_reservation_active", table_name="inventory_reservations")
    op.drop_table("inventory_reservations")
