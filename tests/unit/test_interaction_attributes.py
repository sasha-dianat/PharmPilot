from services.ai.clinical_decision_support.interaction.attributes import (
    load_attribute_index, AttributeIndex,
)


def test_index_loads_seed():
    idx = load_attribute_index()
    assert isinstance(idx, AttributeIndex)
    assert idx.get("simvastatin") is not None


def test_substrate_enzyme_parsed():
    idx = load_attribute_index()
    simva = idx.get("simvastatin")
    sub = [e for e in simva.enzymes if e.role == "substrate"][0]
    assert sub.enzyme == "CYP3A4" and sub.fm == 0.8 and sub.yields == "parent"
    assert simva.nti.is_nti is True and simva.nti.consequence == "toxicity"


def test_inhibitor_and_tdi_flag():
    idx = load_attribute_index()
    clarith = idx.get("clarithromycin")
    inh = clarith.enzymes[0]
    assert inh.role == "inhibitor" and inh.strength == "strong"
    assert inh.inhibition_type == "mechanism_based"


def test_prodrug_and_absorption_and_long_acting():
    idx = load_attribute_index()
    assert idx.get("clopidogrel").prodrug is True
    assert idx.get("clopidogrel").activating_enzyme == "CYP2C19"
    assert idx.get("levothyroxine").absorption.chelation_cations == ["Ca", "Mg", "Al", "Fe"]
    assert idx.get("amiodarone").long_acting is True


def test_unknown_returns_none():
    assert load_attribute_index().get("not-a-drug") is None
