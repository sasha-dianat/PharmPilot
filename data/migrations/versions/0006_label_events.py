"""label_events — audit trail for label generation, printing, and handwritten overrides

Backs the Phase 23 label engine's handwritten-mode audit (and future print/
generate audit events) so every label-related action on an Rx is traceable to
the staff member who performed it.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-07
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Column set matches shared.models.base.AuditedBase (id/created_at/updated_at/
    # created_by/updated_by/is_deleted/deleted_at) plus the label-event-specific
    # fields, mirroring the rx_state_events table created in 0001_initial_schema.
    op.execute("""
        CREATE TABLE IF NOT EXISTS label_events (
            id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            rx_id       UUID NOT NULL REFERENCES prescriptions(id),
            event_type  VARCHAR(30) NOT NULL,
            staff_id    UUID NOT NULL REFERENCES staff(id),
            notes       TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_by  UUID,
            updated_by  UUID,
            is_deleted  BOOLEAN NOT NULL DEFAULT FALSE,
            deleted_at  TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_label_events_rx_id ON label_events (rx_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_label_events_event_type ON label_events (event_type)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS label_events")
