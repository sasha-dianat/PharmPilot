"""IRC succession — carry decided data across a re-registration.

Every decided fact is anchored to an IRC: field overrides, insurer coverage,
crosswalk pointers. When NFI re-registers a product under a new IRC, the new row
arrives empty and the old one keeps data nobody will ever see again.

Two candidate anchors were measured against the real data before choosing one:

  * GTIN — rejected. 2,944 GTINs sit on more than one IRC, but the value is
    mostly a placeholder ('0', '00000000000000') and even the well-formed ones
    reach 77 IRCs spanning 33 different ingredient keys. It identifies a
    manufacturer's barcode range, not a product.
  * the /NFI/Detail page id — kept. Across 10,487 audited pages, ZERO pages
    carry more than one IRC: a page is one registration. That is exactly what
    makes it usable as a time series — the same page id observed with a
    DIFFERENT IRC on a later pass is a re-registration, and nothing else.

So this detector is deliberately evidence-only: with a single audit pass it
proposes nothing (correctly). It arms itself as soon as a second pass runs over
ground already covered — which is why audit mode grew a re-scan option.

The owner can also enter a succession by hand when they know of one; the
carry-over machinery is identical either way, and nothing moves until applied.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import nfi_audit

# fields on the old row that a successor should inherit when it has none
CARRY_FIELDS = ("country", "license_owner", "brand_owner")


def observations(index: Path | None = None) -> dict[int, list[str]]:
    """page_id → IRCs seen on it, in observation order (append-only log).

    A page with a single IRC is stable. Two or more means the registration
    behind that page changed between passes."""
    path = index or nfi_audit.INDEX
    seen: dict[int, list[str]] = {}
    if not path.exists():
        return seen
    for line in path.open(encoding="utf-8"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("error") or d.get("http"):
            continue
        pid, irc = d.get("page_id"), d.get("irc")
        if pid is None or not irc:
            continue
        hist = seen.setdefault(int(pid), [])
        if not hist or hist[-1] != str(irc):
            hist.append(str(irc))
    return seen


def detect(index: Path | None = None) -> list[dict]:
    """[{page_id, old_irc, new_irc, confidence, evidence}] — one per observed
    change, oldest→newest chained pairwise."""
    out: list[dict] = []
    for pid, hist in observations(index).items():
        for old, new in zip(hist, hist[1:]):
            out.append({
                "page_id": pid, "old_irc": old, "new_irc": new,
                # the page is a single registration slot: a change of the IRC it
                # serves is direct evidence, not an inference
                "confidence": 0.95,
                "evidence": {"source": "nfi_page_reregistration",
                             "url": f"https://irc.fda.gov.ir/NFI/Detail/{pid}",
                             "sequence": hist},
            })
    return out


async def sync_proposals(db, index: Path | None = None) -> dict:
    """Upsert detected successions as proposals. Never applies anything."""
    from sqlalchemy import select
    from shared.models.catalog_succession import CatalogSuccession

    found = detect(index)
    created = 0
    for f in found:
        exists = (await db.execute(select(CatalogSuccession).where(
            CatalogSuccession.old_irc == f["old_irc"],
            CatalogSuccession.new_irc == f["new_irc"]))).scalar_one_or_none()
        if exists is not None:
            continue
        db.add(CatalogSuccession(page_id=f["page_id"], old_irc=f["old_irc"],
                                 new_irc=f["new_irc"], status="proposed",
                                 confidence=f["confidence"], evidence=f["evidence"]))
        created += 1
    await db.commit()
    return {"detected": len(found), "created": created}


def same_product_refusal(old, new) -> str | None:
    """Why these two rows are NOT one product re-registered, or None.

    A succession carries insurer coverage, field overrides and crosswalk
    pointers from the old IRC to the new, so the pair must be the same thing
    under a new registration. Two rows at different prices, or holding different
    pack counts, are two products. The 2026-08-06 modafinil pair looked
    identical on brand, manufacturer, strength, ATC and licence date until the
    packs separated them — 30 against 100 — and merging would have moved a
    30-pack's decided facts onto a 100-pack.

    Silence is not evidence: when either side lacks the field, it cannot refuse
    on it. Owner's rule, 2026-08-09: differing price means differing row.
    """
    if old is None or new is None:
        return None
    po, pn = getattr(old, "announced_price", None), getattr(new, "announced_price", None)
    if po and pn and int(po) != int(pn):
        return (f"قیمت اعلامی این دو ردیف یکی نیست ({int(po):,} در برابر {int(pn):,}) — "
                "دو فرآوردهٔ متفاوت‌اند، نه یک ثبت تازه. جانشینی ثبت نشد.")
    co, cn = getattr(old, "package_count", None), getattr(new, "package_count", None)
    if co and cn and co != cn:
        return (f"تعداد بسته یکی نیست ({co} در برابر {cn}) — "
                "دو ارائهٔ متفاوت‌اند. جانشینی ثبت نشد.")
    return None


async def propose_manual(db, old_irc: str, new_irc: str, *, staff_id=None) -> dict:
    """An owner-entered succession — same carry-over, evidence marked manual."""
    from sqlalchemy import select
    from shared.models.catalog_succession import CatalogSuccession
    from shared.models.drug_catalog import DrugCatalogItem
    if not old_irc or not new_irc or old_irc == new_irc:
        raise RuntimeError("دو IRC متفاوت لازم است.")

    # A succession CARRIES coverage and overrides across, so the two rows must be
    # the same thing sold under a new registration. Two rows at different prices
    # are two products — the 2026-08-06 modafinil pair looked identical until the
    # pack counts (30 vs 100) and prices separated them, and merging would have
    # moved a 30-pack's decided facts onto a 100-pack. Owner's rule: differing
    # price means differing row.
    pair = {r.irc: r for r in (await db.execute(select(DrugCatalogItem).where(
        DrugCatalogItem.irc.in_([old_irc, new_irc])))).scalars().all()}
    refusal = same_product_refusal(pair.get(old_irc), pair.get(new_irc))
    if refusal:
        raise RuntimeError(refusal)

    row = (await db.execute(select(CatalogSuccession).where(
        CatalogSuccession.old_irc == old_irc,
        CatalogSuccession.new_irc == new_irc))).scalar_one_or_none()
    if row is None:
        row = CatalogSuccession(old_irc=old_irc, new_irc=new_irc, status="proposed",
                                confidence=1.0, evidence={"source": "manual"},
                                decided_by=staff_id)
        db.add(row)
    await db.commit()
    return {"id": str(row.id), "status": row.status}


async def pending(db, limit: int = 200) -> list[dict]:
    from sqlalchemy import select
    from shared.models.catalog_succession import CatalogSuccession
    from shared.models.drug_catalog import DrugCatalogItem

    rows = list((await db.execute(select(CatalogSuccession)
                                  .where(CatalogSuccession.status == "proposed")
                                  .limit(limit))).scalars().all())
    ircs = {r.old_irc for r in rows} | {r.new_irc for r in rows}
    cat = {}
    if ircs:
        for it in (await db.execute(select(DrugCatalogItem)
                                    .where(DrugCatalogItem.irc.in_(list(ircs))))).scalars().all():
            cat[it.irc] = it
    def side(irc):
        it = cat.get(irc)
        return {"irc": irc, "exists": it is not None,
                "name_fa": getattr(it, "name_fa", None),
                "generic": getattr(it, "generic_name", None),
                "price": int(it.announced_price) if getattr(it, "announced_price", None) else None,
                "coverage": list((getattr(it, "coverage", None) or {}).keys())}
    return [{"id": str(r.id), "page_id": r.page_id, "confidence": r.confidence,
             "evidence": r.evidence, "old": side(r.old_irc), "new": side(r.new_irc)}
            for r in rows]


async def apply(db, ids: list[str], *, staff_id=None) -> dict:
    """Move every decided fact from the old IRC to the new one.

    Additive only: the successor keeps anything it already has, so applying a
    wrong succession cannot destroy good data — it can only add. What moved is
    recorded on the row so it stays explainable."""
    from sqlalchemy import select
    from shared.models.catalog_succession import CatalogSuccession
    from shared.models.crosswalk import CrosswalkEntry, FieldOverride
    from shared.models.drug_catalog import DrugCatalogItem

    rows = list((await db.execute(select(CatalogSuccession)
                                  .where(CatalogSuccession.id.in_(list(ids))))).scalars().all())
    now = datetime.now(timezone.utc)
    applied = skipped = 0
    for r in rows:
        if r.status != "proposed":
            skipped += 1
            continue
        old = (await db.execute(select(DrugCatalogItem).where(
            DrugCatalogItem.irc == r.old_irc))).scalar_one_or_none()
        new = (await db.execute(select(DrugCatalogItem).where(
            DrugCatalogItem.irc == r.new_irc))).scalar_one_or_none()
        if new is None:
            skipped += 1            # nothing to carry INTO yet
            continue
        carried: dict = {"coverage": [], "overrides": [], "crosswalk": 0, "fields": []}

        # 1. insurer coverage — per insurer, only where the successor has none
        if old is not None and isinstance(old.coverage, dict) and old.coverage:
            cov = dict(new.coverage or {})
            for insurer, entry in old.coverage.items():
                if insurer not in cov:
                    cov[insurer] = {**entry, "carried_from": r.old_irc}
                    carried["coverage"].append(insurer)
            if carried["coverage"]:
                new.coverage = cov

        # 2. owner field corrections
        olds = (await db.execute(select(FieldOverride).where(
            FieldOverride.irc == r.old_irc))).scalars().all()
        have = {o.field for o in (await db.execute(select(FieldOverride).where(
            FieldOverride.irc == r.new_irc))).scalars().all()}
        for o in olds:
            if o.field in have:
                continue
            db.add(FieldOverride(irc=r.new_irc, field=o.field, value=o.value,
                                 reason=f"carried from {r.old_irc}",
                                 decided_by=staff_id, decided_at=now))
            carried["overrides"].append(o.field)

        # 3. researched fields the successor's own page never published
        if old is not None:
            for f in CARRY_FIELDS:
                if not getattr(new, f, None) and getattr(old, f, None):
                    setattr(new, f, getattr(old, f))
                    carried["fields"].append(f)

        # 4. every formulary decision pointing at the old product
        for e in (await db.execute(select(CrosswalkEntry).where(
                CrosswalkEntry.irc == r.old_irc))).scalars().all():
            e.revised_from_irc, e.irc = e.irc, r.new_irc
            e.revised_at, e.decided_by = now, staff_id
            carried["crosswalk"] += 1

        # 5. leave a trail on both rows
        if old is not None:
            old.monograph = {**(old.monograph or {}), "superseded_by": r.new_irc,
                             "superseded_at": now.isoformat()}
        new.monograph = {**(new.monograph or {}), "succeeds": r.old_irc}

        r.status, r.carried = "applied", carried
        r.decided_by, r.decided_at = staff_id, now
        applied += 1
    await db.commit()
    return {"applied": applied, "skipped": skipped}


async def dismiss(db, ids: list[str], *, staff_id=None) -> dict:
    from sqlalchemy import select
    from shared.models.catalog_succession import CatalogSuccession
    rows = (await db.execute(select(CatalogSuccession)
                             .where(CatalogSuccession.id.in_(list(ids))))).scalars().all()
    now = datetime.now(timezone.utc)
    for r in rows:
        r.status, r.decided_by, r.decided_at = "dismissed", staff_id, now
    await db.commit()
    return {"dismissed": len(list(rows))}


# ── gap 3: the page id every catalog row was missing ────────────────────────
async def backfill_nfi_id(db, index: Path | None = None) -> dict:
    """Stamp monograph.nfi_id from the audit's irc→page map.

    Harvests write this field now, but the 39,184 rows already in the catalog
    predate it — so the only irc→page link lived in a CSV outside the database,
    and no query could take a suspect row back to its own source page."""
    import csv as _csv
    from sqlalchemy import select
    from shared.models.drug_catalog import DrugCatalogItem

    path = Path(index) if index else nfi_audit.IRCMAP
    if not path.exists():
        return {"mapped": 0, "updated": 0, "missing": 0}
    mapping: dict[str, int] = {}
    with path.open(encoding="utf-8") as fh:
        for row in _csv.DictReader(fh):
            irc, pid = (row.get("irc") or "").strip(), (row.get("page_id") or "").strip()
            if irc and pid.isdigit():
                mapping[irc] = int(pid)
    if not mapping:
        return {"mapped": 0, "updated": 0, "missing": 0}
    updated = 0
    items = (await db.execute(select(DrugCatalogItem)
                              .where(DrugCatalogItem.irc.in_(list(mapping))))).scalars().all()
    found = set()
    for it in items:
        found.add(it.irc)
        mono = dict(it.monograph or {})
        if mono.get("nfi_id") == mapping[it.irc]:
            continue
        mono["nfi_id"] = mapping[it.irc]
        it.monograph = mono
        updated += 1
    await db.commit()
    return {"mapped": len(mapping), "updated": updated,
            "missing": len(mapping) - len(found)}
