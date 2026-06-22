from datetime import date
from types import SimpleNamespace as NS

from services.ai.clinical_decision_support.interaction.review_set import assemble_review_set


def test_provenance_tagging_and_classes():
    rx = [NS(drug_name="ibuprofen")]
    active = [NS(normalized_name="warfarin", drug_name="warfarin", status="active", start_date=date(2026, 1, 1))]
    historical = [NS(normalized_name="simvastatin", drug_name="simvastatin", status="discontinued", start_date=date(2023, 1, 1))]
    rs = assemble_review_set(current_rx=rx, meds=active + historical,
                             conditions=["Peptic Ulcer Disease"], allergies=["penicillin"])
    provs = {m.normalized_name: m.provenance for m in rs.meds}
    assert provs["ibuprofen"] == "current_rx"
    assert provs["warfarin"] == "active"
    assert provs["simvastatin"] == "historical"
    assert any("nsaid" in m.classes for m in rs.meds)       # class resolved via normalizer
    assert rs.conditions[0].concept == "peptic_ulcer_disease"
    assert rs.allergies == ["penicillin"]
