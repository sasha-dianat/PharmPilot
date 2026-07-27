"""crosswalk decisions are per CODED PRODUCT, not per name.

The unique key was (insurer, raw_key), i.e. one decision per product NAME. That
silently breaks every insurer that truncates names: salamat prints «CICLOSPORIN»
for 8 different products (100 mg capsule, 25 mg capsule, oral solution, eye
drops…) distinguished only by their national code, so only ONE of them could
ever hold a decision and the rest returned to the review queue after every
harvest. Measured on the real data: 526 salamat names are shared by more than one
national code, covering 6,588 snapshot rows — the reason the review count stopped
converging.

Keyed on (insurer, raw_key, source_code) instead, with an empty string standing
in for "no code" so Postgres treats codeless rows as one group (a plain NULL
column would make every codeless row distinct and allow duplicates).
"""
import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # the old key exists as a CONSTRAINT in some deployments and as a bare INDEX
    # in others (test DBs built from metadata) — drop whichever is present
    op.execute("ALTER TABLE crosswalk_entries "
               "DROP CONSTRAINT IF EXISTS uq_crosswalk_insurer_rawkey")
    op.execute("DROP INDEX IF EXISTS uq_crosswalk_insurer_rawkey")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_crosswalk_insurer_rawkey_code
            ON crosswalk_entries (insurer, raw_key, coalesce(source_code, ''))
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_crosswalk_insurer_rawkey_code")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_crosswalk_insurer_rawkey
            ON crosswalk_entries (insurer, raw_key)
    """)
