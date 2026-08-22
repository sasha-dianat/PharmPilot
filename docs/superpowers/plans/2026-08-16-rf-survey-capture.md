# RF Survey Capture Implementation Plan — Phase 4 of 7

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the RF engine the two things it needs to actually work — a stored access-point registry and a surveyed radio map — so fingerprinting stops being dead code.

**Architecture:** `services/core/rf_mapping` was built and tested in phase 1, with fingerprinting as the documented default because trilateration's free-space assumption fails indoors. But the live endpoint calls `locate(samples, aps, RadioMap([]))` — **an empty radio map, hard-coded** — so every request falls through to trilateration, the method the module itself describes as 5–15 m and "close to useless" in a depot. Access points are supplied per-request in the body rather than stored, so no caller can share a survey.

**The governing point:** this phase does not improve the algorithm. It supplies the data the algorithm has been missing, and makes the difference measurable.

**Tech Stack:** Python 3.12, numpy, FastAPI, SQLAlchemy async, PostgreSQL, Alembic.

## Global Constraints

- Python interpreter: `/Users/sashad85/miniforge3/bin/python3`
- Tests from repo root: `/Users/sashad85/miniforge3/bin/python3 -m pytest`
- **No new third-party dependencies.** numpy only.
- Every new route MUST carry an auth dependency or be added to the allowlist in `tests/unit/test_route_authentication.py` with a written reason.
- Migration head is `0049`. Task 1 adds `0050`.
- **Migrate BOTH databases.** `tests/conftest.py:12` points at `pharmpilot_test` via `setdefault`; phase 3 lost six tests to migrating only the dev one.
- **Do not export `DATABASE_URL`** when running the suite — `setdefault` means an exported value silently redirects the tests to that database.
- Every new database column MUST be mapped on its model. `test_model_column_parity` will catch it otherwise, and an unmapped column silently discards writes.
- A stored coordinate MUST carry `rf_uncertainty_m` — `ck_surv_obs_rf_uncertainty_required`.
- RF locates a **device**, never a person. Nothing in this phase may join an RF fix to a biometric identity.
- Persian/RTL for user-facing strings; internal identifiers stay English.

---

## What already exists

| Component | Path | State |
|---|---|---|
| `filter_samples`, MAD outlier rejection | `rf_mapping/__init__.py:79` | Tested |
| `trilaterate` (closed-form least squares) | `:117` | Tested, 5 m uncertainty floor |
| `fingerprint_locate` (weighted kNN) | `:164` | Tested — **and unreachable in production** |
| `locate` (fingerprint, else trilaterate) | `:208` | Tested |
| `occupancy_heatmap` | `:220` | Tested |
| `POST /surveillance/rf/batch` | `routers/surveillance.py` | Live, but passes `RadioMap([])` |
| `surveillance_observations.rf_*` | migration `0032` | Stores fixes with uncertainty |

## The three gaps

1. **No access-point registry.** APs arrive in the request body, so every edge caller must carry the whole layout and no two callers can be guaranteed to agree.
2. **No radio map.** `RadioMap([])` is hard-coded, so `locate` always falls through to trilateration and the better method never runs.
3. **No survey capture.** Nothing lets someone walk the depot recording `(x, y) → {ap_id: rssi}`.

## File Structure

| File | Responsibility |
|---|---|
| `data/migrations/versions/0050_rf_survey.py` (create) | `rf_access_points` and `rf_fingerprints`. |
| `shared/models/rf_survey.py` (create) | Models for both, mapped column-for-column. |
| `shared/models/__init__.py` (modify) | Register them. |
| `services/core/rf_mapping/store.py` (create) | Load APs and the radio map from the database, with staleness. |
| `services/platform/routers/surveillance.py` (modify) | Survey endpoints; `rf/batch` uses the stored map. |
| `tests/unit/test_rf_store.py` (create) | Loading, caching, staleness, thin-fingerprint rejection. |
| `tests/unit/test_rf_survey_api.py` (create) | Endpoint shape and auth. |

---

### Task 1: Schema for access points and fingerprints

**Why the fingerprint stores RSSI as JSONB rather than rows:** a fingerprint is read as a whole vector — the kNN distance is computed across every AP the probe and the fingerprint share — so splitting it into one row per AP buys nothing and costs a join on the hot path.

**Why `surveyed_at` is not decoration:** a radio map is a photograph of a building's RF environment. Move a shelving run, replace an AP, and it is wrong in ways that produce confident bad fixes rather than obvious failures. Staleness has to be visible.

**Files:**
- Create: `data/migrations/versions/0050_rf_survey.py`
- Create: `shared/models/rf_survey.py`
- Modify: `shared/models/__init__.py`
- Test: `tests/unit/test_rf_store.py` (schema portion)

**Interfaces:**
- Consumes: nothing.
- Produces: table `rf_access_points(id, pharmacy_id, site, ap_id, x, y, tx_power_dbm, active, created_at, updated_at)` unique on `(pharmacy_id, site, ap_id)`; table `rf_fingerprints(id, pharmacy_id, site, x, y, rssi JSONB, ap_count, surveyed_at, surveyed_by, created_at, updated_at)`; models `RfAccessPoint` and `RfFingerprint` in `shared/models/rf_survey.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_rf_store.py`:

