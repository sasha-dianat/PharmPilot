from services.ai.clinical_decision_support.interaction.attributes import load_attribute_index
from services.ai.clinical_decision_support.interaction.mechanism import (
    metabolic_interaction, fm_bin,
)
from services.ai.clinical_decision_support.interaction.severity import InteractionSeverity as S

ATTR = load_attribute_index()


def test_fm_bin():
    assert fm_bin(0.8) == "high" and fm_bin(0.4) == "low" and fm_bin(None) == "low"


def test_inhibitor_parent_substrate_is_toxicity_major():
    f = metabolic_interaction(ATTR.get("clarithromycin"), ATTR.get("simvastatin"))
    assert f is not None
    assert f["direction"] == "toxicity"
    assert f["base_severity"] is S.MAJOR
    assert f["severity"] is S.CONTRAINDICATED          # NTI victim → +1 step
    assert "5" in f["predicted_magnitude"]


def test_inhibitor_prodrug_is_efficacy_loss():
    f = metabolic_interaction(ATTR.get("omeprazole"), ATTR.get("clopidogrel"))
    assert f is not None and f["direction"] == "efficacy_loss"


def test_no_shared_enzyme_returns_none():
    # rifampin induces 3A4; warfarin substrate is 2C9 → no overlap → None
    f = metabolic_interaction(ATTR.get("rifampin"), ATTR.get("warfarin"))
    assert f is None
