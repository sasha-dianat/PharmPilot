# Interaction Audit / Legal Trail Implementation Plan (Phase 2c)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only admin "Interaction Audit" view — a unified, filterable, tenant-scoped chronological feed of every interaction acknowledgment and physician letter, with verbatim-letter drill-down.

**Architecture:** One `GET /cds/interaction-audit` endpoint queries two sources (acknowledgments from `ClinicalAuditLog`, letters from `physician_letters` joined to `patients`), normalizes each to a common record via pure helpers, merges/sorts/paginates. A new `InteractionAuditView` dashboard renders a filter bar + table + letter drill-down that reuses the existing letter endpoint.

**Tech Stack:** Python 3.12, FastAPI, async SQLAlchemy (JSONB operators), pytest; React 19 + react-query. Run python via `/Users/sashad85/miniforge3/bin/python`.

**Spec:** `docs/superpowers/specs/2026-06-25-interaction-audit-design.md`

---

## File structure

```
services/platform/routers/cds.py    # + _normalize_ack, _normalize_letter, GET /interaction-audit
frontend/workstation/src/lib/api.ts # + clinicalApi.getInteractionAudit, clinicalApi.getPhysicianLetter
frontend/workstation/src/dashboards/InteractionAuditView.tsx   # NEW dashboard
frontend/workstation/src/DashboardShell.tsx                    # register the dashboard
tests/unit/test_interaction_audit.py
```

---

## Task 1: Normalization helpers (pure)

**Files:**
- Modify: `services/platform/routers/cds.py`
- Test: `tests/unit/test_interaction_audit.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_interaction_audit.py
from types import SimpleNamespace as NS
from datetime import datetime, timezone
from uuid import uuid4

from services.platform.routers import cds


def _dt(): return datetime(2026, 6, 25, 10, 0, tzinfo=timezone.utc)


def test_normalize_ack_reads_snapshot():
    row = NS(id=uuid4(), user_id=uuid4(), patient_id=uuid4(), created_at=_dt(),
             input_snapshot={"pharmacist": {"id": "p", "name": "Pat", "license": "L9"},
                             "physician": {"name": "Dr Who", "medical_council_id": "NP-7"},
                             "findings": [{"severity": "Contraindicated"}, {"severity": "Major"}]},
             output_snapshot={"findings_hash": "h"})
    r = cds._normalize_ack(row, "Ali Karimi")
    assert r["type"] == "ack" and r["pharmacist"]["name"] == "Pat"
    assert r["physician"]["medical_council_id"] == "NP-7"
    assert r["patient"]["name"] == "Ali Karimi"
    assert set(r["severities"]) == {"Contraindicated", "Major"}
    assert r["letter_id"] is None


def test_normalize_letter_reads_columns():
    lid = uuid4()
    row = NS(id=lid, pharmacist_id=uuid4(), pharmacist_name="Pat", pharmacist_license="L9",
             patient_id=uuid4(), prescriber_name="Dr Who", prescriber_council_id="NP-7",
             language="fa", source="deterministic", content_hash="abc", created_at=_dt())
    r = cds._normalize_letter(row, "Ali Karimi")
    assert r["type"] == "letter" and r["letter_id"] == str(lid)
    assert r["physician"]["council_id"] == "NP-7" and r["language"] == "fa"
    assert r["content_hash"] == "abc" and r["patient"]["name"] == "Ali Karimi"
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_audit.py -q`
Expected: FAIL — `_normalize_ack` missing

- [ ] **Step 3: Add the helpers** to `cds.py` (near the other interaction helpers)

```python
def _normalize_ack(row, patient_name: str | None) -> dict:
    snap = row.input_snapshot or {}
    severities = sorted({f.get("severity") for f in (snap.get("findings") or []) if f.get("severity")})
    return {
        "id": str(row.id), "type": "ack", "at": row.created_at.isoformat(),
        "pharmacist": snap.get("pharmacist") or {"id": str(row.user_id) if row.user_id else None},
        "patient": {"id": str(row.patient_id) if row.patient_id else None, "name": patient_name},
        "physician": snap.get("physician") or {},
        "severities": severities,
        "letter_id": None, "language": None, "source": None, "content_hash": None,
    }


def _normalize_letter(row, patient_name: str | None) -> dict:
    return {
        "id": str(row.id), "type": "letter", "at": row.created_at.isoformat(),
        "pharmacist": {"id": str(row.pharmacist_id), "name": row.pharmacist_name,
                       "license": row.pharmacist_license},
        "patient": {"id": str(row.patient_id), "name": patient_name},
        "physician": {"name": row.prescriber_name, "council_id": row.prescriber_council_id},
        "severities": [],
        "letter_id": str(row.id), "language": row.language, "source": row.source,
        "content_hash": row.content_hash,
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_audit.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add services/platform/routers/cds.py tests/unit/test_interaction_audit.py
git commit -m "feat(cds): interaction-audit record normalizers (ack + letter)"
```

