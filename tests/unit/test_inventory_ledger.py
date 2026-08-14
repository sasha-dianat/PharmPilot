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


# ── dispense allocation: the one issue type allowed to come up short ──────
def test_a_covered_dispense_allocates_fefo_and_reports_no_shortfall():
    a = L.plan_dispense([lot("a", 10, 200), lot("b", 10, 30)], 15, as_of=TODAY,
                        reason="rx")
    assert [p.lot_id for p in a.plans] == ["b", "a"]
    assert (a.allocated, a.shortfall, a.complete) == (Decimal("15.000"),
                                                      Decimal("0.000"), True)


def test_a_short_dispense_takes_what_exists_and_records_the_gap():
    """The medicine is already with the patient. Refusing here would let a
    bookkeeping error stop patient care; inventing the units would hide the
    discrepancy worth knowing about."""
    a = L.plan_dispense([lot("a", 4)], 10, as_of=TODAY, reason="rx")
    assert a.allocated == Decimal("4.000")
    assert a.shortfall == Decimal("6.000")
    assert a.complete is False
    assert sum(abs(p.quantity_delta) for p in a.plans) == a.allocated


def test_a_dispense_with_no_stock_at_all_still_returns_rather_than_raising():
    a = L.plan_dispense([], 10, as_of=TODAY, reason="rx")
    assert a.plans == [] and a.shortfall == Decimal("10.000")


def test_blocked_and_expired_lots_are_never_dispensed_even_when_short():
    """Coming up short is acceptable; handing over recalled stock is not."""
    lots = [lot("recalled", 100, 200, is_recalled=True),
            lot("expired", 100, -1),
            lot("ok", 3, 100)]
    a = L.plan_dispense(lots, 50, as_of=TODAY, reason="rx")
    assert [p.lot_id for p in a.plans] == ["ok"]
    assert a.shortfall == Decimal("47.000")


def test_a_dispense_never_requires_approval():
    """A prescription plus a pharmacist's verification is the authorisation; a
    second queue between a patient and their medicine is not."""
    a = L.plan_dispense([lot("a", 10)], 5, as_of=TODAY, reason="rx",
                        is_controlled=True)
    assert all(p.requires_approval is False for p in a.plans)
    assert all(p.movement_type == "DISPENSE" for p in a.plans)


def test_a_non_positive_dispense_is_still_a_programming_error():
    for bad in (0, -5):
        with pytest.raises(L.LedgerError):
            L.plan_dispense([lot("a", 10)], bad, as_of=TODAY, reason="rx")


def test_reserved_stock_is_not_available_to_another_patient():
    l = L.Lot(lot_id="a", lot_number="A", expiry_date=TODAY + timedelta(days=90),
              quantity_on_hand=Decimal("10"), quantity_reserved=Decimal("7"))
    a = L.plan_dispense([l], 10, as_of=TODAY, reason="rx")
    assert a.allocated == Decimal("3.000") and a.shortfall == Decimal("7.000")


def test_allocation_conserves_across_many_lots():
    lots = [lot(str(i), 7, 10 + i) for i in range(5)]
    a = L.plan_dispense(lots, 33, as_of=TODAY, reason="rx")
    assert a.allocated + a.shortfall == a.requested
    assert sum(abs(p.quantity_delta) for p in a.plans) == a.allocated


# ── buckets: damage relocates units, it does not delete them ──────────────
def test_damage_moves_units_into_the_bucket_and_conserves_the_lot():
    """The defect this replaces: DAMAGE decremented on-hand and the units
    ceased to exist — uncountable, unvaluable, unclaimable from the supplier."""
    l = lot("a", 100)
    p = L.plan_bucket_transfer(l, 20, movement_type="DAMAGE", reason="crushed carton")
    assert p.quantity_before == Decimal("100.000")
    assert p.quantity_after == Decimal("80.000")
    assert (p.to_bucket, p.bucket_before, p.bucket_after) == (
        "damaged", Decimal("0.000"), Decimal("20.000"))
    # the lot still physically holds 100
    assert p.quantity_after + p.bucket_after == p.quantity_before + p.bucket_before


