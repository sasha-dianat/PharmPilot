# Zone Rules and Reconciliation — Phase 5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the zone from a free-text string into a key the database enforces, then build the pick-reconciliation and rule-lifecycle machinery on top of it — with every rule shipping log-only and no output ever constituting an accusation.

**Architecture:** A new `vision_*` namespace in the `public` schema whose foreign keys point outward into `pharmacies`/`staff`/`pharmacy_shelves`/`inventory_movements` and never the reverse, so the namespace stays droppable and purgeable. Zones become a composite business key `(pharmacy_id, site, code)` that `surveillance_observations` references directly. Anomalies are one row per occurrence in the vision namespace — **not** in `inventory_exceptions`, for four measured structural reasons recorded below. Every rule firing is written to an append-only evidence log whether or not it alerted, which is what makes the alert budget countable and the two-week log-only window measurable.

**Tech Stack:** FastAPI, SQLAlchemy 2.x async, Alembic, PostgreSQL 16.13 (native `polygon` + GiST, no PostGIS), pytest.

---

## Global Constraints

Every task's requirements implicitly include this section.

- **Migration head is `0050`.** Phase 5a is `0051`. Each schema task gets **its own revision** — never edit an already-stamped revision.
- **Apply migrations to BOTH databases**, passing `DATABASE_URL` inline per invocation. **Never `export DATABASE_URL`** — `tests/conftest.py:12` uses `os.environ.setdefault`, so an exported value silently redirects the entire suite (including tests that `CREATE DATABASE` and write rows) at the dev database. Phase 3 lost six tests to migrating only the dev DB.
- **Copy `data/migrations/versions/0050_rf_survey.py` as the migration template**, never `0001_initial_schema.py` (the lone outlier using annotated Alembic-default form).
- **Constraint naming: `ck_<abbreviated-singular-subject>_<predicate>`, dropping the table's namespace word.** Measured live: 23 of 28 CHECK constraints do this (`inventory_approvals` → `ck_approval_not_self`, `rf_access_points` → `ck_rf_ap_site`, `biometric_templates` → `ck_template_modality`). So phase 5 uses `ck_zone_*`, `ck_rule_*`, `ck_anomaly_*`, `ck_pick_*`, `ck_firing_*` — **NOT** `ck_vision_zone_*`. This is exactly the mistake phase 3 made.
- **UNIQUE naming: `uq_<subject>_<discriminator>`**, usually with a `_per_<scope>` suffix (`uq_rf_ap_per_site`, `uq_exception_open_fingerprint`).
- **Composite indexes are purpose-named** (`ix_anomaly_open_by_zone`), not column-named, and **`pharmacy_id` is always the leading column** (18 of 18 live composite indexes do this).
- **Column-width rule.** `String(N)` must satisfy `N >= max(len(v) for v in vocabulary)`. Migration 0046 widened a CHECK to admit `"identify_manually"` (17 chars) while the column stayed `VARCHAR(12)`. House headroom: the smallest multiple of 4 that is at least `max_len + 2`. **Every state column in this plan is `String(16)`.**
- **No native PG enums.** Zero exist in the database and zero appear in 50 migrations. `VARCHAR` + a named `CheckConstraint`.
- **`sa.DateTime(timezone=True)` always** (252 uses; naive `sa.DateTime()` appears zero times). Server default `sa.text("now()")`.
- **Always `server_default=sa.text("uuid_generate_v4()")` on `id`.** 22 live tables lack it and cannot be inserted by raw SQL.
- **`AuditedBase.is_deleted` has a Python default only** — the migration must write `server_default=sa.false()` explicitly or raw-SQL inserts hit a NOT NULL violation.
- **JSONB with a `server_default` and no `nullable=False` silently yields a NULLABLE column.** Already live on `stock_counts.scope` and `biometric_templates.capture_context`. Spell `nullable=False` out.
- **Register every model in `shared/models/__init__.py`.** That one line is necessary and sufficient — `services/platform/main.py` does **not** need an import (it stops at `audio` and has never been updated for `surveillance_log`, `rf_survey`, or `coverage`; importing any submodule executes the package `__init__`).
- **Verify model registration by hand.** `test_model_column_parity.py:115` compares only tables present in **both** the DB and `Base.metadata`, so a wholly-forgotten model drops out silently; `test_alembic_schema_parity.py:269` converts drift to `pytest.xfail()` and is already xfailing with 17 items. "I wrote a migration and forgot the model" is caught by **nothing**.
- **Run parity tests with `-p no:randomly`** — `Base.metadata` is global mutable state and the guard is order-sensitive.
- **Provenance rule.** A planning input declares its basis: `observed | sparse | no_history | declared_default`. Where no measurement exists, write **NULL** — never a fabricated fallback constant.
- **No LLM anywhere in this path.** The reconciler, the rule engine and the report are deterministic. Persian display strings are rendered from the deterministic explanation JSONB by a template, never generated.
- **Python:** `/Users/sashad85/miniforge3/bin/python3`. **Live DB:** `postgresql+asyncpg://pharmpilot:pharmpilot@127.0.0.1:5433/pharmpilot`. **Test DB:** same host, `pharmpilot_test`. Postgres runs TRUST auth locally; the password is decorative.
- **Known pre-existing failure, not yours:** `test_integrations_sandbox.py::test_notifications_sandbox_success_shape_no_network_and_masked_logs` fails order-dependently in the full suite and passes in isolation. Do not chase it.

---

## Why this is two phases

Phase 5 was scoped as "zone rules and reconciliation". Research says that is two deliverables with very different evidence, and binding them together risks losing a migration that is free exactly once.

**Phase 5a — the zone becomes a key.** Every deliverable has a verifiable pass/fail against data that exists right now, and it carries a now-or-never migration: `surveillance_observations` has **0 rows today**, so adding the composite foreign key and the non-empty CHECK is free. It is never free again.

**Phase 5b — picks, rules and reconciliation.** Every input is missing, measured live:

| Table | Rows | Note |
|---|---|---|
| `pharmacy_shelves` | 0 | GET-only; the only INSERT in the tree is a test fixture |
| `shelf_placements` | 0 | |
| `shelf_transfer_events` | 0 | and has no event-time column, only `created_at` |
| `surveillance_observations` | 0 | |
| `replenishment_sessions` | 0 | |

And the reconciliation time basis does not exist. `inventory_movements.created_at` is the API **write** instant, not the physical instant. Measured on the 31 movements joined to their fills:

```
movement.created_at MINUS fill.created_at:  min 52.6d   avg 65.4d   max 79.6d
rows matching the design doc's ±5 minute window:  0
```

It cannot be repaired from this side: `trg_inventory_movements_append_only` rejects a backfill UPDATE, and adding an event-time column to the hash payload would invalidate all 31 currently-verifying rows. The design doc's own exit criterion for this work — "WH-02 report precision ≥ 0.60" — is **unmeasurable on this installation**.

That is not a reason to skip 5b. It is a reason to make 5b's first task the data-production step, gate the rest on real rows, and make the report publish its own blind spots so that "0 findings from 0 reconcilable picks" can never be mistaken for "0 findings from 400".

---

## Rule disposition — all 29 codes in design-doc section E

None silently dropped. **Three are buildable in phase 5.**

| Code | Disposition | Why |
|---|---|---|
| **WH-02** | **phase 5b** | The centrepiece. Both ledger legs exist. Ships as a daily pull report. |
| **WH-03** | **phase 5b** | Same reconciler routed by `zone_class='high_risk'`. Phase 5 delivers the **row, not the interrupt** — see below. |
| **SY-04** | **phase 5b** | The anti-fatigue control for WH-02/WH-03; the hard half already exists in `recommendations.scoreboard()`. |
| SY-05 | phase 7 | Retention is greenfield; phase 5 writes the policy, phase 7 enforces it, SY-05 detects enforcement failing. |
| PH-01 | blocked-primitive | Needs staff binding (doc phase 2) + a schedule mask. |
| PH-02 | blocked-primitive | Needs a dwell **interval**; `surveillance_observations` is point-in-time (one `observed_at`, no `exited_at`/`dwell_s`). |
| PH-03 | blocked-primitive | Pose keypoints crossing a calibrated plane; no pose producer, and `vision_camera.homography` is deferred. |
| PH-04 | blocked-primitive | Needs cross-camera visit tracks + transaction links (doc phase 2). |
| PH-05 | blocked-primitive | Needs a queue model and POS idle confirmation; neither exists. |
| PH-06 | blocked-primitive | Needs a wait-time series to take a rolling median of. |
| PH-07 | blocked-primitive | `detector.py:428 _detect_fall` exists and is tested, but consumes a keypoint feed with **no producer**, and "Immediate" has no delivery channel. |
| PH-08 | blocked-primitive | Needs door counting + an arrival-rate baseline. |
| PH-09 | blocked-primitive | The doc says "Existing pathway" and **that is false**: `DuressProtocolEngine` is constructed without a kafka producer at `security_events.py:140`, so `_alert_pharmacist_ui`'s body is skipped by `if self.kafka:` and it records neither success nor failure. |
| PH-10 | blocked-primitive | Needs an access-control event stream; zero access-event references tree-wide. |
| WH-01 | blocked-primitive | **Two** missing primitives: no motion feed, and no schedule substrate at all. |
| WH-04 | blocked-primitive | Needs object/tote detection AND a dispatch record; no dispatch or dock table exists. |
| WH-05 | blocked-primitive | Dwell needs a presence interval. Same gap as PH-02. |
| WH-06 | blocked-primitive | Needs a static-object model and per-camera egress ROIs; no camera registry, no ROI persistence. |
| WH-07 | blocked-primitive | No bay-state source of any kind. |
| WH-08 | blocked-primitive | Same missing classifier; the inverse predicate does not make the input exist. |
| WH-09 | blocked-primitive | Pose-based, no producer; and "aggregate coaching" needs an aggregation surface with no per-person key. |
| WH-10 | blocked-hardware | Cold-room door sensor. Zero `door_open` references anywhere. |
| WH-11 | blocked-hardware | The **only** temperature column in the schema is `shelf_transfer_events.temperature_logged_c`, a single manual spot reading. Zero humidity columns. No per-lot storage temperature range. |
| WH-12 | blocked-hardware | Zero water-sensor references; doc invariant I-10 makes sensors primary. |
| WH-13 | blocked-hardware | Zero smoke references; I-10 forbids video ever being primary. |
| WH-14 | blocked-primitive | Vehicle detection + a delivery-window schedule. Neither exists. |
| SY-01 | blocked-primitive | Needs `vision_camera` + heartbeat. **No phase in the 7-phase plan owns building the camera registry** — this is a gap in the programme, not just this phase. |
| SY-02 | blocked-primitive | Same heartbeat gap. **But a reduced form is free in 5b**: the pick-ingest payload carries an edge `t_start`, so comparing it to server `now()` at ingest measures clock skew at zero cost. Task 5b-2 does this. |
| SY-03 | blocked-primitive | Needs a multi-camera tracker and a door counter. |