```python
"""Access points and a surveyed radio map, stored.

The RF engine has had fingerprinting since phase 1 and it has never run in
production: the live endpoint passes RadioMap([]), so locate() always falls
through to trilateration — the method the module itself calls 5-15m and "close
to useless" in a depot. This phase supplies the missing data.
"""
from __future__ import annotations

import re
from pathlib import Path

MIGRATION = Path("data/migrations/versions/0050_rf_survey.py")
MODEL = Path("shared/models/rf_survey.py")


def test_migration_chains_from_the_current_head():
    src = MIGRATION.read_text(encoding="utf-8")
    assert re.search(r'^revision\s*=\s*"0050"', src, re.M)
    assert re.search(r'^down_revision\s*=\s*"0049"', src, re.M)


def test_both_tables_are_created():
    src = MIGRATION.read_text(encoding="utf-8")
    assert "rf_access_points" in src
    assert "rf_fingerprints" in src


def test_an_access_point_is_unique_per_site():
    """The same BSSID can legitimately appear at two sites; within one site a
    duplicate would give the trilateration two contradictory positions."""
    src = MIGRATION.read_text(encoding="utf-8")
    assert "UniqueConstraint" in src or "unique_constraint" in src
    assert "ap_id" in src


def test_a_fingerprint_records_when_it_was_surveyed():
    """A radio map is a photograph of a building's RF environment. Move a
    shelving run and it is wrong in ways that produce confident bad fixes."""
    src = MIGRATION.read_text(encoding="utf-8")
    assert "surveyed_at" in src


def test_a_fingerprint_records_how_many_aps_it_saw():
    """A survey point that heard one AP cannot constrain a position. Storing the
    count makes a thin fingerprint filterable instead of silently weightless."""
    src = MIGRATION.read_text(encoding="utf-8")
    assert "ap_count" in src


def test_the_models_exist_and_are_registered():
    src = MODEL.read_text(encoding="utf-8")
    assert "class RfAccessPoint" in src
    assert "class RfFingerprint" in src
    init = Path("shared/models/__init__.py").read_text(encoding="utf-8")
    assert "rf_survey" in init
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_rf_store.py -q`
Expected: FAIL — `FileNotFoundError` on the migration

- [ ] **Step 3: Write the migration**

Create `data/migrations/versions/0050_rf_survey.py`:

```python
"""access-point registry and surveyed radio map

The RF engine has had weighted-kNN fingerprinting since phase 1 and it has never
run: the live endpoint passes RadioMap([]), so locate() always falls through to
trilateration, whose free-space assumption fails indoors at 5-15m error. These
two tables are the data it has been missing.

RSSI is JSONB rather than one row per AP because a fingerprint is read as a
whole vector — the kNN distance spans every AP the probe and the fingerprint
share — so splitting it costs a join on the hot path and buys nothing.

Revision ID: 0050
Revises: 0049
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID


revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rf_access_points",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("pharmacy_id", UUID(as_uuid=True),
                  sa.ForeignKey("pharmacies.id"), nullable=False, index=True),
        sa.Column("site", sa.String(20), nullable=False),
        sa.Column("ap_id", sa.String(64), nullable=False),
        sa.Column("x", sa.Numeric(8, 2), nullable=False),
        sa.Column("y", sa.Numeric(8, 2), nullable=False),
        sa.Column("tx_power_dbm", sa.Numeric(6, 2), nullable=False,
                  server_default="-40"),
        # Retired rather than deleted: a fix computed last month stays
        # explainable from the layout that existed when it was computed.
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        # The same BSSID may legitimately appear at two sites; a duplicate
        # within one site would give trilateration two contradictory positions.
        sa.UniqueConstraint("pharmacy_id", "site", "ap_id",
                            name="uq_rf_ap_per_site"),
        sa.CheckConstraint("site IN ('pharmacy', 'depot')", name="ck_rf_ap_site"),
    )

    op.create_table(
        "rf_fingerprints",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("pharmacy_id", UUID(as_uuid=True),
                  sa.ForeignKey("pharmacies.id"), nullable=False, index=True),
        sa.Column("site", sa.String(20), nullable=False),
        sa.Column("x", sa.Numeric(8, 2), nullable=False),
        sa.Column("y", sa.Numeric(8, 2), nullable=False),
        sa.Column("rssi", JSONB, nullable=False),
        # A survey point that heard one AP cannot constrain a position. Stored
        # so a thin fingerprint is filterable rather than silently weightless.
        sa.Column("ap_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("surveyed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("surveyed_by", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("site IN ('pharmacy', 'depot')",
                           name="ck_rf_fp_site"),
        sa.CheckConstraint("ap_count >= 0", name="ck_rf_fp_ap_count"),
    )
    op.create_index("ix_rf_fingerprints_site", "rf_fingerprints",
                    ["pharmacy_id", "site"])


def downgrade() -> None:
    op.drop_index("ix_rf_fingerprints_site", table_name="rf_fingerprints")
    op.drop_table("rf_fingerprints")
    op.drop_table("rf_access_points")
```

- [ ] **Step 4: Write the models**

Create `shared/models/rf_survey.py`:

