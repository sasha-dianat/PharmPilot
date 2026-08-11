"""Iranian pharmacy pricing & adjudication calculator (pure, deterministic).

Per line item it computes, in Rial:
  gross            = consumer_price × qty                     (قیمت کل)
  covered_base     = insurer_reference_price × qty            (مبنای تعهد بیمه)
  insurer_share    = covered_base × (1 − franchise)           (سهم بیمه)
  patient_share    = covered_base × franchise                 (فرانشیز بیمار)
  differential     = max(0, consumer − reference) × qty       (مابه‌التفاوت)
  vat              = (non-covered gross) × vat_rate
  patient_total    = patient_share + differential + vat

Non-covered items (supplements, cosmetics, non-formulary) get insurer_share = 0
and the patient pays the full gross (+VAT). Conservation holds per line:
  insurer_share + patient_total == gross + vat.

The per-prescription technical fee (حق فنی) is split per the insurer plan.
All tariffs come from `config.py`; this module holds only the arithmetic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum

from .config import InsurerPlan, ROUNDING_UNIT_RIAL


class ItemCategory(str, Enum):
    DRUG = "drug"             # registered medicine (IRC) — insurer-eligible if in formulary
    OTC = "otc"              # OTC medicine
    SUPPLEMENT = "supplement"  # مکمل
    COSMETIC = "cosmetic"    # آرایشی-بهداشتی


def resolve_consumer_price(announced_price: Decimal | None,
                           last_invoice_price: Decimal | None) -> Decimal:
    """Pricing intelligence (high-inflation market): the authority-announced
    price (NFI / irc.fda.gov.ir) lags the real replacement cost, so the latest
    distributor-invoice price can be higher. Sell at the HIGHER of the two so the
    pharmacy never dispenses below its actual acquisition cost. Returns 0 when
    neither is known.

    NOTE: this takes the invoice price literally per the agreed rule. If a margin
    should be applied to the invoice-derived price, do it before calling this.
    """
    candidates = [p for p in (announced_price, last_invoice_price) if p is not None and p > 0]
    return max(candidates) if candidates else Decimal("0")


def _round(amount: Decimal) -> Decimal:
    """Round to the configured Rial unit (default nearest whole Rial)."""
    if ROUNDING_UNIT_RIAL == Decimal("1"):
        return amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return (amount / ROUNDING_UNIT_RIAL).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * ROUNDING_UNIT_RIAL


@dataclass(frozen=True)
class DrugPrice:
    """Pricing facts for one dispensable item (per unit)."""
    irc: str
    name: str
    consumer_price: Decimal                       # قیمت مصرف‌کننده per unit
    insurer_reference_price: Decimal | None = None  # قیمت تعهد per unit; None ⇒ == consumer
    category: ItemCategory = ItemCategory.DRUG
    is_covered: bool = True                        # in formulary / insurer-eligible
    vat_rate: Decimal = Decimal("0")


@dataclass
class LineInput:
    drug: DrugPrice
    quantity: Decimal
    setting: str | None = None   # "outpatient" | "inpatient"; None ⇒ use prescription default


@dataclass
class LineBreakdown:
    irc: str
    name: str
    quantity: Decimal
    unit_price: Decimal
    gross: Decimal
    covered: bool
    covered_base: Decimal
    insurer_share: Decimal
    patient_share: Decimal
    differential: Decimal
    vat: Decimal
    patient_total: Decimal


@dataclass
class FeeSplit:
    total: Decimal
    insurer: Decimal
    patient: Decimal


@dataclass
class Totals:
    gross: Decimal
    insurer: Decimal
    patient: Decimal
    differential: Decimal
    vat: Decimal
    grand_total: Decimal


@dataclass
class PrescriptionPricing:
    lines: list[LineBreakdown]
    technical_fee: FeeSplit
    totals: Totals
    plan_code: str
    setting: str
    notes: list[str] = field(default_factory=list)


def _franchise(plan: InsurerPlan, setting: str) -> Decimal:
    return (plan.inpatient_patient_share if setting == "inpatient"
            else plan.outpatient_patient_share)


def price_line(line: LineInput, plan: InsurerPlan, *, setting: str) -> LineBreakdown:
    d = line.drug
    qty = Decimal(line.quantity)
    eff_setting = line.setting or setting
    gross = _round(d.consumer_price * qty)

    eligible = d.is_covered and d.category in (ItemCategory.DRUG, ItemCategory.OTC)
    if not eligible:
        # Patient pays the full price; VAT may apply (cosmetics/supplements).
        vat = _round(gross * d.vat_rate)
        return LineBreakdown(
            irc=d.irc, name=d.name, quantity=qty, unit_price=d.consumer_price,
            gross=gross, covered=False, covered_base=Decimal("0"),
            insurer_share=Decimal("0"), patient_share=gross, differential=Decimal("0"),
            vat=vat, patient_total=_round(gross + vat),
        )

    # The covered base is the reference — but never MORE than the item actually
    # costs. Iranian prices rise continuously while a published reference stays
    # frozen until the formulary is reissued, so reference < consumer is the
    # ordinary case and produces مابه‌التفاوت. The reverse happens too — a stale
    # or mistaken catalog price below the reference — and uncapped it billed the
    # insurer above the sale price: ketotifen at 5,750 rial against a salamat
    # reference of 19,663 charged the insurer 13,764 and the patient 5,899, so
    # 19,663 was collected on a 5,750 item and the invariant this module
    # documents (insurer_share + patient_total == gross + vat) was violated by
    # 13,913 rial on a single line.
    # The caller must hand us a PER-UNIT reference. Where the insurer quoted a
    # pack, `coverage_import` divides it down and records the fact; the router
    # passes the divided figure. This assert-by-construction is the machine half
    # of the two-flag rule — see `_mark_reference_basis`.
    ref_unit = d.insurer_reference_price if d.insurer_reference_price is not None else d.consumer_price
    ref_unit = min(ref_unit, d.consumer_price)
    covered_base = _round(ref_unit * qty)
    differential = _round(max(Decimal("0"), d.consumer_price - ref_unit) * qty)
    franchise = _franchise(plan, eff_setting)
    patient_share = _round(covered_base * franchise)
    insurer_share = _round(covered_base - patient_share)   # avoids 1-Rial rounding drift
    vat = _round(gross * d.vat_rate)                        # normally 0 for drugs
    patient_total = _round(patient_share + differential + vat)
    return LineBreakdown(
        irc=d.irc, name=d.name, quantity=qty, unit_price=d.consumer_price,
        gross=gross, covered=True, covered_base=covered_base,
        insurer_share=insurer_share, patient_share=patient_share,
        differential=differential, vat=vat, patient_total=patient_total,
    )


def price_prescription(lines: list[LineInput], plan: InsurerPlan, *,
                       technical_fee: Decimal = Decimal("0"),
                       setting: str = "outpatient") -> PrescriptionPricing:
    breakdowns = [price_line(ln, plan, setting=setting) for ln in lines]

    # حق فنی split
    fee_total = _round(Decimal(technical_fee))
    if plan.covers_technical_fee and fee_total > 0:
        fee_patient = _round(fee_total * plan.technical_fee_patient_share)
        fee_insurer = _round(fee_total - fee_patient)
    else:
        fee_patient, fee_insurer = fee_total, Decimal("0")
    fee = FeeSplit(total=fee_total, insurer=fee_insurer, patient=fee_patient)

    gross = sum((b.gross for b in breakdowns), Decimal("0"))
    vat = sum((b.vat for b in breakdowns), Decimal("0"))
    diff = sum((b.differential for b in breakdowns), Decimal("0"))
    insurer = sum((b.insurer_share for b in breakdowns), Decimal("0")) + fee.insurer
    patient = sum((b.patient_total for b in breakdowns), Decimal("0")) + fee.patient

    notes: list[str] = []
    if any(b.differential > 0 for b in breakdowns):
        notes.append("شامل مابه‌التفاوت قیمت — patient pays price above the insurer reference price.")
    if any(not b.covered for b in breakdowns):
        notes.append("شامل اقلام تحت پوشش نبودن بیمه (مکمل/آرایشی) — billed fully to the patient.")

    totals = Totals(
        gross=gross, insurer=insurer, patient=patient, differential=diff, vat=vat,
        grand_total=insurer + patient,
    )
    return PrescriptionPricing(
        lines=breakdowns, technical_fee=fee, totals=totals,
        plan_code=plan.code, setting=setting, notes=notes,
    )
