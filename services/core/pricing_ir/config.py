"""Iranian pricing tariffs — EDITABLE CONFIG (no calculation logic here).

Confidence (researched 2026-07-01):
  • CONFIRMED: outpatient franchise = 30% patient / 70% basic insurer for both
    تأمین اجتماعی and بیمه سلامت (1404 گذاری تعرفه دولتی سرپایی).
  • Special populations (کمیته امداد, روستایی/عشایر, towns <20k) = 15% outpatient;
    special-disease patients (هموفیلی، تالاسمی، دیالیز) = 0% for formulary drugs —
    NOT yet modelled here (add a patient-category override when needed).
  • Inpatient ~10% public is the standard convention (VERIFY exact per scheme).
  • Armed-forces shares: still VERIFY.
  • حق فنی: a per-Rx professional fee set yearly by سازمان غذا و دارو; by regulation
    insurers should pay it but in practice often don't — treat covers_* as policy,
    the amount is deployment-specific (default 0).
  • VAT: registered drugs are exempt (0). Cosmetics/some supplements taxable.

Engine is config-driven so a domain expert corrects any number here without
touching `engine.py`. Money is **Rial** (1 Toman = 10 Rial); shares are fractions.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class InsurerPlan:
    """A basic-insurance scheme's adjudication parameters.

    franchise = patient's share (سهم بیمار). insurer share = 1 - franchise,
    applied to the insurer-recognised base (reference price × qty).
    """
    code: str
    name_fa: str
    outpatient_patient_share: Decimal     # فرانشیز سرپایی  (0.30 CONFIRMED for tamin/salamat)
    inpatient_patient_share: Decimal      # فرانشیز بستری   (~0.10 public; VERIFY per scheme)
    covers_technical_fee: bool            # does the insurer pay part of حق فنی?
    technical_fee_patient_share: Decimal  # patient's % of حق فنی when covered (VERIFY)


# ── Basic insurers (VERIFY all shares) ────────────────────────────────────────
# Common practice: outpatient patient pays ~30%, inpatient ~10%. Armed-forces
# schemes are typically more generous. CONFIRM exact current franchises.
PLANS: dict[str, InsurerPlan] = {
    "tamin": InsurerPlan(
        code="tamin", name_fa="تأمین اجتماعی",
        outpatient_patient_share=Decimal("0.30"),
        inpatient_patient_share=Decimal("0.10"),
        covers_technical_fee=True,
        technical_fee_patient_share=Decimal("0.30"),
    ),
    "salamat": InsurerPlan(
        code="salamat", name_fa="بیمه سلامت",
        outpatient_patient_share=Decimal("0.30"),
        inpatient_patient_share=Decimal("0.10"),
        covers_technical_fee=True,
        technical_fee_patient_share=Decimal("0.30"),
    ),
    "armed_forces": InsurerPlan(
        code="armed_forces", name_fa="خدمات درمانی نیروهای مسلح",
        outpatient_patient_share=Decimal("0.20"),
        inpatient_patient_share=Decimal("0.05"),
        covers_technical_fee=True,
        technical_fee_patient_share=Decimal("0.20"),
    ),
    # Self-pay / no insurance — patient pays everything.
    "cash": InsurerPlan(
        code="cash", name_fa="آزاد",
        outpatient_patient_share=Decimal("1.00"),
        inpatient_patient_share=Decimal("1.00"),
        covers_technical_fee=False,
        technical_fee_patient_share=Decimal("1.00"),
    ),
}

# Per-prescription pharmacist professional/technical fee (حق فنی), Rial. VERIFY.
DEFAULT_TECHNICAL_FEE_RIAL = Decimal("0")

# VAT (مالیات بر ارزش افزوده). Registered drugs are generally exempt (0);
# cosmetics / some supplements may be taxable. VERIFY rate + applicability.
VAT_RATE_DRUG = Decimal("0")
VAT_RATE_COSMETIC = Decimal("0.10")     # VERIFY (Iran standard VAT has been ~9–10%)
VAT_RATE_SUPPLEMENT = Decimal("0")      # VERIFY

# Monetary rounding: round each computed amount to the nearest whole Rial.
# Set to Decimal("1000") to round to nearest 1000 Rial if a pharmacy prefers.
ROUNDING_UNIT_RIAL = Decimal("1")


def get_plan(code: str) -> InsurerPlan:
    """Resolve an insurer plan by code, falling back to cash/self-pay."""
    return PLANS.get(code, PLANS["cash"])
