"""Iranian pricing tariffs — EDITABLE CONFIG (no calculation logic here).

Researched against regulatory and legal sources 2026-08-22 (≈ مرداد ۱۴۰۵).
Every figure below now carries its basis. Where a number is still unverified it
says so in place, rather than in a blanket disclaimer at the top.

CONFIRMED — drugs, outpatient 30% patient / 70% insurer; inpatient 10% / 90%.
  Statutory, and unchanged for 1405. Corroborated by Iranian regulatory
  reporting and by peer-reviewed literature ("90% for inpatient and 70% for
  outpatient pharmaceuticals"). Applies to تأمین اجتماعی and بیمه سلامت alike.

  TRAP, checked and avoided: the 1405 cabinet resolution (هیئت وزیران,
  ۲۵ اسفند ۱۴۰۴, effective ۱ فروردین ۱۴۰۵) replaced the flat outpatient franchise
  with a DECILE band — deciles 1-3 = 25%, 4-6 = 30%, 7-10 = 40% — but explicitly
  «به استثنای داروها». Drugs are carved out and keep the 30%. That table must
  never be applied to a pharmacy line; it would look like a refinement and would
  be wrong on every row.

CONFIRMED — VAT. Drugs and vaccines are exempt under ماده ۹ بند (الف) جزء (۱۵)
  of the 1400 VAT law. Human supplements and vitamins became exempt when
  بخشنامه ۲۰۰/۴/۱۴۰۳ (۱۴۰۳/۰۲/۱۶) deleted the «به استثنای مکمل‌ها» clause — but
  that covers مکمل دارویی holding an IRC licence, NOT food/sport supplements,
  which remain taxable. Cosmetics carry the general 10% (9% + 1% عوارض).

CORRECTED — حق فنی is paid by the PATIENT. Basic insurers do not contribute.
  The pharmacy formula is: پرداختی بیمار = سهم بیمار + اقلام آزاد + حق فنی. The
  pharmacists' association is still lobbying to have basic insurance cover it
  "like a physician's visit fee", which is itself evidence that it does not.
  This file previously had covers_technical_fee=True with a 30% patient share,
  so the engine charged the patient 218,100 and billed an insurer 508,900 that
  the insurer never pays — per prescription.

CORRECTED — armed forces (ساخد). The outpatient DRUG franchise was cut from 30%
  to 15%; drugs already at 0/5/10/15% are unchanged; special-disease insureds are
  at 0%. Inpatient at government and military contracted centres is FREE; 10-35%
  applies only at private contracted centres, so the 0.00 below is the common
  case and not universal.

NOT MODELLED — patient categories. These are real and the engine currently
  over-charges every one of them. Deliberately left out until there is a way to
  record which category a patient is in; a per-category franchise with no
  provenance for the category is worse than none.
    · کمیته امداد / بهزیستی مددجویان — outpatient reduced 30% → 15%; inpatient
      in-referral at government centres 0%
    · روستایی / عشایر / towns <20k, INSIDE the referral path — 30% for drugs
      (not the 15% an earlier note in this file claimed), and 100% OUTSIDE it:
      off-path, the patient pays everything
    · بیماران خاص و صعب‌العلاج — drugs and related outpatient care FREE, 100%
      paid by بیمه سلامت at the government tariff. تأمین اجتماعی likewise exempts
      هموفیلی، تالاسمی، دیالیزی on listed drugs (one source reports 5% for
      هموفیلی — resolve before modelling)
    · دهک‌های ۱ تا ۳ — 0% franchise
    · کودکان زیر ۷ سال — 0%, EXCEPT outpatient drugs, which stay normal

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
    outpatient_patient_share: Decimal     # فرانشیز سرپایی — 0.30 CONFIRMED, drugs
    inpatient_patient_share: Decimal      # فرانشیز بستری  — 0.10 CONFIRMED, drugs
    covers_technical_fee: bool            # does the insurer pay part of حق فنی?
    technical_fee_patient_share: Decimal  # patient's % of حق فنی when covered


# ── Basic insurers ────────────────────────────────────────────────────────────
# The drug franchise is 30% outpatient / 10% inpatient across the basic schemes.
# `covers_technical_fee=False` throughout: no basic insurer pays any part of the
# dispensing fee — see the module docstring. When False the engine bills the
# whole fee to the patient regardless of `technical_fee_patient_share`, which is
# kept at 1.00 so the two never disagree.
PLANS: dict[str, InsurerPlan] = {
    "tamin": InsurerPlan(
        code="tamin", name_fa="تأمین اجتماعی",
        outpatient_patient_share=Decimal("0.30"),
        inpatient_patient_share=Decimal("0.10"),
        covers_technical_fee=False,
        technical_fee_patient_share=Decimal("1.00"),
    ),
    "salamat": InsurerPlan(
        code="salamat", name_fa="بیمه سلامت",
        outpatient_patient_share=Decimal("0.30"),
        inpatient_patient_share=Decimal("0.10"),
        covers_technical_fee=False,
        technical_fee_patient_share=Decimal("1.00"),
    ),
    "armed_forces": InsurerPlan(
        code="armed_forces", name_fa="خدمات درمانی نیروهای مسلح",
        # ساخد cut the outpatient drug franchise 30% → 15%. Inpatient at
        # government/military contracted centres is free; a PRIVATE contracted
        # centre charges 10-35%, which this single figure cannot express — treat
        # 0.00 as the common case, not a universal one.
        outpatient_patient_share=Decimal("0.15"),
        inpatient_patient_share=Decimal("0.00"),
        covers_technical_fee=False,
        technical_fee_patient_share=Decimal("1.00"),
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

# ── حق فنی / تعرفه خدمات دارویی ───────────────────────────────────────────────
# Per-prescription dispensing fee, Rial. 1405 tariff, announced by انجمن
# داروسازان ایران on the cabinet resolution, effective ۱ فروردین ۱۴۰۵:
#
#     کد ۹۰۵۰۱۰  نسخه‌پیچی سرپایی      727,000 ریال   per prescription  ← this
#     کد ۹۰۵۰۱۵  مدیریت عرضه OTC        81,000 ریال   per visit
#     کد ۹۰۵۰۳۰  مشاوره              1,620,000 ریال   per visit
#
# PRIVATE-SECTOR figure, per the owner (2026-08-22). The tariff is banded by
# sector and the other bands are lower — in 1404 the same code 905010 was
# 336,000 خصوصی / 308,700 خیریه / 227,500 عمومی غیردولتی. A دولتی pharmacy, and a
# government hospital pharmacy operated by a private contractor, charge the
# government band. Change this number if the deployment is not private.
#
# LEGAL STATUS — NOT settled law, and the pharmacy should know it. هیئت عمومی
# دیوان عدالت اداری has annulled this charge SIX times: ۳/۲/۸۸، ۱۹/۷/۸۹، ۱۷/۴/۹۳،
# ۲۸/۱/۹۷، ۲۰/۶/۹۷ and ۸/۱۱/۹۸ (see also آرای ۱۹۹ و ۶۸۳). The court held that a
# technical manager's duties are not diagnostic/health/treatment services, that
# ماده ۸ قانون بیمه همگانی خدمات درمانی ۱۳۷۳ confers no power to price them, and
# that setting any such tariff is outside the cabinet's competence. It was then
# re-established under a new name — the annulled «حق فنی» became «تعرفه خدمات
# دارویی», a کتاب ارزش نسبی service code — which is the instrument above.
# It IS collected in practice. Set to 0 to stop charging it.
#
# THREE RULES NOT MODELLED (they change what may lawfully be charged):
#   · chargeable on at most THREE items per prescription
#   · +40% on nights and holidays (older circulars said 10-20%; it has moved)
#   · lawful only while the pharmacy is connected to تی‌تک (TTAC); charging above
#     the tariff is گران‌فروشی and prosecutable
DEFAULT_TECHNICAL_FEE_RIAL = Decimal("727000")

# ── VAT (مالیات بر ارزش افزوده) ───────────────────────────────────────────────
# CONFIRMED 2026-08-22. Drugs and vaccines exempt under ماده ۹ بند (الف) جزء (۱۵)
# of the 1400 VAT law.
VAT_RATE_DRUG = Decimal("0")
# General rate: 9% VAT + 1% عوارض.
VAT_RATE_COSMETIC = Decimal("0.10")
# Exempt since بخشنامه ۲۰۰/۴/۱۴۰۳ (۱۴۰۳/۰۲/۱۶) deleted «به استثنای مکمل‌ها و
# ویتامین‌های مصرفی انسان». CAVEAT: that covers مکمل دارویی carrying an IRC
# licence. A food or sports supplement is not a drug and remains taxable at the
# general rate — the category on the catalog record decides, so a mis-categorised
# food supplement will be under-taxed here.
VAT_RATE_SUPPLEMENT = Decimal("0")

# Monetary rounding: round each computed amount to the nearest whole Rial.
# Set to Decimal("1000") to round to nearest 1000 Rial if a pharmacy prefers.
ROUNDING_UNIT_RIAL = Decimal("1")


def get_plan(code: str) -> InsurerPlan:
    """Resolve an insurer plan by code, falling back to cash/self-pay."""
    return PLANS.get(code, PLANS["cash"])
