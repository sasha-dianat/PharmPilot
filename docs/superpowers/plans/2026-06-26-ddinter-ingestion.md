# DDInter Ingestion + Normalize Alignment Implementation Plan (#3b)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a DDInter→bundle builder (keyed by the engine's own `normalize()`) plus a `normalize()` salt-stripping fix (mineral-cation–guarded), so an imported DDInter bundle's tokens match real Rx names — delivered with a thin Colab notebook.

**Architecture:** `normalize()` gains salt-stripping with a mineral-cation guard. A format-agnostic `ddinter_builder` (in `scripts/`) turns `(drug_a, drug_b, level, mechanism)` rows into a `bundle-v1` SQLite via the same `normalize()` + `SCHEMA_VERSION`. A thin `.ipynb` orchestrates download→build→download on Colab. An end-to-end test proves a DDInter rule fires on a salt-suffixed Rx name.

**Tech Stack:** Python 3.12 (sqlite3, json, hashlib, dataclasses), pytest. Run python via `/Users/sashad85/miniforge3/bin/python`.

**Spec:** `docs/superpowers/specs/2026-06-26-ddinter-ingestion-design.md`

---

## File structure

```
services/ai/clinical_decision_support/normalizer.py     # salt-strip + mineral guard
scripts/interaction_bundle/__init__.py                  # NEW (empty)
scripts/interaction_bundle/ddinter_builder.py           # NEW: RawInteraction, build_rules, write_bundle
notebooks/ddinter_bundle.ipynb                          # NEW: thin Colab notebook (delivered)
tests/unit/test_normalizer_salts.py
tests/unit/test_ddinter_builder.py
```

---

## Task 1: `normalize()` salt-stripping with mineral guard

**Files:**
- Modify: `services/ai/clinical_decision_support/normalizer.py`
- Test: `tests/unit/test_normalizer_salts.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_normalizer_salts.py
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
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_normalizer_salts.py -q`
Expected: FAIL — `tizanidine hcl` etc. not yet stripped

- [ ] **Step 3: Implement.** In `normalizer.py`, add the two sets just above `def normalize` (after
the `GENERIC_CLASSES` dict), and insert the salt-strip line inside `normalize` right after the
`tokens = [...]` line:

```python
# Mineral cations whose salt/anion is part of the clinical identity — never strip when first.
_MINERAL_CATIONS = {
    "calcium", "magnesium", "sodium", "potassium", "iron", "ferrous", "ferric",
    "zinc", "aluminum", "aluminium",
}
# Counter-ions / esters / hydrates that are not the active when trailing an organic drug.
_SALT_TOKENS = {
    "hcl", "hydrochloride", "hbr", "hydrobromide", "sulfate", "sulphate", "bisulfate",
    "mesylate", "maleate", "tartrate", "besylate", "succinate", "fumarate", "tosylate",
    "citrate", "acetate", "carbonate", "phosphate", "gluconate", "bromide", "chloride",
    "dihydrate", "monohydrate", "hemihydrate", "anhydrous",
}
```

The existing `normalize` has:

```python
    tokens = [token for token in re.split(r"[\s/-]+", cleaned) if token and not token[0].isdigit()]
```

Insert immediately after it:

```python
    # Strip salt/counter-ion tokens for organic actives so "tizanidine hcl" → "tizanidine".
    # Guard: when the FIRST token is a mineral cation, the anion defines the product
    # (calcium citrate ≠ calcium carbonate), so keep the whole name.
    if tokens and tokens[0] not in _MINERAL_CATIONS:
        tokens = [tokens[0]] + [t for t in tokens[1:] if t not in _SALT_TOKENS]
```

