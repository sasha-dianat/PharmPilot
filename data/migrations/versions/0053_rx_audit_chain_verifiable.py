"""the audit chain gets the columns that make it verifiable by someone else

`rx_state_events` advertised SHA-256 tamper evidence that no one could check.
Driving the real `RxStateMachine` through a real lifecycle, all 8 links failed
when recomputed from the table and all 8 verified against the Python instant the
writer had hashed and thrown away (median drift 1.9 ms). Both inputs the digest
consumes were absent from the row:

  the instant   `transition()` hashed `datetime.now(timezone.utc)`; the row took
                `created_at` from `server_default=func.now()`. Different clocks.
  the link      the predecessor's hash went into the digest and was discarded,
                so a verifier had to recompute forward and guess the order.

And the order was not guessable. The writer selected its predecessor with
`ORDER BY created_at DESC LIMIT 1`, while Postgres `now()` is transaction-start
time — two transitions committed together carry byte-identical `created_at`, so
which one the second chained to was the planner's choice.

Four columns, one per defect:

  hashed_at        the instant that actually went into the digest, kept beside
                   `created_at` rather than replacing it so the drift between
                   the application clock and the database clock stays readable.
  previous_hash    the predecessor's event_hash, stored instead of inferred.
  sequence_number  monotonic per prescription from 1. UNIQUE per prescription,
                   so two concurrent writers cannot both claim position N —
                   one fails loudly rather than both succeeding ambiguously.
  digest_version   which digest definition built this row's event_hash.

**Existing rows are not rehashed.** Widening the digest changes every hash, and
recomputing the stored ones would re-bless as valid any row that had already
been altered — destroying exactly the evidence the column exists to protect. A
rewrite is also indistinguishable from an attack. So old rows stay
`digest_version = 1` and remain verifiable under the original six-field digest;
only rows written from here on are version 2. The verifier dispatches per row.

`sequence_number` IS backfilled for version-1 rows, by `(created_at, id)` — the
read order a verifier already uses. This is a reconstruction of the read order,
not a record of the write order, and for rows sharing a `created_at` the two may
differ. It is safe precisely because the version-1 digest does not span
`sequence_number`: the backfill cannot make a legacy chain verify or fail
differently than it did before. It is a reading convenience, nothing more.

`hashed_at` and `previous_hash` stay NULL on version-1 rows. A NULL there is the
honest answer — the value was never recorded and cannot be recovered — and the
verifier reports such a row as unverifiable-by-design rather than as tampered.

Revision ID: 0053
Revises: 0052
"""
import sqlalchemy as sa
from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rx_state_events",
                  sa.Column("hashed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("rx_state_events",
                  sa.Column("previous_hash", sa.String(length=64), nullable=True))
    op.add_column("rx_state_events",
                  sa.Column("sequence_number", sa.Integer(), nullable=True))
    # server_default rather than a plain default: existing rows must come out of
    # this migration already labelled version 1, and NOT NULL has to hold the
    # moment the column exists.
    op.add_column("rx_state_events",
                  sa.Column("digest_version", sa.SmallInteger(), nullable=False,
                            server_default="1"))

    # Backfill the read order for legacy rows. `id` breaks ties because rows
    # written in one transaction share created_at exactly — which is the defect
    # this migration exists to end, and it is still present in the old data.
    op.execute("""
        WITH ordered AS (
            SELECT id,
                   row_number() OVER (PARTITION BY prescription_id
                                      ORDER BY created_at, id) AS seq
              FROM rx_state_events
        )
        UPDATE rx_state_events e
           SET sequence_number = ordered.seq
          FROM ordered
         WHERE e.id = ordered.id
    """)

    # Position is unique per prescription from here on. Deliberately a
    # constraint and not just an index: the writer reads max(sequence_number)
    # and adds one, so without this two concurrent transitions both write
    # position N and the chain forks with nothing to show for it.
    op.create_unique_constraint(
        "uq_rx_state_events_seq", "rx_state_events",
        ["prescription_id", "sequence_number"])

    # A version-2 row without its two new inputs would be unverifiable while
    # claiming to be verifiable — worse than a version-1 row, which at least
    # says so. Version 1 is left alone: those columns are legitimately NULL
    # there, and no CHECK should demand a value that was never recorded.
    op.create_check_constraint(
        "ck_rx_state_events_v2_inputs", "rx_state_events",
        "digest_version = 1 OR (hashed_at IS NOT NULL "
        "AND sequence_number IS NOT NULL)")


def downgrade() -> None:
    op.drop_constraint("ck_rx_state_events_v2_inputs", "rx_state_events",
                       type_="check")
    op.drop_constraint("uq_rx_state_events_seq", "rx_state_events",
                       type_="unique")
    op.drop_column("rx_state_events", "digest_version")
    op.drop_column("rx_state_events", "sequence_number")
    op.drop_column("rx_state_events", "previous_hash")
    op.drop_column("rx_state_events", "hashed_at")