**WH-03's "Immediate" is not deliverable.** The only fanout is a module-private process-local dict (`security_events.py:32`); SMS and push short-circuit to a logging sandbox; Celery beat is not running. Phase 5b writes the row at severity 4 and the delivery channel is phase 7. Say so in the ledger rather than letting an operator believe a pager exists.

---

## Decisions this plan makes explicitly

Each was raised by research and would otherwise be decided by accident.

1. **`public` schema, not a dedicated one.** Design doc section C asks for `vision_*` "in their own schema with a dedicated role". Both parity guards hardcode `schema='public'`, so a separate schema silently removes the only column-parity guard the repo has. Every existing table is in `public`. **Consequence recorded honestly:** invariant I-2 ("the vision DB role holds zero write grants on any clinical table") is therefore **unimplemented**, and append-only rests on a row trigger the same role can `DISABLE` — verified: role `pharmpilot` can run `ALTER TABLE inventory_exception_events DISABLE TRIGGER trg_exception_events_append_only`. The docstring at `shared/models/inventory.py:262-263` claiming migration 0030 revoked these is **false** and must not be cited.

2. **Anomalies get their own table; they do not go in `inventory_exceptions`.** Four measured reasons: (a) `uq_exception_open_fingerprint UNIQUE (pharmacy_id, fingerprint) WHERE status <> 'resolved' AND is_deleted = false` permits one open row per fingerprint, so per-occurrence rows do not exist and `debounce_s`/`alert_budget_per_hour`/SY-04 are uncomputable; (b) the recurrence path at `inventory_exceptions.py:123-125` runs `DELETE FROM inventory_exception_rows` then re-stores, destroying the prior firing's evidence — and for a vision anomaly the evidence **is** the alert; (c) `_record_event(..., "opened")` fires only in the `delta.opened` branch and **never on recurrence**, so any budget counted from that log is structurally stuck at 1 (live: `pharmpilot_test` has 381,955 exceptions, 381,955 "opened" events, and 163,684 exceptions with `occurrences >= 2`); (d) `Finding` (`reconciliation.py:31-44`) has no thresholds field, so doc invariant I-4 cannot be satisfied through that pipeline. **Cost accepted:** two operator queues until phase 6 merges the read model. **Also:** this avoids a cross-workstream edit to CL-003-owned files with no recorded handoff.

3. **Zone identity is immutable once observed.** No `ON UPDATE CASCADE` on any zone FK — verified live that it silently rewrites historical observations, and that combined with an append-only child it fails with a misleading error. A rename is a new row plus a `superseded_by` pointer; retirement is `active = false`, never DELETE.

4. **`inventory_exceptions.diff()`'s cross-producer resolution bug is reported, not fixed.** `exceptions.py:313-317` auto-resolves every active fingerprint the current run did not produce, so two producers writing one table would each close the other's findings. Because this plan does not write into that table, the bug is not ours to trip — but it is a real latent defect and Task 5a-6 files it for the inventory owner.

---

# PHASE 5a — The zone becomes a key

Six tasks. Every one is verifiable against data that exists today.

---

### Task 5a-1: The zone registry

**Files:**
- Create: `shared/models/vision.py`
- Create: `data/migrations/versions/0051_vision_zone.py`
- Modify: `shared/models/__init__.py`
- Test: `tests/unit/test_vision_zone.py`

**Interfaces:**
- Produces: `VisionZone` model; table `vision_zone` with business key `(pharmacy_id, site, code)`; `ZONE_CLASSES`, `SITES` tuples importable from `shared.models.vision`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_vision_zone.py`:

```python
"""The zone registry: the first thing in this system that makes a zone a key.

Nine free-text zone vocabularies exist across this codebase and not one of them
is validated. Verified against the live schema: 'Z-CAGE', 'totally made up
zone', and the EMPTY STRING were all accepted into surveillance_observations.
zone_id; only length >= 41 was rejected, and that is varchar truncation, not a
domain check.
"""
from __future__ import annotations

import re
from pathlib import Path

MIGRATION = Path("data/migrations/versions/0051_vision_zone.py")
MODEL = Path("shared/models/vision.py")


def test_migration_chains_from_the_current_head():
    src = MIGRATION.read_text(encoding="utf-8")
    assert re.search(r'^revision\s*=\s*"0051"', src, re.M)
    assert re.search(r'^down_revision\s*=\s*"0050"', src, re.M)


def test_the_zone_carries_a_business_key_not_just_a_uuid():
    """A uuid PK cannot be referenced by surveillance_observations.zone_id,
    which is varchar(40). The composite (pharmacy_id, site, code) can."""
    src = MIGRATION.read_text(encoding="utf-8")
    assert "uq_zone_code_per_site" in src
    for col in ('"pharmacy_id"', '"site"', '"code"'):
        assert col in src


def test_constraint_names_follow_the_house_convention():
    """23 of 28 live CHECK constraints drop the table's namespace word and
    singularise. Guessing ck_<full_table>_<column> is the phase-3 mistake."""
    src = MIGRATION.read_text(encoding="utf-8")
    assert "ck_vision_zone" not in src, "drop the namespace word: ck_zone_*"
    assert "ck_zone_class" in src
    assert "ck_zone_code_shape" in src


def test_a_zone_class_vocabulary_covers_the_design_doc():
    """Including 'credentialed' (Z-STAFFDOOR) and 'prohibited' (toilets,
    changing rooms, prayer room) — both are in the doc's own table and both
    are easy to omit."""
    from shared.models.vision import ZONE_CLASSES
    for c in ("public", "service", "restricted", "high_risk", "safety",
              "credentialed", "prohibited"):
        assert c in ZONE_CLASSES


def test_every_state_column_is_wide_enough_for_its_vocabulary():
    """Migration 0046 widened a CHECK to admit a 17-char value while the column
    stayed VARCHAR(12). House headroom is max_len + 2, rounded up to a multiple
    of four."""
    from shared.models.vision import ZONE_CLASSES
    src = MIGRATION.read_text(encoding="utf-8")
    longest = max(len(c) for c in ZONE_CLASSES)          # 'credentialed' = 12
    assert longest <= 16
    assert "sa.String(16)" in src


def test_retention_days_may_be_null_because_nothing_enforces_it_yet():
    """Provenance rule: a policy number nobody purges against is a claim the
    system cannot honour. NULL says 'not set', a default would say 'decided'."""
    src = MODEL.read_text(encoding="utf-8")
    assert "retention_days" in src
    assert re.search(r"retention_days.*Optional|retention_days.*\|\s*None",
                     src), "retention_days must be nullable"


def test_the_model_is_registered():
    init = Path("shared/models/__init__.py").read_text(encoding="utf-8")
    assert "vision" in init
```

- [ ] **Step 2: Run it to verify it fails**

```bash
/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_vision_zone.py -q -p no:randomly
```

Expected: FAIL — `FileNotFoundError` on the migration path.

- [ ] **Step 3: Write the model**

Create `shared/models/vision.py`:

```python
"""The vision namespace: zones, rules, anomalies and pick events.

**Foreign keys point OUT of this namespace and never into it.** A vision table
may reference pharmacies, staff, pharmacy_shelves or inventory_movements; no
clinical or inventory table may reference a vision table. That one-way rule is
what keeps the namespace droppable and what makes zone-scoped retention a
purge rather than a negotiation.

The single deliberate exception is surveillance_observations, which already has
a zone_id column and predates this registry. It gains a composite foreign key
into vision_zone in migration 0052, because the alternative is a free-text
column that has been proven to accept the empty string.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import (Boolean, CheckConstraint, ForeignKey, Integer, String,
                        UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared.models.base import AuditedBase

# The two sites the rest of the system already enforces, via three live CHECK
# constraints (ck_surv_obs_site, ck_rf_ap_site, ck_rf_fp_site). A zone belongs
# to one of them; inventing a sites table here would break those joins.
SITES = ("pharmacy", "depot")

# From design doc 3.1. 'credentialed' (Z-STAFFDOOR) and 'prohibited' (toilets,
# changing rooms, prayer room, any framing where a screen is legible) are in
# the doc's own table and are the two most commonly dropped.
ZONE_CLASSES = ("public", "service", "restricted", "high_risk", "safety",
                "credentialed", "prohibited")

# Classes whose rules run per-occurrence rather than in a daily batch.
REAL_TIME_CLASSES = ("high_risk",)


class VisionZone(AuditedBase):
    """A zone: the unit of policy. Rules, retention and access attach here.

    AuditedBase rather than TimestampedBase: there are a handful of rows per
    pharmacy, changing retention_days is a compliance-relevant act that must
    name a person, and is_deleted lets a decommissioned zone be retired in
    place so last month's observations still resolve their zone.
    """

    __tablename__ = "vision_zone"

    pharmacy_id: Mapped[UUID] = mapped_column(
        ForeignKey("pharmacies.id"), nullable=False, index=True)
    site: Mapped[str] = mapped_column(String(20), nullable=False)
    # The public identity. surveillance_observations.zone_id references this
    # triple, so it is varchar(40) to match that column exactly.
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name_fa: Mapped[str] = mapped_column(String(200), nullable=False)
    zone_class: Mapped[str] = mapped_column(String(16), nullable=False)

    # Floor-plan polygon as [[x, y], ...] in metres, same frame as the RF
    # access-point layout. NULL until the coverage survey reaches this zone —
    # a fabricated rectangle would be a measurement nobody took.
    polygon: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # NULL, deliberately. Nothing purges yet (verified: no partitioning, no
    # pg_cron, no purge job anywhere in the repo), so a number here would be a
    # compliance claim the system cannot honour. Phase 7 enforces it.
    retention_days: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True)

    # {"tz": "...", "windows": [{"dow": 0-6, "from": "HH:MM", "to": "HH:MM"}]}
    # NULL until measured. No schedule evaluator exists anywhere in this repo
    # (no business-hours, holiday, weekend or shift primitive), so a rule whose
    # firing depends on this must REFUSE TO FIRE while it is NULL rather than
    # fall back to an invented window.
    armed_schedule: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True)

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # A rename is a new row pointing back at the old one. Zone codes are
    # immutable once observed: ON UPDATE CASCADE was measured to silently
    # rewrite historical observations.
    superseded_by: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("vision_zone.id"), nullable=True)

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "site", "code",
                         name="uq_zone_code_per_site"),
        # Spelled out rather than interpolated from SITES: a tuple repr of a
        # one-element tuple is ('pharmacy',) which is not valid SQL, and this
        # constraint must not become fragile if the vocabulary ever shrinks.
        CheckConstraint("site IN ('pharmacy', 'depot')", name="ck_zone_site"),
        CheckConstraint(
            "zone_class IN ('public', 'service', 'restricted', 'high_risk', "
            "'safety', 'credentialed', 'prohibited')",
            name="ck_zone_class"),
        # An empty-string zone is not NULL and would defeat every
        # `zone_id IS NULL` "unzoned" branch downstream.
        CheckConstraint("code <> '' AND code = btrim(code)",
                        name="ck_zone_code_shape"),
        CheckConstraint("retention_days IS NULL OR retention_days > 0",
                        name="ck_zone_retention_positive"),
    )
```

- [ ] **Step 4: Register the model**

In `shared/models/__init__.py`, add `vision` alongside the other imports (match the file's existing style exactly — read it first).

- [ ] **Step 5: Write migration 0051**

Create `data/migrations/versions/0051_vision_zone.py`:

```python
"""the zone registry

