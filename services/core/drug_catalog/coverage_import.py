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
ROLES = ("irc", "gtin", "generic_code", "drug_name", "conditions", "covered",
         "share_pct", "reference_price", "ceiling", "inpatient")

# Alias order matters: dict order is the priority for the substring pass, and a
# role is claimed once. The insurer's own CODE column must therefore be listed
# (generic_code) ahead of any loose name alias — tamin publishes both drug_code
# and drug_name, and the bare "drug" alias used to let drug_code claim the
# drug_name role, dropping the real name column and failing 100% of rows.
_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "irc": ("irc", "کد irc", "کد فرآورده"),
    "gtin": ("gtin", "بارکد"),
    "generic_code": ("کد ژنریک", "کد ملی", "کد ملي", "generalcode", "generic code",
                     "کد عمومی", "drug code", "کد دارو", "drugcode"),
    "drug_name": ("نام ژنریک", "نام دارو", "شرح", "نام", "drug name", "drugname",
                  "drug", "generic name", "name", "شرح دارو", "عنوان"),
    # «شرایط تعهد» is the CONDITIONS of coverage, not the covered flag — it must
    # be claimed before `covered`, whose loose "تعهد" alias used to swallow it,
    # turning "داروهاي غير بيمه اي" (explicitly NOT insured) into covered=true.
    "conditions": ("شرایط تعهد", "شرایط", "ملاحظات", "توضیحات", "شرح شرایط",
                   "conditions", "condition", "notes"),
    "covered": ("تعهد بیمه", "بيمه", "بیمه", "مورد تعهد", "تعهد", "covered", "isbimeh",
                "پوشش", "insurance status", "insurancestatus", "وضعیت بیمه"),
    "share_pct": ("درصد سازمان", "درصد تعهد", "سهم سازمان", "درصد", "percent", "share",
                  "درصد سهم سازمان", "درصد سهم", "organization share percent"),
    "reference_price": ("قیمت مورد تعهد", "قیمت تعهد", "مبلغ مورد قبول", "جمع مورد قبول سازمان",
                        "قیمت بیمه", "orgprice", "reference price", "قيمت",
                        "accepted total price", "accepted price"),
    "ceiling": ("سقف تجویز", "سقف تجويز", "سقف", "ceiling", "prescribedceiling",
                "max prescription", "maxprescription"),
    "inpatient": ("بيمارستاني", "بیمارستانی", "بستری", "inpatient", "isbimarestani",
                  "hospital status", "hospitalstatus"),
}

# "covered"/"not_covered" are tamin's own normalized words. Without them the
# insurance-status column failed the binary test and the HOSPITAL column was
# elected the covered flag instead.
_TRUE_WORDS = {"1", "true", "yes", "بله", "دارد", "فعال", "دارای تعهد", "مورد تعهد",
               "*", "✓", "covered", "است"}
_FALSE_WORDS = {"0", "false", "no", "خیر", "ندارد", "غیرفعال", "فاقد تعهد", "-",
                "not_covered", "not covered", "نيست", "نیست"}


_MONEY_WORDS = ("قیمت", "مبلغ", "بها", "price", "amount", "هزینه")

# Minimum canonical-ingredient similarity for a fuzzy candidate to be considered
# a match at all. Calibrated on real mismatches (all ≤0.615) vs real
# equivalences (all ≥0.692); see the note at the scoring loop.
INGREDIENT_FLOOR = 0.66

# delivery-system words a formulary appends to a product name; stripped when
# retrying an enrichment lookup (the researched entry is keyed on the base name)
_PEN_TOKEN_RE = re.compile(
    r"\b(solostar|flexpen|kwikpen|penfill|quickpen|innolet|cartridge|prefilled|"
    r"pre-?filled|pen)\b|قلم|کارتریج", re.I)

# every header alias, folded — used to recognize a header row restated as data
_ALL_HEADER_PHRASES = frozenset(
    a for aliases in _HEADER_ALIASES.values() for a in aliases)


def _is_header_row(name: str) -> bool:
    return _norm_header(name) in {_norm_header(a) for a in _ALL_HEADER_PHRASES}


