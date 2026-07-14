"""هوش‌یار دارو — spelling-proof keys, suggestion validation, reference plumbing."""
import pytest

from services.core.drug_catalog.enrichment import enrich_key, validate_suggestion


def test_enrich_key_folds_spelling_variants():
    # Arabic yeh/kaf, ZWNJ, case, salt words must all collapse to one key
    a = enrich_key("ویتامین آ-تداژل")
    b = enrich_key("ويتامين آ‌-تداژل")          # Arabic yeh + ZWNJ variant
    assert a == b and a
    assert enrich_key("Metformin HCL") == enrich_key("metformin hydrochloride")


def test_enrich_key_empty_is_empty():
    assert enrich_key("") == "" and enrich_key(None) == ""


def test_validate_suggestion_cleans_and_flags():
    d = {"generic": "vitamin a", "brand": "A-Tedagel", "manufacturer": "Tehran Daru",
         "country": "Iran", "dosage_form": "SOFTGEL",
         "strengths": ["25000 IU", "50000 IU"], "confidence": 0.9,
         "sources": ["https://tehrandarou.com/x"], "junk_field": 1}
    clean, errors = validate_suggestion(d)
    assert errors == []
    assert clean["dosage_form"] == "SOFTGEL" and len(clean["strengths"]) == 2
    assert "junk_field" not in clean


def test_validate_suggestion_rejects_bad_shapes():
    clean, errors = validate_suggestion({"confidence": "high", "sources": "not-a-list"})
    assert errors                                   # confidence not float, sources not list
    clean2, errors2 = validate_suggestion({"generic": "x", "confidence": 1.5, "sources": []})
    assert any("confidence" in e for e in errors2)  # out of [0,1]
