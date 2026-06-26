from types import SimpleNamespace as NS

from scripts.interaction_bundle.ddinter_builder import (
    RawInteraction, build_rules, write_bundle,
)
from services.ai.clinical_decision_support.interaction import bundle


def test_build_rules_normalizes_and_maps():
    rows = [RawInteraction("Tizanidine HCl", "Ciprofloxacin", "Major", "CYP1A2 inhibition.")]
    rules = build_rules(rows)
    assert len(rules) == 1
    r = rules[0]
    assert {r["left"], r["right"]} == {"tizanidine", "ciprofloxacin"}
    assert r["kind"] == "drug_drug" and r["severity"] == "Major"
    assert r["evidence"] == ["DDInter 2.0"] and r["confidence"] < 0.9


def test_build_rules_skips_and_dedups():
    rows = [
        RawInteraction("Aspirin", "Aspirin", "Major"),            # self-pair → skip
        RawInteraction("", "Warfarin", "Major"),                  # blank → skip
        RawInteraction("Warfarin sodium", "Ibuprofen", "Minor"),  # dup of next, lower sev
        RawInteraction("Warfarin", "Ibuprofen", "Major"),         # keep this (more severe)
    ]
    rules = build_rules(rows)
    pairs = [frozenset((r["left"], r["right"])) for r in rules]
    assert pairs.count(frozenset(("warfarin", "ibuprofen"))) == 1
    keep = [r for r in rules if {r["left"], r["right"]} == {"warfarin", "ibuprofen"}][0]
    assert keep["severity"] == "Major"


def test_write_bundle_roundtrips(tmp_path, monkeypatch):
    rules = build_rules([RawInteraction("Tizanidine HCl", "Ciprofloxacin", "Major")])
    dest = tmp_path / "bundle.sqlite"
    meta = write_bundle(rules, dest, {"ddinter": "2.0"})
    assert meta["schema_version"] == bundle.SCHEMA_VERSION and meta["rule_count"] == 1
    assert meta["attribute_count"] == 0 and meta["checksum"]
    monkeypatch.setattr(bundle, "BUNDLE_PATH", dest)
    payloads = bundle.read_rule_payloads()
    assert len(payloads) == 1 and payloads[0]["source"] == "ddinter"


def test_end_to_end_alignment(tmp_path, monkeypatch):
    """A DDInter rule built from 'Tizanidine HCl' fires on a real 'tizanidine hcl 4mg' Rx."""
    from services.ai.clinical_decision_support.interaction.engine import evaluate, reload_indexes
    from services.ai.clinical_decision_support.interaction.review_set import assemble_review_set
    from services.ai.clinical_decision_support.interaction import rules as rules_mod
    from services.ai.clinical_decision_support.interaction import attributes as attrs_mod

    dest = tmp_path / "bundle.sqlite"
    write_bundle(build_rules([RawInteraction("Tizanidine HCl", "Ciprofloxacin", "Major")]),
                 dest, {"ddinter": "2.0"})
    monkeypatch.setattr(bundle, "BUNDLE_PATH", dest)
    reload_indexes()
    rs = assemble_review_set(
        current_rx=[NS(drug_name="tizanidine hcl 4mg"), NS(drug_name="ciprofloxacin 500mg")],
        meds=[], conditions=[], allergies=[])
    rep = evaluate(rs)
    fired = [f for f in rep.findings if f.source == "ddinter"
             and {p["name"] for p in f.participants} == {"tizanidine", "ciprofloxacin"}]
    assert fired and fired[0].severity.value == "Major"
    # cleanup caches
    monkeypatch.setattr(bundle, "BUNDLE_PATH", tmp_path / "gone.sqlite")
    rules_mod.load_rule_index.cache_clear(); attrs_mod.load_attribute_index.cache_clear()
