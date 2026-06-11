from __future__ import annotations

import re
from collections import defaultdict

from services.ai.polypharmacy.knowledge import (
    ACB_SOURCE,
    ANTICHOLINERGIC_BURDEN,
    BEERS_2023_SOURCE,
    CLASS_ALIASES,
    EXPECTED_THERAPY,
    PIM_RULES,
    PRESCRIBING_CASCADES,
    SEDATIVE_FALL_RISK,
    STOPP_START_V2_SOURCE,
    TAPER_REQUIRED,
    TAPER_SOURCE,
)
from services.ai.polypharmacy.schema import BurdenScore, Finding, PolyContext, PolyMedication, ReviewResult


MODEL_VERSION = "polypharmacy-rules-v1"

BLEEDING_SOURCE = (
    "American Geriatrics Society 2023 Beers Criteria and FDA anticoagulant/antiplatelet/NSAID bleeding warnings"
)
TAPERING_CAUTION = (
    "This medication can require gradual dose changes; discuss a taper plan with the prescriber before any change."
)
UNKNOWN_INDICATIONS = {"", "unknown", "not documented", "none", "n/a", "na"}
INACTIVE_STATUSES = {"inactive", "completed", "cancelled", "canceled", "held", "ended"}
PRIORITY_ORDER = {"high": 0, "moderate": 1, "low": 2}


def review(context: PolyContext) -> ReviewResult:
    active_meds = [med for med in context.medications if _is_active(med)]
    findings: list[Finding] = []
    missing_information: list[str] = []

    anticholinergic_score = _anticholinergic_burden(active_meds)
    sedative_score = _sedative_burden(active_meds)

    if anticholinergic_score.score >= 3:
        findings.append(_with_taper_caution(Finding(
            category="anticholinergic_burden",
            priority="high",
            drugs_involved=anticholinergic_score.drugs,
            explanation=(
                f"Total ACB score is {anticholinergic_score.score}. ACB totals of 3 or more are associated "
                "with clinically meaningful anticholinergic burden."
            ),
            suggested_pharmacist_discussion=(
                "Discuss the cumulative anticholinergic burden with the prescriber and ask whether lower-burden "
                "alternatives or deprescribing with an appropriate plan should be reviewed."
            ),
            tapering_caution=None,
            evidence_sources=[ACB_SOURCE, BEERS_2023_SOURCE],
            confidence=0.9,
        ), active_meds))

    if sedative_score.score >= 2:
        findings.append(_with_taper_caution(Finding(
            category="sedative_fall_risk_burden",
            priority="high" if sedative_score.score >= 3 else "moderate",
            drugs_involved=sedative_score.drugs,
            explanation=(
                f"{sedative_score.score} concurrent sedating or fall-risk medications were identified. "
                "Concurrent CNS-active medications can increase sedation, impaired coordination, and fall risk."
            ),
            suggested_pharmacist_discussion=(
                "Discuss fall-risk mitigation and whether each sedating medication remains necessary; if a "
                "taper-required agent is changed, discuss a taper plan with the prescriber."
            ),
            tapering_caution=None,
            evidence_sources=[STOPP_START_V2_SOURCE, BEERS_2023_SOURCE],
            confidence=0.86,
        ), active_meds))

    if context.age is None:
        missing_information.append("patient age/date of birth not available for Beers/STOPP PIM screening")
    elif context.age >= 65:
        findings.extend(_pim_findings(active_meds))

    findings.extend(_duplication_findings(active_meds))
    findings.extend(_cascade_findings(active_meds))
    findings.extend(_bleeding_findings(active_meds))

    unclear_indication_findings, unclear_missing = _unclear_indication_findings(active_meds, context)
    findings.extend(unclear_indication_findings)
    missing_information.extend(unclear_missing)

    findings.extend(_underprescribing_findings(active_meds, context))

    all_missing = sorted({
        item
        for item in [*missing_information, *(missing for finding in findings for missing in finding.missing_information)]
        if item
    })
    return ReviewResult(
        anticholinergic_burden=anticholinergic_score,
        sedative_fall_risk=sedative_score,
        findings=sorted(findings, key=lambda finding: PRIORITY_ORDER[finding.priority]),
        missing_information=all_missing,
    )


def _is_active(med: PolyMedication) -> bool:
    status = (med.status or "active").strip().lower()
    return status not in INACTIVE_STATUSES


def _tokens(med: PolyMedication) -> set[str]:
    normalized = med.normalized_name.strip().lower()
    return {normalized, *med.classes, *CLASS_ALIASES.get(normalized, set())}


def _matches(med: PolyMedication, candidates: set[str]) -> bool:
    return bool(_tokens(med) & candidates)


def _drug_names(meds: list[PolyMedication]) -> list[str]:
    return [med.drug_name for med in meds]


