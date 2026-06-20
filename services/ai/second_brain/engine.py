from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable
from typing import Any

from services.ai.second_brain.schema import (
    DEFAULT_TOP_K,
    EXTRACTIVE_DEGRADED_NOTE,
    FULLTEXT_CHARS,
    LLMMeta,
    REFUSAL_TEXT,
    SNIPPET_CHARS,
    Source,
    SecondBrainResult,
)
from services.ai.second_brain.validator import grounding_ok

RetrieveFn = Callable[[str, int], list[Any] | Awaitable[list[Any]]]
SynthesizeFn = Callable[[str, list[Any]], str | None | Awaitable[str | None]]


async def answer(
    question: str,
    *,
    retrieve: RetrieveFn,
    synthesize: SynthesizeFn | None = None,
    patient_context: str | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> tuple[SecondBrainResult, LLMMeta]:
    chunks = await _maybe_await(retrieve(question, top_k))
    sources = [_source_from_chunk(chunk) for chunk in chunks]

    if not chunks:
        return (
            SecondBrainResult(
                answer=REFUSAL_TEXT,
                sources=[],
                patient_context=patient_context,
                confidence="none",
                refused=True,
                unsupported=False,
            ),
            LLMMeta(llm_used=False, degraded=False),
        )

    confidence = _confidence(sources)
    if synthesize is None:
        return _extractive_result(
            sources=sources,
            patient_context=patient_context,
            confidence=confidence,
            unsupported=False,
            degraded=True,
        )

    synthesized = await _maybe_await(synthesize(question, chunks))
    if not synthesized:
        return _extractive_result(
            sources=sources,
            patient_context=patient_context,
            confidence=confidence,
            unsupported=False,
            degraded=True,
        )

    ok, cited_ids = grounding_ok(
        synthesized,
        {source.source_id for source in sources},
        source_texts=[getattr(source, "snippet", "") for source in sources],
    )
    if not ok:
        result, meta = _extractive_result(
            sources=sources,
            patient_context=patient_context,
            confidence=confidence,
            unsupported=True,
            degraded=True,
        )
        meta.cited_ids = cited_ids
        return result, meta

    return (
        SecondBrainResult(
            answer=synthesized,
            sources=sources,
            patient_context=patient_context,
            confidence=confidence,
            refused=False,
            unsupported=False,
        ),
        LLMMeta(llm_used=True, degraded=False, cited_ids=cited_ids),
    )


def _clean_snippet(text: str, max_len: int = 320) -> str:
    """Trim a retrieved chunk to whole sentences for display.

    Chunk overlap often starts mid-sentence and ends mid-word; this drops a
    leading sentence fragment, caps the length, and ends on the last complete
    sentence so the pharmacist never sees half-words like 'teady state…'.
    """
    text = (text or "").strip()
    if not text:
        return ""
    # Drop a leading partial sentence (chunk started mid-sentence).
    if not text[:1].isupper():
        m = re.search(r"[.!?]\s+([A-Z])", text)
        if m:
            text = text[m.start(1):]
    if len(text) > max_len:
        text = text[:max_len]
    # End on the last complete sentence; else drop the dangling partial word.
    ends = list(re.finditer(r"[.!?](?=\s|$)", text))
    if ends:
        text = text[:ends[-1].end()]
    elif not text.endswith((".", "!", "?")) and " " in text.rstrip():
        text = text.rstrip()[: text.rstrip().rfind(" ")].rstrip() + "…"
    return text.strip()


def _extractive_result(
    *,
    sources: list[Source],
    patient_context: str | None,
    confidence: str,
    unsupported: bool,
    degraded: bool,
) -> tuple[SecondBrainResult, LLMMeta]:
    answer_text = EXTRACTIVE_DEGRADED_NOTE
    # Inline the top retrieved passages so the answer is useful with no LLM —
    # callers that render only `answer` still see the actual source text. Each
    # passage is trimmed to whole sentences, and near-duplicate drug/section
    # variants (e.g. "Duloxetine" vs "Duloxetine HCl") are collapsed.
    blocks, seen = [], set()
    for s in sources:
        snippet = _clean_snippet(getattr(s, "snippet", "") or "")
        if not snippet:
            continue
        title = (s.source_title or "Source").strip()
        base = title.split("—")[0].strip().split()[0].lower() if title else ""
        section = title.split("—")[-1].strip().lower() if "—" in title else ""
        key = (base, section)
        if key in seen:
            continue
        seen.add(key)
        blocks.append(f"• {title}: {snippet}")
        if len(blocks) >= 3:
            break
    if blocks:
        answer_text = answer_text + "\n\n" + "\n\n".join(blocks)
    return (
        SecondBrainResult(
            answer=answer_text,
            sources=sources,
            patient_context=patient_context,
            confidence=confidence,
            refused=False,
            unsupported=unsupported,
        ),
        LLMMeta(llm_used=False, degraded=degraded),
    )


def _source_from_chunk(chunk: Any) -> Source:
    content = " ".join(str(_attr(chunk, "content", "") or "").split())
    return Source(
        source_id=str(_attr(chunk, "source_id", "") or ""),
        source_title=str(_attr(chunk, "source_title", "Unknown Source") or "Unknown Source"),
        source_type=str(_attr(chunk, "source_type", "") or ""),
        snippet=content[:SNIPPET_CHARS],
        similarity_score=float(_attr(chunk, "similarity_score", 0.0) or 0.0),
        evidence_grade=_attr(chunk, "evidence_grade", None),
        url=_attr(chunk, "url", None),
        full_text=content[:FULLTEXT_CHARS],
    )


def _confidence(sources: list[Source]) -> str:
    if not sources:
        return "none"
    top_score = max(source.similarity_score for source in sources)
    distinct_sources = len({source.source_id for source in sources if source.source_id})
    if top_score >= 0.75 and distinct_sources >= 2:
        return "high"
    if top_score >= 0.65:
        return "moderate"
    return "low"


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)