```python
"""Access-point registry and surveyed radio map.

Both exist so `locate()` can use fingerprinting, which has been implemented
since phase 1 and unreachable in production because the live endpoint passed an
empty RadioMap. Trilateration's free-space path-loss assumption does not survive
a depot's shelving; fingerprinting learns the multipath instead of assuming it
away, which is why it is the default — when it has data.

Nothing here identifies a person. An access point knows where it is; a
fingerprint knows what the radio looked like at a spot on the floor.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (Boolean, CheckConstraint, DateTime, ForeignKey, Index,
                        Integer, Numeric, String, UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from shared.models.base import TimestampedBase


class RfAccessPoint(TimestampedBase):
    """One AP at a known position on a site's floor plan."""

    __tablename__ = "rf_access_points"

    pharmacy_id: Mapped[UUID] = mapped_column(
        ForeignKey("pharmacies.id"), nullable=False, index=True)
    site: Mapped[str] = mapped_column(String(20), nullable=False)
    ap_id: Mapped[str] = mapped_column(String(64), nullable=False)
    x: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)
    y: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)
    # Calibrated RSSI at 1 m. The path-loss model is only as good as this.
    tx_power_dbm: Mapped[float] = mapped_column(
        Numeric(6, 2), nullable=False, default=-40.0)
    # Retired rather than deleted: a fix computed last month stays explainable
    # from the layout that existed when it was computed.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "site", "ap_id", name="uq_rf_ap_per_site"),
        CheckConstraint("site IN ('pharmacy', 'depot')", name="ck_rf_ap_site"),
    )


class RfFingerprint(TimestampedBase):
    """What the radio looked like at one surveyed point on the floor."""

    __tablename__ = "rf_fingerprints"

    pharmacy_id: Mapped[UUID] = mapped_column(
        ForeignKey("pharmacies.id"), nullable=False, index=True)
    site: Mapped[str] = mapped_column(String(20), nullable=False)
    x: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)
    y: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)
    # {ap_id: rssi_dbm}. Read as a whole vector by the kNN distance, so one
    # row per AP would cost a join on the hot path and buy nothing.
    rssi: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # A survey point that heard one AP cannot constrain a position.
    ap_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # A radio map is a photograph of a building's RF environment. Move a
    # shelving run and it is wrong in ways that produce confident bad fixes
    # rather than obvious failures, so staleness has to be visible.
    surveyed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)
    surveyed_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True)

    __table_args__ = (
        Index("ix_rf_fingerprints_site", "pharmacy_id", "site"),
        CheckConstraint("site IN ('pharmacy', 'depot')", name="ck_rf_fp_site"),
        CheckConstraint("ap_count >= 0", name="ck_rf_fp_ap_count"),
    )
```

Register in `shared/models/__init__.py`, next to the other imports:

```python
from . import rf_survey  # noqa: F401  (RfAccessPoint, RfFingerprint — RF positioning)
```

- [ ] **Step 5: Apply to BOTH databases and verify**

Phase 3 lost six tests to migrating only the dev database. `conftest.py:12` points at `pharmpilot_test`.

```bash
PW=$(grep -oP '(?<=pharmpilot:)[^@]*' .env | head -1)
for DB in pharmpilot pharmpilot_test; do
  DATABASE_URL="postgresql+asyncpg://pharmpilot:$PW@127.0.0.1:5433/$DB" \
    /Users/sashad85/miniforge3/bin/python3 -m alembic upgrade head 2>&1 | tail -1
done
```

Expected: `Running upgrade 0049 -> 0050` twice.

Then confirm against the live database rather than reading the DDL:

```bash
PW=$(grep -oP '(?<=pharmpilot:)[^@]*' .env | head -1)
/Users/sashad85/miniforge3/bin/python3 - <<PY
import asyncio, asyncpg
async def main():
    c = await asyncpg.connect("postgresql://pharmpilot:$PW@127.0.0.1:5433/pharmpilot")
    for t in ("rf_access_points", "rf_fingerprints"):
        n = await c.fetchval("SELECT count(*) FROM information_schema.columns "
                             "WHERE table_name=\$1", t)
        print(f"{t}: {n} columns")
    await c.close()
asyncio.run(main())
PY
```

- [ ] **Step 6: Run the tests and commit**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_rf_store.py tests/unit/test_model_column_parity.py -q`
Expected: PASS — 6 new, and parity green because both models are mapped.

```bash
git add data/migrations/versions/0050_rf_survey.py shared/models/rf_survey.py shared/models/__init__.py tests/unit/test_rf_store.py
git commit -m "feat(rf): access-point registry and surveyed radio map

The RF engine has had weighted-kNN fingerprinting since phase 1 and it has never
run in production: the live endpoint passes RadioMap([]), so locate() always
falls through to trilateration — the method the module itself calls 5-15m and
close to useless in a depot. These two tables are the data it has been missing.

