"""Smart insurance-coverage extractor (دارونامه/فارماکوپه → catalog coverage JSON).

Insurer covered-drug lists arrive in arbitrary shapes: Excel/CSV/HTML, Persian
or English headers — sometimes junk headers. This module is deterministic-ML:

1. COLUMN-ROLE INFERENCE — headers are matched against a Persian/English alias
   table; unresolved columns are classified by their VALUE DISTRIBUTIONS
   (binary → covered flag, ints in (0,100] → insurer share %, money-scale
   numerics → reference price, digit runs of 14–18 → IRC, long mostly-alpha
   strings → drug name).
2. FUZZY RECORD LINKAGE — each row links to catalog products through a cascade:
   exact IRC → exact GTIN → ingredient similarity (salt-stripped via the
   clinical normalizer + synonym map, scored with strength/form agreement) →
   Persian trade-name similarity. Every link carries a confidence; only
   high-confidence links are applied, the rest are reported for human review.
3. Coverage is per GENERIC, so an applied row spreads to every catalog product
   sharing the ingredient_key (all brands/manufacturers of that generic+dose).

No network, no LLM — auditable and offline, mirroring the platform's
deterministic-first safety architecture.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from services.ai.clinical_decision_support.normalizer import normalize
from .schema import CatalogRecord, canonical_ingredient

_DIGIT_FIX = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

# ── role vocabulary ───────────────────────────────────────────────────────────
ROLES = ("irc", "gtin", "generic_code", "drug_name", "covered", "share_pct",
         "reference_price", "ceiling", "inpatient")

_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "irc": ("irc", "کد irc", "کد فرآورده"),
    "gtin": ("gtin", "بارکد"),
    "generic_code": ("کد ژنریک", "کد ملی", "کد ملي", "generalcode", "generic code", "کد عمومی"),
    "drug_name": ("نام ژنریک", "نام دارو", "شرح", "نام", "drug", "generic name", "name", "شرح دارو"),
    "covered": ("تعهد بیمه", "بيمه", "بیمه", "مورد تعهد", "تعهد", "covered", "isbimeh", "پوشش"),
    "share_pct": ("درصد سازمان", "درصد تعهد", "سهم سازمان", "درصد", "percent", "share"),
    "reference_price": ("قیمت مورد تعهد", "قیمت تعهد", "مبلغ مورد قبول", "جمع مورد قبول سازمان",
                        "قیمت بیمه", "orgprice", "reference price", "قيمت"),
    "ceiling": ("سقف تجویز", "سقف تجويز", "سقف", "ceiling", "prescribedceiling"),
    "inpatient": ("بيمارستاني", "بیمارستانی", "بستری", "inpatient", "isbimarestani"),
}

_TRUE_WORDS = {"1", "true", "yes", "بله", "دارد", "فعال", "دارای تعهد", "مورد تعهد", "*", "✓"}
_FALSE_WORDS = {"0", "false", "no", "خیر", "ندارد", "غیرفعال", "فاقد تعهد", "-", ""}


def _norm_header(h: str) -> str:
    return re.sub(r"\s+", " ", str(h).replace("‌", " ").strip().lower())


def _num(v) -> float | None:
    s = re.sub(r"[,٬،\s]", "", str(v).translate(_DIGIT_FIX))
    try:
        return float(s) if s else None
    except ValueError:
        return None


# ── 1) column-role inference ──────────────────────────────────────────────────
def _value_role(values: list[str]) -> str | None:
    """Classify a column by its value distribution (deterministic features)."""
    vals = [str(v).strip() for v in values if str(v).strip()]
    if not vals:
        return None
    lowered = [v.lower() for v in vals]
    # binary-ish → covered flag
    if all(v in _TRUE_WORDS or v in _FALSE_WORDS for v in lowered):
        return "covered"
    nums = [_num(v) for v in vals]
    if all(n is not None for n in nums):
        ints = [n for n in nums if n is not None]
        if all(float(n).is_integer() and 0 < n <= 100 for n in ints):
            return "share_pct"
        if all(len(re.sub(r"\D", "", v.translate(_DIGIT_FIX))) >= 14 for v in vals):
            return "irc"
        if sorted(ints)[len(ints) // 2] >= 1000:      # median at money scale
            return "reference_price"
        return None
    # mostly alphabetic, reasonably long → drug name
    alpha_share = sum(1 for v in vals if len(re.sub(r"[^A-Za-zآ-ی]", "", v)) >= 0.5 * len(v)) / len(vals)
    avg_len = sum(len(v) for v in vals) / len(vals)
    if alpha_share > 0.7 and avg_len >= 8:
        return "drug_name"
    return None


def infer_columns(rows: list[dict]) -> dict[str, str]:
    """column-name → role. Headers first; value-distribution inference for the rest."""
    if not rows:
        return {}
    cols = list(rows[0].keys())
    roles: dict[str, str] = {}
    taken: set[str] = set()
    headers = {col: _norm_header(col) for col in cols}
    for col in cols:                                   # pass 1a: exact header==alias wins
        h = headers[col]
        for role, aliases in _HEADER_ALIASES.items():
            if role not in taken and any(a == h for a in aliases):
                roles[col] = role
                taken.add(role)
                break
    for col in cols:                                   # pass 1b: substring alias match
        if col in roles:
            continue
        h = headers[col]
        for role, aliases in _HEADER_ALIASES.items():
            if role not in taken and any(a in h for a in aliases):
                roles[col] = role
                taken.add(role)
                break
    for col in cols:                                   # pass 2: value inference
        if col in roles:
            continue
        role = _value_role([r.get(col, "") for r in rows[:200]])
        if role and role not in taken:
            roles[col] = role
            taken.add(role)
    return roles


def normalize_rows(rows: list[dict], roles: dict[str, str]) -> list[dict]:
    """Re-key raw rows to role names, dropping unmapped columns."""
    return [{role: row.get(col, "") for col, role in roles.items()} for row in rows]


# ── 2) fuzzy record linkage ───────────────────────────────────────────────────
_FORM_WORDS = {
    "tablet": "tablet", "tab": "tablet", "قرص": "tablet",
    "capsule": "capsule", "cap": "capsule", "کپسول": "capsule",
    "injection": "injection", "inj": "injection", "amp": "injection",
    "ampoule": "injection", "آمپول": "injection", "vial": "injection", "ویال": "injection",
    "syrup": "syrup", "شربت": "syrup", "suspension": "suspension", "سوسپانسیون": "suspension",
    "drop": "drop", "قطره": "drop", "cream": "cream", "کرم": "cream",
    "ointment": "ointment", "پماد": "ointment", "spray": "spray", "اسپری": "spray",
    "suppository": "suppository", "شیاف": "suppository", "solution": "solution",
}


def _row_signals(text: str) -> tuple[str, set[str], str | None, str]:
    """(canonical ingredient, strength digit-tokens, form, persian part)."""
    t = str(text)
    fa = " ".join(re.findall(r"[آ-ی‌]+", t)).strip()
    latin = re.sub(r"[آ-ی‌]+", " ", t)
    tokens = re.findall(r"[A-Za-z]+|\d+(?:\.\d+)?", latin.translate(_DIGIT_FIX).lower())
    form = None
    words: list[str] = []
    strengths: set[str] = set()
    for tok in tokens:
        if tok in _FORM_WORDS:
            form = _FORM_WORDS[tok]
        elif tok[0].isdigit():
            strengths.add(tok)
        elif tok not in ("mg", "ml", "mcg", "g", "iu", "u", "%"):
            words.append(tok)
    for w in re.findall(r"[آ-ی‌]+", t):                 # persian form words too
        if w in _FORM_WORDS:
            form = _FORM_WORDS[w]
    canon = canonical_ingredient(normalize(" ".join(words))) if words else ""
    return canon, strengths, form, fa


def _cat_signals(rec: CatalogRecord) -> tuple[str, set[str], str | None]:
    canon = canonical_ingredient(normalize(rec.generic_name))
    strengths = set(re.findall(r"\d+(?:\.\d+)?", str(rec.strength or "")))
    form = None
    for tok in re.findall(r"[A-Za-z]+", str(rec.dosage_form or "").lower()):
        if tok in _FORM_WORDS:
            form = _FORM_WORDS[tok]
            break
    return canon, strengths, form


@dataclass
class LinkResult:
    row: dict
    record: CatalogRecord | None
    confidence: float
    method: str
    matched: bool = field(init=False)

    def __post_init__(self):
        self.matched = self.record is not None and self.confidence >= 0.55


def link_rows(rows: list[dict], catalog: list[CatalogRecord]) -> list[LinkResult]:
    by_irc = {r.irc: r for r in catalog}
    by_gtin = {r.gtin: r for r in catalog if r.gtin}
    cat_sig = [(r, *_cat_signals(r)) for r in catalog]

    out: list[LinkResult] = []
    for row in rows:
        irc = re.sub(r"\D", "", str(row.get("irc", "")).translate(_DIGIT_FIX))
        if irc and irc in by_irc:
            out.append(LinkResult(row, by_irc[irc], 1.0, "irc"))
            continue
        gtin = re.sub(r"\D", "", str(row.get("gtin", "")).translate(_DIGIT_FIX))
        if gtin and gtin in by_gtin:
            out.append(LinkResult(row, by_gtin[gtin], 1.0, "gtin"))
            continue

        name = str(row.get("drug_name", "")).strip()
        if not name:
            out.append(LinkResult(row, None, 0.0, "none"))
            continue
        canon, strengths, form, fa = _row_signals(name)

        best: tuple[float, CatalogRecord | None, str] = (0.0, None, "none")
        for rec, c_canon, c_str, c_form in cat_sig:
            if canon and c_canon:
                name_sim = SequenceMatcher(None, canon, c_canon).ratio()
                has_extra = bool(strengths) or bool(form)
                if has_extra:
                    s_score = 1.0 if (strengths and strengths & c_str) else 0.0
                    f_score = 1.0 if (form and form == c_form) else 0.0
                    score = 0.6 * name_sim + 0.25 * s_score + 0.15 * f_score
                else:
                    score = name_sim
                if score > best[0]:
                    best = (score, rec, "ingredient")
            if fa and rec.name_fa:                      # persian trade-name path
                fa_sim = SequenceMatcher(None, fa, rec.name_fa).ratio()
                if fa_sim > best[0]:
                    best = (fa_sim, rec, "persian_name")
        score, rec, method = best
        out.append(LinkResult(row, rec if score >= 0.45 else None,
                              round(score, 3), method))
    return out


# ── 3) coverage building + application ────────────────────────────────────────
def _to_bool(v) -> bool:
    s = str(v).strip().lower()
    if s in _FALSE_WORDS:
        return False
    return True                                       # presence in a دارونامه ⇒ covered


def _to_int(v) -> int | None:
    n = _num(v)
    return int(n) if n is not None else None


@dataclass
class CoverageResult:
    applied: dict            # irc → {insurer: entry}
    review: list             # medium-confidence links for human review
    unmatched: list          # rows we couldn't link
    stats: dict


def build_coverage(links: list[LinkResult], *, insurer: str,
                   min_confidence: float = 0.75,
                   catalog: list[CatalogRecord] | None = None) -> CoverageResult:
    applied: dict = {}
    review, unmatched = [], []
    # coverage is per generic → spread to the whole ingredient group when we can
    group: dict[str, list[str]] = {}
    for r in (catalog or []):
        group.setdefault(r.ingredient_key, []).append(r.irc)

    for link in links:
        if not link.matched:
            unmatched.append({"row": link.row, "confidence": link.confidence})
            continue
        entry: dict = {"covered": _to_bool(link.row.get("covered", "1"))}
        for k, conv in (("share_pct", _to_int), ("reference_price", _to_int),
                        ("ceiling", _to_int)):
            v = link.row.get(k)
            if v not in (None, ""):
                c = conv(v)
                if c is not None:
                    entry[k] = c
        if link.row.get("inpatient") not in (None, ""):
            entry["inpatient"] = _to_bool(link.row["inpatient"])
        entry["match_confidence"] = link.confidence
        entry["match_method"] = link.method

        if link.confidence < min_confidence and link.method != "irc":
            review.append({"row": link.row, "irc": link.record.irc,
                           "name": link.record.name_fa, "confidence": link.confidence,
                           "entry": entry})
            continue
        targets = group.get(link.record.ingredient_key, [link.record.irc]) or [link.record.irc]
        for irc in targets:
            applied.setdefault(irc, {})[insurer] = entry

    return CoverageResult(
        applied=applied, review=review, unmatched=unmatched,
        stats={"rows": len(links), "applied": sum(1 for l in links if l.matched and
                                                  (l.confidence >= min_confidence or l.method == "irc")),
               "review": len(review), "unmatched": len(unmatched),
               "products_updated": len(applied)},
    )


async def apply_coverage(db, applied: dict) -> int:
    """Merge coverage entries into drug_catalog.coverage (per-insurer merge)."""
    from sqlalchemy import select
    from shared.models.drug_catalog import DrugCatalogItem
    n = 0
    for irc, entries in applied.items():
        item = (await db.execute(select(DrugCatalogItem).where(
            DrugCatalogItem.irc == irc))).scalar_one_or_none()
        if not item:
            continue
        cov = dict(item.coverage or {})
        cov.update(entries)
        item.coverage = cov
        n += 1
    await db.commit()
    return n
