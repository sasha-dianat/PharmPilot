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


class InventoryLot(AuditedBase):
    """A specific lot of a drug product — tracks expiry and quantity per lot."""
    __tablename__ = "inventory_lots"

    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    drug_product_id: Mapped[UUID] = mapped_column(ForeignKey("drug_products.id"), nullable=False, index=True)
    ndc11: Mapped[str] = mapped_column(String(11), nullable=False, index=True)

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

    drug: Mapped["DrugProduct"] = relationship()


class StockLevel(TimestampedBase):
    """Current aggregated stock per pharmacy per NDC (denormalized for fast lookup)."""
    __tablename__ = "stock_levels"

    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    ndc11: Mapped[str] = mapped_column(String(11), nullable=False, index=True)
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
