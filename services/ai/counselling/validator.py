from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any

from services.ai.clinical_decision_support.normalizer import normalize
from services.ai.counselling.knowledge import KNOWN_DRUG_NAMES
from services.ai.counselling.schema import CounsellingContent, CounsellingFacts


REQUIRED_SECTION_KEYS = {
    "what_for",
    "how_to_take",
    "what_to_avoid",
    "common_side_effects",
    "serious_red_flags",
    "missed_dose",
    "adherence_tips",
    "teach_back_questions",
}

DOSE_RE = re.compile(r"\b\d+(?:\.\d+)?\s?(?:mg|mcg|g|ml|mL|units?|iu|IU)\b")
DIRECTIVE_RE = re.compile(
    r"\bstop\s+taking\b"
    r"|\bstart\s+taking\b"
    r"|\bdouble\s+(?:your\s+|the\s+)?dose\b"
    r"|\btake\s+an?\s+extra\s+dose\b"
    r"|\bincrease\s+(?:your\s+|the\s+)?dose\b"
    r"|\bdecrease\s+(?:your\s+|the\s+)?dose\b"
    r"|\bchange\s+(?:your\s+|the\s+)?dose\b",
    re.IGNORECASE,
)


def validate(
    llm_json: str | dict[str, Any],
    facts: CounsellingFacts,
    baseline: CounsellingContent,
) -> tuple[CounsellingContent | None, bool]:
    try:
        payload = _load_payload(llm_json)
    except Exception:
        return None, False
    if not isinstance(payload, dict) or not REQUIRED_SECTION_KEYS.issubset(payload):
        return None, False

    if not _sections_have_expected_shapes(payload):
        return None, False

    rendered_text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    baseline_text = _source_text(facts, baseline)
    allowed_drugs = _allowed_drugs(baseline, baseline_text)
    if _contains_foreign_drug(rendered_text, allowed_drugs):
        return None, False
    if _added_numeric_dose(rendered_text, baseline_text):
        return None, False
    if DIRECTIVE_RE.search(rendered_text):
        return None, False

    serious_red_flags = _clean_list(payload["serious_red_flags"])
    if len(serious_red_flags) < len(baseline.serious_red_flags):
        return None, False

    content = replace(
        baseline,
        what_for=str(payload["what_for"]).strip(),
        how_to_take=str(payload["how_to_take"]).strip(),
        what_to_avoid=_clean_list(payload["what_to_avoid"]),
        common_side_effects=_clean_list(payload["common_side_effects"]),
        serious_red_flags=serious_red_flags,
        missed_dose=str(payload["missed_dose"]).strip(),
        adherence_tips=_clean_list(payload["adherence_tips"]),
        teach_back_questions=_clean_list(payload["teach_back_questions"]),
        level=str(payload.get("level") or baseline.level),
        language=str(payload.get("language") or baseline.language),
    )
    return content, True


def _load_payload(value: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _sections_have_expected_shapes(payload: dict[str, Any]) -> bool:
    for key in ("what_for", "how_to_take", "missed_dose"):
        if not str(payload.get(key, "")).strip():
            return False
    for key in ("what_to_avoid", "common_side_effects", "serious_red_flags", "adherence_tips", "teach_back_questions"):
        if not _clean_list(payload.get(key)):
            return False
    return True


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _source_text(facts: CounsellingFacts, baseline: CounsellingContent) -> str:
    return " ".join(
        [
            baseline.drug_name,
            facts.what_for,
            facts.how_to_take,
            *facts.what_to_avoid,
            *facts.common_side_effects,
            *facts.serious_red_flags,
            facts.missed_dose,
            *facts.adherence_tips,
            *baseline.teach_back_questions,
        ]
    )


def _allowed_drugs(baseline: CounsellingContent, source_text: str) -> set[str]:
    allowed = {normalize(baseline.drug_name), baseline.drug_name.lower()}
    source_lower = source_text.lower()
    for drug in KNOWN_DRUG_NAMES:
        if re.search(rf"\b{re.escape(drug.lower())}\b", source_lower):
            allowed.add(drug.lower())
            allowed.add(normalize(drug).lower())
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


def _added_numeric_dose(rendered_text: str, baseline_text: str) -> bool:
    source_doses = {match.group(0).lower().replace(" ", "") for match in DOSE_RE.finditer(baseline_text)}
    for match in DOSE_RE.finditer(rendered_text):
        if match.group(0).lower().replace(" ", "") not in source_doses:
            return True
    return False