def test_damage_needs_no_approval():
    """Taking stock out of use must never wait for a signature — the same rule
    that lets anyone quarantine a lot."""
    p = L.plan_bucket_transfer(lot("a", 10), 1, movement_type="DAMAGE", reason="x")
    assert p.requires_approval is False


def test_damage_accumulates_across_incidents():
    l = L.Lot(lot_id="a", lot_number="A", expiry_date=None,
              quantity_on_hand=Decimal("80"), quantity_damaged=Decimal("20"))
    p = L.plan_bucket_transfer(l, 5, movement_type="DAMAGE", reason="second breakage")
    assert p.bucket_after == Decimal("25.000") and p.quantity_after == Decimal("75.000")


def test_more_cannot_be_damaged_than_is_on_the_shelf():
    with pytest.raises(L.LedgerError, match="only"):
        L.plan_bucket_transfer(lot("a", 5), 10, movement_type="DAMAGE", reason="x")


def test_damage_still_demands_a_reason_and_a_positive_quantity():
    with pytest.raises(L.LedgerError, match="reason"):
        L.plan_bucket_transfer(lot("a", 10), 1, movement_type="DAMAGE", reason=" ")
    for bad in (0, -1):
        with pytest.raises(L.LedgerError):
            L.plan_bucket_transfer(lot("a", 10), bad, movement_type="DAMAGE", reason="x")


def test_an_ordinary_removal_is_not_a_bucket_transfer():
    with pytest.raises(L.LedgerError, match="not a bucket transfer"):
        L.plan_bucket_transfer(lot("a", 10), 1, movement_type="WASTE", reason="x")


# ── the way out of the bucket ─────────────────────────────────────────────
def _damaged_lot(on_hand=80, damaged=20):
    return L.Lot(lot_id="a", lot_number="A", expiry_date=None,
                 quantity_on_hand=Decimal(str(on_hand)),
                 quantity_damaged=Decimal(str(damaged)))


def test_writing_off_from_a_bucket_leaves_sellable_stock_untouched():
    """The units left on-hand when they entered the bucket; deducting them
    again is exactly the double-count the bucket prevents."""
    p = L.plan_bucket_writeoff(_damaged_lot(), 20,
                               movement_type="SUPPLIER_CREDIT",
                               from_bucket="damaged", reason="credit note 1182")
    assert p.quantity_before == p.quantity_after == Decimal("80.000")
    assert (p.from_bucket, p.bucket_after) == ("damaged", Decimal("0.000"))


def test_a_bucket_write_off_always_needs_approval():
    """This is the step that turns a recoverable asset into a loss."""
    p = L.plan_bucket_writeoff(_damaged_lot(), 5, movement_type="WASTE",
                               from_bucket="damaged", reason="unsalvageable")
    assert p.requires_approval is True


def test_more_cannot_be_written_off_than_the_bucket_holds():
    with pytest.raises(L.LedgerError, match="only"):
        L.plan_bucket_writeoff(_damaged_lot(damaged=5), 10, movement_type="WASTE",
                               from_bucket="damaged", reason="x")


def test_an_unknown_bucket_is_rejected():
    with pytest.raises(L.LedgerError, match="unknown bucket"):
        L.plan_bucket_writeoff(_damaged_lot(), 1, movement_type="WASTE",
                               from_bucket="somewhere", reason="x")


def test_damaged_stock_is_never_dispensable():
    """It is off the shelf by construction: FEFO only ever sees on-hand."""
    l = _damaged_lot(on_hand=0, damaged=50)
    a = L.plan_dispense([l], 10, reason="rx")
    assert a.plans == [] and a.shortfall == Decimal("10.000")


