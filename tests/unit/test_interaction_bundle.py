import json
import sqlite3
from pathlib import Path

import pytest

from services.ai.clinical_decision_support.interaction import bundle


def _make_bundle(path: Path, *, rules=None, attrs=None, schema=bundle.SCHEMA_VERSION):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE interaction_rules (id INTEGER PRIMARY KEY, source TEXT, payload TEXT)")
    con.execute("CREATE TABLE drug_attributes (ingredient TEXT PRIMARY KEY, payload TEXT)")
    con.execute("CREATE TABLE bundle_meta (key TEXT PRIMARY KEY, value TEXT)")
    for r in (rules or []):
        con.execute("INSERT INTO interaction_rules (source, payload) VALUES (?,?)", ("ddinter", json.dumps(r)))
    for a in (attrs or []):
        con.execute("INSERT INTO drug_attributes (ingredient, payload) VALUES (?,?)", (a["ingredient"], json.dumps(a)))
    con.execute("INSERT INTO bundle_meta (key, value) VALUES ('schema_version', ?)", (schema,))
    con.execute("INSERT INTO bundle_meta (key, value) VALUES ('rule_count', ?)", (str(len(rules or [])),))
    con.commit(); con.close()


def test_read_rule_payloads_injects_source(tmp_path, monkeypatch):
    p = tmp_path / "bundle.sqlite"
    _make_bundle(p, rules=[{"kind": "drug_drug", "left": "x", "right": "y", "severity": "Major"}])
    monkeypatch.setattr(bundle, "BUNDLE_PATH", p)
    out = bundle.read_rule_payloads()
    assert len(out) == 1 and out[0]["source"] == "ddinter" and out[0]["left"] == "x"


def test_read_attribute_payloads(tmp_path, monkeypatch):
    p = tmp_path / "bundle.sqlite"
    _make_bundle(p, attrs=[{"ingredient": "newdrug", "classes": ["statin"]}])
    monkeypatch.setattr(bundle, "BUNDLE_PATH", p)
    out = bundle.read_attribute_payloads()
    assert out[0]["ingredient"] == "newdrug"


def test_missing_bundle_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle, "BUNDLE_PATH", tmp_path / "nope.sqlite")
    assert bundle.read_rule_payloads() == [] and bundle.read_attribute_payloads() == []
    assert bundle.bundle_stats() == {}


def test_wrong_schema_ignored(tmp_path, monkeypatch):
    p = tmp_path / "bundle.sqlite"
    _make_bundle(p, rules=[{"kind": "drug_drug", "left": "x", "right": "y", "severity": "Major"}], schema="bundle-v999")
    monkeypatch.setattr(bundle, "BUNDLE_PATH", p)
    assert bundle.read_rule_payloads() == []


def test_corrupt_file_ignored(tmp_path, monkeypatch):
    p = tmp_path / "bundle.sqlite"
    p.write_bytes(b"not a database")
    monkeypatch.setattr(bundle, "BUNDLE_PATH", p)
    assert bundle.read_rule_payloads() == []


def test_bad_payload_row_skipped(tmp_path, monkeypatch):
    p = tmp_path / "bundle.sqlite"
    _make_bundle(p, rules=[{"kind": "drug_drug", "left": "x", "right": "y", "severity": "Major"}])
    con = sqlite3.connect(p)
    con.execute("INSERT INTO interaction_rules (source, payload) VALUES ('ddinter', 'not json')")
    con.commit(); con.close()
    monkeypatch.setattr(bundle, "BUNDLE_PATH", p)
    out = bundle.read_rule_payloads()
    assert len(out) == 1 and out[0]["left"] == "x"   # good row kept, bad row skipped


def test_bundle_stats(tmp_path, monkeypatch):
    p = tmp_path / "bundle.sqlite"
    _make_bundle(p, rules=[{"kind": "drug_drug", "left": "x", "right": "y", "severity": "Major"}])
    monkeypatch.setattr(bundle, "BUNDLE_PATH", p)
    s = bundle.bundle_stats()
    assert s.get("schema_version") == bundle.SCHEMA_VERSION and s.get("rule_count") == "1"


from types import SimpleNamespace as NS

