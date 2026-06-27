from services.ai.clinical_decision_support.interaction.report import (
    Finding, InteractionReport, build_report, report_to_dict,
)
from services.ai.clinical_decision_support.interaction.severity import InteractionSeverity as S


def _f(sev, prov="current_rx"):
    return Finding(
        rule_id="r", type="drug_drug", severity=sev, base_severity=sev,
        direction="toxicity", predicted_magnitude=None, onset_offset=None,
        participants=[{"name": "a", "kind": "drug", "provenance": prov, "last_seen": None}],
        mechanism="m", mechanism_basis=None, clinical_problem="p",
        suggested_actions=["a"], evidence_sources=["e"], evidence_grade="Established",
        source="curated", patient_specific_factors=[], confidence=0.9, recency_note=None,
    )


def _pair(sev, provs):
    """Finding with two drug participants of the given provenances."""
    parts = [{"name": n, "kind": "drug", "provenance": p, "last_seen": None}
             for n, p in zip(("x", "y"), provs)]
    f = _f(sev)
    f.participants = parts
    return f


def test_report_summary_and_sort():
    rep = build_report([_f(S.MINOR), _f(S.CONTRAINDICATED), _f(S.MODERATE)])
    assert rep.summary == {"Contraindicated": 1, "Major": 0, "Moderate": 1, "Minor": 1}
    assert [f.severity for f in rep.findings][0] is S.CONTRAINDICATED   # severity desc
    assert rep.degraded is False


def test_degraded_flag():
    rep = build_report([], degraded=True)
    assert rep.degraded is True and rep.findings == []


def test_section_tags_dispense_vs_profile():
    # involves a current_rx drug -> 'dispense'; metformin-only (active) -> 'profile'
    dispense = _pair(S.MAJOR, ("current_rx", "active"))     # e.g. omeprazole × other
    profile = _f(S.CONTRAINDICATED, prov="active")          # e.g. metformin renal alone
    d = report_to_dict(build_report([dispense, profile]))
    by_rule = {tuple(sorted(p["name"] for p in f["participants"])): f["section"]
               for f in d["findings"]}
    assert by_rule[("x", "y")] == "dispense"
    assert by_rule[("a",)] == "profile"
