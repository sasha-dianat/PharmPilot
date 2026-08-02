"""Recover fields we already hold but never extracted.

Some gaps look like source gaps and are not. 1,206 catalog rows carry no
`strength`, and the instinct is to re-crawl NFI for them — but 687 of those rows
already have the dose sitting in `monograph.generic_full`, put there by the same
harvest that left the column empty. The parser takes `strength` from the
`composition` field; when that field is missing or unparseable it gives up,
even though NFI's own «نام عمومی» string states the dose plainly:

    GLICLAZIDE TABLET, EXTENDED RELEASE ORAL 60 mg
    INTERFERON BETA-1A INJECTION PARENTERAL 12000000 [iU] 0.5MILLILITER
    VITAMIN K1 (PHYTOMENADIONE) INJECTION PARENTERAL 10 mg/1mL 1MILLILITER

Re-crawling those pages would fetch the same bytes and produce the same empty
column, because the defect is downstream of the fetch. This module reads what we
have instead — no network, and it can be re-run safely.

Validated against ground truth before use: run over the 33,366 rows that DO have
a strength, the extractor reproduces it exactly for 86.9% and to the same dose
set for a further 11.1% — 98.0% agreement, 0.69% disagreement, and inspection of
the disagreements shows most are rows whose STORED value is truncated at the
column width or plainly wrong («VITAMIN B12» stored as strength "12").
"""
from __future__ import annotations

import re

# Route words NFI prints between the form and the dose.
_ROUTES = ("PARENTERAL", "INTRAVENOUS", "INTRAMUSCULAR", "SUBCUTANEOUS", "ORAL",
           "OPHTHALMIC", "TOPICAL", "RECTAL", "VAGINAL", "NASAL", "INHALATION",
           "SUBLINGUAL", "TRANSDERMAL", "OTIC", "ENDOTRACHEAL", "IRRIGATION",
           "INTRAVESICAL", "EPIDURAL", "INTRATHECAL", "BUCCAL", "DENTAL")

# A dose: number + unit, optionally "per" a denominator («10 mg/1mL», «1 g/5mL»).
_DOSE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:mcg|microgram|µg|ug|mg|kg|gr|g|%|\[\s*iU\s*\]|IU|units?)"
    r"(?:\s*/\s*\d+(?:[.,]\d+)?\s*(?:mcg|µg|ug|mg|g|mL|ml|L)\b)?", re.I)

# A trailing PACK SIZE, e.g. "0.5MILLILITER", "110GRAM", "1LITER".
# It must be preceded by whitespace: in «100000 [iU]/1g 15GRAM» the "1g" is the
# DENOMINATOR of a per-gram concentration, not a pack size. Stripping it left a
# dangling "100000 [iU]/" and made 412 rows disagree with ground truth until the
# lookbehind was added.
_TRAIL = re.compile(r"(?<=[^/])\s+\d+(?:[.,]\d+)?\s*(?:MILLILITERS?|MILLILITRES?|"
                    r"LITERS?|LITRES?|GRAMS?|MILLIGRAMS?|G|ML)\s*$", re.I)


def strength_from_generic_full(generic_full: str, dosage_form: str = "") -> str | None:
    """The dose stated in NFI's «نام عمومی» string, or None.

    Structure, not guesswork: cut past the dosage form, then past the route
    word, drop a trailing pack size, and take from the first dose token to the
    end. Returns None rather than a guess whenever the parse loses a
    denominator — a dangling separator means the answer is not trustworthy.
    """
    s = str(generic_full or "").strip()
    if not s:
        return None
    tail = s
    form = str(dosage_form or "").strip().upper()
    if form and form in s.upper():
        tail = s[s.upper().index(form) + len(form):]
    for r in _ROUTES:
        m = re.match(rf"\s*{r}\b", tail, re.I)
        if m:
            tail = tail[m.end():]
            break
    tail = _TRAIL.sub("", tail).strip()
    if not tail:
        return None
    hits = [m.group(0) for m in _DOSE.finditer(tail)]
    if not hits:
        return None
    start = tail.upper().index(hits[0].upper())
    out = re.sub(r"\s+", " ", tail[start:].strip())
    if not out or out.rstrip().endswith(("/", "+", "-")):
        return None
    return out


async def backfill_strength(db, *, dry_run: bool = True, limit: int | None = None) -> dict:
    """Fill an empty `strength` from `monograph.generic_full`.

    Never overwrites a stated strength, and writes nothing whose extraction
    yields no parseable dose. `ingredient_key` is recomputed in the same
    transaction, because strength is part of it — leaving the key stale would
    make the row unreachable by the very grouping the new strength enables.
    """
    from sqlalchemy import select
    from shared.models.drug_catalog import DrugCatalogItem
    from . import structural_match as sm
    from .schema import ingredient_key, volume_set

    rows = (await db.execute(select(DrugCatalogItem).where(
        (DrugCatalogItem.strength.is_(None)) | (DrugCatalogItem.strength == "")
    ))).scalars().all()

    filled, no_dose, no_source, samples = 0, 0, 0, []
    regrouped = 0
    for r in rows:
        gf = (r.monograph or {}).get("generic_full")
        if not gf:
            no_source += 1
            continue
        got = strength_from_generic_full(gf, r.dosage_form or "")
        if not got or not sm.dose_set(got):
            no_dose += 1
            continue
        if len(samples) < 12:
            samples.append({"irc": r.irc, "name_fa": r.name_fa,
                            "generic_full": gf[:70], "strength": got})
        if not dry_run:
            r.strength = got[:80]                     # DB column width
            vol = " ".join(x for x in (got, gf) if x)
            new_key = ingredient_key(r.generic_name or "", r.strength,
                                     r.dosage_form or "",
                                     volume=vol if volume_set(vol) else "")
            if new_key != r.ingredient_key:
                r.ingredient_key = new_key
                regrouped += 1
        filled += 1
        if limit and filled >= limit:
            break
    if not dry_run:
        await db.commit()
    return {"candidates": len(rows), "filled": filled, "no_dose_in_source": no_dose,
            "no_generic_full": no_source, "ingredient_key_changed": regrouped,
            "dry_run": dry_run, "samples": samples}
