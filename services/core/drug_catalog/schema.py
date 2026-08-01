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


# Common lay/brand names → canonical active ingredient (extend as needed; a real
# deployment loads this from the catalog's synonym table).
_SYNONYMS: dict[str, str] = {
    "vitamin d3": "cholecalciferol", "vitamin d": "cholecalciferol", "vit d3": "cholecalciferol",
    "vitamin d2": "ergocalciferol",
    "vitamin c": "ascorbic acid", "vit c": "ascorbic acid",
    "vitamin b12": "cyanocobalamin", "b12": "cyanocobalamin",
    "folic acid": "folate",
    "omega 3": "omega-3", "fish oil": "omega-3",
    "vitamin b6": "pyridoxine",
    "tylenol": "acetaminophen", "paracetamol": "acetaminophen",
}


def _load_synonym_file() -> dict:
    """INN/BAN → USAN equivalences from data/reference/ingredient_synonyms.json.
    Iranian formularies use INN spellings (ciclosporin, aciclovir, salbutamol)
    while NFI stores USAN ones (cyclosporine, acyclovir, albuterol) — without
    this bridge those rows can never link. File-backed so the list is
    reviewable and extendable without a code change."""
    from pathlib import Path
    import json
    p = Path(__file__).resolve().parents[3] / "data" / "reference" / "ingredient_synonyms.json"
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    syn = doc.get("synonyms")
    return {str(k).strip().lower(): str(v).strip().lower()
            for k, v in syn.items()} if isinstance(syn, dict) else {}


# file entries load first so the built-ins above stay authoritative on conflict
_SYNONYMS = {**_load_synonym_file(), **_SYNONYMS}


def canonical_ingredient(name: str) -> str:
    """Map a lay/brand/INN ingredient name to its canonical active ingredient."""
    n = (name or "").strip().lower()
    return _SYNONYMS.get(n, n)


# ── volume: the dimension the structural key was blind to ───────────────────
# A vial's FILL VOLUME is not its strength. «IOHEXOL 300 mg/1mL 10 mL» and
# «IOHEXOL 300 mg/1mL 100 mL» have identical dose sets ({300.0}) and identical
# (generic, form) keys, so the matcher could not tell them apart — 13 tamin
# formulary rows collapsed onto one stub, with references from 333,700 to
# 25,000,000 rial (ratios up to x17,361). Volume therefore gets its own
# namespace and its own agreement rule.
#
# It is a CONSTRAINT, not another dose token: dose agreement is "any dose on one
# side matches any on the other" (names list several numbers), but two stated
# volumes that differ mean two different products, full stop.
_VOL_ML = {"ml": 1.0, "milliliter": 1.0, "millilitre": 1.0, "cc": 1.0,
           "l": 1000.0, "liter": 1000.0, "litre": 1000.0}
# a CONCENTRATION denominator («300 mg/1mL», «2 mg/1 mL») is not a fill volume —
# strip those before looking for the volume the presentation actually has
_CONC_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:mcg|microgram|µg|ug|mg|kg|gr|g|%|\[?\s*i\.?u\.?\s*\]?|units?)"
    r"\s*/\s*\d+(?:[.,]\d+)?\s*(?:ml|milliliters?|millilitres?|cc|l|liters?|litres?)\b",
    re.I)
_VOL_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(milliliters?|millilitres?|liters?|litres?|ml|cc|l)\b", re.I)


def volume_set(text) -> set:
    """Fill volumes in `text`, in mL. Concentration denominators are removed
    first, so «300 mg/1mL 50 mL» yields {50.0} and not {1.0, 50.0}."""
    s = _CONC_RE.sub(" ", str(text or ""))
    out: set = set()
    for num, unit in _VOL_RE.findall(s):
        try:
            out.add(round(float(num.replace(",", ".")) * _VOL_ML[unit.lower()], 4))
        except (ValueError, KeyError):
            continue
    return out


def volumes_agree(a: set, b: set, tol: float = 0.02) -> bool:
    """True unless BOTH sides state a volume and none of them match.

    Silence is not disagreement: most catalog rows never recorded a volume, and
    refusing those would throw away every correct match we already make."""
    if not a or not b:
        return True
    for x in a:
        for y in b:
            if abs(x - y) <= tol * max(x, y, 1e-9):
                return True
    return False


def ingredient_key(generic_name: str, strength: str, dosage_form: str,
                   volume: str = "") -> str:
    """Canonical grouping key. Normalizes the active ingredient (brand→generic,
    salt-stripping via the shared normalizer, then a synonym map for lay names
    like 'Vitamin D3' → cholecalciferol) and the strength/form spelling.

    Multi-ingredient products (multivitamins) produce very long strength strings;
    keys are capped at 300 chars (DB column width) with a hash suffix so equal
    compositions still collide onto the same key and unequal ones don't.

    FILL VOLUME joins the key when the product states one. A 50 mL vial and a
    100 mL vial of the same concentration are not interchangeable: the insurer
    prices them separately, so spreading one's reference price across the other
    is wrong, and offering one as a "cheaper alternative" to the other compares
    different quantities of drug. Measured on the live catalog, 527 groups held
    more than one volume and 9,401 rows sat inside them.

    The segment is appended ONLY when a volume is known, so the 29,843 rows that
    state none keep exactly the key they had — the split is confined to the rows
    that carry the evidence.
    """
    g = canonical_ingredient(normalize(generic_name) or (generic_name or "").strip().lower())
    s = re.sub(r"\s+", "", (strength or "").lower())
    f = (dosage_form or "").strip().lower()
    key = f"{g}|{s}|{f}"
    vols = volume_set(volume) if volume else set()
    if vols:
        v = min(vols)
        key += f"|{int(v) if float(v).is_integer() else v}ml"
    if len(key) > 300:
        import hashlib
        key = key[:266] + "#" + hashlib.sha256(key.encode()).hexdigest()[:32]
    return key


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
    # {"tamin": {"covered": true, "reference_price": 110000}, "salamat": {...}, ...}
    coverage: dict | None = None
    country: str | None = None                # کشور تولیدکننده (from the NFI brands table)
    license_owner: str | None = None          # صاحب پروانه
    brand_owner: str | None = None            # صاحب برند
    license_valid_until: str | None = None    # تاریخ اعتبار پروانه (Jalali, as printed)
    # everything else the NFI page offers: clinical sections + composition + brands
    monograph: dict | None = None

    @property
    def ingredient_key(self) -> str:
        # NFI keeps the presentation volume in monograph.generic_full, never in
        # `strength` — both are fed in so a 50 mL vial never shares a group with
        # a 100 mL one.
        gf = (self.monograph or {}).get("generic_full") or ""
        return ingredient_key(self.generic_name, self.strength, self.dosage_form,
                              f"{self.strength or ''} {gf}")

    @property
    def effective_price(self) -> Decimal:
        """Pricing intelligence: higher of announced vs latest invoice (inflation-safe)."""
        return resolve_consumer_price(self.announced_price, self.last_invoice_price)
