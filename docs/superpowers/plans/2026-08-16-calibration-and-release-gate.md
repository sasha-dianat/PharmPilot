# Calibration and Release Gate Implementation Plan — Phase 3 of 7

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make calibration per-occlusion-stratum, gate release on per-cell performance rather than an average, and let a new model run in shadow without voting.

**Architecture:** Calibration exists but is one-per-modality. The design requires one per occlusion stratum, because a single pooled `ImpostorStats` returns a threshold that is too **low** for veiled faces — so the FPIR guarantee silently fails for exactly the group it most affects. `ImpostorStats.population` was added for this in phase 1 and has never been populated by anything.

**The governing rule:** *no averaging.* A mean that hides a failing stratum or a failing demographic cell is worse than no measurement, because it licenses a system to vote on people it cannot recognise.

**Tech Stack:** Python 3.12, numpy, SQLAlchemy async, PostgreSQL, Alembic.

## Global Constraints

- Python interpreter: `/Users/sashad85/miniforge3/bin/python3`
- Tests from repo root: `/Users/sashad85/miniforge3/bin/python3 -m pytest`
- **No new third-party dependencies.** numpy only.
- Every new route MUST carry an auth dependency or be added to the allowlist in `tests/unit/test_route_authentication.py` with a written reason.
- An uncalibrated modality/stratum must not vote. `stats=None` means excluded, by design, and nothing in this phase may weaken that.
- Migration head is `0047` (`0047_shortage_warning_kind` landed while this plan was drafted). Task 1 adds `0048`, Task 3 adds `0049`.
- **Do not export `DATABASE_URL`** when running the suite.
- Before blaming a slow suite on code, check `pharmpilot_test` for accumulated `inventory_exceptions` — it regrew to 270k rows / 1.3 GB in two runs during phase 2.
- Persian/RTL for user-facing strings; internal identifiers stay English.

---

## What already exists

| Component | Path | State |
|---|---|---|
| `Calibration` dataclass with `usable`/`reason` | `repository.py:148` | Works, per-modality only |
| `measure_impostor_stats(index, min_pairs=1000)` | `repository.py:170` | Scores all cross-identity pairs; returns ONE result |
| `load_impostor_stats(db, pharmacy, modality)` | `repository.py:224` | Rejects a stored calibration failing the sample floor |
| `biometric_score_stats` table | migration `0033` | Unique on `(pharmacy_id, modality, model_version)` — **no stratum** |
| `biometric_templates.capture_context` JSONB | migration `0033` | Present, empty — where the stratum belongs |
| `ImpostorStats.population` | `thresholds.py:57` | Declared, **never populated by anything** |
| `OcclusionStratum` / `SURVIVING_MODALITIES` | `occlusion.py` | From phase 1 |
| `gallery_stats` with `can_vote`/`why_excluded` | `biometric_admin.py:129` | Per-modality |

## The three gaps

1. **Calibration is not stratified.** One `ImpostorStats` per modality means the veiled subset is averaged in with unoccluded faces, producing a threshold too low for the majority case.
2. **No release gate.** Nothing measures per-demographic-cell performance, and nothing blocks a release on a failing cell.
3. **No shadow mode.** `model_version` exists on both tables, but nothing marks a version as observing-but-not-voting.

## File Structure

| File | Responsibility |
|---|---|
| `data/migrations/versions/0048_calibration_strata.py` (create) | `stratum` column + widened unique key; `shadow` flag on templates. |
| `services/biometric/identity_resolution/strata.py` (create) | Split a gallery by stratum; measure and persist per stratum. |
| `services/biometric/release_gate.py` (create) | Per-cell metrics and the pass/fail decision. No averaging. |
| `services/biometric/identity_resolution/repository.py` (modify) | `load_impostor_stats` takes a stratum; shadow versions never load as active. |
| `services/platform/routers/biometric_admin.py` (modify) | Calibrate per stratum; report the gate. |
| `tests/unit/test_calibration_strata.py` (create) | Stratified measurement and persistence. |
| `tests/unit/test_release_gate.py` (create) | Per-cell gating, including the detector channel. |
| `tests/unit/test_shadow_mode.py` (create) | A shadow model observes and never votes. |

---

### Task 1: Stratified calibration schema

**Why:** `biometric_score_stats` is unique on `(pharmacy_id, modality, model_version)`, so a second calibration for the same modality overwrites the first. Storing per-stratum rows requires the stratum in both the column set and the key. And `biometric_templates` needs a `shadow` flag so a model can be enrolled and measured without being loaded into the voting index.

**Files:**
- Create: `data/migrations/versions/0048_calibration_strata.py`
- Test: `tests/unit/test_calibration_strata.py` (schema portion)

**Interfaces:**
- Consumes: nothing.
- Produces: `biometric_score_stats.stratum VARCHAR(16) NOT NULL DEFAULT 'all'`, unique key widened to `(pharmacy_id, modality, model_version, stratum)`; `biometric_templates.shadow BOOLEAN NOT NULL DEFAULT false`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_calibration_strata.py`:

```python
"""Calibration is per occlusion stratum, not per modality.

A single pooled ImpostorStats averages veiled faces in with unoccluded ones and
returns a threshold that is too LOW for the veiled subset — so the FPIR
guarantee silently fails for exactly the group it most affects. ImpostorStats
carries a `population` field for this and nothing has ever populated it.
"""
from __future__ import annotations

import re
from pathlib import Path

MIGRATION = Path("data/migrations/versions/0048_calibration_strata.py")


def test_migration_exists_and_chains_from_the_current_head():
    src = MIGRATION.read_text(encoding="utf-8")
    assert re.search(r'^revision\s*=\s*"0048"', src, re.M)
    assert re.search(r'^down_revision\s*=\s*"0047"', src, re.M)


def test_stratum_is_added_to_the_score_stats_table():
    src = MIGRATION.read_text(encoding="utf-8")
    assert "biometric_score_stats" in src
    assert "stratum" in src


