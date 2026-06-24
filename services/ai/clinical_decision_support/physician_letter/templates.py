# services/ai/clinical_decision_support/physician_letter/templates.py
from __future__ import annotations

from .content import ClinicalContent

LETTER_VERSION = "letter-v1"

_FA = """بسمه تعالی

تاریخ: {{DATE}}
جناب آقای/سرکار خانم دکتر {{PHYSICIAN_NAME}} — شماره نظام پزشکی: {{COUNCIL_ID}}

با سلام و احترام،
در زمان بازبینی نسخهٔ بیمار {{PATIENT_NAME}} (کد ملی: {{PATIENT_NATIONAL_ID}}) یک تداخل داروییِ منع‌مصرف شناسایی شد.

هشدار: {warning}
سازوکار: {mechanism}
داروهای مرتبط: {drugs}

خواهشمند است در صورت صلاحدید و پذیرش مسئولیت بالینیِ تجویز این داروها، مراتب را تأیید فرمایید.

با احترام،
داروساز: {{PHARMACIST_NAME}} (شماره نظام: {{PHARMACIST_LICENSE}})
داروخانه: {{PHARMACY_NAME}}

— تأیید پزشک —
اینجانب تداخل فوق را بررسی نموده و مسئولیت بالینیِ تجویز این داروها را می‌پذیرم.
امضا: ____________     تاریخ: ____________     شماره نظام پزشکی: ____________
"""

_EN = """Date: {{DATE}}
Dr {{PHYSICIAN_NAME}} — Medical Council ID: {{COUNCIL_ID}}

Dear Doctor,
During pharmacist verification of the prescription for {{PATIENT_NAME}}
(National ID: {{PATIENT_NATIONAL_ID}}), a contraindicated drug interaction was identified.

Warning: {warning}
Mechanism: {mechanism}
Medications involved: {drugs}

If, in your clinical judgement, you choose to proceed and assume responsibility for
administering these medications, please confirm below.

Respectfully,
Pharmacist: {{PHARMACIST_NAME}} (License: {{PHARMACIST_LICENSE}})
Pharmacy: {{PHARMACY_NAME}}

— Physician acknowledgement —
I have reviewed the interaction above and assume clinical responsibility for administering
these medications.
Signature: ____________     Date: ____________     Medical Council ID: ____________
"""


def deterministic_template(content: ClinicalContent, language: str) -> str:
    body = _EN if language == "en" else _FA   # any non-en language → Persian
    drugs = "، ".join(content.drugs) if body is _FA else ", ".join(content.drugs)
    # NOTE: str.format() would treat the literal "{{TOKEN}}" placeholders as escaped
    # braces and collapse them to "{TOKEN}", destroying the identifier tokens. Use
    # targeted .replace() for the three clinical-content fields instead, leaving the
    # "{{...}}" identifier placeholders untouched for later substitute().
    return (body.replace("{warning}", content.warning)
                .replace("{mechanism}", content.mechanism)
                .replace("{drugs}", drugs))
