"""the shelf keeps a fraction of a unit, in all three places it keeps one

`prescription_fills.quantity_dispensed` and `inventory_lots.quantity_on_hand`
are `Numeric(10,3)`, because liquids, creams and paediatric doses are ordinary.
The shelf layer stored the same quantity as `INTEGER` in three columns, so every
fractional dispense rounded on the way in. Demonstrated against the real
dispense path by `scripts/verify_shelf_domain.py`: 2.9 units off a placement of
30 should leave 27.1, and left

    shelf_placements.units          25   (-2.1)
    pharmacy_shelves.current_units  29   (+1.9)
    shelf_transfer_events.quantity_delta  -2   (the audit row, rounded too)

Two copies of one number four apart, and an audit trail that could not
reconstruct either. It is not a rounding nuisance: the shelf ledger is the
"expected" side of the theft detector in `services/core/inventory/shelf.py`. The
placement copy under-reports the floor, so a correct count reads as SURPLUS; the
cached copy over-reports it, so the same count reads as MISSING. An expected
built on either accuses honest staff or exonerates real loss.

Three columns move to `Numeric(10,3)`, matching the lot and the fill:

  shelf_placements.units                what is standing on this shelf
  pharmacy_shelves.current_units        the cached Σ of the above
  shelf_transfer_events.quantity_delta  the movement ledger between them

`pharmacy_shelves.capacity_units` deliberately stays `INTEGER`. A shelf's
capacity is a property of the furniture, not of a dispense; nothing divides it
and no fractional value is ever written to it.

**The upgrade is lossless and the downgrade is not.** Widening integer to
`Numeric(10,3)` preserves every existing value exactly. Going back rounds, and
any fractional unit recorded while this migration was in force is destroyed —
`ROUND()` rather than truncation so the loss is at worst half a unit in either
direction rather than systematically downward, but it is still loss. The
downgrade is written to be runnable in an emergency, not to be safe.

Verified up→down→up on a disposable clone of the test database; see the CL-005
ledger entry.

Revision ID: 0054
Revises: 0053
"""
import sqlalchemy as sa
from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None

# (table, column). One shape, three columns, so the two directions cannot drift
# apart the way the three write paths did.
_COLUMNS = (
    ("shelf_placements", "units"),
    ("pharmacy_shelves", "current_units"),
    ("shelf_transfer_events", "quantity_delta"),
)


def upgrade() -> None:
    for table, column in _COLUMNS:
        op.alter_column(
            table, column,
            existing_type=sa.Integer(),
            type_=sa.Numeric(10, 3),
            existing_nullable=False,
            # Every integer is exactly representable in Numeric(10,3); this cast
            # cannot fail and cannot change a value.
            postgresql_using=f"{column}::numeric(10,3)")

    # The two totals carry a server_default of '0' from migration 0012. Left as
    # written: '0' is a valid numeric literal and re-stating it as '0.000' would
    # be a no-op diff that a future schema comparison has to explain.


def downgrade() -> None:
    for table, column in reversed(_COLUMNS):
        op.alter_column(
            table, column,
            existing_type=sa.Numeric(10, 3),
            type_=sa.Integer(),
            existing_nullable=False,
            # ROUND, not a bare cast: Postgres rounds numeric→integer anyway,
            # but saying it here makes the lossy step visible in the migration
            # rather than implicit in a cast.
            postgresql_using=f"ROUND({column})::integer")