def test_the_unique_key_includes_the_stratum():
    """Without this a second stratum's calibration overwrites the first, and the
    table silently holds one row where it should hold four."""
    src = MIGRATION.read_text(encoding="utf-8")
    body = src[src.index("def upgrade"):src.index("def downgrade")]
    assert "stratum" in body
    # the old three-column key must be dropped, not merely supplemented
    assert "drop_constraint" in body or "DROP CONSTRAINT" in body


def test_templates_gain_a_shadow_flag():
    """A shadow model must be enrollable and measurable without voting."""
    src = MIGRATION.read_text(encoding="utf-8")
    assert "biometric_templates" in src
    assert "shadow" in src


def test_downgrade_is_written():
    src = MIGRATION.read_text(encoding="utf-8")
    body = src[src.index("def downgrade"):]
    assert "stratum" in body and "shadow" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_calibration_strata.py -q`
Expected: FAIL — `FileNotFoundError` on the migration path

- [ ] **Step 3: Write the migration**

Create `data/migrations/versions/0048_calibration_strata.py`:

```python
"""calibration per occlusion stratum, and a shadow flag on templates

Two changes, both required before a stratified calibration can be stored.

biometric_score_stats was unique on (pharmacy_id, modality, model_version), so
a second stratum's calibration would overwrite the first and the table would
silently hold one row where it should hold four. The stratum joins the key.

biometric_templates gains `shadow`: a candidate model must be enrollable and
measurable without its templates entering the voting index.

Revision ID: 0049
Revises: 0048
"""
import sqlalchemy as sa
from alembic import op

revision = "0049"
down_revision = "0047"
branch_labels = None
depends_on = None

_OLD_KEY = "uq_score_stats_modality_version"
_NEW_KEY = "uq_score_stats_modality_version_stratum"


def upgrade() -> None:
    op.add_column("biometric_score_stats",
                  sa.Column("stratum", sa.String(16), nullable=False,
                            server_default="all"))
    # 'all' is the pooled calibration that existed before strata. It stays
    # valid as a fallback, but a stratified query prefers its own row.
    try:
        op.drop_constraint(_OLD_KEY, "biometric_score_stats", type_="unique")
    except Exception:
        # The constraint may carry an auto-generated name; find and drop it.
        op.execute("""
            DO $$
            DECLARE c text;
            BEGIN
              SELECT conname INTO c FROM pg_constraint
              WHERE conrelid = 'biometric_score_stats'::regclass
                AND contype = 'u' LIMIT 1;
              IF c IS NOT NULL THEN
                EXECUTE format('ALTER TABLE biometric_score_stats '
                               'DROP CONSTRAINT %I', c);
              END IF;
            END $$;""")
    op.create_unique_constraint(
        _NEW_KEY, "biometric_score_stats",
        ["pharmacy_id", "modality", "model_version", "stratum"])

    op.add_column("biometric_templates",
                  sa.Column("shadow", sa.Boolean, nullable=False,
                            server_default=sa.false()))
    op.create_index("ix_biometric_templates_shadow", "biometric_templates",
                    ["pharmacy_id", "modality", "shadow"])


def downgrade() -> None:
    op.drop_index("ix_biometric_templates_shadow",
                  table_name="biometric_templates")
    op.drop_column("biometric_templates", "shadow")
    op.drop_constraint(_NEW_KEY, "biometric_score_stats", type_="unique")
    # Collapsing back to one row per model: keep the pooled calibration and drop
    # the stratified ones, which have nowhere to live under the narrower key.
    op.execute("DELETE FROM biometric_score_stats WHERE stratum <> 'all'")
    op.drop_column("biometric_score_stats", "stratum")
    op.create_unique_constraint(
        _OLD_KEY, "biometric_score_stats",
        ["pharmacy_id", "modality", "model_version"])
```

- [ ] **Step 4: Run the migration and verify it applied**

`env.py` reads `DATABASE_URL` from `os.environ` only and ignores `.env`, so it must be loaded explicitly or alembic silently targets a different database on port 5432:

```bash
set -a; . ./.env; set +a
/Users/sashad85/miniforge3/bin/python3 -m alembic upgrade head
```

Expected: `Running upgrade 0047 -> 0048`

Then confirm against the live database — reading the DDL is not proof it applied:

```bash
set -a; . ./.env; set +a
/Users/sashad85/miniforge3/bin/python3 - <<'PY'
import asyncio, os, asyncpg
u = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
async def main():
    c = await asyncpg.connect(u)
    for t, col in (("biometric_score_stats", "stratum"),
                   ("biometric_templates", "shadow")):
        got = await c.fetchval("""
            SELECT data_type FROM information_schema.columns
            WHERE table_name=$1 AND column_name=$2""", t, col)
        print(f"{t}.{col}: {got}")
    keys = await c.fetch("""
        SELECT conname, pg_get_constraintdef(oid) d FROM pg_constraint
        WHERE conrelid='biometric_score_stats'::regclass AND contype='u'""")
    for k in keys:
        print(f"unique: {k['conname']} {k['d']}")
    await c.close()
asyncio.run(main())
PY
```

Expected: both columns present, and the unique constraint listing four columns including `stratum`.

- [ ] **Step 5: Run the test and commit**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_calibration_strata.py -q`
Expected: PASS — 5 passed

```bash
git add data/migrations/versions/0048_calibration_strata.py tests/unit/test_calibration_strata.py
git commit -m "feat(biometric): calibration schema gains a stratum, templates a shadow flag

biometric_score_stats was unique on (pharmacy, modality, model_version), so a
second stratum's calibration would overwrite the first and the table would
silently hold one row where it should hold four. The stratum joins the key, and
'all' remains as the pooled fallback that existed before strata.

biometric_templates gains `shadow`: a candidate model must be enrollable and
measurable without its templates entering the voting index."
```

---

### Task 2: Measure and store calibration per stratum

**Why:** `measure_impostor_stats` takes one index and returns one `Calibration`. Stratified calibration needs the gallery split by the capture's occlusion stratum first, each subset measured independently, and each result stored with its stratum — with the **honest failure** made explicit: a stratum with too few pairs is not calibrated, and therefore may not vote, rather than borrowing the pooled figure.

