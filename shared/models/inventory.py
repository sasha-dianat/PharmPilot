from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import AuditedBase, TimestampedBase


class DrugProduct(AuditedBase):
    """Master drug catalog entry — one record per dispensable product NDC."""
    __tablename__ = "drug_products"

    ndc11: Mapped[str] = mapped_column(String(11), unique=True, nullable=False, index=True)
    ndc10: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)

    # Drug identity
    brand_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    generic_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    labeler_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    strength: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dosage_form: Mapped[str | None] = mapped_column(String(100), nullable=True)
    route: Mapped[str | None] = mapped_column(String(100), nullable=True)
    package_size: Mapped[str | None] = mapped_column(String(100), nullable=True)
    package_quantity: Mapped[float | None] = mapped_column(Numeric(10, 3), nullable=True)

    # Classification
    gpi: Mapped[str | None] = mapped_column(String(14), nullable=True, index=True)  # Generic Product ID
    rxcui: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    dea_schedule: Mapped[str | None] = mapped_column(String(5), nullable=True, index=True)
    is_controlled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_hazardous: Mapped[bool] = mapped_column(Boolean, default=False)  # NIOSH Table 1
    requires_refrigeration: Mapped[bool] = mapped_column(Boolean, default=False)
    is_generic: Mapped[bool] = mapped_column(Boolean, default=True)
    is_otc: Mapped[bool] = mapped_column(Boolean, default=False)

    # Pricing
    awp_unit_price: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    awp_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    wac_price: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)

    # Drug database metadata
    fdb_drug_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    medspan_drug_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    discontinued: Mapped[bool] = mapped_column(Boolean, default=False)
    drug_db_metadata: Mapped[dict] = mapped_column(JSONB, default=dict)

    # Depot→shelf verification (migration 0012, additive)
    storage_condition: Mapped[str | None] = mapped_column(String(20), nullable=True)  # ROOM_TEMP|REFRIGERATED|FROZEN|LIGHT_PROTECTED
    high_risk_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    lasa_group: Mapped[str | None] = mapped_column(String(80), nullable=True)
    primary_shelf_id: Mapped[UUID | None] = mapped_column(ForeignKey("pharmacy_shelves.id"), nullable=True)
    blisters_per_box: Mapped[int | None] = mapped_column(Integer, nullable=True)
    units_per_blister: Mapped[int | None] = mapped_column(Integer, nullable=True)


class InventoryLot(AuditedBase):
    """A specific lot of a drug product — tracks expiry and quantity per lot."""
    __tablename__ = "inventory_lots"

    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    drug_product_id: Mapped[UUID] = mapped_column(ForeignKey("drug_products.id"), nullable=False, index=True)
    ndc11: Mapped[str] = mapped_column(String(11), nullable=False, index=True)
    # Binds this lot to the national formulary (drug_catalog.irc) — without it
    # stock cannot be priced, adjudicated, or matched to a formulary script.
    irc: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    lot_number: Mapped[str] = mapped_column(String(50), nullable=False)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    quantity_received: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)
    quantity_on_hand: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)
    quantity_reserved: Mapped[float] = mapped_column(Numeric(10, 3), default=0.0)
    # reserved = committed to fills not yet dispensed

    unit_cost: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    storage_location: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # e.g., "SHELF-A3", "REFRIGERATOR-1", "VAULT-CS"

    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    purchase_order_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("purchase_orders.id"), nullable=True
    )
    is_recalled: Mapped[bool] = mapped_column(Boolean, default=False)
    recall_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_quarantined: Mapped[bool] = mapped_column(Boolean, default=False)

    # DSCSA serialization
    serial_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    transaction_history: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Depot→shelf verification (migration 0012, additive)
    split_pack_open: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    split_pack_remaining_blisters: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cold_chain_breach: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cold_chain_breach_log: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    drug: Mapped["DrugProduct"] = relationship()


