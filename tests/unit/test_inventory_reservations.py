"""Reservations — the half of the feature that was missing.

Everything downstream (`Lot.available`, `pick_fefo`, `check_over_reservation`,
the dispense decrement) was written assuming reservations existed. Nothing ever
made one. These tests pin the write path, and in particular the two behaviours
that distinguish a reservation from a counter: it is exact on release, and it
expires.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from services.core.inventory import reservations as RSV
from services.core.inventory.ledger import Lot

TODAY = date(2026, 8, 9)
NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)


def lot(lot_id, number, days_to_expiry, on_hand, reserved=0, **kw) -> Lot:
    return Lot(lot_id=lot_id, lot_number=number,
               expiry_date=date.fromordinal(TODAY.toordinal() + days_to_expiry),
               quantity_on_hand=Decimal(str(on_hand)),
               quantity_reserved=Decimal(str(reserved)), **kw)


def rsv(rid, lot_id, qty, status="active", expires=None) -> dict:
    return {"id": rid, "inventory_lot_id": lot_id, "quantity": Decimal(str(qty)),
            "status": status, "expires_at": expires}


# ── making a reservation ──────────────────────────────────────────────────
def test_a_reservation_takes_the_soonest_expiring_lot_first():
    lots = [lot("L2", "B", 200, 100), lot("L1", "A", 30, 100)]
    p = RSV.plan_reserve(lots, 60, prescription_id="rx1", ndc11="N",
                         as_of=TODAY, now=NOW)
    assert [a.lot_id for a in p.allocations] == ["L1"]
    assert p.total == Decimal("60.000")


def test_a_reservation_spans_lots_when_one_cannot_cover_it():
    lots = [lot("L1", "A", 30, 40), lot("L2", "B", 200, 100)]
    p = RSV.plan_reserve(lots, 90, prescription_id="rx1", ndc11="N",
                         as_of=TODAY, now=NOW)
    assert [(a.lot_id, float(a.quantity)) for a in p.allocations] == \
        [("L1", 40.0), ("L2", 50.0)]
    assert p.total == Decimal("90.000")


def test_reserving_more_than_is_available_is_refused_not_partially_filled():
    """A partial reservation would show the prescription ready to fill while the
    shelf could not supply it — discovered with the patient at the counter."""
    lots = [lot("L1", "A", 30, 40)]
    with pytest.raises(RSV.ReservationError) as e:
        RSV.plan_reserve(lots, 90, prescription_id="rx1", ndc11="N", as_of=TODAY)
    assert "insufficient stock" in str(e.value)


def test_units_already_reserved_are_not_available_to_reserve_again():
    """The double-promise this module exists to prevent."""
    lots = [lot("L1", "A", 30, 100, reserved=80)]
    with pytest.raises(RSV.ReservationError):
        RSV.plan_reserve(lots, 30, prescription_id="rx2", ndc11="N", as_of=TODAY)


def test_an_expired_lot_is_never_reserved():
    lots = [lot("L1", "A", -1, 100), lot("L2", "B", 100, 100)]
    p = RSV.plan_reserve(lots, 50, prescription_id="rx1", ndc11="N",
                         as_of=TODAY, now=NOW)
    assert [a.lot_id for a in p.allocations] == ["L2"]


def test_a_quarantined_lot_is_never_reserved():
    lots = [lot("L1", "A", 30, 100, is_quarantined=True)]
    with pytest.raises(RSV.ReservationError):
        RSV.plan_reserve(lots, 10, prescription_id="rx1", ndc11="N", as_of=TODAY)


def test_a_recalled_lot_is_never_reserved():
    lots = [lot("L1", "A", 30, 100, is_recalled=True)]
    with pytest.raises(RSV.ReservationError):
        RSV.plan_reserve(lots, 10, prescription_id="rx1", ndc11="N", as_of=TODAY)


def test_a_nonpositive_reservation_is_refused():
    with pytest.raises(RSV.ReservationError):
        RSV.plan_reserve([lot("L1", "A", 30, 10)], 0,
                         prescription_id="rx", ndc11="N", as_of=TODAY)


def test_a_reservation_carries_an_expiry():
    p = RSV.plan_reserve([lot("L1", "A", 30, 100)], 10, prescription_id="rx",
                         ndc11="N", as_of=TODAY, now=NOW, ttl_days=14)
    assert p.expires_at == NOW + timedelta(days=14)


# ── releasing ─────────────────────────────────────────────────────────────
def test_releasing_returns_only_active_rows():
    """Releasing a consumed reservation would credit back units already with a
    patient, inflating available by stock that no longer exists."""
    rows = [rsv("r1", "L1", 10), rsv("r2", "L1", 5, status="consumed")]
    out = RSV.plan_release(rows, reason="cancelled")
    assert [r["reservation_id"] for r in out] == ["r1"]


def test_a_cancellation_releases_and_names_why():
    rows = [rsv("r1", "L1", 10)]
    out = RSV.release_for_transition(rows, "CANCELLED")
    assert out[0]["reason"] == "prescription cancelled"


def test_a_transition_that_does_not_free_stock_releases_nothing():
    rows = [rsv("r1", "L1", 10)]
    assert RSV.release_for_transition(rows, "FILLING") == []


def test_dispensing_is_not_a_release():
    """DISPENSED consumes the reservation through the dispense hook; treating it
    as a release here would return the units to available as they leave."""
    assert "DISPENSED" not in RSV.RELEASE_ON


def test_release_refuses_to_go_below_zero_instead_of_clamping():
    """GREATEST(0, reserved - taken) turns a corrupted counter into a plausible
    one, which is exactly the state no check can then detect."""
    with pytest.raises(RSV.ReservationError) as e:
        RSV.apply_release(Decimal("5"), 9)
    assert "disagree" in str(e.value)


def test_an_exact_release_lands_on_zero():
    assert RSV.apply_release(Decimal("9"), 9) == Decimal("0.000")


def test_an_unknown_terminal_status_is_refused():
    with pytest.raises(RSV.ReservationError):
        RSV.plan_release([rsv("r1", "L1", 1)], reason="x", status="deleted")


# ── expiry ────────────────────────────────────────────────────────────────
def test_a_hold_that_has_run_out_is_expired():
    rows = [rsv("r1", "L1", 10, expires=NOW - timedelta(seconds=1))]
    assert [r["id"] for r in RSV.expired_rows(rows, now=NOW)] == ["r1"]


def test_a_hold_still_running_is_not_expired():
    rows = [rsv("r1", "L1", 10, expires=NOW + timedelta(days=1))]
    assert RSV.expired_rows(rows, now=NOW) == []


def test_a_naive_timestamp_is_read_as_utc_rather_than_crashing():
    rows = [rsv("r1", "L1", 10, expires=datetime(2026, 8, 1, 12))]
    assert len(RSV.expired_rows(rows, now=NOW)) == 1


def test_a_consumed_reservation_never_expires():
    rows = [rsv("r1", "L1", 10, status="consumed",
                expires=NOW - timedelta(days=99))]
    assert RSV.expired_rows(rows, now=NOW) == []


# ── the counter is a denormalisation, so it can drift ─────────────────────
def test_reserved_per_lot_sums_only_active_rows():
    rows = [rsv("r1", "L1", 10), rsv("r2", "L1", 5),
            rsv("r3", "L1", 100, status="released"),
            rsv("r4", "L2", 7)]
    assert RSV.reserved_by_lot(rows) == {"L1": Decimal("15.000"),
                                         "L2": Decimal("7.000")}


def test_a_counter_matching_its_reservations_shows_no_drift():
    rows = [rsv("r1", "L1", 10)]
    assert RSV.drift({"L1": Decimal("10")}, rows) == []


def test_a_counter_holding_units_no_reservation_claims_is_drift():
    """These units are invisible to the allocator and to every report."""
    d = RSV.drift({"L1": Decimal("10")}, [])
    assert d[0]["drift"] == 10.0
    assert d[0]["active_reservations"] == 0.0


def test_a_reservation_the_counter_does_not_know_about_is_drift():
    d = RSV.drift({}, [rsv("r1", "L1", 4)])
    assert d[0]["drift"] == -4.0
