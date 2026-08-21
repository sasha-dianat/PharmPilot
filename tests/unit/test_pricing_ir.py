"""Iranian pharmacy pricing/adjudication engine — worked examples.

All monetary values are in Rial (Decimal). Numbers in config are VERIFY-tagged
tariffs; these tests pin the *calculation logic*, not the exact tariffs.
"""
from decimal import Decimal

from services.core.pricing_ir.engine import (
    DrugPrice, LineInput, ItemCategory, price_line, price_prescription,
    resolve_consumer_price,
)
from services.core.pricing_ir.config import InsurerPlan


# A simple test plan: 30% patient outpatient, 10% inpatient, covers tech fee 70/30.
PLAN = InsurerPlan(
    code="test", name_fa="آزمایشی",
    outpatient_patient_share=Decimal("0.30"),
    inpatient_patient_share=Decimal("0.10"),
    covers_technical_fee=True,
    technical_fee_patient_share=Decimal("0.30"),
)
TECH_FEE = Decimal("200000")


def _drug(price, ref=None, category=ItemCategory.DRUG, covered=True, vat="0"):
    return DrugPrice(irc="IRC-1", name="drug", consumer_price=Decimal(str(price)),
                     insurer_reference_price=None if ref is None else Decimal(str(ref)),
                     category=category, is_covered=covered, vat_rate=Decimal(vat))


def test_covered_drug_outpatient_franchise():
    # price 10,000 × 10 = 100,000; 30% patient → 30,000 patient / 70,000 insurer
    lines = [LineInput(drug=_drug(10000), quantity=Decimal("10"))]
    p = price_prescription(lines, PLAN, technical_fee=Decimal("0"), setting="outpatient")
    ln = p.lines[0]
    assert ln.gross == Decimal("100000")
    assert ln.insurer_share == Decimal("70000")
    assert ln.patient_share == Decimal("30000")
    assert ln.differential == Decimal("0")
    assert ln.patient_total == Decimal("30000")
    assert p.totals.insurer == Decimal("70000")
    assert p.totals.patient == Decimal("30000")
    assert p.totals.gross == Decimal("100000")


def test_reference_price_differential():
    # consumer 12,000 but insurer reimburses against 10,000 reference; qty 10.
    # covered_base = 100,000 → insurer 70,000, copay 30,000.
    # differential = (12,000-10,000)×10 = 20,000 → patient_total = 50,000.
    lines = [LineInput(drug=_drug(12000, ref=10000), quantity=Decimal("10"))]
    p = price_prescription(lines, PLAN, technical_fee=Decimal("0"))
    ln = p.lines[0]
    assert ln.gross == Decimal("120000")
    assert ln.insurer_share == Decimal("70000")
    assert ln.patient_share == Decimal("30000")
    assert ln.differential == Decimal("20000")
    assert ln.patient_total == Decimal("50000")
    # insurer + patient == gross (conservation)
    assert ln.insurer_share + ln.patient_total == ln.gross


def test_inpatient_uses_lower_franchise():
    lines = [LineInput(drug=_drug(10000), quantity=Decimal("10"))]
    p = price_prescription(lines, PLAN, technical_fee=Decimal("0"), setting="inpatient")
    assert p.lines[0].patient_share == Decimal("10000")   # 10%
    assert p.lines[0].insurer_share == Decimal("90000")


def test_non_covered_supplement_full_patient():
    lines = [LineInput(drug=_drug(50000, category=ItemCategory.SUPPLEMENT, covered=False),
                       quantity=Decimal("2"))]
    p = price_prescription(lines, PLAN, technical_fee=Decimal("0"))
    ln = p.lines[0]
    assert ln.insurer_share == Decimal("0")
    assert ln.patient_total == Decimal("100000")
    assert ln.covered is False


