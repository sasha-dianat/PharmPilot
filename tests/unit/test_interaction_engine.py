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


def test_phenoconversion_codeine_efficacy_loss():
    rep = evaluate(_rs(["paroxetine", "codeine"]))   # strong 2D6 inhibitor + 2D6 prodrug
    f = [x for x in rep.findings if x.direction == "efficacy_loss"
         and {p["name"] for p in x.participants} == {"paroxetine", "codeine"}]
    assert f


def test_transporter_pgp_digoxin():
    rep = evaluate(_rs(["clarithromycin", "digoxin"]))   # P-gp inhibitor + P-gp substrate
    assert any("P-gp" in (f.mechanism_basis or "") for f in rep.findings)


def test_absorption_chelation_separation_action():
    rep = evaluate(_rs(["levothyroxine", "calcium carbonate"]))
    f = [x for x in rep.findings if x.type == "drug_drug" and "separate" in
         " ".join(x.suggested_actions).lower()]
    assert f


def test_renal_competition_lithium():
    rep = evaluate(_rs(["ibuprofen", "lithium"]))
    assert any(f.direction == "toxicity" and {p["name"] for p in f.participants} ==
               {"ibuprofen", "lithium"} for f in rep.findings)


def test_pd_additive_bleeding():
    rep = evaluate(_rs(["warfarin", "paroxetine"]))   # both bleeding axis, no explicit rule
    assert any(f.type == "drug_drug" and f.direction == "additive_risk" and
               "bleeding" in f.mechanism.lower() for f in rep.findings)


def test_pd_opposition_nsaid_antihypertensive():
    rep = evaluate(_rs(["ibuprofen", "lisinopril"]))
    assert any(f.direction == "opposition" for f in rep.findings)


def test_maoi_serotonergic_contraindicated():
    rep = evaluate(_rs(["phenelzine", "paroxetine"]))
    assert any(f.severity is S.CONTRAINDICATED for f in rep.findings)


def _rs_hist(current, historical_name, days_ago, status="discontinued"):
    from datetime import date, timedelta
    return assemble_review_set(
        current_rx=[NS(drug_name=d) for d in current],
        meds=[NS(normalized_name=historical_name, drug_name=historical_name,
                 status=status, start_date=date.today() - timedelta(days=days_ago))],
        conditions=[], allergies=[])


def test_stale_historical_drug_drug_suppressed():
    from datetime import date
    rep = evaluate(_rs_hist(["simvastatin"], "clarithromycin", days_ago=400), now=date.today())
    assert not any({p["name"] for p in f.participants} == {"clarithromycin", "simvastatin"}
                   for f in rep.findings)


def test_long_acting_bypasses_cutoff():
    from datetime import date
    rep = evaluate(_rs_hist(["simvastatin"], "amiodarone", days_ago=400), now=date.today())
    assert any({p["name"] for p in f.participants} == {"amiodarone", "simvastatin"}
               for f in rep.findings)