def _norm_header(h: str) -> str:
    # fold ZWNJ and underscores (excel_import.read_table already turns spaces/ZWNJ
    # into "_") back to spaces, and Arabic yeh/kaf to Persian, so exact-alias
    # matching survives real-world files (e.g. the salamat .xls uses ي not ی)
    s = str(h).replace("‌", " ").replace("_", " ").replace("ي", "ی").replace("ك", "ک")
    return re.sub(r"\s+", " ", s.strip().lower())


# Aliases must survive the same folding as headers (Arabic yeh/kaf → Persian),
# else an Arabic-yeh alias like "قيمت" can never match a folded header "قیمت".
_ALIASES_NORM: dict[str, tuple[str, ...]] = {
    role: tuple(_norm_header(a) for a in aliases)
    for role, aliases in _HEADER_ALIASES.items()
}


def _pure_num(v) -> float | None:
    """A number ONLY if the whole cell is numeric (after stripping separators
    and %). Used for column-role inference, where "ATORVASTATIN 20MG" must NOT
    read as 20 — a drug-name column would otherwise look all-numeric."""
    s = re.sub(r"[,٬،%\s]", "", str(v).translate(_DIGIT_FIX))
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _num(v) -> float | None:
    """First numeric VALUE in a cell, for value extraction. A source cell may
    pack SEVERAL values: tamin publishes tiered organization shares in one
    column as "70%\\r90%" (سرپایی/بستری) — stripping the separators concatenated
    them into 7090, an impossible share and a 25000× price fault downstream. We
    strip only thousand separators (,٬،) so 16,425,650 stays one number, then
    take the first number token; the full raw cell is kept in the snapshot row."""
    s = re.sub(r"[,٬،]", "", str(v).translate(_DIGIT_FIX))
    m = re.search(r"\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


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
    nums = [_pure_num(v) for v in vals]      # strict: whole cell must be numeric
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
        for role, aliases in _ALIASES_NORM.items():
            if role not in taken and any(a == h for a in aliases):
                roles[col] = role
                taken.add(role)
                break
    for col in cols:                                   # pass 1b: substring alias match
        if col in roles:
            continue
        h = headers[col]
        # A money column may only claim a money role. Without this, salamat's
        # «قيمت کل مورد درتعهد با احتساب يارانه ارزي» (a price) was elected the
        # covered flag, because "تعهد" appears inside it.
        money = any(w in h for w in _MONEY_WORDS)
        for role, aliases in _ALIASES_NORM.items():
            if money and role not in ("reference_price", "ceiling"):
                continue
            if role not in taken and any(a in h for a in aliases):
                roles[col] = role
                taken.add(role)
                break
    for col in cols:                                   # pass 2: value inference
        if col in roles:
            continue
        role = _value_role([r.get(col, "") for r in rows[:200]])
        # same money rule as the alias pass: an all-zero price column looks
        # binary by value, and must still never become the covered flag
        if any(w in headers[col] for w in _MONEY_WORDS) and \
                role not in ("reference_price", "ceiling"):
            continue
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
    "softgel": "capsule", "سافت": "capsule", "سافتژل": "capsule", "softgelcap": "capsule",
}


_UNIT_MG = {"µg": 0.001, "ug": 0.001, "mcg": 0.001, "microgram": 0.001,
            "milligram": 1.0, "mg": 1.0, "gram": 1000.0, "gr": 1000.0, "g": 1000.0}
_MG_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(µg|ug|mcg|microgram|milligram|mg|gram|gr|g)\b", re.I)


def _strength_mg(text) -> set[float]:
    """Milligram-equivalents of every '<num> <mass-unit>' in text (mcg/g folded
    to mg), so 0.05 mg == 50 microgram compare EQUAL. IU/mL/% carry no mass unit
    and yield an empty set — those drugs keep the legacy raw-token behavior."""
    out: set[float] = set()
    for num, unit in _MG_RE.findall(str(text).translate(_DIGIT_FIX)):
        out.add(round(float(num) * _UNIT_MG[unit.lower()], 6))
    return out


