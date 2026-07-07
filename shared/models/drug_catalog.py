"""Drug catalog table — IRC-keyed priced product list (reference data, not PHI).

Populated by `services/core/drug_catalog/importer.py` from the official
فهرست رسمی دارویی / NFI export. `ingredient_key` is computed at ingest so the
affordability flow can fetch same-ingredient alternatives with one indexed query.
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import TimestampedBase


class DrugCatalogItem(TimestampedBase):
    __tablename__ = "drug_catalog"

    irc: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    erx_code: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    gtin: Mapped[str | None] = mapped_column(String(20), nullable=True)

    name_fa: Mapped[str] = mapped_column(String(300), nullable=False)
    name_en: Mapped[str | None] = mapped_column(String(300), nullable=True)
    generic_name: Mapped[str] = mapped_column(String(200), nullable=False)   # active ingredient
    # same active ingredient + strength + form → same key (indexed for fast alt lookup)
    ingredient_key: Mapped[str] = mapped_column(String(300), index=True, nullable=False)

    dosage_form: Mapped[str | None] = mapped_column(String(80), nullable=True)
    strength: Mapped[str | None] = mapped_column(String(80), nullable=True)
    brand_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    atc: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True)
    package_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    is_generic: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_otc: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # "drug" | "otc" | "supplement" | "cosmetic"
    category: Mapped[str] = mapped_column(String(20), default="drug", nullable=False)

    # Prices in Rial. announced = authority (NFI); invoice = latest distributor فاکتور.
    announced_price: Mapped[float | None] = mapped_column(Numeric(14, 0), nullable=True)
    announced_price_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_invoice_price: Mapped[float | None] = mapped_column(Numeric(14, 0), nullable=True)
    last_invoice_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Per-insurer coverage: {"tamin": {"covered": true, "reference_price": 90000}, ...}
    coverage: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # NFI enrichment (full detail-page extraction)
    country: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    license_owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    brand_owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    license_valid_until: Mapped[str | None] = mapped_column(String(20), nullable=True)  # Jalali as printed
    # everything else the NFI page offers: clinical sections + composition + brands table
    monograph: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    source: Mapped[str | None] = mapped_column(String(40), nullable=True)   # "nfi" | "manual" | ...