RSSI is JSONB because a fingerprint is read as a whole vector; splitting it into
one row per AP would cost a join on the hot path and buy nothing. ap_count is
stored so a survey point that heard one AP is filterable rather than silently
weightless. surveyed_at is not decoration: move a shelving run and a radio map
is wrong in ways that produce confident bad fixes rather than obvious failures."
```

---

### Task 2: Loading the registry and the map

**Why a cache, and why it is keyed by site:** the radio map is read on every RF ingest and written only during a survey. Reloading hundreds of fingerprints per request would dominate the request. The cache follows `repository._CACHE`'s shape so the two behave alike, including explicit invalidation on write.

**Files:**
- Create: `services/core/rf_mapping/store.py`
- Test: `tests/unit/test_rf_store.py` (append)

**Interfaces:**
- Consumes: `rf_mapping.AccessPoint`, `rf_mapping.Fingerprint`, `rf_mapping.RadioMap`.
- Produces: `async load_access_points(db, pharmacy_id, site) -> dict[str, AccessPoint]`; `async load_radio_map(db, pharmacy_id, site, *, min_aps=3, force=False) -> RadioMap`; `map_age_days(db, pharmacy_id, site) -> float | None`; `invalidate(pharmacy_id, site=None) -> int`; `STALE_AFTER_DAYS = 180`; `MIN_FINGERPRINT_APS = 3`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_rf_store.py`:

```python
# ── loading and filtering ────────────────────────────────────────────────

import pytest

from services.core.rf_mapping import store as S


def test_thin_fingerprints_are_excluded_from_the_map():
    """A survey point that heard fewer than three APs cannot constrain a
    position, and including it drags the weighted average toward wherever it
    happened to be. Filtered, not down-weighted."""
    rows = [
        {"x": 1.0, "y": 2.0, "rssi": {"a": -50, "b": -60, "c": -70}, "ap_count": 3},
        {"x": 9.0, "y": 9.0, "rssi": {"a": -80}, "ap_count": 1},
    ]
    fps = S.rows_to_fingerprints(rows, min_aps=3)
    assert len(fps) == 1
    assert (fps[0].x, fps[0].y) == (1.0, 2.0)


def test_a_fingerprint_keeps_every_ap_it_heard():
    rows = [{"x": 0.0, "y": 0.0, "rssi": {"a": -50, "b": -60, "c": -70},
             "ap_count": 3}]
    fp = S.rows_to_fingerprints(rows)[0]
    assert set(fp.rssi) == {"a", "b", "c"}


def test_an_empty_map_is_returned_rather_than_none():
    """locate() takes a RadioMap and checks its length. Returning None here
    would move the emptiness check into every caller."""
    assert len(S.rows_to_fingerprints([])) == 0


def test_staleness_is_reported_in_days():
    from datetime import datetime, timedelta, timezone
    old = datetime.now(timezone.utc) - timedelta(days=200)
    assert S.age_days(old) == pytest.approx(200, abs=1)
    assert S.age_days(None) is None


def test_a_map_older_than_the_threshold_is_flagged_stale():
    """Not blocked — flagged. A stale map still beats trilateration; the
    operator needs to know it is aging, not be locked out of positioning."""
    from datetime import datetime, timedelta, timezone
    old = datetime.now(timezone.utc) - timedelta(days=S.STALE_AFTER_DAYS + 10)
    fresh = datetime.now(timezone.utc) - timedelta(days=5)
    assert S.is_stale(old) is True
    assert S.is_stale(fresh) is False
    assert S.is_stale(None) is True          # never surveyed counts as stale


def test_the_cache_is_keyed_by_site():
    """The pharmacy and the depot have different floor plans and different APs.
    Sharing a slot would hand one site's radio map to the other."""
    S._CACHE.clear()
    from services.core.rf_mapping import RadioMap
    S._CACHE[("p1", "depot")] = (RadioMap([]), None)
    S._CACHE[("p1", "pharmacy")] = (RadioMap([]), None)
    assert len(S._CACHE) == 2
    assert S.invalidate("p1", "depot") == 1
    assert ("p1", "pharmacy") in S._CACHE


def test_invalidating_a_pharmacy_clears_every_site():
    S._CACHE.clear()
    from services.core.rf_mapping import RadioMap
    S._CACHE[("p1", "depot")] = (RadioMap([]), None)
    S._CACHE[("p1", "pharmacy")] = (RadioMap([]), None)
    assert S.invalidate("p1") == 2
    assert S._CACHE == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_rf_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.core.rf_mapping.store'`

- [ ] **Step 3: Write the implementation**

Create `services/core/rf_mapping/store.py`:

