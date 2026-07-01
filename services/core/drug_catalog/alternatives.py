"""Cheaper same-ingredient alternative lookup for the affordability swap lever."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from .schema import CatalogRecord


@dataclass(frozen=True)
class Alternative:
    record: CatalogRecord
    effective_price: Decimal
    savings_vs_current: Decimal   # per-unit saving vs the currently-selected product


def find_alternatives(records: Iterable[CatalogRecord], current_irc: str, *,
                      only_cheaper: bool = True,
                      in_stock_ircs: set[str] | None = None) -> list[Alternative]:
    """Same-ingredient (key) substitutes for `current_irc`, cheapest first.

    only_cheaper   — drop options that cost ≥ the current product (default).
    in_stock_ircs  — when given, restrict to IRCs the pharmacy actually stocks.
    """
    records = list(records)
    current = next((r for r in records if r.irc == current_irc), None)
    if current is None:
        return []
    key = current.ingredient_key
    cur_price = current.effective_price

    out: list[Alternative] = []
    for r in records:
        if r.irc == current_irc or r.ingredient_key != key:
            continue
        if in_stock_ircs is not None and r.irc not in in_stock_ircs:
            continue
        savings = cur_price - r.effective_price
        if only_cheaper and savings <= 0:
            continue
        out.append(Alternative(record=r, effective_price=r.effective_price, savings_vs_current=savings))

    out.sort(key=lambda a: (a.effective_price, a.record.name_fa))
    return out
