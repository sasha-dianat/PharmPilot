# services/ai/clinical_decision_support/physician_letter/placeholders.py
from __future__ import annotations

import re

ALLOWED_TOKENS = {
    "{{PATIENT_NAME}}", "{{PATIENT_NATIONAL_ID}}", "{{PHYSICIAN_NAME}}",
    "{{COUNCIL_ID}}", "{{PHARMACIST_NAME}}", "{{PHARMACIST_LICENSE}}",
    "{{PHARMACY_NAME}}", "{{DATE}}",
}

_TOKEN_RE = re.compile(r"\{\{[A-Z_]+\}\}")


def substitute(template: str, values: dict[str, str]) -> str:
    """Replace identifier placeholders with real values. Raises ValueError on an
    unknown token or any leftover placeholder (never emit an unfilled letter)."""
    for tok in _TOKEN_RE.findall(template):
        if tok not in ALLOWED_TOKENS:
            raise ValueError(f"unknown placeholder {tok}")
    out = template
    for tok, val in values.items():
        if tok in ALLOWED_TOKENS:
            out = out.replace(tok, str(val))
    if _TOKEN_RE.search(out):
        raise ValueError(f"unfilled placeholder remains: {_TOKEN_RE.search(out).group()}")
    return out
