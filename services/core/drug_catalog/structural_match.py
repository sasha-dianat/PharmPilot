"""Structural matching on the shared IRC/FDA controlled vocabulary.

Discovery (2026-07-25): insurer دارونامه names and NFI's own columns are the
SAME controlled vocabulary, not two dialects to be fuzzy-matched. tamin writes

    OCTREOTIDE (AS ACETATE) 20 mg INJECTION, POWDER, FOR SUSPENSION, EXTENDED RELEASE PARENTERAL
    └─ generic ─┘ └─ salt ─┘ └dose┘ └────────────── dosage_form ──────────────┘ └─ route ─┘

and NFI stores exactly those pieces as generic_name / strength / dosage_form —
"INJECTION, POWDER, FOR SUSPENSION, EXTENDED RELEASE" appears VERBATIM in both.
86.5% of tamin names contain an exact NFI dosage_form string; 767 carry a
compound form (≥14 chars) that pins the variant precisely.

So instead of scoring string similarity against Persian brand names, we parse
the formulary name into its parts and look up (generic, exact form, dose). On
the real data this reaches 67.3% of tamin rows by itself, and — combined with
the national-code join that lends tamin's rich name to salamat's truncated one
(«CICLOSPORIN» → «CICLOSPORIN 100 mg CAPSULE, LIQUID FILLED ORAL») — it lifts
salamat from 6.4% to 50.7%.

Deterministic and offline-testable; no LLM, no network.
"""
from __future__ import annotations

import re

from .schema import canonical_ingredient, normalize

# Route vocabulary (نحوه مصرف). Present in ~2,250 tamin names; extracted so it
# never pollutes the generic head, and kept for future route-aware scoring once
# NFI's own route column is populated by a fresh crawl.
ROUTES = ("PARENTERAL", "INTRAVENOUS", "INTRAMUSCULAR", "SUBCUTANEOUS", "OPHTHALMIC",
          "INTRATHECAL", "INHALATION", "RESPIRATORY", "SUBLINGUAL", "TRANSDERMAL",
          "IRRIGATION", "INTRAOCULAR", "INTRAVESICAL", "INTRAUTERINE", "PERIODONTAL",
          "INTRAARTICULAR", "EPIDURAL", "BUCCAL", "VAGINAL", "RECTAL", "TOPICAL",
          "NASAL", "OTIC", "DENTAL", "ORAL")

_MASS_MG = {"mg": 1.0, "g": 1000.0, "gr": 1000.0, "mcg": 0.001, "ug": 0.001,
            "µg": 0.001, "microgram": 0.001, "kg": 1_000_000.0}
_MASS_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(mcg|microgram|µg|ug|mg|kg|gr|g)\b", re.I)
_PCT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
_IU_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:\[?\s*i\.?u\.?\s*\]?|units?)\b", re.I)


def dose_set(text) -> set:
    """Every dose in `text` as comparable tokens. Mass folds to mg (so 0.05 mg
    == 50 microgram compare equal); percentages and IU keep their own namespace
    because 0.1% is not 0.1 mg and 100 IU is not 100 mg."""
    s = str(text or "")
    out: set = set()
    for num, unit in _MASS_RE.findall(s):
        try:
            out.add(round(float(num.replace(",", ".")) * _MASS_MG[unit.lower()], 6))
        except (ValueError, KeyError):
            continue
    for num in _PCT_RE.findall(s):
        try:
            out.add(("pct", round(float(num.replace(",", ".")), 4)))
        except ValueError:
            continue
    for num in _IU_RE.findall(s):
        try:
            out.add(("iu", round(float(num.replace(",", ".")), 4)))
        except ValueError:
            continue
    return out


def doses_agree(a: set, b: set, tol: float = 0.01) -> bool:
    """Any dose in `a` matching any in `b` (1% tolerance on numbers)."""
    for x in a:
        for y in b:
            if isinstance(x, tuple) != isinstance(y, tuple):
                continue
            if isinstance(x, tuple):
                if x[0] == y[0] and abs(x[1] - y[1]) <= tol * max(x[1], y[1], 1e-9):
                    return True
            elif abs(x - y) <= tol * max(x, y, 1e-9):
                return True
    return False


def form_family(form: str) -> str:
    """Coarse family of an exact form string: 'INJECTION, POWDER, LYOPHILIZED,
    FOR SOLUTION' → 'INJECTION'. Lets a row that states only the family still
    reach the specific product, at lower confidence than an exact form hit."""
    return re.split(r"[,;(]", str(form or "").upper(), maxsplit=1)[0].strip()


def build_form_vocab(catalog) -> list[str]:
    """NFI's own dosage_form strings, longest first so the most specific form
    present in a name wins ('INJECTION, SOLUTION' before 'INJECTION')."""
    vocab = {str(getattr(r, "dosage_form", "") or "").strip().upper() for r in catalog}
    return sorted((v for v in vocab if len(v) >= 3), key=len, reverse=True)


