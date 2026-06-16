"""depot shelf dual-verification

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-14

LINEAGE: originally cut from master at head 0010 (on a branch where the sibling
0011 — prescriber medical_council_id — was not an ancestor). On consolidation,
0011 was merged into master first, so this migration is re-pointed to chain 0011,
giving a clean linear history (0010 → 0011 → 0012 → 0013) with a single head.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def _audit():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    ]


def _id():
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()"))


def upgrade() -> None:
    op.create_table(
        "pharmacy_shelves", _id(),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("label", sa.String(40), nullable=False),
        sa.Column("zone", sa.String(40), nullable=True),
        sa.Column("capacity_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("storage_condition", sa.String(20), nullable=False, server_default="ROOM_TEMP"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        *_audit(),
    )
    op.create_index("ix_pharmacy_shelves_pharmacy_id", "pharmacy_shelves", ["pharmacy_id"])

    op.create_table(
        "shelf_placements", _id(),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("inventory_lot_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("inventory_lots.id"), nullable=False),
        sa.Column("shelf_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacy_shelves.id"), nullable=False),
        sa.Column("ndc11", sa.String(11), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("placed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False),
        *_audit(),
    )
    op.create_index("ix_shelf_placements_lot", "shelf_placements", ["inventory_lot_id"])
    op.create_index("ix_shelf_placements_shelf", "shelf_placements", ["shelf_id"])

    op.create_table(
        "replenishment_sessions", _id(),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PICK_LIST"),
        sa.Column("pick_list", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("depot_checkpoint", postgresql.JSONB, nullable=True),
        sa.Column("reconciliation", postgresql.JSONB, nullable=True),
        sa.Column("started_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_audit(),
    )
    op.create_index("ix_replenishment_sessions_pharmacy", "replenishment_sessions", ["pharmacy_id"])

    op.create_table(
        "shelf_transfer_events", _id(),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("replenishment_sessions.id"), nullable=True),
        sa.Column("inventory_lot_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("inventory_lots.id"), nullable=False),
        sa.Column("shelf_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacy_shelves.id"), nullable=False),
        sa.Column("ndc11", sa.String(11), nullable=False),
        sa.Column("quantity_delta", sa.Integer(), nullable=False),
        sa.Column("performed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("barcode_verification_result", postgresql.JSONB, nullable=True),
        sa.Column("ai_verification_result", postgresql.JSONB, nullable=True),
        sa.Column("depot_checkpoint_result", postgresql.JSONB, nullable=True),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column("override_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pharmacist_attestation_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pharmacist_attestation_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("temperature_logged_c", sa.Numeric(4, 1), nullable=True),
        sa.Column("near_expiry_placement_confirmed", sa.Boolean(), nullable=False, server_default="false"),
        *_audit(),
    )
    op.create_index("ix_shelf_transfer_events_session", "shelf_transfer_events", ["session_id"])

    op.create_table(
        "surveillance_events", _id(),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("camera_id", sa.String(40), nullable=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("replenishment_sessions.id"), nullable=True),
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False, server_default="low"),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("clip_ref", sa.Text(), nullable=True),
        sa.Column("ai_result", postgresql.JSONB, nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("owner_notified", sa.Boolean(), nullable=False, server_default="false"),
        *_audit(),
    )
    op.create_index("ix_surveillance_events_pharmacy", "surveillance_events", ["pharmacy_id"])
    op.create_index("ix_surveillance_events_type", "surveillance_events", ["event_type"])

    op.create_table(
        "shift_handover_reports", _id(),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("shift_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("shift_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("performed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("transfers_completed", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("fefo_overrides", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("anomaly_signals", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("open_split_packs", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("cold_chain_events", postgresql.JSONB, nullable=False, server_default="[]"),
        *_audit(),
    )
    op.create_index("ix_shift_handover_reports_pharmacy", "shift_handover_reports", ["pharmacy_id"])

    # ── Additive columns ──────────────────────────────────────────────────────
    op.add_column("inventory_lots", sa.Column("split_pack_open", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("inventory_lots", sa.Column("split_pack_remaining_blisters", sa.Integer(), nullable=True))
    op.add_column("inventory_lots", sa.Column("cold_chain_breach", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("inventory_lots", sa.Column("cold_chain_breach_log", postgresql.JSONB, nullable=True))
    op.add_column("drug_products", sa.Column("storage_condition", sa.String(20), nullable=True))
    op.add_column("drug_products", sa.Column("high_risk_flag", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("drug_products", sa.Column("lasa_group", sa.String(80), nullable=True))
    op.add_column("drug_products", sa.Column("primary_shelf_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacy_shelves.id"), nullable=True))
    op.add_column("drug_products", sa.Column("blisters_per_box", sa.Integer(), nullable=True))
    op.add_column("drug_products", sa.Column("units_per_blister", sa.Integer(), nullable=True))


def downgrade() -> None:
    for col in ("units_per_blister", "blisters_per_box", "primary_shelf_id", "lasa_group", "high_risk_flag", "storage_condition"):
        op.drop_column("drug_products", col)
    for col in ("cold_chain_breach_log", "cold_chain_breach", "split_pack_remaining_blisters", "split_pack_open"):
        op.drop_column("inventory_lots", col)
    for t in ("shift_handover_reports", "surveillance_events", "shelf_transfer_events",
              "replenishment_sessions", "shelf_placements", "pharmacy_shelves"):
        op.drop_table(t)