def _anticholinergic_burden(meds: list[PolyMedication]) -> BurdenScore:
    contributors: list[str] = []
    total = 0
    for med in meds:
        score = ANTICHOLINERGIC_BURDEN.get(med.normalized_name)
        if not score:
            continue
        total += score
        contributors.append(f"{med.drug_name} (ACB {score})")
    return BurdenScore(score=total, drugs=contributors)


def _sedative_burden(meds: list[PolyMedication]) -> BurdenScore:
    contributors = [med.drug_name for med in meds if _matches(med, SEDATIVE_FALL_RISK)]
    return BurdenScore(score=len(contributors), drugs=contributors)


def _pim_findings(meds: list[PolyMedication]) -> list[Finding]:
    findings: list[Finding] = []
    for rule in PIM_RULES:
        matched = [med for med in meds if _matches(med, rule.matches)]
        if not matched:
            continue
        findings.append(_with_taper_caution(Finding(
            category="potentially_inappropriate_medication",
            priority=rule.priority,
            drugs_involved=_drug_names(matched),
            explanation=rule.explanation,
            suggested_pharmacist_discussion=(
                "Discuss whether the medication remains appropriate for this older adult and whether safer "
                "alternatives or a deprescribing plan should be reviewed with the prescriber."
            ),
            tapering_caution=None,
            evidence_sources=rule.evidence_sources,
            confidence=rule.confidence,
        ), matched))
    return findings


def _duplication_findings(meds: list[PolyMedication]) -> list[Finding]:
    by_class: dict[str, list[PolyMedication]] = defaultdict(list)
    for med in meds:
        for class_name in sorted(med.classes):
            by_class[class_name].append(med)

    findings: list[Finding] = []
    for class_name, class_meds in by_class.items():
        unique_names = {med.normalized_name for med in class_meds}
        if len(unique_names) < 2:
            continue
        findings.append(_with_taper_caution(Finding(
            category="therapeutic_duplication",
            priority="moderate",
            drugs_involved=_drug_names(class_meds),
            explanation=f"Multiple active medications share the {class_name.replace('_', ' ')} class.",
            suggested_pharmacist_discussion=(
                "Confirm whether this is intentional combination therapy or a medication-list reconciliation issue; "
                "discuss consolidation or a deprescribing plan with the prescriber when appropriate."
            ),
            tapering_caution=None,
            evidence_sources=["Medication therapy management reconciliation best practice; duplicate therapy screening"],
            confidence=0.88,
        ), class_meds))
    return findings


def _cascade_findings(meds: list[PolyMedication]) -> list[Finding]:
    findings: list[Finding] = []
    for rule in PRESCRIBING_CASCADES:
        triggers = [med for med in meds if _matches(med, rule.trigger_matches)]
        treating = [med for med in meds if _matches(med, rule.treating_matches)]
        if not triggers or not treating:
            continue
        involved = [*triggers, *treating]
        findings.append(_with_taper_caution(Finding(
            category="prescribing_cascade",
            priority="moderate",
            drugs_involved=_drug_names(involved),
            explanation=f"{rule.explanation} Potential cascade effect: {rule.effect}.",
            suggested_pharmacist_discussion=(
                "Consider whether the second drug is treating a side effect of the first and request prescriber "
                "review of the indication, timing, and alternatives."
            ),
            tapering_caution=None,
            evidence_sources=rule.evidence_sources,
            confidence=rule.confidence,
        ), involved))
    return findings


def _bleeding_findings(meds: list[PolyMedication]) -> list[Finding]:
    nsaids = [med for med in meds if _matches(med, {"nsaid", "ibuprofen", "naproxen", "diclofenac", "meloxicam", "celecoxib"})]
    anticoagulants = [med for med in meds if _matches(med, {"anticoagulant", "warfarin", "apixaban", "rivaroxaban", "dabigatran", "edoxaban"})]
    antiplatelets = [med for med in meds if _matches(med, {"antiplatelet", "aspirin", "clopidogrel", "prasugrel", "ticagrelor"})]

    findings: list[Finding] = []
    if nsaids and anticoagulants:
        findings.append(_bleeding_finding([*nsaids, *anticoagulants], "NSAID plus anticoagulant"))
    if antiplatelets and anticoagulants:
        findings.append(_bleeding_finding([*antiplatelets, *anticoagulants], "antiplatelet plus anticoagulant"))
    if len({med.normalized_name for med in antiplatelets}) >= 2:
        findings.append(_bleeding_finding(antiplatelets, "dual antiplatelet therapy"))
    return findings


def _bleeding_finding(meds: list[PolyMedication], combo: str) -> Finding:
    return _with_taper_caution(Finding(
        category="bleeding_risk_combination",
        priority="high",
        drugs_involved=_drug_names(meds),
        explanation=f"{combo} can increase clinically significant bleeding risk.",
        suggested_pharmacist_discussion=(
            "Discuss bleeding risk, indication, duration, gastroprotection, and monitoring with the prescriber."
        ),
        tapering_caution=None,
        evidence_sources=[BLEEDING_SOURCE, BEERS_2023_SOURCE],
        confidence=0.9,
    ), meds)


