from services.ai.clinical_decision_support.interaction.rules import load_rule_index
from services.ai.clinical_decision_support.interaction.severity import InteractionSeverity as S


def test_drug_drug_lookup_is_order_independent():
    idx = load_rule_index()
    r1 = idx.find_drug_drug({"clarithromycin"}, {"simvastatin"})
    r2 = idx.find_drug_drug({"simvastatin"}, {"clarithromycin"})
    assert r1 and r2 and r1[0].severity is S.CONTRAINDICATED


def test_class_level_match():
    idx = load_rule_index()
    hits = idx.find_drug_drug({"warfarin", "anticoagulant"}, {"ibuprofen", "nsaid"})
    assert any(h.severity is S.MAJOR for h in hits)


def test_drug_disease_lookup():
    idx = load_rule_index()
    hits = idx.find_drug_disease({"ibuprofen", "nsaid"}, "peptic_ulcer_disease")
    assert hits and hits[0].severity is S.MAJOR
