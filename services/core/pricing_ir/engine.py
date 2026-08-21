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

from . import config as _config
from .config import InsurerPlan


class ItemCategory(str, Enum):
    DRUG = "drug"             # registered medicine (IRC) — insurer-eligible if in formulary
    OTC = "otc"              # OTC medicine
    SUPPLEMENT = "supplement"  # مکمل
    COSMETIC = "cosmetic"    # آرایشی-بهداشتی


def resolve_consumer_price(announced_price: Decimal | None,
                           last_invoice_price: Decimal | None) -> Decimal:
    """CATALOG PRICE PROPOSALS ONLY — this is no longer what the customer pays.

    Callers are `drug_catalog.pricing_sync` and `drug_catalog.schema`, which
    propose a catalog figure from what the authority announced and what the last
    invoice showed, taking the higher because the announced price lags in a
    market where replacement cost only rises.

    What the counter charges does NOT come through here. The shelf price is the
    highest sellable batch price versus the owner's own — `inventory.shelf_price`
    — and a quote resolves it via `shelf_prices_for_ircs`, marking `price_source`
    when it has to fall back. The owner was explicit that NFI's number is not
    authoritative for the patient's remainder, so do not reintroduce this
    function into the money path.

    Returns 0 when neither figure is known.
    """
    candidates = [p for p in (announced_price, last_invoice_price) if p is not None and p > 0]
    return max(candidates) if candidates else Decimal("0")


def _round(amount: Decimal) -> Decimal:
    """Round to the configured Rial unit (default nearest whole Rial).

    Read from the config MODULE at call time, not bound at import. `from .config
    import ROUNDING_UNIT_RIAL` copies the value once, so a deployment that set a
    coarser unit — the thing config.py explicitly invites — kept rounding to the
    whole Rial until the process restarted, and a test that set it saw no effect
    at all. An engine documented as config-driven has to actually read the config.
    """
    unit = _config.ROUNDING_UNIT_RIAL
    if unit == Decimal("1"):
        return amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return (amount / unit).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * unit


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
    # Via str(): `Decimal(0.1)` is 0.1000000000000000055511151231257827…, and a
    # quantity arriving as a float from JSON would carry that noise into every
    # product below it. The router already stringifies; this makes it hold for
    # every caller rather than for the one that remembered.
    qty = line.quantity if isinstance(line.quantity, Decimal) else Decimal(str(line.quantity))
    if qty < 0:
        # Negative money computed silently is worse than a refused call: the
        # conservation identity below assumes ref×qty ≤ consumer×qty, which
        # inverts for a negative quantity and would hand back a negative insurer
        # share. A return or credit is a different operation, not a negative line.
        raise ValueError(f"quantity must not be negative (got {qty})")
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

    # The differential is DERIVED from the two rounded figures, never rounded on
    # its own. Rounding `(consumer − ref) × qty` independently makes the line
    # stop adding up, because round(a) + round(b) ≠ round(a + b): at the default
    # whole-Rial unit a 3,324.5 item against a 1,662.25 reference over 30 units
    # broke the invariant this module documents by 1 Rial, and at the 1,000-Rial
    # unit the config invites a pharmacy to choose, a single ordinary line was
    # out by 1,000. Sub-Rial prices are not hypothetical — `sell_price` and
    # `manual_shelf_price` are both Numeric(12,4) — and fractional quantities are
    # ordinary, so both halves of that were reachable in production.
    #
    # Defining it as "the part of the sale price the insurer does not recognise"
    # is also the truer reading of مابه‌التفاوت, and it makes
    #     covered_base + differential == gross
    # true by construction at ANY rounding unit. `ref_unit ≤ consumer_price` and
    # quantize is monotone, so covered_base ≤ gross and this is never negative.
    differential = gross - covered_base
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