class StockLevel(TimestampedBase):
    """Current aggregated stock per pharmacy per NDC (denormalized for fast lookup)."""
    __tablename__ = "stock_levels"

    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    ndc11: Mapped[str] = mapped_column(String(11), nullable=False, index=True)
    irc: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    drug_product_id: Mapped[UUID] = mapped_column(ForeignKey("drug_products.id"), nullable=False)

    quantity_on_hand: Mapped[float] = mapped_column(Numeric(10, 3), default=0.0, nullable=False)
    quantity_reserved: Mapped[float] = mapped_column(Numeric(10, 3), default=0.0)
    quantity_on_order: Mapped[float] = mapped_column(Numeric(10, 3), default=0.0)
    par_level_min: Mapped[float | None] = mapped_column(Numeric(10, 3), nullable=True)
    par_level_max: Mapped[float | None] = mapped_column(Numeric(10, 3), nullable=True)

    # ML-computed reorder signals (updated by inventory intelligence service)
    reorder_point: Mapped[float | None] = mapped_column(Numeric(10, 3), nullable=True)
    reorder_quantity: Mapped[float | None] = mapped_column(Numeric(10, 3), nullable=True)
    safety_stock: Mapped[float | None] = mapped_column(Numeric(10, 3), nullable=True)
    avg_daily_demand: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    stockout_probability_7d: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    forecast_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    last_dispensed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PurchaseOrder(AuditedBase):
    __tablename__ = "purchase_orders"

    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    wholesaler: Mapped[str] = mapped_column(String(50), nullable=False)
    # mckesson, cardinal, amerisource, secondary

    po_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    # draft, submitted, acknowledged, partial, complete, cancelled

    ordered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expected_delivery: Mapped[date | None] = mapped_column(Date, nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    total_cost: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    # True if generated by ML reorder engine; still requires pharmacist approval

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    lines: Mapped[list["PurchaseOrderLine"]] = relationship(back_populates="order")


class PurchaseOrderLine(AuditedBase):
    __tablename__ = "purchase_order_lines"

    order_id: Mapped[UUID] = mapped_column(ForeignKey("purchase_orders.id"), nullable=False, index=True)
    drug_product_id: Mapped[UUID] = mapped_column(ForeignKey("drug_products.id"), nullable=False)
    ndc11: Mapped[str] = mapped_column(String(11), nullable=False)

    irc: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    quantity_ordered: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)
    quantity_received: Mapped[float] = mapped_column(Numeric(10, 3), default=0.0)
    unit_cost: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    wholesaler_item_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="ordered")
    # ordered, partial, complete, backordered, substituted

    order: Mapped["PurchaseOrder"] = relationship(back_populates="lines")


class ReceivingRecord(AuditedBase):
    __tablename__ = "receiving_records"

    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    purchase_order_id: Mapped[UUID | None] = mapped_column(ForeignKey("purchase_orders.id"), nullable=True)
    received_by_id: Mapped[UUID] = mapped_column(nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    invoice_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    discrepancies: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # [{ndc, ordered_qty, received_qty, lot, expiry}]


class InventoryMovement(AuditedBase):
    """Append-only audit ledger for every stock change — receipts, dispensing,
    manual count adjustments, wholesaler/patient returns, damage/expiry
    write-offs, recall removals. One row per change, recording who (created_by),
    when (created_at), where (inventory_lot_id), why (movement_type + reason),
    and the before/after quantities. Never updated or deleted — corrections are
    new offsetting rows.

    `prev_hash`/`event_hash` chain each row to its predecessor for the pharmacy
    (see `services.core.inventory.ledger.movement_hash`), so an edited or
    deleted row is detectable rather than merely discouraged. Migration 0030
    also revokes UPDATE/DELETE on this table from the application role.
    """
    __tablename__ = "inventory_movements"

    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    ndc11: Mapped[str] = mapped_column(String(11), nullable=False, index=True)
    # The national formulary key (drug_catalog.irc). ndc11 is retained for the
    # legacy US-coded demo catalog; irc is what binds a movement to the real
    # 39,184-row Iranian formulary used for pricing and insurer coverage.
    irc: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    inventory_lot_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("inventory_lots.id"), nullable=True, index=True
    )

    # DISPENSE | RECEIPT | TRANSFER_IN/OUT | ADJUSTMENT | CORRECTION | RETURN
    # | WASTE | DAMAGE | EXPIRY_REMOVAL | RECALL_REMOVAL | COUNT_GAIN | COUNT_LOSS
    movement_type: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(120), nullable=False)

    quantity_before: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)
    quantity_after: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)
    quantity_delta: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)

    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)  # RMA / PO / recall ref
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Traceability: which dispense consumed these units. This is what makes a
    # batch recall answerable — "which patients received lot X".
    prescription_fill_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("prescription_fills.id"), nullable=True, index=True
    )
    # Maker-checker: set when this movement needed a second person's approval.
    approval_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("inventory_approvals.id"), nullable=True
    )
    # Tamper evidence
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)


