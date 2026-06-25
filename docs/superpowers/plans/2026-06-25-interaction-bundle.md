# Interaction Knowledge Bundle Implementation Plan (Sub-project #3a)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the interaction engine merge a SQLite knowledge bundle (DDInter/CredibleMeds/PharmGKB-derived) with its curated YAML — read-side only, curated-wins, fail-safe, hot-reloadable.

**Architecture:** A stdlib-only `bundle.py` reads JSON-payload rows from `data/bundle.sqlite` and returns rule/attribute dicts (the same shape the engine's `_parse` already consumes). The two `@lru_cache` loaders append bundle entries to the curated YAML; the engine's existing `_dedup` keeps curated precedence. `reload_indexes()` clears the caches for hot-swap.

**Tech Stack:** Python 3.12 (sqlite3, json), pytest. Run python via `/Users/sashad85/miniforge3/bin/python`.

**Spec:** `docs/superpowers/specs/2026-06-25-interaction-bundle-design.md`

---

## File structure

```
services/ai/clinical_decision_support/interaction/bundle.py   # NEW: stdlib reader (no engine imports)
services/ai/clinical_decision_support/interaction/rules.py    # load_rule_index appends bundle rules
services/ai/clinical_decision_support/interaction/attributes.py # load_attribute_index gap-fills
services/ai/clinical_decision_support/interaction/engine.py   # + reload_indexes()
.gitignore                                                    # ignore bundle.sqlite
tests/unit/test_interaction_bundle.py
```

---

## Task 1: Bundle reader (`bundle.py`)

**Files:**
- Create: `services/ai/clinical_decision_support/interaction/bundle.py`
- Test: `tests/unit/test_interaction_bundle.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_interaction_bundle.py
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
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_bundle.py -q`
Expected: FAIL — `bundle` module / attributes missing

- [ ] **Step 3: Implement `bundle.py`**

```python
# services/ai/clinical_decision_support/interaction/bundle.py
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

BUNDLE_PATH = Path(__file__).parent / "data" / "bundle.sqlite"
SCHEMA_VERSION = "bundle-v1"

logger = logging.getLogger(__name__)


def _open(path: Path):
    """Read-only connection. Raises if the file is missing or not a DB."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.execute("SELECT 1")  # force a read so a non-DB file fails here, not later
    return con


def _schema_ok(con: sqlite3.Connection) -> bool:
    try:
        row = con.execute("SELECT value FROM bundle_meta WHERE key='schema_version'").fetchone()
        return bool(row) and row[0] == SCHEMA_VERSION
    except sqlite3.Error:
        return False


def _rows(table: str, path: Path | None) -> list[str]:
    path = path or BUNDLE_PATH
    if not path.exists():
        return []
    con = None
    try:
        con = _open(path)
        if not _schema_ok(con):
            logger.warning("[interaction.bundle] schema mismatch/absent in %s — ignoring", path)
            return []
        col = "payload"
        return [r[0] for r in con.execute(f"SELECT {col} FROM {table}")]
    except sqlite3.Error as exc:
        logger.warning("[interaction.bundle] cannot read %s from %s: %s", table, path, exc)
        return []
    finally:
        if con is not None:
            con.close()


def _parse_payloads(payloads: list[str]) -> list[dict]:
    out: list[dict] = []
    for p in payloads:
        try:
            d = json.loads(p)
            if isinstance(d, dict):
                out.append(d)
        except (ValueError, TypeError):
            continue
    return out


def read_rule_payloads(path: Path | None = None) -> list[dict]:
    out = _parse_payloads(_rows("interaction_rules", path))
    for d in out:
        d["source"] = "ddinter"   # precedence is unambiguous regardless of stored value
    return out


def read_attribute_payloads(path: Path | None = None) -> list[dict]:
    return _parse_payloads(_rows("drug_attributes", path))


def bundle_stats(path: Path | None = None) -> dict:
    path = path or BUNDLE_PATH
    if not path.exists():
        return {}
    con = None
    try:
        con = _open(path)
        if not _schema_ok(con):
            return {}
        return {k: v for k, v in con.execute("SELECT key, value FROM bundle_meta")}
    except sqlite3.Error:
        return {}
    finally:
        if con is not None:
            con.close()
```

- [ ] **Step 4: Run to verify pass**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_bundle.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add services/ai/clinical_decision_support/interaction/bundle.py tests/unit/test_interaction_bundle.py
git commit -m "feat(cds): interaction knowledge-bundle SQLite reader (fail-safe, schema-checked)"
```

---

## Task 2: Merge the bundle into the loaders

**Files:**
- Modify: `services/ai/clinical_decision_support/interaction/rules.py`
- Modify: `services/ai/clinical_decision_support/interaction/attributes.py`
- Test: `tests/unit/test_interaction_bundle.py`

- [ ] **Step 1: Write the failing test (append)**

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_bundle.py -q`
Expected: FAIL — bundle rules/attrs not merged yet

- [ ] **Step 3: Extend `rules.py`.** Add the import near the top:

```python
from .bundle import read_rule_payloads
```

Change `load_rule_index` to append bundle rules:

```python
@lru_cache(maxsize=1)
def load_rule_index() -> RuleIndex:
    raw = yaml.safe_load(_DATA.read_text()) or []
    rules = [_parse(e) for e in raw] + [_parse(p) for p in read_rule_payloads()]
    return RuleIndex(
        drug_drug=tuple(r for r in rules if r.kind == "drug_drug"),
        drug_disease=tuple(r for r in rules if r.kind == "drug_disease"),
        drug_context=tuple(r for r in rules if r.kind == "drug_context"),
    )
```

- [ ] **Step 4: Extend `attributes.py`.** Add the import near the top:

```python
from .bundle import read_attribute_payloads
```

Change `load_attribute_index` to gap-fill from the bundle:

```python
@lru_cache(maxsize=1)
def load_attribute_index() -> AttributeIndex:
    raw = yaml.safe_load(_DATA.read_text()) or []
    by_ingredient = {a.ingredient: a for a in (_parse(e) for e in raw)}
    for payload in read_attribute_payloads():
        try:
            attr = _parse(payload)
        except Exception:
            continue
        if attr.ingredient not in by_ingredient:   # curated wins; bundle fills gaps
            by_ingredient[attr.ingredient] = attr
    return AttributeIndex(by_ingredient=by_ingredient)
```

- [ ] **Step 5: Run to verify pass**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_bundle.py -q`
Expected: PASS (10 passed)

- [ ] **Step 6: Run the full interaction suite (no regressions; bundle absent in normal runs)**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_*.py -q`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add services/ai/clinical_decision_support/interaction/rules.py \
        services/ai/clinical_decision_support/interaction/attributes.py \
        tests/unit/test_interaction_bundle.py
git commit -m "feat(cds): merge knowledge bundle into rule/attribute loaders (curated wins)"
```

---

## Task 3: Hot-reload + gitignore

**Files:**
- Modify: `services/ai/clinical_decision_support/interaction/engine.py`
- Modify: `.gitignore`
- Test: `tests/unit/test_interaction_bundle.py`

- [ ] **Step 1: Write the failing test (append)**

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_bundle.py::test_reload_indexes_picks_up_new_bundle -q`
Expected: FAIL — `reload_indexes` missing

- [ ] **Step 3: Add `reload_indexes` to `engine.py`.** Add the import near the top:

```python
from .bundle import bundle_stats
```

Add at the end of `engine.py`:

```python
def reload_indexes() -> dict:
    """Clear + warm the rule/attribute caches so a freshly-installed bundle takes
    effect without a process restart. Returns the new bundle stats."""
    load_rule_index.cache_clear()
    load_attribute_index.cache_clear()
    load_rule_index()
    load_attribute_index()
    return bundle_stats()
```

(`load_rule_index` and `load_attribute_index` are already imported in `engine.py`.)

- [ ] **Step 4: Run to verify pass**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_bundle.py -q`
Expected: PASS (11 passed)

- [ ] **Step 5: Gitignore the bundle**

Append to `.gitignore`:

```
# Generated interaction knowledge bundle (installed by the admin module, never committed)
services/ai/clinical_decision_support/interaction/data/bundle.sqlite
```

- [ ] **Step 6: Full suite + commit**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_*.py tests/unit/test_cds_engine.py -q`
Expected: all pass

```bash
git add services/ai/clinical_decision_support/interaction/engine.py .gitignore tests/unit/test_interaction_bundle.py
git commit -m "feat(cds): reload_indexes() hot-reload + gitignore the interaction bundle"
```

---

## Notes for the implementer

- **Caches are process-global.** Every bundle test MUST `cache_clear()` both loaders after
  monkeypatching `bundle.BUNDLE_PATH`, and clear again at the end (point `BUNDLE_PATH` at a
  nonexistent file + clear) so a leaked bundle path can't poison later tests. The helper
  `_install_and_reload` does this; use it.
- **Curated precedence** is enforced two ways: rules by the engine's existing `_dedup`
  (`curated` < `ddinter` in its source-rank), attributes by the explicit "skip if present" merge.
  Don't change `_dedup`.
- **Fail-safe is the cardinal rule:** no bundle error may ever raise into `load_rule_index`/
  `load_attribute_index`. `bundle.py` swallows everything and returns `[]`/`{}`.
- **Out of scope:** the Colab notebook that *produces* the bundle (#3b) and the admin install UI
  (#3c). This sub-project only reads/merges a bundle that already exists.
```
