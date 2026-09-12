"""The shelf oracle — the sales floor, and who may be accused of taking from it.

The property that carries this domain is monotonicity of doubt: uncertainty must
only ever *weaken* a verdict. A detector that grows more confident as its own
guesswork grows is backwards, and one false accusation ends the credibility of
every true one.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from tests.simulation.domains.shelf import (
    BASES, INFERENCE_NOISE, INFERRED, MIN_UNITS_TO_JUDGE, OBSERVED, VERDICTS,
    ShelfDomain, q)
from tests.simulation.spine.domain import Domain
from tests.simulation.spine.facts import Fact
from tests.simulation.spine.harness import Harness

START = date(2026, 1, 1)
T0 = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
D = Decimal


def place(s, pid, units, *, shelf="A-03", lot="L1", ndc="N1", at=T0, seq=1):
    s.observe(Fact(seq=seq, at=at, day=0, kind="placed_on_shelf", subject=ndc,
                   quantity=D(str(units)), actor="alice",
                   payload={"placement_id": pid, "shelf_id": shelf,
                            "lot_id": lot, "placed_at": at}))


def take(s, pid, units, *, basis=INFERRED, seq=2, at=T0):
    s.observe(Fact(seq=seq, at=at, day=0, kind="taken_off_shelf", subject="N1",
                   quantity=D(str(units)),
                   payload={"placement_id": pid, "basis": basis}))


# ── the provenance distinction, which is the whole point ──────────────────
def test_a_placement_is_observed_and_a_dispense_take_is_inferred():
    assert OBSERVED in BASES and INFERRED in BASES
    s = ShelfDomain()
    place(s, "P1", 10)
    take(s, "P1", 3, basis=INFERRED)
    assert s.inferred[("A-03", "N1")] == D("3.000")


def test_a_scanned_take_adds_no_doubt():
    """If somebody scanned the shelf, the movement is evidence and must not be
    discounted as our own guesswork."""
    s = ShelfDomain()
    place(s, "P1", 10)
    take(s, "P1", 3, basis=OBSERVED)
    assert ("A-03", "N1") not in s.inferred


def test_an_unknown_basis_is_the_oracles_fault():
    s = ShelfDomain()
    place(s, "P1", 10)
    take(s, "P1", 1, basis="guessed")
    assert any("guessed" in v for v in s.violations())


# ── allocation ────────────────────────────────────────────────────────────
def test_allocation_takes_the_oldest_placement_first():
    s = ShelfDomain()
    place(s, "NEW", 10, at=datetime(2026, 1, 5, tzinfo=timezone.utc))
    place(s, "OLD", 10, at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    plan = s.allocate("L1", 4)
    assert [t["placement_id"] for t in plan["takes"]] == ["OLD"]


def test_allocation_spills_to_the_next_placement_then_to_backstock():
    s = ShelfDomain()
    place(s, "A", 5, at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    place(s, "B", 5, at=datetime(2026, 1, 2, tzinfo=timezone.utc))
    plan = s.allocate("L1", 12)
    assert [t["placement_id"] for t in plan["takes"]] == ["A", "B"]
    assert plan["from_shelf"] == D("10.000")
    assert plan["from_backstock"] == D("2.000")


def test_backstock_is_ordinary_and_not_an_error():
    """A lot of 100 with 40 placed is the normal case; dispensing 60 takes 40 off
    the shelf and 20 from behind it."""
    s = ShelfDomain()
    place(s, "A", 40)
    plan = s.allocate("L1", 60)
    assert s.allocation_findings(60, plan) == []
    assert plan["from_backstock"] == D("20.000")


def test_every_allocation_accounts_for_every_unit():
    s = ShelfDomain()
    place(s, "A", 7, at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    place(s, "B", 3, at=datetime(2026, 1, 2, tzinfo=timezone.utc))
    for want in ("0.5", "1", "7", "9.999", "10", "10.001", "40"):
        plan = s.allocate("L1", want)
        assert s.allocation_findings(want, plan) == [], want


def test_an_allocation_that_loses_units_is_reported():
    """Checked against a hand-built plan: an invariant that can only pass
    because the oracle's own allocator prevents the input is not checking
    anything."""
    s = ShelfDomain()
    bad = {"takes": [{"placement_id": "A", "shelf_id": "A-03", "before": D("5"),
                      "units": D("5"), "after": D("0"), "basis": INFERRED}],
           "from_shelf": D("5"), "from_backstock": D("0")}
    found = s.allocation_findings(10, bad)
    assert any("allocation lost units" in f for f in found)


def test_a_take_that_drives_a_placement_negative_is_reported():
    s = ShelfDomain()
    place(s, "P1", 2)
    take(s, "P1", 5)
    assert any("the shelf would go negative" in f for f in s._findings)


def test_a_take_off_an_unknown_placement_is_reported():
    s = ShelfDomain()
    take(s, "GHOST", 5)
    assert any("no record of" in f for f in s._findings)


def test_allocation_only_touches_the_requested_lot():
    s = ShelfDomain()
    place(s, "P1", 10, lot="L1")
    place(s, "P2", 10, lot="L2")
    plan = s.allocate("L1", 20)
    assert [t["placement_id"] for t in plan["takes"]] == ["P1"]
    assert plan["from_backstock"] == D("10.000")


# ── the detector: verdicts ────────────────────────────────────────────────
def test_an_uncounted_shelf_claims_nothing():
    v = ShelfDomain().reconcile(shelf_id="A", ndc11="N1", expected=10,
                                counted=None)
    assert v.verdict == "uncounted" and v.counted is None


def test_a_matching_count_agrees():
    v = ShelfDomain().reconcile(shelf_id="A", ndc11="N1", expected=10, counted=10)
    assert v.verdict == "agrees" and v.variance == 0


def test_counting_more_than_expected_is_never_a_loss():
    """Stock does not appear by itself. Counted above expected is a bookkeeping
    failure, and treating it as a finding against a person would be absurd."""
    v = ShelfDomain().reconcile(shelf_id="A", ndc11="N1", expected=10, counted=14)
    assert v.verdict == "surplus"
    assert not ShelfDomain().worth_investigating(v)


def test_one_missing_unit_is_a_miscount_not_a_case():
    v = ShelfDomain().reconcile(shelf_id="A", ndc11="N1", expected=10, counted=9)
    assert v.verdict == "inconclusive"


def test_a_shortfall_inside_the_inferred_movement_is_not_called_theft():
    """Four units were guessed off this shelf and three are missing; the three
    may be standing on the next shelf along."""
    v = ShelfDomain().reconcile(shelf_id="A", ndc11="N1", expected=10, counted=7,
                                inferred_units=4)
    assert v.verdict == "inconclusive"
    assert any("next shelf along" in c or "next shelf along" in v.why
               for c in [v.why])


def test_a_shortfall_larger_than_the_guesswork_is_shrinkage():
    v = ShelfDomain().reconcile(shelf_id="A", ndc11="N1", expected=100,
                                counted=60, inferred_units=4, sell_price=1000)
    assert v.verdict == "shrinkage"
    assert v.value_at_risk == D("40000.000")
    assert ShelfDomain().worth_investigating(v)


def test_shrinkage_without_a_price_is_reported_in_units():
    v = ShelfDomain().reconcile(shelf_id="A", ndc11="N1", expected=100,
                                counted=60)
    assert v.verdict == "shrinkage" and v.value_at_risk is None
    assert any("units not money" in c for c in v.concerns)


def test_every_verdict_is_in_the_declared_vocabulary():
    s = ShelfDomain()
    seen = set()
    for exp, got, inf in ((10, None, 0), (10, 10, 0), (10, 14, 0), (10, 9, 0),
                          (10, 7, 4), (100, 60, 0)):
        seen.add(s.reconcile(shelf_id="A", ndc11="N1", expected=exp,
                             counted=got, inferred_units=inf).verdict)
    assert seen <= set(VERDICTS)
    assert seen == set(VERDICTS)          # all five are reachable


# ── monotonicity of doubt: the property that matters most ─────────────────
def test_more_unscanned_movement_never_strengthens_a_verdict():
    """A detector that grows more confident as its own guesswork grows is
    backwards. Sweeping the inferred figure upward may only ever move a verdict
    from shrinkage toward inconclusive, never the other way."""
    s = ShelfDomain()
    strength = {"inconclusive": 0, "agrees": 0, "surplus": 0, "uncounted": 0,
                "shrinkage": 1}
    previous = 1
    for inferred in range(0, 60, 3):
        v = s.reconcile(shelf_id="A", ndc11="N1", expected=100, counted=60,
                        inferred_units=inferred)
        assert strength[v.verdict] <= previous, (inferred, v.verdict)
        previous = strength[v.verdict]


def test_a_clean_count_is_never_shrinkage_however_much_was_inferred():
    s = ShelfDomain()
    for inferred in (0, 1, 50, 10_000):
        v = s.reconcile(shelf_id="A", ndc11="N1", expected=10, counted=10,
                        inferred_units=inferred)
        assert v.verdict == "agrees"


def test_the_unscanned_movement_is_always_disclosed():
    """Whatever the verdict, a reader must be told how much of the position was
    never observed — otherwise 'agrees' looks like a measurement."""
    v = ShelfDomain().reconcile(shelf_id="A", ndc11="N1", expected=10,
                                counted=10, inferred_units=6)
    assert any("unscanned" in c for c in v.concerns)
    assert v.inferred_units == D("6.000")


def test_only_shrinkage_reaches_a_person():
    s = ShelfDomain()
    for exp, got, inf in ((10, None, 0), (10, 10, 0), (10, 14, 0), (10, 9, 0),
                          (10, 7, 4)):
        v = s.reconcile(shelf_id="A", ndc11="N1", expected=exp, counted=got,
                        inferred_units=inf)
        assert not s.worth_investigating(v), v.verdict


# ── position and valuation ────────────────────────────────────────────────
def test_the_floor_position_sums_the_placements():
    s = ShelfDomain()
    place(s, "P1", 10, shelf="A-01")
    place(s, "P2", "2.5", shelf="A-02")
    pos = s.position()
    assert pos["units"] == D("12.500")
    assert set(pos["shelves"]) == {"A-01", "A-02"}


def test_an_unpriced_line_contributes_units_and_no_money():
    """A total that silently omits them reads as smaller than the floor is."""
    s = ShelfDomain()
    place(s, "P1", 10, ndc="PRICED")
    place(s, "P2", 10, ndc="UNPRICED")
    pos = s.position({"PRICED": D("1000")})
    assert pos["units"] == D("20.000")
    assert pos["value"] == D("10000.000")
    assert pos["unpriced_lines"] == 1


def test_an_emptied_placement_stops_counting_toward_the_floor():
    s = ShelfDomain()
    place(s, "P1", 10)
    take(s, "P1", 10)
    assert s.position()["units"] == 0


# ── the policy gate ───────────────────────────────────────────────────────
def test_the_oracle_and_the_implementation_share_a_policy_today():
    drift = ShelfDomain().policy_drift()
    assert drift == [], f"policy drift: {drift}"


def test_the_thresholds_are_the_ones_the_module_documents():
    assert INFERENCE_NOISE == D("1.0")
    assert MIN_UNITS_TO_JUDGE == D("2")


# ── exactness ─────────────────────────────────────────────────────────────
def test_fractional_units_survive_the_shelf_ledger():
    """Liquids and creams dispense fractionally, and quantity_dispensed is
    Numeric(10,3). A shelf ledger that cannot hold a half unit loses it."""
    s = ShelfDomain()
    place(s, "P1", "10.500")
    take(s, "P1", "0.250")
    assert s.placements["P1"].units == D("10.250")
    assert s.position()["units"] == D("10.250")


def test_quantisation_is_three_places_like_the_ledger():
    assert q("1.0005") == D("1.001")
    assert q(2) == D("2.000")


# ── it is a citizen of the spine ──────────────────────────────────────────
def test_shelf_is_a_domain():
    assert isinstance(ShelfDomain(), Domain)
    assert ShelfDomain().name == "shelf"


@pytest.mark.asyncio
async def test_the_harness_carries_shelf_with_stock_and_money():
    from tests.simulation.domains.money import MoneyDomain

    s, m = ShelfDomain(), MoneyDomain()
    h = Harness(db=None, start=START, domains=[s, m])
    h.fact("placed_on_shelf", subject="N1", quantity=D("20"), actor="alice",
           payload={"placement_id": "P1", "shelf_id": "A-03", "lot_id": "L1"})
    h.fact("priced", subject="RX1", payload={
        "rx": "RX1", "plan": "tamin", "setting": "outpatient",
        "technical_fee": "0",
        "lines": [{"irc": "N1", "quantity": "2", "consumer_price": "100000",
                   "reference_price": None}]})
    h.fact("dispensed", subject="N1", quantity=D("2"), payload={"rx": "RX1"})
    h.fact("taken_off_shelf", subject="N1", quantity=D("2"),
           payload={"placement_id": "P1", "basis": INFERRED})
    assert await h.check("placed, priced, dispensed, taken") == []
    assert s.placements["P1"].units == D("18.000")


@pytest.mark.asyncio
async def test_a_shelf_fault_and_a_money_fault_are_both_reported():
    from tests.simulation.domains.money import MoneyDomain

    s, m = ShelfDomain(), MoneyDomain()
    h = Harness(db=None, start=START, domains=[s, m])
    h.fact("taken_off_shelf", subject="N1", quantity=D("2"),
           payload={"placement_id": "GHOST", "basis": INFERRED})
    h.fact("dispensed", subject="N1", quantity=D("2"),
           payload={"rx": "RX-UNPRICED"})
    found = await h.check("two faults")
    assert {f.domain for f in found} == {"shelf", "money"}
