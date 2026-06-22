from __future__ import annotations

from datetime import date
from itertools import combinations

from .attributes import AttributeIndex, load_attribute_index
from .mechanism import (
    metabolic_interaction, phenoconversion, transporter_interaction,
    absorption_interaction, renal_competition,
)
from .pd import pd_interactions
from .report import Finding, InteractionReport, build_report
from .review_set import ReviewSet
from .rules import RuleIndex, load_rule_index
from .severity import InteractionSeverity

MODEL_VERSION = "interaction-v1"

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


def _apply_lab_escalation(rule, base: InteractionSeverity, labs: dict) -> InteractionSeverity:
    esc = rule.lab_escalation
    if not esc:
        return base
    val = labs.get(esc["lab"])
    if val is None:
        return base
    for s in esc["steps"]:               # steps are highest-threshold first
        if val >= s["min"]:
            return s["severity"]
    return base


def _explicit_drug_drug(rs: ReviewSet, rules: RuleIndex) -> list[Finding]:
    out = []
    for a, b in combinations(rs.meds, 2):
        for r in rules.find_drug_drug(set(a.classes), set(b.classes)):
            sev = _apply_lab_escalation(r, r.severity, rs.labs)
            factors = [f"{r.lab_escalation['lab']}={rs.labs.get(r.lab_escalation['lab']):g}"] \
                if r.lab_escalation and rs.labs.get(r.lab_escalation["lab"]) is not None else []
            out.append(_finding(
                rule_id=f"dd:{r.left}-{r.right}", type="drug_drug", severity=sev,
                base=r.severity, direction="toxicity", mechanism=r.mechanism, actions=[r.action],
                evidence=r.evidence, grade="Established", source=r.source,
                participants=[_p(a), _p(b)], factors=factors, confidence=r.confidence))
    return out


def _drug_context(rs: ReviewSet, rules: RuleIndex) -> list[Finding]:
    out = []
    for med in rs.meds:
        for r in rules.find_drug_context(set(med.classes)):
            if r.requires_age_min is not None:
                if rs.age is None or rs.age < r.requires_age_min:
                    continue
                sev, factors = r.severity, [f"age {rs.age} ≥ {r.requires_age_min}"]
            elif r.requires_lab:
                val = rs.labs.get(r.requires_lab)
                if val is None:
                    sev = r.missing_severity or InteractionSeverity.MINOR
                    factors = [f"{r.requires_lab} not on file"]
                else:
                    sev = r.missing_severity or InteractionSeverity.MINOR
                    factors = [f"{r.requires_lab}={val:g}"]
                    for t in r.thresholds:
                        if val <= t["max"]:
                            sev = t["severity"]
                            break
                    else:
                        continue   # value above all thresholds → no alert
            else:
                sev, factors = r.severity, []
            out.append(_finding(
                rule_id=f"ctx:{r.left}", type="drug_context", severity=sev,
                direction="toxicity", mechanism=r.mechanism, actions=[r.action],
                evidence=r.evidence, grade="Established", source=r.source,
                participants=[_p(med)], factors=factors, confidence=r.confidence))
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


_MECHANISMS = (metabolic_interaction, phenoconversion, transporter_interaction,
               absorption_interaction, renal_competition)


def _inferred_pk(rs: ReviewSet, attrs: AttributeIndex) -> list[Finding]:
    out = []
    for a, b in combinations(rs.meds, 2):
        aa, ba = attrs.get(a.normalized_name), attrs.get(b.normalized_name)
        if not aa or not ba:
            continue
        for perp, victim, pm, vm in ((aa, ba, a, b), (ba, aa, b, a)):
            for fn in _MECHANISMS:
                m = fn(perp, victim)
                if not m:
                    continue
                out.append(_finding(
                    rule_id=f"{fn.__name__}:{perp.ingredient}->{victim.ingredient}",
                    type="drug_drug", severity=m["severity"], base=m["base_severity"],
                    direction=m["direction"], magnitude=m.get("predicted_magnitude"),
                    onset=m.get("onset_offset"), basis=m["mechanism_basis"],
                    mechanism=f"{perp.ingredient}: {m['mechanism_basis']} → {victim.ingredient}",
                    actions=[m.get("action", "Review need; monitor or adjust dose.")],
                    evidence=["Mechanistic inference"], grade="Predicted",
                    source="inferred_mechanistic", participants=[_p(pm), _p(vm)],
                    factors=m.get("patient_specific_factors", []), confidence=0.6))
    return out


def _inferred_pd(rs: ReviewSet, attrs: AttributeIndex) -> list[Finding]:
    out = []
    for a, b in combinations(rs.meds, 2):
        aa, ba = attrs.get(a.normalized_name), attrs.get(b.normalized_name)
        if not aa or not ba:
            continue
        for m in pd_interactions(aa, ba, labs=rs.labs):
            out.append(_finding(
                rule_id=f"pd:{m['axis']}:{aa.ingredient}-{ba.ingredient}", type="drug_drug",
                severity=m["severity"], direction=m["direction"], mechanism=m["mechanism"],
                actions=["Review combination; monitor for the additive/opposing effect."],
                evidence=["Mechanistic inference (PD)"], grade="Predicted",
                source="inferred_mechanistic", participants=[_p(a), _p(b)], confidence=0.6))
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


HISTORICAL_DDI_WINDOW_DAYS = 183


def _is_stale(med, attrs: AttributeIndex, now: date) -> bool:
    if med.provenance != "historical" or med.last_seen_date is None:
        return False
    a = attrs.get(med.normalized_name)
    if a and a.long_acting:
        return False
    window = HISTORICAL_DDI_WINDOW_DAYS
    if a and a.induction_offset_days:
        window = max(window, a.induction_offset_days)
    return (now - med.last_seen_date).days > window


def _suppress_stale_dd(findings: list[Finding], rs: ReviewSet,
                       attrs: AttributeIndex, now: date) -> list[Finding]:
    by_name = {m.normalized_name: m for m in rs.meds}
    kept = []
    for f in findings:
        if f.type == "drug_drug":
            meds = [by_name.get(p["name"]) for p in f.participants if p["kind"] == "drug"]
            if any(m and _is_stale(m, attrs, now) for m in meds):
                continue
        kept.append(f)
    return kept


def evaluate(rs: ReviewSet, *, rules: RuleIndex | None = None,
             attrs: AttributeIndex | None = None,
             now: date | None = None) -> InteractionReport:
    try:
        rules = rules or load_rule_index()
        attrs = attrs or load_attribute_index()
    except Exception:
        return build_report([], degraded=True)
    now = now or date.today()
    findings: list[Finding] = []
    findings += _explicit_drug_drug(rs, rules)
    findings += _drug_context(rs, rules)
    findings += _inferred_pk(rs, attrs)
    findings += _inferred_pd(rs, attrs)
    findings += _drug_disease(rs, rules)
    findings += _duplicate_therapy(rs)
    findings += _drug_allergy(rs)
    findings = _dedup(findings)
    findings = _suppress_stale_dd(findings, rs, attrs, now)
    return build_report(findings)
