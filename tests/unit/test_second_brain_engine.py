from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.ai.second_brain.engine import answer
from services.ai.second_brain.schema import EXTRACTIVE_DEGRADED_NOTE, REFUSAL_TEXT


def chunk(source_id: str, score: float = 0.82, content: str = "Metformin renal dosing evidence."):
    return SimpleNamespace(
        chunk_id=f"chunk-{source_id}",
        source_id=source_id,
        content=content,
        source_title=f"Source {source_id}",
        source_type="guideline",
        url="https://example.test",
        doi=None,
        evidence_grade="A",
        similarity_score=score,
        drugs_mentioned=[],
        specialty_tags=[],
    )


@pytest.mark.anyio
async def test_sources_retrieved_grounded_synthesis_returns_answer():
    async def retrieve(question: str, top_k: int):
        return [chunk("S1"), chunk("S2", score=0.78)]

    async def synthesize(question: str, chunks):
        return "Use the retrieved renal dosing guidance for the answer. [S1]"

    result, meta = await answer("How should I dose this?", retrieve=retrieve, synthesize=synthesize)

    assert result.answer.startswith("Use the retrieved")
    assert [source.source_id for source in result.sources] == ["S1", "S2"]
    assert result.refused is False
    assert result.unsupported is False
    assert result.confidence == "high"
    assert meta.llm_used is True
    assert meta.cited_ids == ["S1"]


@pytest.mark.anyio
async def test_no_sources_refuses_and_does_not_call_synthesize():
    called = False

    async def retrieve(question: str, top_k: int):
        return []

    async def synthesize(question: str, chunks):
        nonlocal called
        called = True
        return "This must not be used."

    result, meta = await answer("Any evidence?", retrieve=retrieve, synthesize=synthesize)

    assert result.refused is True
    assert result.answer == REFUSAL_TEXT
    assert result.sources == []
    assert result.confidence == "none"
    assert meta.llm_used is False
    assert called is False


@pytest.mark.anyio
async def test_degraded_without_synthesizer_returns_extractive_sources():
    async def retrieve(question: str, top_k: int):
        return [chunk("S1", content="A source snippet about adverse effects.")]

    result, meta = await answer("What adverse effects?", retrieve=retrieve, synthesize=None)

    assert result.refused is False
    assert result.unsupported is False
    assert result.answer.startswith(EXTRACTIVE_DEGRADED_NOTE)
    assert result.sources[0].snippet == "A source snippet about adverse effects."
    assert meta.llm_used is False
    assert meta.degraded is True


@pytest.mark.anyio
async def test_hallucinated_citation_falls_back_to_extractive_unsupported():
    async def retrieve(question: str, top_k: int):
        return [chunk("S1"), chunk("S2")]

    async def synthesize(question: str, chunks):
        return "This cites a source that was not retrieved. [S999]"

    result, meta = await answer("Can I trust this?", retrieve=retrieve, synthesize=synthesize)

    assert result.unsupported is True
    assert result.refused is False
    assert result.answer.startswith(EXTRACTIVE_DEGRADED_NOTE)
    assert "S999" not in result.answer
    assert meta.llm_used is False
    assert meta.cited_ids == ["S999"]


@pytest.mark.anyio
async def test_patient_context_passed_through_but_not_sent_to_synthesize():
    patient_context = "PATIENT CONTEXT: age 76; secret patient fact"
    captured = {}

    async def retrieve(question: str, top_k: int):
        return [chunk("S1")]

    async def synthesize(question: str, chunks):
        captured["question"] = question
        captured["chunks"] = chunks
        captured["args_count"] = 2
        return "The answer is grounded in the retrieved source. [S1]"

    result, meta = await answer(
        "Question without patient fact",
        retrieve=retrieve,
        synthesize=synthesize,
        patient_context=patient_context,
    )

    assert result.patient_context == patient_context
    assert meta.llm_used is True
    assert captured["args_count"] == 2
    assert captured["question"] == "Question without patient fact"
    assert all("secret patient fact" not in item.content for item in captured["chunks"])