**Files:**
- Create: `services/biometric/identity_resolution/strata.py`
- Modify: `services/biometric/identity_resolution/repository.py` (`load_impostor_stats` signature)
- Test: `tests/unit/test_calibration_strata.py` (append)

**A prerequisite the survey turned up:** `ModalityIndex.load` (vector_store.py)
takes rows keyed `{identity_id, template_id, embedding, quality}` — note
**`embedding`**, not `vector` — and it **discards `capture_context` entirely**.
There is no `_contexts` attribute. Splitting by stratum is impossible until the
index retains the context, so this task extends `load` first.

**Interfaces:**
- Consumes: `repository.measure_impostor_stats`, `repository.Calibration`, `vector_store.ModalityIndex`, `occlusion.OcclusionStratum`.
- Produces: `ModalityIndex._contexts: list[dict]` populated by `load` from each row's optional `capture_context`; `split_by_stratum(index) -> dict[str, V.ModalityIndex]`; `measure_all_strata(index, min_pairs=1000) -> dict[str, Calibration]`; and `repository.load_impostor_stats(db, pharmacy_id, modality, stratum="all")` — a new keyword with a backward-compatible default.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_calibration_strata.py`:

```python
# ── stratified measurement ───────────────────────────────────────────────

import numpy as np
import pytest

from services.biometric.identity_resolution import vector_store as V
from services.biometric.identity_resolution.strata import (
    POOLED, measure_all_strata, split_by_stratum)


def _index(rows):
    """rows: [(identity_id, stratum, vector)]"""
    idx = V.ModalityIndex(modality="face", dim=8)
    idx.load([{"identity_id": i, "template_id": f"t{n}", "quality": 0.9,
               "embedding": v, "capture_context": {"occlusion_stratum": s}}
              for n, (i, s, v) in enumerate(rows)])
    return idx


def _vec(seed, dim=8):
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def test_a_gallery_splits_into_its_strata():
    idx = _index([("a", "clear", _vec(1)), ("b", "clear", _vec(2)),
                  ("c", "mask", _vec(3))])
    parts = split_by_stratum(idx)
    assert set(parts) >= {"clear", "mask"}
    assert len(parts["clear"]) == 2
    assert len(parts["mask"]) == 1


def test_templates_with_no_recorded_stratum_go_to_the_pooled_bucket():
    """Everything enrolled before phase 1 has an empty capture_context. It is
    still usable as the pooled calibration; it is just not stratified."""
    idx = V.ModalityIndex(modality="face", dim=8)
    idx.load([{"identity_id": "a", "template_id": "t0", "quality": 0.9,
               "embedding": _vec(1), "capture_context": {}}])
    parts = split_by_stratum(idx)
    assert POOLED in parts and len(parts[POOLED]) == 1


def test_a_stratum_with_too_few_pairs_is_not_usable():
    """The honest failure. A thin stratum must not borrow the pooled figure —
    that is precisely how a threshold too low for veiled faces gets applied."""
    rows = [(f"id{i}", "clear", _vec(i)) for i in range(30)]
    rows += [("x", "chador", _vec(100)), ("y", "chador", _vec(101))]
    cals = measure_all_strata(_index(rows), min_pairs=50)
    assert cals["chador"].usable is False
    assert "pairs" in cals["chador"].reason.lower()


def test_each_stratum_is_measured_independently():
    rows = [(f"c{i}", "clear", _vec(i)) for i in range(20)]
    rows += [(f"m{i}", "mask", _vec(200 + i)) for i in range(20)]
    cals = measure_all_strata(_index(rows), min_pairs=10)
    assert set(cals) >= {"clear", "mask"}
    # measured separately, so the two need not agree
    assert cals["clear"].modality == "face"
    assert cals["clear"].pairs > 0 and cals["mask"].pairs > 0


def test_the_stratum_travels_into_the_impostor_stats_population():
    """ImpostorStats.population has existed unused since phase 1. It is what
    tells a reviewer which subset a threshold was derived from."""
    from services.biometric.identity_resolution.strata import to_impostor_stats

    rows = [(f"c{i}", "clear", _vec(i)) for i in range(20)]
    cal = measure_all_strata(_index(rows), min_pairs=10)["clear"]
    stats = to_impostor_stats(cal, stratum="clear", sample_floor=10)
    assert stats is not None
    assert stats.population == "clear"


def test_an_unusable_calibration_yields_no_stats_at_all():
    """None is the correct return: fusion excludes an uncalibrated stream, and
    a placeholder here would licence a vote that was never measured."""
    from services.biometric.identity_resolution.strata import to_impostor_stats
    from services.biometric.identity_resolution.repository import Calibration

    bad = Calibration("face", 0.0, 0.0, 0, None, None, 0, False, "too few pairs")
    assert to_impostor_stats(bad, stratum="mask") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_calibration_strata.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.biometric.identity_resolution.strata'`

If `_index` fails first, read `ModalityIndex.load` in `vector_store.py` and adapt the row dict to its actual key names before proceeding — do not change `load`.

- [ ] **Step 3a: Make the index retain capture context**

`ModalityIndex.load` currently discards `capture_context`, so nothing downstream
can know which stratum a template came from. In `vector_store.py`, add a
`_contexts` field to the dataclass:

```python
    _contexts: list[dict] = field(default_factory=list)
```

and populate it inside `load`, alongside the existing lists:

```python
        vecs, ids, tids, quals, ctxs = [], [], [], [], []
        for r in rows:
            ...
            quals.append(float(r.get("quality") or 1.0))
            # Retained so calibration can be stratified. Without this the
            # occlusion stratum recorded at enrolment is lost on load, and a
            # single pooled threshold gets applied to every kind of capture.
            ctxs.append(dict(r.get("capture_context") or {}))
        ...
        self._identities, self._templates, self._quality = ids, tids, quals
        self._contexts = ctxs
```

Also add `capture_context` to whatever SELECT in `repository.load_index` builds
those rows, so the column actually reaches `load`.

