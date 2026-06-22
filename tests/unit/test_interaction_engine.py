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