from services.ai.clinical_decision_support.interaction.engine import evaluate
from services.ai.clinical_decision_support.interaction.review_set import assemble_review_set
from services.ai.clinical_decision_support.interaction import rules as rules_mod
from services.ai.clinical_decision_support.interaction import attributes as attrs_mod
from services.ai.clinical_decision_support.interaction.severity import InteractionSeverity as S


def _rs(drugs):
    return assemble_review_set(current_rx=[NS(drug_name=d) for d in drugs], meds=[],
                               conditions=[], allergies=[])


def _install_and_reload(monkeypatch, path):
    monkeypatch.setattr(bundle, "BUNDLE_PATH", path)
    rules_mod.load_rule_index.cache_clear()
    attrs_mod.load_attribute_index.cache_clear()


def test_bundle_rule_merges_into_engine(tmp_path, monkeypatch):
    p = tmp_path / "bundle.sqlite"
    # a NEW specific-drug pair not in the curated YAML
    _make_bundle(p, rules=[{"kind": "drug_drug", "left": "tizanidine", "right": "ciprofloxacin",
                            "severity": "Major", "mechanism": "CYP1A2 inhibition raises tizanidine."}])
    _install_and_reload(monkeypatch, p)
    rep = evaluate(_rs(["tizanidine", "ciprofloxacin"]))
    dd = [f for f in rep.findings if f.source == "ddinter"
          and {x["name"] for x in f.participants} == {"tizanidine", "ciprofloxacin"}]
    assert dd and dd[0].severity is S.MAJOR
    _install_and_reload(monkeypatch, tmp_path / "gone.sqlite")  # cleanup → caches cleared


def test_curated_wins_over_bundle(tmp_path, monkeypatch):
    p = tmp_path / "bundle.sqlite"
    # duplicate the curated warfarin×nsaid pair but with a WRONG lower severity
    _make_bundle(p, rules=[{"kind": "drug_drug", "left": "anticoagulant", "right": "nsaid",
                            "severity": "Minor", "mechanism": "bundle (should be overridden)."}])
    _install_and_reload(monkeypatch, p)
    rep = evaluate(_rs(["warfarin", "ibuprofen"]))
    dd = [f for f in rep.findings if f.type == "drug_drug"
          and {x["name"] for x in f.participants} == {"warfarin", "ibuprofen"}]
    assert len(dd) == 1 and dd[0].source == "curated" and dd[0].severity is S.MAJOR
    _install_and_reload(monkeypatch, tmp_path / "gone.sqlite")


def test_bundle_attribute_gap_fills_only(tmp_path, monkeypatch):
    p = tmp_path / "bundle.sqlite"
    _make_bundle(p, attrs=[
        {"ingredient": "brandnewdrug", "classes": ["statin"]},          # new → added
        {"ingredient": "warfarin", "classes": ["WRONG"]},               # existing → ignored
    ])
    _install_and_reload(monkeypatch, p)
    idx = attrs_mod.load_attribute_index()
    assert idx.get("brandnewdrug") is not None                          # gap-filled
    assert "WRONG" not in idx.get("warfarin").classes                   # curated preserved
    _install_and_reload(monkeypatch, tmp_path / "gone.sqlite")


from services.ai.clinical_decision_support.interaction.engine import reload_indexes


def test_reload_indexes_picks_up_new_bundle(tmp_path, monkeypatch):
    p = tmp_path / "bundle.sqlite"
    monkeypatch.setattr(bundle, "BUNDLE_PATH", p)
    # start with no bundle
    rules_mod.load_rule_index.cache_clear(); attrs_mod.load_attribute_index.cache_clear()
    rep = evaluate(_rs(["tizanidine", "ciprofloxacin"]))
    assert not any(f.source == "ddinter" for f in rep.findings)
    # install a bundle + reload
    _make_bundle(p, rules=[{"kind": "drug_drug", "left": "tizanidine", "right": "ciprofloxacin",
                            "severity": "Major", "mechanism": "m"}])
    stats = reload_indexes()
    assert stats.get("schema_version") == bundle.SCHEMA_VERSION
    rep2 = evaluate(_rs(["tizanidine", "ciprofloxacin"]))
    assert any(f.source == "ddinter" for f in rep2.findings)
    _install_and_reload(monkeypatch, tmp_path / "gone.sqlite")