- [ ] **Step 3b: Write the implementation**

Create `services/biometric/identity_resolution/strata.py`:

```python
"""Calibration per occlusion stratum.

A single pooled ImpostorStats averages veiled faces in with unoccluded ones. The
resulting threshold is too LOW for the veiled subset, so the FPIR guarantee
silently fails for exactly the group it most affects — and in this deployment
that group is the majority of female customers.

THE HONEST FAILURE MATTERS MOST HERE. A stratum with too few enrolled pairs is
reported as NOT usable, and `to_impostor_stats` returns None for it. It does not
borrow the pooled figure. Borrowing is how a threshold measured on unoccluded
faces ends up applied to chador captures, which is the exact defect stratified
calibration exists to prevent.
"""
from __future__ import annotations

import numpy as np

from . import repository as R
from . import vector_store as V

# Templates enrolled before the stratum was recorded. Still usable as the
# pooled calibration; simply not stratified.
POOLED = "all"


def _stratum_of(context) -> str:
    if not isinstance(context, dict):
        return POOLED
    return str(context.get("occlusion_stratum") or POOLED)


def split_by_stratum(index: V.ModalityIndex) -> dict[str, V.ModalityIndex]:
    """One sub-index per stratum present in the gallery."""
    n = len(index)
    # Populated by ModalityIndex.load (see Step 3a). Defensive default so an
    # index built by older code degrades to the pooled bucket rather than
    # raising.
    contexts = getattr(index, "_contexts", None) or [{}] * n

    buckets: dict[str, list[int]] = {}
    for i in range(n):
        buckets.setdefault(_stratum_of(contexts[i]), []).append(i)

    out: dict[str, V.ModalityIndex] = {}
    for stratum, rows in buckets.items():
        sub = V.ModalityIndex(modality=index.modality, dim=index.dim)
        sub._vectors = index._vectors[rows]
        sub._identities = [index._identities[i] for i in rows]
        sub._templates = [index._templates[i] for i in rows]
        sub._quality = [index._quality[i] for i in rows]
        sub._contexts = [contexts[i] for i in rows]
        out[stratum] = sub
    return out


def measure_all_strata(index: V.ModalityIndex,
                       min_pairs: int = 1000) -> dict[str, R.Calibration]:
    """Measure each stratum independently. No stratum inherits another's."""
    return {s: R.measure_impostor_stats(sub, min_pairs=min_pairs)
            for s, sub in split_by_stratum(index).items()}


def to_impostor_stats(cal: R.Calibration, stratum: str,
                      sample_floor: int = 1000):
    """`ImpostorStats` for a usable calibration, or None.

    None is the correct answer for an unusable stratum: the fusion engine
    excludes an uncalibrated stream, and returning a placeholder would licence a
    vote on a distribution nobody measured.
    """
    from .thresholds import ImpostorStats

    if not cal.usable or cal.pairs < sample_floor or cal.impostor_std <= 0:
        return None
    try:
        return ImpostorStats(
            mean=float(cal.impostor_mean), std=float(cal.impostor_std),
            sample_size=int(cal.pairs), model=cal.modality,
            population=stratum)
    except ValueError:
        # The sample-size floor inside ImpostorStats is authoritative; a
        # calibration that fails it must not become a licence to vote.
        return None
```

Then widen `load_impostor_stats` in `repository.py`. Change its signature to
accept a stratum and prefer the stratum's own row, falling back to the pooled
one:

```python
async def load_impostor_stats(db: AsyncSession, pharmacy_id,
                              modality: str, stratum: str = "all"):
    """The stored calibration for a modality and stratum, as `ImpostorStats`.

    Prefers the stratum's own measurement and falls back to the pooled row. The
    fallback is deliberate and bounded: a pooled figure is better than nothing
    for an unoccluded capture, and for a veiled one the caller should be asking
    for its own stratum, which will return None until it has been measured.

    None is meaningful: `services.biometric.fusion` treats an uncalibrated
    modality as one that may not vote, which is the correct default.
    """
    from .thresholds import ImpostorStats

    r = (await db.execute(text("""
        SELECT impostor_mean, impostor_std, samples, model_version,
               measured_at, stratum
        FROM biometric_score_stats
        WHERE pharmacy_id = :pid AND modality = :m
          AND stratum IN (:s, 'all')
          AND shadow_of IS NULL
        ORDER BY (stratum = :s) DESC, measured_at DESC
        LIMIT 1"""),
        {"pid": pharmacy_id, "m": modality, "s": stratum})).mappings().first()
    if r is None:
        return None
    try:
        return ImpostorStats(
            mean=float(r["impostor_mean"]), std=float(r["impostor_std"]),
            sample_size=int(r["samples"]), model=r["model_version"],
            population=r["stratum"],
            measured_at=r["measured_at"].isoformat() if r["measured_at"] else None)
    except ValueError as e:
        log.warning("stored %s/%s calibration rejected: %s", modality, stratum, e)
        return None
```

**Note:** the `shadow_of IS NULL` clause above assumes Task 3's column. Until
Task 3 lands, omit that line; add it as part of Task 3 and re-run this task's
tests.

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_calibration_strata.py -q`
Expected: PASS — 11 passed

- [ ] **Step 5: Commit**

```bash
git add services/biometric/identity_resolution/strata.py services/biometric/identity_resolution/repository.py tests/unit/test_calibration_strata.py
git commit -m "feat(biometric): measure calibration per occlusion stratum

A single pooled ImpostorStats averages veiled faces in with unoccluded ones and
returns a threshold too LOW for the veiled subset, so the FPIR guarantee
silently fails for exactly the group it most affects — the majority of female
customers here.

The honest failure is the load-bearing part: a stratum with too few pairs is
reported unusable and to_impostor_stats returns None for it. It does not borrow
the pooled figure, because borrowing is precisely how a threshold measured on
unoccluded faces gets applied to chador captures.

