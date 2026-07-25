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
from difflib import SequenceMatcher

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


# Salt / hydrate / qualifier words that modify an ingredient without identifying
# it. Present on one side only, they are noise; present on BOTH, they inflate
# string similarity and hide that the drugs differ.
FILLER_TOKENS = frozenset((
    "acid", "sodium", "potassium", "calcium", "magnesium", "aluminum", "aluminium",
    "hydrochloride", "dihydrochloride", "hydrobromide", "sulfate", "sulphate",
    "acetate", "mesylate", "maleate", "tartrate", "bitartrate", "phosphate",
    "citrate", "chloride", "bromide", "nitrate", "oxide", "hydroxide",
    "carbonate", "gluconate", "lactate", "benzoate", "salicylate", "stearate",
    "succinate", "fumarate", "valerate", "propionate", "palmitate", "besilate",
    "dihydrate", "monohydrate", "trihydrate", "anhydrous", "concentrated",
    "compound", "complex", "combination", "ion", "base", "salt",
    # dosage-form and presentation words: the form is compared separately, so
    # their presence on one side only must not defeat ingredient agreement
    # («PIRACETAM … LIQUID» vs "piracetam" is the same drug)
    "solution", "injection", "liquid", "syrup", "suspension", "powder",
    "tablet", "capsule", "drops", "drop", "spray", "cream", "ointment", "gel",
    "elixir", "emulsion", "lotion", "granule", "granules", "sachet", "vial",
    "ampoule", "inhaler", "suppository", "pessary", "patch", "lozenge"))

# Calibrated on real Iranian formulary pairs, ordered by similarity:
#   cefalexin↔cephalexin 0.842 · alendronate↔alendronic 0.762  ← same drug
#   trientine↔trimetazidine 0.727 · amino↔amikacin 0.615       ← different drugs
# 0.75 is the widest gap between those two groups.
_AGREE_TOKEN = 0.75          # discriminating tokens must reach this pairwise
_AGREE_WHOLE = 0.66          # and the remaining strings this overall


def ingredient_agrees(a: str, b: str) -> bool:
    """Do two canonical ingredient strings name the SAME active substance?

    Plain string similarity is not enough: shared filler words inflate it and
    conceal different molecules — "calcium folinate" vs "calcium gluconate"
    scores 0.788 on the shared "calcium", and "trientine dihydrochloride" vs
    "trimetazidine dihydrochloride" reaches 0.889 on the shared salt. So the
    comparison is made on what is LEFT after removing the tokens they share:

      calcium folinate / calcium gluconate  → folinate vs gluconate   → refuse
      trientine … / trimetazidine …         → trientine vs trimetazidine → refuse
      amino acid / amikacin                 → no token pair agrees    → refuse
      alendronate / alendronic acid         → alendronate ≈ alendronic → accept
      dextrose / anhydrous dextrose         → only a filler differs   → accept
      potassium chloride / potassiumchlorideconcentrated → substring  → accept
    """
    ta = [t for t in str(a or "").split() if len(t) >= 3]
    tb = [t for t in str(b or "").split() if len(t) >= 3]
    if not ta or not tb:
        return False
    shared = set(ta) & set(tb)
    ra = [t for t in ta if t not in shared]
    rb = [t for t in tb if t not in shared]
    if not ra and not rb:
        return True                          # identical up to token order
    if not ra or not rb:
        # one side carries extra words: fine only if they are all fillers
        extra = ra or rb
        return all(t in FILLER_TOKENS for t in extra)
    # both remainders are non-identifying (sodium fluoride vs fluoride ion):
    # the discriminating ingredient is what they share, so they agree
    if all(t in FILLER_TOKENS for t in ra + rb):
        return True
    # both sides have discriminating content — it must actually agree
    sa, sb = " ".join(ra), " ".join(rb)
    if SequenceMatcher(None, sa, sb).ratio() < _AGREE_WHOLE:
        return False
    for x in ra:
        for y in rb:
            if x in y or y in x:             # potassium ⊂ potassiumchloride…
                return True
            if SequenceMatcher(None, x, y).ratio() >= _AGREE_TOKEN:
                return True
    return False


def form_family(form: str) -> str:
    """Coarse family of an exact form string: 'INJECTION, POWDER, LYOPHILIZED,
    FOR SOLUTION' → 'INJECTION'. Lets a row that states only the family still
    reach the specific product, at lower confidence than an exact form hit."""
    return re.split(r"[,;(]", str(form or "").upper(), maxsplit=1)[0].strip()


