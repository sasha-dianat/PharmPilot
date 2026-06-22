from types import SimpleNamespace as NS

from services.ai.clinical_decision_support.interaction.engine import evaluate
from services.ai.clinical_decision_support.interaction.review_set import assemble_review_set
from services.ai.clinical_decision_support.interaction.severity import InteractionSeverity as S


def _rs(drugs, conditions=None, allergies=None):
    return assemble_review_set(current_rx=[NS(drug_name=d) for d in drugs],
                               meds=[], conditions=conditions or [], allergies=allergies or [])


def test_explicit_drug_drug_pair():
    rep = evaluate(_rs(["warfarin", "ibuprofen"]))
    dd = [f for f in rep.findings if f.type == "drug_drug" and f.source == "curated"]
    assert dd and dd[0].severity is S.MAJOR


def test_drug_disease():
    rep = evaluate(_rs(["ibuprofen"], conditions=["peptic_ulcer_disease"]))
    assert any(f.type == "drug_disease" for f in rep.findings)


def test_duplicate_therapy_same_class():
    rep = evaluate(_rs(["ibuprofen", "naproxen"]))
    assert any(f.type == "duplicate_therapy" for f in rep.findings)


def test_drug_allergy():
    rep = evaluate(_rs(["amoxicillin"], allergies=["penicillin"]))
    assert any(f.type == "drug_allergy" for f in rep.findings)


def test_inferred_pk_when_no_explicit_rule():
    # clarithromycin + simvastatin: explicit rule EXISTS → source curated, inferred suppressed
    rep = evaluate(_rs(["clarithromycin", "simvastatin"]))
    dd = [f for f in rep.findings if {p["name"] for p in f.participants} ==
          {"clarithromycin", "simvastatin"}]
    assert len(dd) == 1 and dd[0].source == "curated"     # precedence: curated > inferred


def test_inferred_pk_emitted_without_explicit_rule():
    # amiodarone (mod 3A4 inhibitor) + simvastatin (3A4 substrate) has no curated rule here
    rep = evaluate(_rs(["amiodarone", "simvastatin"]))
    inf = [f for f in rep.findings if f.source == "inferred_mechanistic"
           and {p["name"] for p in f.participants} == {"amiodarone", "simvastatin"}]
    assert inf and inf[0].evidence_grade == "Predicted"
    assert inf[0].direction == "toxicity"