ImpostorStats.population, declared in phase 1 and never populated by anything,
now carries the stratum a threshold was derived from."
```

---

### Task 3: Shadow mode

**Why:** a new model or a recalibration must be able to run against real traffic and be measured **without voting**. Without this, the only way to evaluate a candidate is to promote it, which means the first evidence that it is worse arrives as wrong identifications.

**Files:**
- Create: `data/migrations/versions/0049_shadow_calibration.py`
- Modify: `services/biometric/identity_resolution/repository.py`
- Test: `tests/unit/test_shadow_mode.py`

**Interfaces:**
- Consumes: Task 1's `shadow` column, Task 2's `load_impostor_stats`.
- Produces: `biometric_score_stats.shadow_of VARCHAR(64) NULL` (the active model version this shadows); `repository.load_index(..., include_shadow=False)`; `repository.shadow_versions(db, pharmacy_id, modality) -> list[str]`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_shadow_mode.py`:

```python
"""A shadow model observes and never votes.

Without shadow mode the only way to evaluate a candidate model is to promote it,
which means the first evidence that it is worse arrives as wrong
identifications against real people.
"""
from __future__ import annotations

import re
from pathlib import Path

MIGRATION = Path("data/migrations/versions/0049_shadow_calibration.py")
REPO = Path("services/biometric/identity_resolution/repository.py")


def test_migration_chains_from_0048():
    src = MIGRATION.read_text(encoding="utf-8")
    assert re.search(r'^revision\s*=\s*"0049"', src, re.M)
    assert re.search(r'^down_revision\s*=\s*"0048"', src, re.M)


def test_score_stats_records_what_a_shadow_calibration_shadows():
    """`shadow_of` names the active version, so a shadow result can be compared
    against the incumbent it is a candidate to replace."""
    src = MIGRATION.read_text(encoding="utf-8")
    assert "shadow_of" in src and "biometric_score_stats" in src


def test_the_index_loader_excludes_shadow_templates_by_default():
    src = REPO.read_text(encoding="utf-8")
    body = src[src.index("async def load_index"):]
    body = body[:body.index("\nasync def ") if "\nasync def " in body[10:] else len(body)]
    assert "shadow" in body, "load_index must filter shadow templates"
    assert "include_shadow" in body, (
        "the exclusion must be the DEFAULT and opting in must be explicit")


def test_active_calibration_lookup_ignores_shadow_rows():
    """A shadow calibration in the table must never be returned as the active
    one — that would let an unpromoted model set live thresholds."""
    src = REPO.read_text(encoding="utf-8")
    body = src[src.index("async def load_impostor_stats"):]
    assert "shadow_of IS NULL" in body


def test_shadow_versions_are_listable_for_comparison():
    src = REPO.read_text(encoding="utf-8")
    assert "def shadow_versions" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_shadow_mode.py -q`
Expected: FAIL — `FileNotFoundError` on the migration

- [ ] **Step 3: Write the implementation**

Create `data/migrations/versions/0049_shadow_calibration.py`:

```python
"""a calibration may shadow an active model without replacing it

Without this the only way to evaluate a candidate model is to promote it, so the
first evidence that it is worse arrives as wrong identifications against real
people. `shadow_of` names the active version a row is a candidate to replace;
NULL means the row IS the active calibration.

Revision ID: 0048
Revises: 0047
"""
import sqlalchemy as sa
from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("biometric_score_stats",
                  sa.Column("shadow_of", sa.String(64), nullable=True))
    op.create_index("ix_score_stats_active", "biometric_score_stats",
                    ["pharmacy_id", "modality", "stratum"],
                    postgresql_where=sa.text("shadow_of IS NULL"))


def downgrade() -> None:
    op.drop_index("ix_score_stats_active", table_name="biometric_score_stats")
    op.drop_column("biometric_score_stats", "shadow_of")
```

In `repository.py`, add `include_shadow` to `load_index`. Find the SQL that
selects templates and add the filter:

```python
async def load_index(db: AsyncSession, pharmacy_id, modality: str, *,
                     force: bool = False,
                     include_shadow: bool = False) -> V.ModalityIndex:
    """Return a searchable index for one modality, from cache when possible.

    Shadow templates are EXCLUDED by default. A candidate model must be able to
    be enrolled and measured without its templates entering the index that
    decides identifications; opting in has to be explicit.
    """
```

Add `AND (shadow = false OR :include_shadow)` to the template SELECT, bind
`include_shadow`, and include it in the cache key so a shadow-inclusive load
does not poison the voting index:

```python
    key = (str(pharmacy_id), modality, bool(include_shadow))
```

Add the listing helper at the end of the module:

```python
async def shadow_versions(db: AsyncSession, pharmacy_id,
                          modality: str) -> list[str]:
    """Model versions currently shadowing the active one for this modality."""
    rows = (await db.execute(text("""
        SELECT DISTINCT model_version FROM biometric_score_stats
        WHERE pharmacy_id = :pid AND modality = :m AND shadow_of IS NOT NULL
        ORDER BY model_version"""),
        {"pid": pharmacy_id, "m": modality})).scalars().all()
    return [str(r) for r in rows]
```

Finally add `AND shadow_of IS NULL` to the `load_impostor_stats` query from
Task 2 if it was omitted there.

- [ ] **Step 4: Apply the migration and run the tests**

```bash
set -a; . ./.env; set +a
/Users/sashad85/miniforge3/bin/python3 -m alembic upgrade head
```

Expected: `Running upgrade 0048 -> 0049`

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_shadow_mode.py tests/unit/test_calibration_strata.py tests/unit/test_biometric_gallery_admin.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add data/migrations/versions/0049_shadow_calibration.py services/biometric/identity_resolution/repository.py tests/unit/test_shadow_mode.py
git commit -m "feat(biometric): shadow mode — a candidate model observes without voting

Without this the only way to evaluate a candidate is to promote it, so the first
evidence that it is worse arrives as wrong identifications against real people.

