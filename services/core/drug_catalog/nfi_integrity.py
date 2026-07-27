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
    """(known generic names, generic → ATC 4-prefix families) from the catalog.

    Vocabulary hygiene (hard-won, 2026-07-23):
    - ONLY the catalog's own generic_name column qualifies — formulary raw
      names are PRODUCT names, and ingesting them taught the checker that
      trade names (AVASTIN) were generics, which made the gate falsely
      quarantine clean rows (bevacizumab → 'avastin') during harvest.
    - Rows the gate flagged and nobody repaired are EXCLUDED as sources:
      their generic may itself be a gate-written brand token, and letting
      them vote poisons the vocabulary with the gate's own mistakes."""
    from sqlalchemy import select, or_, func
    from shared.models.drug_catalog import DrugCatalogItem

    vocab: set[str] = set()
    families: dict[str, set[str]] = {}
    flagged = func.coalesce(
        DrugCatalogItem.monograph["integrity"]["spliced_page"].astext, "") == "true"
    repaired = func.coalesce(
        DrugCatalogItem.monograph["integrity"]["repaired"].astext, "") == "true"
    rows = (await db.execute(
        select(DrugCatalogItem.generic_name, DrugCatalogItem.atc)
        .where(or_(~flagged, repaired)))).all()
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
    return vocab, families


# chemistry vocabulary too common to identify a drug — never grounds agreement
_COMMON_CHEM_TOKENS = frozenset((
    "acid", "sodium", "potassium", "calcium", "magnesium", "aluminum",
    "aluminium", "hydrochloride", "sulfate", "sulphate", "acetate", "mesylate",
    "maleate", "tartrate", "phosphate", "citrate", "chloride", "bromide",
    "nitrate", "oxide", "hydroxide", "carbonate", "gluconate", "lactate",
    "benzoate", "salicylate", "stearate", "succinate", "fumarate", "valerate",
    "propionate", "palmitate", "dihydrate", "monohydrate", "trihydrate",
    "anhydrous", "compound", "complex", "extract", "injection", "solution"))


