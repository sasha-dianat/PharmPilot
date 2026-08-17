"""Where a lot's sell price came from — the invoice, or an arithmetic.

Migration 0040 modelled the price chain as `unit_cost + margin_pct → sell_price`,
with the margin as the input. The owner has corrected the direction (2026-08-16):

    "almost always the sell price is defined in each invoice ... that percentage
     of margin is dependent on the drug, brand, dosage form, if the product is a
     drug or of cosmetics. cosmetics usually possess a higher profit margin. so
     it is a case by case matter"

So the consumer price is READ OFF THE فاکتور along with the purchase price, and
the margin is what falls out of the two — a derived, reportable figure, not a
policy dial. That also disposes of the idea of a pharmacy-wide default margin:
there is no single number to default to when a cosmetic and a generic tablet
carry different markups by their nature, and a constant applied across both
would be wrong in every row it touched.

`sell_price_basis` records which of the two happened:

  `invoice` — the price was transcribed from the delivery document. Observed.
  `margin`  — the invoice did not state one, someone declared a margin, and the
              price is that arithmetic. Declared, and traceable to the declarer.
  NULL      — neither. The lot does not price, and the product falls through to
              whatever else can price it. Nothing is invented to fill the gap.

The distinction is the point. Both produce a number in `sell_price`, and without
this column an observed price and a computed one are indistinguishable in the
money path — which is the provenance rule the planning inputs already obey:
declare the basis, and write NULL rather than a fallback constant.
"""
import sqlalchemy as sa
from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inventory_lots",
                  sa.Column("sell_price_basis", sa.String(16), nullable=True))
    # Every lot that already carries a sell price got it from the only path that
    # existed — cost times a margin. Labelling those `invoice` would claim a
    # document that was never read.
    op.execute("""
        UPDATE inventory_lots
           SET sell_price_basis = 'margin'
         WHERE sell_price IS NOT NULL
           AND sell_price_basis IS NULL
    """)


def downgrade() -> None:
    op.drop_column("inventory_lots", "sell_price_basis")