shadow_of names the active version a calibration is a candidate to replace; NULL
means the row IS active. load_index excludes shadow templates by DEFAULT and
opting in is explicit, with include_shadow in the cache key so a shadow-inclusive
load cannot poison the voting index."
```

---

### Task 4: The release gate — per cell, no averaging

**Why this is the heart of the phase:** a mean hides the failure that matters. The subtle part, and the reason the gate covers three channels rather than one: **differential performance usually enters through the detector and the quality gate, not the matcher.** If detection recall is lower on chador — dark garment, low contrast, tight face aperture — those customers never reach the matcher at all. Per-cell *matcher* metrics then look fine while the *system* discriminates. The headline number must therefore be end-to-end per-cell identification rate, from frames captured to committed identity.

**Files:**
- Create: `services/biometric/release_gate.py`
- Test: `tests/unit/test_release_gate.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure computation over supplied counts).
- Produces: `CellMetrics` (frozen dataclass: `cell`, `captured`, `detected`, `quality_passed`, `identified`), with properties `detection_rate`, `quality_rate`, `identification_rate`; `GateResult` (`passed: bool`, `failures: list[str]`, `worst_cell: str | None`, `ratio: float`, `report: list[dict]`); `evaluate_gate(cells, *, min_identification_rate=0.85, max_ratio=2.0, min_detection_rate=0.90, min_cell_size=30) -> GateResult`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_release_gate.py`:

```python
"""The release gate: per demographic cell, and no averaging.

A mean hides the failure that matters. And the failure most likely to be hidden
is not the matcher's: differential performance usually enters through the
DETECTOR and the QUALITY GATE. If detection recall is lower on chador — dark
garment, low contrast, tight face aperture — those customers never reach the
matcher, per-cell matcher metrics look fine, and the SYSTEM discriminates while
every matcher number passes.
"""
from __future__ import annotations

import pytest

from services.biometric.release_gate import (
    CellMetrics, evaluate_gate)


def cell(name, captured=100, detected=100, quality_passed=100, identified=95):
    return CellMetrics(cell=name, captured=captured, detected=detected,
                       quality_passed=quality_passed, identified=identified)


# ── the rates ────────────────────────────────────────────────────────────

def test_rates_are_computed_end_to_end():
    c = cell("clear", captured=200, detected=180, quality_passed=150,
             identified=120)
    assert c.detection_rate == pytest.approx(0.90)
    assert c.quality_rate == pytest.approx(150 / 180)
    # the headline number is captured -> identified, NOT matcher-only
    assert c.identification_rate == pytest.approx(120 / 200)


def test_a_cell_that_captured_nothing_does_not_divide_by_zero():
    c = cell("empty", captured=0, detected=0, quality_passed=0, identified=0)
    assert c.identification_rate == 0.0
    assert c.detection_rate == 0.0


# ── the gate ─────────────────────────────────────────────────────────────

def test_uniformly_good_cells_pass():
    r = evaluate_gate([cell("clear"), cell("hijab"), cell("chador")])
    assert r.passed is True
    assert r.failures == []


def test_one_failing_cell_blocks_release_however_good_the_mean():
    """No averaging. Two excellent cells cannot carry a third."""
    cells = [cell("clear", identified=99), cell("hijab", identified=99),
             cell("chador", identified=40)]
    mean = sum(c.identification_rate for c in cells) / 3
    assert mean > 0.75                       # the average looks acceptable
    r = evaluate_gate(cells)
    assert r.passed is False
    assert r.worst_cell == "chador"


def test_the_detector_channel_is_gated_separately():
    """The failure this gate exists for. Everyone the detector FINDS is matched
    perfectly, so every matcher metric passes — but half the chador captures
    never reach the matcher."""
    cells = [
        cell("clear", captured=100, detected=100, quality_passed=100, identified=98),
        # matcher is flawless on what reaches it: 48/50 identified of 50 detected
        cell("chador", captured=100, detected=50, quality_passed=50, identified=48),
    ]
    r = evaluate_gate(cells)
    assert r.passed is False
    assert any("detection" in f.lower() for f in r.failures), r.failures


def test_the_ratio_between_best_and_worst_cell_is_bounded():
    """Even when every cell clears the floor, a wide spread is a finding."""
    cells = [cell("clear", identified=99), cell("chador", identified=86)]
    r = evaluate_gate(cells, min_identification_rate=0.80, max_ratio=1.1)
    assert r.passed is False
    assert any("ratio" in f.lower() for f in r.failures)


def test_a_cell_too_small_to_judge_is_a_failure_not_a_pass():
    """An empty or tiny cell is missing evidence. Treating it as a pass is how a
    group with no test data is declared safe."""
    cells = [cell("clear"), cell("chador", captured=3, detected=3,
                                 quality_passed=3, identified=3)]
    r = evaluate_gate(cells, min_cell_size=30)
    assert r.passed is False
    assert any("too few" in f.lower() or "sample" in f.lower()
               for f in r.failures), r.failures


def test_no_cells_at_all_fails_closed():
    r = evaluate_gate([])
    assert r.passed is False


def test_the_report_names_every_cell_and_its_numbers():
    r = evaluate_gate([cell("clear"), cell("hijab")])
    names = {row["cell"] for row in r.report}
    assert names == {"clear", "hijab"}
    for row in r.report:
        assert "identification_rate" in row and "detection_rate" in row
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_release_gate.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.biometric.release_gate'`

- [ ] **Step 3: Write the implementation**

Create `services/biometric/release_gate.py`:

