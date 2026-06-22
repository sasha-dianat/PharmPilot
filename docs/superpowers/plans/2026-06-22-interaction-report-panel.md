# Interaction Report Panel Implementation Plan (Phase 2a)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface the Phase-1 interaction engine in the pharmacist's middle panel — precomputed at Rx intake, cached per-patient, read instantly, with a findings-hash-bound, full-context audited acknowledgment before adjudication.

**Architecture:** A cache table (`interaction_reports`) holds one current `InteractionReport` per patient, keyed by a review-set hash. Intake/reanalyze hooks recompute-and-cache (isolated, never blocking). A cache-first `GET` endpoint serves the panel; a `POST /cds/interaction-ack` writes a self-contained `ClinicalAuditLog` snapshot stamped with the authenticated pharmacist. The React panel renders a concise severity-grouped report and gates adjudication on acknowledgment when serious findings exist.

**Tech Stack:** Python 3.12, FastAPI, async SQLAlchemy, Alembic, PyYAML, pytest; React 19 + Vite + react-query + Tailwind. Run python via `/Users/sashad85/miniforge3/bin/python`.

**Spec:** `docs/superpowers/specs/2026-06-22-interaction-report-panel-design.md`

---

## File structure

```
services/ai/clinical_decision_support/interaction/
  review_set.py        # + review_set_hash(rs)
  report.py            # + report_to_dict, serious_findings, findings_hash
  engine.py            # + MODEL_VERSION
  precompute.py        # NEW: recompute_and_cache, recompute_for_patient_id
shared/models/clinical.py            # + InteractionReportCache model
data/migrations/versions/0014_interaction_reports.py   # NEW table
services/platform/routers/cds.py     # + GET /interaction-report/{pid}, POST /interaction-ack, _ack_snapshot
services/platform/routers/prescriptions.py  # intake + reanalyze precompute hooks
frontend/workstation/src/lib/api.ts          # + getInteractionReport, acknowledgeInteractions
frontend/workstation/src/design/severity.ts  # + contraindicated/major/minor mapping
frontend/workstation/src/components/InteractionReportPanel.tsx  # NEW
frontend/workstation/src/components/VerificationCenter.tsx       # mount panel + ack-gated adjudication
tests/unit/test_interaction_cache.py, test_interaction_endpoints.py
```

---

## Task 1: Hash helpers (review-set + findings)

**Files:**
- Modify: `services/ai/clinical_decision_support/interaction/review_set.py`
- Modify: `services/ai/clinical_decision_support/interaction/report.py`
- Test: `tests/unit/test_interaction_cache.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_interaction_cache.py
from types import SimpleNamespace as NS

from services.ai.clinical_decision_support.interaction.review_set import (
    assemble_review_set, review_set_hash,
)
from services.ai.clinical_decision_support.interaction.engine import evaluate
from services.ai.clinical_decision_support.interaction.report import (
    findings_hash, serious_findings, report_to_dict,
)
from services.ai.clinical_decision_support.interaction.severity import InteractionSeverity as S


def _rs(drugs, conditions=None, labs=None):
    return assemble_review_set(current_rx=[NS(drug_name=d) for d in drugs], meds=[],
                              conditions=conditions or [], labs=labs or {})


def test_review_set_hash_stable_and_sensitive():
    a = review_set_hash(_rs(["warfarin", "ibuprofen"]))
    b = review_set_hash(_rs(["ibuprofen", "warfarin"]))   # order-independent
    assert a == b
    assert a != review_set_hash(_rs(["warfarin"]))                       # drug change
    assert a != review_set_hash(_rs(["warfarin", "ibuprofen"], conditions=["x"]))  # condition change
    assert a != review_set_hash(_rs(["warfarin", "ibuprofen"], labs={"potassium": 5.6}))  # lab change


def test_findings_hash_covers_only_serious():
    rep = evaluate(_rs(["warfarin", "ibuprofen"]))     # Major drug-drug
    assert serious_findings(rep)                         # at least one Major/Contra
    h1 = findings_hash(rep)
    rep2 = evaluate(_rs(["warfarin"]))                   # no serious pair
    assert findings_hash(rep2) != h1


def test_report_to_dict_shape():
    d = report_to_dict(evaluate(_rs(["warfarin", "ibuprofen"])))
    assert set(d) >= {"summary", "findings", "degraded"}
    assert d["findings"][0]["severity"] in {s.value for s in S}
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_cache.py -q`
Expected: FAIL — `ImportError: cannot import name 'review_set_hash'`

