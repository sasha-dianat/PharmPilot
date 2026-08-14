"""The middle link of the price chain: what a lot cost, and what it sells for.

The pricing engine computes the insurer's share from the insurer's own reference
price. The patient's remainder is whatever is left of the SHELF price — and
until now nothing in this database held a shelf price. `inventory_lots` recorded
`unit_cost`, what the distributor charged, and stopped there. So the quote fell
back on NFI's announced price, which the owner has ruled is not authoritative
for either side of the calculation.

Two columns close it:

  margin_pct   the profit percentage applied to this purchase
  sell_price   the per-unit shelf price it produces

Per LOT rather than per product, because the buy price is per invoice. A lot
bought in Mordad at a higher cost keeps its own pair, and a price rise does not
rewrite what the Khordad stock cost — which is what makes the margin auditable
back to the invoice that set it.

The product's current shelf price is the HIGHEST sell_price among lots still
holding sellable stock (owner's rule, 2026-08-09): in a market where the
replacement cost only rises, selling older stock below the newest purchase price
funds the next purchase at a loss. That maximum is always taken WITHIN ONE
PRODUCT — never across brands of the same molecule, which are separate products
at separate prices, as the salt and presentation work established.

Nullable because most existing lots predate this and have no margin recorded.
A lot with no sell_price simply does not participate in the shelf-price
maximum; it does not price at zero.
"""
import sqlalchemy as sa
from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inventory_lots",
                  sa.Column("margin_pct", sa.Numeric(6, 3), nullable=True))
    op.add_column("inventory_lots",
                  sa.Column("sell_price", sa.Numeric(12, 4), nullable=True))
    # Reading "the shelf price of this product" means finding the largest
    # sell_price among that product's sellable lots, on every quote.
    op.create_index("ix_inventory_lots_product_sell_price", "inventory_lots",
                    ["drug_product_id", "sell_price"])


def downgrade() -> None:
    op.drop_index("ix_inventory_lots_product_sell_price", table_name="inventory_lots")
    op.drop_column("inventory_lots", "sell_price")
    op.drop_column("inventory_lots", "margin_pct")