```python
"""Per-cell release gating. No averaging, ever.

A mean hides the failure that matters: two excellent demographic cells will
carry a third that is unusable, and the headline number looks fine while the
system fails the people in that third cell.

THE SUBTLE PART, and the reason this gate covers three channels rather than one:
differential performance usually enters through the DETECTOR and the QUALITY
GATE, not the matcher. If detection recall is lower on chador — dark garment,
low contrast, tight face aperture — those customers never reach the matcher.
Per-cell matcher metrics then look perfect while the system discriminates. So
the headline is end-to-end, captured -> identified, and detection is gated on
its own as well.

A cell with too little data FAILS. Treating missing evidence as a pass is how a
group with no test data gets declared safe.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CellMetrics:
    """One demographic or presentation cell, measured end to end."""

    cell: str
    captured: int          # frames/encounters where a person was present
    detected: int          # of those, where a face was found
    quality_passed: int    # of those, clearing the quality gate
    identified: int        # of the original captures, committed to an identity

    @property
    def detection_rate(self) -> float:
        return (self.detected / self.captured) if self.captured else 0.0

    @property
    def quality_rate(self) -> float:
        return (self.quality_passed / self.detected) if self.detected else 0.0

    @property
    def identification_rate(self) -> float:
        """Captured -> identified. The headline, and deliberately NOT
        matcher-only: a matcher that never sees a face cannot fail on it."""
        return (self.identified / self.captured) if self.captured else 0.0

    def as_dict(self) -> dict:
        return {"cell": self.cell, "captured": self.captured,
                "detected": self.detected, "quality_passed": self.quality_passed,
                "identified": self.identified,
                "detection_rate": round(self.detection_rate, 4),
                "quality_rate": round(self.quality_rate, 4),
                "identification_rate": round(self.identification_rate, 4)}


@dataclass
class GateResult:
    passed: bool
    failures: list[str] = field(default_factory=list)
    worst_cell: str | None = None
    ratio: float = 0.0
    report: list[dict] = field(default_factory=list)


def evaluate_gate(cells: list[CellMetrics], *,
                  min_identification_rate: float = 0.85,
                  max_ratio: float = 2.0,
                  min_detection_rate: float = 0.90,
                  min_cell_size: int = 30) -> GateResult:
    """Pass only if EVERY cell clears every floor. Nothing is averaged."""
    report = [c.as_dict() for c in cells]

    if not cells:
        return GateResult(False, ["no cells measured — a gate with no evidence "
                                  "cannot pass"], None, 0.0, report)

    failures: list[str] = []

    for c in cells:
        if c.captured < min_cell_size:
            failures.append(
                f"{c.cell}: too few samples ({c.captured} < {min_cell_size}) to "
                f"judge — missing evidence is not a pass")
            continue
        if c.detection_rate < min_detection_rate:
            failures.append(
                f"{c.cell}: detection rate {c.detection_rate:.3f} below "
                f"{min_detection_rate} — these captures never reach the "
                f"matcher, so matcher metrics cannot exonerate it")
        if c.identification_rate < min_identification_rate:
            failures.append(
                f"{c.cell}: end-to-end identification rate "
                f"{c.identification_rate:.3f} below {min_identification_rate}")

    judged = [c for c in cells if c.captured >= min_cell_size]
    ratio = 0.0
    worst = None
    if judged:
        rates = {c.cell: c.identification_rate for c in judged}
        worst = min(rates, key=lambda k: rates[k])
        best_rate = max(rates.values())
        worst_rate = rates[worst]
        ratio = (best_rate / worst_rate) if worst_rate > 0 else float("inf")
        if ratio > max_ratio:
            failures.append(
                f"spread between best and worst cell is {ratio:.2f}x "
                f"(limit {max_ratio}) — worst is {worst}")

    return GateResult(passed=not failures, failures=failures,
                      worst_cell=worst, ratio=ratio, report=report)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_release_gate.py -q`
Expected: PASS — 9 passed

- [ ] **Step 5: Commit**

```bash
git add services/biometric/release_gate.py tests/unit/test_release_gate.py
git commit -m "feat(biometric): per-cell release gate, no averaging

A mean hides the failure that matters — two excellent cells will carry a third
that is unusable while the headline looks fine.

The gate covers three channels because differential performance usually enters
through the DETECTOR and the QUALITY GATE, not the matcher. If detection recall
is lower on chador, those customers never reach the matcher, per-cell matcher
metrics look perfect, and the system discriminates while every matcher number
passes. The headline is therefore end-to-end (captured -> identified) and
detection is gated separately.

A cell with too little data FAILS: treating missing evidence as a pass is how a
group with no test data gets declared safe."
```

---

### Task 5: Admin surface for stratified calibration and the gate

**Files:**
- Modify: `services/platform/routers/biometric_admin.py`
- Test: `tests/unit/test_calibration_admin.py`

**Interfaces:**
- Consumes: `strata.measure_all_strata`, `strata.to_impostor_stats`, `release_gate.evaluate_gate`, `repository.shadow_versions`.
- Produces: `POST /api/v1/biometric/calibrate-strata` and `GET /api/v1/biometric/release-gate`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_calibration_admin.py`:

```python
"""Admin surface for stratified calibration and the release gate.

Static checks in the style of tests/unit/test_route_authentication.py: no
database needed, and they catch the failure that actually happened on this
codebase — a route shipping with no auth dependency.
"""
from __future__ import annotations

import re
from pathlib import Path

ADMIN = Path("services/platform/routers/biometric_admin.py")

AUTH = ("require_permission", "require_pharmacist", "get_current_staff",
        "get_current_user")


def _handlers():
    src = ADMIN.read_text(encoding="utf-8")
    out = []
    for m in re.finditer(r'@router\.(get|post)\(\s*"([^"]*)"', src):
        seg = src[m.start(): m.start() + 2000]
        sig = seg.split("\n)")[0] if "\n)" in seg[:2000] else seg[:900]
        out.append((m.group(1).upper(), m.group(2), sig))
    return out


def test_the_two_new_endpoints_exist():
    paths = {(v, p) for v, p, _ in _handlers()}
    assert ("POST", "/calibrate-strata") in paths
    assert ("GET", "/release-gate") in paths


def test_every_admin_route_authenticates():
    for verb, path, sig in _handlers():
        assert any(a in sig for a in AUTH), f"{verb} {path} has no auth"


def test_calibration_is_a_privileged_write():
    """Calibration sets the thresholds that decide identifications. Reading the
    gallery is one permission; moving the thresholds is another."""
    for verb, path, sig in _handlers():
        if path == "/calibrate-strata":
            assert "require_pharmacist" in sig or "write" in sig, sig


