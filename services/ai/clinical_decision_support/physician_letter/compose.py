# services/ai/clinical_decision_support/physician_letter/compose.py
from __future__ import annotations

import logging

from services.ai.intelligence_core import local_llm
from .content import ClinicalContent
from .templates import deterministic_template
from .validate import is_safe_template

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You write a formal, respectful clinical letter from a pharmacist to a physician. "
    "Use ONLY these placeholder tokens for any person, pharmacy, or date: "
    "{{PATIENT_NAME}}, {{PATIENT_NATIONAL_ID}}, {{PHYSICIAN_NAME}}, {{COUNCIL_ID}}, "
    "{{PHARMACIST_NAME}}, {{PHARMACIST_LICENSE}}, {{PHARMACY_NAME}}, {{DATE}}. "
    "NEVER invent or write a real name, ID number, email, or phone. End with a physician "
    "signature line."
)


def build_prompt(content: ClinicalContent, language: str) -> str:
    return (
        f"Language: {language}\n"
        f"Warning: {content.warning}\n"
        f"Mechanism: {content.mechanism}\n"
        f"Medications: {', '.join(content.drugs)}\n\n"
        "Write the letter now, in the requested language, using only the placeholder tokens "
        "for identifiers (e.g. address the physician as 'Dr {{PHYSICIAN_NAME}}' and the "
        "patient as '{{PATIENT_NAME}}')."
    )


async def compose_via_llm(content: ClinicalContent, language: str) -> tuple[str, str] | None:
    try:
        res = await local_llm.generate(
            build_prompt(content, language), system=_SYSTEM,
            max_tokens=900, temperature=0.2, phi=False, task="summarize")
    except Exception as exc:  # pragma: no cover
        logger.warning("[physician_letter] LLM compose failed: %s", exc)
        return None
    text = (getattr(res, "text", "") or "").strip()
    if getattr(res, "degraded", False) or not text or not is_safe_template(text):
        return None
    return text, f"{res.provider}:{res.model}"


async def compose(content: ClinicalContent, language: str) -> tuple[str, str]:
    out = await compose_via_llm(content, language)
    if out:
        return out
    return deterministic_template(content, language), "deterministic"
