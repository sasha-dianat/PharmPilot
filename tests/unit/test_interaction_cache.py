from types import SimpleNamespace as NS

from services.ai.clinical_decision_support.interaction.review_set import (
    assemble_review_set, review_set_hash,
)
from services.ai.clinical_decision_support.interaction.engine import evaluate
from services.ai.clinical_decision_support.interaction.report import (
    findings_hash, serious_findings, report_to_dict,
)
from services.ai.clinical_decision_support.interaction.severity import InteractionSeverity as S


def _rs(drugs, conditions=None, labs=None):
    return assemble_review_set(current_rx=[NS(drug_name=d) for d in drugs], meds=[],
                               conditions=conditions or [], allergies=[], labs=labs or {})


def test_review_set_hash_stable_and_sensitive():
    a = review_set_hash(_rs(["warfarin", "ibuprofen"]))
    b = review_set_hash(_rs(["ibuprofen", "warfarin"]))   # order-independent
    assert a == b
    assert a != review_set_hash(_rs(["warfarin"]))                       # drug change
    assert a != review_set_hash(_rs(["warfarin", "ibuprofen"], conditions=["x"]))  # condition change
    assert a != review_set_hash(_rs(["warfarin", "ibuprofen"], labs={"potassium": 5.6}))  # lab change


def test_findings_hash_covers_only_serious():
    rep = evaluate(_rs(["warfarin", "ibuprofen"]))     # Major drug-drug
    assert serious_findings(rep)                         # at least one Major/Contra
    h1 = findings_hash(rep)
    rep2 = evaluate(_rs(["warfarin"]))                   # no serious pair
    assert findings_hash(rep2) != h1


def test_report_to_dict_shape():
    d = report_to_dict(evaluate(_rs(["warfarin", "ibuprofen"])))
    assert set(d) >= {"summary", "findings", "degraded"}
    assert d["findings"][0]["severity"] in {s.value for s in S}