def test_vat_on_cosmetic():
    # cosmetic, not covered, 9% VAT: gross 100,000 → vat 9,000 → patient 109,000
    lines = [LineInput(drug=_drug(100000, category=ItemCategory.COSMETIC, covered=False, vat="0.09"),
                       quantity=Decimal("1"))]
    p = price_prescription(lines, PLAN, technical_fee=Decimal("0"))
    ln = p.lines[0]
    assert ln.vat == Decimal("9000")
    assert ln.patient_total == Decimal("109000")
    assert p.totals.vat == Decimal("9000")


def test_technical_fee_split_between_insurer_and_patient():
    lines = [LineInput(drug=_drug(10000), quantity=Decimal("10"))]
    p = price_prescription(lines, PLAN, technical_fee=TECH_FEE)
    # tech fee 200,000; patient 30% = 60,000, insurer 140,000
    assert p.technical_fee.patient == Decimal("60000")
    assert p.technical_fee.insurer == Decimal("140000")
    assert p.totals.patient == Decimal("30000") + Decimal("60000")
    assert p.totals.insurer == Decimal("70000") + Decimal("140000")


def test_pricing_intelligence_takes_higher_of_invoice_and_announced():
    # High-inflation rule: sell at the higher of announced (NFI) vs latest invoice.
    assert resolve_consumer_price(Decimal("10000"), Decimal("13000")) == Decimal("13000")
    assert resolve_consumer_price(Decimal("15000"), Decimal("13000")) == Decimal("15000")
    assert resolve_consumer_price(None, Decimal("9000")) == Decimal("9000")
    assert resolve_consumer_price(Decimal("9000"), None) == Decimal("9000")
    assert resolve_consumer_price(None, None) == Decimal("0")


def test_mixed_basket_conservation():
    # covered drug + non-covered supplement; grand total == gross + vat + tech fee
    lines = [
        LineInput(drug=_drug(10000), quantity=Decimal("10")),                         # covered
        LineInput(drug=_drug(50000, category=ItemCategory.SUPPLEMENT, covered=False), quantity=Decimal("1")),
    ]
    p = price_prescription(lines, PLAN, technical_fee=TECH_FEE)
    assert p.totals.insurer + p.totals.patient == p.totals.gross + p.totals.vat + TECH_FEE
    assert p.totals.grand_total == p.totals.insurer + p.totals.patient


# ── the covered base can never exceed what the item actually costs ──────────
def test_reference_above_the_sale_price_does_not_over_bill_the_insurer():
    """Iranian prices rise continuously while a published reference stays frozen
    until the formulary is reissued, so reference < consumer is the ordinary
    case and yields مابه‌التفاوت. The reverse happens too — our catalog price
    lags the market, or the insurer's reference is simply higher — and uncapped
    it billed the insurer ABOVE the sale price.

    Measured on live data 2026-08-09: ketotifen 1 mg at 5,750 rial against a
    salamat reference of 19,663 charged the insurer 13,764 and the patient
    5,899, collecting 19,663 on a 5,750 item. 4,175 tamin and 2,948 salamat
    products sat in that state.
    """
    plan = InsurerPlan(code="salamat", name_fa="سلامت",
                       outpatient_patient_share=Decimal("0.30"),
                       inpatient_patient_share=Decimal("0.10"),
                       covers_technical_fee=True,
                       technical_fee_patient_share=Decimal("0.30"))
    d = DrugPrice(irc="K", name="ketotifen", consumer_price=Decimal("5750"),
                  insurer_reference_price=Decimal("19663"), is_covered=True)
    b = price_line(LineInput(drug=d, quantity=Decimal("1"), setting="outpatient"),
                   plan, setting="outpatient")

    assert b.covered_base == Decimal("5750")          # capped at the sale price
    assert b.differential == Decimal("0")             # nothing to add
    assert b.insurer_share + b.patient_total == b.gross + b.vat   # the invariant
    assert b.insurer_share < Decimal("5750")


