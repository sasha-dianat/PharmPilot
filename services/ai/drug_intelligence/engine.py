from __future__ import annotations

from collections.abc import Sequence

from services.ai.clinical_decision_support.normalizer import normalize
from services.ai.second_brain import engine as second_brain_engine
from services.ai.second_brain.engine import RetrieveFn, SynthesizeFn

from .schema import DrugMonograph, MonographSection, SECTION_BY_KEY, SECTIONS, SectionDefinition
from .synthesizer import synthesize_local


_DEFAULT_SYNTHESIZER = object()


async def build_monograph(
    drug_name: str,
    *,
    retrieve: RetrieveFn,
    synthesize: SynthesizeFn | None | object = _DEFAULT_SYNTHESIZER,
    sections: Sequence[str | SectionDefinition] | None = None,
    top_k: int = 6,
) -> DrugMonograph:
    selected_sections = _resolve_sections(sections)
    synthesize_fn = synthesize_local if synthesize is _DEFAULT_SYNTHESIZER else synthesize
    normalized_name = normalize(drug_name)

    monograph_sections: list[MonographSection] = []
    any_evidence = False
    any_llm_used = False
    degraded = False

    for section in selected_sections:
        question = section.query_template.format(drug=drug_name)
        result, llm_meta = await second_brain_engine.answer(
            question,
            retrieve=retrieve,
            synthesize=synthesize_fn,  # type: ignore[arg-type]
            patient_context=None,
            top_k=top_k,
        )
        monograph_sections.append(
            MonographSection(
                key=section.key,
                label=section.label,
                answer=result.answer,
                sources=result.sources,
                confidence=result.confidence,
                refused=result.refused,
                unsupported=result.unsupported,
                llm_used=llm_meta.llm_used,
            )
        )
        any_evidence = any_evidence or bool(result.sources)
        any_llm_used = any_llm_used or llm_meta.llm_used
        degraded = degraded or (not result.refused and llm_meta.degraded)

    return DrugMonograph(
        drug_name=drug_name,
        normalized_name=normalized_name,
        sections=monograph_sections,
        any_evidence=any_evidence,
        llm_used=any_llm_used,
        degraded=degraded,
    )


def _resolve_sections(sections: Sequence[str | SectionDefinition] | None) -> list[SectionDefinition]:
    if sections is None:
        return list(SECTIONS)
    resolved: list[SectionDefinition] = []
    for section in sections:
        if isinstance(section, SectionDefinition):
            resolved.append(section)
            continue
        resolved.append(SECTION_BY_KEY[str(section)])
    return resolved