def _distinctive(tok: str) -> bool:
    return len(tok) >= 6 and tok not in _COMMON_CHEM_TOKENS


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
        DrugCatalogItem.gtin, DrugCatalogItem.announced_price,
        DrugCatalogItem.monograph)
        .where(DrugCatalogItem.source.like("nfi%")))).all()

    from collections import Counter
    checked = []
    for r in rows:
        rec = {"irc": r.irc, "name_fa": r.name_fa, "brand_name": r.brand_name,
               "generic_name": r.generic_name, "dosage_form": r.dosage_form,
               "strength": r.strength, "atc": r.atc, "manufacturer": r.manufacturer,
               "gtin": r.gtin,
               "announced_price": float(r.announced_price) if r.announced_price is not None else None}
        integ = ((r.monograph or {}).get("integrity") or {}) if isinstance(r.monograph, dict) else {}
        rec["_gate_flag"] = bool(integ.get("spliced_page")) and not integ.get("repaired")
        rec["_gate_reasons"] = [str(x) for x in (integ.get("reasons") or [])]
        rec["_reasons"] = page_coherence(rec, vocab, families)
        # gate-flagged rows join the suspect flow even when they now LOOK
        # coherent — the gate may have rewritten generic from the brand token
        # (correct for true splices, wrong for trade names like AVASTIN), and
        # either way the row lost its monograph/ATC; a donor completes it.
        if rec["_gate_flag"] and not rec["_reasons"]:
            rec["_reasons"] = [f"gate: {x}" for x in rec["_gate_reasons"]] or ["gate: flagged"]
        rec["_cand"] = brand_stated_generic(rec.get("brand_name") or rec.get("name_fa"), vocab)
        # the generic the ORIGINAL page's monograph stated, preserved in the
        # gate's reason text — the strongest donor-agreement anchor for rows
        # whose brand token turned out to be a trade name (AVASTIN, CELLCEPT)
        import re as _re
        rec["_orig_generic"] = next(
            (m.group(1).strip().lower() for x in rec["_gate_reasons"]
             if (m := _re.search(r"monograph says (.+)$", x))), None)
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
    by_brand_token: dict[str, list[dict]] = {}
    prices_by_generic: dict[str, list[float]] = {}
    for r in clean:
        k = _brand_key(r.get("brand_name"), r.get("manufacturer"))
        if k and k not in by_brand_key:
            by_brand_key[k] = r
        toks = brand_generic_tokens(r.get("brand_name"))
        if toks:
            bucket = by_brand_token.setdefault(toks[0], [])
            if len(bucket) < 8:
                bucket.append(r)
        g = str(r.get("generic_name") or "").lower()
        if g and r.get("announced_price"):
            prices_by_generic.setdefault(g, []).append(r["announced_price"])

    out = []
    for s in suspects[:limit]:
        bk = _brand_key(s.get("brand_name"), s.get("manufacturer"))

        def _agrees(donor_rec) -> bool:
            """NFI reuses GTINs across unrelated products — a donor is only
            trusted when its generic agrees with what we independently know:
            the brand-stated generic, or the original monograph generic the
            gate recorded (CELLCEPT's GTIN donor was lamivudine without this).

            Agreement demands a DISTINCTIVE shared stem. Matching on common
            chemistry words was catastrophic: 'acid' ∈ 'sodium acid
            pyrophosphate' once validated that donor for mycophenolic acid."""
            from difflib import SequenceMatcher
            dg = str(donor_rec.get("generic_name") or "").lower()
            anchor = s.get("_cand") or s.get("_orig_generic")
            if not anchor:
                return True                      # nothing to check against
            a_toks = [t for t in anchor.split() if _distinctive(t)]
            d_toks = [t for t in dg.split() if _distinctive(t)]
            if any(at == dt or at[:8] == dt[:8]      # exact or salt/ester stem
                   for at in a_toks for dt in d_toks):
                return True
            return SequenceMatcher(None, anchor, dg).ratio() >= 0.75

        donor = by_gtin.get(s.get("gtin") or "")
        if donor and not _agrees(donor):
            donor = None                         # GTIN reuse — try the brand key
        if donor is None and bk:
            donor = by_brand_key.get(bk)
            if donor and not _agrees(donor):
                donor = None
        if donor is None:
            # last resort: a clean row wearing the SAME brand token whose
            # generic agrees with the anchor. This is what distinguishes a
            # trade-name FP (clean AVASTIN rows are bevacizumab → restore)
            # from a true splice (no clean LACTOSE row is haloperidol → the
            # foreign monograph stays refused and the row goes to review).
            toks = brand_generic_tokens(s.get("brand_name") or s.get("name_fa"))
            cands = by_brand_token.get(toks[0], []) if toks else []
            for c in sorted(cands, key=lambda x: x.get("atc") is None):
                if _agrees(c):
                    donor = c
                    break
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
        # Coverage was matched against the OLD (foreign) identity — its
        # reference_price/share belong to the other drug (ranitidine 150 tab
        # wearing follitropin's 16,425,650﷼). Identity changed ⇒ that link is
        # void; clear it so the next coverage run re-matches the true molecule.
        # (ingredient_key changed too, so the stale entry could never self-heal.)
        cleared_cov = bool(row.coverage)
        row.coverage = None
        # the clinical texts belong to the OTHER drug — drop them, keep the audit trail
        row.monograph = {"integrity": {"spliced_page": True,
                                       "repaired": True,
                                       "coverage_cleared": cleared_cov,
                                       "reasons": s["reasons"],
                                       "donor_irc": s["donor_irc"]}}
        repaired += 1
    await db.commit()
    return {"requested": len(ircs), "repaired": repaired,
            "not_suspect": [i for i in ircs if i not in proposals]}