class InventoryApproval(AuditedBase):
    """Maker-checker record for a stock change that destroys or exports value.

    The requester and the approver must be different people (enforced in
    `services.core.inventory.approvals`, and by a CHECK constraint). Approval is
    recorded BEFORE the movement is applied, and the movement carries the
    approval's id — so no write-off can exist without a named second signature.
    """
    __tablename__ = "inventory_approvals"

    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    irc: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    ndc11: Mapped[str | None] = mapped_column(String(11), nullable=True)
    inventory_lot_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("inventory_lots.id"), nullable=True
    )

    movement_type: Mapped[str] = mapped_column(String(24), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)
    reason: Mapped[str] = mapped_column(String(240), nullable=False)
    is_controlled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # pending | approved | rejected | applied
    status: Mapped[str] = mapped_column(String(12), default="pending", nullable=False, index=True)
    requested_by_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    decided_by_id: Mapped[UUID | None] = mapped_column(nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A controlled-substance destruction needs a witness beyond the approver.
    witness_id: Mapped[UUID | None] = mapped_column(nullable=True)


class StockCount(AuditedBase):
    """A physical count session — full inventory or a cycle count of a subset.

    Counting is blind by default (`blind=True` hides the expected quantity from
    the counter), because showing the book figure turns a count into a
    confirmation exercise and destroys its value as an independent check.
    """
    __tablename__ = "stock_counts"

    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    # CYCLE | FULL | SPOT | CONTROLLED
    count_type: Mapped[str] = mapped_column(String(16), nullable=False, default="CYCLE")
    scope: Mapped[dict] = mapped_column(JSONB, default=dict)   # {abc_class, location, irc[]}
    # open | counting | review | posted | cancelled
    status: Mapped[str] = mapped_column(String(12), default="open", nullable=False, index=True)
    blind: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    posted_by_id: Mapped[UUID | None] = mapped_column(nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    lines: Mapped[list["StockCountLine"]] = relationship(back_populates="count")


class StockCountLine(AuditedBase):
    """One counted lot. `expected_quantity` is snapshotted when the line is
    generated so a late movement cannot silently change what the variance is
    measured against."""
    __tablename__ = "stock_count_lines"

    stock_count_id: Mapped[UUID] = mapped_column(
        ForeignKey("stock_counts.id"), nullable=False, index=True
    )
    inventory_lot_id: Mapped[UUID] = mapped_column(
        ForeignKey("inventory_lots.id"), nullable=False, index=True
    )
    irc: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ndc11: Mapped[str | None] = mapped_column(String(11), nullable=True)
    lot_number: Mapped[str | None] = mapped_column(String(50), nullable=True)

    expected_quantity: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)
    counted_quantity: Mapped[float | None] = mapped_column(Numeric(10, 3), nullable=True)
    variance: Mapped[float | None] = mapped_column(Numeric(10, 3), nullable=True)
    counted_by_id: Mapped[UUID | None] = mapped_column(nullable=True)
    counted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recount_of_id: Mapped[UUID | None] = mapped_column(nullable=True)
    movement_id: Mapped[UUID | None] = mapped_column(nullable=True)   # posted variance
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    count: Mapped["StockCount"] = relationship(back_populates="lines")
