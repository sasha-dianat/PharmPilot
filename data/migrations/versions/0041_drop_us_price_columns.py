"""Remove AWP and WAC — two American prices with no Iranian meaning.

`drug_products.awp_unit_price` (Average Wholesale Price) and `wac_price`
(Wholesale Acquisition Cost) are US wholesale benchmarks. They were seeded by
`scripts/seed_fda_ndc.py` and held exactly what that implies: sixteen American
brands — Amoxil, Lipitor, Lantus at 19.84 — priced in DOLLARS. No Iranian
product ever had a value in either column.

They were not merely unused, they were dangerous. `routers/inventory.py` used
`wac_price` as the fallback acquisition cost when a purchase-order line arrived
without one, so a missing cost silently became a US dollar figure read as rial.
And `DrugSearch.tsx` rendered `awp_unit_price` with a `$` in front of it, in a
pharmacy that trades in rial.

The real numbers now live where they belong: `inventory_lots.unit_cost` is what
the distributor charged, and `inventory_lots.sell_price` (migration 0040) is
what the customer pays. Both are per invoice, both are in rial, and both are
entered by the people who know them.

Owner's ruling, 2026-08-09.
"""
import sqlalchemy as sa
from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("drug_products", "awp_unit_price")
    op.drop_column("drug_products", "wac_price")


def downgrade() -> None:
    # Restored empty. The values they held were US seed data; re-creating the
    # columns cannot re-create a meaning they never had for this pharmacy.
    op.add_column("drug_products",
                  sa.Column("wac_price", sa.Numeric(12, 4), nullable=True))
    op.add_column("drug_products",
                  sa.Column("awp_unit_price", sa.Numeric(12, 4), nullable=True))
