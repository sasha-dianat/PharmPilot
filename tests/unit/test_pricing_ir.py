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
