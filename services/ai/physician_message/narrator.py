from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, replace

from services.ai.intelligence_core import local_llm
from services.ai.physician_message import validator
from services.ai.physician_message.schema import LLMMeta, MessageContent, MessageInput


SYSTEM = (
    "You translate pharmacist-authored physician communication. Use only the supplied ground truth. "
    "Do not add drugs, doses, clinical facts, diagnoses, or recommendations. Do not change the urgency token. "
    "Do not drop the recommendation/question. Return strict JSON only."
)

DEGRADED_NOTE = "Only the deterministic English baseline could be produced; the requested non-English language was not honored."


async def render(
    content: MessageContent,
    input: MessageInput,
    fmt: str,
    language: str,
) -> tuple[MessageContent, LLMMeta]:
    baseline = replace(content, format=fmt, language="en")
    if language == "en":
        return baseline, LLMMeta(llm_used=False, provider="none", degraded=False)

    try:
        result = await asyncio.wait_for(
            local_llm.generate(
                _prompt(baseline, language),
                system=SYSTEM,
                max_tokens=1400,
                temperature=0.1,
                phi=True,
                task="physician_message",
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

    clean, ok = validator.validate(text, input, replace(baseline, language=language))
    if not ok or clean is None:
        return baseline, LLMMeta(llm_used=False, provider=provider, degraded=True, note=DEGRADED_NOTE)
    clean.language = language
    return clean, LLMMeta(llm_used=True, provider=provider, degraded=False)


def _prompt(content: MessageContent, language: str) -> str:
    payload = {
        "target_language": language,
        "format": content.format,
        "urgency": content.urgency,
        "subject": content.subject,
        "deterministic_ground_truth_sections": content.sections,
        "strict_output_schema": {
            "language": language,
            "sections": {key: "translated string preserving facts and urgency token" for key in content.sections},
        },
        "constraints": [
            "Return strict JSON only.",
            "Use exactly the same section keys.",
            "Translate and refine professional, diplomatic tone only.",
            "Keep the urgency token exactly as provided: routine, urgent, or emergent.",
            "Do not add drug names.",
            "Do not add numeric doses.",
            "Do not add clinical claims.",
            "Do not drop or weaken the recommendation/question.",
        ],
    }
    return (
        "Translate these deterministic physician-message sections using only this JSON ground truth. "
        f"Return strict JSON matching the schema.\n{json.dumps(payload, default=_json_default)}"
    )


def _json_default(value):
    try:
        return asdict(value)
    except Exception:
        return str(value)