---

## Task 2: The audit endpoint

**Files:**
- Modify: `services/platform/routers/cds.py`
- Test: `tests/unit/test_interaction_audit.py`

- [ ] **Step 1: Write the failing test (append)**

```python
import asyncio


class _Result:
    def __init__(self, rows): self._rows = rows
    def all(self): return self._rows


class _DB:
    """Returns the given result row-lists in the order the endpoint issues its
    queries. Pass one list per expected execute() call. Each row is a tuple
    (orm_row, first_name, last_name) like a joined select."""
    def __init__(self, *results): self._seq = [_Result(r) for r in results]
    async def execute(self, *_a, **_k): return self._seq.pop(0) if self._seq else _Result([])


def _ack_row(hour, sev):
    return NS(id=uuid4(), user_id=uuid4(), patient_id=uuid4(),
              created_at=datetime(2026, 6, 25, hour, tzinfo=timezone.utc),
              input_snapshot={"pharmacist": {"name": "Pat"}, "physician": {},
                              "findings": [{"severity": sev}]}, output_snapshot={})


def _letter_row(hour):
    return NS(id=uuid4(), pharmacist_id=uuid4(), pharmacist_name="Pat", pharmacist_license="L9",
              patient_id=uuid4(), prescriber_name="Dr Who", prescriber_council_id="NP-7",
              language="fa", source="deterministic", content_hash="abc",
              created_at=datetime(2026, 6, 25, hour, tzinfo=timezone.utc))


def test_interaction_audit_merges_and_sorts():
    # type=None → endpoint runs ack query then letter query (two execute calls).
    db = _DB([(_ack_row(9, "Major"), "Ali", "Karimi")], [(_letter_row(11), "Ali", "Karimi")])
    staff = NS(id=uuid4(), pharmacy_id=uuid4())
    out = asyncio.run(cds.interaction_audit(
        patient_id=None, patient_name=None, council_id=None, from_=None, to=None,
        type=None, limit=50, offset=0, staff=staff, db=db))
    assert out["count"] == 2
    # newest first → the 11:00 letter precedes the 09:00 ack
    assert out["records"][0]["type"] == "letter"
    assert out["records"][1]["type"] == "ack"


def test_interaction_audit_type_filter_letter_only():
    # type=letter → endpoint runs ONLY the letter query (one execute call).
    db = _DB([(_letter_row(11), "Ali", "Karimi")])
    staff = NS(id=uuid4(), pharmacy_id=uuid4())
    out = asyncio.run(cds.interaction_audit(
        patient_id=None, patient_name=None, council_id=None, from_=None, to=None,
        type="letter", limit=50, offset=0, staff=staff, db=db))
    assert out["count"] == 1 and out["records"][0]["type"] == "letter"
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_audit.py -q`
Expected: FAIL — `interaction_audit` missing

- [ ] **Step 3: Add the endpoint** to `cds.py`