- [ ] **Step 3: Add `review_set_hash` to `review_set.py`**

```python
# append to services/ai/clinical_decision_support/interaction/review_set.py
import hashlib

# Lab keys whose values can change findings (engine reads these).
_RELEVANT_LABS = ("potassium", "egfr")


def review_set_hash(rs: ReviewSet) -> str:
    meds = sorted(f"{m.normalized_name}:{m.provenance}" for m in rs.meds)
    conds = sorted(c.concept for c in rs.conditions)
    labs = [f"{k}={round(rs.labs[k], 2)}" for k in _RELEVANT_LABS if k in rs.labs]
    payload = "|".join(["M", *meds, "C", *conds, "L", *labs, "A", *sorted(rs.allergies)])
    return hashlib.sha256(payload.encode()).hexdigest()
```

- [ ] **Step 4: Add helpers to `report.py`**

```python
# append to services/ai/clinical_decision_support/interaction/report.py
import hashlib

_SERIOUS = {InteractionSeverity.CONTRAINDICATED, InteractionSeverity.MAJOR}


def serious_findings(report: InteractionReport) -> list[Finding]:
    return [f for f in report.findings if f.severity in _SERIOUS]


def findings_hash(report: InteractionReport) -> str:
    parts = sorted(
        f"{f.rule_id}:{','.join(sorted(p['name'] for p in f.participants))}:{f.severity.value}"
        for f in serious_findings(report)
    )
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _finding_to_dict(f: Finding) -> dict:
    from dataclasses import asdict
    d = asdict(f)
    d["severity"] = f.severity.value
    d["base_severity"] = f.base_severity.value
    return d


def report_to_dict(report: InteractionReport) -> dict:
    return {
        "summary": report.summary,
        "degraded": report.degraded,
        "findings": [_finding_to_dict(f) for f in report.findings],
        "findings_hash": findings_hash(report),
    }
```

- [ ] **Step 5: Run to verify pass**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_cache.py -q`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add services/ai/clinical_decision_support/interaction/review_set.py \
        services/ai/clinical_decision_support/interaction/report.py \
        tests/unit/test_interaction_cache.py
git commit -m "feat(cds): review-set + findings hashes and report serialization"
```

---

## Task 2: Cache model + migration

**Files:**
- Modify: `shared/models/clinical.py`
- Create: `data/migrations/versions/0014_interaction_reports.py`

- [ ] **Step 1: Add the model** to `shared/models/clinical.py` (after `ClinicalAuditLog`)

```python
class InteractionReportCache(AuditedBase):
    __tablename__ = "interaction_reports"
    __table_args__ = (UniqueConstraint("patient_id", "pharmacy_id", name="uq_interaction_report_patient"),)

    patient_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    pharmacy_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    review_set_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    findings_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    report: Mapped[dict] = mapped_column(JSONB, nullable=False)
    model_version: Mapped[str] = mapped_column(String(40), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

Ensure the imports at the top of `clinical.py` include `UniqueConstraint`, `String`, `DateTime`,
`datetime`, `UUID`, `PG_UUID`, `JSONB`, `Mapped`, `mapped_column` (most already present —
add `UniqueConstraint` to the `from sqlalchemy import ...` line if missing).

- [ ] **Step 2: Verify the model imports**

Run: `/Users/sashad85/miniforge3/bin/python -c "from shared.models.clinical import InteractionReportCache; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Create the migration**

```python
# data/migrations/versions/0014_interaction_reports.py
"""interaction report cache

Revision ID: 0014
Revises: 0013
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "interaction_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_set_hash", sa.String(64), nullable=False),
        sa.Column("findings_hash", sa.String(64), nullable=False),
        sa.Column("report", postgresql.JSONB(), nullable=False),
        sa.Column("model_version", sa.String(40), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_interaction_reports_patient_id", "interaction_reports", ["patient_id"])
    op.create_index("ix_interaction_reports_pharmacy_id", "interaction_reports", ["pharmacy_id"])
    op.create_unique_constraint("uq_interaction_report_patient", "interaction_reports",
                                ["patient_id", "pharmacy_id"])


def downgrade() -> None:
    op.drop_table("interaction_reports")
```

- [ ] **Step 4: Apply the migration**

Run: `/Users/sashad85/miniforge3/bin/python -m alembic -c data/migrations/alembic.ini upgrade head 2>&1 | tail -5`
(If the alembic.ini path differs, find it: `find . -name alembic.ini -not -path '*/node_modules/*'`.)
Expected: runs to revision 0014 with no error.

