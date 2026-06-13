from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


MODEL_VERSION = "second-brain-v1"

PHARMACIST_VERIFICATION_NOTICE = (
    "For pharmacist verification only. Confirm recommendations against the cited source material, "
    "the current patient record, and professional judgment before acting."
)

REFUSAL_TEXT = (
    "No supporting sources were found in the ingested clinical knowledge base for this question. "
    "Please consult a primary reference or ingest the relevant source before using Second Brain "
    "for this answer."
)

EXTRACTIVE_DEGRADED_NOTE = (
    "AI synthesis was unavailable or unsupported. Review the relevant retrieved source snippets below."
)

VECTOR_STORE_UNAVAILABLE_TEXT = "Knowledge base is temporarily unavailable."

DEFAULT_TOP_K = 8
MAX_TOP_K = 20
SCORE_THRESHOLD = 0.55
SNIPPET_CHARS = 300

Confidence = Literal["high", "moderate", "low", "none"]


@dataclass(slots=True)
class Source:
    source_id: str
    source_title: str
    source_type: str
    snippet: str
    similarity_score: float
    evidence_grade: str | None = None
    url: str | None = None


@dataclass(slots=True)
class SecondBrainResult:
    answer: str
    sources: list[Source]
    patient_context: str | None = None
    confidence: Confidence = "none"
    refused: bool = False
    unsupported: bool = False
    pharmacist_verification_notice: str = PHARMACIST_VERIFICATION_NOTICE


@dataclass(slots=True)
class LLMMeta:
    llm_used: bool
    degraded: bool
    provider: str = "none"
    model: str | None = None
    cited_ids: list[str] = field(default_factory=list)

