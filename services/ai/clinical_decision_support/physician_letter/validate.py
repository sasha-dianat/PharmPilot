# services/ai/clinical_decision_support/physician_letter/validate.py
from __future__ import annotations

import re

from services.ai.intelligence_core.phi_scrub import scrub_identifiers
from .placeholders import ALLOWED_TOKENS

_TOKEN_RE = re.compile(r"\{\{[A-Z_]+\}\}")


def is_safe_template(text: str) -> bool:
    """A composed template is safe only if every placeholder is allowed AND, once
    placeholders are stripped, no real identifier (national id / email / phone) remains —
    i.e. the model didn't invent or echo an identifier."""
    if not text:
        return False
    for tok in _TOKEN_RE.findall(text):
        if tok not in ALLOWED_TOKENS:
            return False
    stripped = _TOKEN_RE.sub(" ", text)
    _, hits = scrub_identifiers(stripped)
    return not hits