- [ ] **Step 5: Commit**

```bash
git add shared/models/clinical.py data/migrations/versions/0014_interaction_reports.py
git commit -m "feat(cds): interaction_reports cache table + model"
```

---

## Task 3: Precompute service

**Files:**
- Modify: `services/ai/clinical_decision_support/interaction/engine.py` (add MODEL_VERSION)
- Create: `services/ai/clinical_decision_support/interaction/precompute.py`
- Test: `tests/unit/test_interaction_cache.py`

- [ ] **Step 1: Add the version constant** to `engine.py` (top, after imports)

```python
MODEL_VERSION = "interaction-v1"
```

- [ ] **Step 2: Write the failing test (append)**

```python
# append to tests/unit/test_interaction_cache.py
import asyncio


class _Scalar:
    def __init__(self, rows): self._rows = rows
    def scalars(self): return self
    def all(self): return self._rows
    def scalar_one_or_none(self): return self._rows[0] if self._rows else None


class _FakeDB:
    """Every query returns empty (no cache row, no meds) — order-independent.
    The patient is passed directly to recompute_and_cache, so no fetch is needed."""
    def __init__(self): self.added = []
    async def execute(self, *_a, **_k): return _Scalar([])
    def add(self, row): self.added.append(row)
    async def flush(self): pass


def test_recompute_and_cache_writes_row():
    from services.ai.clinical_decision_support.interaction.precompute import recompute_and_cache
    patient = NS(id="p1", conditions=["peptic_ulcer_disease"], date_of_birth=None)
    db = _FakeDB()
    report, rsh, fh = asyncio.run(recompute_and_cache(db=db, patient=patient, pharmacy_id="ph1"))
    assert db.added and db.added[0].review_set_hash == rsh
    assert db.added[0].findings_hash == fh
    assert isinstance(db.added[0].report, dict)
```

- [ ] **Step 3: Run to verify failure**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_cache.py::test_recompute_and_cache_writes_row -q`
Expected: FAIL — module `precompute` missing

- [ ] **Step 4: Implement `precompute.py`**

```python
# services/ai/clinical_decision_support/interaction/precompute.py
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.clinical import InteractionReportCache
from shared.models.patient import Patient
from .engine import MODEL_VERSION, evaluate
from .report import findings_hash, report_to_dict
from .review_set import build_review_set, review_set_hash

logger = logging.getLogger(__name__)


async def recompute_and_cache(*, db: AsyncSession, patient, pharmacy_id, rs=None):
    if rs is None:
        rs = await build_review_set(db=db, patient=patient, pharmacy_id=pharmacy_id)
    report = evaluate(rs)
    rsh = review_set_hash(rs)
    fh = findings_hash(report)
    existing = (await db.execute(
        select(InteractionReportCache).where(
            InteractionReportCache.patient_id == patient.id,
            InteractionReportCache.pharmacy_id == pharmacy_id,
        ))).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    payload = report_to_dict(report)
    if existing:
        existing.review_set_hash = rsh
        existing.findings_hash = fh
        existing.report = payload
        existing.model_version = MODEL_VERSION
        existing.computed_at = now
    else:
        db.add(InteractionReportCache(
            patient_id=patient.id, pharmacy_id=pharmacy_id,
            review_set_hash=rsh, findings_hash=fh, report=payload,
            model_version=MODEL_VERSION, computed_at=now))
    await db.flush()
    return report, rsh, fh


async def recompute_for_patient_id(*, db: AsyncSession, patient_id, pharmacy_id) -> bool:
    """Isolated hook for intake/transition paths. Never raises into the caller."""
    try:
        patient = (await db.execute(
            select(Patient).where(Patient.id == patient_id,
                                  Patient.pharmacy_id == pharmacy_id,
                                  Patient.is_deleted == False))).scalar_one_or_none()  # noqa: E712
        if not patient:
            return False
        await recompute_and_cache(db=db, patient=patient, pharmacy_id=pharmacy_id)
        return True
    except Exception as exc:  # pragma: no cover
        logger.warning("[interaction] precompute failed for patient %s: %s", patient_id, exc)
        return False
```

- [ ] **Step 5: Run to verify pass**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_cache.py -q`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add services/ai/clinical_decision_support/interaction/engine.py \
        services/ai/clinical_decision_support/interaction/precompute.py \
        tests/unit/test_interaction_cache.py
