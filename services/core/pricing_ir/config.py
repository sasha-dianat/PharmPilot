"""Iranian pricing tariffs — EDITABLE CONFIG (no calculation logic here).

⚠️ VERIFY: every numeric tariff below is a best-effort placeholder pending
confirmation against primary sources (سازمان غذا و دارو, سازمان تأمین اجتماعی,
سازمان بیمه سلامت) — the deep-research pass was cut short by an account session
limit, so these have NOT been source-verified. The engine is intentionally
config-driven so a domain expert can correct any number here without touching
`engine.py`. Tariffs are revised ~yearly; treat this file as the single place
to update them.

Money is in **Rial** (1 Toman = 10 Rial). Shares are fractions of 1.0.
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
    outpatient_patient_share: Decimal     # فرانشیز سرپایی  (VERIFY)
    inpatient_patient_share: Decimal      # فرانشیز بستری   (VERIFY)
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
