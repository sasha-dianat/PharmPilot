"""NFI internal-consistency auditor — spliced-monograph detection and repair.

Root cause (established 2026-07-22): legacy NFI product pages reference a
generic-entity id the site has since REUSED, so the page renders drug A's
product block (نام/IRC/قیمت/تولیدکننده) with drug B's monograph
(نام عمومی/شکل دارویی/ATC). The harvest faithfully imported those splices —
e.g. RANITIDINE 150 (امین) carrying follitropin/INJECTION/G03GA05.

This module finds such rows IN the catalog (same coherence check the harvest
gate now runs per page) and proposes a repair per row:
  1. donor sibling — a clean row for the same physical product (same GTIN, or
     same brand tokens + manufacturer) donates generic/ATC/form;
  2. otherwise the brand string itself (row truth) yields generic/strength/form.
Prices are sanity-checked against the donor family's envelope, never guessed.

Approved repairs are persisted as field_overrides (X3), so a future crawl of
the still-broken page cannot re-poison the row. Deterministic, no LLM.
"""
from __future__ import annotations

from .nfi import (brand_generic_tokens, brand_stated_form, brand_stated_generic,
                  page_coherence, _STRENGTH_IN_BRAND_RE)


async def load_vocab(db) -> tuple[set[str], dict[str, set[str]]]:
    """(known generic names, generic → ATC 4-prefix families) from the catalog
    and formulary snapshots. The families keep synonym pairs quiet."""
    from sqlalchemy import select
    from shared.models.drug_catalog import DrugCatalogItem
    from shared.models.formulary_snapshot import FormularySnapshot

    vocab: set[str] = set()
    families: dict[str, set[str]] = {}
    rows = (await db.execute(select(DrugCatalogItem.generic_name,
                                    DrugCatalogItem.atc))).all()
    for g, atc in rows:
        g = str(g or "").lower().strip()
        if len(g) < 5:
            continue
        vocab.add(g)
        fam = str(atc or "")[:4].upper()
        if fam:
            families.setdefault(g, set()).add(fam)
        first = g.split()[0]
        if len(first) >= 6:
            vocab.add(first)
            if fam:
                families.setdefault(first, set()).add(fam)
    for (n,) in (await db.execute(select(FormularySnapshot.raw_name).distinct())).all():
        toks = brand_generic_tokens(n)
        if toks and len(toks[0]) >= 6:
            vocab.add(toks[0])
        if len(toks) >= 2:
            vocab.add(" ".join(toks[:2]))
    return vocab, families


def _brand_key(brand, manufacturer) -> str | None:
    toks = brand_generic_tokens(brand)
    if not toks:
        return None
    return f"{' '.join(toks)}|{str(manufacturer or '').strip()}"


def _derive_from_brand(brand: str) -> dict:
    out: dict = {}
    toks = brand_generic_tokens(brand)
    if toks:
        out["generic_name"] = " ".join(toks)
    m = _STRENGTH_IN_BRAND_RE.search(str(brand or ""))
    if m:
        unit = (m.group(2) or "").lower()
        out["strength"] = f"{m.group(1)} {unit}".strip()
    stated = brand_stated_form(brand)
    if stated:
        out["dosage_form"] = stated
    return out


