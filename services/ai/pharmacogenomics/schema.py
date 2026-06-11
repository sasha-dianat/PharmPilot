from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal


MODEL_VERSION = "pgx-rules-v1"

PHARMACIST_VERIFICATION_NOTICE = (
    "Advisory pharmacogenomics support only. A licensed pharmacist must verify the genotype, phenotype, "
    "clinical context, contraindications, and formulary factors and discuss any therapy change with the prescriber."
)

Confidence = Literal["high", "moderate", "low"]

THIOPURINE_DRUGS = {"azathioprine", "mercaptopurine", "thioguanine", "thiopurines", "thiopurine"}
OXCARBAZEPINE_DRUGS = {"oxcarbazepine"}


@dataclass(frozen=True)
class PGxGenotype:
    gene: str
    diplotype: str | None = None
    phenotype: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class PGxContext:
    genotypes: list[PGxGenotype] = field(default_factory=list)
    medications: list[str] = field(default_factory=list)
    requested_drugs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RuleEntry:
    clinical_implication: str
    suggested_pharmacist_action: str
    alternatives_or_caution: str
    evidence_source: str
    confidence: Confidence
    actionable: bool


@dataclass(frozen=True)
class Interpretation:
    gene: str
    diplotype: str | None
    phenotype: str
    drug: str
    clinical_implication: str
    suggested_pharmacist_action: str
    alternatives_or_caution: str
    evidence_source: str
    confidence: Confidence
    actionable: bool
    pharmacist_verification_notice: str = PHARMACIST_VERIFICATION_NOTICE


@dataclass(frozen=True)
class PGxResult:
    interpretations: list[Interpretation] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)
    model_version: str = MODEL_VERSION


def canonical_gene(gene: str | None) -> str:
    cleaned = (gene or "").strip().upper().replace(" ", "")
    aliases = {
        "HLAB1502": "HLA-B*15:02",
        "HLA-B1502": "HLA-B*15:02",
        "HLA-B*1502": "HLA-B*15:02",
    }
    return aliases.get(cleaned, cleaned)


def canonical_drug_for_rules(normalized_drug: str) -> set[str]:
    drug = normalized_drug.strip().lower()
    keys = {drug} if drug else set()
    if drug in THIOPURINE_DRUGS:
        keys.add("thiopurines")
    if drug in OXCARBAZEPINE_DRUGS:
        keys.add("carbamazepine")
    return keys


def matching_rule_keys_for_drug(
    normalized_drug: str,
    rule_keys: Iterable[tuple[str, str]],
) -> list[tuple[str, str]]:
    candidates = canonical_drug_for_rules(normalized_drug)
    return sorted({key for key in rule_keys if key[1] in candidates})
