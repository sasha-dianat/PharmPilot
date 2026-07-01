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


async def run_sync(db: AsyncSession, incoming: list[dict], *, source: str = "sync") -> dict:
    """Diff the feed against the catalog → replace pending proposals for the
    affected IRCs. Never mutates catalog prices directly."""
    ircs = [str(r.get("irc")).strip() for r in incoming if r.get("irc")]
    current = await repo.fetch_by_irc(db, ircs)
    proposals = compute_proposals(list(current.values()), incoming)

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


async def _apply_to_catalog(db: AsyncSession, pr: DrugPriceProposal) -> None:
    item = (await db.execute(select(DrugCatalogItem).where(
        DrugCatalogItem.irc == pr.irc))).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if item:
        if pr.proposed_announced is not None:
            item.announced_price = int(pr.proposed_announced)
            item.announced_price_at = now
        if pr.proposed_invoice is not None:
            item.last_invoice_price = int(pr.proposed_invoice)
            item.last_invoice_at = now
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
