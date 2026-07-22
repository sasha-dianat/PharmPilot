"""Format-agnostic catalog ingestion.

`build_records` maps loosely-typed export rows (from the NFI / فهرست رسمی دارویی
CSV/Excel/JSON, or the IRC API) into validated CatalogRecords — accepting common
column aliases so a thin adapter is all that's needed per source. `upsert_catalog`
persists them, computing the ingredient_key once at ingest.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from .schema import CatalogRecord
from services.core.pricing_ir.engine import ItemCategory

# Column aliases tolerated in raw export rows (Persian + English headers).
# Header keys are normalized (spaces/ZWNJ → "_", lowercased) before lookup, so
# real FDA/NFI/IRC export headers like "قیمت مصرف‌کننده" match "قیمت_مصرف_کننده".
_ALIASES: dict[str, tuple[str, ...]] = {
    "irc": ("irc", "irc_code", "کد", "کد_irc", "code", "کد_فرآورده", "کد_فراورده",
            "کد_محصول", "uid", "کد_۱۶_رقمی"),
    "name_fa": ("name_fa", "name", "نام", "نام_فارسی", "title", "product_name",
                "نام_فرآورده", "نام_فراورده", "نام_دارو", "نام_کالا", "عنوان",
                "عنوان_فرآورده", "نام_محصول"),
    "generic_name": ("generic_name", "generic", "ژنریک", "ماده_موثره", "ماده_مؤثره",
                     "active_ingredient", "ingredient", "نام_ژنریک", "نام_علمی",
                     "نام_ژنریک_دارو", "generic_code_name"),
    "strength": ("strength", "dose", "dosage", "قدرت", "دوز", "میزان", "دوز_دارو"),
    "dosage_form": ("dosage_form", "form", "شکل", "شکل_دارویی", "شکل_فرآورده", "شکل_دارو"),
    "announced_price": ("announced_price", "price", "قیمت", "قیمت_مصرف_کننده", "consumer_price",
                        "قیمت_مصوب", "قیمت_عمومی", "قیمت_مصرف‌کننده", "قیمت_فروش",
                        "قیمت_مصرفکننده", "consumer"),
    "last_invoice_price": ("last_invoice_price", "invoice_price", "قیمت_خرید", "purchase_price",
                           "قیمت_فاکتور", "قیمت_خرید_از_پخش"),
    "brand_name": ("brand_name", "brand", "برند", "نام_تجاری", "نام_برند"),
    "manufacturer": ("manufacturer", "company", "تولیدکننده", "شرکت", "صاحب_پروانه",
                     "سازنده", "شرکت_تولیدکننده", "شرکت_سازنده"),
    "atc": ("atc", "atc_code", "کد_atc"),
    "package_count": ("package_count", "package", "تعداد_در_بسته", "pack", "بسته_بندی", "تعداد"),
    "gtin": ("gtin", "barcode", "بارکد", "کد_gtin"),
    "category": ("category", "نوع", "type", "دسته", "گروه", "نوع_فرآورده", "نوع_محصول"),
    "is_generic": ("is_generic", "generic_flag", "ژنریک_است"),
    "coverage": ("coverage",),
    "country": ("country", "کشور", "کشور_تولیدکننده"),
    "license_owner": ("license_owner",),
    "brand_owner": ("brand_owner",),
    "license_valid_until": ("license_valid_until",),
}

# Persian / Arabic-Indic digits → ASCII, and thousands separators → "".
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def normalize_header(h) -> str:
    """FDA/NFI headers use spaces + ZWNJ (نیم‌فاصله). Fold both to '_' and lower
    so 'قیمت مصرف‌کننده' → 'قیمت_مصرف_کننده' to match the alias table."""
    # BOM survives pandas' default utf-8 read of a utf-8-sig CSV and would make
    # the first column ('﻿drug_code') invisible to every alias/role lookup
    s = str(h).replace("﻿", "").strip().lower().replace("‌", "_")   # ZWNJ → _
    return re.sub(r"\s+", "_", s)


def _pick(row: dict, field: str):
    for alias in _ALIASES[field]:
        if alias in row and row[alias] not in (None, ""):
            return row[alias]
    return None


def _to_decimal(v) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        s = str(v).translate(_DIGITS)
        s = re.sub(r"[,٬،\s٬،]", "", s).strip()   # strip grouping separators
        return Decimal(s) if s else None
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
        mono_keys = ("indications", "mechanism", "pharmacokinetics", "warnings",
                     "side_effects", "interactions_text", "advice", "composition", "brands",
                     "atc_path", "integrity")
        mono = {k: row[k] for k in mono_keys if row.get(k)}
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
            coverage=(row.get("coverage") if isinstance(row.get("coverage"), dict) else None),
            country=(str(_pick(row, "country")).strip() if _pick(row, "country") else None),
            license_owner=(str(_pick(row, "license_owner")).strip() if _pick(row, "license_owner") else None),
            brand_owner=(str(_pick(row, "brand_owner")).strip() if _pick(row, "brand_owner") else None),
            license_valid_until=(str(_pick(row, "license_valid_until")).strip() if _pick(row, "license_valid_until") else None),
            monograph=(mono or None),
        ))
    return out


SEED_PATH = Path(__file__).parent / "data" / "seed_sample.json"


def load_seed() -> list[CatalogRecord]:
    """Load the bundled sample catalog (demo/dev until the real NFI export is ingested)."""
    rows = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    return build_records(rows)


# DB column widths (shared/models/drug_catalog.py) — clamp so a single oversized
# row (multivitamin composition strings…) can't abort a bulk ingest.
_COL_LIMITS = {"irc": 32, "gtin": 20, "name_fa": 300, "generic_name": 200,
               "ingredient_key": 300, "dosage_form": 80, "strength": 80,
               "brand_name": 200, "manufacturer": 200, "atc": 16, "source": 40,
               "country": 80, "license_owner": 200, "brand_owner": 200,
               "license_valid_until": 20}


def _clamp(field: str, v):
    if v is None or not isinstance(v, str):
        return v
    limit = _COL_LIMITS.get(field)
    return v[:limit] if limit else v


# Nullable enrichment a source may not carry — omit from the UPDATE when None so
# e.g. an Excel price import can't wipe NFI monographs or insurer coverage.
_STICKY_FIELDS = ("coverage", "monograph", "country", "license_owner",
                  "brand_owner", "license_valid_until")


def apply_enrichment_gaps(rec: CatalogRecord, enrichments: dict) -> CatalogRecord:
    """Gap-fill ONLY blank catalog fields from an owner-approved enrichment —
    NFI-provided values are authoritative and never overwritten. Keyed by the
    record's name, falling back to its generic. Returns rec unchanged if no
    approved enrichment matches."""
    import dataclasses
    from .enrichment import enrich_key
    e = enrichments.get(enrich_key(rec.name_fa)) or enrichments.get(enrich_key(rec.generic_name))
    if not e:
        return rec
    patch: dict = {}
    for field, src in (("country", "country"), ("manufacturer", "manufacturer"),
                       ("brand_name", "brand_name"), ("dosage_form", "dosage_form")):
        if not getattr(rec, field, None) and e.get(src):
            patch[field] = str(e[src]).strip()
    if not rec.strength and e.get("strengths"):
        patch["strength"] = str(e["strengths"][0]).strip()
    return dataclasses.replace(rec, **patch) if patch else rec


def upsert_values(r: CatalogRecord, *, source: str) -> tuple[dict, dict]:
    """(insert values, on-conflict update columns) for one record."""
    values = dict(
        irc=r.irc, name_fa=r.name_fa, generic_name=r.generic_name,
        ingredient_key=r.ingredient_key, dosage_form=r.dosage_form, strength=r.strength,
        brand_name=r.brand_name, manufacturer=r.manufacturer, atc=r.atc,
        package_count=r.package_count, gtin=r.gtin, is_generic=r.is_generic,
        category=r.category.value,
        announced_price=(int(r.announced_price) if r.announced_price is not None else None),
        last_invoice_price=(int(r.last_invoice_price) if r.last_invoice_price is not None else None),
        coverage=r.coverage, source=source,
        country=r.country, license_owner=r.license_owner, brand_owner=r.brand_owner,
        license_valid_until=r.license_valid_until, monograph=r.monograph,
    )
    values = {k: _clamp(k, v) for k, v in values.items()}
    update_cols = {k: v for k, v in values.items()
                   if k != "irc" and not (k in _STICKY_FIELDS and v is None)}
    return values, update_cols


async def upsert_catalog(session, records: Iterable[CatalogRecord], *, source: str = "nfi",
                         enrichments: dict | None = None,
                         overrides: dict | None = None) -> int:
    """Upsert CatalogRecords into drug_catalog by IRC, computing ingredient_key.
    When `enrichments` (owner-approved reference) is supplied, blank catalog
    fields are gap-filled from it before write — NFI values are never overwritten.
    Returns the number of rows written."""
    from sqlalchemy.dialects.postgresql import insert
    from shared.models.drug_catalog import DrugCatalogItem

    n = 0
    for r in records:
        if enrichments:
            r = apply_enrichment_gaps(r, enrichments)
        if overrides:
            # X3: owner corrections are re-asserted over the freshly imported
            # source values, so a manual fix is permanent policy rather than a
            # value the next crawl silently erases. Applied LAST — it wins.
            from .crosswalk import apply_overrides
            r = apply_overrides(r, overrides)
        values, update_cols = upsert_values(r, source=source)
        stmt = insert(DrugCatalogItem).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=["irc"], set_=update_cols)
        await session.execute(stmt)
        n += 1
    await session.commit()
    return n
