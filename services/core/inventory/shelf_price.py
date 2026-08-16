"""What the customer is charged, and why that number and not another.

The price chain the owner specified:

    buy price (distributor invoice)  →  + profit %  →  sell price = shelf price

`inventory_lots` records the buy price per invoice as `unit_cost`, and now the
margin and the sell price it produces. This module answers the one question the
pricing engine asks of inventory: **what does this product sell for today?**

Two rules, both the owner's, and both load-bearing:

1. **The highest candidate wins.** The candidates are every batch registered
   through purchasing — each lot's own `sell_price`, counted only while it still
   holds sellable units — AND whatever the owner has entered by hand. The
   maximum of all of them is the shelf price. In a market where replacement cost
   only rises, selling older stock at its older price funds the next purchase at
   a loss, so a dearer batch must be able to lift the shelf even above a figure
   the owner typed earlier; and the owner's figure must be able to lift it above
   the batches. Neither source overwrites the other — they compete.

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


def shelf_price_of(lots, manual_price=None) -> Decimal | None:
    """The product's shelf price: the highest of EVERY candidate price.

    Two sources, and neither overrules the other:

      * each lot's `sell_price` — what that batch, bought at its own cost and
        margin, needs to sell for. Only lots still holding sellable stock count.
      * `manual_price` — what the owner entered by hand.

    The maximum decides. An owner's figure does not silence the batches: a lot
    bought dearer than the owner last typed must still set the shelf, or that
    lot sells below its own replacement cost. And a batch does not silence the
    owner either — whichever is higher is the price.

    `lots` must be the lots of ONE product. Passing a mixed set would price a
    generic at an imported brand's price — see the module docstring.
    """
    prices = [Decimal(str(l.sell_price)) for l in lots
              if getattr(l, "sell_price", None) is not None
              and _sellable(l) > 0]
    if manual_price is not None and Decimal(str(manual_price)) > 0:
        prices.append(Decimal(str(manual_price)))
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
    from shared.models.inventory import DrugProduct
    lots = (await db.execute(select(InventoryLot).where(
        InventoryLot.drug_product_id == drug_product_id))).scalars().all()
    product = (await db.execute(select(DrugProduct).where(
        DrugProduct.id == drug_product_id))).scalar_one_or_none()
    return shelf_price_of(lots, getattr(product, "manual_shelf_price", None))


async def set_shelf_price(db, drug_product_id, new_price, *, reason: str | None = None,
                          staff_id=None) -> dict:
    """The owner enters a price. It joins the batch prices as a candidate.

    It does NOT overwrite them. An earlier version wrote the owner's number onto
    every sellable lot, which destroyed each batch's own derived figure and, with
    it, the answer to what that batch needed to sell for. It also inverted the
    rule: the shelf price is the maximum of all candidates, so a batch bought
    dearer than the owner last typed must still be able to set it.

    The effective shelf price is therefore returned alongside the entered one —
    they differ whenever a batch is dearer, and the caller should show both so
    nobody wonders why the till charges more than they typed.

    Every entry is appended to `price_history` as a `shelf` point through
    `record_price`, the same SCD type-2 path every other price takes.
    """
    from decimal import InvalidOperation
    from datetime import datetime, timezone
    from sqlalchemy import select
    from shared.models.inventory import DrugProduct, InventoryLot
    from services.core.drug_catalog.price_history import record_price

    try:
        price = Decimal(str(new_price))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("قیمت نامعتبر است.")
    if price <= 0:
        raise ValueError("قیمت باید بزرگ‌تر از صفر باشد.")

    product = (await db.execute(select(DrugProduct).where(
        DrugProduct.id == drug_product_id))).scalar_one_or_none()
    if product is None:
        raise ValueError("فرآورده یافت نشد.")
    lots = (await db.execute(select(InventoryLot).where(
        InventoryLot.drug_product_id == drug_product_id))).scalars().all()

    previous = shelf_price_of(lots, product.manual_shelf_price)
    product.manual_shelf_price = price
    product.manual_price_set_at = datetime.now(timezone.utc)
    product.manual_price_set_by = staff_id
    effective = shelf_price_of(lots, price)

    recorded = []
    for irc in sorted({l.irc for l in lots if l.irc}):
        recorded.append(await record_price(
            db, irc, "shelf", effective or price,
            source=f"owner:{staff_id}" if staff_id else "owner"))
    await db.commit()
    return {
        "entered_price": int(price),
        "effective_shelf_price": int(effective) if effective is not None else int(price),
        "overridden_by_batch": bool(effective is not None and effective > price),
        "previous_shelf_price": int(previous) if previous is not None else None,
        "history_points": [r for r in recorded if r in ("inserted", "changed")],
        "reason": reason,
    }
