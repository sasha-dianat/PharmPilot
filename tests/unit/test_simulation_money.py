"""The money oracle — what each party owes, re-derived from the regulation.

These pin the properties that make the oracle worth having: it must be able to
catch the defects the pricing engine has actually had (an insurer billed above
the sale price, حق فنی split with an insurer that pays none of it, a line that
stops adding up at a coarse rounding unit), and it must distinguish "the engine
is wrong" from "the tariff moved" — because those two sentences go to different
people.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tests.simulation.domains.money import (
    FRANCHISE, INSURER_PAYS_ANY_TECHNICAL_FEE, Line, MoneyDomain)
from tests.simulation.spine.clock import SimClock
from tests.simulation.spine.domain import Domain
from tests.simulation.spine.facts import Fact
from tests.simulation.spine.harness import Harness

START = date(2026, 1, 1)
D = Decimal


def line(consumer, ref=None, qty="1", category="drug", covered=True, irc="IRC1"):
    return Line(irc=irc, quantity=D(qty), consumer_price=D(consumer),
                reference_price=None if ref is None else D(ref),
                category=category, is_covered=covered)


def fact(kind, subject, **kw):
    return Fact(seq=1, at=SimClock(start=START).now(), day=0, kind=kind,
                subject=subject, **kw)


# ── the arithmetic ────────────────────────────────────────────────────────
def test_an_ordinary_covered_line_splits_seventy_thirty():
    m = MoneyDomain()
    b = m.price_line(line("100000", qty="3"), "tamin", "outpatient")
    assert b["gross"] == D("300000")
    assert b["patient_share"] == D("90000")     # 30%
    assert b["insurer"] == D("210000")          # 70%
    assert b["differential"] == 0


def test_conservation_holds_on_every_line():
    m = MoneyDomain()
    for consumer, ref, qty in (("3324.5", "1662.25", "30"), ("100000", None, "1"),
                               ("17", "3", "7"), ("999999", "1", "13")):
        b = m.price_line(line(consumer, ref, qty), "salamat", "outpatient")
        assert b["insurer"] + b["patient_total"] == b["gross"] + b["vat"]
        assert b["covered_base"] + b["differential"] == b["gross"]


def test_conservation_survives_a_coarse_rounding_unit():
    """The config invites a pharmacy to round to the nearest 1,000 Rial. A line
    that adds up at whole Rial and not at 1,000 is the defect that rounding the
    differential independently produced."""
    m = MoneyDomain(rounding_unit=D("1000"))
    b = m.price_line(line("3324.5", "1662.25", "30"), "tamin", "outpatient")
    assert b["covered_base"] + b["differential"] == b["gross"]
    assert b["insurer"] + b["patient_total"] == b["gross"] + b["vat"]


def test_the_patient_pays_the_difference_above_the_reference():
    m = MoneyDomain()
    b = m.price_line(line("50000", "30000", qty="2"), "tamin", "outpatient")
    assert b["covered_base"] == D("60000")
    assert b["differential"] == D("40000")      # (50000-30000) × 2
    assert b["patient_total"] == D("18000") + D("40000")


def test_an_insurer_is_never_billed_above_the_sale_price():
    """Ketotifen at 5,750 Rial against a salamat reference of 19,663 charged the
    insurer 13,764 and the patient 5,899 — 19,663 collected on a 5,750 item."""
    m = MoneyDomain()
    b = m.price_line(line("5750", "19663"), "salamat", "outpatient")
    assert b["covered_base"] == D("5750")
    assert b["insurer"] + b["patient_total"] == b["gross"]
    assert b["insurer"] <= b["gross"]


def test_a_covered_base_above_gross_is_reported_not_absorbed():
    """The cap above makes this unreachable through price_line, so the invariant
    is checked directly — an oracle whose invariant can only pass because its own
    code prevents the input is not checking anything."""
    m = MoneyDomain()
    bad = {"irc": "X", "covered": True, "gross": D("100"),
           "covered_base": D("500"), "insurer": D("350"),
           "patient_share": D("150"), "differential": D("0"),
           "vat": D("0"), "patient_total": D("150")}
    q = type("Q", (), {"lines": [bad], "fee_insurer": D("0"), "insurer": D("350"),
                       "patient": D("150"), "gross": D("100"), "vat": D("0"),
                       "fee_total": D("0")})()
    found = m._check_invariants("RX1", q)
    assert any("exceeds the sale price" in f for f in found)
    assert any("conservation broken" in f for f in found)


# ── حق فنی ────────────────────────────────────────────────────────────────
def test_the_whole_technical_fee_is_the_patients():
    m = MoneyDomain()
    q = m.quote("RX1", [line("100000")], "tamin", "outpatient", D("727000"))
    assert q.fee_patient == D("727000")
    assert q.fee_insurer == 0
    assert INSURER_PAYS_ANY_TECHNICAL_FEE is False


def test_an_engine_that_splits_the_fee_is_reported():
    """The defect commit d68acfb corrected: the patient was charged 218,100 and
    an insurer billed 508,900 that it never pays, per prescription."""
    m = MoneyDomain()
    m.observe(fact("priced", "RX1", payload={
        "rx": "RX1", "plan": "tamin", "setting": "outpatient",
        "technical_fee": "727000",
        "lines": [{"irc": "A", "quantity": "1", "consumer_price": "100000",
                   "reference_price": None}],
        "engine": {"fee_insurer": "508900"}}))
    assert any("the whole fee is the patient" in d for d in m._disagreements)


# ── plans ─────────────────────────────────────────────────────────────────
def test_armed_forces_outpatient_drug_franchise_is_fifteen_percent():
    m = MoneyDomain()
    b = m.price_line(line("100000"), "armed_forces", "outpatient")
    assert b["patient_share"] == D("15000")
    assert FRANCHISE[("armed_forces", "outpatient")] == D("0.15")


def test_inpatient_is_ten_percent_for_the_basic_schemes():
    m = MoneyDomain()
    assert m.price_line(line("100000"), "tamin", "inpatient")["patient_share"] == D("10000")
    assert m.price_line(line("100000"), "salamat", "inpatient")["patient_share"] == D("10000")


def test_self_pay_pays_everything_and_the_insurer_nothing():
    m = MoneyDomain()
    b = m.price_line(line("100000"), "cash", "outpatient")
    assert b["patient_total"] == D("100000") and b["insurer"] == 0


def test_an_unknown_plan_is_an_oracle_fault_not_a_silent_cash_fallback():
    """A typo'd plan code that quietly charges the patient 100% is a money defect
    wearing a configuration defect's clothes."""
    m = MoneyDomain()
    m.price_line(line("100000"), "tamiin", "outpatient")
    assert any("no franchise known" in v for v in m.violations())


