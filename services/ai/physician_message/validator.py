from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any

from services.ai.adr_detective.knowledge import KNOWN_DRUG_NAMES
from services.ai.clinical_decision_support.normalizer import normalize
from services.ai.physician_message.builder import body_from_sections
from services.ai.physician_message.schema import MessageContent, MessageInput, URGENCIES


DOSE_RE = re.compile(r"\b\d+(?:\.\d+)?\s?(?:mg|mcg|g|ml|mL|units?|iu|IU|%)\b")


def validate(
    llm_json: str | dict[str, Any],
    input: MessageInput,
    baseline: MessageContent,
) -> tuple[MessageContent | None, bool]:
    try:
        payload = _load_payload(llm_json)
    except Exception:
        return None, False
    if not isinstance(payload, dict):
        return None, False

    sections = payload.get("sections", payload)
    if not isinstance(sections, dict):
        return None, False

    required = set(baseline.sections)
    if not required.issubset(sections):
        return None, False

    clean_sections: dict[str, str] = {}
    for key in baseline.sections:
        value = str(sections.get(key, "")).strip()
        if not value:
            return None, False
        clean_sections[key] = value

    rendered_text = json.dumps(clean_sections, ensure_ascii=False, sort_keys=True)
    source_text = _source_text(input, baseline)
    if _contains_foreign_drug(rendered_text, _allowed_drugs(source_text)):
        return None, False
    if _adds_numeric_dose(rendered_text, source_text):
        return None, False
    if _changes_urgency(rendered_text, input.urgency):
        return None, False
    if not clean_sections.get(_recommendation_key(baseline.format), "").strip():
        return None, False

    return (
        replace(
            baseline,
            language=str(payload.get("language") or baseline.language),
            body=body_from_sections(baseline.format, clean_sections),
            sections=clean_sections,
        ),
        True,
    )


def _load_payload(value: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _source_text(input: MessageInput, baseline: MessageContent) -> str:
    return " ".join(
        [
            input.prescriber_name or "",
            input.patient_context or "",
            input.medication_issue,
            input.clinical_rationale or "",
            input.recommendation_or_question,
            " ".join(input.supporting_data),
            input.pharmacist_name or "",
            baseline.subject,
            baseline.body,
        ]
    )


def _allowed_drugs(source_text: str) -> set[str]:
    allowed: set[str] = set()
    source_lower = source_text.lower()
    for drug in KNOWN_DRUG_NAMES:
        normalized = normalize(drug).lower()
        if re.search(rf"\b{re.escape(drug.lower())}\b", source_lower) or (
            normalized and re.search(rf"\b{re.escape(normalized)}\b", source_lower)
        ):
            allowed.add(drug.lower())
            allowed.add(normalized)
    return {item for item in allowed if item}


def _contains_foreign_drug(text: str, allowed_drugs: set[str]) -> bool:
    lowered = text.lower()
    for drug in KNOWN_DRUG_NAMES:
        normalized = normalize(drug).lower()
        if drug.lower() in allowed_drugs or normalized in allowed_drugs:
            continue
        if re.search(rf"\b{re.escape(drug.lower())}\b", lowered):
            return True
    return False


def _adds_numeric_dose(rendered_text: str, source_text: str) -> bool:
    source_doses = {match.group(0).lower().replace(" ", "") for match in DOSE_RE.finditer(source_text)}
    for match in DOSE_RE.finditer(rendered_text):
        if match.group(0).lower().replace(" ", "") not in source_doses:
            return True
    return False


def _changes_urgency(rendered_text: str, urgency: str) -> bool:
    lowered = rendered_text.lower()
    present = {token for token in URGENCIES if re.search(rf"\b{re.escape(token)}\b", lowered)}
    return urgency not in present or any(token != urgency for token in present)


def _recommendation_key(fmt: str) -> str:
    if fmt == "soap":
        return "plan"
    return "recommendation"