def family_index(catalog) -> dict:
    """ATC-keyed product families, falling back to the canonical generic.

    ATC is the only reliable family key here: NFI spells the same substance
    differently across forms — promethazine tablets are "promethazine
    hydrochloride" while the injections are "isopromethazine hydrochloride" (an
    NFI typo) — so a generic_name family cannot see across the forms, and 128
    ATC codes carry more than one generic_name spelling. Grouping on ATC keeps
    the whole R06AD02 family (tablet 180﷼ … injection 550,000﷼) together.
    """
    idx: dict[str, list] = {}
    for rec in catalog or []:
        atc = str(getattr(rec, "atc", "") or "").strip().upper()
        key = f"atc:{atc}" if atc else None
        if key is None:
            cg = components(getattr(rec, "generic_name", "") or "")
            key = f"gen:{cg[0]}" if cg else None
        if key:
            idx.setdefault(key, []).append(rec)
    return idx


def family_of(rec, fam_idx: dict) -> list:
    """The ATC (or generic) family a record belongs to."""
    atc = str(getattr(rec, "atc", "") or "").strip().upper()
    if atc:
        return fam_idx.get(f"atc:{atc}", [])
    cg = components(getattr(rec, "generic_name", "") or "")
    return fam_idx.get(f"gen:{cg[0]}", []) if cg else []


def price_picks_form(candidates, ref_price, *, ratio: float = 3.0):
    """Use the insurer's reference price to choose WHICH FORM a form-less row means.

    Truncated insurer names often omit the dosage form («PROMETHAZINE HCL»), and
    the same generic exists in NFI in many forms at wildly different prices —
    promethazine is 180﷼ as a 25 mg tablet and 550,000﷼ as a 25 mg/1mL injection.
    Matching on the ingredient alone picks a form arbitrarily, and the insurer
    price then lands on the wrong product: the real case that produced a
    +299,546% price proposal on a tablet.

    The price is a strong form signal. Given the same-ingredient candidates and
    the row's reference price, return the candidate whose own announced price is
    closest in RATIO to it — but only when that candidate is at least `ratio`×
    closer than the runner-up, so a genuine stale price is never mistaken for a
    form mismatch. Returns None when the evidence is not decisive.
    """
    try:
        ref = float(ref_price or 0)
    except (TypeError, ValueError):
        return None
    if ref <= 0:
        return None
    scored = []
    for rec in candidates or []:
        try:
            p = float(getattr(rec, "announced_price", 0) or 0)
        except (TypeError, ValueError):
            continue
        if p <= 0:
            continue
        # symmetric log-distance: 2× too high and 2× too low score the same
        scored.append((max(ref, p) / min(ref, p), rec))
    if len(scored) < 2:
        return None
    scored.sort(key=lambda x: x[0])
    best, second = scored[0], scored[1]
    if best[0] * ratio <= second[0]:
        return best[1]
    return None


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
    salt: dict[tuple, list] = {}
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
            # salt-tolerant lane: NFI keeps the salt in the generic name
            # ("chlorpheniramine maleate") while a formulary may print the base
            # ("CHLORPHENIRAMINE 4 mg TABLET"). Keyed by first word so the
            # lookup stays O(1); resolved only when UNAMBIGUOUS (see match()).
            head = cg.split()[0]
            if head != cg:
                salt.setdefault((head, form), []).append((cg, entry))
    return {"exact": exact, "family": family, "salt": salt}


# confidence tiers — an exact controlled-vocabulary hit is strong evidence, but
# a combination row matched on one component is NOT (it could be the mono
# product), so it is held below the auto-apply line for human review.
CONF_EXACT_DOSE = 0.93
CONF_EXACT_NO_DOSE = 0.80
CONF_FAMILY_DOSE = 0.78
CONF_SALT = 0.76
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

    # salt-tolerant last resort, single-ingredient rows only: the formulary
    # prints the base and NFI keeps the salt. Accepted ONLY when exactly one
    # NFI ingredient extends the row's name — otherwise "INSULIN" would silently
    # pick one of insulin glargine / aspart / lispro.
    if not combo:
        g = generics[0]
        cands = index.get("salt", {}).get((g.split()[0], form)) or []
        widened = {cg for cg, _e in cands if cg.startswith(g + " ")}
        if len(widened) == 1:
            for cg, (rec, cat_doses, comps) in cands:
                if cg not in widened or len(comps) != 1:
                    continue
                if doses and cat_doses and not doses_agree(doses, cat_doses):
                    continue
                return rec, CONF_SALT, f"salt-tolerant ({g} → {cg})"
    return None, 0.0, ""