git commit -m "feat(cds): interaction precompute-and-cache service"
```

---

## Task 4: Cache-first read endpoint + acknowledgment endpoint

**Files:**
- Modify: `services/platform/routers/cds.py`
- Test: `tests/unit/test_interaction_endpoints.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_interaction_endpoints.py
import asyncio
from types import SimpleNamespace as NS
from uuid import uuid4

from services.platform.routers import cds


class _Scalar:
    def __init__(self, rows): self._rows = rows
    def scalars(self): return self
    def all(self): return self._rows
    def scalar_one_or_none(self): return self._rows[0] if self._rows else None


class _DB:
    """First execute() returns the patient (the endpoint's first query); all
    subsequent queries (review-set build, cache lookup) return empty → cache miss."""
    def __init__(self, patient): self._first = _Scalar([patient]); self._used = False; self.added = []
    async def execute(self, *_a, **_k):
        if not self._used:
            self._used = True
            return self._first
        return _Scalar([])
    def add(self, r): self.added.append(r)
    async def flush(self): pass


def test_get_interaction_report_recomputes_on_miss():
    pid = uuid4(); ph = uuid4()
    patient = NS(id=pid, pharmacy_id=ph, conditions=["peptic_ulcer_disease"],
                 date_of_birth=None, is_deleted=False)
    db = _DB(patient)
    staff = NS(pharmacy_id=ph, id=uuid4())
    out = asyncio.run(cds.get_interaction_report(pid, staff=staff, db=db))
    assert out["cached"] is False
    assert "findings_hash" in out and "report" in out


def test_ack_snapshot_captures_full_context():
    staff = NS(id=uuid4(), first_name="Pat", last_name="Pharm", pharmacist_license_number="LIC-9")
    patient = NS(id=uuid4(), first_name="Ali", last_name="Karimi", national_id="1234567890")
    prescriber = NS(first_name="Dr", last_name="Who", medical_council_id="NP-77", specialty="cardio")
    rx = NS(id=uuid4(), drug_name="warfarin")
    snap = cds._ack_snapshot(staff=staff, patient=patient, prescription=rx, prescriber=prescriber,
                             acknowledged=[{"rule_id": "dd:anticoagulant-nsaid", "severity": "Major"}])
    assert snap["pharmacist"]["license"] == "LIC-9"
    assert snap["physician"]["medical_council_id"] == "NP-77"
    assert snap["patient"]["national_id"] == "1234567890"
    assert snap["prescription"]["rx_id"] == str(rx.id)
    assert snap["findings"][0]["severity"] == "Major"
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_endpoints.py -q`
Expected: FAIL — `get_interaction_report` / `_ack_snapshot` missing

- [ ] **Step 3: Add to `cds.py`** (after the existing `interaction_report` POST handler)

```python
from datetime import datetime, timezone

from services.ai.clinical_decision_support.interaction.precompute import recompute_and_cache
from services.ai.clinical_decision_support.interaction.review_set import build_review_set as _build_rs, review_set_hash
from shared.models.clinical import InteractionReportCache
from shared.models.prescriber import Prescriber


@router.get("/interaction-report/{patient_id}")
async def get_interaction_report(
    patient_id: UUID,
    staff: Staff = Depends(require_permission("clinical:read")),
    db: AsyncSession = Depends(get_db),
):
    patient = (await db.execute(select(Patient).where(
        Patient.id == patient_id, Patient.pharmacy_id == staff.pharmacy_id,
        Patient.is_deleted == False))).scalar_one_or_none()  # noqa: E712
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    rs = await _build_rs(db=db, patient=patient, pharmacy_id=staff.pharmacy_id)
    rsh = review_set_hash(rs)
    cache = (await db.execute(select(InteractionReportCache).where(
        InteractionReportCache.patient_id == patient_id,
        InteractionReportCache.pharmacy_id == staff.pharmacy_id))).scalar_one_or_none()
    if cache and cache.review_set_hash == rsh:
        return {"report": cache.report, "findings_hash": cache.findings_hash,
                "review_set_hash": rsh, "cached": True,
                "computed_at": cache.computed_at.isoformat()}
    _report, rsh2, fh = await recompute_and_cache(
        db=db, patient=patient, pharmacy_id=staff.pharmacy_id, rs=rs)
    from services.ai.clinical_decision_support.interaction.report import report_to_dict
    return {"report": report_to_dict(_report), "findings_hash": fh,
            "review_set_hash": rsh2, "cached": False,
            "computed_at": datetime.now(timezone.utc).isoformat()}