def _mg_agree(a: set[float], b: set[float], tol: float = 0.01) -> bool:
    """Any mg-dose in `a` within 1% of any in `b` (tolerates float/rounding)."""
    return any(abs(x - y) <= tol * max(x, y, 1e-9) for x in a for y in b)


_SALT_NOTE_RE = re.compile(r"\(\s*AS\s+[^)]*\)|\bAS\s+(?:SODIUM|POTASSIUM|CALCIUM|"
                           r"HYDROCHLORIDE|SULFATE|SULPHATE|ACETATE|MALEATE|"
                           r"MESYLATE|TARTRATE|CITRATE|PHOSPHATE|BESILATE|"
                           r"FUMARATE|SUCCINATE|BITARTRATE|DIHYDRATE)\b", re.I)
# route words carry no identity and must not enter the fuzzy ingredient string —
# "ALENDRONATE (AS SODIUM) … ORAL" scored 0.611 against "alendronic acid" purely
# from the trailing noise, versus 0.692 once cleaned
_ROUTE_WORDS = frozenset(w.lower() for w in (
    "oral", "parenteral", "intravenous", "intramuscular", "subcutaneous",
    "ophthalmic", "topical", "rectal", "vaginal", "nasal", "otic", "buccal",
    "sublingual", "inhalation", "respiratory", "transdermal", "irrigation",
    "intrathecal", "dental", "as"))


def _row_signals(text: str) -> tuple[str, set[str], str | None, str]:
    """(canonical ingredient, strength digit-tokens, form, persian part)."""
    t = _SALT_NOTE_RE.sub(" ", str(text))
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
        elif tok not in ("mg", "ml", "mcg", "g", "iu", "u", "%") and \
                tok not in _ROUTE_WORDS:
            words.append(tok)
    for w in re.findall(r"[آ-ی‌]+", t):                 # persian form words too
        if w in _FORM_WORDS:
            form = _FORM_WORDS[w]
    canon = canonical_ingredient(normalize(" ".join(words))) if words else ""
    return canon, strengths, form, fa


def _cat_signals(rec: CatalogRecord) -> tuple[str, set[str], str | None, set[float]]:
    canon = canonical_ingredient(normalize(rec.generic_name))
    strengths = set(re.findall(r"\d+(?:\.\d+)?", str(rec.strength or "")))
    mg = _strength_mg(rec.strength or "")
    form = None
    for tok in re.findall(r"[A-Za-z]+", str(rec.dosage_form or "").lower()):
        if tok in _FORM_WORDS:
            form = _FORM_WORDS[tok]
            break
    return canon, strengths, form, mg


@dataclass
class LinkResult:
    row: dict
    record: CatalogRecord | None
    confidence: float
    method: str
    matched: bool = field(init=False)

    def __post_init__(self):
        self.matched = self.record is not None and self.confidence >= 0.55


