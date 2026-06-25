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
