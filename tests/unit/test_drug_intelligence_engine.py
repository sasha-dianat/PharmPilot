from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.ai.drug_intelligence.council import council_drug_reference
from services.ai.drug_intelligence.engine import build_monograph
from services.ai.drug_intelligence.schema import SECTIONS
from services.ai.second_brain.schema import EXTRACTIVE_DEGRADED_NOTE, REFUSAL_TEXT


def chunk(source_id: str = "S1", score: float = 0.82, content: str = "Drug reference evidence."):
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
async def test_all_sections_with_sources_grounded_synthesis():
    async def retrieve(question: str, top_k: int):
        return [chunk("S1"), chunk("S2", score=0.78)]

    async def synthesize(question: str, chunks):
        return "Use only the retrieved drug reference evidence. [S1]"

    monograph = await build_monograph("Metformin", retrieve=retrieve, synthesize=synthesize)

    assert [section.key for section in monograph.sections] == [section.key for section in SECTIONS]
    assert monograph.any_evidence is True
    assert monograph.llm_used is True
    assert monograph.degraded is False
    assert all(section.answer.startswith("Use only") for section in monograph.sections)
    assert all(section.sources for section in monograph.sections)
    assert all(section.llm_used for section in monograph.sections)


@pytest.mark.anyio
async def test_synthesize_none_uses_extractive_fallback():
    async def retrieve(question: str, top_k: int):
        return [chunk("S1", content="Relevant source snippet.")]

    monograph = await build_monograph("Metformin", retrieve=retrieve, synthesize=None)

    assert monograph.any_evidence is True
    assert monograph.llm_used is False
    assert monograph.degraded is True
    assert all(not section.refused for section in monograph.sections)
    assert all(section.answer.startswith(EXTRACTIVE_DEGRADED_NOTE) for section in monograph.sections)


@pytest.mark.anyio
async def test_empty_retrieve_refuses_only_that_section():
    async def retrieve(question: str, top_k: int):
        if "adverse drug reactions" in question:
            return []
        return [chunk("S1")]

    monograph = await build_monograph("Metformin", retrieve=retrieve, synthesize=None)
    by_key = {section.key: section for section in monograph.sections}

    assert by_key["adr"].refused is True
    assert by_key["adr"].sources == []
    assert by_key["adr"].answer == REFUSAL_TEXT
    assert by_key["dosing"].refused is False
    assert by_key["dosing"].sources


@pytest.mark.anyio
async def test_council_hook_returns_reference_summary_without_patient_data():
    async def retrieve(question: str, top_k: int):
        return [chunk("S1", content="Drug-level reference evidence.")]

    summary = await council_drug_reference("Metformin", retrieve=retrieve, synthesize=None)

    assert summary["drug"] == "Metformin"
    assert set(summary["sections"]) == {"contraindications", "cautions", "adr"}
    assert summary["sections"]["contraindications"]["source_ids"] == ["S1"]
    assert summary["any_evidence"] is True
    assert "patient_id" not in summary
    assert "patient_context" not in summary
    assert "patient" not in repr(summary).lower()