def link_rows(rows: list[dict], catalog: list[CatalogRecord],
              enrichments: dict | None = None,
              crosswalk: dict | None = None, insurer: str = "",
              code_registry: dict | None = None,
              structural: bool = True) -> list[LinkResult]:
    from . import structural_match as sm
    by_irc = {r.irc: r for r in catalog}
    by_gtin = {r.gtin: r for r in catalog if r.gtin}
    cat_sig = [(r, *_cat_signals(r)) for r in catalog]
    struct_index = sm.build_index(catalog) if structural else None
    form_vocab = sm.build_form_vocab(catalog) if structural else []
    fam_idx = sm.family_index(catalog)

    # ── blocking indexes ──────────────────────────────────────────────────────
    # Fuzzy comparison is expensive (SequenceMatcher); comparing every row to
    # every product is O(rows×catalog) and melts on real inputs (4k rows × 39k
    # products ≈ 143M ratios). Classic record-linkage blocking: only candidates
    # sharing a cheap prefix key get the expensive treatment. Both sides pass
    # through canonical_ingredient() first, so lay-name synonyms already align;
    # a typo INSIDE the first 3 latin chars (rare) is the accepted trade-off —
    # such rows fall to `unmatched` and land in the human-review lane.
    latin_block: dict[str, list] = {}
    fa_block: dict[str, list] = {}
    for item in cat_sig:
        rec, c_canon, _c_str, _c_form, _c_mg = item
        if c_canon:
            latin_block.setdefault(c_canon[:3], []).append(item)
        if rec.name_fa:
            fa_block.setdefault(rec.name_fa[:2], []).append(item)

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
        # A repeated header row inside the sheet (Iranian exports often restate
        # «نام ژنريک» mid-table) is not a product — it was fuzzy-matching to
        # nandrolone. Detected by folding it against the header vocabulary.
        if _is_header_row(name):
            out.append(LinkResult(row, None, 0.0, "header_row"))
            continue
        # ── decision crosswalk (X2): the owner already ruled on this row ──────
        # Consulted BEFORE any fuzzy work: a confirmed mapping is ground truth
        # (confidence 1.0, never re-litigated), and a rejected one is left
        # unmatched instead of being re-proposed every harvest. This is what
        # makes review effort cumulative rather than repeated.
        if crosswalk:
            from .crosswalk import lookup_key, row_source_code
            from .enrichment import enrich_key
            for k in lookup_key(insurer, row_source_code(row), enrich_key(name)):
                d = crosswalk.get(k)
                if not d:
                    continue
                if d.get("status") == "confirmed" and d.get("irc") in by_irc:
                    out.append(LinkResult(row, by_irc[d["irc"]], 1.0, "crosswalk"))
                    break
                if d.get("status") == "rejected":
                    out.append(LinkResult(row, None, 0.0, "crosswalk-rejected"))
                    break
            else:
                d = None
            if d and (d.get("status") == "rejected"
                      or (d.get("status") == "confirmed" and d.get("irc") in by_irc)):
                continue

        # ── national-code resolver: the cross-insurer Rosetta stone ──────────
        # Tier 1: a code the owner confirmed (for ANY insurer) resolves exactly.
        # Tier 2: a richer name observed for the same code elsewhere (tamin's
        # full «…100 mg CAPSULE…» vs salamat's bare «CICLOSPORIN») substitutes
        # into the matching signals — the result still flows through normal
        # scoring + review gates, so a bad join cannot silently apply.
        match_name = name
        code_assisted = False
        if code_registry:
            from .crosswalk import row_source_code
            _code = row_source_code(row)
            reg = code_registry.get(_code) if _code else None
            if reg:
                if reg.get("irc") and reg["irc"] in by_irc:
                    out.append(LinkResult(row, by_irc[reg["irc"]], 1.0, "code"))
                    continue
                rich = str(reg.get("name") or "")
                if len(rich) > len(name) + 4:      # meaningfully more specified
                    match_name = rich
                    code_assisted = True

        canon, strengths, form, fa = _row_signals(match_name)
        row_mg = _strength_mg(match_name)
        name = match_name  # downstream narrowing (pack/pen) reads the rich name

        # ── structural match on the shared controlled vocabulary ─────────────
        # Formulary names and NFI columns are the SAME IRC/FDA vocabulary, so an
        # exact (generic, dosage_form, dose) hit is real evidence — far stronger
        # than string similarity against a Persian brand name. Tried before the
        # fuzzy scorer; a miss simply falls through.
        if struct_index is not None:
            parsed = sm.parse_name(match_name, form_vocab or [])
            s_rec, s_conf, s_why = sm.match(parsed, struct_index)
            if s_rec is not None:
                if code_assisted:
                    # cross-insurer evidence stays review-first: the owner
                    # confirms the pairing once, then the crosswalk makes it exact
                    s_conf = min(s_conf, 0.74)
                out.append(LinkResult(row, s_rec, round(s_conf, 3),
                                      "structural+code" if code_assisted else "structural"))
                continue

        # ── approved-enrichment augmentation ──────────────────────────────────
        # A vague brand row («ویتامین آ-تداژل») carries no generic/form/strength,
        # so it can't link. If an OWNER-APPROVED enrichment knows this drug, expand
        # the row's signals with its researched generic/form/strengths before
        # scoring — converting unmatched → matched. An explicit irc pin wins
        # outright. Enrichment is consulted only when provided (deterministic path
        # untouched when enrichments is None).
        if enrichments:
            from .enrichment import enrich_key
            # A formulary row commonly appends the delivery system to the name
            # that was researched («INSULINGLAR X» → «INSULINGLAR X SOLOSTAR»),
            # so a plain key lookup missed the enrichment entirely and the row
            # fell through to comparing the brand string against the generic.
            # Try the full name first, then with container tokens removed.
            e = enrichments.get(enrich_key(name))
            if not e:
                trimmed = _PEN_TOKEN_RE.sub(" ", name).strip()
                if trimmed and trimmed != name:
                    e = enrichments.get(enrich_key(trimmed))
            if e:
                if e.get("irc") and e["irc"] in by_irc:
                    out.append(LinkResult(row, by_irc[e["irc"]], 1.0, "enrichment"))
                    continue
                if e.get("generic_name"):
                    # An APPROVED enrichment is the owner stating what this brand
                    # actually contains, so it outranks whatever the brand string
                    # happens to look like. Previously it was consulted only when
                    # parsing yielded nothing, which left «INSULINGLAR X SOLOSTAR»
                    # to be compared as a literal string against "insulin
                    # glargine" — a comparison the ingredient floor rightly fails.
                    canon = canonical_ingredient(normalize(e["generic_name"])) or canon
                def _form_of(s):
                    for tok in re.findall(r"[A-Za-z]+", str(s or "").lower()):
                        if tok in _FORM_WORDS:
                            return _FORM_WORDS[tok]
                    return None

                # Variant-aware refinement: when the row names a form
                # («سالبوتامول شربت»), use ONLY the matching variant's strengths
                # so the syrup row can't inherit the tablet's 4 mg.
                variants = e.get("variants") or []
                v_use = variants
                if variants and form:
                    matched = [v for v in variants
                               if _form_of(v.get("dosage_form")) == form]
                    if matched:
                        v_use = matched
                if len(v_use) > 1:
                    # Pen/container type disambiguates (SoloStar vs Penfill
                    # cartridge are different priced products): keep variants
                    # whose container tokens appear in the row name.
                    low = str(name).lower()
                    pen_hits = [t for t in ("solostar", "flexpen", "kwikpen",
                                            "penfill", "cartridge", "pen",
                                            "قلم", "کارتریج")
                                if t in low]
                    if pen_hits:
                        contained = [v for v in v_use if v.get("container") and
                                     any(t in str(v["container"]).lower()
                                         for t in pen_hits)]
                        if contained:
                            v_use = contained
                if len(v_use) > 1:
                    # pack size disambiguates further: «…30 g GEL» keeps only
                    # the 30 g variant (identical form/strength in a different
                    # pack is a different priced product).
                    row_nums = set(re.findall(
                        r"\d+(?:\.\d+)?", str(name).translate(_DIGIT_FIX)))
                    packed = [v for v in v_use if v.get("pack_size") and
                              set(re.findall(r"\d+(?:\.\d+)?",
                                             str(v["pack_size"]))) & row_nums]
                    if packed:
                        v_use = packed
                if not form:
                    v_forms = {f for f in (_form_of(v.get("dosage_form"))
                                           for v in variants) if f}
                    if len(v_forms) == 1:
                        form = next(iter(v_forms))
                e_strengths = ([s for v in v_use
                                for s in (v.get("strength"), v.get("concentration"))
                                if s]
                               if variants else (e.get("strengths") or []))
                if e_strengths:
                    for s_disp in e_strengths:
                        strengths |= set(re.findall(
                            r"\d+(?:\.\d+)?", str(s_disp).translate(_DIGIT_FIX)))
                    row_mg = row_mg | _strength_mg(" ".join(map(str, e_strengths)))
                if not form and e.get("dosage_form"):
                    form = _form_of(e["dosage_form"]) or form

        best: tuple[float, CatalogRecord | None, str] = (0.0, None, "none")
        for rec, c_canon, c_str, c_form, c_mg in (latin_block.get(canon[:3], []) if canon else []):
            name_sim = SequenceMatcher(None, canon, c_canon).ratio()
            # ── ingredient-agreement floor ────────────────────────────────────
            # The 3-char prefix block is a SPEED optimization, never evidence of
            # identity. When the true ingredient is absent from NFI, agreeing
            # strength+form used to carry a same-prefix DIFFERENT MOLECULE over
            # the review line: Rivanol (ethacridine, antiseptic) → rivaroxaban
            # (anticoagulant), Perforan (St John's wort) → perampanel,
            # methylphenidate → metaproterenol, CEPHALEXIN → cefixime,
            # calcium folinate → calcitonin. Measured on those real pairs: every
            # wrong one scores ≤0.615 while true equivalences (alendronate↔
            # alendronic acid 0.692, cefalexin↔cephalexin 0.842) sit above —
            # genuine synonyms are handled upstream by canonical_ingredient, so
            # a low score here means a DIFFERENT drug. Refusing leaves the row
            # unmatched for enrichment, which is the honest answer.
            if name_sim < INGREDIENT_FLOOR:
                continue
            # …and the discriminating parts must agree. Shared filler words
            # ("calcium", "dihydrochloride") inflate raw similarity and hide a
            # different molecule: calcium folinate ↔ calcium gluconate scores
            # 0.788, trientine ↔ trimetazidine dihydrochloride 0.889.
            if not sm.ingredient_agrees(canon, c_canon):
                continue
            has_extra = bool(strengths) or bool(form)
            if has_extra:
                # strength agreement: mg-normalized (0.05 mg == 50 microgram),
                # falling back to raw digit-token overlap for unit-less strengths
                s_match = _mg_agree(row_mg, c_mg) or bool(strengths and c_str and (strengths & c_str))
                s_score = 1.0 if s_match else 0.0
                f_score = 1.0 if (form and form == c_form) else 0.0
                score = 0.6 * name_sim + 0.25 * s_score + 0.15 * f_score
                # DEFINITE strength conflict (both give mg doses, none agree) ⇒ a
                # different product of the same generic; hold below the auto-apply
                # line so it goes to human review, never silently spreading a
                # wrong-strength reference price (e.g. octreotide 30mg → 50mcg).
                if row_mg and c_mg and not _mg_agree(row_mg, c_mg):
                    score = min(score, 0.6)
            else:
                score = name_sim
            if score > best[0]:
                best = (score, rec, "ingredient")
        if fa:                                          # persian trade-name path
            for rec, _c_canon, _c_str, _c_form, _c_mg in fa_block.get(fa[:2], []):
                fa_sim = SequenceMatcher(None, fa, rec.name_fa).ratio()
                if fa_sim > best[0]:
                    best = (fa_sim, rec, "persian_name")
        score, rec, method = best
        # ── price picks the form when the name doesn't state one ──────────────
        # A form-less truncated name («PROMETHAZINE HCL») matches an arbitrary
        # member of its ingredient family, and the insurer price then lands on
        # the wrong product — promethazine tablet 180﷼ receiving the injection's
        # 539,362﷼. When the row carries a reference price and the family spans
        # several forms, the price identifies which one.
        if rec is not None and not form and method == "ingredient":
            fam = sm.family_of(rec, fam_idx)
            if len({str(r.dosage_form or "") for r in fam}) > 1:
                picked = sm.price_picks_form(fam, row.get("reference_price"))
                if picked is not None and picked.irc != rec.irc:
                    rec = picked
                    method = "ingredient+price_form"
                    # a form inferred from price is evidence, not proof
                    score = min(score, 0.74)
        if code_assisted and rec is not None:
            # Signals came from the code-joined rich name. Review-first: a
            # cross-insurer join is powerful but NEW evidence — cap under the
            # auto-apply threshold so the owner confirms each pairing once;
            # the crosswalk then makes it permanent (and tier-1 thereafter).
            method = f"{method}+code"
            score = min(score, 0.74)
        out.append(LinkResult(row, rec if score >= 0.45 else None,
                              round(score, 3), method))
    return out