# ── VAT and categories ────────────────────────────────────────────────────
def test_drugs_carry_no_vat():
    m = MoneyDomain()
    assert m.price_line(line("100000", category="drug"), "tamin", "outpatient")["vat"] == 0


def test_a_cosmetic_is_uninsurable_and_carries_ten_percent():
    m = MoneyDomain()
    b = m.price_line(line("100000", category="cosmetic", covered=False),
                     "tamin", "outpatient")
    assert b["covered"] is False
    assert b["insurer"] == 0
    assert b["vat"] == D("10000")
    assert b["patient_total"] == D("110000")


def test_a_non_covered_drug_is_billed_entirely_to_the_patient():
    m = MoneyDomain()
    b = m.price_line(line("100000", covered=False), "tamin", "outpatient")
    assert b["insurer"] == 0 and b["patient_share"] == D("100000")


# ── comparing against the engine ──────────────────────────────────────────
def test_a_disagreement_names_both_numbers_and_the_difference():
    m = MoneyDomain()
    m.observe(fact("priced", "RX1", payload={
        "rx": "RX1", "plan": "tamin", "setting": "outpatient", "technical_fee": "0",
        "lines": [{"irc": "A", "quantity": "1", "consumer_price": "100000",
                   "reference_price": None}],
        "engine": {"insurer": "70000", "patient": "40000"}}))
    d = " ".join(m._disagreements)
    assert "engine says patient 40000" in d and "regulation gives 30000" in d


def test_agreement_produces_no_finding():
    m = MoneyDomain()
    m.observe(fact("priced", "RX1", payload={
        "rx": "RX1", "plan": "tamin", "setting": "outpatient", "technical_fee": "727000",
        "lines": [{"irc": "A", "quantity": "1", "consumer_price": "100000",
                   "reference_price": None}],
        "engine": {"gross": "100000", "insurer": "70000",
                   "patient": "757000", "differential": "0", "vat": "0",
                   "fee_insurer": "0"}}))
    assert m._disagreements == []