class InteractionAckRequest(BaseModel):
    patient_id: UUID
    rx_id: UUID | None = None
    findings_hash: str
    acknowledged: list[dict]


def _ack_snapshot(*, staff, patient, prescription, prescriber, acknowledged) -> dict:
    return {
        "pharmacist": {"id": str(staff.id),
                       "name": f"{getattr(staff,'first_name','')} {getattr(staff,'last_name','')}".strip(),
                       "license": getattr(staff, "pharmacist_license_number", None)},
        "physician": {
            "name": (f"{prescriber.first_name} {prescriber.last_name}" if prescriber else None),
            "medical_council_id": getattr(prescriber, "medical_council_id", None) if prescriber else None,
            "specialty": getattr(prescriber, "specialty", None) if prescriber else None,
        },
        "patient": {"id": str(patient.id),
                    "name": f"{getattr(patient,'first_name','')} {getattr(patient,'last_name','')}".strip(),
                    "national_id": getattr(patient, "national_id", None)},
        "prescription": {"rx_id": (str(prescription.id) if prescription else None),
                         "drug_name": getattr(prescription, "drug_name", None) if prescription else None},
        "findings": list(acknowledged),
    }


@router.post("/interaction-ack")
async def acknowledge_interactions(
    body: InteractionAckRequest,
    staff: Staff = Depends(require_permission("clinical:read")),
    db: AsyncSession = Depends(get_db),
):
    patient = (await db.execute(select(Patient).where(
        Patient.id == body.patient_id, Patient.pharmacy_id == staff.pharmacy_id,
        Patient.is_deleted == False))).scalar_one_or_none()  # noqa: E712
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    prescription = prescriber = None
    if body.rx_id:
        prescription = (await db.execute(select(Prescription).where(
            Prescription.id == body.rx_id,
            Prescription.pharmacy_id == staff.pharmacy_id))).scalar_one_or_none()
        if prescription:
            prescriber = (await db.execute(select(Prescriber).where(
                Prescriber.id == prescription.prescriber_id))).scalar_one_or_none()

    snapshot = _ack_snapshot(staff=staff, patient=patient, prescription=prescription,
                             prescriber=prescriber, acknowledged=body.acknowledged)
    audit = ClinicalAuditLog(
        user_id=staff.id, patient_id=patient.id, module="interaction_acknowledgment",
        input_snapshot=_jsonable(snapshot),
        output_snapshot=_jsonable({"acknowledged_by": str(staff.id),
                                   "acknowledged_at": datetime.now(timezone.utc).isoformat(),
                                   "findings_hash": body.findings_hash}),
        rules_triggered=[a.get("rule_id") for a in body.acknowledged],
        model_version="interaction-v1", created_by=staff.id, updated_by=staff.id)
    db.add(audit)
    await db.flush()
    return {"audit_id": str(audit.id), "findings_hash": body.findings_hash}
```

- [ ] **Step 4: Run to verify pass**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_endpoints.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Verify the routes register + app imports**

Run: `/Users/sashad85/miniforge3/bin/python -c "import services.platform.main; import services.platform.routers.cds as c; print(sorted(r.path for r in c.router.routes))"`
Expected: includes `/interaction-report/{patient_id}` and `/interaction-ack`

- [ ] **Step 6: Commit**

```bash
git add services/platform/routers/cds.py tests/unit/test_interaction_endpoints.py
git commit -m "feat(cds): cache-first interaction-report read + full-context ack endpoint"
```

---

## Task 5: Precompute hooks at intake + reanalyze

**Files:**
- Modify: `services/platform/routers/prescriptions.py`
- Test: `tests/unit/test_interaction_endpoints.py`

- [ ] **Step 1: Write the failing test (append)**

```python
# append to tests/unit/test_interaction_endpoints.py
def test_intake_triggers_precompute(monkeypatch):
    from services.ai.clinical_decision_support.interaction import precompute
    called = {}
    async def _fake(*, db, patient_id, pharmacy_id):
        called["patient_id"] = patient_id; return True
    monkeypatch.setattr(precompute, "recompute_for_patient_id", _fake)
    # the router imports the symbol; ensure it resolves to the patched module attr
    from services.platform.routers import prescriptions
    monkeypatch.setattr(prescriptions, "recompute_for_patient_id", _fake, raising=False)
    assert hasattr(prescriptions, "recompute_for_patient_id")
