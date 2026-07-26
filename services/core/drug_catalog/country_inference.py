"""Propose a country of origin for products whose NFI page never stated one.

NFI publishes country only inside the محصولات مشابه table, and that table is
absent from 5,336 product pages. The relationship is absolute — every page WITH
the table yields a country, every page without yields none — so crawling those
ids again cannot help; 1,743 of them have already been re-fetched with the
current parser and still have no country.

The manufacturer is the remaining evidence. A Persian-named firm's output is
Iranian 94.2% of the time across 23,978 known rows — but the exception is
systematic, not noise: Iranian factories also import. کارخانجات دارو پخش shows
926 Iranian products and 23 foreign ones, so a flat "Persian name ⇒ ایران" rule
would mislabel the imports of exactly the largest manufacturers, and country
feeds pricing and procurement.

So this module does not decide. It computes, per firm, the observed split from
that firm's OWN products whose country is known, attaches it to every proposal
as evidence, and leaves the ruling to the owner. Firms with no known-country
products, and firms that only ever import, are excluded outright.

Deterministic; no LLM. Approved values are written with a provenance stamp and
stay sticky, so a future crawl that finally supplies a real country replaces
them.
"""
from __future__ import annotations

MIN_EVIDENCE = 3          # a firm needs this many known-country products to speak
IRAN = "ایران"


async def firm_profiles(db) -> dict[str, dict]:
    """Per-manufacturer split of its OWN products whose country is known.
    → {firm: {iran, foreign, total, confidence}}"""
    from sqlalchemy import text
    rows = (await db.execute(text(r"""
        SELECT manufacturer,
               count(*) FILTER (WHERE country = :iran) AS iran,
               count(*) FILTER (WHERE country <> :iran) AS foreign_
        FROM drug_catalog
        WHERE manufacturer ~ '[؀-ۿ]'
          AND country IS NOT NULL AND country <> ''
        GROUP BY 1"""), {"iran": IRAN})).all()
    out: dict[str, dict] = {}
    for firm, iran, foreign in rows:
        total = (iran or 0) + (foreign or 0)
        if not total:
            continue
        out[firm] = {"iran": iran or 0, "foreign": foreign or 0, "total": total,
                     "confidence": round((iran or 0) / total, 4)}
    return out


async def propose(db, *, min_confidence: float = 0.0, limit: int = 5000) -> dict:
    """Country proposals for country-less products, each carrying the evidence
    the reviewer needs. Sorted most-confident first."""
    from sqlalchemy import text
    profiles = await firm_profiles(db)
    rows = (await db.execute(text(r"""
        SELECT irc, name_fa, generic_name, manufacturer
        FROM drug_catalog
        WHERE (country IS NULL OR country = '')
          AND manufacturer ~ '[؀-ۿ]'
        ORDER BY manufacturer, name_fa"""))).all()

    proposals, skipped = [], {"no_evidence": 0, "importer_only": 0, "low_confidence": 0}
    for irc, name_fa, generic, firm in rows:
        p = profiles.get(firm)
        if not p or p["total"] < MIN_EVIDENCE:
            skipped["no_evidence"] += 1
            continue
        if p["iran"] == 0:                      # this firm only ever imports
            skipped["importer_only"] += 1
            continue
        if p["confidence"] < min_confidence:
            skipped["low_confidence"] += 1
            continue
        proposals.append({
            "irc": irc, "name_fa": name_fa, "generic_name": generic,
            "manufacturer": firm, "proposed_country": IRAN,
            "confidence": p["confidence"],
            "evidence": f"{firm}: {p['iran']} ایرانی / {p['foreign']} خارجی",
            "firm_iran": p["iran"], "firm_foreign": p["foreign"],
        })
    proposals.sort(key=lambda x: (-x["confidence"], x["manufacturer"]))
    return {"proposals": proposals[:limit], "total": len(proposals),
            "skipped": skipped,
            "firms": {"usable": sum(1 for p in profiles.values()
                                    if p["total"] >= MIN_EVIDENCE and p["iran"]),
                      "importer_only": sum(1 for p in profiles.values() if not p["iran"]),
                      "insufficient": sum(1 for p in profiles.values()
                                          if p["total"] < MIN_EVIDENCE)}}


async def apply(db, ircs: list[str], *, staff_id=None) -> dict:
    """Write approved countries with a provenance stamp.

    Stamped in monograph (not field_overrides) on purpose: country is already a
    sticky field, so no crawl can erase this, but a crawl that DOES supply a real
    country will replace it — which is the outcome we want. An override would
    freeze an inference over the source's own answer.
    """
    from datetime import datetime, timezone
    from sqlalchemy import select
    from shared.models.drug_catalog import DrugCatalogItem

    if not ircs:
        return {"applied": 0}
    wanted = {p["irc"]: p for p in (await propose(db))["proposals"]}
    now = datetime.now(timezone.utc)
    applied = 0
    for irc in ircs:
        p = wanted.get(irc)
        if not p:
            continue
        row = (await db.execute(select(DrugCatalogItem).where(
            DrugCatalogItem.irc == irc))).scalar_one_or_none()
        if row is None or (row.country or "").strip():
            continue                            # never overwrite a real value
        row.country = p["proposed_country"]
        mono = dict(row.monograph or {})
        mono["country_provenance"] = {
            "country": "inferred", "basis": "manufacturer-nationality",
            "manufacturer": p["manufacturer"], "confidence": p["confidence"],
            "evidence": p["evidence"], "at": now.isoformat(),
            "note": "استنتاج از ملیت سازنده — صفحهٔ NFI کشور را اعلام نکرده بود",
        }
        row.monograph = mono
        applied += 1
    await db.commit()
    return {"applied": applied, "requested": len(ircs)}
