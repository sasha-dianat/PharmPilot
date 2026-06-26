# DDInter Ingestion + Normalize Alignment — Design Spec (Sub-project #3b)

**Date:** 2026-06-26
**Status:** Approved design, pre-implementation
**Scope:** Sub-project #3b — a Google Colab notebook that builds a DDInter-derived interaction bundle (`.sqlite`) matching #3a's `bundle-v1` schema, plus a `normalize()` salt-stripping fix so the bundle's drug tokens match the tokens the engine derives from real Rx names.

## Context

#3a built the bundle read path; #3c the admin installer. #3b produces the bundle's content from DDInter (the largest open drug–drug interaction dataset). The make-or-break is **token alignment**: the engine matches a rule when `rule.left/right` equals `normalize(rx_drug_name)`. Today `normalize()` returns clean generics for *known* drugs but leaves salt noise for unknown ones (`"tizanidine HCl 4mg"` → `"tizanidine hcl"`). So #3b has two parts: fix `normalize()` to strip salts, and build the bundle using that same `normalize()` so both sides agree.

### Decisions locked (in brainstorming)

- **Robust DDInter v1 + fix `normalize()`.** Salt-stripping in the engine + a DDInter→bundle builder that keys rules by `normalize()` output.
- **CredibleMeds + PharmGKB are a follow-up** (#3b-2), not this pass.
- The **notebook is delivered, Colab-run** — I write + locally sample-validate the builder; the full download/normalize runs on Colab. The builder logic is fully unit-tested here.
- Builder in `scripts/`, notebook in `notebooks/`.

### Non-goals (#3b)

- No CredibleMeds/PharmGKB (drug_attributes enrichment) — follow-up.
- No automatic install — the admin uploads the produced `.sqlite` via #3c.
- No change to the engine's matching/severity/merge or the bundle schema (#3a/#3c own those).

## Components

### Part A — `normalize()` salt-stripping (`normalizer.py`)

After the existing tokenization, drop known **salt/ester** tokens that are **not the first token** (the active ingredient is first), then proceed unchanged:

```python
_SALT_TOKENS = {
    "hcl", "hydrochloride", "hbr", "hydrobromide", "sodium", "potassium", "calcium",
    "magnesium", "sulfate", "sulphate", "mesylate", "maleate", "tartrate", "besylate",
    "succinate", "fumarate", "phosphate", "citrate", "acetate", "bromide", "tosylate",
    "dihydrate", "monohydrate", "hemihydrate", "anhydrous",
}
# inside normalize(), after `tokens = [...]`:
if tokens:
    tokens = [tokens[0]] + [t for t in tokens[1:] if t not in _SALT_TOKENS]
```

- `"tizanidine hcl"` → `tizanidine`; `"warfarin sodium"` → `warfarin`; `"metoprolol succinate"` →
  `metoprolol`.
- Preserved (active is first token): `"sodium chloride"`, `"calcium carbonate"`,
  `"potassium chloride"`.
- Guarded + covered by new tests; must not regress existing normalize/engine tests.

### Part B — bundle builder (`scripts/interaction_bundle/ddinter_builder.py`, testable)

Format-agnostic core so it survives DDInter's exact CSV layout and is unit-testable:

```text
@dataclass
class RawInteraction: drug_a: str; drug_b: str; level: str; mechanism: str = ""

build_rules(rows: Iterable[RawInteraction]) -> list[dict]
    # normalize(a), normalize(b) via the engine's normalize(); skip if either empty or a==b;
    # map level → severity (DDInter Major/Moderate/Minor → our Major/Moderate/Minor; unknown→Minor);
    # rule dict {kind:"drug_drug", left, right, severity, mechanism (or "DDInter-listed interaction"),
    #            evidence:["DDInter 2.0"], confidence:0.7}; dedup by frozenset({left,right}) keeping
    #            the most-severe.

write_bundle(rules: list[dict], dest: Path, datasets: dict) -> dict
    # create the bundle-v1 SQLite (interaction_rules + empty drug_attributes + bundle_meta);
    # bundle_meta: schema_version=SCHEMA_VERSION, datasets=json, built_at, checksum (sha256 of the
    # sorted rule payloads), rule_count, attribute_count=0. Returns the meta dict.
```

- Imports `normalize` from the engine's normalizer and `SCHEMA_VERSION` from `interaction.bundle`
  (single source of truth — the builder can never drift from the reader's schema constant).
- Severity uses the same canonical strings the engine's `normalize_severity` accepts.

### Part C — Colab notebook (`notebooks/ddinter_bundle.ipynb`, delivered, Colab-run)

Cells, top to bottom:
1. `pip install pandas requests` + clone/`pip install` the PharmPilot package (or vendor `normalizer.py`
   + `bundle.py`'s `SCHEMA_VERSION` so the notebook uses the *same* normalize/schema).
2. Download the DDInter 2.0 CSV exports (per-ATC files) — a documented URL list the operator can update.
3. Adapt the DDInter columns → `RawInteraction` rows (a small per-format mapping cell).
4. `rules = build_rules(rows); write_bundle(rules, "bundle.sqlite", {"ddinter": "2.0"})`.
5. Print the meta (rule_count, checksum) and trigger a file download of `bundle.sqlite`.
6. A markdown cell: "upload this file in Admin → Interaction Bundle (#3c)".

The notebook is thin orchestration; all logic lives in the tested builder.

## Data flow

```
Colab: DDInter CSVs → adapt → RawInteraction rows
     → build_rules() [normalize() per drug, severity map, dedup]
     → write_bundle() → bundle.sqlite (bundle-v1)
     → download → admin uploads via #3c → reload_indexes() → engine merges (curated wins)
```

## Error handling

- A row with an unmappable/blank drug (normalizes to "") → skipped (logged count), not fatal.
- A row where both drugs normalize to the same token → skipped (self-pair).
- Duplicate pairs → deduped, most-severe kept.
- The builder never needs the network; the notebook's download cell is the only online step and is the
  operator's responsibility.
- A malformed produced bundle is caught downstream by #3c's `validate_bundle_file` (it won't install).

## Testing

`tests/unit/test_normalizer_salts.py`:
- `normalize("tizanidine hcl 4mg") == "tizanidine"`; `"warfarin sodium"` → `warfarin`;
  `"metoprolol succinate"` → `metoprolol`.
- Preserved: `normalize("sodium chloride")` keeps `sodium` (and `chloride`); `"calcium carbonate"`
  unchanged in spirit (first token preserved).
- A focused regression run of the existing engine suite (no behavior change for known drugs).

`tests/unit/test_ddinter_builder.py`:
- `build_rules`: maps a `RawInteraction("Tizanidine HCl", "Ciprofloxacin", "Major")` to
  `{left:"tizanidine", right:"ciprofloxacin", severity:"Major", source-less rule dict}`; dedup keeps
  most severe; self-pair + blank skipped.
- `write_bundle`: produces a file that `interaction.bundle.read_rule_payloads` reads back, with correct
  `bundle_meta` (schema_version, rule_count, checksum present).
- **End-to-end alignment (the key test):** build a bundle from the Tizanidine×Ciprofloxacin row,
  monkeypatch `bundle.BUNDLE_PATH` to it, `reload_indexes()`, then `evaluate` a review set built from
  the *real* Rx name `"tizanidine hcl 4mg"` + `"ciprofloxacin 500mg"` → the DDInter rule fires
  (`source == "ddinter"`, severity Major). This proves Part A + Part B align end-to-end.

The notebook (`.ipynb`) is delivered and reviewed but not executed in CI (Colab-only).

## Invariants

- Bundle tokens are produced by the engine's own `normalize()` — alignment by construction.
- The builder reads `SCHEMA_VERSION` from `interaction.bundle` — it can never emit a stale schema.
- Imported DDInter rules carry lower confidence than curated and lose to curated via the engine's
  existing precedence; nothing curated is overridden.
- Salt-stripping preserves the first (active) token, so single-ingredient salt drugs canonicalize
  while multi-word actives (sodium chloride) are untouched.
