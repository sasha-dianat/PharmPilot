from __future__ import annotations

from dataclasses import dataclass

from services.ai.second_brain.schema import (
    PHARMACIST_VERIFICATION_NOTICE as SECOND_BRAIN_VERIFICATION_NOTICE,
    Confidence,
    Source,
)


MODEL_VERSION = "drug-intel-v1"

PHARMACIST_VERIFICATION_NOTICE = SECOND_BRAIN_VERIFICATION_NOTICE
TRAINABLE_NOTE = "Trainable: ingest more sources to improve monograph quality."


@dataclass(frozen=True, slots=True)
class SectionDefinition:
    key: str
    label: str
    query_template: str


SECTIONS: tuple[SectionDefinition, ...] = (
    SectionDefinition("adr", "Adverse Drug Reactions", "adverse drug reactions / side effects of {drug}"),
    SectionDefinition("cautions", "Cautions", "warnings and precautions for {drug}"),
    SectionDefinition("contraindications", "Contraindications", "contraindications for {drug}"),
    SectionDefinition(
        "pharmacokinetics",
        "Pharmacokinetics",
        "pharmacokinetics absorption distribution metabolism elimination half-life of {drug}",
    ),
    SectionDefinition("dosing", "Dosing", "usual dosing and renal/hepatic dose adjustment for {drug}"),
    SectionDefinition("monitoring", "Monitoring", "monitoring parameters for {drug}"),
    SectionDefinition("interactions", "Interactions", "major drug interactions of {drug}"),
)

SECTION_KEYS = tuple(section.key for section in SECTIONS)
SECTION_BY_KEY = {section.key: section for section in SECTIONS}


@dataclass(slots=True)
class MonographSection:
    key: str
    label: str
    answer: str
    sources: list[Source]
    confidence: Confidence
    refused: bool
    unsupported: bool
    llm_used: bool


@dataclass(slots=True)
class DrugMonograph:
    drug_name: str
    normalized_name: str
    sections: list[MonographSection]
    any_evidence: bool
    llm_used: bool
    degraded: bool

