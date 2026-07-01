"""Catalog record shape + the active-ingredient grouping key.

The ingredient_key groups products that are clinically interchangeable for the
affordability swap — same active ingredient, same strength, same dosage form —
so a brand and its generics (and parallel brands) fall in one bucket while a
different strength/form does not.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from services.ai.clinical_decision_support.normalizer import normalize
from services.core.pricing_ir.engine import ItemCategory, resolve_consumer_price


def ingredient_key(generic_name: str, strength: str, dosage_form: str) -> str:
    """Canonical grouping key. Normalizes the active ingredient (brand→generic,
    salt-stripping via the shared normalizer) and the strength/form spelling."""
    g = normalize(generic_name) or (generic_name or "").strip().lower()
    s = re.sub(r"\s+", "", (strength or "").lower())
    f = (dosage_form or "").strip().lower()
    return f"{g}|{s}|{f}"


@dataclass(frozen=True)
class CatalogRecord:
    irc: str                                  # 16-digit IRC code (PK)
    name_fa: str
    generic_name: str                         # active ingredient
    dosage_form: str
    strength: str
    announced_price: Decimal | None           # قیمت مصرف‌کننده (NFI / authority)
    last_invoice_price: Decimal | None = None  # latest distributor فاکتور cost
    brand_name: str | None = None
    manufacturer: str | None = None
    is_generic: bool = True
    category: ItemCategory = ItemCategory.DRUG
    atc: str | None = None
    package_count: int | None = None
    gtin: str | None = None

    @property
    def ingredient_key(self) -> str:
        return ingredient_key(self.generic_name, self.strength, self.dosage_form)

    @property
    def effective_price(self) -> Decimal:
        """Pricing intelligence: higher of announced vs latest invoice (inflation-safe)."""
        return resolve_consumer_price(self.announced_price, self.last_invoice_price)
