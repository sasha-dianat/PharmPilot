"""Price time-series recording & queries (Phase C).

record_price implements SCD type-2: a price change closes the current open row
(valid_to = now) and opens a new one; an unchanged price is a no-op. Prices are
therefore auditable and dated — a quote can pin to a date, and staleness is a
query (age of the current row) instead of a re-crawl heuristic.

Deterministic, offline-testable; no LLM. Recording is best-effort and must
never break an ingest, so callers wrap it defensively.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

PRICE_TYPES = ("announced", "invoice", "insurer_reference")


async def record_price(db, irc: str, price_type: str, value, *,
                       insurer: str | None = None, source: str | None = None,
                       at: datetime | None = None) -> str:
    """Append a dated price point. Returns 'inserted' | 'changed' | 'unchanged'
    | 'skipped'. SCD type-2: only a DIFFERENT value from the current open row
    creates history (and closes the previous)."""
    from sqlalchemy import select
    from shared.models.price_history import PriceHistory

    if not irc or price_type not in PRICE_TYPES:
        return "skipped"
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return "skipped"
    if v <= 0:
        return "skipped"
    now = at or datetime.now(timezone.utc)

    q = (select(PriceHistory)
         .where(PriceHistory.irc == irc, PriceHistory.price_type == price_type,
                PriceHistory.valid_to.is_(None)))
    q = q.where(PriceHistory.insurer == insurer) if insurer is not None \
        else q.where(PriceHistory.insurer.is_(None))
    current = (await db.execute(q.order_by(PriceHistory.valid_from.desc()).limit(1))
               ).scalar_one_or_none()

    if current is not None and current.value == v:
        return "unchanged"
    if current is not None:
        current.valid_to = now
    db.add(PriceHistory(irc=irc, price_type=price_type, insurer=insurer,
                        value=v, source=source, valid_from=now))
    return "changed" if current is not None else "inserted"


async def current_price(db, irc: str, price_type: str = "announced",
                        insurer: str | None = None):
    """The value of the current open row (valid_to IS NULL), or None."""
    from sqlalchemy import select
    from shared.models.price_history import PriceHistory
    q = (select(PriceHistory.value)
         .where(PriceHistory.irc == irc, PriceHistory.price_type == price_type,
                PriceHistory.valid_to.is_(None)))
    q = q.where(PriceHistory.insurer == insurer) if insurer is not None \
        else q.where(PriceHistory.insurer.is_(None))
    return (await db.execute(q.limit(1))).scalar_one_or_none()


async def stale_prices(db, *, max_age_days: int = 180, price_type: str = "announced",
                       limit: int = 200) -> dict:
    """Products whose CURRENT price is older than max_age_days — the structural
    replacement for the stale-price re-crawl guesswork. → {count, cutoff, samples}."""
    from sqlalchemy import select
    from shared.models.price_history import PriceHistory
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    q = (select(PriceHistory.irc, PriceHistory.value, PriceHistory.valid_from,
                PriceHistory.source)
         .where(PriceHistory.price_type == price_type,
                PriceHistory.valid_to.is_(None),
                PriceHistory.valid_from < cutoff)
         .order_by(PriceHistory.valid_from.asc()))
    rows = (await db.execute(q.limit(limit))).all()
    total = (await db.execute(
        select(PriceHistory.irc).where(PriceHistory.price_type == price_type,
                                       PriceHistory.valid_to.is_(None),
                                       PriceHistory.valid_from < cutoff))).scalars().all()
    return {"count": len(total), "max_age_days": max_age_days,
            "cutoff": cutoff.isoformat(),
            "samples": [{"irc": r.irc, "value": r.value,
                         "since": r.valid_from.isoformat() if r.valid_from else None,
                         "source": r.source} for r in rows]}


async def backfill_from_catalog(db, *, source: str = "catalog-backfill") -> dict:
    """One-time seed: open a current row for every catalog product's announced &
    invoice price that has no open history yet. Lets the time-series start from
    today's known prices without waiting for the next change. → counts."""
    from sqlalchemy import select
    from shared.models.drug_catalog import DrugCatalogItem
    from shared.models.price_history import PriceHistory

    have = set((await db.execute(
        select(PriceHistory.irc, PriceHistory.price_type)
        .where(PriceHistory.valid_to.is_(None)))).all())
    items = (await db.execute(select(DrugCatalogItem))).scalars().all()
    n = 0
    for it in items:
        for ptype, val, ts in (("announced", it.announced_price, it.announced_price_at),
                               ("invoice", it.last_invoice_price, it.last_invoice_at)):
            if val and (it.irc, ptype) not in have:
                # seed valid_from from the catalog's known price age, so existing
                # stale prices surface immediately instead of resetting the clock.
                r = await record_price(db, it.irc, ptype, val, source=source, at=ts)
                if r in ("inserted", "changed"):
                    n += 1
    await db.commit()
    return {"recorded": n}