```python
"""Loading the access-point registry and the surveyed radio map.

Both are read on every RF ingest and written only during a survey, so they are
cached with explicit invalidation — the same shape as
`identity_resolution.repository._CACHE`, so the two behave alike.

The cache is keyed by (pharmacy, site) because the pharmacy and the depot have
different floor plans and different access points. Sharing a slot would hand one
site's radio map to the other and produce fixes that look plausible and are
somewhere else entirely.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import AccessPoint, Fingerprint, RadioMap

# A survey point that heard fewer than this cannot constrain a position.
MIN_FINGERPRINT_APS = 3
# A radio map ages as the building changes. Flagged, not blocked: a stale map
# still beats trilateration, and the operator needs to know rather than be
# locked out of positioning.
STALE_AFTER_DAYS = 180

_CACHE: dict[tuple[str, str], tuple[RadioMap, datetime | None]] = {}


def invalidate(pharmacy_id, site: str | None = None) -> int:
    """Drop cached radio maps after a survey write. Returns how many."""
    keys = [k for k in _CACHE
            if k[0] == str(pharmacy_id) and (site is None or k[1] == site)]
    for k in keys:
        _CACHE.pop(k, None)
    return len(keys)


def age_days(surveyed_at: datetime | None) -> float | None:
    if surveyed_at is None:
        return None
    now = datetime.now(timezone.utc)
    if surveyed_at.tzinfo is None:
        surveyed_at = surveyed_at.replace(tzinfo=timezone.utc)
    return (now - surveyed_at).total_seconds() / 86400.0


def is_stale(surveyed_at: datetime | None) -> bool:
    """Never surveyed counts as stale — there is nothing to trust."""
    age = age_days(surveyed_at)
    return True if age is None else age > STALE_AFTER_DAYS


def rows_to_fingerprints(rows, min_aps: int = MIN_FINGERPRINT_APS
                         ) -> list[Fingerprint]:
    """Database rows to fingerprints, dropping the ones too thin to help.

    Filtered rather than down-weighted: a point that heard one AP does not
    merely contribute little, it drags the inverse-distance weighted average
    toward wherever it happened to be.
    """
    out: list[Fingerprint] = []
    for r in rows:
        rssi = dict(r["rssi"] or {})
        if len(rssi) < min_aps:
            continue
        out.append(Fingerprint(x=float(r["x"]), y=float(r["y"]),
                               rssi={k: float(v) for k, v in rssi.items()}))
    return out


async def load_access_points(db: AsyncSession, pharmacy_id,
                             site: str) -> dict[str, AccessPoint]:
    """Active access points for a site, keyed by ap_id."""
    rows = (await db.execute(text("""
        SELECT ap_id, x, y, tx_power_dbm FROM rf_access_points
        WHERE pharmacy_id = :pid AND site = :s AND active = true"""),
        {"pid": pharmacy_id, "s": site})).mappings().all()
    return {r["ap_id"]: AccessPoint(ap_id=r["ap_id"], x=float(r["x"]),
                                    y=float(r["y"]),
                                    tx_power_dbm=float(r["tx_power_dbm"]))
            for r in rows}


async def load_radio_map(db: AsyncSession, pharmacy_id, site: str, *,
                         min_aps: int = MIN_FINGERPRINT_APS,
                         force: bool = False) -> RadioMap:
    """The surveyed radio map for a site, from cache when possible."""
    key = (str(pharmacy_id), site)
    if not force and key in _CACHE:
        return _CACHE[key][0]

    rows = (await db.execute(text("""
        SELECT x, y, rssi, surveyed_at FROM rf_fingerprints
        WHERE pharmacy_id = :pid AND site = :s
        ORDER BY surveyed_at DESC"""),
        {"pid": pharmacy_id, "s": site})).mappings().all()

    rmap = RadioMap(rows_to_fingerprints(rows, min_aps=min_aps))
    newest = rows[0]["surveyed_at"] if rows else None
    _CACHE[key] = (rmap, newest)
    return rmap


async def map_age_days(db: AsyncSession, pharmacy_id,
                       site: str) -> float | None:
    """How old the newest fingerprint for this site is, in days."""
    newest = (await db.execute(text("""
        SELECT max(surveyed_at) FROM rf_fingerprints
        WHERE pharmacy_id = :pid AND site = :s"""),
        {"pid": pharmacy_id, "s": site})).scalar()
    return age_days(newest)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_rf_store.py -q`
Expected: PASS — 13 passed

- [ ] **Step 5: Commit**

```bash
git add services/core/rf_mapping/store.py tests/unit/test_rf_store.py
git commit -m "feat(rf): load the access-point registry and radio map

Cached with explicit invalidation, the same shape as the identity repository's
cache so the two behave alike. Keyed by (pharmacy, site) because the pharmacy
and the depot have different floor plans and different APs — sharing a slot
would hand one site's radio map to the other and produce fixes that look
plausible and are somewhere else entirely.

Thin fingerprints are FILTERED, not down-weighted: a survey point that heard one
AP does not merely contribute little, it drags the inverse-distance weighted
average toward wherever it happened to be.

A map older than 180 days is flagged stale rather than blocked — a stale map
still beats trilateration, and the operator needs to know it is aging rather
than be locked out of positioning. Never surveyed counts as stale."
```

---

### Task 3: Survey capture and registry endpoints

**Files:**
- Modify: `services/platform/routers/surveillance.py`
- Test: `tests/unit/test_rf_survey_api.py`

**Interfaces:**
- Consumes: `store.load_access_points`, `store.invalidate`, models from Task 1.
- Produces: `POST /surveillance/rf/access-points` (upsert one AP), `POST /surveillance/rf/survey` (record one fingerprint), `GET /surveillance/rf/map-status` (coverage and staleness).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_rf_survey_api.py`:

```python
"""Survey capture endpoints.

Static checks in the style of tests/unit/test_route_authentication.py: no
database needed, and they catch the failure that actually happened on this
codebase — a route shipping with no auth dependency.
"""
from __future__ import annotations

import re
from pathlib import Path

ROUTER = Path("services/platform/routers/surveillance.py")

AUTH = ("require_permission", "require_pharmacist", "get_current_staff",
        "get_current_user", "require_ws_staff")


