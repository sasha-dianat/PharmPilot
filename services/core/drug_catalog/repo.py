"""DB access for the drug catalog → CatalogRecord (engine/alternatives input)."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.drug_catalog import DrugCatalogItem
from services.ai.clinical_decision_support.normalizer import normalize
from services.core.pricing_ir.engine import ItemCategory
from .schema import CatalogRecord, canonical_ingredient


def _to_record(row: DrugCatalogItem) -> CatalogRecord:
    try:
        category = ItemCategory(row.category)
    except ValueError:
        category = ItemCategory.DRUG
    return CatalogRecord(
        irc=row.irc, name_fa=row.name_fa, generic_name=row.generic_name,
        dosage_form=row.dosage_form or "", strength=row.strength or "",
        announced_price=Decimal(str(row.announced_price)) if row.announced_price is not None else None,
        last_invoice_price=Decimal(str(row.last_invoice_price)) if row.last_invoice_price is not None else None,
        brand_name=row.brand_name, manufacturer=row.manufacturer,
        is_generic=row.is_generic, category=category, atc=row.atc,
        package_count=row.package_count, gtin=row.gtin, coverage=row.coverage,
        country=row.country, license_owner=row.license_owner,
        brand_owner=row.brand_owner, license_valid_until=row.license_valid_until,
        monograph=row.monograph,
    )


async def fetch_by_irc(db: AsyncSession, ircs: list[str]) -> dict[str, CatalogRecord]:
    if not ircs:
        return {}
    rows = (await db.execute(select(DrugCatalogItem).where(DrugCatalogItem.irc.in_(ircs)))).scalars().all()
    return {r.irc: _to_record(r) for r in rows}


async def fetch_all(db: AsyncSession) -> list[CatalogRecord]:
    rows = (await db.execute(select(DrugCatalogItem))).scalars().all()
    return [_to_record(r) for r in rows]


async def fetch_by_ingredient_keys(db: AsyncSession, keys: list[str]) -> list[CatalogRecord]:
    if not keys:
        return []
    rows = (await db.execute(
        select(DrugCatalogItem).where(DrugCatalogItem.ingredient_key.in_(keys)))).scalars().all()
    return [_to_record(r) for r in rows]


async def resolve_by_name(db: AsyncSession, drug_name: str) -> CatalogRecord | None:
    """Best-effort match a free-text Rx drug name to a catalog item by normalized
    active ingredient. Defaults to the 'as-prescribed' representative — the brand
    if one exists, else the highest-priced product — so the affordability flow has
    cheaper alternatives to offer. (Policy is configurable: pick min price instead
    to model automatic generic substitution.)"""
    g = canonical_ingredient(normalize(drug_name))
    if not g:
        return None
    rows = (await db.execute(
        select(DrugCatalogItem).where(DrugCatalogItem.generic_name.ilike(f"%{g}%")))).scalars().all()
    recs = [_to_record(r) for r in rows]
    if not recs:
        return None
    brands = [r for r in recs if not r.is_generic]
    pool = brands or recs
    return max(pool, key=lambda r: r.effective_price or Decimal("0"))
