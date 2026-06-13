from __future__ import annotations

import re


_BRACKETED_CITATION_RE = re.compile(r"[\[(]([A-Za-z0-9][A-Za-z0-9_.:-]{0,127})[\])]")
_REFUSAL_RE = re.compile(
    r"\b("
    r"i\s+(?:do\s+not|don't)\s+know|"
    r"cannot\s+answer|can't\s+answer|"
    r"no\s+(?:supporting\s+)?sources?|"
    r"available\s+sources\s+do\s+not|"
    r"not\s+contain\s+the\s+answer"
    r")\b",
    re.IGNORECASE,
)


def grounding_ok(answer_text: str, retrieved_source_ids: set[str] | list[str] | tuple[str, ...]) -> tuple[bool, list[str]]:
    retrieved = {str(source_id) for source_id in retrieved_source_ids if str(source_id)}
    cited_ids = list(dict.fromkeys(_BRACKETED_CITATION_RE.findall(answer_text or "")))
    if not answer_text or _REFUSAL_RE.search(answer_text):
        return False, cited_ids
    if not cited_ids:
        return False, cited_ids
    return all(source_id in retrieved for source_id in cited_ids), cited_ids