def test_a_reference_below_the_price_still_yields_the_differential():
    """The cap must not touch the ordinary direction."""
    plan = InsurerPlan(code="salamat", name_fa="سلامت",
                       outpatient_patient_share=Decimal("0.30"),
                       inpatient_patient_share=Decimal("0.10"),
                       covers_technical_fee=True,
                       technical_fee_patient_share=Decimal("0.30"))
    d = DrugPrice(irc="Z", name="zaditen", consumer_price=Decimal("29200"),
                  insurer_reference_price=Decimal("19663"), is_covered=True)
    b = price_line(LineInput(drug=d, quantity=Decimal("1"), setting="outpatient"),
                   plan, setting="outpatient")
    assert b.covered_base == Decimal("19663")
    assert b.differential == Decimal("29200") - Decimal("19663")
    assert b.insurer_share + b.patient_total == b.gross + b.vat


# ── the money must add up, at every rounding unit ───────────────────────────
#
# The engine's docstring states `insurer_share + patient_total == gross + vat`.
# It did not hold. `covered_base` and `differential` were each rounded on their
# own, and round(a) + round(b) ≠ round(a + b): a randomised sweep of 48,000
# lines found ~4% breaking at the DEFAULT whole-Rial unit and ~7% at the
# 1,000-Rial unit that config.py invites a pharmacy to choose. Money appeared
# and vanished a Rial at a time, on a receipt a patient is handed.
#
# The fix derives the differential — `gross − covered_base` — so the identity
# holds by construction rather than by luck of the inputs.
import contextlib
import itertools

from services.core.pricing_ir import config as _cfg


@contextlib.contextmanager
def rounding_unit(unit):
    """The engine reads the unit from the config module at call time, which is
    the only reason this context manager works. It used to be import-bound, so
    setting it here changed nothing and a coarse-rounding deployment silently
    kept rounding to the whole Rial."""
    before = _cfg.ROUNDING_UNIT_RIAL
    _cfg.ROUNDING_UNIT_RIAL = Decimal(str(unit))
    try:
        yield
    finally:
        _cfg.ROUNDING_UNIT_RIAL = before


def test_the_rounding_unit_is_read_not_baked_in():
    d = _drug(15400, ref=10600)
    with rounding_unit(1000):
        assert price_line(LineInput(drug=d, quantity=Decimal("1")),
                          PLAN, setting="outpatient").gross == Decimal("15000")
    assert price_line(LineInput(drug=d, quantity=Decimal("1")),
                      PLAN, setting="outpatient").gross == Decimal("15400")


def test_the_two_lines_that_broke_conservation():
    """Regression pins, both found by sweep rather than by inspection.

    The first is reachable today: `sell_price` and `manual_shelf_price` are both
    Numeric(12,4), so a sub-Rial shelf price is ordinary, and 30 units of it is
    an ordinary quantity.
    """
    with rounding_unit(1):                       # was out by 1 Rial
        b = price_line(LineInput(drug=_drug("3324.5", ref="1662.25"),
                                 quantity=Decimal("30")), PLAN, setting="outpatient")
        assert b.insurer_share + b.patient_total == b.gross + b.vat
        assert b.covered_base + b.differential == b.gross

    with rounding_unit(1000):                    # was out by 1,000 Rial
        b = price_line(LineInput(drug=_drug(15400, ref=10600),
                                 quantity=Decimal("1")), PLAN, setting="outpatient")
        assert b.insurer_share + b.patient_total == b.gross + b.vat
        assert b.covered_base + b.differential == b.gross


