from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, replace

from services.ai.counselling import validator
from services.ai.counselling.schema import CounsellingContent, CounsellingFacts, LLMMeta
from services.ai.intelligence_core import local_llm


SYSTEM = (
    "You translate and simplify pharmacist-approved counselling text. Use only the provided ground truth. "
    "Do not add medicines, do not add or change doses, keep every serious red flag, do not diagnose, "
    "and do not tell the patient to start, stop, or change therapy."
)

DEGRADED_NOTE = "Only English standard-level content could be produced deterministically."


async def render(
    content: CounsellingContent,
    facts: CounsellingFacts,
    level: str,
    language: str,
) -> tuple[CounsellingContent, LLMMeta]:
    baseline = replace(content, level="standard", language="en")
    if language == "en" and level == "standard":
        return baseline, LLMMeta(llm_used=False, provider="none", degraded=False)

    try:
        result = await asyncio.wait_for(
            local_llm.generate(
                _prompt(baseline, level, language),
                system=SYSTEM,
                max_tokens=1200,
                temperature=0.1,
                phi=False,
                task="counseling",
            ),
            timeout=6,
        )
    except Exception:
        return baseline, LLMMeta(llm_used=False, provider="none", degraded=True, note=DEGRADED_NOTE)

    provider = getattr(result, "provider", "unknown")
    degraded = bool(getattr(result, "degraded", False))
    text = getattr(result, "text", "") or ""
    if degraded or not text.strip():
        return baseline, LLMMeta(llm_used=False, provider=provider, degraded=True, note=DEGRADED_NOTE)

    clean, ok = validator.validate(text, facts, replace(baseline, level=level, language=language))
    if not ok or clean is None:
        return baseline, LLMMeta(llm_used=False, provider=provider, degraded=True, note=DEGRADED_NOTE)
    clean.level = level
    clean.language = language
    return clean, LLMMeta(llm_used=True, provider=provider, degraded=False)


def _prompt(content: CounsellingContent, level: str, language: str) -> str:
    payload = {
        "target_language": language,
        "target_reading_level": level,
        "deterministic_ground_truth": {
            "what_for": content.what_for,
            "how_to_take": content.how_to_take,
            "what_to_avoid": content.what_to_avoid,
            "common_side_effects": content.common_side_effects,
            "serious_red_flags": content.serious_red_flags,
            "missed_dose": content.missed_dose,
            "adherence_tips": content.adherence_tips,
            "teach_back_questions": content.teach_back_questions,
        },
        "strict_output_schema": {
            "what_for": "string",
            "how_to_take": "string",
            "what_to_avoid": ["string"],
            "common_side_effects": ["string"],
            "serious_red_flags": ["string"],
            "missed_dose": "string",
            "adherence_tips": ["string"],
            "teach_back_questions": ["string"],
        },
        "constraints": [
            "Return strict JSON only.",
            "Keep the same section keys.",
            "Do not add medicines.",
            "Do not add or change doses.",
            "Keep every serious red flag.",
            "Do not give a diagnosis.",
            "Do not tell the patient to start, stop, or change therapy.",
        ],
    }
    return (
        "Translate and adapt the deterministic counselling sections using only this JSON ground truth. "
        f"Return strict JSON with the same section keys.\n{json.dumps(payload, default=_json_default)}"
    )


def _json_default(value):
    try:
        return asdict(value)
    except Exception:
        return str(value)
