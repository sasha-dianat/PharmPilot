"""
Clinical-validation tests for the hereditary / familial risk engine.
Focus on patient-safety behavior: G6PD/favism, HAE, long-QT, FH, consent gating,
and the provenance requirement ("based on relative X").
"""
from services.ai.clinical_brain.hereditary.engine import (
    HereditaryRiskEngine, canonicalize_condition,
)


def _eng():
    return HereditaryRiskEngine()


# ── G6PD / favism (high Iranian relevance) ────────────────────────────────────

def test_g6pd_self_plus_oxidant_drug_is_blocker():
    f = _eng().evaluate("Nitrofurantoin 100mg",
                        {"inherited_conditions": ["G6PD deficiency"], "diagnoses": []}, [])
    assert len(f) == 1
    assert f[0].severity == "blocker"
    assert "self" in f[0].provenance


def test_g6pd_family_only_is_caution_with_provenance():
    f = _eng().evaluate("co-trimoxazole",
                        {"inherited_conditions": [], "diagnoses": []},
                        [{"relationship": "brother", "diagnoses": ["فاویسم"]}])
    assert len(f) == 1
    assert f[0].severity == "caution"
    assert f[0].provenance == ["relative: brother — فاویسم"]


def test_g6pd_no_oxidant_drug_no_finding():
    f = _eng().evaluate("Amlodipine 5mg",
                        {"inherited_conditions": ["g6pd deficiency"], "diagnoses": []}, [])
    assert f == []


def test_persian_favism_synonym_recognized():
    assert canonicalize_condition("فاویسم") == "g6pd_deficiency"
    assert canonicalize_condition("کمبود g6pd") == "g6pd_deficiency"


# ── Hereditary angioedema + ACE inhibitor ─────────────────────────────────────

def test_hae_self_plus_ace_inhibitor_is_blocker():
    f = _eng().evaluate("Lisinopril 10mg",
                        {"inherited_conditions": ["hereditary angioedema"], "diagnoses": []}, [])
    assert any(x.severity == "blocker" and "angioedema" in x.condition.lower() for x in f)


# ── Congenital long-QT ────────────────────────────────────────────────────────

def test_long_qt_family_plus_qt_drug_is_caution():
    f = _eng().evaluate("Azithromycin 250mg",
                        {"diagnoses": []},
                        [{"relationship": "mother", "diagnoses": ["congenital long QT"]}])
    assert any(x.severity == "caution" and "long-QT" in x.condition for x in f)


# ── Consent gating ────────────────────────────────────────────────────────────

def test_family_consent_off_suppresses_family_findings():
    fam = [{"relationship": "mother", "diagnoses": ["hereditary angioedema"]}]
    f = _eng().evaluate("Lisinopril", {"diagnoses": []}, fam, family_consent=False)
    assert f == []


def test_consent_off_still_evaluates_patient_own_status():
    f = _eng().evaluate("Nitrofurantoin",
                        {"inherited_conditions": ["g6pd deficiency"]}, [],
                        family_consent=False)
    assert any(x.severity == "blocker" for x in f)


# ── Pharmacogenomics ──────────────────────────────────────────────────────────

def test_codeine_triggers_cyp2d6_prompt():
    f = _eng().evaluate("Codeine/Acetaminophen", {"diagnoses": []}, [])
    assert any("CYP2D6" in x.condition for x in f)


def test_clopidogrel_triggers_cyp2c19_prompt():
    f = _eng().evaluate("Clopidogrel 75mg", {"diagnoses": []}, [])
    assert any("CYP2C19" in x.condition for x in f)


# ── Familial hypercholesterolemia ─────────────────────────────────────────────

def test_fh_with_statin_is_reinforcing_counseling():
    f = _eng().evaluate("Atorvastatin 40mg",
                        {"diagnoses": ["familial hypercholesterolemia"]}, [])
    assert any(x.severity == "counseling" and "hypercholesterolemia" in x.condition.lower() for x in f)
