"""Format-agnostic catalog ingestion.

`build_records` maps loosely-typed export rows (from the NFI / فهرست رسمی دارویی
CSV/Excel/JSON, or the IRC API) into validated CatalogRecords — accepting common
column aliases so a thin adapter is all that's needed per source. `upsert_catalog`
persists them, computing the ingredient_key once at ingest.
"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from .schema import CatalogRecord
from services.core.pricing_ir.engine import ItemCategory

# Column aliases tolerated in raw export rows (Persian + English headers).
_ALIASES: dict[str, tuple[str, ...]] = {
    "irc": ("irc", "irc_code", "کد", "کد_irc", "code"),
    "name_fa": ("name_fa", "name", "نام", "نام_فارسی", "title", "product_name"),
    "generic_name": ("generic_name", "generic", "ژنریک", "ماده_موثره", "active_ingredient", "ingredient"),
    "strength": ("strength", "dose", "dosage", "قدرت", "دوز"),
    "dosage_form": ("dosage_form", "form", "شکل", "شکل_دارویی"),
    "announced_price": ("announced_price", "price", "قیمت", "قیمت_مصرف_کننده", "consumer_price"),
    "last_invoice_price": ("last_invoice_price", "invoice_price", "قیمت_خرید", "purchase_price"),
    "brand_name": ("brand_name", "brand", "برند", "نام_تجاری"),
    "manufacturer": ("manufacturer", "company", "تولیدکننده", "شرکت"),
    "atc": ("atc", "atc_code"),
    "package_count": ("package_count", "package", "تعداد_در_بسته", "pack"),
    "gtin": ("gtin", "barcode", "بارکد"),
    "category": ("category", "نوع", "type"),
    "is_generic": ("is_generic", "generic_flag"),
}


def _pick(row: dict, field: str):
    for alias in _ALIASES[field]:
        if alias in row and row[alias] not in (None, ""):
            return row[alias]
    return None


def _to_decimal(v) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


_CATEGORY_MAP = {
    "drug": ItemCategory.DRUG, "دارو": ItemCategory.DRUG,
    "otc": ItemCategory.OTC, "او تی سی": ItemCategory.OTC,
    "supplement": ItemCategory.SUPPLEMENT, "مکمل": ItemCategory.SUPPLEMENT,
    "cosmetic": ItemCategory.COSMETIC, "آرایشی": ItemCategory.COSMETIC, "آرایشی بهداشتی": ItemCategory.COSMETIC,
}


def build_records(rows: Iterable[dict]) -> list[CatalogRecord]:
    """Map raw export rows → CatalogRecords. Rows lacking an IRC or name are skipped."""
    out: list[CatalogRecord] = []
    for row in rows:
        irc = _pick(row, "irc")
        name = _pick(row, "name_fa")
        generic = _pick(row, "generic_name") or name
        if not irc or not name:
            continue
        cat_raw = (str(_pick(row, "category") or "drug")).strip().lower()
        category = _CATEGORY_MAP.get(cat_raw, ItemCategory.DRUG)
        gen_flag = _pick(row, "is_generic")
        out.append(CatalogRecord(
            irc=str(irc).strip(),
            name_fa=str(name).strip(),
            generic_name=str(generic).strip(),
            dosage_form=str(_pick(row, "dosage_form") or "").strip(),
            strength=str(_pick(row, "strength") or "").strip(),
            announced_price=_to_decimal(_pick(row, "announced_price")),
            last_invoice_price=_to_decimal(_pick(row, "last_invoice_price")),
            brand_name=(str(_pick(row, "brand_name")).strip() if _pick(row, "brand_name") else None),
            manufacturer=(str(_pick(row, "manufacturer")).strip() if _pick(row, "manufacturer") else None),
            is_generic=bool(gen_flag) if gen_flag is not None else True,
            category=category,
            atc=(str(_pick(row, "atc")).strip() if _pick(row, "atc") else None),
            package_count=(int(_pick(row, "package_count")) if str(_pick(row, "package_count") or "").isdigit() else None),
            gtin=(str(_pick(row, "gtin")).strip() if _pick(row, "gtin") else None),
        ))
    return out


SEED_PATH = Path(__file__).parent / "data" / "seed_sample.json"


def load_seed() -> list[CatalogRecord]:
    """Load the bundled sample catalog (demo/dev until the real NFI export is ingested)."""
    rows = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    return build_records(rows)


async def upsert_catalog(session, records: Iterable[CatalogRecord], *, source: str = "nfi") -> int:
    """Upsert CatalogRecords into drug_catalog by IRC, computing ingredient_key.
    Returns the number of rows written."""
    from sqlalchemy.dialects.postgresql import insert
    from shared.models.drug_catalog import DrugCatalogItem

    n = 0
    for r in records:
        values = dict(
            irc=r.irc, name_fa=r.name_fa, generic_name=r.generic_name,
            ingredient_key=r.ingredient_key, dosage_form=r.dosage_form, strength=r.strength,
            brand_name=r.brand_name, manufacturer=r.manufacturer, atc=r.atc,
            package_count=r.package_count, gtin=r.gtin, is_generic=r.is_generic,
            category=r.category.value,
            announced_price=(int(r.announced_price) if r.announced_price is not None else None),
            last_invoice_price=(int(r.last_invoice_price) if r.last_invoice_price is not None else None),
            source=source,
        )
        stmt = insert(DrugCatalogItem).values(**values)
        update_cols = {k: v for k, v in values.items() if k != "irc"}
        stmt = stmt.on_conflict_do_update(index_elements=["irc"], set_=update_cols)
        await session.execute(stmt)
        n += 1
    await session.commit()
    return n
