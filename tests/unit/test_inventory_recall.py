"""Recall logic — and above all, its honesty about what it cannot see.

The failure this file exists to prevent: a recall that resolves the lots, finds
no linked dispenses, and reports "no patients affected" when in fact 46
dispenses of that product carry no lot at all. A confident zero is far worse
than an admitted unknown.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from services.core.inventory import recall as RC

TODAY = date(2026, 8, 3)


def lot(qty=100, **kw):
    base = {"id": "lot-1", "lot_number": "L-1", "quantity_on_hand": qty,
            "expiry_date": date(2027, 1, 1), "storage_location": "SHELF-A1",
            "is_quarantined": False, "is_recalled": False, "cold_chain_breach": False}
    base.update(kw)
    return base


def dispense(qty=30, **kw):
    base = {"lot_id": "lot-1", "lot_number": "L-1", "quantity": qty,
            "fill_id": "f1", "patient_id": "p1", "dispensed_at": "2026-07-01"}
    base.update(kw)
    return base


# ── classifying what is still here ────────────────────────────────────────
def test_sellable_stock_is_the_urgent_class():
    u = RC.classify_lot(lot(), as_of=TODAY)
    assert u.state == RC.ON_SHELF
    assert "quarantine" in u.action


def test_an_empty_lot_produces_nothing_to_act_on():
    assert RC.classify_lot(lot(qty=0), as_of=TODAY) is None


@pytest.mark.parametrize("flag", ["is_quarantined", "is_recalled", "cold_chain_breach"])
def test_stock_already_off_the_shelf_is_not_urgent(flag):
    u = RC.classify_lot(lot(**{flag: True}), as_of=TODAY)
    assert u.state == RC.BLOCKED


def test_expired_stock_is_still_physically_present_and_must_be_accounted_for():
    """It cannot be dispensed, but it is on the premises and a recall has to
    remove it."""
    u = RC.classify_lot(lot(expiry_date=date(2026, 1, 1)), as_of=TODAY)
    assert u.state == RC.BLOCKED and u.quantity == Decimal("100.000")


# ── the blind spot ────────────────────────────────────────────────────────
def test_a_fully_traced_recall_says_so():
    imp = RC.build_impact([lot()], [dispense()], product_dispenses_total=1)
    assert imp.completeness.complete is True
    assert imp.completeness.traceable_pct == 100.0
    assert imp.completeness.warning(RC.CLASS_II) is None


def test_untraceable_dispenses_are_counted_not_ignored():
    """The exact situation in this pharmacy: 46 fills, none carrying a lot."""
    imp = RC.build_impact([lot()], dispenses=[], product_dispenses_total=46)
    assert imp.completeness.fills_untraceable == 46
    assert imp.completeness.traceable_pct == 0.0
    assert imp.completeness.complete is False


def test_a_short_patient_list_carries_the_warning_that_explains_it():
    imp = RC.build_impact([lot()], [dispense()], product_dispenses_total=46)
    warn = imp.summary()["warning"]
    assert warn and "45 of 46" in warn and "incomplete" in warn


def test_a_class_one_recall_says_to_widen_the_notification():
    imp = RC.build_impact([lot()], [dispense()], severity=RC.CLASS_I,
                          product_dispenses_total=46)
    warn = imp.completeness.warning(RC.CLASS_I)
    assert "every patient who received this product" in warn


def test_a_lesser_class_suggests_rather_than_instructs():
    imp = RC.build_impact([lot()], [dispense()], severity=RC.CLASS_III,
                          product_dispenses_total=46)
    assert "Consider widening" in imp.completeness.warning(RC.CLASS_III)


def test_an_unknown_recall_class_is_rejected():
    with pytest.raises(RC.RecallError):
        RC.build_impact([], [], severity="urgent-ish")


# ── the picture a pharmacist reads ────────────────────────────────────────
def test_the_summary_leads_with_what_is_still_sellable():
    imp = RC.build_impact([lot(qty=40), lot(id="lot-2", lot_number="L-2", qty=10,
                                            is_quarantined=True)],
                          [dispense()], product_dispenses_total=1)
    s = imp.summary()
    assert s["on_shelf"] == 40.0 and s["blocked"] == 10.0
    assert "quarantine before anything else" in s["urgent_action"]


def test_no_urgent_action_when_nothing_is_sellable():
    imp = RC.build_impact([lot(is_quarantined=True)], [], product_dispenses_total=0)
    assert imp.summary()["urgent_action"] is None


def test_patients_are_deduplicated_across_multiple_fills():
    ds = [dispense(fill_id="f1", patient_id="p1"),
          dispense(fill_id="f2", patient_id="p1"),
          dispense(fill_id="f3", patient_id="p2")]
    imp = RC.build_impact([], ds, product_dispenses_total=3)
    assert imp.patients == ["p1", "p2"]


def test_a_dispense_with_no_patient_link_is_still_recorded_as_a_unit_that_left():
    imp = RC.build_impact([], [dispense(patient_id=None)], product_dispenses_total=1)
    assert imp.quantity_in(RC.DISPENSED) == Decimal("30.000")
    assert imp.patients == []          # nobody to name, but the units are counted


def test_dispensed_units_are_summed_not_just_counted():
    imp = RC.build_impact([], [dispense(qty=30, fill_id="a"),
                               dispense(qty=12, fill_id="b")],
                          product_dispenses_total=2)
    assert imp.completeness.units_dispensed == Decimal("42.000")


# ── closure ───────────────────────────────────────────────────────────────
def test_a_recall_cannot_close_while_stock_is_still_sellable():
    imp = RC.build_impact([lot(qty=5)], [], product_dispenses_total=0)
    out = RC.closure_check(imp, patients_notified=0)
    assert out["may_close"] is False
    assert any("sellable" in b for b in out["blockers"])


def test_a_recall_cannot_close_while_patients_are_unnotified():
    imp = RC.build_impact([lot(is_quarantined=True)], [dispense()],
                          product_dispenses_total=1)
    out = RC.closure_check(imp, patients_notified=0)
    assert out["may_close"] is False
    assert any("not been notified" in b for b in out["blockers"])


def test_contained_is_distinguished_from_closed():
    """Nothing sellable remains, but the patients still need telling — that is
    a real and different state from finished."""
    imp = RC.build_impact([lot(is_quarantined=True)], [dispense()],
                          product_dispenses_total=1)
    assert RC.closure_check(imp, patients_notified=0)["next_status"] == RC.CONTAINED


def test_a_clean_recall_closes():
    imp = RC.build_impact([lot(qty=0)], [dispense()], product_dispenses_total=1)
    out = RC.closure_check(imp, patients_notified=1)
    assert out["may_close"] is True and out["next_status"] == RC.CLOSED


def test_a_class_one_recall_may_not_close_over_an_incomplete_trace():
    """The dispenses we cannot trace are precisely the ones most likely to
    matter; closing over them turns a recall into paperwork."""
    imp = RC.build_impact([lot(qty=0)], [dispense()], severity=RC.CLASS_I,
                          product_dispenses_total=46)
    out = RC.closure_check(imp, patients_notified=1)
    assert out["may_close"] is False
    assert any("Class I" in b for b in out["blockers"])


def test_a_lesser_class_may_close_over_an_incomplete_trace():
    imp = RC.build_impact([lot(qty=0)], [dispense()], severity=RC.CLASS_II,
                          product_dispenses_total=46)
    assert RC.closure_check(imp, patients_notified=1)["may_close"] is True


def test_closing_over_blockers_is_possible_but_never_silent():
    imp = RC.build_impact([lot(qty=5)], [], product_dispenses_total=0)
    out = RC.closure_check(imp, patients_notified=0,
                           force_reason="stock destroyed on site, witnessed")
    assert out["may_close"] is True and out["forced"] is True
    assert out["blockers"]                      # the reason does not erase them


def test_forcing_closure_still_demands_a_reason():
    imp = RC.build_impact([lot(qty=5)], [], product_dispenses_total=0)
    with pytest.raises(RC.RecallError):
        RC.closure_check(imp, patients_notified=0, force_reason="   ")