```python
from datetime import time as _time

from fastapi import Query
from sqlalchemy import func

from shared.models.clinical import PhysicianLetter  # (already imported in cds.py from Phase 2b)


@router.get("/interaction-audit")
async def interaction_audit(
    patient_id: UUID | None = None,
    patient_name: str | None = None,
    council_id: str | None = None,
    from_: date | None = Query(None, alias="from"),
    to: date | None = None,
    type: str | None = None,
    limit: int = 50,
    offset: int = 0,
    staff: Staff = Depends(require_permission("reports:read")),
    db: AsyncSession = Depends(get_db),
):
    cap = offset + limit
    records: list[dict] = []

    if type in (None, "ack"):
        q = (select(ClinicalAuditLog, Patient.first_name, Patient.last_name)
             .join(Patient, Patient.id == ClinicalAuditLog.patient_id)
             .where(ClinicalAuditLog.module == "interaction_acknowledgment",
                    Patient.pharmacy_id == staff.pharmacy_id))
        if patient_id:
            q = q.where(ClinicalAuditLog.patient_id == patient_id)
        if patient_name:
            q = q.where(ClinicalAuditLog.input_snapshot["patient"]["name"].astext.ilike(f"%{patient_name}%"))
        if council_id:
            q = q.where(ClinicalAuditLog.input_snapshot["physician"]["medical_council_id"].astext.ilike(f"%{council_id}%"))
        if from_:
            q = q.where(ClinicalAuditLog.created_at >= datetime.combine(from_, _time.min))
        if to:
            q = q.where(ClinicalAuditLog.created_at <= datetime.combine(to, _time.max))
        q = q.order_by(ClinicalAuditLog.created_at.desc()).limit(cap)
        for row, fn, ln in (await db.execute(q)).all():
            records.append(_normalize_ack(row, f"{fn} {ln}".strip()))

    if type in (None, "letter"):
        q = (select(PhysicianLetter, Patient.first_name, Patient.last_name)
             .join(Patient, Patient.id == PhysicianLetter.patient_id, isouter=True)
             .where(PhysicianLetter.pharmacy_id == staff.pharmacy_id))
        if patient_id:
            q = q.where(PhysicianLetter.patient_id == patient_id)
        if patient_name:
            q = q.where(func.concat(Patient.first_name, " ", Patient.last_name).ilike(f"%{patient_name}%"))
        if council_id:
            q = q.where(PhysicianLetter.prescriber_council_id.ilike(f"%{council_id}%"))
        if from_:
            q = q.where(PhysicianLetter.created_at >= datetime.combine(from_, _time.min))
        if to:
            q = q.where(PhysicianLetter.created_at <= datetime.combine(to, _time.max))
        q = q.order_by(PhysicianLetter.created_at.desc()).limit(cap)
        for row, fn, ln in (await db.execute(q)).all():
            name = f"{fn or ''} {ln or ''}".strip() or None
            records.append(_normalize_letter(row, name))

    records.sort(key=lambda r: r["at"], reverse=True)
    return {"records": records[offset:offset + limit], "count": len(records)}
```

- [ ] **Step 4: Run to verify pass + routes register + app import**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_audit.py -q && /Users/sashad85/miniforge3/bin/python -c "import services.platform.main; import services.platform.routers.cds as c; print('/interaction-audit' in [r.path for r in c.router.routes])"`
Expected: PASS (4 passed) + `True`

- [ ] **Step 5: Commit**

```bash
git add services/platform/routers/cds.py tests/unit/test_interaction_audit.py
git commit -m "feat(cds): GET /cds/interaction-audit unified legal-trail feed"
```

---

## Task 3: Frontend API wrappers

**Files:**
- Modify: `frontend/workstation/src/lib/api.ts`

- [ ] **Step 1: Add methods** to `clinicalApi` (after `generatePhysicianLetter`)

```ts
  getPhysicianLetter: (id: string) => apiClient.get(`/cds/physician-letter/${id}`),
  getInteractionAudit: (params: {
    patient_name?: string; council_id?: string; from?: string; to?: string;
    type?: 'ack' | 'letter'; limit?: number; offset?: number;
  }) => apiClient.get('/cds/interaction-audit', { params }),
```

- [ ] **Step 2: Type-check**

Run: `cd frontend/workstation && npx tsc --noEmit 2>&1 | grep -E "api.ts" | head; echo "exit ${PIPESTATUS[0]}"`
Expected: no errors (exit 0)

- [ ] **Step 3: Commit**

```bash
git add frontend/workstation/src/lib/api.ts
git commit -m "feat(web): interaction-audit + physician-letter GET API wrappers"
```

---

## Task 4: InteractionAuditView dashboard + register

**Files:**
- Create: `frontend/workstation/src/dashboards/InteractionAuditView.tsx`
- Modify: `frontend/workstation/src/DashboardShell.tsx`

- [ ] **Step 1: Create the dashboard**

```tsx
// frontend/workstation/src/dashboards/InteractionAuditView.tsx
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { clinicalApi } from '../lib/api'

interface AuditRecord {
  id: string; type: 'ack' | 'letter'; at: string
  pharmacist: { name?: string; license?: string | null }
  patient: { id?: string | null; name?: string | null }
  physician: { name?: string | null; council_id?: string | null; medical_council_id?: string | null }
  severities: string[]
  letter_id: string | null; language: string | null; source: string | null; content_hash: string | null
}