async def audit(db, limit: int = 1000) -> dict:
    """Scan nfi-harvest rows for spliced monographs → suspects with proposals.

    Each suspect: {irc, brand_name, name_fa, manufacturer, current{…},
    reasons[…], proposal{…}, donor{irc,…}|None, price_flag|None}."""
    from sqlalchemy import select
    from shared.models.drug_catalog import DrugCatalogItem

    vocab, families = await load_vocab(db)
    rows = (await db.execute(select(
        DrugCatalogItem.irc, DrugCatalogItem.name_fa, DrugCatalogItem.brand_name,
        DrugCatalogItem.generic_name, DrugCatalogItem.dosage_form,
        DrugCatalogItem.strength, DrugCatalogItem.atc, DrugCatalogItem.manufacturer,
        DrugCatalogItem.gtin, DrugCatalogItem.announced_price)
        .where(DrugCatalogItem.source.like("nfi%")))).all()

    from collections import Counter
    checked = []
    for r in rows:
        rec = {"irc": r.irc, "name_fa": r.name_fa, "brand_name": r.brand_name,
               "generic_name": r.generic_name, "dosage_form": r.dosage_form,
               "strength": r.strength, "atc": r.atc, "manufacturer": r.manufacturer,
               "gtin": r.gtin,
               "announced_price": float(r.announced_price) if r.announced_price is not None else None}
        rec["_reasons"] = page_coherence(rec, vocab, families)
        rec["_cand"] = brand_stated_generic(rec.get("brand_name") or rec.get("name_fa"), vocab)
        checked.append(rec)

    # majority vote per stated-generic family: when MOST products whose brand
    # states generic X carry monograph generic Y, that pairing is the site's
    # naming convention (glibenclamide→glyburide, co-amoxiclav→amoxicillin) —
    # a SPLICE is always a minority within its family (5 follitropin rows
    # among ~60 ranitidines). Applies only to the generic signal; a form
    # contradiction is per-variant and never excused by the family.
    family_votes: dict[str, Counter] = {}
    for rec in checked:
        if rec["_cand"]:
            family_votes.setdefault(rec["_cand"], Counter())[
                str(rec.get("generic_name") or "").lower()] += 1

    def _is_convention(rec) -> bool:
        if any(not x.startswith("generic:") for x in rec["_reasons"]):
            return False
        votes = family_votes.get(rec["_cand"] or "")
        if not votes:
            return False
        n = votes[str(rec.get("generic_name") or "").lower()]
        return n >= 3 and n / sum(votes.values()) >= 0.6

    suspects = [r for r in checked if r["_reasons"] and not _is_convention(r)]
    clean = [r for r in checked if not r["_reasons"]]

    # donor indexes over CLEAN rows only
    by_gtin = {r["gtin"]: r for r in clean if r.get("gtin")}
    by_brand_key: dict[str, dict] = {}
    prices_by_generic: dict[str, list[float]] = {}
    for r in clean:
        k = _brand_key(r.get("brand_name"), r.get("manufacturer"))
        if k and k not in by_brand_key:
            by_brand_key[k] = r
        g = str(r.get("generic_name") or "").lower()
        if g and r.get("announced_price"):
            prices_by_generic.setdefault(g, []).append(r["announced_price"])

    out = []
    for s in suspects[:limit]:
        bk = _brand_key(s.get("brand_name"), s.get("manufacturer"))
        donor = by_gtin.get(s.get("gtin") or "") or (by_brand_key.get(bk) if bk else None)
        if donor and s.get("_cand"):
            # NFI reuses GTINs across unrelated products — a donor is only
            # trusted when its generic agrees with what the brand string states
            from difflib import SequenceMatcher
            dg = str(donor.get("generic_name") or "").lower()
            ok = any(t in dg for t in s["_cand"].split()) or \
                SequenceMatcher(None, s["_cand"], dg[:len(s["_cand"]) + 8]).ratio() >= 0.7
            if not ok:
                donor = None
        derived = _derive_from_brand(s.get("brand_name") or s.get("name_fa") or "")
        if s.get("_cand"):
            derived["generic_name"] = s["_cand"]   # vocab-matched beats raw tokens
        proposal: dict = {}
        if donor:
            proposal["generic_name"] = donor["generic_name"]
            proposal["atc"] = donor["atc"]
            proposal["dosage_form"] = derived.get("dosage_form") or donor["dosage_form"]
            proposal["strength"] = derived.get("strength") or ""
        else:
            proposal = {"generic_name": derived.get("generic_name"),
                        "atc": None,
                        "dosage_form": derived.get("dosage_form"),
                        "strength": derived.get("strength") or ""}
        price_flag = None
        g = str(proposal.get("generic_name") or "").lower()
        env = prices_by_generic.get(g) or []
        if s.get("announced_price") and env:
            lo, hi = min(env), max(env)
            if not (0.2 * lo <= s["announced_price"] <= 5 * hi):
                price_flag = f"{s['announced_price']:.0f} خارج از بازهٔ هم‌گروه‌ها ({lo:.0f}–{hi:.0f})"
        out.append({
            "irc": s["irc"], "name_fa": s["name_fa"], "brand_name": s["brand_name"],
            "manufacturer": s["manufacturer"], "gtin": s["gtin"],
            "announced_price": s["announced_price"],
            "current": {k: s[k] for k in ("generic_name", "dosage_form", "strength", "atc")},
            "reasons": s["_reasons"],
            "proposal": proposal,
            "donor_irc": donor["irc"] if donor else None,
            "price_flag": price_flag,
        })
    out.sort(key=lambda x: (x["donor_irc"] is None, str(x["brand_name"] or "")))
    return {"suspects": out,
            "counts": {"checked": len(checked), "suspect": len(suspects),
                       "shown": len(out),
                       "with_donor": sum(1 for x in out if x["donor_irc"])}}


REPAIR_FIELDS = ("generic_name", "dosage_form", "strength", "atc")


async def apply_repairs(db, ircs: list[str], *, staff_id=None) -> dict:
    """Apply the auditor's proposal for the accepted IRCs. Each changed field
    becomes a field_override (crawl-proof) AND is written to the row now;
    ingredient_key is recomputed; the foreign monograph text is dropped."""
    from sqlalchemy import select
    from shared.models.drug_catalog import DrugCatalogItem
    from .crosswalk import set_override
    from .schema import ingredient_key

    report = await audit(db, limit=100_000)
    proposals = {s["irc"]: s for s in report["suspects"]}
    wanted = [i for i in ircs if i in proposals]
    repaired = 0
    for irc in wanted:
        s = proposals[irc]
        row = (await db.execute(select(DrugCatalogItem).where(
            DrugCatalogItem.irc == irc))).scalar_one_or_none()
        if row is None:
            continue
        for f in REPAIR_FIELDS:
            v = s["proposal"].get(f)
            setattr(row, f, v if v is not None else None)
            await set_override(db, irc, f, v, reason="nfi_integrity:spliced_page",
                               staff_id=staff_id)
        row.ingredient_key = ingredient_key(row.generic_name or "",
                                            row.strength or "",
                                            row.dosage_form or "")
        # the clinical texts belong to the OTHER drug — drop them, keep the audit trail
        row.monograph = {"integrity": {"spliced_page": True,
                                       "repaired": True,
                                       "reasons": s["reasons"],
                                       "donor_irc": s["donor_irc"]}}
        repaired += 1
    await db.commit()
    return {"requested": len(ircs), "repaired": repaired,
            "not_suspect": [i for i in ircs if i not in proposals]}
