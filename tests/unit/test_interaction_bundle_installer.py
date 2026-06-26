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


import asyncio
import os
from types import SimpleNamespace as NS

from services.platform.routers import cds
from services.ai.clinical_decision_support.interaction import rules as rules_mod
from services.ai.clinical_decision_support.interaction import attributes as attrs_mod
from services.ai.clinical_decision_support.interaction.engine import evaluate
from services.ai.clinical_decision_support.interaction.review_set import assemble_review_set


class _FakeUpload:
    def __init__(self, data: bytes): self._data = data; self._pos = 0
    async def read(self, n: int = -1) -> bytes:
        if self._pos >= len(self._data):
            return b""
        end = len(self._data) if n is None or n < 0 else self._pos + n
        chunk = self._data[self._pos:end]; self._pos = end
        return chunk


def _bundle_bytes(tmp_path, rule_left="tizanidine", rule_right="ciprofloxacin"):
    src = tmp_path / "src.sqlite"
    src.unlink(missing_ok=True)
    con = sqlite3.connect(src)
    con.execute("CREATE TABLE interaction_rules (id INTEGER PRIMARY KEY, source TEXT, payload TEXT)")
    con.execute("CREATE TABLE drug_attributes (ingredient TEXT PRIMARY KEY, payload TEXT)")
    con.execute("CREATE TABLE bundle_meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO interaction_rules (source, payload) VALUES ('ddinter', ?)",
                (json.dumps({"kind": "drug_drug", "left": rule_left, "right": rule_right,
                             "severity": "Major", "mechanism": "m"}),))
    con.execute("INSERT INTO bundle_meta VALUES ('schema_version', ?)", (bundle.SCHEMA_VERSION,))
    con.execute("INSERT INTO bundle_meta VALUES ('rule_count', '1')")
    con.commit(); con.close()
    return src.read_bytes()


def _reset(monkeypatch, dest):
    monkeypatch.setattr(bundle, "BUNDLE_PATH", dest)
    rules_mod.load_rule_index.cache_clear(); attrs_mod.load_attribute_index.cache_clear()


def _rs(drugs):
    return assemble_review_set(current_rx=[NS(drug_name=d) for d in drugs], meds=[],
                               conditions=[], allergies=[])


def test_install_then_engine_sees_rule(tmp_path, monkeypatch):
    dest = tmp_path / "data" / "bundle.sqlite"
    _reset(monkeypatch, dest)
    staff = NS(id="s", pharmacy_id="p")
    up = _FakeUpload(_bundle_bytes(tmp_path))
    out = asyncio.run(cds.install_interaction_bundle(file=up, confirm_replace=False, staff=staff))
    assert out["installed"] and out["stats"].get("rule_count") == "1"
    rep = evaluate(_rs(["tizanidine", "ciprofloxacin"]))
    assert any(f.source == "ddinter" for f in rep.findings)
    _reset(monkeypatch, tmp_path / "gone.sqlite")  # cleanup caches


def test_replace_requires_confirmation(tmp_path, monkeypatch):
    from fastapi import HTTPException
    dest = tmp_path / "data" / "bundle.sqlite"
    _reset(monkeypatch, dest)
    staff = NS(id="s", pharmacy_id="p")
    asyncio.run(cds.install_interaction_bundle(file=_FakeUpload(_bundle_bytes(tmp_path)),
                                               confirm_replace=False, staff=staff))
    # a second install without confirm → 409, existing bundle untouched
    try:
        asyncio.run(cds.install_interaction_bundle(
            file=_FakeUpload(_bundle_bytes(tmp_path, "warfarin", "x")),
            confirm_replace=False, staff=staff))
        assert False, "expected 409"
    except HTTPException as e:
        assert e.status_code == 409
    assert bundle.bundle_stats(dest).get("rule_count") == "1"   # unchanged
    # with confirm → installs
    out = asyncio.run(cds.install_interaction_bundle(
        file=_FakeUpload(_bundle_bytes(tmp_path, "warfarin", "x")),
        confirm_replace=True, staff=staff))
    assert out["installed"]
    _reset(monkeypatch, tmp_path / "gone.sqlite")


def test_install_rejects_bad_bundle(tmp_path, monkeypatch):
    from fastapi import HTTPException
    dest = tmp_path / "data" / "bundle.sqlite"
    _reset(monkeypatch, dest)
    staff = NS(id="s", pharmacy_id="p")
    try:
        asyncio.run(cds.install_interaction_bundle(file=_FakeUpload(b"not a db"),
                                                   confirm_replace=False, staff=staff))
        assert False, "expected 422"
    except HTTPException as e:
        assert e.status_code == 422
    assert not dest.exists()   # nothing installed
    _reset(monkeypatch, tmp_path / "gone.sqlite")


def test_status_endpoint(tmp_path, monkeypatch):
    dest = tmp_path / "data" / "bundle.sqlite"
    _reset(monkeypatch, dest)
    staff = NS(id="s", pharmacy_id="p")
    assert asyncio.run(cds.interaction_bundle_status(staff=staff)) == {"installed": False, "stats": {}}
    asyncio.run(cds.install_interaction_bundle(file=_FakeUpload(_bundle_bytes(tmp_path)),
                                               confirm_replace=False, staff=staff))
    st = asyncio.run(cds.interaction_bundle_status(staff=staff))
    assert st["installed"] and st["stats"].get("rule_count") == "1"
    _reset(monkeypatch, tmp_path / "gone.sqlite")
