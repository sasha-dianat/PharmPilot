"""Provenance for the demand signal — where the number came from.

`stock_levels.avg_daily_demand` drives order quantity, order-by date, urgency,
days-of-supply, the overstocked filter and the dead-stock classification. It was
carrying seeded values that no dispense record supports: one item claimed 14
units/day against nothing dispensed at all, another claimed 4/day for an item
moving at 12.9. `forecast_updated_at` equalled `created_at` on all 16 rows and
had never moved, so the staleness was invisible too.

A number that steers purchasing must be able to say where it came from. These
four columns are that statement:

  demand_basis          observed | sparse | no_history — how it was derived
  demand_confidence     0-1, how much weight a planner should give it
  demand_window_days    the observation window it was measured over
  demand_units_observed the raw units behind it, so the rate can be re-derived

`no_history` with a NULL rate is a first-class outcome, not a gap to fill with a
default. A purchasing engine that receives NULL recommends nothing, which is
correct; one that receives an invented 1.0/day orders stock for a drug nobody
takes. That was the previous behaviour, and it is what this makes impossible to
do silently.
"""
import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("stock_levels",
                  sa.Column("demand_basis", sa.String(16), nullable=True))
    op.add_column("stock_levels",
                  sa.Column("demand_confidence", sa.Numeric(4, 3), nullable=True))
    op.add_column("stock_levels",
                  sa.Column("demand_window_days", sa.Integer(), nullable=True))
    op.add_column("stock_levels",
                  sa.Column("demand_units_observed", sa.Numeric(12, 3), nullable=True))

    # A basis outside the vocabulary means some other writer invented one, and
    # the whole point of these columns is that the provenance is honest.
    op.create_check_constraint(
        "ck_stock_demand_basis_known", "stock_levels",
        "demand_basis IS NULL OR demand_basis IN ('observed','sparse','no_history')")
    op.create_check_constraint(
        "ck_stock_demand_confidence_range", "stock_levels",
        "demand_confidence IS NULL OR (demand_confidence >= 0 AND demand_confidence <= 1)")

    # The seeded signal is not evidence about this pharmacy's dispensing. Retire
    # it rather than leave it in place looking measured: NULL reads as "unknown"
    # everywhere downstream, which suppresses recommendations until a real
    # refresh runs. The derived values go too — a reorder point computed from a
    # demand rate we have just withdrawn is no better supported than the rate.
    op.execute("""
        UPDATE stock_levels
           SET avg_daily_demand = NULL,
               reorder_point = NULL,
               safety_stock = NULL,
               stockout_probability_7d = NULL,
               forecast_updated_at = NULL,
               demand_basis = NULL
         WHERE forecast_updated_at IS NOT NULL
           AND forecast_updated_at <= created_at
    """)


def downgrade() -> None:
    op.drop_constraint("ck_stock_demand_confidence_range", "stock_levels",
                       type_="check")
    op.drop_constraint("ck_stock_demand_basis_known", "stock_levels",
                       type_="check")
    for col in ("demand_units_observed", "demand_window_days",
                "demand_confidence", "demand_basis"):
        op.drop_column("stock_levels", col)
    # The retired seed values are not restored: they were never measurements,
    # and re-creating them on a downgrade would reintroduce the defect.
