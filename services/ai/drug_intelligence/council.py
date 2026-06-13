from __future__ import annotations

from services.ai.second_brain.engine import RetrieveFn, SynthesizeFn

from .engine import build_monograph


COUNCIL_SECTION_KEYS = ("contraindications", "cautions", "adr")


async def council_drug_reference(
    drug_name: str,
    *,
    retrieve: RetrieveFn,
    synthesize: SynthesizeFn | None = None,
) -> dict:
    monograph = await build_monograph(
        drug_name,
        retrieve=retrieve,
        synthesize=synthesize,
        sections=COUNCIL_SECTION_KEYS,
    )
    return {
        "drug": monograph.drug_name,
        "normalized_name": monograph.normalized_name,
        "sections": {
            section.key: {
                "answer": section.answer,
                "source_ids": [source.source_id for source in section.sources],
                "confidence": section.confidence,
                "refused": section.refused,
                "llm_used": section.llm_used,
            }
            for section in monograph.sections
        },
        "any_evidence": monograph.any_evidence,
        "llm_used": monograph.llm_used,
    }