def _handlers():
    src = ROUTER.read_text(encoding="utf-8")
    out = []
    for m in re.finditer(r'@router\.(get|post)\(\s*"([^"]*)"', src):
        seg = src[m.start(): m.start() + 2000]
        sig = seg.split("\n)")[0] if "\n)" in seg[:2000] else seg[:900]
        out.append((m.group(1).upper(), m.group(2), sig))
    return out


def test_the_three_survey_endpoints_exist():
    paths = {(v, p) for v, p, _ in _handlers()}
    assert ("POST", "/rf/access-points") in paths
    assert ("POST", "/rf/survey") in paths
    assert ("GET", "/rf/map-status") in paths


def test_every_route_still_authenticates():
    for verb, path, sig in _handlers():
        assert any(a in sig for a in AUTH), f"{verb} {path} has no auth"


def test_recording_a_survey_point_is_a_write_permission():
    """A survey defines where the system thinks people are. Reading a heatmap
    is one permission; moving the reference frame is another."""
    for verb, path, sig in _handlers():
        if path in ("/rf/survey", "/rf/access-points"):
            assert "write" in sig, f"{path}: {sig}"


def test_a_survey_write_invalidates_the_cached_map():
    """Otherwise the next fix is computed against the map as it was before the
    survey, and the surveyor sees no effect from their work."""
    src = ROUTER.read_text(encoding="utf-8")
    body = src[src.index('"/rf/survey"'):]
    nxt = body.find("\n@router")
    body = body[:nxt] if nxt > 0 else body
    assert "invalidate" in body


def test_the_batch_endpoint_no_longer_hard_codes_an_empty_radio_map():
    """The defect this phase exists to fix: RadioMap([]) meant locate() always
    fell through to trilateration and fingerprinting never ran."""
    src = ROUTER.read_text(encoding="utf-8")
    body = src[src.index("async def ingest_rf_batch"):]
    nxt = body.find("\n@router")
    body = body[:nxt] if nxt > 0 else body
    assert "RadioMap([])" not in body, (
        "rf/batch still passes an empty radio map, so fingerprinting cannot run")
    assert "load_radio_map" in body


def test_the_batch_endpoint_uses_the_stored_access_points():
    """APs arriving in the request body meant every edge caller carried the
    whole layout and no two were guaranteed to agree."""
    src = ROUTER.read_text(encoding="utf-8")
    body = src[src.index("async def ingest_rf_batch"):]
    nxt = body.find("\n@router")
    body = body[:nxt] if nxt > 0 else body
    assert "load_access_points" in body


def test_the_fix_reports_which_method_produced_it():
    """Fingerprint and trilateration differ by roughly 3x in error. A caller
    that cannot tell them apart cannot weigh the result."""
    src = ROUTER.read_text(encoding="utf-8")
    body = src[src.index("async def ingest_rf_batch"):]
    nxt = body.find("\n@router")
    body = body[:nxt] if nxt > 0 else body
    assert "method" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_rf_survey_api.py -q`
Expected: FAIL — the three endpoints do not exist and `RadioMap([])` is still present

- [ ] **Step 3: Write the implementation**

**First, the imports.** `surveillance.py` currently imports only `select` from
sqlalchemy and does not import `json` at all, and the endpoints below need both:

```python
import json
from sqlalchemy import select, text
```

Then add the request models next to the existing ones:

```python
class AccessPointIn(BaseModel):
    site: str
    ap_id: str
    x: float
    y: float
    tx_power_dbm: float = -40.0
    active: bool = True


class SurveyPointIn(BaseModel):
    """One spot on the floor, and what the radio looked like there."""
    site: str
    x: float
    y: float
    samples: list[RssiIn]
