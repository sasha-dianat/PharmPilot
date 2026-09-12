"""Let an approval carry something other than a quantity.

`inventory_approvals` was shaped for stock movements: a type and a number. The
admin panel introduces a second thing that needs two signatures and is not a
quantity at all — a field correction that could hide something. Extending an
expiry date, releasing a recalled lot, or clearing a cold-chain breach each put
blocked stock back on the shelf, so each needs the same maker-checker treatment
as a write-off.

Rather than a parallel approval table with its own half-implemented rules,
`payload` makes the existing one general: for a stock movement it stays empty,
and for a FIELD_EDIT it holds {table, row_id, field, old, new}. One queue, one
set of separation-of-duties rules, one audit trail.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inventory_approvals",
                  sa.Column("payload", JSONB, nullable=True))
    # A quantity is meaningless for a field edit, so it may no longer be
    # required to be positive — but it must still never be negative.
    op.create_check_constraint("ck_approval_qty_non_negative",
                               "inventory_approvals", "quantity >= 0")


def downgrade() -> None:
    op.drop_constraint("ck_approval_qty_non_negative", "inventory_approvals",
                       type_="check")
    op.drop_column("inventory_approvals", "payload")
