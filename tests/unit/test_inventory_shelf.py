"""The shelf, which until now only ever filled up.

`depot_transfer` created placements and added to `current_units`; nothing
anywhere subtracted. A dispense reduced the lot and left the shelf believing it
still held everything ever brought to it — so the morning round saw a permanently
full shelf, nobody could say what the floor was worth, and theft detection had no
"expected" to compare a count against.

The distinction these pin is between a number somebody *scanned* and a number we
*worked out*. Both move the shelf. Only one is evidence, and a detector that
cannot tell them apart accuses an honest pharmacy over its own arithmetic.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from services.core.inventory import shelf as SH

NOW = datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc)


def placed(pid="p1", shelf="s1", lot="L1", units=40, days_ago=1, **kw):
    return {"id": pid, "shelf_id": shelf, "inventory_lot_id": lot,
            "ndc11": "N1", "units": Decimal(str(units)),
            "placed_at": NOW - timedelta(days=days_ago), **kw}


# ── taking units off the shelf ────────────────────────────────────────────
def test_a_dispense_comes_off_the_shelf():
    a = SH.allocate([placed(units=40)], 10, lot_id="L1", ndc11="N1")
    assert a.from_shelf == Decimal("10.000")
    assert a.takes[0].after == Decimal("30.000")


def test_the_front_of_the_shelf_goes_first():
    """A pharmacy works the front, and it matches the FEFO order the units were
    placed in."""
    a = SH.allocate([placed("new", units=40, days_ago=1),
                     placed("old", units=40, days_ago=9)], 10,
                    lot_id="L1", ndc11="N1")
    assert a.takes[0].placement_id == "old"


def test_a_take_spanning_two_placements_empties_the_first():
    a = SH.allocate([placed("a", units=10, days_ago=9),
                     placed("b", units=40, days_ago=1)], 25,
                    lot_id="L1", ndc11="N1")
    assert [(t.placement_id, float(t.units)) for t in a.takes] == \
        [("a", 10.0), ("b", 15.0)]


def test_units_the_shelf_cannot_cover_came_from_behind_it():
    """A lot of 100 with only 40 on the shelf is the ordinary case. Dispensing 60
    takes 40 off the shelf and 20 from back-stock, and neither is an error."""
    a = SH.allocate([placed(units=40)], 60, lot_id="L1", ndc11="N1")
    assert a.from_shelf == Decimal("40.000")
    assert a.from_backstock == Decimal("20.000")
    assert "depot back-stock" in a.explanation


def test_a_lot_that_is_on_no_shelf_takes_nothing_off_one():
    a = SH.allocate([placed(lot="OTHER")], 10, lot_id="L1", ndc11="N1")
    assert a.takes == [] and a.from_backstock == Decimal("10.000")


def test_a_placement_never_goes_negative():
    a = SH.allocate([placed(units=5)], 500, lot_id="L1", ndc11="N1")
    assert all(t.after >= 0 for t in a.takes)


def test_nothing_is_taken_for_a_nonpositive_quantity():
    assert SH.allocate([placed()], 0, lot_id="L1", ndc11="N1").takes == []


def test_a_dispense_is_inferred_and_says_so_when_it_had_to_choose():
    """Which shelf the hand reached for is genuinely unknowable without a scan."""
    a = SH.allocate([placed("a", shelf="s1", days_ago=9),
                     placed("b", shelf="s2", days_ago=1)], 10,
                    lot_id="L1", ndc11="N1")
    assert all(t.basis == SH.INFERRED for t in a.takes)
    assert "inferred from the lot's placements rather than observed" in a.explanation


def test_a_scanned_movement_is_recorded_as_observed():
    a = SH.allocate([placed()], 10, lot_id="L1", ndc11="N1", basis=SH.OBSERVED)
    assert a.takes[0].basis == SH.OBSERVED


# ── what the floor is holding, and what it is worth ───────────────────────
def test_the_floor_is_valued_at_what_it_would_ring_up_for():
    p = SH.position([placed("a", units=10, sell_price=Decimal("100"),
                            label="A-01", capacity_units=50),
                     placed("b", shelf="s2", units=5, sell_price=Decimal("40"),
                            label="B-01", capacity_units=50)], at=NOW)
    assert p.units == Decimal("15.000")
    assert p.value == Decimal("1200.000")
    assert len(p.shelves) == 2


def test_an_unpriced_line_counts_units_and_no_money_and_says_so():
    """A total that silently omits them reads as smaller than the floor is."""
    p = SH.position([placed("a", units=10, sell_price=Decimal("100"), label="A"),
                     placed("b", units=7, sell_price=None, label="A")], at=NOW)
    assert p.units == Decimal("17.000")
    assert p.value == Decimal("1000.000")
    assert p.unpriced_lines == 1
    assert "a floor rather than a total" in p.explanation


def test_an_empty_placement_is_not_on_the_floor():
    assert SH.position([placed(units=0)], at=NOW).units == Decimal("0.000")


def test_utilisation_is_reported_where_the_shelf_has_a_capacity():
    p = SH.position([placed(units=25, label="A-01", capacity_units=50)], at=NOW)
    assert p.shelves[0].utilisation == Decimal("0.500")


def test_a_shelf_with_no_capacity_recorded_reports_no_utilisation():
    p = SH.position([placed(units=25, label="A-01", capacity_units=None)], at=NOW)
    assert p.shelves[0].utilisation is None


# ── expected against counted, which is the only honest theft signal ───────
def rec(**kw):
    base = dict(shelf_id="s1", ndc11="N1", expected=Decimal("40"),
                counted=Decimal("40"), inferred_units=Decimal("0"),
                sell_price=Decimal("100"))
    base.update(kw)
    return SH.reconcile(**base)


def test_a_shelf_that_agrees_says_so():
    assert rec().verdict == "agrees"


def test_stock_missing_beyond_what_the_inference_explains_is_shrinkage():
    v = rec(counted=Decimal("25"), inferred_units=Decimal("2"))
    assert v.verdict == "shrinkage"
    assert v.variance == Decimal("-15.000")
    assert v.value_at_risk == Decimal("1500.000")
    assert SH.worth_investigating(v) is True


def test_more_on_the_shelf_than_the_books_is_not_a_loss():
    """Stock does not appear by itself. It is a placement nobody recorded, and
    treating it as a finding against a person would be absurd."""
    v = rec(counted=Decimal("48"))
    assert v.verdict == "surplus"
    assert "not a loss" in v.explanation
    assert SH.worth_investigating(v) is False


def test_a_gap_no_bigger_than_our_own_guesswork_is_not_an_accusation():
    """Every dispense against a lot on two shelves guessed which one the hand
    reached for. The missing units may be standing on the next shelf along."""
    v = rec(counted=Decimal("36"), inferred_units=Decimal("12"))
    assert v.verdict == "inconclusive"
    assert "next shelf along" in v.explanation
    assert SH.worth_investigating(v) is False


def test_the_same_gap_with_nothing_inferred_is_a_finding():
    """Every movement here was scanned, so the arithmetic cannot explain it."""
    v = rec(counted=Decimal("36"), inferred_units=Decimal("0"))
    assert v.verdict == "shrinkage"


def test_one_missing_tablet_is_a_miscount_not_a_case():
    v = rec(counted=Decimal("39"))
    assert v.verdict == "inconclusive"
    assert "gets switched off" in v.explanation


def test_an_uncounted_shelf_claims_nothing():
    assert SH.reconcile(shelf_id="s1", ndc11="N1", expected=40,
                        counted=None).verdict == "uncounted"


def test_unscanned_movement_is_declared_even_when_the_count_agrees():
    v = rec(inferred_units=Decimal("9"))
    assert v.verdict == "agrees"
    assert any("without anybody scanning it" in c for c in v.concerns)


def test_a_loss_with_no_shelf_price_is_counted_in_units():
    v = rec(counted=Decimal("25"), sell_price=None)
    assert v.verdict == "shrinkage"
    assert v.value_at_risk is None
    assert any("in units and not in money" in c for c in v.concerns)