# ── the cross-domain rule the spine exists for ────────────────────────────
@pytest.mark.asyncio
async def test_units_that_left_the_shelf_but_were_never_priced_are_reported():
    m = MoneyDomain()
    m.observe(fact("dispensed", "N1", quantity=D("30"), payload={"rx": "RX9"}))
    found = await m.check(None)
    assert any("never priced" in f and "nothing was owed" in f for f in found)


@pytest.mark.asyncio
async def test_collecting_less_than_was_owed_is_reported():
    m = MoneyDomain()
    m.observe(fact("priced", "RX1", payload={
        "rx": "RX1", "plan": "tamin", "setting": "outpatient", "technical_fee": "0",
        "lines": [{"irc": "A", "quantity": "1", "consumer_price": "100000",
                   "reference_price": None}]}))
    m.observe(fact("paid", "RX1", money=D("10000"), payload={"rx": "RX1"}))
    found = await m.check(None)
    assert any("collected 10000, the patient owed 30000" in f for f in found)


@pytest.mark.asyncio
async def test_money_collected_against_an_unpriced_prescription_is_reported():
    m = MoneyDomain()
    m.observe(fact("paid", "RX7", money=D("500"), payload={"rx": "RX7"}))
    found = await m.check(None)
    assert any("never priced" in f for f in found)


@pytest.mark.asyncio
async def test_billing_the_insurer_a_different_figure_is_reported():
    m = MoneyDomain()
    m.observe(fact("priced", "RX1", payload={
        "rx": "RX1", "plan": "tamin", "setting": "outpatient", "technical_fee": "0",
        "lines": [{"irc": "A", "quantity": "1", "consumer_price": "100000",
                   "reference_price": None}]}))
    m.observe(fact("adjudicated", "RX1", money=D("99999"), payload={"rx": "RX1"}))
    found = await m.check(None)
    assert any("billed the insurer 99999" in f and "owed 70000" in f for f in found)


@pytest.mark.asyncio
async def test_a_priced_and_paid_prescription_is_clean():
    m = MoneyDomain()
    m.observe(fact("priced", "RX1", payload={
        "rx": "RX1", "plan": "tamin", "setting": "outpatient", "technical_fee": "727000",
        "lines": [{"irc": "A", "quantity": "1", "consumer_price": "100000",
                   "reference_price": None}]}))
    m.observe(fact("dispensed", "N1", quantity=D("1"), payload={"rx": "RX1"}))
    m.observe(fact("paid", "RX1", money=D("757000"), payload={"rx": "RX1"}))
    m.observe(fact("adjudicated", "RX1", money=D("70000"), payload={"rx": "RX1"}))
    assert await m.check(None) == []


# ── it is a citizen of the spine ──────────────────────────────────────────
def test_money_is_a_domain():
    assert isinstance(MoneyDomain(), Domain)
    assert MoneyDomain().name == "money"


@pytest.mark.asyncio
async def test_the_harness_carries_money_alongside_another_domain():
    m = MoneyDomain()
    h = Harness(db=None, start=START, domains=[m])
    h.fact("priced", subject="RX1", payload={
        "rx": "RX1", "plan": "tamin", "setting": "outpatient", "technical_fee": "0",
        "lines": [{"irc": "A", "quantity": "1", "consumer_price": "100000",
                   "reference_price": None}]})
    h.fact("dispensed", subject="N1", quantity=D("1"), payload={"rx": "RX1"})
    assert await h.check("priced then dispensed") == []
    assert h.report.ok


# ── the tariff gate ───────────────────────────────────────────────────────
def test_the_oracle_agrees_with_the_deployments_tariffs_today():
    """If this fails a tariff moved. That is a different conversation from the
    engine computing a wrong number, and it goes to a different person."""
    drift = MoneyDomain().tariff_drift()
    assert drift == [], f"tariff drift: {drift}"


def test_tariff_drift_is_not_reported_as_an_engine_defect():
    """Drift must never leak into check(); a stale oracle would otherwise read
    as a broken platform on every single line."""
    m = MoneyDomain()
    assert m.violations() == []
    assert m._disagreements == []