def test_conservation_holds_across_the_parameter_space():
    """Exhaustive rather than illustrative: every combination of price, reference
    ratio, quantity, rounding unit and VAT below must balance to the Rial."""
    prices = ["15400", "24500", "120500", "3324.5", "2770.25", "1000.0001", "7"]
    ratios = ["0.2", "0.5", "0.7", "0.9", "1.0", "1.3"]
    qtys = ["0", "1", "2", "0.5", "1.5", "2.5", "30", "0.333"]
    units = [1, 10, 100, 1000]
    vats = ["0", "0.10"]

    checked = 0
    for unit in units:
        with rounding_unit(unit):
            for price, ratio, qty, vat in itertools.product(prices, ratios, qtys, vats):
                cp = Decimal(price)
                d = _drug(cp, ref=cp * Decimal(ratio), vat=vat)
                for setting in ("outpatient", "inpatient"):
                    b = price_line(LineInput(drug=d, quantity=Decimal(qty)),
                                   PLAN, setting=setting)
                    assert b.insurer_share + b.patient_total == b.gross + b.vat, (
                        f"unit={unit} price={price} ratio={ratio} qty={qty} "
                        f"vat={vat} setting={setting}")
                    assert b.covered_base + b.differential == b.gross
                    assert b.differential >= 0 and b.insurer_share >= 0
                    checked += 1
    assert checked == len(prices) * len(ratios) * len(qtys) * len(units) * len(vats) * 2


def test_the_prescription_total_is_exactly_what_was_charged():
    """grand_total == gross + vat + حق فنی, with no rounding gap opening up
    between the lines and the sum of them."""
    lines = [
        LineInput(drug=_drug("3324.5", ref="1662.25"), quantity=Decimal("30")),
        LineInput(drug=_drug(120500, ref=60250), quantity=Decimal("1.5")),
        LineInput(drug=_drug(9900, category=ItemCategory.COSMETIC,
                             covered=False, vat="0.10"), quantity=Decimal("2")),
    ]
    for unit in (1, 10, 100, 1000):
        with rounding_unit(unit):
            p = price_prescription(lines, PLAN, technical_fee=TECH_FEE,
                                   setting="outpatient")
            assert p.totals.grand_total == (p.totals.gross + p.totals.vat
                                            + p.technical_fee.total), f"unit={unit}"
            assert (p.technical_fee.insurer + p.technical_fee.patient
                    == p.technical_fee.total)


def test_a_negative_quantity_is_refused_rather_than_priced():
    """The identity assumes ref×qty ≤ consumer×qty, which inverts below zero and
    would hand back a negative insurer share. A return is a different operation,
    not a line with a minus sign."""
    import pytest
    with pytest.raises(ValueError, match="negative"):
        price_line(LineInput(drug=_drug(10000), quantity=Decimal("-1")),
                   PLAN, setting="outpatient")


def test_a_float_quantity_does_not_carry_binary_noise():
    """`Decimal(0.1)` is 0.1000000000000000055511151231257827…; every product
    below it inherits that. The router stringifies, but the engine should not
    depend on every caller having remembered to."""
    b = price_line(LineInput(drug=_drug(10000), quantity=0.1),
                   PLAN, setting="outpatient")
    assert b.quantity == Decimal("0.1")
    assert b.gross == Decimal("1000")


def test_an_uncovered_line_bills_the_patient_the_whole_gross():
    """The other half of the rule, and the one that made a verification script
    report a defect that was not there: on an uncovered line `covered_base` and
    `differential` are both zero BY DESIGN, so `covered_base + differential ==
    gross` does not apply. What must hold is that the insurer pays nothing and
    the patient pays all of it."""
    for unit in (1, 1000):
        with rounding_unit(unit):
            b = price_line(LineInput(drug=_drug(9900, category=ItemCategory.COSMETIC,
                                                covered=False, vat="0.10"),
                                     quantity=Decimal("2")), PLAN, setting="outpatient")
            assert b.insurer_share == 0
            assert b.patient_share == b.gross
            assert b.covered_base == 0 and b.differential == 0
            assert b.insurer_share + b.patient_total == b.gross + b.vat


def test_the_quote_line_exposes_the_base_the_insurer_recognised():
    """A line that shows مابه‌التفاوت without the base it was measured against
    cannot be checked by the person paying it. `covered_base + differential ==
    gross` is what makes the line auditable at the counter."""
    import inspect
    from services.platform.routers import pricing
    src = inspect.getsource(pricing.quote)
    assert '"covered_base"' in src
