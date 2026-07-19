"""Daily price-sync persistence: turn a feed into pending proposals, and apply
manager-approved proposals to the catalog."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.drug_catalog import DrugCatalogItem
from shared.models.drug_price_proposal import DrugPriceProposal
from . import repo
from .pricing_sync import compute_proposals
from .schema import ingredient_key


def _i(d) -> int | None:
    return int(d) if d is not None else None


async def run_sync(db: AsyncSession, incoming: list[dict], *, source: str = "sync",
                   min_pct: float = 0.0) -> dict:
    """Diff the feed against the catalog → replace pending proposals for the
    affected IRCs. Never mutates catalog prices directly. `min_pct` drops
    proposals whose |pct_change| is below the threshold (noise floor)."""
    ircs = [str(r.get("irc")).strip() for r in incoming if r.get("irc")]
    current = await repo.fetch_by_irc(db, ircs)
    proposals = compute_proposals(list(current.values()), incoming)
    if min_pct > 0:
        proposals = [p for p in proposals if abs(float(p.pct_change)) >= min_pct]

    if proposals:
        await db.execute(delete(DrugPriceProposal).where(
            DrugPriceProposal.irc.in_([p.irc for p in proposals]),
            DrugPriceProposal.status == "pending"))
    for p in proposals:
        db.add(DrugPriceProposal(
            irc=p.irc, name_fa=p.name, kind=p.kind, status="pending", source=source,
            current_announced=_i(p.current_announced), proposed_announced=_i(p.proposed_announced),
            current_invoice=_i(p.current_invoice), proposed_invoice=_i(p.proposed_invoice),
            current_effective=int(p.current_effective), proposed_effective=int(p.proposed_effective),
            delta=int(p.delta), pct_change=float(p.pct_change)))
    await db.commit()

    by_kind: dict[str, int] = {}
    for p in proposals:
        by_kind[p.kind] = by_kind.get(p.kind, 0) + 1
    return {"feed_rows": len(incoming), "proposals_created": len(proposals), "by_kind": by_kind}


async def propose_prices_from_run(db: AsyncSession, run_id, *,
                                  min_confidence: float = 0.85,
                                  min_pct: float = 25.0) -> dict:
    """Turn an insurer coverage run's HIGH-CONFIDENCE matched prices into price
    proposals — the price-refresh: an insurer's current price (e.g. tamin's
    accepted_total_price) refreshes a stale catalog announced_price, but ONLY
    for confident matches and only when the divergence clears min_pct. Flows
    into the existing proposal review → apply → price_history pipeline; nothing
    is applied without owner approval.

    Reads the run's staged entries (irc → {reference_price, match_confidence,
    match_method}). Returns run_sync's summary + how many entries qualified."""
    from shared.models.coverage import CoverageRun
    run = (await db.execute(select(CoverageRun).where(CoverageRun.id == run_id))).scalar_one()
    staged = run.staged if isinstance(run.staged, dict) else {}
    insurer = run.insurer

    incoming: list[dict] = []
    for irc, per_ins in staged.items():
        entry = (per_ins or {}).get(insurer) if isinstance(per_ins, dict) else None
        if not isinstance(entry, dict):
            continue
        price = entry.get("reference_price")
        conf = entry.get("match_confidence")
        method = entry.get("match_method")
        # exact-code matches are ground truth (conf may be absent); else gate on conf
        ok_conf = method == "irc" or (conf is not None and conf >= min_confidence)
        if price and ok_conf:
            incoming.append({"irc": str(irc), "announced_price": price})

    res = await run_sync(db, incoming, source=f"insurer-refresh:{insurer}", min_pct=min_pct)
    res["qualified"] = len(incoming)
    res["insurer"] = insurer
    return res


async def _apply_to_catalog(db: AsyncSession, pr: DrugPriceProposal) -> None:
    item = (await db.execute(select(DrugCatalogItem).where(
        DrugCatalogItem.irc == pr.irc))).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    # Phase C: every approved price change also appends a dated history point.
    from .price_history import record_price
    if item:
        if pr.proposed_announced is not None:
            item.announced_price = int(pr.proposed_announced)
            item.announced_price_at = now
            await record_price(db, pr.irc, "announced", pr.proposed_announced,
                               source=pr.source or "sync", at=now)
        if pr.proposed_invoice is not None:
            item.last_invoice_price = int(pr.proposed_invoice)
            item.last_invoice_at = now
            await record_price(db, pr.irc, "invoice", pr.proposed_invoice,
                               source=pr.source or "sync", at=now)
    else:
        # 'new' item from a price feed — stub row; enrich via a full catalog ingest.
        db.add(DrugCatalogItem(
            irc=pr.irc, name_fa=pr.name_fa, generic_name=pr.name_fa,
            ingredient_key=ingredient_key(pr.name_fa, "", ""),
            announced_price=_i(pr.proposed_announced), announced_price_at=now if pr.proposed_announced is not None else None,
            last_invoice_price=_i(pr.proposed_invoice), last_invoice_at=now if pr.proposed_invoice is not None else None,
            source=pr.source or "sync"))


async def decide_proposals(db: AsyncSession, ids: list[UUID], *, approve: bool,
                           staff_id: UUID) -> dict:
    rows = (await db.execute(select(DrugPriceProposal).where(
        DrugPriceProposal.id.in_(ids), DrugPriceProposal.status == "pending"))).scalars().all()
    now = datetime.now(timezone.utc)
    for pr in rows:
        if approve:
            await _apply_to_catalog(db, pr)
        pr.status = "approved" if approve else "rejected"
        pr.decided_by = staff_id
        pr.decided_at = now
    await db.commit()
    return {"decided": len(rows), "status": "approved" if approve else "rejected"}


async def list_proposals(db: AsyncSession, *, status: str = "pending", limit: int = 500) -> list[DrugPriceProposal]:
    rows = (await db.execute(select(DrugPriceProposal).where(
        DrugPriceProposal.status == status)
        .order_by(DrugPriceProposal.pct_change.desc()).limit(limit))).scalars().all()
    return list(rows)
