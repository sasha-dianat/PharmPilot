from __future__ import annotations

from services.ai.clinical_decision_support.normalizer import normalize
from services.ai.pharmacogenomics.knowledge import DIPLOTYPE_PHENOTYPE, PGX_RULES
from services.ai.pharmacogenomics.schema import (
    MODEL_VERSION,
    Interpretation,
    PGxContext,
    PGxGenotype,
    PGxResult,
    canonical_drug_for_rules,
    canonical_gene,
)


def interpret(context: PGxContext) -> list[Interpretation]:
    return interpret_with_missing(context).interpretations


def interpret_with_missing(context: PGxContext) -> PGxResult:
    interpretations: list[Interpretation] = []
    missing_information: list[str] = []
    drug_matches = _drug_rule_matches(context)

    if not context.genotypes:
        return PGxResult(interpretations=[], missing_information=["no genotype results available"])

    for genotype in context.genotypes:
        gene = canonical_gene(genotype.gene)
        phenotype = _resolve_phenotype(genotype)
        if not phenotype:
            missing_information.append(
                f"phenotype for {gene} {genotype.diplotype or 'unspecified diplotype'} could not be resolved"
            )
            continue

        for normalized_drug, rule_drug in drug_matches:
            rule = PGX_RULES.get((gene, rule_drug), {}).get(phenotype)
            if not rule:
                continue
            interpretations.append(
                Interpretation(
                    gene=gene,
                    diplotype=genotype.diplotype,
                    phenotype=phenotype,
                    drug=normalized_drug,
                    clinical_implication=rule.clinical_implication,
                    suggested_pharmacist_action=rule.suggested_pharmacist_action,
                    alternatives_or_caution=rule.alternatives_or_caution,
                    evidence_source=rule.evidence_source,
                    confidence=rule.confidence,
                    actionable=rule.actionable,
                )
            )

    interpretations = sorted(
        interpretations,
        key=lambda item: (
            0 if item.actionable else 1,
            item.gene,
            item.drug,
            item.phenotype,
        ),
    )
    return PGxResult(
        interpretations=interpretations,
        missing_information=sorted(set(missing_information)),
        model_version=MODEL_VERSION,
    )


def _resolve_phenotype(genotype: PGxGenotype) -> str | None:
    if genotype.phenotype and genotype.phenotype.strip():
        return _canonical_phenotype(genotype.phenotype)

    gene = canonical_gene(genotype.gene)
    diplotype = _canonical_diplotype(genotype.diplotype)
    if not diplotype:
        return None
    phenotype = DIPLOTYPE_PHENOTYPE.get(gene, {}).get(diplotype)
    return _canonical_phenotype(phenotype) if phenotype else None


def _canonical_diplotype(diplotype: str | None) -> str | None:
    if not diplotype:
        return None
    cleaned = " ".join(diplotype.strip().split())
    if "/" in cleaned:
        return cleaned.replace(" ", "")
    return cleaned


def _canonical_phenotype(phenotype: str | None) -> str | None:
    if not phenotype:
        return None
    cleaned = " ".join(phenotype.strip().lower().replace("_", " ").split())
    aliases = {
        "normal": "normal metabolizer",
        "intermediate": "intermediate metabolizer",
        "poor": "poor metabolizer",
        "rapid": "rapid metabolizer",
        "ultra rapid metabolizer": "ultrarapid metabolizer",
        "ultra-rapid metabolizer": "ultrarapid metabolizer",
        "ultrarapid": "ultrarapid metabolizer",
        "positive/present": "positive",
        "present": "positive",
        "detected": "positive",
        "negative/absent": "negative",
        "absent": "negative",
        "not detected": "negative",
        "normal responder": "normal sensitivity",
        "sensitive": "increased sensitivity",
    }
    return aliases.get(cleaned, cleaned)


def _drug_rule_matches(context: PGxContext) -> list[tuple[str, str]]:
    drugs = [*context.medications, *context.requested_drugs]
    matches: set[tuple[str, str]] = set()
    for drug in drugs:
        normalized = normalize(drug)
        if not normalized:
            continue
        for rule_drug in canonical_drug_for_rules(normalized):
            if any(key[1] == rule_drug for key in PGX_RULES):
                matches.add((normalized, rule_drug))
    return sorted(matches)
