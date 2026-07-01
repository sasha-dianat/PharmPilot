"""Daily price-sync → manager-approved price proposals.

`compute_proposals` diffs an incoming price feed (updated announced prices and/or
distributor invoices) against the current catalog and emits PriceProposals for
items whose effective sale price would change. Effective price = max(announced,
invoice). Proposals are reviewed/approved by a manager before catalog prices move
— never auto-applied (mirrors قانون فارما / باستانی طب 'proposed new price').
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .schema import CatalogRecord
from services.core.pricing_ir.engine import resolve_consumer_price


@dataclass(frozen=True)
class PriceProposal:
    irc: str
    name: str
    kind: str                       # "new" | "increase" | "decrease"
    current_announced: Decimal | None
    proposed_announced: Decimal | None
    current_invoice: Decimal | None
    proposed_invoice: Decimal | None
    current_effective: Decimal
    proposed_effective: Decimal
    delta: Decimal
    pct_change: float


def _dec(v) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def compute_proposals(current: list[CatalogRecord], incoming: list[dict]) -> list[PriceProposal]:
    by_irc = {r.irc: r for r in current}
    out: list[PriceProposal] = []
    for row in incoming:
        irc = str(row.get("irc") or "").strip()
        if not irc:
            continue
        new_announced = _dec(row.get("announced_price"))
        new_invoice = _dec(row.get("last_invoice_price"))
        cur = by_irc.get(irc)

        if cur is None:
            proposed = resolve_consumer_price(new_announced, new_invoice)
            if proposed <= 0:
                continue
            out.append(PriceProposal(
                irc=irc, name=str(row.get("name_fa") or irc), kind="new",
                current_announced=None, proposed_announced=new_announced,
                current_invoice=None, proposed_invoice=new_invoice,
                current_effective=Decimal("0"), proposed_effective=proposed,
                delta=proposed, pct_change=100.0))
            continue

        # carry forward whichever side the feed didn't update
        eff_announced = new_announced if new_announced is not None else cur.announced_price
        eff_invoice = new_invoice if new_invoice is not None else cur.last_invoice_price
        cur_eff = cur.effective_price
        proposed = resolve_consumer_price(eff_announced, eff_invoice)
        if proposed == cur_eff:
            continue
        delta = proposed - cur_eff
        pct = float((delta / cur_eff * 100)) if cur_eff > 0 else 100.0
        out.append(PriceProposal(
            irc=irc, name=cur.name_fa, kind="increase" if delta > 0 else "decrease",
            current_announced=cur.announced_price, proposed_announced=eff_announced,
            current_invoice=cur.last_invoice_price, proposed_invoice=eff_invoice,
            current_effective=cur_eff, proposed_effective=proposed,
            delta=delta, pct_change=round(pct, 2)))
    return out
