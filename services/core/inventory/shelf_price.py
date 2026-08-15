"""What the customer is charged, and why that number and not another.

The price chain the owner specified:

    buy price (distributor invoice)  →  + profit %  →  sell price = shelf price

`inventory_lots` records the buy price per invoice as `unit_cost`, and now the
margin and the sell price it produces. This module answers the one question the
pricing engine asks of inventory: **what does this product sell for today?**

Two rules, both the owner's, and both load-bearing:

1. **The highest sellable lot wins.** Where several lots of one product are in
   stock at different prices, the shelf price is the largest `sell_price` among
   those still holding sellable units. In a market where replacement cost only
   rises, selling older stock at its older price funds the next purchase at a
   loss — the pharmacy buys back higher than it sold.

2. **Never mix brands.** The maximum is taken WITHIN ONE `drug_product_id`.
   Brands of the same molecule are separate products at separate prices, exactly
   as different salts and presentations are: «زادیتن» from Switzerland and an
   Iranian ketotifen are not interchangeable rows, and taking a maximum across
   them would price the generic at the imported brand's price. The function
   cannot mix them because it never sees more than one product's lots.

A lot with no `sell_price` does not take part in the maximum. It does not price
at zero — silence is not a price, and reading it as one would hand the customer
a free item.
"""
from __future__ import annotations

from decimal import Decimal


def lot_sell_price(unit_cost, margin_pct) -> Decimal | None:
    """buy price + margin → the per-unit shelf price for one lot.

    Returns None when either input is missing: a margin with no cost, or a cost
    with no margin, is not a price and must not be guessed into one.
    """
    if unit_cost is None or margin_pct is None:
        return None
    cost, pct = Decimal(str(unit_cost)), Decimal(str(margin_pct))
    if cost <= 0:
        return None
    return (cost * (Decimal("1") + pct / Decimal("100"))).quantize(Decimal("1"))


def shelf_price_of(lots) -> Decimal | None:
    """The product's current shelf price: the highest sell_price among lots that
    still hold sellable stock.

    `lots` must be the lots of ONE product. Passing a mixed set would price a
    generic at an imported brand's price — see the module docstring.
    """
    prices = [Decimal(str(l.sell_price)) for l in lots
              if getattr(l, "sell_price", None) is not None
              and _sellable(l) > 0]
    return max(prices) if prices else None


def _sellable(lot) -> Decimal:
    """Units that can actually be handed over — on hand, less what is reserved
    for fills not yet dispensed and anything sitting in a holding bucket."""
    def q(name):
        v = getattr(lot, name, None)
        return Decimal(str(v)) if v is not None else Decimal("0")
    return (q("quantity_on_hand") - q("quantity_reserved")
            - q("quantity_in_transit") - q("quantity_damaged") - q("quantity_returned"))


async def shelf_price_for_product(db, drug_product_id) -> Decimal | None:
    """Shelf price for one product, read from its own lots only."""
    from sqlalchemy import select
    from shared.models.inventory import InventoryLot
    lots = (await db.execute(select(InventoryLot).where(
        InventoryLot.drug_product_id == drug_product_id))).scalars().all()
    return shelf_price_of(lots)


async def set_shelf_price(db, drug_product_id, new_price, *, reason: str | None = None,
                          staff_id=None, margin_pct=None) -> dict:
    """The owner reprices a product. Every sellable lot takes the new price.

    All sellable lots, not just the dearest, because the shelf price is the
    MAXIMUM across them: leaving an older lot at a higher figure would silently
    overrule the number the owner just typed.

    The old price is not overwritten quietly — each change is appended to
    `price_history` as a `shelf` point, keyed by the lot's IRC, through
    `record_price`, which is SCD type-2 and closes the previous open row. That
    is deliberately the same path every other price in this system takes; a
    manual repricing that bypassed it would be the one price movement with no
    history, which is precisely the one anybody would later want to explain.
    """
    from decimal import InvalidOperation
    from sqlalchemy import select
    from shared.models.inventory import InventoryLot
    from services.core.drug_catalog.price_history import record_price

    try:
        price = Decimal(str(new_price))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("قیمت نامعتبر است.")
    if price <= 0:
        raise ValueError("قیمت باید بزرگ‌تر از صفر باشد.")

    lots = (await db.execute(select(InventoryLot).where(
        InventoryLot.drug_product_id == drug_product_id))).scalars().all()
    sellable = [l for l in lots if _sellable(l) > 0]
    if not sellable:
        raise ValueError("این فرآورده موجودی قابل فروش ندارد.")

    previous = shelf_price_of(sellable)
    ircs, recorded = set(), []
    for lot in sellable:
        lot.sell_price = price
        if margin_pct is not None:
            lot.margin_pct = Decimal(str(margin_pct))
        elif lot.unit_cost and Decimal(str(lot.unit_cost)) > 0:
            # Keep the margin honest against what THIS lot actually cost, so the
            # figure stays auditable back to its own invoice.
            cost = Decimal(str(lot.unit_cost))
            lot.margin_pct = ((price - cost) / cost * Decimal("100")).quantize(Decimal("0.001"))
        if lot.irc:
            ircs.add(lot.irc)
    for irc in sorted(ircs):
        recorded.append(await record_price(
            db, irc, "shelf", price,
            source=f"owner:{staff_id}" if staff_id else "owner"))
    await db.commit()
    return {"lots_updated": len(sellable),
            "previous_shelf_price": int(previous) if previous is not None else None,
            "new_shelf_price": int(price),
            "history_points": [r for r in recorded if r in ("inserted", "changed")],
            "reason": reason}