- [ ] **Step 4: Run to verify pass + no engine regressions**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_normalizer_salts.py tests/unit/test_interaction_*.py tests/unit/test_cds_engine.py -q`
Expected: all pass (the salt test + the full interaction/cds suite unchanged)

- [ ] **Step 5: Commit**

```bash
git add services/ai/clinical_decision_support/normalizer.py tests/unit/test_normalizer_salts.py
git commit -m "feat(cds): salt-stripping in normalize() with mineral-cation guard"
```

---

## Task 2: DDInter bundle builder

**Files:**
- Create: `scripts/interaction_bundle/__init__.py` (empty)
- Create: `scripts/interaction_bundle/ddinter_builder.py`
- Test: `tests/unit/test_ddinter_builder.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_ddinter_builder.py
from types import SimpleNamespace as NS
from pathlib import Path

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
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_ddinter_builder.py -q`
Expected: FAIL — module missing

- [ ] **Step 3: Implement `ddinter_builder.py`**

```python
# scripts/interaction_bundle/ddinter_builder.py
"""Build a bundle-v1 interaction SQLite from DDInter rows.

Format-agnostic: the Colab notebook adapts DDInter's CSVs into RawInteraction
rows and calls build_rules() + write_bundle(). Tokens are produced by the
engine's own normalize(), so a bundle rule matches the same real-Rx names the
engine derives — see tests/unit/test_ddinter_builder.py::test_end_to_end_alignment.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from services.ai.clinical_decision_support.normalizer import normalize
from services.ai.clinical_decision_support.interaction.bundle import SCHEMA_VERSION

_LEVEL_MAP = {"major": "Major", "moderate": "Moderate", "minor": "Minor",
              "contraindicated": "Contraindicated"}
_RANK = {"Minor": 0, "Moderate": 1, "Major": 2, "Contraindicated": 3}
_IMPORT_CONFIDENCE = 0.7


@dataclass
class RawInteraction:
    drug_a: str
    drug_b: str
    level: str
    mechanism: str = ""


def build_rules(rows: Iterable[RawInteraction]) -> list[dict]:
    best: dict[frozenset, dict] = {}
    for row in rows:
        a, b = normalize(row.drug_a), normalize(row.drug_b)
        if not a or not b or a == b:
            continue
        severity = _LEVEL_MAP.get((row.level or "").strip().lower(), "Minor")
        rule = {
            "kind": "drug_drug", "left": a, "right": b, "severity": severity,
            "mechanism": (row.mechanism or "").strip() or "DDInter-listed interaction.",
            "evidence": ["DDInter 2.0"], "confidence": _IMPORT_CONFIDENCE,
        }
        key = frozenset((a, b))
        if key not in best or _RANK[severity] > _RANK[best[key]["severity"]]:
            best[key] = rule
    return list(best.values())


def write_bundle(rules: list[dict], dest: Path, datasets: dict) -> dict:
    dest = Path(dest)
    if dest.exists():
        dest.unlink()
    payloads = sorted(json.dumps(r, sort_keys=True) for r in rules)
    checksum = hashlib.sha256("\n".join(payloads).encode()).hexdigest()
    meta = {
        "schema_version": SCHEMA_VERSION,
        "datasets": json.dumps(datasets),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "checksum": checksum,
        "rule_count": len(rules),
        "attribute_count": 0,
    }
    con = sqlite3.connect(dest)
    try:
        con.execute("CREATE TABLE interaction_rules (id INTEGER PRIMARY KEY, source TEXT, payload TEXT)")
        con.execute("CREATE TABLE drug_attributes (ingredient TEXT PRIMARY KEY, payload TEXT)")
        con.execute("CREATE TABLE bundle_meta (key TEXT PRIMARY KEY, value TEXT)")
        con.executemany("INSERT INTO interaction_rules (source, payload) VALUES ('ddinter', ?)",
                        [(json.dumps(r),) for r in rules])
        con.executemany("INSERT INTO bundle_meta (key, value) VALUES (?, ?)", list(meta.items()))
        con.commit()
    finally:
        con.close()
    return meta
```

- [ ] **Step 4: Run to verify pass**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_ddinter_builder.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add scripts/interaction_bundle/__init__.py scripts/interaction_bundle/ddinter_builder.py tests/unit/test_ddinter_builder.py
git commit -m "feat(cds): DDInter→bundle builder (normalize-aligned, schema-versioned)"
```

---

## Task 3: Colab notebook (delivered)

**Files:**
- Create: `notebooks/ddinter_bundle.ipynb`

- [ ] **Step 1: Generate the notebook.** Run this Python (it writes a valid nbformat-4 notebook —
no jupyter dependency needed):

```python
# run via: /Users/sashad85/miniforge3/bin/python - <<'PY'
import json, os
os.makedirs("notebooks", exist_ok=True)

def md(*lines):  return {"cell_type": "markdown", "metadata": {}, "source": list(lines)}
def code(*lines): return {"cell_type": "code", "metadata": {}, "execution_count": None,
                          "outputs": [], "source": list(lines)}

cells = [
    md("# DDInter → PharmPilot Interaction Bundle\n",
       "Builds a `bundle-v1` SQLite from DDInter 2.0 and downloads it. ",
       "Then install it in **Admin → Interaction Bundle** (#3c)."),
    code("# 1. Dependencies + PharmPilot package (for the SAME normalize() + schema constant).\n",
         "!pip -q install pandas requests\n",
         "# Option A: clone the repo so the builder is importable.\n",
         "# !git clone <YOUR_REPO_URL> pharmpilot && pip -q install -e pharmpilot\n",
         "# Option B: upload normalizer.py + bundle.py and adjust sys.path.\n"),
    code("# 2. Download DDInter 2.0 CSV exports (per-ATC). Update URLs to the current release.\n",
         "import pandas as pd, requests, io\n",
         "DDINTER_CSV_URLS = [\n",
         "    # 'https://ddinter.scbdd.com/static/media/download/ddinter_downloads_code_A.csv',\n",
         "    # ... add the per-category files you need ...\n",
         "]\n",
         "frames = []\n",
         "for url in DDINTER_CSV_URLS:\n",
         "    frames.append(pd.read_csv(io.StringIO(requests.get(url, timeout=60).text)))\n",
         "raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()\n",
         "raw.head()\n"),
    code("# 3. Adapt DDInter columns → RawInteraction rows.\n",
         "# DDInter columns are typically: Drug_A, Drug_B, Level (Major/Moderate/Minor).\n",
         "from scripts.interaction_bundle.ddinter_builder import RawInteraction\n",
         "def to_rows(df):\n",
         "    for _, r in df.iterrows():\n",
         "        yield RawInteraction(str(r.get('Drug_A','')), str(r.get('Drug_B','')),\n",
         "                             str(r.get('Level','')), str(r.get('Mechanism','') or ''))\n",
         "rows = list(to_rows(raw))\n",
         "len(rows)\n"),
    code("# 4. Build + write the bundle.\n",
         "from scripts.interaction_bundle.ddinter_builder import build_rules, write_bundle\n",
         "rules = build_rules(rows)\n",
         "meta = write_bundle(rules, 'bundle.sqlite', {'ddinter': '2.0'})\n",
         "print(meta)\n"),
    code("# 5. Download the bundle, then install it in Admin → Interaction Bundle.\n",
         "from google.colab import files  # Colab only\n",
         "files.download('bundle.sqlite')\n"),
    md("**Next:** in PharmPilot, open *Dashboards → Interaction Bundle* and upload `bundle.sqlite`. ",
       "It is validated + atomically installed + hot-reloaded; curated rules always win."),
]
nb = {"cells": cells, "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3"},
      "language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 5}
with open("notebooks/ddinter_bundle.ipynb", "w") as f:
    json.dump(nb, f, indent=1)
print("wrote notebooks/ddinter_bundle.ipynb")
PY
```

- [ ] **Step 2: Verify it's valid JSON / loadable**

Run: `/Users/sashad85/miniforge3/bin/python -c "import json; nb=json.load(open('notebooks/ddinter_bundle.ipynb')); print('cells:', len(nb['cells']), 'nbformat:', nb['nbformat'])"`
Expected: `cells: 7 nbformat: 4`

- [ ] **Step 3: Commit**

```bash
git add notebooks/ddinter_bundle.ipynb
git commit -m "feat(cds): Colab notebook to build the DDInter interaction bundle"
```

---

## Task 4: Final verification

- [ ] **Step 1: Full relevant suite**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_normalizer_salts.py tests/unit/test_ddinter_builder.py tests/unit/test_interaction_*.py tests/unit/test_cds_engine.py -q`
Expected: all pass

- [ ] **Step 2: Sanity — builder produces an installable bundle end-to-end**

Run:
```bash
/Users/sashad85/miniforge3/bin/python - <<'PY'
from scripts.interaction_bundle.ddinter_builder import RawInteraction, build_rules, write_bundle
from services.ai.clinical_decision_support.interaction import bundle
import tempfile, pathlib
p = pathlib.Path(tempfile.mkdtemp()) / "b.sqlite"
meta = write_bundle(build_rules([RawInteraction("Tizanidine HCl","Ciprofloxacin","Major")]), p, {"ddinter":"2.0"})
ok, reason, stats = bundle.validate_bundle_file(p)   # the #3c installer's own validator
print("validates for installer:", ok, reason, "| rules:", meta["rule_count"])
PY
```
Expected: `validates for installer: True ok | rules: 1`

- [ ] **Step 3: Refresh graphify + final commit**

```bash
graphify update . >/dev/null 2>&1 &
git add -A && git commit -m "chore(cds): finalize DDInter ingestion (#3b)" || echo "nothing to commit"
```

---

## Notes for the implementer

- **Token alignment is the whole point:** the builder calls the engine's `normalize()`, and Task 1
  makes `normalize()` strip salts (mineral-guarded). The `test_end_to_end_alignment` test is the proof
  — keep it green.
- **Schema can't drift:** the builder imports `SCHEMA_VERSION` from `interaction.bundle`; never hardcode it.
- **Curated precedence preserved:** imported rules carry `confidence 0.7` and `source` becomes
  `ddinter` on read — the engine's `_dedup` still lets curated win.
- **The notebook is delivered, not CI-run.** Its `build_rules`/`write_bundle` calls are the tested
  surface; the download/adapt cells are operator-edited for the current DDInter release.
- **Out of scope:** CredibleMeds + PharmGKB attribute enrichment (#3b-2).
```
