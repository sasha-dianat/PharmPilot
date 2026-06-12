from __future__ import annotations

from services.ai.counselling.knowledge import resolve_facts
from services.ai.counselling.schema import CounsellingContent, CounsellingFacts


TEACH_BACK_QUESTIONS = [
    "Can you tell me what this medicine is for?",
    "How will you take it?",
    "What will you do if you miss a dose?",
    "What symptoms would make you call us right away?",
]


def build_baseline(drug: str, level: str = "standard") -> CounsellingContent | None:
    normalized, facts = resolve_facts(drug)
    if facts is None:
        return None
    return _content_from_facts(normalized or drug.strip(), facts, level=level, language="en")


def _content_from_facts(drug_name: str, facts: CounsellingFacts, *, level: str, language: str) -> CounsellingContent:
    return CounsellingContent(
        drug_name=drug_name,
        what_for=facts.what_for,
        how_to_take=facts.how_to_take,
        what_to_avoid=list(facts.what_to_avoid),
        common_side_effects=list(facts.common_side_effects),
        serious_red_flags=list(facts.serious_red_flags),
        missed_dose=facts.missed_dose,
        adherence_tips=list(facts.adherence_tips),
        teach_back_questions=list(TEACH_BACK_QUESTIONS),
        level=level,
        language=language,
    )