def test_the_full_lifecycle_conserves_from_receipt_to_write_off():
    """100 received → 20 damaged → staged for return → credited away.
    80 sellable throughout, and nothing silently vanished at any step."""
    dmg = L.plan_bucket_transfer(lot("a", 100), 20,
                                 movement_type="DAMAGE", reason="crushed")
    mid = L.Lot(lot_id="a", lot_number="A", expiry_date=None,
                quantity_on_hand=dmg.quantity_after,
                quantity_damaged=dmg.bucket_after)

    staged = L.plan_bucket_transfer(mid, 20, movement_type="RETURN_TO_SUPPLIER",
                                    reason="claim raised", from_bucket="damaged")
    assert staged.quantity_after == Decimal("80.000")     # sellable untouched
    assert staged.bucket_after == Decimal("20.000")       # now in `returned`

    staged_lot = L.Lot(lot_id="a", lot_number="A", expiry_date=None,
                       quantity_on_hand=Decimal("80"), quantity_damaged=Decimal("0"),
                       quantity_returned=Decimal("20"))
    out = L.plan_bucket_writeoff(staged_lot, 20, movement_type="SUPPLIER_CREDIT",
                                 from_bucket="returned", reason="credit note 1182")
    assert out.quantity_after == Decimal("80.000")
    assert out.bucket_after == Decimal("0.000")
    assert out.requires_approval is True


# ── transfers out and supplier returns ────────────────────────────────────
def test_transfer_out_holds_stock_in_transit_rather_than_deleting_it():
    """Stock that left the depot has not left the pharmacy. Until it arrives it
    is neither sellable here nor gone — which is exactly what a bucket is for."""
    p = L.plan_bucket_transfer(lot("a", 100), 30, movement_type="TRANSFER_OUT",
                               reason="depot → shelf")
    assert p.to_bucket == "in_transit"
    assert (p.quantity_after, p.bucket_after) == (Decimal("70.000"), Decimal("30.000"))
    assert p.quantity_after + p.bucket_after == Decimal("100.000")


def test_staging_a_supplier_return_is_immediate_and_conserving():
    p = L.plan_bucket_transfer(lot("a", 50), 10,
                               movement_type="RETURN_TO_SUPPLIER",
                               reason="over-shipped")
    assert p.to_bucket == "returned" and p.requires_approval is False
    assert p.quantity_after + p.bucket_after == Decimal("50.000")


def test_damaged_stock_can_be_staged_for_return_without_touching_sellable_stock():
    """The units stopped being sellable when they were damaged; passing them
    back through on-hand to reach `returned` would double-count them."""
    l = L.Lot(lot_id="a", lot_number="A", expiry_date=None,
              quantity_on_hand=Decimal("80"), quantity_damaged=Decimal("20"))
    p = L.plan_bucket_transfer(l, 20, movement_type="RETURN_TO_SUPPLIER",
                               reason="claim", from_bucket="damaged")
    assert p.quantity_before == p.quantity_after == Decimal("80.000")
    assert (p.from_bucket, p.to_bucket) == ("damaged", "returned")


def test_a_bucket_cannot_transfer_into_itself():
    l = L.Lot(lot_id="a", lot_number="A", expiry_date=None,
              quantity_on_hand=Decimal("10"), quantity_damaged=Decimal("5"))
    with pytest.raises(L.LedgerError, match="cannot also draw from it"):
        L.plan_bucket_transfer(l, 1, movement_type="DAMAGE", reason="x",
                               from_bucket="damaged")


def test_more_cannot_be_staged_than_the_source_holds():
    l = L.Lot(lot_id="a", lot_number="A", expiry_date=None,
              quantity_on_hand=Decimal("80"), quantity_damaged=Decimal("5"))
    with pytest.raises(L.LedgerError, match="only"):
        L.plan_bucket_transfer(l, 10, movement_type="RETURN_TO_SUPPLIER",
                               reason="x", from_bucket="damaged")


# ── the way back onto the shelf ───────────────────────────────────────────
def _in_transit_lot(on_hand=70, transit=30):
    return L.Lot(lot_id="a", lot_number="A", expiry_date=None,
                 quantity_on_hand=Decimal(str(on_hand)),
                 quantity_in_transit=Decimal(str(transit)))


def test_an_arrival_returns_stock_to_sale_and_needs_approval():
    """Releasing is the direction that always needs a second person: these
    units have been out of sight."""
    p = L.plan_bucket_release(_in_transit_lot(), 30, from_bucket="in_transit",
                              reason="arrived at shelf")
    assert (p.quantity_after, p.bucket_after) == (Decimal("100.000"), Decimal("0.000"))
    assert p.requires_approval is True
    assert p.quantity_delta == Decimal("30.000")          # a receipt, so positive


