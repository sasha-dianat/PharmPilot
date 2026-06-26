from services.ai.clinical_decision_support.normalizer import normalize


def test_organic_actives_get_salt_stripped():
    assert normalize("tizanidine hcl 4mg") == "tizanidine"
    assert normalize("warfarin sodium") == "warfarin"
    assert normalize("metoprolol succinate") == "metoprolol"
    assert normalize("sildenafil citrate") == "sildenafil"
    assert normalize("lithium carbonate") == "lithium"


def test_mineral_cation_anion_preserved():
    # calcium citrate and calcium carbonate are DIFFERENT products — must not collapse
    assert normalize("calcium citrate") != normalize("calcium carbonate")
    assert normalize("calcium citrate") == "calcium citrate"
    assert normalize("calcium carbonate") == "calcium carbonate"
    assert normalize("ferrous sulfate") == "ferrous sulfate"
    assert normalize("sodium chloride") == "sodium chloride"


def test_known_drugs_unchanged():
    assert normalize("Coumadin 5 mg") == "warfarin"
    assert normalize("clarithromycin") == "clarithromycin"
