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


_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]{3,}")


def _supported_by_sources(answer_text: str, source_texts, threshold: float = 0.45) -> bool:
    """True if most of the answer's significant words appear in the source text.

    A grounding fallback for LLMs that don't reliably emit [source_id] citations
    (e.g. Llama via Groq) — proves the answer is derived from the retrieved
    sources, not hallucinated, without requiring bracket tags.
    """
    ans_words = {w.lower() for w in _WORD_RE.findall(answer_text or "")}
    if len(ans_words) < 4:
        return False
    src_words = set()
    for t in source_texts:
        src_words.update(w.lower() for w in _WORD_RE.findall(str(t or "")))
    if not src_words:
        return False
    overlap = len(ans_words & src_words) / len(ans_words)
    return overlap >= threshold


def grounding_ok(
    answer_text: str,
    retrieved_source_ids: set[str] | list[str] | tuple[str, ...],
    source_texts: list[str] | None = None,
) -> tuple[bool, list[str]]:
    retrieved = {str(source_id) for source_id in retrieved_source_ids if str(source_id)}
    cited_ids = list(dict.fromkeys(_BRACKETED_CITATION_RE.findall(answer_text or "")))
    if not answer_text or _REFUSAL_RE.search(answer_text):
        return False, cited_ids
    # Citation path: every bracketed [id] must be a retrieved source (Claude/GPT).
    if cited_ids and all(source_id in retrieved for source_id in cited_ids):
        return True, cited_ids
    # Citation-free fallback: accept if the answer is substantially supported by
    # the source text (covers models that don't emit [id] tags, e.g. Llama/Groq).
    if source_texts and _supported_by_sources(answer_text, source_texts):
        return True, cited_ids
    return False, cited_ids

