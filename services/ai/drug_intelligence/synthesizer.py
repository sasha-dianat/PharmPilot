from __future__ import annotations

import asyncio

from services.ai.intelligence_core import local_llm


SYSTEM = (
    "Answer the drug-reference question using ONLY the numbered sources provided. "
    "Cite source_id values in square brackets after clinical claims, e.g. [S3]. "
    "If the sources do not cover the question, say the available sources do not cover it. "
    "Do not use outside knowledge. Do not infer patient-specific advice."
)


async def synthesize_local(question: str, chunks: list[object]) -> str | None:
    if not chunks:
        return None
    try:
        result = await asyncio.wait_for(
            local_llm.generate(
                _prompt(question, chunks),
                system=SYSTEM,
                max_tokens=900,
                temperature=0.1,
                prefer_local=True,
                phi=False,
                task="rag",
            ),
            timeout=10,
        )
    except Exception:
        return None

    text = getattr(result, "text", "") or ""
    degraded = bool(getattr(result, "degraded", False))
    if degraded or not text.strip():
        return None
    return text.strip()


def _prompt(question: str, chunks: list[object]) -> str:
    source_blocks: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        source_id = _attr(chunk, "source_id", f"S{index}")
        title = _attr(chunk, "source_title", "Unknown Source")
        source_type = _attr(chunk, "source_type", "reference")
        evidence_grade = _attr(chunk, "evidence_grade", "not graded") or "not graded"
        content = str(_attr(chunk, "content", "") or "").strip()
        source_blocks.append(
            f"{index}. source_id: {source_id}\n"
            f"   title: {title}\n"
            f"   type: {source_type}\n"
            f"   evidence_grade: {evidence_grade}\n"
            f"   excerpt: {content}"
        )
    sources_text = "\n\n".join(source_blocks)
    return (
        "Drug-reference question:\n"
        f"{question.strip()}\n\n"
        "Retrieved sources:\n"
        f"{sources_text}\n\n"
        "Answer with citations to source_id values only."
    )


def _attr(obj: object, name: str, default: object = None) -> object:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)