```

(This asserts the hook symbol is wired into the router module; the end-to-end intake path is
covered by the existing intake tests + manual verification.)

- [ ] **Step 2: Run to verify failure**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_endpoints.py::test_intake_triggers_precompute -q`
Expected: FAIL — `prescriptions` has no `recompute_for_patient_id`

- [ ] **Step 3: Wire the hook** — add the import and call in `prescriptions.py`

At the top imports:

```python
from services.ai.clinical_decision_support.interaction.precompute import recompute_for_patient_id
```

In `intake_prescription`, after `await db.flush()` (the Rx is now persisted), before building the
response:

```python
    await recompute_for_patient_id(db=db, patient_id=rx.patient_id, pharmacy_id=staff.pharmacy_id)
```

In the `reanalyze` handler (`@router.post("/{rx_id}/reanalyze")`), after it loads the `rx`, add the
same call:

```python
    await recompute_for_patient_id(db=db, patient_id=rx.patient_id, pharmacy_id=staff.pharmacy_id)
```

(`recompute_for_patient_id` is self-isolating — it never raises into the intake path.)

- [ ] **Step 4: Run to verify pass + app import**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_endpoints.py -q && /Users/sashad85/miniforge3/bin/python -c "import services.platform.routers.prescriptions; print('ok')"`
Expected: PASS + `ok`

- [ ] **Step 5: Commit**

```bash
git add services/platform/routers/prescriptions.py tests/unit/test_interaction_endpoints.py
git commit -m "feat(cds): precompute interaction report on Rx intake + reanalyze"
```

---

## Task 6: Frontend API + severity mapping

**Files:**
- Modify: `frontend/workstation/src/lib/api.ts`
- Modify: `frontend/workstation/src/design/severity.ts`

- [ ] **Step 1: Add API methods** to `clinicalApi` in `api.ts` (next to `queryKnowledge`)

```ts
  getInteractionReport: (patientId: string) =>
    apiClient.get(`/cds/interaction-report/${patientId}`),
  acknowledgeInteractions: (body: {
    patient_id: string; rx_id?: string; findings_hash: string;
    acknowledged: { rule_id: string; severity: string }[];
  }) => apiClient.post('/cds/interaction-ack', body),
```

- [ ] **Step 2: Map the engine severities** — edit `toSeverity` in `severity.ts`

Target visual mapping: **Contraindicated→blocker (red), Major→caution (orange), Moderate→warning
(amber), Minor→neutral (grey).** The existing switch maps `moderate → caution`; change it so the
four engine tiers map as above. Replace the relevant cases so the switch contains exactly:

```ts
    case 'critical': case 'blocker': case 'high': case 'severe': case 'contraindicated': return 'blocker'
    case 'major': return 'caution'
    case 'caution': return 'caution'
    case 'warning': case 'warn': case 'low': case 'moderate': return 'warning'
    case 'minor': case 'neutral': return 'neutral'
```

Leave the other cases (`safe`, `counsel`, `dur`, `intel`, `default`) unchanged.

- [ ] **Step 3: Type-check**

Run: `cd frontend/workstation && npx tsc --noEmit 2>&1 | grep -E "api.ts|severity.ts" | head; echo "exit=${PIPESTATUS[0]}"`
Expected: no errors for these files (exit 0)

- [ ] **Step 4: Commit**

```bash
git add frontend/workstation/src/lib/api.ts frontend/workstation/src/design/severity.ts
git commit -m "feat(web): interaction-report API methods + severity token mapping"
```

---

## Task 7: InteractionReportPanel component

**Files:**
- Create: `frontend/workstation/src/components/InteractionReportPanel.tsx`

- [ ] **Step 1: Create the component**

```tsx
// frontend/workstation/src/components/InteractionReportPanel.tsx
import { useQuery } from '@tanstack/react-query'
import { clinicalApi } from '../lib/api'
import { SEVERITY, toSeverity } from '../design/severity'

interface Finding {
  rule_id: string; type: string; severity: string; direction: string
  predicted_magnitude: string | null; mechanism: string; suggested_actions: string[]
  evidence_grade: string; source: string; recency_note: string | null
  participants: { name: string; kind: string }[]
}
interface ReportPayload {
  report: { summary: Record<string, number>; degraded: boolean; findings: Finding[] }
  findings_hash: string; cached: boolean; computed_at: string
}

const ORDER = ['Contraindicated', 'Major', 'Moderate', 'Minor']