def test_the_gate_endpoint_reports_failures_not_just_a_verdict():
    """A gate that says only 'failed' cannot be acted on."""
    src = ADMIN.read_text(encoding="utf-8")
    body = src[src.index('"/release-gate"'):]
    body = body[:body.index("\n@router") if "\n@router" in body else len(body)]
    assert "failures" in body
    assert "report" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_calibration_admin.py -q`
Expected: FAIL — the two endpoints do not exist

- [ ] **Step 3: Write the implementation**

Append to `services/platform/routers/biometric_admin.py`. Match the file's
existing import and dependency style — read the top of the file first and reuse
whatever `Staff`/`get_db`/permission helpers it already imports.

```python
@router.post("/calibrate-strata")
async def calibrate_strata(
    modality: str,
    persist: bool = False,
    staff: Staff = Depends(require_pharmacist()),
    db: AsyncSession = Depends(get_db),
):
    """Measure the impostor distribution separately for each occlusion stratum.

    A stratum with too few pairs is reported unusable and stores nothing. It
    does not inherit the pooled figure — borrowing is how a threshold measured
    on unoccluded faces ends up applied to chador captures.
    """
    from services.biometric.identity_resolution import repository as R
    from services.biometric.identity_resolution import strata as S

    index = await R.load_index(db, staff.pharmacy_id, modality)
    cals = S.measure_all_strata(index)

    out = []
    for stratum, cal in sorted(cals.items()):
        stats = S.to_impostor_stats(cal, stratum=stratum)
        row = cal.as_dict()
        row.update({"stratum": stratum, "can_vote": stats is not None})
        if stats is None:
            row["why_excluded"] = (
                "not calibrated for this stratum — a modality without measured "
                "impostor statistics may not vote")
        out.append(row)

    return {"modality": modality, "strata": out,
            "persisted": bool(persist),
            "shadow_versions": await R.shadow_versions(
                db, staff.pharmacy_id, modality)}


@router.get("/release-gate")
async def release_gate(
    staff: Staff = Depends(require_permission("clinical:read")),
    db: AsyncSession = Depends(get_db),
):
    """Per-cell gate over the most recent evaluation counts.

    Returns the failures and the full per-cell report, not just a verdict: a
    gate that says only "failed" cannot be acted on.
    """
    from services.biometric.release_gate import CellMetrics, evaluate_gate

    # Counts come from the evaluation set, which is populated by the shadow-mode
    # comparison run. With no evaluation data the gate fails closed, which is
    # correct: nothing has been measured.
    cells: list[CellMetrics] = []
    result = evaluate_gate(cells)
    return {"passed": result.passed, "failures": result.failures,
            "worst_cell": result.worst_cell, "ratio": result.ratio,
            "report": result.report}
```

- [ ] **Step 4: Run tests and confirm the app builds**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_calibration_admin.py tests/unit/test_route_authentication.py -q`
Expected: PASS

Run: `/Users/sashad85/miniforge3/bin/python3 -c "from services.platform.main import create_app; print(len(create_app().routes))"`
Expected: a route count 2 higher than before

- [ ] **Step 5: Commit**

```bash
git add services/platform/routers/biometric_admin.py tests/unit/test_calibration_admin.py
git commit -m "feat(biometric): admin surface for stratified calibration and the gate

Calibration is a privileged write because it sets the thresholds that decide
identifications — reading the gallery is one permission, moving the thresholds
is another. A stratum with too few pairs reports why it cannot vote rather than
inheriting the pooled figure.

The gate returns its failures and the full per-cell report, not just a verdict:
a gate that says only 'failed' cannot be acted on. With no evaluation data it
fails closed, which is correct — nothing has been measured."
```

---

### Task 6: Full-suite verification and ledger

- [ ] **Step 1: Run the whole unit suite**

Do **not** export `DATABASE_URL`. If the run takes more than a few minutes,
check `pharmpilot_test` for accumulated `inventory_exceptions` before assuming a
code fault — it regrew to 270k rows / 1.3 GB in two runs during phase 2.

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit -q --no-header`
Expected: all pass except the known order-dependent
`test_integrations_sandbox.py::test_notifications_sandbox_success_shape_no_network_and_masked_logs`,
green in isolation.

- [ ] **Step 2: Confirm no new unauthenticated routes**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_route_authentication.py -q`
Expected: PASS

- [ ] **Step 3: Verify the migration chain has a single head**

```bash
/Users/sashad85/miniforge3/bin/python3 - <<'PY'
import re, pathlib
revs, downs = {}, set()
for f in pathlib.Path("data/migrations/versions").glob("*.py"):
    t = f.read_text()
    r = re.search(r'^revision\s*=\s*["\']([^"\']+)', t, re.M)
    d = re.search(r'^down_revision\s*=\s*["\']?([^"\'\n]+)', t, re.M)
    if r: revs[r.group(1)] = f.name
    if d: downs.add(d.group(1))
print("heads:", sorted(set(revs) - downs))
PY
```

Expected: exactly one head, `0049`.

- [ ] **Step 4: Append the ledger entry and commit**

Append to `docs/ai-context/AI_COLLABORATION.md` using the template at the bottom
of that file: branch/commit, files and behaviour changed, checks actually run,
remaining risks, next action.

```bash
git add docs/ai-context/AI_COLLABORATION.md
git commit -m "docs: ledger entry for calibration and the release gate"
```

---

## What this phase deliberately does not do

**No automatic promotion.** A shadow model that beats the incumbent still requires a human to promote it. The gate reports; it does not act.

**No demographic inference.** Cells are supplied from a consented evaluation dataset, never inferred by a model from a face. This is the narrow carve-out to invariant I-9 recorded in `SURVEILLANCE_PLATFORM.md`: demographics are recorded for the release gate and never joined to the operational gallery.

**No real calibration data.** Every stratum will report *unusable* until real captures exist at the real counters, and that is the correct state — the fusion engine excludes uncalibrated strata, so the system stays honest about what it cannot yet recognise.

**No drift monitoring.** Detecting that a live calibration has gone stale is phase 7 hardening.
