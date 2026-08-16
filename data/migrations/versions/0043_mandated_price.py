"""A price the owner mandates, and the batch prices they can choose from.

Migration 0042 made the owner's price a CANDIDATE: the shelf price is the
maximum of every batch and whatever the owner typed. That is the right default —
a batch bought dearer than the owner last entered must still lift the shelf, or
that batch sells below its own replacement cost.

But a default needs an override, because the rule can produce a wrong answer.
A batch received with a mistyped cost, a margin entered as 200 instead of 20, a
lot whose price was set against the wrong pack size — any of these lifts the
maximum and there is no way to bring it down while the bad figure is still the
largest candidate. The owner would be left correcting the batch to correct the
shelf, which is editing history to change today's price.

`manual_price_is_mandate` says: this number IS the price, do not compare it. The
batch figures stay untouched and keep explaining what each lot needed to earn;
they simply stop deciding. It is the escape hatch for a final price that came
out wrong, and it is a switch rather than a special value so that "the owner
fixed this deliberately" and "the owner happened to type a big number" never
look the same in the data.

Selling below the dearest batch is a real loss and the UI says so at the moment
of choosing — but it remains the owner's to choose, which is the entire point of
an override.
"""
import sqlalchemy as sa
from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("drug_products",
                  sa.Column("manual_price_is_mandate", sa.Boolean(),
                            server_default=sa.false(), nullable=False))


def downgrade() -> None:
    op.drop_column("drug_products", "manual_price_is_mandate")
