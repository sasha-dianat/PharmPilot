"""Resolve a stock row to the national formulary (`drug_catalog.irc`).

Inventory was built against `drug_products` (US NDC, 16 demo rows). The real
catalogue is `drug_catalog` (39,184 Iranian rows keyed by IRC). This module
proposes the link; it never writes one. Auto-binding stock to the wrong
formulary row would put a wrong price and a wrong insurer coverage on a real
product — a worse outcome than leaving it unbound, which is at least visible.

Confidence tiers follow the same ladder the catalog matcher already uses, so a
reviewer reads one scale across the product:

  0.99  GTIN equality — a global trade item number is the product itself
  0.93  exact generic + strength + dosage form
  0.86  exact generic + strength (form absent on one side)
  0.78  exact generic only, and the catalogue offers exactly one such product
  ----  below 0.78 is not proposed; ambiguity is reported instead
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

CONF_GTIN = 0.99
CONF_GENERIC_STRENGTH_FORM = 0.93
CONF_GENERIC_STRENGTH = 0.86
CONF_GENERIC_UNIQUE = 0.78
FLOOR = 0.78

# Dosage-form vocabulary shared by both catalogues, folded to one token so
# "TAB"/"tablet"/"قرص" compare equal.
_FORM_SYNONYMS = {
    "tab": "tablet", "tabs": "tablet", "tablet": "tablet", "قرص": "tablet",
    "cap": "capsule", "caps": "capsule", "capsule": "capsule", "کپسول": "capsule",
    "inj": "injection", "injection": "injection", "amp": "injection",
    "ampoule": "injection", "vial": "injection", "آمپول": "injection",
    "syr": "syrup", "syrup": "syrup", "شربت": "syrup",
    "susp": "suspension", "suspension": "suspension", "سوسپانسیون": "suspension",
    "cream": "cream", "کرم": "cream", "oint": "ointment", "ointment": "ointment",
    "drop": "drop", "drops": "drop", "قطره": "drop",
    "supp": "suppository", "suppository": "suppository", "شیاف": "suppository",
}

_UNIT_TO_MG = {"mg": 1.0, "g": 1000.0, "gr": 1000.0, "mcg": 0.001, "µg": 0.001,
               "ug": 0.001}
_STRENGTH_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(mg|mcg|µg|ug|g|gr|iu|%)", re.I)


def _fold(s: str | None) -> str:
    """Normalise for comparison: NFKC, Arabic/Persian digit and letter variants,
    lowercase, collapsed whitespace."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    trans = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹يك", "01234567890123456789یک")
    return re.sub(r"\s+", " ", s.translate(trans).lower()).strip()


def normalize_form(value: str | None) -> str:
    f = _fold(value)
    if not f:
        return ""
    for token in re.split(r"[^\w؀-ۿ]+", f):
        if token in _FORM_SYNONYMS:
            return _FORM_SYNONYMS[token]
    return f.split(" ")[0] if f else ""


def normalize_strength(value: str | None) -> tuple[float, str] | None:
    """→ (magnitude, namespace). Masses collapse to mg so 1 g == 1000 mg;
    IU and % keep their own namespace because they are not convertible."""
    f = _fold(value)
    m = _STRENGTH_RE.search(f)
    if not m:
        return None
    mag = float(m.group(1).replace(",", "."))
    unit = m.group(2).lower()
    if unit in _UNIT_TO_MG:
        return round(mag * _UNIT_TO_MG[unit], 6), "mass"
    return round(mag, 6), unit


def normalize_generic(value: str | None) -> str:
    """Strip salt/ester qualifiers so 'metformin hcl' matches 'metformin'.
    Kept deliberately small: an aggressive stripper merges genuinely different
    molecules."""
    f = _fold(value)
    f = re.sub(r"\b(hcl|hydrochloride|sodium|potassium|sulfate|sulphate|"
               r"maleate|tartrate|besylate|mesylate|acetate|citrate|phosphate|"
               r"succinate|fumarate|as\s+base)\b", " ", f)
    return re.sub(r"\s+", " ", f).strip()


@dataclass
class BindingProposal:
    ndc11: str
    irc: str | None
    confidence: float
    method: str
    evidence: dict
    candidates: int = 1
    ambiguous: bool = False


