from __future__ import annotations

from itertools import combinations

from .attributes import AttributeIndex, load_attribute_index
from .mechanism import metabolic_interaction
from .report import Finding, InteractionReport, build_report
from .review_set import ReviewSet
from .rules import RuleIndex, load_rule_index
from .severity import InteractionSeverity

CROSS_REACTIVITY = {"penicillin": {"penicillin", "cephalosporin"}}


def _finding(*, rule_id, type, severity, direction, mechanism, actions, evidence,
             grade, source, participants, base=None, magnitude=None, onset=None,
             basis=None, factors=None, confidence=0.85, recency=None,
             clinical_problem="") -> Finding:
    return Finding(
        rule_id=rule_id, type=type, severity=severity, base_severity=base or severity,
        direction=direction, predicted_magnitude=magnitude, onset_offset=onset,
        participants=participants, mechanism=mechanism, mechanism_basis=basis,
        clinical_problem=clinical_problem or mechanism, suggested_actions=actions,
        evidence_sources=list(evidence), evidence_grade=grade, source=source,
        patient_specific_factors=factors or [], confidence=confidence, recency_note=recency,
    )


def _p(med):
    return {"name": med.normalized_name, "kind": "drug", "provenance": med.provenance,
            "last_seen": med.last_seen_date.isoformat() if med.last_seen_date else None}


def _explicit_drug_drug(rs: ReviewSet, rules: RuleIndex) -> list[Finding]:
    out = []
    for a, b in combinations(rs.meds, 2):
        for r in rules.find_drug_drug(set(a.classes), set(b.classes)):
            out.append(_finding(
                rule_id=f"dd:{r.left}-{r.right}", type="drug_drug", severity=r.severity,
                direction="toxicity", mechanism=r.mechanism, actions=[r.action],
                evidence=r.evidence, grade="Established", source=r.source,
                participants=[_p(a), _p(b)], confidence=r.confidence))
    return out


def _drug_disease(rs: ReviewSet, rules: RuleIndex) -> list[Finding]:
    out = []
    for med in rs.meds:
        for cond in rs.conditions:
            for r in rules.find_drug_disease(set(med.classes), cond.concept):
                out.append(_finding(
                    rule_id=f"ddz:{r.left}-{r.right}", type="drug_disease", severity=r.severity,
                    direction="toxicity", mechanism=r.mechanism, actions=[r.action],
                    evidence=r.evidence, grade="Established", source=r.source,
                    participants=[_p(med), {"name": cond.concept, "kind": "condition",
                                            "provenance": cond.status, "last_seen": None}],
                    confidence=r.confidence))
    return out


def _duplicate_therapy(rs: ReviewSet) -> list[Finding]:
    out = []
    for a, b in combinations(rs.meds, 2):
        if a.normalized_name == b.normalized_name:
            continue
        shared = (set(a.classes) & set(b.classes)) - {a.normalized_name, b.normalized_name}
        if shared:
            out.append(_finding(
                rule_id=f"dup:{sorted(shared)[0]}", type="duplicate_therapy",
                severity=InteractionSeverity.MODERATE, direction="additive_risk",
                mechanism=f"Therapeutic duplication: both are {sorted(shared)[0]}.",
                actions=["Confirm intentional; risk of additive class effects/overdose."],
                evidence=["Therapeutic duplication"], grade="Established", source="curated",
                participants=[_p(a), _p(b)], confidence=0.8))
    return out


def _drug_allergy(rs: ReviewSet) -> list[Finding]:
    from services.ai.clinical_decision_support.normalizer import normalize, classes_of
    out = []
    allergy_classes = set()
    for a in rs.allergies:
        allergy_classes |= {normalize(a), *classes_of(a)}
        allergy_classes |= CROSS_REACTIVITY.get(normalize(a), set())
    for med in rs.meds:
        if set(med.classes) & allergy_classes:
            out.append(_finding(
                rule_id="allergy", type="drug_allergy", severity=InteractionSeverity.MAJOR,
                direction="toxicity",
                mechanism=f"{med.normalized_name} overlaps a documented allergy class.",
                actions=["Verify allergy history; consider non-cross-reactive alternative."],
                evidence=["Allergy cross-reactivity"], grade="Established", source="curated",
                participants=[_p(med)], confidence=0.85))
    return out


def _inferred_pk(rs: ReviewSet, attrs: AttributeIndex) -> list[Finding]:
    out = []
    for a, b in combinations(rs.meds, 2):
        aa, ba = attrs.get(a.normalized_name), attrs.get(b.normalized_name)
        if not aa or not ba:
            continue
        for perp, victim, pm, vm in ((aa, ba, a, b), (ba, aa, b, a)):
            m = metabolic_interaction(perp, victim)
            if not m:
                continue
            out.append(_finding(
                rule_id=f"pk:{m['enzyme']}:{perp.ingredient}->{victim.ingredient}",
                type="drug_drug", severity=m["severity"], base=m["base_severity"],
                direction=m["direction"], magnitude=m["predicted_magnitude"],
                onset=m["onset_offset"], basis=m["mechanism_basis"],
                mechanism=f"{perp.ingredient}: {m['mechanism_basis']} → {victim.ingredient}",
                actions=["Review need; monitor for the predicted effect or adjust dose."],
                evidence=["Mechanistic inference (PK)"], grade="Predicted",
                source="inferred_mechanistic", participants=[_p(pm), _p(vm)],
                factors=m["patient_specific_factors"], confidence=0.6))
    return out


def _dedup(findings: list[Finding]) -> list[Finding]:
    _SRC = {"curated": 0, "ddinter": 1, "inferred_mechanistic": 2}
    best: dict[tuple, Finding] = {}
    for f in findings:
        key = (f.type, frozenset(p["name"] for p in f.participants))
        cur = best.get(key)
        if cur is None or (_SRC[f.source], -f.severity.rank) < (_SRC[cur.source], -cur.severity.rank):
            best[key] = f
    return list(best.values())


def evaluate(rs: ReviewSet, *, rules: RuleIndex | None = None,
             attrs: AttributeIndex | None = None) -> InteractionReport:
    try:
        rules = rules or load_rule_index()
        attrs = attrs or load_attribute_index()
    except Exception:
        return build_report([], degraded=True)
    findings: list[Finding] = []
    findings += _explicit_drug_drug(rs, rules)
    findings += _inferred_pk(rs, attrs)
    findings += _drug_disease(rs, rules)
    findings += _duplicate_therapy(rs)
    findings += _drug_allergy(rs)
    return build_report(_dedup(findings))
