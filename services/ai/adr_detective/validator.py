from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any

from services.ai.adr_detective.knowledge import KNOWN_DRUG_NAMES
from services.ai.adr_detective.schema import ADRContext, SuspectedCause
from services.ai.clinical_decision_support.normalizer import normalize


CAUSALITY_RANK = {"unclear": 0, "unlikely": 1, "possible": 2, "probable": 3}
DIRECTIVE_RE = re.compile(
    r"\b(discontinue|hold)\b|\bstop\s+(?:the\s+)?(?:drug|medication|medicine|[a-z][a-z-]+)"
    r"|\bstart\s+(?:the\s+)?(?:drug|medication|medicine|[a-z][a-z-]+)"
    r"|\b(initiate|begin)\s+(?:therapy|treatment|[a-z][a-z-]+)"
    r"|\b(increase|decrease|reduce|raise|lower|adjust|change)\s+(?:the\s+)?dose\b"
    r"|\bdose\s+(?:increase|decrease|reduction|adjustment)\b",
    re.IGNORECASE,
)


def validate(llm_json: str | dict[str, Any], causes: list[SuspectedCause], context: ADRContext) -> tuple[list[SuspectedCause], bool]:
    try:
        payload = json.loads(llm_json) if isinstance(llm_json, str) else llm_json
    except Exception:
        return causes, False
    if not isinstance(payload, dict) or not isinstance(payload.get("causes"), list):
        return causes, False

    allowed_names = _allowed_drug_names(context)
    full_text = json.dumps(payload, sort_keys=True)
    if _contains_hallucinated_drug(full_text, allowed_names):
        return causes, False
    if DIRECTIVE_RE.search(full_text):
        return causes, False

    by_drug = {cause.normalized_name: cause for cause in causes}
    clean: list[SuspectedCause] = []
    used: set[str] = set()
    for item in payload["causes"]:
        if not isinstance(item, dict):
            return causes, False
        drug = str(item.get("drug", ""))
        normalized = normalize(drug)
        if normalized not in by_drug:
            return causes, False
        deterministic = by_drug[normalized]
        requested_causality = str(item.get("causality", deterministic.causality)).lower()
        if CAUSALITY_RANK.get(requested_causality, 99) > CAUSALITY_RANK[deterministic.causality]:
            return causes, False
        clean.append(
            replace(
                deterministic,
                reasoning=_list_or_default(item.get("reasoning"), deterministic.reasoning),
                alternative_explanations=_list_or_default(
                    item.get("alternative_explanations"),
                    deterministic.alternative_explanations,
                ),
                questions_to_ask=_list_or_default(item.get("questions_to_ask"), deterministic.questions_to_ask),
            )
        )
        used.add(normalized)

    clean.extend(cause for cause in causes if cause.normalized_name not in used)
    return clean, True


def _allowed_drug_names(context: ADRContext) -> set[str]:
    allowed: set[str] = set()
    for medication in context.medications:
        allowed.add(medication.drug_name.lower())
        allowed.add(medication.normalized_name.lower())
        allowed.add(normalize(medication.drug_name).lower())
    return {item for item in allowed if item}


def _contains_hallucinated_drug(text: str, allowed_names: set[str]) -> bool:
    lowered = text.lower()
    for drug in KNOWN_DRUG_NAMES:
        if drug.lower() in allowed_names:
            continue
        if re.search(rf"\b{re.escape(drug.lower())}\b", lowered):
            return True
    return False


def _list_or_default(value: Any, default: list[str]) -> list[str]:
    if not isinstance(value, list):
        return default
    clean = [str(item).strip() for item in value if str(item).strip()]
    return clean or default
