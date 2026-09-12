"""Receipt anomaly — catching the wrong number at the door.

Goods receipt is where bad numbers enter inventory, and everything downstream is
computed from them. `check_unit_conversion` finds the 30x pack/unit error after
the shelf figure is already wrong and has already driven a purchase; this finds
it while the delivery is still on the counter.

The engine's whole claim to being usable today rests on the formulary: 36,612
rows carry an announced price, so the reference check works before this pharmacy
has dispensed anything. The history checks need receipts and say so.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from services.core.inventory import receipt_anomaly as RA

TODAY = date(2026, 8, 16)
FAR = TODAY + timedelta(days=400)


def check(**kw):
    base = dict(ndc11="N1", lot_number="L1", unit_cost=Decimal("200"),
                quantity=Decimal("100"), expiry_date=FAR, as_of=TODAY,
                announced_price=Decimal("25000"), package_count=100)
    base.update(kw)
    return RA.check_receipt(**base)


def codes(v):
    return {f.check for f in v.findings}


# ── the pack-basis trap, which this catalogue has already sprung once ─────
def test_the_reference_price_is_per_unit_not_per_pack():
    """`announced_price` is per pack and `unit_cost` is per unit. Comparing them
    directly makes every ordinary receipt look thirty times too cheap."""
    assert RA.unit_reference_price(Decimal("25000"), 100) == Decimal("250.000")
    assert RA.unit_reference_price(Decimal("75000"), 30) == Decimal("2500.000")


def test_an_ordinary_purchase_below_retail_is_not_flagged():
    """Acquisition cost sits under the announced retail price. A band that fires
    on every normal delivery gets switched off."""
    v = check(unit_cost=Decimal("200"))          # 250 per unit announced
    assert "cost_above_reference" not in codes(v)
    assert "cost_below_reference" not in codes(v)


def test_a_cost_above_the_published_retail_price_is_flagged():
    v = check(unit_cost=Decimal("400"))
    assert "cost_above_reference" in codes(v)
    assert v.worst == "high"


def test_an_implausibly_cheap_cost_is_flagged_as_a_pack_slip():
    """A pack cost divided by the wrong pack size lands here."""
    v = check(unit_cost=Decimal("2"))            # under 5% of 250
    assert "cost_below_reference" in codes(v)
    assert "pack size" in [f.remediation for f in v.findings
                           if f.check == "cost_below_reference"][0]


def test_a_product_with_no_formulary_price_says_the_check_is_unavailable():
    """Silence would read as 'checked and fine'."""
    v = check(announced_price=None)
    assert "no_price_reference" in codes(v)
    assert [f.basis for f in v.findings if f.check == "no_price_reference"] == \
        ["no_reference"]


def test_a_missing_pack_size_disables_the_comparison_rather_than_guessing():
    """Assuming a pack of one would call every receipt implausibly cheap."""
    assert RA.unit_reference_price(Decimal("25000"), None) is None
    v = check(package_count=None)
    assert "no_price_reference" in codes(v)


# ── robust statistics: one bad receipt must not train the detector ────────
def test_too_little_history_is_reported_not_guessed():
    v = check(cost_history=[Decimal("200"), Decimal("205")])
    f = [f for f in v.findings if f.check == "cost_history"][0]
    assert f.basis == "insufficient_history"
    assert "too few" in f.detail


def test_a_cost_far_from_this_items_usual_is_flagged():
    v = check(unit_cost=Decimal("900"),
              cost_history=[Decimal("200"), Decimal("205"), Decimal("198"),
                            Decimal("202")])
    assert "cost_unlike_history" in codes(v)


def test_an_ordinary_variation_is_not_flagged():
    v = check(unit_cost=Decimal("207"),
              cost_history=[Decimal("200"), Decimal("205"), Decimal("198"),
                            Decimal("202")])
    assert "cost_unlike_history" not in codes(v)


def test_the_spread_is_robust_so_one_earlier_outlier_does_not_hide_the_next():
    """With a mean and standard deviation, a single 5,000 in the history inflates
    the spread enough that the next bad receipt looks ordinary — the detector
    training itself to accept the thing it exists to catch."""
    poisoned = [Decimal("200"), Decimal("205"), Decimal("198"), Decimal("202"),
                Decimal("5000")]
    z = RA.robust_z(Decimal("900"), poisoned)
    assert z is not None and abs(z) > RA.MAD_THRESHOLD


def test_an_identical_history_treats_any_change_as_a_departure():
    """Every prior receipt the same price means the spread is zero; a different
    number is a departure rather than an undefined division."""
    z = RA.robust_z(Decimal("500"), [Decimal("200")] * 4)
    assert z is not None and abs(z) > RA.MAD_THRESHOLD
    assert RA.robust_z(Decimal("200"), [Decimal("200")] * 4) == Decimal("0.000")


def test_a_quantity_far_from_the_usual_is_flagged_as_a_unit_error():
    v = check(quantity=Decimal("3000"),
              quantity_history=[Decimal("100"), Decimal("100"), Decimal("90"),
                                Decimal("120")])
    assert "quantity_unlike_history" in codes(v)
    assert "packs keyed as units" in [f.detail for f in v.findings
                                      if f.check == "quantity_unlike_history"][0]


# ── the delivery itself ───────────────────────────────────────────────────
def test_stock_that_arrives_already_expired_is_critical():
    v = check(expiry_date=TODAY - timedelta(days=2))
    assert "expired_on_arrival" in codes(v)
    assert v.worst == "critical"
    assert "supplier claim" in [f.remediation for f in v.findings
                                if f.check == "expired_on_arrival"][0]


def test_short_shelf_life_on_arrival_is_flagged():
    v = check(expiry_date=TODAY + timedelta(days=30))
    assert "short_shelf_life" in codes(v)


def test_a_delivery_older_than_stock_already_held_is_flagged():
    """FEFO will issue the new delivery first, which is rarely what was
    intended and often means the supplier shipped near-dated stock."""
    v = check(expiry_date=TODAY + timedelta(days=100),
              shortest_existing_expiry=TODAY + timedelta(days=300))
    assert "arrives_older_than_stock" in codes(v)


def test_a_normal_delivery_passes_everything():
    v = check(cost_history=[Decimal("200"), Decimal("205"), Decimal("198"),
                            Decimal("202")],
              quantity_history=[Decimal("100"), Decimal("100"), Decimal("90")])
    assert v.clean is True
    assert v.worst == "info"


def test_a_repeated_lot_number_suggests_a_re_keyed_delivery():
    v = check(prior_lot_numbers={"L1"})
    assert "repeated_lot_number" in codes(v)


def test_a_new_lot_number_is_not_flagged():
    v = check(prior_lot_numbers={"OTHER"})
    assert "repeated_lot_number" not in codes(v)


# ── what becomes advice ───────────────────────────────────────────────────
def test_only_findings_that_need_a_decision_are_raised_as_advice():
    """Info is context for whoever is already looking at the receipt. Filing it
    would bury the ones that need a decision."""
    info_only = check(announced_price=None, cost_history=[])
    assert info_only.clean is False              # it has findings
    assert all(f.severity == "info" for f in info_only.findings)
    assert RA.worth_raising(info_only) is False


def test_a_real_problem_is_raised():
    assert RA.worth_raising(check(unit_cost=Decimal("400"))) is True


def test_a_receipt_with_no_cost_is_not_price_checked_but_is_still_date_checked():
    v = check(unit_cost=None, expiry_date=TODAY - timedelta(days=1))
    assert "cost_above_reference" not in codes(v)
    assert "expired_on_arrival" in codes(v)
