"""The owner's own price, kept beside the batch prices rather than over them.

Migration 0040 gave each lot a `sell_price` derived from what that batch cost
plus its margin, and the shelf price is the highest of them. The repricing
control then wrote the owner's number onto every sellable lot — which destroyed
the batch-derived figures it overwrote, and with them the answer to "what did
this batch need to sell for?".

It also got the rule wrong. The shelf price is the maximum of ALL the candidate
prices — every batch registered through purchasing, AND whatever the owner has
entered by hand. The owner's number is one more candidate, not a command that
silences the others: a batch bought at a higher price than the owner last typed
must still set the shelf, or the pharmacy sells that batch below its own
replacement cost.

So the manual price lives on the product, where an owner sets it, and the batch
prices stay on their lots, where purchasing computes them. Neither overwrites
the other and the maximum decides.

`manual_price_set_at` and `_by` are here because a price somebody typed is worth
less than a price with a name and a date attached — the history in
`price_history` records the movement, and these record who owns the current
number.
"""
import sqlalchemy as sa
from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("drug_products",
                  sa.Column("manual_shelf_price", sa.Numeric(12, 4), nullable=True))
    op.add_column("drug_products",
                  sa.Column("manual_price_set_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("drug_products",
                  sa.Column("manual_price_set_by", sa.UUID(), nullable=True))


def downgrade() -> None:
    op.drop_column("drug_products", "manual_price_set_by")
    op.drop_column("drug_products", "manual_price_set_at")
    op.drop_column("drug_products", "manual_shelf_price")