def test_a_partial_arrival_leaves_the_remainder_in_transit():
    p = L.plan_bucket_release(_in_transit_lot(), 12, from_bucket="in_transit",
                              reason="partial delivery")
    assert (p.quantity_after, p.bucket_after) == (Decimal("82.000"), Decimal("18.000"))


def test_more_cannot_arrive_than_was_sent():
    with pytest.raises(L.LedgerError, match="only"):
        L.plan_bucket_release(_in_transit_lot(transit=5), 10,
                              from_bucket="in_transit", reason="x")


def test_a_recalled_lot_cannot_have_stock_released_back_onto_the_shelf():
    l = L.Lot(lot_id="a", lot_number="A", expiry_date=None,
              quantity_on_hand=Decimal("10"), quantity_in_transit=Decimal("5"),
              is_recalled=True)
    with pytest.raises(L.LedgerError, match="recalled"):
        L.plan_bucket_release(l, 5, from_bucket="in_transit", reason="arrived")


def test_a_release_must_use_a_receipt_type():
    with pytest.raises(L.LedgerError, match="cannot release"):
        L.plan_bucket_release(_in_transit_lot(), 5, from_bucket="in_transit",
                              reason="x", movement_type="WASTE")


def test_a_patient_can_never_be_handed_bucketed_stock():
    """DISPENSE is absent from the write-off vocabulary by design, so draining
    a bucket to a patient is not merely forbidden but unrepresentable."""
    assert "DISPENSE" not in L.BUCKET_WRITEOFF_TYPES
    with pytest.raises(L.LedgerError, match="cannot write off"):
        L.plan_bucket_writeoff(_damaged_lot(), 1, movement_type="DISPENSE",
                               from_bucket="damaged", reason="x")


def test_transfers_out_and_returns_are_no_longer_removals():
    """They conserve now, so treating one as an issue must fail loudly rather
    than silently deleting the stock it was meant to hold."""
    for t in ("TRANSFER_OUT", "RETURN_TO_SUPPLIER"):
        assert t not in L.ISSUE_TYPES
        with pytest.raises(L.LedgerError, match="not an issue movement"):
            L.plan_issue([lot("a", 10)], 1, movement_type=t, reason="x", as_of=TODAY)


# ── quantities the schema cannot hold ─────────────────────────────────────
# Found by the simulator (phase 3, "huge receipt"): a receipt of ~10^7 units was
# accepted, and the *next* receipt on that SKU then failed with a raw numeric
# overflow from inside the flush. A mistyped 99999999 wedged the item.

def test_a_receipt_beyond_what_a_quantity_column_holds_is_refused():
    lot = L.Lot(lot_id="L1", lot_number="A", expiry_date=date(2027, 1, 1),
                quantity_on_hand=Decimal("0"))
    with pytest.raises(L.LedgerError) as e:
        L.plan_receipt(lot, Decimal("10000000"), movement_type="RECEIPT",
                       reason="delivery")
    assert "beyond the" in str(e.value)
    assert "split the delivery" in str(e.value)


def test_a_receipt_that_tips_an_existing_lot_over_the_limit_is_refused():
    """The overflow is reached by accumulation, not by one large number."""
    lot = L.Lot(lot_id="L1", lot_number="A", expiry_date=date(2027, 1, 1),
                quantity_on_hand=L.MAX_QUANTITY)
    with pytest.raises(L.LedgerError):
        L.plan_receipt(lot, Decimal("0.001"), movement_type="RECEIPT",
                       reason="one more unit")


def test_a_receipt_landing_exactly_on_the_limit_is_allowed():
    lot = L.Lot(lot_id="L1", lot_number="A", expiry_date=date(2027, 1, 1),
                quantity_on_hand=Decimal("0"))
    plan = L.plan_receipt(lot, L.MAX_QUANTITY, movement_type="RECEIPT",
                          reason="a very large but storable delivery")
    assert plan.quantity_after == L.MAX_QUANTITY


def test_the_limit_matches_what_the_column_can_store():
    """NUMERIC(10,3) holds up to 9999999.999. If the schema widens, this moves."""
    assert L.MAX_QUANTITY == Decimal("9999999.999")
