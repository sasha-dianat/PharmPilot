# Interaction Knowledge Bundle — Design Spec (Sub-project #3a)

**Date:** 2026-06-25
**Status:** Approved design, pre-implementation
**Scope:** Sub-project #3a — the SQLite bundle schema + the engine read path that merges a bundle with the curated YAML. The contract that #3b (Colab notebook) produces and #3c (admin installer) installs.

## Context

The interaction engine ([[interaction-engine-program]]) loads its knowledge from curated YAML into in-memory indexes (`load_rule_index` / `load_attribute_index`, each `@lru_cache(maxsize=1)`), and its `_dedup` already honors `curated > ddinter > inferred` source precedence. Sub-project #3 pours broad real data (DDInter, CredibleMeds, PharmGKB) into the engine. #3a builds the **read side**: a SQLite bundle format and the loader merge, so the engine grows from "high-yield seed" to broad coverage **without changing the engine's matching/severity logic**.

### Decisions locked (in brainstorming)

- **JSON-payload rows.** Bundle rows carry the *same dict structure as a YAML entry*, so the existing `_parse(dict)` functions consume them unchanged (one parsing path). No normalized relational schema — the engine loads everything into memory at startup and never queries the bundle via SQL.
- **Curated YAML wins** on conflicts (rules via `_dedup` precedence; attributes via per-ingredient precedence). The bundle fills gaps + adds new entries.
- **Fail-safe:** a missing/corrupt/wrong-schema bundle is logged and ignored — the engine always works on curated YAML alone.
- **Hot-reload** via `lru_cache` clear, so #3c can install a new bundle without a process restart.
- Bundle file is **gitignored** (large generated data; installed by #3c, never committed).

### Non-goals (#3a)

- No dataset downloading/normalization (that's #3b, off-machine on Colab).
- No admin upload/install UI (#3c).
- No change to the engine's matching, severity, inference, or dedup logic — only the loaders gain a second source.

## Components

### 1. Bundle schema (produced by #3b, read here)

A single SQLite file with three tables:

```sql
CREATE TABLE interaction_rules (id INTEGER PRIMARY KEY, source TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE drug_attributes  (ingredient TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE bundle_meta      (key TEXT PRIMARY KEY, value TEXT NOT NULL);
```

- `interaction_rules.payload` — JSON of one rule dict: `{kind, left, right, severity, mechanism,
  action, evidence, confidence}` (same shape as a YAML rule entry). `source` defaults to `ddinter`.
- `drug_attributes.payload` — JSON of one attribute dict: `{ingredient, classes, enzymes,
  transporters, pd, nti, prodrug, …}` (same shape as a YAML attribute entry).
- `bundle_meta` rows: `schema_version` (e.g. `bundle-v1`), `datasets` (JSON of per-source versions),
  `built_at`, `checksum`, `rule_count`, `attribute_count`.

### 2. Bundle reader — `interaction/bundle.py` (stdlib only; no engine imports)

```text
BUNDLE_PATH = <pkg>/data/bundle.sqlite
SCHEMA_VERSION = "bundle-v1"
read_rule_payloads()      -> list[dict]   # rule dicts (source forced "ddinter"); [] if absent/bad
read_attribute_payloads() -> list[dict]   # attribute dicts; [] if absent/bad
bundle_stats()            -> dict          # meta + counts; {} if absent
```

- Each function opens the SQLite read-only, verifies `bundle_meta.schema_version == SCHEMA_VERSION`,
  parses the JSON payloads, and returns them. Any exception (missing file, bad SQLite, wrong schema,
  bad JSON) → log a warning and return `[]`/`{}`. Never raises.
- `read_rule_payloads` injects `source="ddinter"` into each dict so precedence is unambiguous.
- Imports only `sqlite3`, `json`, `pathlib`, `logging` — so `rules.py`/`attributes.py` can import it
  without a cycle.

### 3. Loader extension

- `rules.py` `load_rule_index()`: after parsing the curated YAML rules, append
  `[_parse(p) for p in read_rule_payloads()]` to the rule list before partitioning into
  drug_drug/drug_disease/drug_context. Curated and bundle rules coexist; the engine's `_dedup`
  drops a bundle rule when a curated rule covers the same participants.
- `attributes.py` `load_attribute_index()`: build the curated `by_ingredient` first, then for each
  `read_attribute_payloads()` entry add it **only if its `ingredient` is not already present**
  (curated wins; bundle fills gaps + adds new ingredients).
- Both keep `@lru_cache(maxsize=1)` — the bundle is read once per process (until reload).

### 4. Hot-reload — `engine.py` `reload_indexes()`

```python
def reload_indexes() -> dict:
    load_rule_index.cache_clear()
    load_attribute_index.cache_clear()
    load_rule_index(); load_attribute_index()   # warm
    return bundle_stats()
```

Called by #3c after installing a new bundle. Returns the new bundle stats for the admin display.

### 5. Gitignore

Add `services/ai/clinical_decision_support/interaction/data/bundle.sqlite` to `.gitignore`
(curated YAML stays tracked; the bundle never is).

## Data flow

```
startup → load_rule_index()  = curated YAML rules + bundle interaction_rules (source=ddinter)
        → load_attribute_index() = curated YAML attrs + bundle drug_attributes (gap-fill)
evaluate(...) unchanged — matches/severity/dedup operate over the merged indexes;
              curated precedence preserved by existing _dedup.

#3c installs new bundle.sqlite → reload_indexes() → caches cleared+warmed → bundle_stats()
```

## Error handling

- Bundle absent → loaders return curated-only indexes (the current behavior). No log noise beyond a
  single debug line.
- Bundle present but corrupt / wrong `schema_version` / bad JSON row → `bundle.py` logs one warning and
  returns `[]`; the engine runs on curated YAML. A single bad payload row is skipped, not fatal to the
  whole bundle (parse per-row, collect the good ones).
- `_parse` raising on a malformed-but-valid-JSON dict → caught per row in `bundle.py`, that row skipped.

## Testing

`tests/unit/test_interaction_bundle.py` (build a tiny `.sqlite` in a tmp dir, monkeypatch
`bundle.BUNDLE_PATH` to it):
- A bundle rule (e.g. `drugX × drugY = Major`) appears in `load_rule_index()` results with
  `source="ddinter"`.
- **Curated precedence:** a bundle rule duplicating a curated pair is dropped by the engine
  (`evaluate` returns the curated one). 
- A bundle attribute for a NEW ingredient is added; a bundle attribute for an ingredient already in
  curated YAML does NOT override it.
- **Fail-safe:** a corrupt/garbage `.sqlite`, a missing file, and a wrong-`schema_version` bundle each
  yield `[]` and a working engine.
- A single malformed payload row is skipped; the other rows still load.
- `reload_indexes()` picks up a changed bundle (swap the file → reload → new rule present).
- `bundle_stats()` returns meta + counts for a valid bundle, `{}` for none.

## Invariants

- The engine's logic is untouched; only the knowledge sources merge. Deterministic, offline, no LLM.
- Curated YAML always wins — vetted clinical rules can't be silently overridden by imported data.
- A bad bundle can never break the engine (fail-safe to curated-only).
- The bundle is generated data, never committed.
