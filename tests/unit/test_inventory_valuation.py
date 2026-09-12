"""Valuation — what the stock is worth, and what the losses cost.

unit_cost has always been on the lot and nothing ever added it up, so shrinkage
was reported in units: a hundred lost paracetamol tablets and a hundred lost
insulin pens read identically. These tests pin the two things that make the
currency figure trustworthy — that no lot borrows another's price, and that
recoverable losses are not merged with real ones.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from services.core.inventory import valuation as V


def lot(ndc="N1", qty=100, cost=10, expiry="2027-01-01", number="L1", **kw):
    d = {"ndc11": ndc, "quantity_on_hand": qty, "unit_cost": cost,
         "expiry_date": expiry, "lot_number": number,
         "quantity_damaged": 0, "quantity_returned": 0, "quantity_in_transit": 0}
    d.update(kw)
    return d


def mv(mtype, units=10, cost=10, ndc="N1"):
    return {"movement_type": mtype, "quantity_delta": -units,
            "unit_cost": cost, "ndc11": ndc}


# ── valuing what is on the shelf ──────────────────────────────────────────
def test_a_single_costed_lot_values_at_its_own_price():
    out = V.value_item("N1", [lot(qty=100, cost=12)])
    assert out.value == Decimal("1200.000")
    assert out.unit_cost == Decimal("12.000")


def test_lots_bought_at_different_prices_are_valued_at_their_own():
    """The same drug arrives at different prices repeatedly. Picking one is a
    policy decision, not arithmetic."""
    out = V.value_item("N1", [lot(qty=100, cost=10, number="A"),
                              lot(qty=100, cost=20, number="B")])
    assert out.value == Decimal("3000.000")


def test_a_lot_with_no_cost_contributes_nothing_rather_than_borrowing_one():
    """Borrowing a sibling's price produces a plausible total no purchase
    supports — the fabricated demand rate again, in currency."""
    out = V.value_item("N1", [lot(qty=100, cost=10, number="A"),
                              lot(qty=100, cost=None, number="B")])
    assert out.value == Decimal("1000.000")
    assert out.lots_uncosted == 1
    assert "borrowing" in out.explanation


def test_empty_lots_do_not_contribute():
    out = V.value_item("N1", [lot(qty=0, cost=10)])
    assert out.value == Decimal("0.000")
    assert out.unit_cost is None


def test_the_weighted_method_blends_and_says_so():
    fifo = V.value_item("N1", [lot(qty=100, cost=10, number="A"),
                               lot(qty=300, cost=20, number="B")], method="fifo")
    wavg = V.value_item("N1", [lot(qty=100, cost=10, number="A"),
                               lot(qty=300, cost=20, number="B")], method="weighted")
    assert wavg.unit_cost == Decimal("17.500")
    assert wavg.method == "weighted" and fifo.method == "fifo"
    # Same total; the difference is the per-unit figure they report.
    assert fifo.value == wavg.value


def test_an_unknown_method_is_refused():
    with pytest.raises(ValueError):
        V.value_item("N1", [lot()], method="vibes")


def test_a_total_that_could_not_value_everything_says_so():
    """A valuation reported as a fact when part of the shelf had no price is
    how a floor becomes a figure."""
    out = V.value_stock([lot(ndc="N1", cost=10),
                         lot(ndc="N2", cost=None, qty=50)])
    assert out.uncosted_items == 1
    assert out.uncosted_units == Decimal("50.000")
    assert "a floor, not the figure" in out.as_dict()["coverage_note"]


def test_everything_costed_says_so_plainly():
    out = V.value_stock([lot(ndc="N1", cost=10)])
    assert "Every unit on hand carries a cost" in out.as_dict()["coverage_note"]


def test_holding_buckets_are_valued_apart_from_sellable_stock():
    """Damaged stock awaiting a supplier claim is a real asset. Folding it into
    on-hand would overstate what can be sold and hide what needs chasing."""
    out = V.value_stock([lot(qty=100, cost=10, quantity_damaged=20,
                             quantity_returned=5)])
    assert out.total == Decimal("1000.000")
    assert out.buckets["damaged"] == Decimal("200.000")
    assert out.buckets["returned"] == Decimal("50.000")


def test_items_are_ranked_by_value():
    out = V.value_stock([lot(ndc="SMALL", qty=1, cost=1),
                         lot(ndc="BIG", qty=100, cost=100)])
    assert [i.ndc11 for i in out.items] == ["BIG", "SMALL"]


# ── what the losses cost ──────────────────────────────────────────────────
def test_shrinkage_is_reported_in_currency_not_units():
    """A hundred lost tablets and a hundred lost insulin pens are the same
    number of units and nothing like the same event."""
    cheap = V.shrinkage([mv("WASTE", units=100, cost=1)])
    dear = V.shrinkage([mv("WASTE", units=100, cost=500)])
    assert cheap.total_lost == Decimal("100.000")
    assert dear.total_lost == Decimal("50000.000")


def test_dispensing_is_not_shrinkage():
    """Dispensed stock was sold, not lost. Folding it in would make every busy
    day look like a theft."""
    assert V.shrinkage([mv("DISPENSE", units=500, cost=100)]).total_lost == \
        Decimal("0.000")


def test_recoverable_losses_are_reported_apart_from_real_ones():
    """A supplier claim somebody should be chasing is not money already gone —
    and merging them lets the first quietly become the second."""
    out = V.shrinkage([mv("EXPIRY_REMOVAL", units=10, cost=10),
                       mv("RETURN_TO_SUPPLIER", units=10, cost=10),
                       mv("DAMAGE", units=10, cost=10)])
    assert out.total_lost == Decimal("100.000")
    assert out.recoverable == Decimal("200.000")
    assert "if nobody files the claim" in out.explanation


def test_losses_are_broken_down_by_type_and_by_item():
    out = V.shrinkage([mv("WASTE", units=10, cost=10, ndc="A"),
                       mv("COUNT_LOSS", units=5, cost=100, ndc="B")])
    assert out.by_type["COUNT_LOSS"] == Decimal("500.000")
    assert out.by_item[0]["ndc11"] == "B"


def test_a_movement_with_no_cost_is_skipped_rather_than_counted_at_zero():
    """Counting it at zero would report a loss of nothing and make the total
    look smaller than it is."""
    out = V.shrinkage([{"movement_type": "WASTE", "quantity_delta": -10,
                        "unit_cost": None, "ndc11": "A"}])
    assert out.total_lost == Decimal("0.000")
    assert out.by_type == {}


# ── the stored figure against the lots underneath it ──────────────────────
def test_a_matching_book_value_reports_no_drift():
    v = V.value_stock([lot(qty=100, cost=10)])
    assert V.drift(Decimal("1000"), v) is None


def test_a_book_value_that_no_longer_matches_its_lots_is_reported():
    v = V.value_stock([lot(qty=100, cost=10)])
    d = V.drift(Decimal("1500"), v)
    assert d is not None and d["drift"] == 500.0


def test_no_book_value_is_not_a_drift():
    assert V.drift(None, V.value_stock([lot()])) is None