# ── conditions column (شرایط تعهد) ───────────────────────────────────────────
# salamat encodes real coverage policy as free Persian text in one column:
# whether the drug is insured at all, the organization share, and the
# prescribing restrictions. Treating it as a boolean threw all of that away AND
# marked 610 explicitly NON-INSURED rows as covered. Deterministic phrase
# matching; the raw text is always preserved alongside the parse.
_COND_FLAGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("specialist",       ("تجویز توسط پزشک متخصص", "پزشک متخصص")),
    ("subspecialist",    ("فوق تخصص",)),
    ("gp_allowed",       ("پزشکان عمومی",)),
    ("inpatient_only",   ("بیمارستانی",)),
    ("file_required",    ("پرونده ای", "پرونده‌ای")),
    ("pharmacy_approval", ("تایید در داروخانه", "تأیید در داروخانه")),
    ("authenticity_code", ("کد اصالت",)),
    ("domestic_only",    ("تولید داخل",)),
    ("fx_subsidy",       ("یارانه ارزی",)),
    ("hard_to_treat",    ("صعب العلاج",)),
    ("price_stability",  ("ثبات قیمت",)),
)
_NOT_INSURED = ("غیر بیمه", "غیربیمه")
_SHARE_IN_TEXT = re.compile(r"سهم\s*سازمان\s*(\d+)\s*درصد")
_AGE_RANGE = re.compile(r"بیش\s*از\s*(\d+)\s*سال\D*?کمتر\s*از\s*(\d+)\s*سال")