def propose(product: dict, catalog: list[dict]) -> BindingProposal:
    """Propose the IRC for one `drug_products` row against catalogue candidates.

    `catalog` rows need: irc, gtin, generic_name, strength, dosage_form, name_fa.
    Ambiguity is reported, never broken by a tiebreak — two products that look
    identical on these fields differ on something this function cannot see.
    """
    ndc = product.get("ndc11") or ""

    # 1. GTIN — the strongest possible evidence.
    p_gtin = _fold(product.get("gtin"))
    if p_gtin:
        hits = [c for c in catalog if _fold(c.get("gtin")) == p_gtin]
        if len(hits) == 1:
            return BindingProposal(ndc, hits[0]["irc"], CONF_GTIN, "gtin",
                                   {"gtin": p_gtin})

    gen = normalize_generic(product.get("generic_name"))
    if not gen:
        return BindingProposal(ndc, None, 0.0, "no_generic_name", {})

    same_generic = [c for c in catalog
                    if normalize_generic(c.get("generic_name")) == gen]
    if not same_generic:
        return BindingProposal(ndc, None, 0.0, "generic_not_in_formulary",
                               {"generic": gen})

    p_str = normalize_strength(product.get("strength"))
    p_form = normalize_form(product.get("dosage_form"))

    # 2. generic + strength + form
    if p_str and p_form:
        hits = [c for c in same_generic
                if normalize_strength(c.get("strength")) == p_str
                and normalize_form(c.get("dosage_form")) == p_form]
        if len(hits) == 1:
            return BindingProposal(ndc, hits[0]["irc"], CONF_GENERIC_STRENGTH_FORM,
                                   "generic_strength_form",
                                   {"generic": gen, "strength": p_str, "form": p_form})
        if len(hits) > 1:
            return BindingProposal(ndc, None, CONF_GENERIC_STRENGTH_FORM,
                                   "ambiguous_generic_strength_form",
                                   {"generic": gen, "strength": p_str, "form": p_form,
                                    "irc_options": [h["irc"] for h in hits[:10]]},
                                   candidates=len(hits), ambiguous=True)

    # 3. generic + strength
    if p_str:
        hits = [c for c in same_generic
                if normalize_strength(c.get("strength")) == p_str]
        if len(hits) == 1:
            return BindingProposal(ndc, hits[0]["irc"], CONF_GENERIC_STRENGTH,
                                   "generic_strength",
                                   {"generic": gen, "strength": p_str})
        if len(hits) > 1:
            return BindingProposal(ndc, None, CONF_GENERIC_STRENGTH,
                                   "ambiguous_generic_strength",
                                   {"generic": gen, "strength": p_str,
                                    "irc_options": [h["irc"] for h in hits[:10]]},
                                   candidates=len(hits), ambiguous=True)

    # 4. generic alone, only if the formulary offers exactly one such product
    if len(same_generic) == 1:
        return BindingProposal(ndc, same_generic[0]["irc"], CONF_GENERIC_UNIQUE,
                               "generic_unique", {"generic": gen})

    return BindingProposal(ndc, None, 0.0, "ambiguous_generic",
                           {"generic": gen,
                            "irc_options": [c["irc"] for c in same_generic[:10]]},
                           candidates=len(same_generic), ambiguous=True)


def summarize(proposals: list[BindingProposal]) -> dict:
    """Split proposals into what an owner can accept in bulk and what needs a
    decision. Anything ambiguous is never in the bulk bucket."""
    bindable = [p for p in proposals if p.irc and p.confidence >= FLOOR]
    return {
        "total": len(proposals),
        "bindable": len(bindable),
        "ambiguous": sum(1 for p in proposals if p.ambiguous),
        "unmatched": sum(1 for p in proposals if not p.irc and not p.ambiguous),
        "by_method": {m: sum(1 for p in proposals if p.method == m)
                      for m in sorted({p.method for p in proposals})},
        "proposals": [
            {"ndc11": p.ndc11, "irc": p.irc, "confidence": p.confidence,
             "method": p.method, "evidence": p.evidence,
             "candidates": p.candidates, "ambiguous": p.ambiguous}
            for p in proposals
        ],
    }