def parse_name(name: str, form_vocab: list[str]) -> dict:
    """Split a formulary product name into its controlled-vocabulary parts.
    → {generics: [canonical…], form: exact NFI form|None, route, doses, combo}"""
    raw = str(name or "").strip()
    upper = raw.upper()
    form = next((f for f in form_vocab if f in upper), None)
    route = next((r for r in ROUTES if re.search(rf"\b{r}\b", upper)), None)

    head = upper
    if form:                                   # everything before the form phrase
        head = head.split(form, 1)[0]
    head = re.sub(r"\([^)]*\)", " ", head)     # drop "(AS ACETATE)" salt notes
    head = re.split(r"\bAS\s+[A-Z]+", head, maxsplit=1)[0]
    head = re.split(r"\d", head, maxsplit=1)[0]        # stop at the first dose digit
    parts = [p.strip() for p in re.split(r"[/+]", head) if p.strip()]

    generics: list[str] = []
    for p in parts:
        p = re.sub(r"[^A-Za-z\s-]", " ", p).strip()
        if len(p) < 3:
            continue
        cg = canonical_ingredient(normalize(p.lower())) or p.lower()
        cg = re.sub(r"\s+", " ", cg).strip()
        if cg and cg not in generics:
            generics.append(cg)
    return {"generics": generics, "form": form, "route": route,
            "doses": dose_set(raw), "combo": len(generics) > 1}


def components(text) -> list[str]:
    """Canonical active-ingredient components of a generic string, split on the
    combination separators. Must be computed BEFORE canonicalization of the
    whole string, which drops the '/' and would make a combination look like a
    single ingredient — that is how 'DACLATASVIR / SOFOSBUVIR' once matched a
    pure sofosbuvir product."""
    out: list[str] = []
    for part in re.split(r"[/+]|\band\b", str(text or "")):
        p = re.sub(r"\([^)]*\)", " ", part)
        p = re.sub(r"[^A-Za-z\s-]", " ", p).strip()
        if len(p) < 3:
            continue
        cg = re.sub(r"\s+", " ",
                    canonical_ingredient(normalize(p.lower())) or p.lower()).strip()
        if cg and cg not in out:
            out.append(cg)
    return out


def build_index(catalog) -> dict:
    """(canonical generic, exact form) → [(record, doses, component set)], plus a
    form-family index. Component sets let a combination match only another
    combination of the same ingredients, in either direction."""
    exact: dict[tuple, list] = {}
    family: dict[tuple, list] = {}
    for rec in catalog:
        comps = components(getattr(rec, "generic_name", "") or "")
        if not comps:
            continue
        form = str(getattr(rec, "dosage_form", "") or "").strip().upper()
        doses = dose_set(getattr(rec, "strength", "") or "")
        entry = (rec, doses, frozenset(comps))
        for cg in comps:                       # reachable by ANY of its components
            exact.setdefault((cg, form), []).append(entry)
            family.setdefault((cg, form_family(form)), []).append(entry)
    return {"exact": exact, "family": family}


# confidence tiers — an exact controlled-vocabulary hit is strong evidence, but
# a combination row matched on one component is NOT (it could be the mono
# product), so it is held below the auto-apply line for human review.
CONF_EXACT_DOSE = 0.93
CONF_EXACT_NO_DOSE = 0.80
CONF_FAMILY_DOSE = 0.78
CONF_COMBO_CAP = 0.70


def match(parsed: dict, index: dict) -> tuple[object | None, float, str]:
    """Best structural candidate for a parsed name → (record, confidence, why).
    Returns (None, 0.0, "") when the vocabulary does not resolve."""
    generics = parsed.get("generics") or []
    if not generics or not parsed.get("form"):
        return None, 0.0, ""
    form = parsed["form"]
    doses = parsed.get("doses") or set()
    want = frozenset(generics)
    combo = len(want) > 1

    for level in ("exact", "family"):
        lookup_form = form if level == "exact" else form_family(form)
        for cg in generics:
            for rec, cat_doses, comps in index[level].get((cg, lookup_form), []):
                # Ingredient sets must agree in BOTH directions: a combination
                # never lands on a mono product, and a mono row never lands on a
                # combination that merely contains it.
                if comps != want:
                    continue
                if doses and cat_doses and not doses_agree(doses, cat_doses):
                    continue
                if doses and cat_doses:
                    conf = CONF_EXACT_DOSE if level == "exact" else CONF_FAMILY_DOSE
                    why = f"{level} form + dose"
                else:
                    conf = CONF_EXACT_NO_DOSE if level == "exact" else CONF_FAMILY_DOSE
                    why = f"{level} form"
                if combo:
                    conf = min(conf, CONF_COMBO_CAP)
                    why += " (combination — confirm components)"
                return rec, conf, why
    return None, 0.0, ""