def _fold_fa(s) -> str:
    """Arabic yeh/kaf → Persian and whitespace collapse, so phrase matching
    survives the mixed spellings real Iranian files use."""
    t = str(s or "").replace("ي", "ی").replace("ك", "ک").replace("‌", " ")
    return re.sub(r"\s+", " ", t).strip()


def parse_conditions(text) -> dict:
    """Coverage policy encoded in the شرایط تعهد text.
    → {covered?, share_pct?, flags[], age_min?, age_max?, text}"""
    t = _fold_fa(text)
    if not t:
        return {}
    out: dict = {"text": t[:300]}
    if any(p in t for p in _NOT_INSURED):
        out["covered"] = False              # the source says so explicitly
    m = _SHARE_IN_TEXT.search(t)
    if m:
        try:
            out["share_pct"] = int(m.group(1))
        except ValueError:
            pass
    a = _AGE_RANGE.search(t)
    if a:
        out["age_min"], out["age_max"] = int(a.group(1)), int(a.group(2))
    flags = [name for name, phrases in _COND_FLAGS
             if any(_fold_fa(p) in t for p in phrases)]
    if flags:
        out["flags"] = flags
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
        # the شرایط تعهد text is policy, and it OVERRIDES the columns: it is where
        # salamat states that a drug is not insured at all, what the real
        # organization share is, and who may prescribe it.
        cond = parse_conditions(link.row.get("conditions"))
        if cond:
            if cond.get("covered") is False:
                entry["covered"] = False
            if cond.get("share_pct") is not None:
                entry["share_pct"] = cond["share_pct"]
            if cond.get("flags"):
                entry["restrictions"] = cond["flags"]
                if "inpatient_only" in cond["flags"]:
                    entry["inpatient"] = True
            for k in ("age_min", "age_max"):
                if cond.get(k) is not None:
                    entry[k] = cond[k]
            entry["conditions_text"] = cond["text"]
        entry["match_confidence"] = link.confidence
        entry["match_method"] = link.method

        if link.confidence < min_confidence and link.method != "irc":
            review.append({"row": link.row, "irc": link.record.irc,
                           "name": link.record.name_fa, "confidence": link.confidence,
                           "entry": entry})
            continue
        # ingredient_key = generic|strength|form, so this group is ONLY the
        # clinically interchangeable set (same product, different brands) — a
        # 15 mg row's entry (incl. reference_price, which insurers define per
        # interchangeable group) can never reach the 30 mg sibling.
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