def _unclear_indication_findings(
    meds: list[PolyMedication],
    context: PolyContext,
) -> tuple[list[Finding], list[str]]:
    missing: list[str] = []
    unclear: list[PolyMedication] = []
    condition_tokens = _condition_tokens(context.conditions)
    for med in meds:
        indication = (med.indication or "").strip().lower()
        if indication and indication not in UNKNOWN_INDICATIONS:
            continue
        if _med_maps_to_condition(med, condition_tokens):
            continue
        unclear.append(med)
        missing.append(f"indication for {med.drug_name} not documented")

    if not unclear:
        return [], missing

    return [Finding(
        category="unclear_indication",
        priority="low",
        drugs_involved=_drug_names(unclear),
        explanation="One or more active medications do not have a documented indication in the available profile.",
        suggested_pharmacist_discussion=(
            "Clarify indication, therapeutic goal, duration, and whether continued therapy should be reviewed with the prescriber."
        ),
        tapering_caution=None,
        evidence_sources=["Medication therapy management medication-list reconciliation standard"],
        confidence=0.68,
        missing_information=missing,
    )], missing


def _underprescribing_findings(meds: list[PolyMedication], context: PolyContext) -> list[Finding]:
    present_tokens = set().union(*(_tokens(med) for med in meds)) if meds else set()
    conditions = _condition_tokens(context.conditions)
    findings: list[Finding] = []
    for rule in EXPECTED_THERAPY:
        if not conditions & rule.condition_matches:
            continue
        if rule.min_age is not None and (context.age is None or context.age < rule.min_age) and not (conditions & rule.any_condition_matches):
            continue
        if present_tokens & rule.expected_matches:
            continue
        findings.append(Finding(
            category="possible_underprescribing",
            priority="low",
            drugs_involved=[],
            explanation=rule.explanation,
            suggested_pharmacist_discussion=(
                "Consider discussing this possible therapy gap with the prescriber, including contraindications, "
                "patient preference, prior intolerance, and whether therapy has already been addressed elsewhere."
            ),
            tapering_caution=None,
            evidence_sources=rule.evidence_sources,
            confidence=rule.confidence,
        ))
    return findings


def _condition_tokens(conditions: list[str]) -> set[str]:
    tokens: set[str] = set()
    for condition in conditions:
        cleaned = re.sub(r"[^a-z0-9]+", "_", condition.lower()).strip("_")
        if cleaned:
            tokens.add(cleaned)
        spaced = re.sub(r"[^a-z0-9]+", " ", condition.lower()).strip()
        if spaced:
            tokens.add(spaced)
    return tokens


def _med_maps_to_condition(med: PolyMedication, conditions: set[str]) -> bool:
    tokens = _tokens(med)
    condition_map = {
        "hypertension": {"ace_inhibitor", "angiotensin_receptor_blocker", "beta_blocker", "calcium_channel_blocker", "thiazide_diuretic"},
        "high_blood_pressure": {"ace_inhibitor", "angiotensin_receptor_blocker", "beta_blocker", "calcium_channel_blocker", "thiazide_diuretic"},
        "diabetes": {"biguanide", "insulin", "metformin"},
        "type_2_diabetes": {"biguanide", "insulin", "metformin"},
        "atrial_fibrillation": {"anticoagulant"},
        "afib": {"anticoagulant"},
        "hyperlipidemia": {"statin"},
        "coronary_artery_disease": {"statin", "beta_blocker", "antiplatelet"},
        "cad": {"statin", "beta_blocker", "antiplatelet"},
        "heart_failure": {"ace_inhibitor", "angiotensin_receptor_blocker", "beta_blocker", "loop_diuretic", "potassium_sparing_diuretic"},
        "heart_failure_ref": {"ace_inhibitor", "angiotensin_receptor_blocker", "beta_blocker", "loop_diuretic", "potassium_sparing_diuretic"},
        "gout": {"allopurinol", "febuxostat"},
        "depression": {"ssri", "snri"},
        "anxiety": {"ssri", "snri", "benzodiazepine"},
        "pain": {"nsaid", "opioid", "serotonergic_opioid", "gabapentinoid"},
        "gerd": {"ppi"},
    }
    return any(tokens & expected for condition, expected in condition_map.items() if condition in conditions)


def _with_taper_caution(finding: Finding, meds: list[PolyMedication]) -> Finding:
    if not any(_matches(med, TAPER_REQUIRED) for med in meds):
        return finding
    sources = list(dict.fromkeys([*finding.evidence_sources, TAPER_SOURCE]))
    return Finding(
        category=finding.category,
        priority=finding.priority,
        drugs_involved=finding.drugs_involved,
        explanation=finding.explanation,
        suggested_pharmacist_discussion=finding.suggested_pharmacist_discussion,
        tapering_caution=TAPERING_CAUTION,
        evidence_sources=sources,
        confidence=finding.confidence,
        missing_information=finding.missing_information,
    )