export default function InteractionAuditView() {
  const [filters, setFilters] = useState<{ patient_name: string; council_id: string; from: string; to: string; type: '' | 'ack' | 'letter' }>(
    { patient_name: '', council_id: '', from: '', to: '', type: '' })
  const [applied, setApplied] = useState(filters)
  const [offset, setOffset] = useState(0)
  const [letter, setLetter] = useState<{ html: string; hash: string } | null>(null)

  const { data, isLoading } = useQuery<{ records: AuditRecord[]; count: number }>({
    queryKey: ['interaction-audit', applied, offset],
    queryFn: () => clinicalApi.getInteractionAudit({
      ...(applied.patient_name ? { patient_name: applied.patient_name } : {}),
      ...(applied.council_id ? { council_id: applied.council_id } : {}),
      ...(applied.from ? { from: applied.from } : {}),
      ...(applied.to ? { to: applied.to } : {}),
      ...(applied.type ? { type: applied.type } : {}),
      limit: 50, offset,
    }).then(r => r.data),
  })

  const viewLetter = async (id: string) => {
    const { data } = await clinicalApi.getPhysicianLetter(id)
    setLetter({ html: data.letter_html, hash: data.content_hash })
  }
  const printLetter = () => {
    if (!letter) return
    const w = window.open('', '_blank'); if (!w) return
    w.document.write(letter.html); w.document.close(); w.focus(); w.print()
  }

  const input = 'text-sm bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100'
  const records = data?.records ?? []

  return (
    <div className="p-4 space-y-4 text-slate-100">
      <h2 className="text-lg font-bold">Interaction Audit · Legal Trail</h2>

      <div className="flex flex-wrap gap-2 items-end">
        <input className={input} placeholder="Patient name" value={filters.patient_name}
          onChange={e => setFilters({ ...filters, patient_name: e.target.value })} />
        <input className={input} placeholder="Council ID" value={filters.council_id}
          onChange={e => setFilters({ ...filters, council_id: e.target.value })} />
        <input className={input} type="date" value={filters.from}
          onChange={e => setFilters({ ...filters, from: e.target.value })} />
        <input className={input} type="date" value={filters.to}
          onChange={e => setFilters({ ...filters, to: e.target.value })} />
        <select className={input} value={filters.type}
          onChange={e => setFilters({ ...filters, type: e.target.value as '' | 'ack' | 'letter' })}>
          <option value="">All</option><option value="ack">Acknowledgments</option><option value="letter">Letters</option>
        </select>
        <button onClick={() => { setOffset(0); setApplied(filters) }}
          className="px-4 py-2 bg-indigo-600 text-white text-sm rounded-lg">Apply</button>
        <button onClick={() => { const f = { patient_name: '', council_id: '', from: '', to: '', type: '' as const }; setFilters(f); setApplied(f); setOffset(0) }}
          className="px-4 py-2 bg-slate-700 text-white text-sm rounded-lg">Clear</button>
      </div>

      {isLoading && <p className="text-slate-400 text-sm">Loading…</p>}
      {!isLoading && records.length === 0 && <p className="text-slate-400 text-sm">No records match these filters.</p>}

      {records.length > 0 && (
        <table className="w-full text-sm">
          <thead className="text-slate-400 text-left">
            <tr><th className="py-1">Time</th><th>Type</th><th>Pharmacist</th><th>Patient</th><th>Physician</th><th>Severity</th><th></th></tr>
          </thead>
          <tbody>
            {records.map(r => (
              <tr key={r.type + r.id} className="border-t border-slate-800">
                <td className="py-1.5">{new Date(r.at).toLocaleString()}</td>
                <td><span className={`text-[11px] px-2 py-0.5 rounded ${r.type === 'letter' ? 'bg-red-500/20 text-red-300' : 'bg-amber-500/20 text-amber-300'}`}>{r.type === 'letter' ? 'Letter' : 'Ack'}</span></td>
                <td>{r.pharmacist?.name ?? '—'}</td>
                <td>{r.patient?.name ?? '—'}</td>
                <td>{(r.physician?.name ?? '—')}{(r.physician?.council_id || r.physician?.medical_council_id) ? ` · ${r.physician.council_id || r.physician.medical_council_id}` : ''}</td>
                <td>{r.severities.join(', ') || '—'}</td>
                <td>{r.letter_id && <button onClick={() => viewLetter(r.letter_id!)} className="text-indigo-400 text-xs underline">View letter</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="flex gap-2">
        <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}
          className="px-3 py-1 bg-slate-700 rounded text-sm disabled:opacity-40">Prev</button>
        <button disabled={records.length < 50} onClick={() => setOffset(offset + 50)}
          className="px-3 py-1 bg-slate-700 rounded text-sm disabled:opacity-40">Next</button>
      </div>

      {letter && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4" onClick={() => setLetter(null)}>
          <div className="bg-slate-900 max-w-2xl w-full p-4 rounded-lg space-y-2" onClick={e => e.stopPropagation()}>
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-bold">Physician letter</h3>
              <span className="text-[11px] text-slate-500">hash {letter.hash.slice(0, 12)}…</span>
              <button onClick={printLetter} className="ml-auto px-3 py-1 bg-emerald-600 text-white text-xs rounded">Print</button>
              <button onClick={() => setLetter(null)} className="text-slate-400">✕</button>
            </div>
            <iframe title="letter" srcDoc={letter.html} className="w-full h-[60vh] bg-white rounded" />
          </div>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Register in `DashboardShell.tsx`.** Read the file first. Add the import near the other
dashboard imports:

```ts
import InteractionAuditView from './dashboards/InteractionAuditView'
```

Add a `SECTIONS` entry (in the `SECTIONS` array, after the `knowledge`/`depot` entries):

```ts
  { id:'interaction-audit', key:'a', label:'Interaction Audit', icon:ClipboardCheck, description:'Acknowledgment & letter legal trail', shortcut:'Alt+A' },
```

(`ClipboardCheck` is already imported in DashboardShell — it's used by the `adr` section. Reuse it.)

Add to the component map (the object that maps id→component, where `'second-brain': SecondBrainChat` lives):

```ts
  'interaction-audit': InteractionAuditView,
```

If the shell gates any sections by role (look for `userRole`), gate `interaction-audit` to admins/managers
by only including it when `['super_admin','pharmacy_manager'].includes(userRole)`. If there is no existing
per-section role gate, leave it visible to all — the endpoint enforces `reports:read`, so unauthorized
users simply get an empty/errored feed.

- [ ] **Step 3: Type-check**

Run: `cd frontend/workstation && npx tsc --noEmit 2>&1 | grep -E "InteractionAuditView|DashboardShell" | head; echo "exit ${PIPESTATUS[0]}"`
Expected: no errors (exit 0)

- [ ] **Step 4: Commit**

```bash
git add frontend/workstation/src/dashboards/InteractionAuditView.tsx frontend/workstation/src/DashboardShell.tsx
git commit -m "feat(web): Interaction Audit legal-trail dashboard"
```

---

## Task 5: End-to-end verification

- [ ] **Step 1: Backend suite**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_audit.py tests/unit/test_physician_letter*.py tests/unit/test_interaction_*.py -q`
Expected: all pass

- [ ] **Step 2: Full frontend type-check**

Run: `cd frontend/workstation && npx tsc --noEmit; echo "exit $?"`
Expected: exit 0

- [ ] **Step 3: Browser smoke** (preview). Restart the API (to load the new endpoint), open the
workstation, log in as `pharmacist`/`Pharmacist2024!`, open Dashboards → "Interaction Audit". Confirm
the feed loads (it will show the acknowledgment/letter rows created during earlier testing, e.g. the
physician letter generated in Phase 2b), the filters narrow results, and "View letter" opens the
verbatim Persian letter with a working Print. Capture a screenshot.
(Restart API: `lsof -ti:8001 | xargs kill 2>/dev/null` then wait for it to come back on :8001.)

- [ ] **Step 4: Refresh graphify + final commit**

```bash
graphify update . >/dev/null 2>&1 &
git add -A && git commit -m "chore(cds): finalize interaction audit (phase 2c)" || echo "nothing to commit"
```

---

## Notes for the implementer

- **Read-only.** No mutation of audit rows. The endpoint only SELECTs.
- **Tenant scope is mandatory** on both queries (acks via the `patients` join, letters via
  `pharmacy_id`) — never return another pharmacy's records.
- **Bounded fetch:** each source is `LIMIT offset+limit`, merged in Python, then sliced — correct for
  pharmacy-scale volumes; a SQL UNION pagination is a future optimization, not needed now.
- **`from` is a reserved word** — the query param uses `alias="from"` and the Python arg is `from_`.
- The `physician.council_id` (letters) vs `physician.medical_council_id` (acks, from the snapshot) key
  difference is handled in the frontend by reading either.
- **Out of scope:** CSV/PDF export, closed-loop sign-back.
```
