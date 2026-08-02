"""The conserving ledger: FEFO, conservation, no clamping, tamper evidence.

Each test names the failure it prevents. The ledger is the only writer of stock
quantities, so a hole here is a hole in every number the pharmacy trusts.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from services.core.inventory import ledger as L

TODAY = date(2026, 8, 2)


def lot(lot_id, qty, exp_days=365, **kw):
    return L.Lot(lot_id=lot_id, lot_number=f"LOT-{lot_id}",
                 expiry_date=TODAY + timedelta(days=exp_days) if exp_days is not None else None,
                 quantity_on_hand=Decimal(str(qty)), **kw)


# ── FEFO ──────────────────────────────────────────────────────────────────
def test_picks_the_earliest_expiry_first():
    lots = [lot("a", 10, 200), lot("b", 10, 30), lot("c", 10, 90)]
    picks = L.pick_fefo(lots, 15, as_of=TODAY)
    assert [p[0].lot_id for p in picks] == ["b", "c"]
    assert [p[1] for p in picks] == [Decimal("10.000"), Decimal("5.000")]


def test_unknown_expiry_never_beats_a_known_one():
    """A lot with no expiry date sorts last. Treating NULL as 'expires first'
    would drain undated stock while dated stock ages out."""
    lots = [lot("undated", 10, None), lot("dated", 10, 30)]
    picks = L.pick_fefo(lots, 5, as_of=TODAY)
    assert picks[0][0].lot_id == "dated"


def test_allocation_conserves_exactly():
    lots = [lot("a", 3.5, 10), lot("b", 4.25, 20)]
    picks = L.pick_fefo(lots, 7.75, as_of=TODAY)
    assert sum(p[1] for p in picks) == Decimal("7.750")


def test_short_stock_raises_rather_than_under_filling():
    """Silent partial fulfilment is how the books and the shelf diverge."""
    with pytest.raises(L.LedgerError, match="insufficient stock"):
        L.pick_fefo([lot("a", 5)], 10, as_of=TODAY)


def test_reserved_units_are_not_allocatable():
    l = L.Lot(lot_id="a", lot_number="A", expiry_date=TODAY + timedelta(days=90),
              quantity_on_hand=Decimal("10"), quantity_reserved=Decimal("8"))
    with pytest.raises(L.LedgerError):
        L.pick_fefo([l], 5, as_of=TODAY)
    assert L.pick_fefo([l], 2, as_of=TODAY)[0][1] == Decimal("2.000")


# ── Blocked stock ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("flag", ["is_recalled", "is_quarantined", "cold_chain_breach"])
def test_blocked_lots_are_never_dispensed(flag):
    lots = [lot("bad", 100, 30, **{flag: True}), lot("good", 100, 200)]
    picks = L.pick_fefo(lots, 50, as_of=TODAY)
    assert [p[0].lot_id for p in picks] == ["good"]


def test_expired_lots_are_not_dispensable():
    lots = [lot("expired", 100, -1), lot("good", 100, 200)]
    assert [p[0].lot_id for p in L.pick_fefo(lots, 10, as_of=TODAY)] == ["good"]


def test_expiry_removal_can_reach_the_stock_a_dispense_cannot():
    """The pull of expired/quarantined stock is precisely a removal OF blocked
    stock — it must not be blocked by the rule that blocks dispensing."""
    lots = [lot("expired", 40, -5, is_quarantined=True)]
    plans = L.plan_issue(lots, 40, movement_type="EXPIRY_REMOVAL",
                         reason="monthly expiry pull", as_of=TODAY)
    assert plans[0].quantity_after == Decimal("0.000")
    assert plans[0].requires_approval is True


# ── Sign discipline & validation ──────────────────────────────────────────
def test_issue_is_always_negative_and_receipt_always_positive():
    out = L.plan_issue([lot("a", 10)], 4, movement_type="DISPENSE",
                       reason="rx", as_of=TODAY)[0]
    inn = L.plan_receipt(lot("a", 10), 4, movement_type="RECEIPT", reason="po")
    assert out.quantity_delta == Decimal("-4.000")
    assert inn.quantity_delta == Decimal("4.000")


def test_a_receipt_type_cannot_be_used_to_issue():
    with pytest.raises(L.LedgerError, match="not an issue movement"):
        L.plan_issue([lot("a", 10)], 1, movement_type="RECEIPT", reason="x", as_of=TODAY)


def test_every_movement_needs_a_reason():
    with pytest.raises(L.LedgerError, match="reason"):
        L.plan_issue([lot("a", 10)], 1, movement_type="DISPENSE", reason="  ", as_of=TODAY)


def test_quantities_are_exact_not_floating_point():
    """0.1 + 0.2 must equal 0.3 in a ledger that will be reconciled against a
    physical count."""
    picks = L.pick_fefo([lot("a", 0.1, 10), lot("b", 0.2, 20)], 0.3, as_of=TODAY)
    assert sum(p[1] for p in picks) == Decimal("0.300")


def test_controlled_substances_always_require_approval():
    p = L.plan_issue([lot("a", 10)], 1, movement_type="DISPENSE", reason="rx",
                     as_of=TODAY, is_controlled=True)[0]
    assert p.requires_approval is True


# ── Count variance ────────────────────────────────────────────────────────
def test_matching_count_produces_no_movement():
    assert L.plan_count_variance(lot("a", 10), 10, reason="cycle count") is None


def test_count_shortfall_is_a_loss_requiring_approval():
    p = L.plan_count_variance(lot("a", 10), 7, reason="cycle count", is_controlled=True)
    assert p.movement_type == "COUNT_LOSS"
    assert p.quantity_delta == Decimal("-3.000")
    assert p.requires_approval is True
    assert p.meta["variance_pct"] == pytest.approx(30.0)


def test_count_surplus_is_a_gain_not_a_silent_overwrite():
    p = L.plan_count_variance(lot("a", 10), 12, reason="cycle count")
    assert (p.movement_type, p.quantity_delta) == ("COUNT_GAIN", Decimal("2.000"))


def test_negative_count_is_rejected():
    with pytest.raises(L.LedgerError):
        L.plan_count_variance(lot("a", 10), -1, reason="x")


# ── Tamper-evident chain ──────────────────────────────────────────────────
def _row(i, prev, delta="-1", after="9"):
    r = {"id": f"m{i}", "pharmacy_id": "ph1", "irc": "123", "inventory_lot_id": "l1",
         "movement_type": "DISPENSE", "quantity_delta": delta, "quantity_after": after,
         "created_by": "staff1", "created_at_iso": f"2026-08-02T10:0{i}:00+00:00",
         "prev_hash": prev}
    r["event_hash"] = L.movement_hash(
        prev_hash=prev, pharmacy_id=r["pharmacy_id"], irc=r["irc"],
        lot_id=r["inventory_lot_id"], movement_type=r["movement_type"],
        quantity_delta=r["quantity_delta"], quantity_after=r["quantity_after"],
        actor_id=r["created_by"], created_at_iso=r["created_at_iso"])
    return r


def _chain(n=4):
    rows, prev = [], L.GENESIS
    for i in range(n):
        r = _row(i, prev)
        rows.append(r)
        prev = r["event_hash"]
    return rows


def test_an_untouched_chain_verifies():
    res = L.verify_chain(_chain())
    assert res["intact"] is True and res["verified"] == 4


def test_editing_a_quantity_breaks_the_chain():
    rows = _chain()
    rows[2]["quantity_delta"] = "-999"        # someone hides a loss
    res = L.verify_chain(rows)
    assert res["intact"] is False and res["break_index"] == 2
    assert "edited" in res["detail"]


def test_deleting_a_row_breaks_the_chain():
    rows = _chain()
    del rows[1]
    res = L.verify_chain(rows)
    assert res["intact"] is False and res["break_index"] == 1


def test_the_hash_is_reproducible_from_the_stored_row_alone():
    """Verification must work years later from the database contents, with no
    in-memory state."""
    r = _row(0, L.GENESIS)
    again = L.movement_hash(
        prev_hash=L.GENESIS, pharmacy_id="ph1", irc="123", lot_id="l1",
        movement_type="DISPENSE", quantity_delta=Decimal("-1.000"),
        quantity_after=Decimal("9.0"), actor_id="staff1",
        created_at_iso="2026-08-02T10:00:00+00:00")
    assert again == r["event_hash"]
