from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from services.ai.second_brain.schema import (
    DEFAULT_TOP_K,
    EXTRACTIVE_DEGRADED_NOTE,
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

    ok, cited_ids = grounding_ok(synthesized, {source.source_id for source in sources})
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
    # callers that render only `answer` still see the actual source text.
    blocks = [f"• {s.source_title}: {s.snippet.strip()}"
              for s in sources[:3] if getattr(s, "snippet", "")]
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
    content = str(_attr(chunk, "content", "") or "")
    snippet = " ".join(content.split())[:SNIPPET_CHARS]
    return Source(
        source_id=str(_attr(chunk, "source_id", "") or ""),
        source_title=str(_attr(chunk, "source_title", "Unknown Source") or "Unknown Source"),
        source_type=str(_attr(chunk, "source_type", "") or ""),
        snippet=snippet,
        similarity_score=float(_attr(chunk, "similarity_score", 0.0) or 0.0),
        evidence_grade=_attr(chunk, "evidence_grade", None),
        url=_attr(chunk, "url", None),
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
