import json
import sqlite3
from pathlib import Path

from services.ai.clinical_decision_support.interaction import bundle


def _good_bundle(path: Path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE interaction_rules (id INTEGER PRIMARY KEY, source TEXT, payload TEXT)")
    con.execute("CREATE TABLE drug_attributes (ingredient TEXT PRIMARY KEY, payload TEXT)")
    con.execute("CREATE TABLE bundle_meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO interaction_rules (source, payload) VALUES ('ddinter', ?)",
                (json.dumps({"kind": "drug_drug", "left": "a", "right": "b", "severity": "Major"}),))
    con.execute("INSERT INTO bundle_meta VALUES ('schema_version', ?)", (bundle.SCHEMA_VERSION,))
    con.execute("INSERT INTO bundle_meta VALUES ('rule_count', '1')")
    con.commit(); con.close()


def test_validate_accepts_good_bundle(tmp_path):
    p = tmp_path / "b.sqlite"; _good_bundle(p)
    ok, reason, stats = bundle.validate_bundle_file(p)
    assert ok and reason == "ok" and stats.get("rule_count") == "1"


def test_validate_rejects_corrupt(tmp_path):
    p = tmp_path / "b.sqlite"; p.write_bytes(b"nope")
    ok, reason, _ = bundle.validate_bundle_file(p)
    assert not ok and "SQLite" in reason


def test_validate_rejects_missing_table(tmp_path):
    p = tmp_path / "b.sqlite"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE bundle_meta (key TEXT, value TEXT)")
    con.execute("INSERT INTO bundle_meta VALUES ('schema_version', ?)", (bundle.SCHEMA_VERSION,))
    con.commit(); con.close()
    ok, reason, _ = bundle.validate_bundle_file(p)
    assert not ok and "missing table" in reason


def test_validate_rejects_wrong_schema(tmp_path):
    p = tmp_path / "b.sqlite"; _good_bundle(p)
    con = sqlite3.connect(p)
    con.execute("UPDATE bundle_meta SET value='bundle-v999' WHERE key='schema_version'")
    con.commit(); con.close()
    ok, reason, _ = bundle.validate_bundle_file(p)
    assert not ok and "schema version mismatch" in reason


def test_install_bundle_replaces_atomically(tmp_path, monkeypatch):
    dest = tmp_path / "data" / "bundle.sqlite"
    monkeypatch.setattr(bundle, "BUNDLE_PATH", dest)
    src = tmp_path / "incoming.sqlite"; _good_bundle(src)
    bundle.install_bundle(src)
    assert dest.exists() and not src.exists()        # moved, not copied
    assert bundle.bundle_stats(dest).get("rule_count") == "1"