Nine zone vocabularies exist across this codebase and not one is validated.
Proven against the live schema in a rolled-back transaction: 'Z-CAGE', 'totally
made up zone' and the EMPTY STRING were all accepted into
surveillance_observations.zone_id; only length >= 41 was rejected, and that is
varchar truncation rather than a domain check.

This creates the registry. Migration 0052 makes surveillance_observations
reference it, which is free exactly once — that table has 0 rows today.

The key is the composite (pharmacy_id, site, code) rather than the uuid PK,
because the column that must reference it is varchar(40) and a uuid cannot.
Retention and schedule are both NULL-able and both default to NULL: nothing in
this repo purges, and no schedule evaluator exists, so a number in either
column would be a policy claim the system cannot honour.

Revision ID: 0051
Revises: 0050
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None

# Longest is 'credentialed' at 12; String(16) gives the house headroom of
# max_len + 2 rounded up to a multiple of four.
_ZONE_CLASSES = ("public", "service", "restricted", "high_risk", "safety",
                 "credentialed", "prohibited")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(chr(39) + v + chr(39) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "vision_zone",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("pharmacy_id", UUID(as_uuid=True),
                  sa.ForeignKey("pharmacies.id"), nullable=False, index=True),
        sa.Column("site", sa.String(20), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name_fa", sa.String(200), nullable=False),
        sa.Column("zone_class", sa.String(16), nullable=False),
        sa.Column("polygon", JSONB, nullable=True),
        sa.Column("retention_days", sa.Integer, nullable=True),
        sa.Column("armed_schedule", JSONB, nullable=True),
        sa.Column("active", sa.Boolean, nullable=False,
                  server_default=sa.true()),
        sa.Column("superseded_by", UUID(as_uuid=True),
                  sa.ForeignKey("vision_zone.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
        # AuditedBase declares is_deleted with a PYTHON default only; without
        # this server_default a raw-SQL insert hits a NOT NULL violation.
        sa.Column("is_deleted", sa.Boolean, nullable=False,
                  server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("pharmacy_id", "site", "code",
                            name="uq_zone_code_per_site"),
        sa.CheckConstraint(_in_list("site", ("pharmacy", "depot")),
                           name="ck_zone_site"),
        sa.CheckConstraint(_in_list("zone_class", _ZONE_CLASSES),
                           name="ck_zone_class"),
        sa.CheckConstraint("code <> '' AND code = btrim(code)",
                           name="ck_zone_code_shape"),
        sa.CheckConstraint("retention_days IS NULL OR retention_days > 0",
                           name="ck_zone_retention_positive"),
    )


def downgrade() -> None:
    op.drop_table("vision_zone")
```

- [ ] **Step 6: Run the test**

```bash
/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_vision_zone.py -q -p no:randomly
```

Expected: PASS.

- [ ] **Step 7: Apply to BOTH databases**

```bash
cd /Users/sashad85/PharmPilot-Claude && for DB in pharmpilot pharmpilot_test; do DATABASE_URL="postgresql+asyncpg://pharmpilot:pharmpilot@127.0.0.1:5433/$DB" /Users/sashad85/miniforge3/bin/python3 -m alembic upgrade head; done
```

Expected: `Running upgrade 0050 -> 0051` twice.

- [ ] **Step 8: Verify the constraints actually reject, against the live schema**

A static grep proves nothing about SQL. Run this and confirm every line reads as annotated:

```bash
cd /Users/sashad85/PharmPilot-Claude && /Users/sashad85/miniforge3/bin/python3 - <<'PY'
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
async def m():
    e = create_async_engine("postgresql+asyncpg://pharmpilot:pharmpilot@127.0.0.1:5433/pharmpilot")
    async with e.connect() as c:
        t = await c.begin()
        pid = (await c.execute(text("SELECT id FROM pharmacies LIMIT 1"))).scalar()
        base = ("INSERT INTO vision_zone (pharmacy_id, site, code, name_fa, zone_class) "
                "VALUES (:p, :s, :c, 'x', :k)")
        cases = [("valid", "depot", "Z-CAGE", "high_risk", True),
                 ("empty code", "depot", "", "high_risk", False),
                 ("padded code", "depot", " Z-A ", "high_risk", False),
                 ("bad site", "warehouse", "Z-A", "high_risk", False),
                 ("bad class", "depot", "Z-B", "super_secret", False),
                 ("credentialed", "pharmacy", "Z-STAFFDOOR", "credentialed", True),
                 ("prohibited", "pharmacy", "Z-WC", "prohibited", True)]
        for label, site, code, klass, want in cases:
            sp = await c.begin_nested()
            try:
                await c.execute(text(base), {"p": pid, "s": site, "c": code, "k": klass})
                got = True
            except Exception:
                got = False
            await sp.rollback()
            print(f"  {label:14} {'accepted' if got else 'rejected':8} "
                  f"{'OK' if got == want else '*** WRONG ***'}")
        await t.rollback()
    await e.dispose()
asyncio.run(m())
PY
```

Expected: seven lines, every one `OK`.

- [ ] **Step 9: Commit**

```bash
cd /Users/sashad85/PharmPilot-Claude && git add shared/models/vision.py shared/models/__init__.py data/migrations/versions/0051_vision_zone.py tests/unit/test_vision_zone.py && git commit -m "feat(vision): the zone registry"
```

---

### Task 5a-2: Make the zone a key Postgres enforces

**Files:**
- Create: `data/migrations/versions/0052_zone_fk.py`
- Modify: `services/core/surveillance/recorder.py`
- Test: `tests/unit/test_zone_enforcement.py`

**Interfaces:**
- Consumes: `vision_zone` from Task 5a-1.
- Produces: composite FK on `surveillance_observations`; `recorder.build_observation` raising `ValueError` on an unregistered zone.

**This is the now-or-never task.** `surveillance_observations` has 0 rows today. The FK is free now and never again.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_zone_enforcement.py`:

```python
"""A zone id that Postgres refuses to accept when it names nothing.

Measured before this task: surveillance_observations.zone_id accepted 'Z-CAGE',
'totally made up zone' and the empty string. The recorder validated `site` and
`fusion_decision` and passed zone_id straight through.
"""
from __future__ import annotations

import re
from pathlib import Path

MIGRATION = Path("data/migrations/versions/0052_zone_fk.py")
RECORDER = Path("services/core/surveillance/recorder.py")


def test_migration_chains_from_0051():
    src = MIGRATION.read_text(encoding="utf-8")
    assert re.search(r'^revision\s*=\s*"0052"', src, re.M)
    assert re.search(r'^down_revision\s*=\s*"0051"', src, re.M)


def test_the_fk_is_composite_and_matches_the_business_key():
    src = MIGRATION.read_text(encoding="utf-8")
    assert "fk_surv_obs_zone" in src
    assert "pharmacy_id" in src and "site" in src and "zone_id" in src
    assert "vision_zone" in src


def test_the_fk_does_not_cascade_on_update():
    """Measured live: ON UPDATE CASCADE silently rewrites the zone of a
    historical observation when a zone is renamed. In a system that watches
    people, a rename must never retroactively relocate an observation."""
    src = MIGRATION.read_text(encoding="utf-8")
    assert "CASCADE" not in src.upper()


def test_the_empty_string_is_refused_separately_from_the_fk():
    """'' would satisfy no FK either, but the CHECK gives the honest error and
    keeps `zone_id IS NULL` meaning exactly 'unzoned'."""
    src = MIGRATION.read_text(encoding="utf-8")
    assert "ck_surv_obs_zone_shape" in src


def test_the_recorder_refuses_an_unregistered_zone_before_the_driver_does():
    """A ValueError with the zone code in it is actionable; an asyncpg
    ForeignKeyViolation three frames down is not."""
    src = RECORDER.read_text(encoding="utf-8")
    assert "zone_id" in src
    assert "known_zones" in src or "valid_zones" in src


def test_the_recorder_still_allows_an_unzoned_observation():
    """Not every observation has a zone. NULL must stay legal."""
    from services.core.surveillance.recorder import build_observation
    from datetime import datetime, timezone
    row = build_observation(
        pharmacy_id="00000000-0000-0000-0000-000000000000", site="depot",
        action_type="movement", observed_at=datetime.now(timezone.utc),
        zone_id=None, known_zones=frozenset({"Z-CAGE"}))
    assert row["zone_id"] is None


def test_the_recorder_rejects_a_zone_that_is_not_registered():
    import pytest
    from services.core.surveillance.recorder import build_observation
    from datetime import datetime, timezone
    with pytest.raises(ValueError, match="Z-TYPO"):
        build_observation(
            pharmacy_id="00000000-0000-0000-0000-000000000000", site="depot",
            action_type="movement", observed_at=datetime.now(timezone.utc),
            zone_id="Z-TYPO", known_zones=frozenset({"Z-CAGE"}))


def test_an_unchecked_recorder_still_works_when_zones_are_unknown():
    """known_zones=None means 'the caller could not look them up'. Refusing
    every observation in that case would make a registry outage a capture
    outage, which is a worse failure than an unvalidated string."""
    from services.core.surveillance.recorder import build_observation
    from datetime import datetime, timezone
    row = build_observation(
        pharmacy_id="00000000-0000-0000-0000-000000000000", site="depot",
        action_type="movement", observed_at=datetime.now(timezone.utc),
        zone_id="Z-ANYTHING", known_zones=None)
    assert row["zone_id"] == "Z-ANYTHING"
```

- [ ] **Step 2: Run it to verify it fails**

```bash
/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_zone_enforcement.py -q -p no:randomly
```

Expected: FAIL — migration file missing, and `build_observation` has no `known_zones` parameter.

- [ ] **Step 3: Write migration 0052**

Create `data/migrations/versions/0052_zone_fk.py`:

```python
"""a zone id that names something

surveillance_observations.zone_id has been varchar(40) with no CHECK and no FK
since migration 0032. Proven live: it accepted 'Z-CAGE', 'totally made up zone'
and the EMPTY STRING; only length >= 41 was refused.

The foreign key is composite — (pharmacy_id, site, zone_id) referencing
vision_zone (pharmacy_id, site, code) — because zone_id is varchar(40) and
cannot reference a uuid primary key. MATCH SIMPLE (the default) keeps a NULL
zone_id legal, which is what 'unzoned' must stay.

Deliberately NO ON UPDATE CASCADE. Measured: with CASCADE, renaming a zone
silently rewrites the zone of every historical observation, and where the child
is append-only the UPDATE fails naming the wrong table. Zone codes are
immutable once observed; a rename is a new row with superseded_by.

This is free exactly once. surveillance_observations has 0 rows today.

Revision ID: 0052
Revises: 0051
"""
import sqlalchemy as sa
from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Order matters: the CHECK first, so any '' already present surfaces as a
    # CHECK violation naming the shape rather than as an FK violation.
    op.create_check_constraint(
        "ck_surv_obs_zone_shape", "surveillance_observations",
        "zone_id IS NULL OR (zone_id <> '' AND zone_id = btrim(zone_id))")
    op.create_foreign_key(
        "fk_surv_obs_zone", "surveillance_observations", "vision_zone",
        ["pharmacy_id", "site", "zone_id"], ["pharmacy_id", "site", "code"])


def downgrade() -> None:
    op.drop_constraint("fk_surv_obs_zone", "surveillance_observations",
                       type_="foreignkey")
    op.drop_constraint("ck_surv_obs_zone_shape", "surveillance_observations",
                       type_="check")
```

- [ ] **Step 4: Teach the recorder to refuse**

In `services/core/surveillance/recorder.py`, add the `known_zones` parameter to `build_observation` and validate. Read the file first and match its existing style; the validation block is:

```python
    # A ValueError naming the code is actionable at the edge; an asyncpg
    # ForeignKeyViolation three frames down is not. known_zones=None means the
    # caller could not look the registry up — refusing every observation in
    # that case would turn a registry outage into a capture outage.
    if zone_id is not None and known_zones is not None:
        if zone_id not in known_zones:
            raise ValueError(
                f"zone {zone_id!r} is not registered for this pharmacy; "
                f"register it in vision_zone before sending observations")
```

- [ ] **Step 5: Run the tests**

```bash
/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_zone_enforcement.py tests/unit/test_surveillance_recorder.py -q -p no:randomly
```

Expected: PASS, including the pre-existing recorder suite — `known_zones` must default to `None` so every existing caller is unchanged.

- [ ] **Step 6: Apply to both databases and verify live**

```bash
cd /Users/sashad85/PharmPilot-Claude && for DB in pharmpilot pharmpilot_test; do DATABASE_URL="postgresql+asyncpg://pharmpilot:pharmpilot@127.0.0.1:5433/$DB" /Users/sashad85/miniforge3/bin/python3 -m alembic upgrade head; done
```

Then verify all four outcomes against the real table:

```bash
cd /Users/sashad85/PharmPilot-Claude && /Users/sashad85/miniforge3/bin/python3 - <<'PY'
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
async def m():
    e = create_async_engine("postgresql+asyncpg://pharmpilot:pharmpilot@127.0.0.1:5433/pharmpilot")
    async with e.connect() as c:
        t = await c.begin()
        pid = (await c.execute(text("SELECT id FROM pharmacies LIMIT 1"))).scalar()
        await c.execute(text(
            "INSERT INTO vision_zone (pharmacy_id, site, code, name_fa, zone_class) "
            "VALUES (:p, 'depot', 'Z-CAGE', 'قفس', 'high_risk')"), {"p": pid})
        base = ("INSERT INTO surveillance_observations "
                "(pharmacy_id, observed_at, site, action_type, zone_id) "
                "VALUES (:p, now(), 'depot', 'movement', {})")
        for label, val, want in [("NULL (unzoned)", "NULL", True),
                                 ("'Z-CAGE' registered", "'Z-CAGE'", True),
                                 ("'MADE-UP' unregistered", "'MADE-UP'", False),
                                 ("'' empty string", "''", False)]:
            sp = await c.begin_nested()
            try:
                await c.execute(text(base.format(val)), {"p": pid}); got = True
            except Exception:
                got = False
            await sp.rollback()
            print(f"  {label:24} {'accepted' if got else 'rejected':8} "
                  f"{'OK' if got == want else '*** WRONG ***'}")
        await t.rollback()
    await e.dispose()
asyncio.run(m())
PY
```

Expected: four lines, every one `OK`.

- [ ] **Step 7: Commit**

```bash
cd /Users/sashad85/PharmPilot-Claude && git add data/migrations/versions/0052_zone_fk.py services/core/surveillance/recorder.py tests/unit/test_zone_enforcement.py && git commit -m "feat(vision): a zone id that names something"
```

---

### Task 5a-3: Dispose of all nine zone vocabularies

**Files:**
- Create: `services/core/vision/zones.py`
- Modify: `services/audio/transcription/pipeline.py`
- Test: `tests/unit/test_zone_vocabularies.py`

**Interfaces:**
- Consumes: `vision_zone`.
- Produces: `LEGACY_ZONE_ALIAS: dict[str, str]`, `load_zone_codes(db, pharmacy_id, site) -> frozenset[str]`, `invalidate(pharmacy_id, site=None) -> int`.

**Why this task exists.** A census found **nine** zone vocabularies in code plus a tenth in the design doc. A plan that says "reconcile the three zone vocabularies" leaves silent spelling forks. Each must be dispositioned: subsume, alias, delete, or leave alone — and the disposition must be pinned by a test, or the next vocabulary arrives unnoticed.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_zone_vocabularies.py`:

```python
"""Every zone vocabulary in the tree, dispositioned.

A census found nine in code plus one in the design doc. This test is the pin:
if a tenth appears, or a dispositioned one changes, it fails here rather than
producing a silent spelling fork three phases later.
"""
from __future__ import annotations

from pathlib import Path

# The disposition. Each entry is (where it lives, what we do about it).
#   subsume  — its values become vision_zone rows
#   alias    — kept, mapped one-way into vision_zone codes
#   delete   — dead code, removed by this task
#   leave    — out of scope, deliberately untouched, with a reason
DISPOSITION = {
    "surveillance_observations.zone_id": "subsume",
    "pharmacy_shelves.zone": "alias",
    "services/audio/transcription/pipeline.py:ZONE_CONFIGS": "delete",
    "services/biometric/behavioral_analysis/detector.py:DEFAULT_ZONE_CONFIG": "alias",
    "security_events.event_metadata->>'camera_zone'": "alias",
    "services/biometric/evidence_vault/vault.py:camera_zone": "leave",
    "services/platform/config.py:zone settings": "leave",
    "frontend SecuritySurveillance.tsx SVG rects": "leave",
    "docs/design/SURVEILLANCE_PLATFORM.md Z-* table": "subsume",
}


def test_every_vocabulary_has_a_disposition():
    assert len(DISPOSITION) == 9
    for where, what in DISPOSITION.items():
        assert what in ("subsume", "alias", "delete", "leave"), where


def test_the_dead_vocabulary_is_actually_deleted():
    """ZONE_CONFIGS in the audio pipeline has exactly one reference in the
    whole tree: its own definition. Zero blast radius."""
    src = Path("services/audio/transcription/pipeline.py").read_text(encoding="utf-8")
    assert "ZONE_CONFIGS" not in src


def test_the_alias_map_is_one_way_and_total():
    """One-way: legacy spelling -> Z-code. Never the reverse, or a rename in
    the registry would silently reinterpret historical rows."""
    from services.core.vision.zones import LEGACY_ZONE_ALIAS
    for legacy, canonical in LEGACY_ZONE_ALIAS.items():
        assert canonical.startswith("Z-"), f"{legacy} -> {canonical}"
        assert canonical not in LEGACY_ZONE_ALIAS, "the map must not round-trip"


def test_the_detector_vocabulary_is_covered_by_the_alias_map():
    """detector.py has six named zones plus two literals injected at runtime
    ('general_floor' at :131 and 'entry' at :469) that are in no dict. Both
    must resolve or the detector emits codes the registry never heard of."""
    from services.biometric.behavioral_analysis.detector import DEFAULT_ZONE_CONFIG
    from services.core.vision.zones import LEGACY_ZONE_ALIAS
    for name in list(DEFAULT_ZONE_CONFIG) + ["general_floor", "entry"]:
        assert name in LEGACY_ZONE_ALIAS, f"{name} has no canonical zone"


def test_the_cache_is_keyed_by_pharmacy_and_site():
    """The pharmacy and the depot have different floor plans. Sharing a cache
    slot would hand one site's zone set to the other — the same defect
    rf_mapping.store was built to avoid."""
    from services.core.vision import zones as Z
    Z._CACHE.clear()
    Z._CACHE[("p1", "depot")] = frozenset({"Z-CAGE"})
    Z._CACHE[("p1", "pharmacy")] = frozenset({"Z-ENT"})
    assert Z.invalidate("p1", "depot") == 1
    assert ("p1", "pharmacy") in Z._CACHE
```

- [ ] **Step 2: Run it to verify it fails**

```bash
/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_zone_vocabularies.py -q -p no:randomly
```

Expected: FAIL — `ModuleNotFoundError: services.core.vision`.

- [ ] **Step 3: Write the module**

Create `services/core/vision/__init__.py` (empty) and `services/core/vision/zones.py`:

```python
"""Zone codes: the registry lookup, and what to do with the nine that predate it.

A census of this tree found NINE zone vocabularies plus a tenth in the design
doc. None validated anything. This module is where they converge.

The alias map is deliberately ONE-WAY — legacy spelling to canonical Z-code,
never the reverse. A reverse map would let a registry rename silently
reinterpret rows written years earlier.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Legacy spelling -> canonical code from design doc 3.1.
#
# 'general_floor' (detector.py:131, the get_zone fallback) and 'entry'
# (detector.py:469, hard-coded and in no dict) are included because the
# detector emits them at runtime and they appear in no zone config at all.
LEGACY_ZONE_ALIAS: dict[str, str] = {
    # services/biometric/behavioral_analysis/detector.py:137-144
    "waiting_area": "Z-WAIT",
    "dispensing_counter_public": "Z-COUNTER-1",
    "dispensing_counter_interior": "Z-COUNTER-1",
    "vault_room": "Z-CDS",
    "pharmacist_only": "Z-BACKOFFICE",
    "otc_shelves": "Z-OTC",
    "general_floor": "Z-WAIT",
    "entry": "Z-ENT",
    # security_events.event_metadata->>'camera_zone' — the only vocabulary
    # with live rows, seeded by scripts/seed_security_events.py:28-38.
    "parking_lot": "Z-DOCK",
    "staff_door": "Z-STAFFDOOR",
    "front_entrance": "Z-ENT",
    "stockroom": "Z-AISLE-A",
    "dispensing_1": "Z-COUNTER-1",
    "rear_door": "Z-STAFFDOOR",
    "back_entrance": "Z-STAFFDOOR",
    # pharmacy_shelves.zone — one writer, a test fixture writing 'main'.
    "main": "Z-AISLE-A",
}

_CACHE: dict[tuple[str, str], frozenset[str]] = {}


def canonical(legacy: str | None) -> str | None:
    """Map a legacy zone spelling to its canonical code, or pass it through."""
    if legacy is None:
        return None
    return LEGACY_ZONE_ALIAS.get(legacy, legacy)


def invalidate(pharmacy_id, site: str | None = None) -> int:
    """Drop cached zone sets after a registry write. Returns how many."""
    keys = [k for k in _CACHE
            if k[0] == str(pharmacy_id) and (site is None or k[1] == site)]
    for k in keys:
        _CACHE.pop(k, None)
    return len(keys)


async def load_zone_codes(db: AsyncSession, pharmacy_id, site: str, *,
                          force: bool = False) -> frozenset[str]:
    """Active zone codes for a site. Cached with explicit invalidation."""
    key = (str(pharmacy_id), site)
    if not force and key in _CACHE:
        return _CACHE[key]
    rows = (await db.execute(text("""
        SELECT code FROM vision_zone
        WHERE pharmacy_id = :pid AND site = :s
          AND active = true AND is_deleted = false"""),
        {"pid": pharmacy_id, "s": site})).scalars().all()
    codes = frozenset(rows)
    _CACHE[key] = codes
    return codes
```

- [ ] **Step 4: Delete the dead vocabulary**

Remove the `ZONE_CONFIGS` dict from `services/audio/transcription/pipeline.py` (lines ~32-65) and the `ZoneConfig` dataclass if it has no other user. Confirm zero blast radius first:

```bash
cd /Users/sashad85/PharmPilot-Claude && grep -rn "ZONE_CONFIGS\|ZoneConfig" --include="*.py" . | grep -v ".claude/worktrees" | grep -v "test_zone_vocabularies"
```

Expected before deleting: only the definition itself.

- [ ] **Step 5: Run the tests**

```bash
/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_zone_vocabularies.py tests/unit/test_behavioral_analysis.py -q -p no:randomly
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/sashad85/PharmPilot-Claude && git add services/core/vision/ services/audio/transcription/pipeline.py tests/unit/test_zone_vocabularies.py && git commit -m "feat(vision): dispose of all nine zone vocabularies"
```

---

### Task 5a-4: The vision router, its permissions, and its tenancy proof

**Files:**
- Create: `services/platform/routers/vision.py`
- Modify: `services/platform/main.py`, `shared/models/auth.py`
- Test: `tests/unit/test_vision_routes.py`

**Interfaces:**
- Consumes: `load_zone_codes`, `VisionZone`.
- Produces: `GET /api/v1/vision/zones`, `POST /api/v1/vision/zones`; permissions `vision:read`, `vision:write`.

**Ownership note.** `ROLE_PERMISSIONS` in `shared/models/auth.py` is listed in CL-003's owned scope (`AI_COLLABORATION.md:75`) and CL-003 is `READY_FOR_REVIEW` on this branch with uncommitted changes present. Adding two permission strings is additive and low-risk, but **record it in the ledger as a cross-workstream touch** rather than letting it pass silently.

Declare **only** `vision:read` and `vision:write`. Do **not** declare `vision:clip`, `vision:clip:elevated`, `vision:export`, `vision:audit` or `vision:ingest` — `audio:read` is already a dead permission granted to two roles and used by zero routes, and a declared-but-unused permission is the same rot.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_vision_routes.py`:

```python
"""The vision router: authentication, tenancy, and no dead permissions.

No test in this repo enforces tenancy scoping on a NEW table or route —
test_router_http_tenant_isolation.py imports only label_engine and
prescriptions. A tenancy-blind vision endpoint would ship green. This is that
missing test, landing with the first route rather than after four more.
"""
from __future__ import annotations

import re
from pathlib import Path

ROUTER = Path("services/platform/routers/vision.py")
AUTH = ("require_permission", "require_pharmacist", "get_current_staff",
        "get_current_user")


def _handlers():
    src = ROUTER.read_text(encoding="utf-8")
    out = []
    for m in re.finditer(r'@router\.(get|post|patch)\(\s*"([^"]*)"', src):
        seg = src[m.start(): m.start() + 2000]
        sig = seg.split("\n)")[0] if "\n)" in seg[:2000] else seg[:900]
        out.append((m.group(1).upper(), m.group(2), sig))
    return out


def test_every_route_authenticates():
    for verb, path, sig in _handlers():
        assert any(a in sig for a in AUTH), f"{verb} {path} has no auth"


def test_reading_and_writing_zones_are_different_permissions():
    """A zone defines where policy applies. Reading the map is one thing;
    moving the boundary is another."""
    for verb, path, sig in _handlers():
        if path == "/zones" and verb == "POST":
            assert "vision:write" in sig
        if path == "/zones" and verb == "GET":
            assert "vision:read" in sig


def test_no_handler_takes_pharmacy_id_from_the_request():
    """Tenancy comes from the authenticated staff row, never from the caller.
    There is a still-open defect in this repo where an endpoint is
    permission-gated but not tenancy-gated; this is what makes it
    unrepeatable rather than merely un-repeated."""
    src = ROUTER.read_text(encoding="utf-8")
    for bad in ("pharmacy_id: UUID = Query", "pharmacy_id: UUID = Body",
                "body.pharmacy_id"):
        assert bad not in src, f"tenancy must not come from the request: {bad}"
    assert "staff.pharmacy_id" in src


def test_only_two_vision_permissions_are_declared():
    """audio:read is already a dead permission granted to two roles and used by
    zero routes. A declared-but-unused permission is the same rot."""
    src = Path("shared/models/auth.py").read_text(encoding="utf-8")
    declared = set(re.findall(r'"(vision:[a-z:]+)"', src))
    assert declared == {"vision:read", "vision:write"}, declared


def test_the_router_is_mounted():
    src = Path("services/platform/main.py").read_text(encoding="utf-8")
    assert "vision" in src
```

- [ ] **Step 2: Run it to verify it fails**

```bash
/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_vision_routes.py -q -p no:randomly
```

Expected: FAIL — router file missing.

- [ ] **Step 3: Write the router**

Create `services/platform/routers/vision.py`:

```python
"""Zones, and the policy that attaches to them.

Tenancy comes from the authenticated staff row and never from the request. A
cross-tenant miss returns 404, not 403: 403 confirms the row exists.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.core.vision import zones as Z
from services.platform.auth import require_permission
from services.platform.database import get_db
from shared.models.auth import Staff
from shared.models.vision import SITES, ZONE_CLASSES, VisionZone

router = APIRouter(tags=["vision"])


class ZoneIn(BaseModel):
    site: str
    code: str = Field(min_length=1, max_length=40)
    name_fa: str = Field(min_length=1, max_length=200)
    zone_class: str
    # Both NULL-able on purpose. Nothing purges yet and no schedule evaluator
    # exists, so a value here would be a policy claim we cannot honour.
    polygon: Optional[list[list[float]]] = None
    retention_days: Optional[int] = None
    armed_schedule: Optional[dict] = None


@router.get("/zones")
async def list_zones(
    site: Optional[str] = Query(None),
    staff: Staff = Depends(require_permission("vision:read")),
    db: AsyncSession = Depends(get_db),
):
    """Registered zones for this pharmacy, with their policy."""
    q = select(VisionZone).where(VisionZone.pharmacy_id == staff.pharmacy_id,
                                 VisionZone.is_deleted.is_(False))
    if site is not None:
        q = q.where(VisionZone.site == site)
    rows = (await db.execute(q.order_by(VisionZone.site, VisionZone.code))).scalars().all()
    return {"zones": [
        {"id": str(z.id), "site": z.site, "code": z.code,
         "name_fa": z.name_fa, "zone_class": z.zone_class,
         "polygon": z.polygon, "active": z.active,
         "retention_days": z.retention_days,
         # Say which of these were measured and which were never set. A NULL
         # that reads as 0 is how an unenforced policy looks enforced.
         "retention_basis": "declared" if z.retention_days is not None
                            else "not_set",
         "armed_schedule": z.armed_schedule,
         "schedule_basis": "declared" if z.armed_schedule is not None
                           else "not_set"}
        for z in rows]}


@router.post("/zones", status_code=201)
async def create_zone(
    body: ZoneIn,
    staff: Staff = Depends(require_permission("vision:write")),
    db: AsyncSession = Depends(get_db),
):
    """Register a zone. The code is immutable once observations reference it."""
    if body.site not in SITES:
        raise HTTPException(400, f"site must be one of {SITES}")
    if body.zone_class not in ZONE_CLASSES:
        raise HTTPException(400, f"zone_class must be one of {ZONE_CLASSES}")

    zone = VisionZone(
        pharmacy_id=staff.pharmacy_id, site=body.site, code=body.code.strip(),
        name_fa=body.name_fa, zone_class=body.zone_class,
        polygon={"points": body.polygon} if body.polygon else None,
        retention_days=body.retention_days,
        armed_schedule=body.armed_schedule, created_by=staff.id)
    db.add(zone)
    await db.commit()
    # The recorder reads this set on every observation.
    Z.invalidate(staff.pharmacy_id, body.site)
    return {"id": str(zone.id), "code": zone.code, "site": zone.site}
```

- [ ] **Step 4: Mount it and declare the permissions**

In `services/platform/main.py`, mount the router at `/api/v1/vision` following the pattern of the surrounding routers (read the file and match it exactly).

In `shared/models/auth.py`, add `"vision:read"` and `"vision:write"` to the roles that should hold them — read `ROLE_PERMISSIONS` first and follow its existing shape. `vision:read` belongs with the roles that already hold `inventory:read`; `vision:write` with those holding `inventory:approve`.

- [ ] **Step 5: Run the tests**

```bash
/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_vision_routes.py tests/unit/test_route_authentication.py -q -p no:randomly
```

Expected: PASS.

- [ ] **Step 6: Verify the app builds and the routes appear**

```bash
cd /Users/sashad85/PharmPilot-Claude && /Users/sashad85/miniforge3/bin/python3 -c "
from services.platform.main import create_app
app = create_app()
print('routes:', len(app.routes))
for r in sorted(p for p in (getattr(x, 'path', '') for x in app.routes) if '/vision/' in p): print('  ', r)"
```

Expected: route count 2 higher than before (369 → 371), and both `/api/v1/vision/zones` entries listed.

- [ ] **Step 7: Commit**

```bash
cd /Users/sashad85/PharmPilot-Claude && git add services/platform/routers/vision.py services/platform/main.py shared/models/auth.py tests/unit/test_vision_routes.py && git commit -m "feat(vision): the zone router, two permissions, and the tenancy test that did not exist"
```

---

### Task 5a-5: Seed the design doc's zones, and prove a new tenant is not silently blind

**Files:**
- Create: `scripts/seed_vision_zones.py`
- Test: `tests/unit/test_vision_zone_seed_e2e.py`

**Interfaces:**
- Consumes: `VisionZone`, `POST /vision/zones`.
- Produces: `seed_zones(db, pharmacy_id) -> int`.

**Why this task exists.** A pharmacy created after phase 5 silently gets zero zones, so every observation it sends is refused or unzoned. The failure mode is **silence**, which is indistinguishable from a clean shop. The seed must be callable per-pharmacy and must be tested against a second pharmacy, not just the one that exists.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_vision_zone_seed_e2e.py`:

```python
"""The seed, exercised against real SQL.

A static grep proves nothing about SQL. This runs against pharmpilot_test.
"""
from __future__ import annotations

import os
import re
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.asyncio


def _test_url() -> str | None:
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        return url
    env = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    try:
        with open(env, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("DATABASE_URL="):
                    raw = line.split("=", 1)[1].strip()
                    return re.sub(r"/pharmpilot$", "/pharmpilot_test", raw)
    except OSError:
        return None
    return None


@pytest.fixture
async def db():
    url = _test_url()
    if not url:
        pytest.skip("no test database URL")
    engine = create_async_engine(url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def test_seeding_a_fresh_pharmacy_gives_it_every_doc_zone(db):
    """The failure mode this prevents is silence: a pharmacy with no zones
    accepts no zoned observation and raises no error anywhere."""
    from scripts.seed_vision_zones import seed_zones

    pid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO pharmacies (id, name, timezone) "
        "VALUES (:i, 'seed test', 'Asia/Tehran')"), {"i": pid})
    try:
        n = await seed_zones(db, pid)
        await db.commit()
        assert n >= 12, f"only {n} zones seeded"

        codes = (await db.execute(text(
            "SELECT code FROM vision_zone WHERE pharmacy_id = :p"),
            {"p": pid})).scalars().all()
        for expected in ("Z-ENT", "Z-WAIT", "Z-CDS", "Z-CAGE", "Z-COLD",
                         "Z-DOCK", "Z-STAFFDOOR", "Z-CONSULT"):
            assert expected in codes, expected
    finally:
        await db.execute(text("DELETE FROM vision_zone WHERE pharmacy_id = :p"), {"p": pid})
        await db.execute(text("DELETE FROM pharmacies WHERE id = :p"), {"p": pid})
        await db.commit()


async def test_seeding_is_idempotent(db):
    """Run twice, get one set. A seed that duplicates on re-run cannot be put
    in a startup path or a provisioning script."""
    from scripts.seed_vision_zones import seed_zones

    pid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO pharmacies (id, name, timezone) "
        "VALUES (:i, 'seed idem', 'Asia/Tehran')"), {"i": pid})
    try:
        await seed_zones(db, pid)
        await db.commit()
        first = (await db.execute(text(
            "SELECT count(*) FROM vision_zone WHERE pharmacy_id = :p"),
            {"p": pid})).scalar()
        await seed_zones(db, pid)
        await db.commit()
        second = (await db.execute(text(
            "SELECT count(*) FROM vision_zone WHERE pharmacy_id = :p"),
            {"p": pid})).scalar()
        assert first == second
    finally:
        await db.execute(text("DELETE FROM vision_zone WHERE pharmacy_id = :p"), {"p": pid})
        await db.execute(text("DELETE FROM pharmacies WHERE id = :p"), {"p": pid})
        await db.commit()


async def test_no_seeded_zone_carries_an_unmeasured_policy(db):
    """Provenance: retention_days and armed_schedule must be NULL, because
    nothing purges and no schedule evaluator exists. A seeded 30 would be a
    fabricated constant on a compliance-facing column."""
    from scripts.seed_vision_zones import DOC_ZONES
    for z in DOC_ZONES:
        assert z.get("retention_days") is None, z["code"]
        assert z.get("armed_schedule") is None, z["code"]
```

- [ ] **Step 2: Run it to verify it fails**

```bash
/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_vision_zone_seed_e2e.py -q -p no:randomly
```

Expected: FAIL — `ModuleNotFoundError: scripts.seed_vision_zones`.

- [ ] **Step 3: Write the seed**

Create `scripts/seed_vision_zones.py`:

```python
"""Seed one pharmacy's zones from design doc 3.1.

Idempotent: ON CONFLICT on the business key does nothing. Safe to call from a
provisioning path, because the failure mode it prevents is silence — a pharmacy
with no zones refuses every zoned observation and reports nothing anywhere.

Every policy column is seeded NULL. Nothing in this repo purges and no schedule
evaluator exists, so a retention_days or armed_schedule value here would be a
number nobody measured on a compliance-facing column.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Design doc 3.1. Codes with an n-suffix in the doc (Z-COUNTER-1..n,
# Z-AISLE-A..n, Z-EGRESS-1..n) are seeded with their first instance only; the
# rest are registered per site when the coverage survey names them.
DOC_ZONES: list[dict] = [
    {"site": "pharmacy", "code": "Z-ENT",        "name_fa": "ورودی",              "zone_class": "public"},
    {"site": "pharmacy", "code": "Z-WAIT",       "name_fa": "سالن انتظار",         "zone_class": "public"},
    {"site": "pharmacy", "code": "Z-COUNTER-1",  "name_fa": "پیشخوان ۱",          "zone_class": "service"},
    {"site": "pharmacy", "code": "Z-OTC",        "name_fa": "قفسه‌های OTC",        "zone_class": "public"},
    {"site": "pharmacy", "code": "Z-CDS",        "name_fa": "کابینت داروهای کنترل‌شده", "zone_class": "restricted"},
    {"site": "pharmacy", "code": "Z-COMPOUND",   "name_fa": "اتاق ترکیب",          "zone_class": "restricted"},
    {"site": "pharmacy", "code": "Z-BACKOFFICE", "name_fa": "دفتر پشتی",           "zone_class": "restricted"},
    # No camera, by design. Registered so that an observation naming it is a
    # loud constraint violation rather than a silent unzoned row.
    {"site": "pharmacy", "code": "Z-CONSULT",    "name_fa": "اتاق مشاوره",         "zone_class": "prohibited"},
    {"site": "pharmacy", "code": "Z-STAFFDOOR",  "name_fa": "درب کارکنان",         "zone_class": "credentialed"},
    {"site": "pharmacy", "code": "Z-EGRESS-1",   "name_fa": "مسیر خروج ۱",         "zone_class": "safety"},
    {"site": "depot",    "code": "Z-DOCK",       "name_fa": "بارانداز",            "zone_class": "restricted"},
    {"site": "depot",    "code": "Z-AISLE-A",    "name_fa": "راهرو A",             "zone_class": "restricted"},
    {"site": "depot",    "code": "Z-CAGE",       "name_fa": "قفس مخدر",            "zone_class": "high_risk"},
    {"site": "depot",    "code": "Z-COLD",       "name_fa": "سردخانه",             "zone_class": "high_risk"},
    {"site": "depot",    "code": "Z-QUARANTINE", "name_fa": "قرنطینه",             "zone_class": "restricted"},
    {"site": "depot",    "code": "Z-STAFFDOOR",  "name_fa": "درب کارکنان انبار",   "zone_class": "credentialed"},
    {"site": "depot",    "code": "Z-EGRESS-1",   "name_fa": "مسیر خروج انبار ۱",   "zone_class": "safety"},
]


async def seed_zones(db: AsyncSession, pharmacy_id) -> int:
    """Register every design-doc zone for one pharmacy. Returns how many."""
    n = 0
    for z in DOC_ZONES:
        await db.execute(text("""
            INSERT INTO vision_zone
                (pharmacy_id, site, code, name_fa, zone_class,
                 retention_days, armed_schedule)
            VALUES (:pid, :site, :code, :fa, :klass, NULL, NULL)
            ON CONFLICT (pharmacy_id, site, code) DO NOTHING"""),
            {"pid": pharmacy_id, "site": z["site"], "code": z["code"],
             "fa": z["name_fa"], "klass": z["zone_class"]})
        n += 1
    return n
```

- [ ] **Step 4: Run the tests**

```bash
/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_vision_zone_seed_e2e.py -q -p no:randomly
```

Expected: PASS.

- [ ] **Step 5: Seed the live pharmacy**

```bash
cd /Users/sashad85/PharmPilot-Claude && /Users/sashad85/miniforge3/bin/python3 - <<'PY'
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from scripts.seed_vision_zones import seed_zones
async def m():
    e = create_async_engine("postgresql+asyncpg://pharmpilot:pharmpilot@127.0.0.1:5433/pharmpilot")
    async with async_sessionmaker(e, expire_on_commit=False)() as s:
        pid = (await s.execute(text("SELECT id FROM pharmacies LIMIT 1"))).scalar()
        print("seeded:", await seed_zones(s, pid))
        await s.commit()
        for site, code, klass in (await s.execute(text(
                "SELECT site, code, zone_class FROM vision_zone "
                "WHERE pharmacy_id = :p ORDER BY site, code"), {"p": pid})).all():
            print(f"   {site:9} {code:14} {klass}")
    await e.dispose()
asyncio.run(m())
PY
```

Expected: 17 zones listed.

- [ ] **Step 6: Commit**

```bash
cd /Users/sashad85/PharmPilot-Claude && git add scripts/seed_vision_zones.py tests/unit/test_vision_zone_seed_e2e.py && git commit -m "feat(vision): seed the design-doc zones, idempotently"
```

---

### Task 5a-6: Full-suite verification, the defect report, and the ledger

- [ ] **Step 1: Check the test database before blaming code**

```bash
cd /Users/sashad85/PharmPilot-Claude && /Users/sashad85/miniforge3/bin/python3 -c "
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
async def m():
    e = create_async_engine('postgresql+asyncpg://pharmpilot:pharmpilot@127.0.0.1:5433/pharmpilot_test')
    async with e.begin() as c:
        print(' size:', (await c.execute(text(\"SELECT pg_size_pretty(pg_database_size('pharmpilot_test'))\"))).scalar())
        for t, n in (await c.execute(text('SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY n_live_tup DESC LIMIT 3'))).all():
            print(f'   {t}: {n:,}')
    await e.dispose()
asyncio.run(m())"
```

Expected: ~2.6 GB, `inventory_exception_rows` in the hundreds of thousands. **This is the inventory workstream's fixture design, not a fault of this phase**, and it is why the suite takes ~12 minutes rather than ~8.

- [ ] **Step 2: Run the whole unit suite**

Do **not** export `DATABASE_URL`.

```bash
cd /Users/sashad85/PharmPilot-Claude && /Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit -q --no-header -p no:cacheprovider
```

Expected: all pass except `test_integrations_sandbox.py::test_notifications_sandbox_success_shape_no_network_and_masked_logs`, which is green in isolation and pre-existing.

- [ ] **Step 3: Confirm parity, auth and a single head**

```bash
cd /Users/sashad85/PharmPilot-Claude && /Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_route_authentication.py tests/unit/test_model_column_parity.py -q -p no:randomly
```

```bash
cd /Users/sashad85/PharmPilot-Claude && /Users/sashad85/miniforge3/bin/python3 - <<'PY'
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

Expected: both green, exactly one head at `0052`.

- [ ] **Step 4: Verify the model registered — by hand**

The suite will not catch a forgotten model. Confirm it directly:

```bash
cd /Users/sashad85/PharmPilot-Claude && /Users/sashad85/miniforge3/bin/python3 -c "
from shared.models.pharmacy import Pharmacy
from shared.models.base import Base
t = sorted(Base.metadata.tables)
print('vision_zone registered:', 'vision_zone' in t)
print('total tables in metadata:', len(t))"
```

Expected: `True`.

- [ ] **Step 5: File the `diff()` defect for the inventory owner**

This plan does not write into `inventory_exceptions`, so this bug is not ours to trip — but it is real, latent, and would bite the first person who adds a second producer. Append to `docs/ai-context/AI_COLLABORATION.md` under the ledger entry, as a referred defect:

> **Referred, not fixed — inventory workstream (CL-003).** `exceptions.diff()`
> (`services/core/inventory/exceptions.py:313-317`) auto-resolves every active
> fingerprint the current run did not produce. With a single producer that is
> correct — a standing defect that stops firing has been fixed. With two
> producers writing one register, each silently closes the other's findings on
> every run, with `resolved_by='system'` and the reason "the check no longer
> fires". Nothing detects it; the symptom is an empty queue, which is
> indistinguishable from a clean shop. A `scope: set[str]` parameter naming
> the check codes a given run is authoritative for would fix it. Recorded here
> rather than edited because `exceptions.py` is CL-003's file and phase 5
> avoided the problem by not writing into that table.
>
> Related, same file: the recurrence path at
> `routers/inventory_exceptions.py:106-127` calls `_record_event` in the
> `delta.opened` branch only, never on recurrence, so the event log
> under-counts recurrences badly. Live: `pharmpilot_test` holds 381,955
> exceptions and 381,955 `opened` events, while 163,684 of those exceptions
> have `occurrences >= 2`.

- [ ] **Step 6: Append the ledger entry**

Follow the format of the most recent entries in `docs/ai-context/AI_COLLABORATION.md`. It must record: branch and commits; that phase 5 was **split into 5a and 5b** and why (with the measured 52.6–79.6 day movement write-lag and the five empty input tables); the rule disposition summary (3 of 29 buildable); the four explicit decisions above; that `ROLE_PERMISSIONS` in `shared/models/auth.py` was touched across the CL-003 boundary; that invariant I-2 is unimplemented and append-only rests on a trigger the app role can disable; checks actually run; and next action.

- [ ] **Step 7: Commit**

```bash
cd /Users/sashad85/PharmPilot-Claude && git add docs/ai-context/AI_COLLABORATION.md && git commit -m "docs: ledger entry for phase 5a, zone registry"
```

---

# PHASE 5b — Picks, rules and reconciliation

> **These are task outlines, not executable steps.** Phase 5a above is specified to the
> bite-sized standard — failing test first, complete code, exact commands, commit. 5b is
> deliberately not, and the reason is not laziness: **its acceptance criteria cannot be
> evaluated today.** Every input table is empty, and the reconciliation time basis is a
> write timestamp lagging its own event by 52.6–79.6 days. Writing 40 executable steps
> against inputs that do not exist would produce a plan whose tests all pass and whose
> feature finds nothing, forever.
>
> What follows captures every schema decision, constraint, and trap the research
> established, so none of it is lost. **5b gets its own full plan when its gate opens** —
> at which point the thresholds it needs can be set from an observed distribution rather
> than declared.

**Gate.** Do not start 5b until 5a is merged **and** the owner has decided how bays get registered (Task 5b-1 builds the write path, but whether real shelves get entered is an operations decision, not an engineering one). 5b's own first task is data production for exactly this reason.

---

### Task 5b-1: The bay write path that does not exist

**Files:** modify `services/platform/routers/depot_transfer.py`; test `tests/unit/test_shelf_registration.py`.

`vision_pick_event.bay_id` points at `pharmacy_shelves`, which has **0 rows**, GET-only routes (`depot_transfer.py:445`), and exactly one INSERT in the entire tree — a test fixture at `test_inventory_admin_e2e.py:675`. Without a write path, `bay_id` references an unpopulatable table and the whole reconciliation has nothing to join to.

Add `POST /depot/shelves` (`inventory:write`), with `label`, `zone` (validated against `vision_zone` codes via `Z.load_zone_codes`, which is why this task follows 5a), `capacity_units` and `storage_condition`. Key test: a shelf whose `zone` is not a registered vision zone is refused with a message naming the code.

---

### Task 5b-2: `vision_pick_event` and idempotent ingest

**Files:** create `data/migrations/versions/0053_vision_pick_event.py`; modify `shared/models/vision.py`, `services/platform/routers/vision.py`; test `tests/unit/test_pick_ingest.py`.

`TimestampedBase`, not `AuditedBase` — this is the highest-volume table in the phase and `is_deleted` purges nothing, so a retention proof reporting `bytes_purged` would be a lie about data still on disk.

Columns: `pharmacy_id`, `zone_id`+`site` (composite FK to `vision_zone`), `bay_id` → `pharmacy_shelves.id`, `t_start`, `t_end`, `edge_event_key` (String(80)), `binding_id` (bare uuid, **no FK, never projected** — reserved for phase 6), `reconcile_state` String(16), `matched_movement_id` → `inventory_movements.id`, `matched_transfer_id` → `shelf_transfer_events.id`, `match_detail` JSONB `nullable=False`, `expires_at`, `clock_skew_ms`.

Two constraints are load-bearing:

```python
sa.UniqueConstraint("pharmacy_id", "edge_event_key", name="uq_pick_per_edge_key"),
# An uncertain reconciliation is physically incapable of naming a movement, so
# it can never be quoted as evidence against anyone. Measured: only 8 of 39
# live movements are unique inside their own +/-5-min ndc11 window, so
# 'ambiguous' is the common case, not the edge case.
sa.CheckConstraint(
    "reconcile_state <> 'ambiguous' OR "
    "(matched_movement_id IS NULL AND matched_transfer_id IS NULL)",
    name="ck_pick_ambiguous_claims_nothing"),
sa.CheckConstraint(
    "reconcile_state <> 'matched' OR "
    "(matched_movement_id IS NOT NULL OR matched_transfer_id IS NOT NULL)",
    name="ck_pick_matched_has_reference"),
```

`POST /vision/picks` (`vision:write`) ingests a batch, upserting on `edge_event_key` so an edge retry cannot inflate the pick count — which would inflate the numerator of the very unmatched rate WH-02 exists to publish. **This is also where SY-02 lands in reduced form**: compare the edge-supplied `t_start` to server `now()` and store `clock_skew_ms`. It costs nothing and the reconciliation trusts that timestamp completely.

---

### Task 5b-3: The reconciler, its legs, and its refusal to guess

**Files:** create `services/core/vision/reconcile.py`; test `tests/unit/test_pick_reconcile.py` and `tests/unit/test_pick_reconcile_e2e.py`.

Pure functions over already-fetched rows, following `reconciliation.py:4-7`. Two test layers: hand-built counterexamples for the predicates, plus an `_e2e` module running the real SQL against **both** `pharmpilot_test` and `pharmpilot` (they differ: 76 vs 91 base tables).

**Step 0 is a measurement, not a match.** Compute `movement_time_basis` from the median movement-to-fill lag and record it: `synchronous` (< 5 min), `lagged` (anything else), `unknown` (no joinable rows). On this installation it is `lagged` at a 65.4-day median, and **Leg B must be disabled when lagged**. A pick that cannot be reconciled because the time basis does not support it is `unknowable` — a distinct state from `unmatched`, excluded from both numerator and denominator, so a supervisor is never shown a discrepancy that is an artefact of a missing column.

Three legs, in descending order of trust:

- **Leg A (spatial):** `bay_id` → `shelf_transfer_events.shelf_id`, matched on `inventory_lot_id` within the window. The only leg with a real spatial key. 0 rows today.
- **Leg B (aspatial):** `ndc11` against `inventory_movements` within ±5 min of `created_at`. **Disabled while `movement_time_basis = 'lagged'`.**
- **Leg C (lot):** `pharmacy_shelves` → `shelf_placements.inventory_lot_id` → `inventory_movements.inventory_lot_id`. The final hop is fully populated (39/39 movements carry `inventory_lot_id`, and `ix_inventory_movements_inventory_lot_id` exists). **State plainly that it does not fix ambiguity**: tested, every 6-peer neighbourhood resolves to exactly one distinct lot, so lot identity does not separate the candidates.

Match on `quantity_delta < 0`, not on a `movement_type` list — three Python vocabularies disagree and the column has no CHECK, so the sign is the only predicate the database itself guarantees the meaning of.

Carry `match_integrity`: `hash_chained` or `unverifiable`. All 8 unhashed live movements are `DAMAGE`/`EXPIRY_REMOVAL`/`ADJUSTMENT`/`RECALL_REMOVAL` — precisely the types that legitimately explain an unmatched pick, so an explanation resting on one must render as having no tamper evidence.

**Idempotence must be tested against the case that matters**: a later-arriving pick with a smaller `|lag_s|` must not steal a candidate already consumed by an earlier matched pick. Make consumption append-only and test that, rather than asserting "running twice gives identical state" on a frozen fixture where it is trivially true.

---

### Task 5b-4: The rule registry and a version that lets an anomaly explain itself

**Files:** create `data/migrations/versions/0054_vision_rules.py`, `services/core/vision/rules.py`; test `tests/unit/test_vision_rules.py`.

`vision_anomaly_rule` (`AuditedBase` — promoting a rule is exactly the decision an auditor asks who made): `code` String(16), `version` Integer, `state` String(16) (`log_only|active|demoted`), `zone_class`, `debounce_s`, `confidence_floor`, `match_window_s`, `alert_budget_per_hour`, `threshold_basis` String(20), `log_only_since`, `observations_seen`, `created_by`/`promoted_by` (both **NOT NULL FK to `staff.id`** — a nullable `created_by` makes `promoted_by != created_by` vacuous for every seeded rule), `promotion_reason`.

Only **WH-02, WH-03 and SY-04** get evaluators. The other 26 codes are declared in a Python `CATALOGUE` with their disposition and are **not** in the DB CHECK — a two-value CHECK would make adding a third rule a migration, and a 29-value CHECK would claim 26 rules exist that cannot fire.

Three constraints carry the phase:

```python
# Provenance: you may not store a threshold while claiming nothing was measured.
sa.CheckConstraint(
    "threshold_basis <> 'no_history' OR (debounce_s IS NULL AND "
    "confidence_floor IS NULL AND match_window_s IS NULL AND "
    "alert_budget_per_hour IS NULL)",
    name="ck_rule_no_unmeasured_threshold"),
# No code path, fixture or migration can make a rule capable of interrupting a
# human without a named human and a budget.
sa.CheckConstraint(
    "state <> 'active' OR (promoted_at IS NOT NULL AND promoted_by IS NOT NULL "
    "AND btrim(promotion_reason) <> '' AND alert_budget_per_hour > 0)",
    name="ck_rule_active_needs_promotion"),
sa.CheckConstraint("version >= 1", name="ck_rule_version_positive"),
```

`version` exists because `model_version` is already a house column on six live tables and because without it, an anomaly raised under the old thresholds cannot be grouped, compared, or measured across an edit.

---

### Task 5b-5: The firing log, and the two-week clock that counts evidence

**Files:** extend `0054`; create `services/core/vision/lifecycle.py`; test `tests/unit/test_rule_lifecycle.py`.

`vision_rule_firing` (`TimestampedBase`, append-only trigger copied from `trg_exception_events_append_only`): one row per trigger **whether or not it alerted**, with `rule_id`, `rule_version`, `rule_state_at_emit`, `pick_event_id`, `zone_id`, `fired_at`, `alerted` bool, `withheld_reason`, and `explanation` JSONB frozen at firing time.

This table is why the budget is countable and the log-only window measurable. It is the thing `inventory_exceptions` structurally cannot be.

```python
sa.CheckConstraint(
    "(alerted = false AND anomaly_id IS NULL) OR "
    "(alerted = true AND anomaly_id IS NOT NULL)",
    name="ck_firing_alert_consistency"),
# A log-only rule's output can never reach an operator queue, even through a
# router bug: it is a property of the row, not of the code path.
sa.CheckConstraint(
    "rule_state_at_emit = 'active' OR alerted = false",
    name="ck_firing_log_only_never_alerts"),
```

Four lifecycle rules, all of which research showed every design got wrong:

1. **The two-week clock counts evidence, not calendar.** Promotion requires `now() - log_only_since >= 14 days` **AND** `observations_seen >= MIN_OBSERVATIONS`. Fourteen days after `alembic upgrade`, a rule that has seen zero events is not eligible — it is unmeasured. This is `release_gate.py:18-20` applied here: *"A cell with too little data FAILS. Treating missing evidence as a pass is how a group with no test data gets declared safe."* Phase 3 already merged the correct gate; use it rather than `scoreboard()`'s verdicts, which admit `unmeasured` as "not noisy and not ignored".
2. **Every lifecycle function takes `now: datetime | None = None`**, following `clock.pharmacy_today(tz_name, *, now=None)`. This is how a rule is tested without waiting a fortnight — the alternative is backdating `log_only_since` by raw SQL, which writes a state the application can never produce.
3. **The budget is enforced inside the emitting transaction**, not by an hourly batch. The firing that would breach the budget is written `alerted=false, withheld_reason='budget'` and the rule moves to `demoted` in the same transaction — so the supervisor's queue receives 3 items, not 5. Alert fatigue is the safety property; a budget enforced after the flood does not preserve it.
4. **`demoted → active` is forbidden.** A flooded rule serves a full new log-only window. Follow `match_intel.verify_links()`: demote with a reason, count it, never auto-promote.

Also: **a backfill must not auto-demote the rule it was run for.** `fired_at` defaults to `now()`, so replaying 90 days breaches any hourly budget instantly. Backfilled firings carry `backfill=true` and are excluded from the budget window.

---

### Task 5b-6: `vision_anomaly`, `vision_alert_action`, and the WH-02 daily report

**Files:** create `data/migrations/versions/0055_vision_anomaly.py`; modify `services/platform/routers/vision.py`; test `tests/unit/test_vision_anomaly.py`, `tests/unit/test_wh02_report_e2e.py`.

`vision_anomaly` holds **only workflow state** and FKs to its firing — the evidence lives in the append-only firing row, so the record that could end a career is not editable with ordinary app credentials. `vision_alert_action` is the append-only action ledger with a mandatory reason, copied field-for-field from `inventory_exception_events` including its trigger.

**The day boundary needs a validated clock.** `pharmacies.timezone` has **no CHECK constraint**, and Python and SQL disagree on a bad value: `clock.zone()` catches the error and silently returns UTC, while `((now()) AT TIME ZONE 'IRST')::date` **raises**. A pharmacy configured with `'IRST'` — a plausible Iranian entry — would file picks under a UTC day and then 500 on the report. Validate the timezone at report time and store `report_tz` on the row beside `report_date`, so a later timezone correction cannot silently re-file yesterday.

`GET /vision/picks/unreconciled` (`inventory:read`) returns the daily report with a **header that publishes its own blind spots**:

```python
{"match_rate": None,        # null, never 0.0, when the denominator is zero
 "movement_time": {"basis": "lagged", "n": 31, "median_lag_days": 65.4},
 "coverage": {"bays_with_picks": 0, "bays_registered": 0,
              "zones_without_polygon": 17,
              "movements_in_day_with_no_pick": 0},
 "excluded": {"unknowable": 0, "no_ledger_coverage": 0}}
```

`movements_in_day_with_no_pick` is a recall proxy that raises no alerts — it is what tells a supervisor the camera is blind rather than the shelf clean. **A rule must not alert about its own blindness.**

`POST /vision/picks/{id}/resolve` (`inventory:write`) requires a reason and writes an action row.

---

### Task 5b-7: Full-suite verification, the PH-rule reconciliation note, and the ledger

Beyond the standard verification, one thing must be written down:

**Five of the ten PH rules are already implemented.** `services/biometric/behavioral_analysis/detector.py:34-48` defines eleven `BehaviorType` values — `LOITERING`, `COUNTER_BYPASS_ATTEMPT`, `VAULT_ZONE_INTRUSION`, `TAILGATING`, `AFTER_HOURS_PRESENCE`, `FALL_DETECTED` and more — with hardcoded thresholds (`LOITERING_THRESHOLD_SECONDS = 480`, `STATIONARY_THRESHOLD_PIXELS = 30`, `AGGRESSION_VELOCITY_THRESHOLD = 200`, `TAILGATING_TIME_WINDOW_SECONDS = 2.0`), its own severity vocabulary, its own zone names, and a passing test suite. These overlap PH-01, PH-02, PH-03, PH-07 and WH-01.

The rule catalogue must state, per PH code, its relationship to the matching `BehaviorType` — subsume, alias, or leave alone — pinned by a test, exactly as Task 5a-3 does for the nine zone vocabularies. Otherwise phase 6 promotes PH-02 and loitering fires under two codes with two severity scales. Those five constants are also exactly the fabricated fallback constants the provenance rule forbids, with no basis field; note that every design's provenance discipline applied only to the tables it created.

---

## What this plan deliberately does not do

- **No camera registry.** `vision_camera` is needed by SY-01 and SY-02 and **no phase in the seven-phase programme currently owns building it**. That is a gap in the programme, not a deferral by this phase, and the ledger must say so.
- **No retention enforcement.** `retention_days` is written as policy and stays NULL until measured. Nothing purges: zero partitioned relations, no `pg_cron`/`pg_partman`, no purge job anywhere. Phase 7 must build it, and `vision_retention_job.bytes_purged` cannot be satisfied by soft delete.
- **No identity, no bindings, no clips.** `binding_id` is reserved as an unFK'd, never-projected column; `clip_ref` stays an opaque varchar with no FK so the clip table arrives later without a data migration. `vision_transaction_link` — the highest-risk privacy surface in the design doc — must not be built alongside a reconciliation feature.
- **No two-person escalation table.** Phase 5 exposes no identity at all, so there is nothing yet to gate. When phase 6 adds the binding endpoint it must add `vision_escalation` with `escalated_by` and `seconded_by` both NOT NULL FKs, `CHECK (seconded_by <> escalated_by)`, and a NOT NULL corroborating non-video record — **not** a `reviewed_by` column and **not** a free-text verb in another workstream's table.
- **No scheduler.** Nothing runs the reconciler; the report is a pull endpoint. An executor is phase 7, and it must not inherit `sweep.py:158-163`'s naive `datetime.now()`, which schedules on the process clock while its docstring claims "the pharmacy's own small hours".
- **No WS stream.** `require_permission` cannot be used in a WS scope, and all three existing WS routes take `pharmacy_id` from the path and never compare it to `staff.pharmacy_id`.

---

## Open decisions for the owner

1. **Bay registration is an operations decision.** Task 5b-1 builds the write path; whether real shelves get entered, and by whom, is not an engineering call. Until they are, reconciliation has nothing to join to.
2. **`inventory_movements` needs an event-time column** for WH-02 to ever be measurable. That is the inventory workstream's table and its append-only trigger blocks a backfill. Either movements start being written synchronously from now on — in which case reconciliation becomes measurable for future data only — or the owner accepts that WH-02 reports `unknowable` for historical picks permanently.
3. **Two operator queues** exist until phase 6 merges the read model: `inventory_exceptions` and `vision_anomaly`. That is the accepted cost of not writing into another workstream's table.