```

Then the three endpoints:

```python
@router.post("/rf/access-points", status_code=201)
async def upsert_access_point(
    body: AccessPointIn,
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    """Register or move an access point.

    Stored rather than passed per-request: an AP layout carried in every edge
    request means no two callers are guaranteed to agree about where the
    reference points are.
    """
    from services.core.rf_mapping import store as S

    await db.execute(text("""
        INSERT INTO rf_access_points
            (pharmacy_id, site, ap_id, x, y, tx_power_dbm, active)
        VALUES (:pid, :site, :ap, :x, :y, :tx, :active)
        ON CONFLICT (pharmacy_id, site, ap_id) DO UPDATE
        SET x = EXCLUDED.x, y = EXCLUDED.y,
            tx_power_dbm = EXCLUDED.tx_power_dbm,
            active = EXCLUDED.active, updated_at = now()"""),
        {"pid": staff.pharmacy_id, "site": body.site, "ap": body.ap_id,
         "x": body.x, "y": body.y, "tx": body.tx_power_dbm,
         "active": body.active})
    await db.commit()
    # Moving an AP changes every trilateration that references it.
    S.invalidate(staff.pharmacy_id, body.site)
    return {"ap_id": body.ap_id, "site": body.site}


@router.post("/rf/survey", status_code=201)
async def record_survey_point(
    body: SurveyPointIn,
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    """Record what the radio looks like at a known point on the floor.

    This is the data fingerprinting has been missing. Walking a grid of these
    is what lets `locate()` learn the building's multipath instead of assuming
    a free-space path loss the shelving does not obey.
    """
    from services.core.rf_mapping import filter_samples, store as S

    cleaned = filter_samples([RssiSample(s.ap_id, s.rssi_dbm)
                              for s in body.samples])
    rssi = {s.ap_id: s.rssi_dbm for s in cleaned}

    await db.execute(text("""
        INSERT INTO rf_fingerprints
            (pharmacy_id, site, x, y, rssi, ap_count, surveyed_at, surveyed_by)
        VALUES (:pid, :site, :x, :y, CAST(:rssi AS jsonb), :n, now(), :by)"""),
        {"pid": staff.pharmacy_id, "site": body.site, "x": body.x, "y": body.y,
         "rssi": json.dumps(rssi), "n": len(rssi), "by": staff.id})
    await db.commit()
    # Otherwise the next fix uses the map as it was before this survey, and the
    # surveyor sees no effect from their work.
    S.invalidate(staff.pharmacy_id, body.site)

    return {"site": body.site, "x": body.x, "y": body.y,
            "ap_count": len(rssi),
            "usable": len(rssi) >= S.MIN_FINGERPRINT_APS,
            "note": None if len(rssi) >= S.MIN_FINGERPRINT_APS else
                    f"heard only {len(rssi)} access points; a point below "
                    f"{S.MIN_FINGERPRINT_APS} cannot constrain a position and "
                    f"is excluded from the map"}


@router.get("/rf/map-status")
async def rf_map_status(
    site: str = Query("depot"),
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    """Coverage and staleness of the radio map for a site."""
    from services.core.rf_mapping import store as S

    aps = await S.load_access_points(db, staff.pharmacy_id, site)
    rmap = await S.load_radio_map(db, staff.pharmacy_id, site, force=True)
    age = await S.map_age_days(db, staff.pharmacy_id, site)
    return {"site": site, "access_points": len(aps),
            "fingerprints": len(rmap), "map_age_days": age,
            "stale": S.STALE_AFTER_DAYS is not None and (
                age is None or age > S.STALE_AFTER_DAYS),
            "positioning": ("fingerprint" if len(rmap) else
                            "trilateration" if len(aps) >= 3 else "unavailable")}
```

Finally, rewrite the body of `ingest_rf_batch` to use the stored data:

```python
    from services.core.rf_mapping import store as S

    aps = await S.load_access_points(db, staff.pharmacy_id, body.site)
    # Any access points supplied in the request supplement the registry rather
    # than replacing it, so an edge node can report an AP the registry has not
    # been told about yet without silently overriding a surveyed position.
    for a in body.access_points:
        aps.setdefault(a["ap_id"], AccessPoint(**a))

    radio_map = await S.load_radio_map(db, staff.pharmacy_id, body.site)
    samples = [RssiSample(s.ap_id, s.rssi_dbm) for s in body.samples]
    fix = locate(samples, aps, radio_map)
```

- [ ] **Step 4: Run tests and confirm the app builds**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_rf_survey_api.py tests/unit/test_surveillance_ingest.py tests/unit/test_route_authentication.py -q`
Expected: PASS

Run: `/Users/sashad85/miniforge3/bin/python3 -c "from services.platform.main import create_app; print(len(create_app().routes))"`
Expected: a route count 3 higher than before

- [ ] **Step 5: Commit**

```bash
git add services/platform/routers/surveillance.py tests/unit/test_rf_survey_api.py
git commit -m "feat(rf): survey capture, and rf/batch finally uses the radio map

The defect this phase exists to fix: ingest_rf_batch passed RadioMap([]), so
locate() always fell through to trilateration and the weighted-kNN
fingerprinting written in phase 1 never ran once in production.

Access points are now stored rather than carried in every edge request, where no
two callers were guaranteed to agree about the reference frame. Request-supplied
APs supplement the registry rather than replacing it, so an edge node can report
one the registry has not been told about without silently overriding a surveyed
position.

A survey write invalidates the cached map, or the next fix uses the map as it
was before the survey and the surveyor sees no effect from their work."
```

---

### Task 4: Prove fingerprinting beats trilateration on stored data

**Why this task exists:** phase 1 has a test showing fingerprinting wins on a synthetic map built in memory. It has never been shown end to end through the store, and the whole justification for this phase is that difference. A claim nobody measured through the real path is a claim.

**Files:**
- Test: `tests/unit/test_rf_store.py` (append)

**Interfaces:**
- Consumes: `store.rows_to_fingerprints`, `rf_mapping.locate`, `trilaterate`, `fingerprint_locate`.
- Produces: nothing.

- [ ] **Step 1: Write the test**

Append to `tests/unit/test_rf_store.py`:

```python
# ── the whole point of the phase, measured ───────────────────────────────

import math

from services.core.rf_mapping import (AccessPoint, RssiSample, locate,
                                      trilaterate)

APS = {a.ap_id: a for a in [
    AccessPoint("ap-nw", 0.0, 0.0, -40.0),
    AccessPoint("ap-ne", 20.0, 0.0, -40.0),
    AccessPoint("ap-sw", 0.0, 15.0, -40.0),
]}


def _rssi(ap, x, y, n=2.5, attenuate=0.0):
    d = max(1.0, math.hypot(x - ap.x, y - ap.y))
    return ap.tx_power_dbm - 10.0 * n * math.log10(d) - attenuate


def _survey_rows(attenuated_ap="ap-sw", loss_db=12.0):
    """A 5m grid, with one AP attenuated by shelving — the physical distortion
    a free-space model cannot know about and a survey captures for free."""
    rows = []
    for gx in range(0, 21, 5):
        for gy in range(0, 16, 5):
            rssi = {ap.ap_id: _rssi(ap, gx, gy,
                                    attenuate=loss_db if ap.ap_id == attenuated_ap else 0.0)
                    for ap in APS.values()}
            rows.append({"x": float(gx), "y": float(gy), "rssi": rssi,
                         "ap_count": len(rssi)})
    return rows


def test_fingerprinting_beats_trilateration_through_the_store():
    """The justification for this whole phase, measured end to end rather than
    asserted. Shelving attenuates one AP; trilateration's free-space assumption
    cannot know that, and the survey captured it without being told."""
    from services.core.rf_mapping import RadioMap

    tx, ty = 10.0, 5.0
    probe = [RssiSample(ap.ap_id,
                        _rssi(ap, tx, ty, attenuate=12.0 if ap.ap_id == "ap-sw" else 0.0))
             for ap in APS.values()]

    rmap = RadioMap(S.rows_to_fingerprints(_survey_rows()))
    assert len(rmap) > 0, "the survey grid produced no usable fingerprints"

    fp_fix = locate(probe, APS, rmap)
    tri_fix = trilaterate(probe, APS)

    fp_err = math.hypot(fp_fix.x - tx, fp_fix.y - ty)
    tri_err = math.hypot(tri_fix.x - tx, tri_fix.y - ty)
    assert fp_fix.method == "fingerprint"
    assert fp_err < tri_err, f"fingerprint {fp_err:.2f}m vs trilateration {tri_err:.2f}m"


def test_with_no_survey_the_system_still_positions_by_trilateration():
    """Degradation, not failure. A depot with APs but no survey yet must still
    get a fix — with the larger uncertainty that method honestly carries."""
    from services.core.rf_mapping import RadioMap

    probe = [RssiSample(ap.ap_id, _rssi(ap, 8.0, 6.0)) for ap in APS.values()]
    fix = locate(probe, APS, RadioMap([]))
    assert fix is not None
    assert fix.method == "trilateration"
    assert fix.uncertainty_m >= 5.0     # the honest indoor floor
```

- [ ] **Step 2: Run it**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_rf_store.py -q -k "beats or trilateration"`
Expected: PASS. If fingerprinting does *not* win, do **not** adjust the test to make it pass — print both errors and investigate, because the phase's premise is then wrong.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_rf_store.py
git commit -m "test(rf): fingerprinting beats trilateration through the store

Phase 1 showed this on a radio map built in memory. It had never been shown end
to end through the stored path, and that difference is the entire justification
for this phase — a claim nobody measured through the real path is a claim.

Also pins graceful degradation: a depot with access points but no survey yet
still gets a fix, by trilateration, carrying the larger uncertainty that method
honestly has."
```

---

### Task 5: Full-suite verification and ledger

- [ ] **Step 1: Check the test database before blaming code**

`inventory_exceptions` stood at 270k rows / 1.3 GB after phase 2 and makes the suite take ~8 minutes. That is expected, not a fault.

```bash
PW=$(grep -oP '(?<=pharmpilot:)[^@]*' .env | head -1)
/Users/sashad85/miniforge3/bin/python3 - <<PY
import asyncio, asyncpg
async def main():
    c = await asyncpg.connect("postgresql://pharmpilot:$PW@127.0.0.1:5433/pharmpilot_test")
    print(await c.fetchval("SELECT count(*) FROM inventory_exceptions"), "rows")
    await c.close()
asyncio.run(main())
PY
```

- [ ] **Step 2: Run the whole unit suite**

Do **not** export `DATABASE_URL`.

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit -q --no-header`
Expected: all pass except the known order-dependent
`test_integrations_sandbox.py::test_notifications_sandbox_success_shape_no_network_and_masked_logs`,
green in isolation.

- [ ] **Step 3: Confirm auth, parity and a single migration head**

```bash
/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_route_authentication.py tests/unit/test_model_column_parity.py -q
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

Expected: both suites green, exactly one head at `0050`.

- [ ] **Step 4: Append the ledger entry and commit**

Append to `docs/ai-context/AI_COLLABORATION.md` using the template at the bottom of that file: branch/commit, files and behaviour changed, checks actually run, remaining risks, next action.

```bash
git add docs/ai-context/AI_COLLABORATION.md
git commit -m "docs: ledger entry for RF survey capture"
```

---

## What this phase deliberately does not do

**No survey UI.** Recording a fingerprint is an authenticated POST with a coordinate; the handheld interface for walking a grid belongs with the review UI in phase 6.

**No automatic re-survey.** Staleness is reported, never acted on. A map older than 180 days is flagged so an operator can decide — silently discarding a stale map would drop positioning to trilateration with no one told.

**No device identity.** `rf_device_ref` remains an opaque reference. Binding a device to a person is not in this phase and is not planned: RF locates a device, and MAC randomisation makes customer handsets both unreliable and collected without a basis.

**No pharmacy-side survey.** The depot is where positioning earns its place. Surveying the pharmacy floor is possible with the same endpoints if it is ever wanted.
