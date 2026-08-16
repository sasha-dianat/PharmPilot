"""The shelf price, and the two rules that decide it.

Owner's specification (2026-08-09): inventory holds the buy price, the profit
percentage and the sell price; the sell price is what the customer pays, and the
patient's remainder in a quote is computed from it rather than from NFI's
announced price.

Where several lots are in stock the HIGHEST sellable price wins — in a market
where replacement cost only rises, selling old stock at its old price funds the
next purchase at a loss. And the maximum is taken within one product only:
brands of a molecule are separate products at separate prices.
"""
from __future__ import annotations

from decimal import Decimal

from services.core.inventory.shelf_price import (
    lot_sell_price, shelf_price_of,
)


class Lot:
    """Enough of an InventoryLot to price."""
    def __init__(self, sell_price=None, on_hand=100, reserved=0,
                 in_transit=0, damaged=0, returned=0):
        self.sell_price = sell_price
        self.quantity_on_hand = on_hand
        self.quantity_reserved = reserved
        self.quantity_in_transit = in_transit
        self.quantity_damaged = damaged
        self.quantity_returned = returned


# ── buy price + margin → sell price ─────────────────────────────────────────
def test_the_margin_produces_the_shelf_price():
    assert lot_sell_price(2770, 20) == Decimal("3324")
    assert lot_sell_price(1000, 0) == Decimal("1000")


def test_a_missing_input_is_not_a_price():
    """A cost with no margin, or a margin with no cost, must not be guessed into
    a number the customer is then charged."""
    assert lot_sell_price(2770, None) is None
    assert lot_sell_price(None, 20) is None
    assert lot_sell_price(0, 20) is None


# ── which lot sets the shelf price ──────────────────────────────────────────
def test_the_highest_sellable_lot_sets_the_price():
    """Replacement cost only rises; selling the older lot at its older price
    funds the next purchase at a loss."""
    lots = [Lot(sell_price=3000), Lot(sell_price=3324), Lot(sell_price=2800)]
    assert shelf_price_of(lots) == Decimal("3324")


# ── the owner's price competes with the batches, it does not silence them ───
def test_a_dearer_batch_outranks_the_price_the_owner_typed():
    """Owner's rule: the shelf price is max(every batch, the manual price). A lot
    bought dearer than the owner last entered must still set the shelf, or that
    lot sells below its own replacement cost."""
    lots = [Lot(sell_price=3000), Lot(sell_price=3324)]
    assert shelf_price_of(lots, manual_price=3100) == Decimal("3324")


def test_the_owner_outranks_the_batches_when_higher():
    lots = [Lot(sell_price=3000), Lot(sell_price=3324)]
    assert shelf_price_of(lots, manual_price=5000) == Decimal("5000")


def test_either_source_alone_is_enough():
    assert shelf_price_of([], manual_price=5000) == Decimal("5000")
    assert shelf_price_of([Lot(sell_price=3324)], manual_price=None) == Decimal("3324")
    assert shelf_price_of([], manual_price=None) is None


def test_a_zero_or_negative_manual_price_is_not_a_candidate():
    """Nothing may price a product at zero by accident."""
    assert shelf_price_of([Lot(sell_price=3324)], manual_price=0) == Decimal("3324")
    assert shelf_price_of([], manual_price=0) is None


def test_a_lot_with_no_sellable_units_does_not_set_the_price():
    """The expensive lot is entirely reserved or damaged — it cannot be handed
    over, so it cannot be what the customer is charged."""
    lots = [Lot(sell_price=3000, on_hand=50),
            Lot(sell_price=9000, on_hand=10, reserved=10),
            Lot(sell_price=8000, on_hand=10, damaged=10)]
    assert shelf_price_of(lots) == Decimal("3000")


def test_a_lot_with_no_price_is_skipped_not_read_as_zero():
    """Silence is not a price. Reading a missing sell_price as 0 would hand the
    customer a free item."""
    assert shelf_price_of([Lot(sell_price=None), Lot(sell_price=3324)]) == Decimal("3324")
    assert shelf_price_of([Lot(sell_price=None)]) is None
    assert shelf_price_of([]) is None


def test_holding_buckets_are_excluded_from_sellable():
    """in-transit, damaged and returned units are physically present but not
    sellable — the 0035 buckets exist precisely so they stop counting."""
    assert shelf_price_of([Lot(sell_price=5000, on_hand=10, in_transit=10)]) is None
    assert shelf_price_of([Lot(sell_price=5000, on_hand=10, returned=10)]) is None


def test_the_maximum_is_taken_within_one_product_only():
    """Owner's rule, stated twice: do not mix different brands. The function is
    given one product's lots and cannot see another's — so an imported brand's
    price can never become the generic's shelf price.

    Guarded here by construction: `shelf_price_for_product` filters on
    drug_product_id, and `shelf_price_of` takes whatever list it is handed. This
    test documents the contract that the caller must honour it.
    """
    iranian = [Lot(sell_price=3324), Lot(sell_price=3000)]
    swiss = [Lot(sell_price=29200)]
    assert shelf_price_of(iranian) == Decimal("3324")     # not 29,200
    assert shelf_price_of(swiss) == Decimal("29200")
    # and the mixed call, which the caller must never make, would be wrong:
    assert shelf_price_of(iranian + swiss) == Decimal("29200")


# ── the owner reprices, and the old price survives ─────────────────────────
def test_only_the_owner_may_reprice():
    """INVENTORY_STAFF receive goods and record what they cost. What the
    customer is charged is a commercial decision — the same separation that
    stops a requester approving their own write-off."""
    from shared.models.auth import ROLE_PERMISSIONS, StaffRole
    assert "inventory:price" in ROLE_PERMISSIONS[StaffRole.PHARMACY_MANAGER]
    assert ROLE_PERMISSIONS[StaffRole.SUPER_ADMIN] == ["*"]
    for role in (StaffRole.INVENTORY_STAFF, StaffRole.PHARMACY_TECHNICIAN,
                 StaffRole.PHARMACIST, StaffRole.CASHIER, StaffRole.PHARMACY_INTERN):
        assert "inventory:price" not in ROLE_PERMISSIONS[role], role


def test_shelf_is_a_price_type_in_its_own_right():
    """A repricing must be queryable apart from an authority announcement or a
    distributor invoice, so it cannot be mistaken for either."""
    from services.core.drug_catalog.price_history import PRICE_TYPES
    assert "shelf" in PRICE_TYPES
    assert {"announced", "invoice", "insurer_reference"} <= set(PRICE_TYPES)


# ── the bridge from a prescription line to the shelf ────────────────────────
def test_an_irc_with_no_priced_stock_is_absent_not_zero():
    """A quote line whose product has no priced sellable stock must not be
    handed a zero. The caller falls back and SAYS it fell back; a silent zero
    would dispense the item free."""
    from services.core.inventory.shelf_price import shelf_price_of
    assert shelf_price_of([Lot(sell_price=None, on_hand=100)], None) is None


def test_the_quote_marks_where_its_price_came_from():
    """`price_source` is the honest half of the fallback. NFI's announced price
    is not authoritative for the patient's remainder — if it is being used, the
    counter has to be able to see that and price the item properly."""
    import inspect
    from services.platform.routers import pricing
    src = inspect.getsource(pricing.quote)
    assert '"shelf"' in src and '"nfi_fallback"' in src
    assert "shelf_prices_for_ircs" in src