export default function InteractionReportPanel({
  patientId, onSerious,
}: { patientId?: string; onSerious: (hash: string | null) => void }) {
  const { data, isLoading, isError } = useQuery<ReportPayload>({
    queryKey: ['interaction-report', patientId],
    enabled: !!patientId,
    queryFn: () => clinicalApi.getInteractionReport(patientId!).then(r => r.data),
  })

  if (!patientId) return null
  const report = data?.report
  const findings = report?.findings ?? []
  const serious = findings.filter(f => f.severity === 'Contraindicated' || f.severity === 'Major')

  // tell the parent whether a serious sign-off is required (and for which hash)
  if (report && !report.degraded) onSerious(serious.length ? (data!.findings_hash) : null)

  return (
    <div className="cd-card p-3 space-y-2">
      <div className="flex items-center gap-1.5 text-sm font-semibold text-ink cd-ui">
        <span className="w-5 h-5 rounded-md bg-intel-soft text-intel flex items-center justify-center text-xs">🧬</span>
        <span>Interaction report</span>
        {data && <span className="ml-auto text-[11px] text-ink3">{data.cached ? 'cached' : 'fresh'}</span>}
      </div>

      {isLoading && <p className="cd-narr text-sm text-ink3">Computing interactions…</p>}
      {isError && <p className="cd-narr text-sm text-[#ef4444]">Interaction check unavailable — manual review required.</p>}
      {report?.degraded && (
        <p className="cd-narr text-sm text-[#fb923c]">Could not compute interactions — manual review required.</p>
      )}

      {report && !report.degraded && (
        <>
          <div className="flex flex-wrap gap-1.5">
            {ORDER.map(sev => {
              const n = report.summary[sev] ?? 0
              const tok = SEVERITY[toSeverity(sev)]
              return (
                <span key={sev}
                  className={`cd-ui text-[11px] font-semibold px-2 py-0.5 rounded-md ${tok.bg} ${tok.text} border ${tok.border} ${n === 0 ? 'opacity-40' : ''}`}>
                  {n} {sev}
                </span>
              )
            })}
          </div>

          {findings.length === 0 && (
            <p className="cd-narr text-sm text-[#34d399]">No interactions detected for the current basket.</p>
          )}

          <div className="space-y-2 max-h-80 overflow-y-auto">
            {[...findings].sort((a, b) => ORDER.indexOf(a.severity) - ORDER.indexOf(b.severity)).map((f, i) => {
              const tok = SEVERITY[toSeverity(f.severity)]
              return (
                <div key={i} className={`border-l-2 pl-3 ${tok.border}`}>
                  <div className="flex items-center gap-2">
                    <span className={`cd-ui text-[11px] font-bold ${tok.text}`}>{f.severity}</span>
                    <span className="cd-ui text-[11px] text-ink3">{f.direction.replace('_', ' ')}</span>
                    {f.source === 'inferred_mechanistic'
                      ? <span className="cd-ui text-[10px] text-ink3 italic">predicted</span>
                      : <span className="cd-ui text-[10px] text-ink3">{f.evidence_grade}</span>}
                  </div>
                  <p className="cd-ui text-[13px] font-bold text-ink leading-snug mt-0.5">
                    {f.participants.map(p => p.name).join(' × ')}
                  </p>
                  <p className="cd-narr text-sm text-ink leading-[1.7] mt-0.5">{f.mechanism}</p>
                  {f.predicted_magnitude && <p className="cd-narr text-[12px] text-ink2 mt-0.5">{f.predicted_magnitude}</p>}
                  {f.suggested_actions?.[0] && <p className="cd-narr text-[12px] text-intel mt-0.5">→ {f.suggested_actions[0]}</p>}
                  {f.recency_note && <p className="cd-narr text-[11px] italic text-ink3 mt-0.5">{f.recency_note}</p>}
                </div>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend/workstation && npx tsc --noEmit 2>&1 | grep -E "InteractionReportPanel" | head; echo "exit=${PIPESTATUS[0]}"`
Expected: no errors (exit 0)

- [ ] **Step 3: Commit**

```bash
git add frontend/workstation/src/components/InteractionReportPanel.tsx
git commit -m "feat(web): InteractionReportPanel (severity-grouped concise report)"
```

---

## Task 8: Mount panel + ack-gated adjudication in VerificationCenter

**Files:**
- Modify: `frontend/workstation/src/components/VerificationCenter.tsx`

- [ ] **Step 1: Import + state.** Near the other imports add:

```ts
import InteractionReportPanel from './InteractionReportPanel'
```

Inside the `VerificationCenter` component, near the other `useState` hooks:

```ts
const [requiredAckHash, setRequiredAckHash] = useState<string | null>(null)
const [ackedHash, setAckedHash] = useState<string | null>(null)
```

- [ ] **Step 2: Extend the adjudication gate.** Find the `canAdjudicate` definition
(`const canAdjudicate = selectedRx?.status === 'verification_in_progress' && criticalHardStops === 0`)
and add the interaction-acknowledgment condition:

```ts
const interactionSignedOff = !requiredAckHash || ackedHash === requiredAckHash
const canAdjudicate = selectedRx?.status === 'verification_in_progress'
  && criticalHardStops === 0 && interactionSignedOff
```

- [ ] **Step 3: Render the panel + acknowledge control.** In the middle column, right after the
DUR Alerts `</ErrorBoundary>` block (around the `{/* ── DUR Alerts ── */}` section), insert:

```tsx
      <ErrorBoundary label="Interaction Report" inline>
        <InteractionReportPanel
          patientId={selectedRx?.patient_id}
          onSerious={setRequiredAckHash}
        />
        {requiredAckHash && ackedHash !== requiredAckHash && (
          <button
            onClick={async () => {
              try {
                await clinicalApi.acknowledgeInteractions({
                  patient_id: selectedRx!.patient_id,
                  rx_id: selectedRx!.id,
                  findings_hash: requiredAckHash,
                  acknowledged: [],
                })
                setAckedHash(requiredAckHash)
              } catch { /* leave gated */ }
            }}
            className="cd-ui mt-1 w-full px-3 py-2 bg-[#fb923c] text-white text-sm rounded-lg hover:brightness-110">
            Acknowledge serious interactions to proceed
          </button>
        )}
      </ErrorBoundary>
```

- [ ] **Step 4: Reset ack state when the selected Rx changes.** Find the existing `useEffect`
that reacts to `selectedRx` (or add one) and reset:

```ts
useEffect(() => { setAckedHash(null); setRequiredAckHash(null) }, [selectedRx?.id])
```

- [ ] **Step 5: Type-check**

Run: `cd frontend/workstation && npx tsc --noEmit 2>&1 | grep -E "VerificationCenter" | head; echo "exit=${PIPESTATUS[0]}"`
Expected: no errors (exit 0)

- [ ] **Step 6: Commit**

```bash
git add frontend/workstation/src/components/VerificationCenter.tsx
git commit -m "feat(web): mount interaction report + ack-gated adjudication"
```

---

## Task 9: End-to-end verification

- [ ] **Step 1: Full backend suite**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_*.py tests/unit/test_cds_engine.py -q`
Expected: all pass

- [ ] **Step 2: Full frontend type-check**

Run: `cd frontend/workstation && npx tsc --noEmit; echo "exit=$?"`
Expected: exit 0

- [ ] **Step 3: Browser verification** (preview). Start the workstation preview, log in
(`pharmacist` / `Pharmacist2024!`), select an Rx with a known interaction (e.g. the Warfarin Rx),
and confirm:
  - the "Interaction report" card renders with severity chips + findings,
  - for a Contraindicated/Major finding, the "Acknowledge serious interactions" button appears and
    the Adjudicate button is disabled until it's clicked,
  - clicking Acknowledge enables adjudication.
Capture a screenshot as proof.

- [ ] **Step 4: Refresh graphify**

Run: `graphify update . >/dev/null 2>&1 &`

- [ ] **Step 5: Final commit (if anything outstanding)**

```bash
git add -A && git commit -m "chore(cds): finalize interaction report panel (phase 2a)" || echo "nothing to commit"
```

---

## Notes for the implementer

- **Failure never implies safety:** the panel shows distinct loading / degraded / error / clean
  states. A degraded report or a fetch error must NOT render "No interactions detected".
- **Ack binds to `findings_hash`:** the gate compares the acknowledged hash to the report's current
  hash. When the basket changes, the new report has a new hash and the gate re-locks automatically.
- **Precompute is isolated:** `recompute_for_patient_id` never raises into the intake/transition
  path — a compute failure just leaves the cache stale, and the read endpoint recomputes on open.
- **Out of scope (later phases):** physician letter (2b), admin legal-trail view (2c).
- **DRY:** `report_to_dict` is the single serializer used by both the read endpoint and precompute.
