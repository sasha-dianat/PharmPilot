from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

from services.ai.adr_detective import validator
from services.ai.adr_detective.schema import ADRContext, LLMMeta, SuspectedCause
from services.ai.intelligence_core.local_llm import generate


SYSTEM_PROMPT = (
    "You are drafting wording for a pharmacist. Do not add drugs, do not change the causality rating, "
    "do not recommend starting/stopping/dosing therapy, do not invent facts. Use only the provided findings."
)


async def narrate(causes: list[SuspectedCause], context: ADRContext) -> tuple[list[SuspectedCause], LLMMeta]:
    if not causes:
        return causes, LLMMeta(llm_used=False, provider="none", degraded=False)

    prompt = _prompt(causes, context)
    try:
        result = await asyncio.wait_for(
            generate(
                prompt,
                system=SYSTEM_PROMPT,
                max_tokens=900,
                temperature=0.1,
                phi=True,
                task="general",
                pharmacy_id=context.pharmacy_id,
                staff_id=context.staff_id,
            ),
            timeout=4,
        )
    except Exception:
        return causes, LLMMeta(llm_used=False, provider="none", degraded=True)

    provider = getattr(result, "provider", "unknown")
    degraded = bool(getattr(result, "degraded", False))
    text = getattr(result, "text", "") or ""
    if degraded or not text.strip():
        return causes, LLMMeta(llm_used=False, provider=provider, degraded=True)

    clean, ok = validator.validate(text, causes, context)
    if not ok:
        return causes, LLMMeta(llm_used=False, provider=provider, degraded=True)
    return clean, LLMMeta(llm_used=True, provider=provider, degraded=False)


def _prompt(causes: list[SuspectedCause], context: ADRContext) -> str:
    findings = [
        {
            "drug": cause.drug,
            "reaction": cause.reaction,
            "causality": cause.causality,
            "seriousness": cause.seriousness,
            "signals": cause.signals,
            "reasoning": cause.reasoning,
            "alternative_explanations": cause.alternative_explanations,
            "questions_to_ask": cause.questions_to_ask,
            "missing_information": cause.missing_information,
        }
        for cause in causes
    ]
    medications = [
        {
            "drug_name": medication.drug_name,
            "normalized_name": medication.normalized_name,
            "classes": sorted(medication.classes),
        }
        for medication in context.medications
    ]
    payload = {
        "complaint": context.complaint,
        "onset_date": context.onset_date.isoformat() if context.onset_date else None,
        "medications": medications,
        "deterministic_findings": findings,
        "output_schema": {
            "causes": [
                {
                    "drug": "same drug string as provided",
                    "causality": "same or weaker than deterministic causality",
                    "reasoning": ["clear pharmacist-facing wording"],
                    "alternative_explanations": ["only alternatives already provided"],
                    "questions_to_ask": ["questions only, no directives"],
                }
            ]
        },
    }
    return (
        "Rewrite only reasoning, alternative_explanations, and questions_to_ask in strict JSON. "
        "The deterministic findings are ground truth and must not be changed.\n"
        f"{json.dumps(payload, default=_json_default)}"
    )


def _json_default(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    try:
        return asdict(value)
    except Exception:
        return str(value)
