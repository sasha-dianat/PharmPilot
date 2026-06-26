# Interaction Bundle Admin Installer Implementation Plan (#3c)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an admin upload, validate, and atomically install a Colab-produced interaction bundle (`.sqlite`) — with a replace-confirmation guardrail — then hot-reload the engine.

**Architecture:** `bundle.py` gains `validate_bundle_file` + `install_bundle` (atomic `os.replace`). Two `cds.py` endpoints stream the upload into the bundle's own dir, validate, guard against accidental replace (409 unless `confirm_replace`), install, and `reload_indexes()`. A small admin dashboard shows status + installs.

**Tech Stack:** Python 3.12 (sqlite3, os, tempfile), FastAPI (UploadFile/Form), pytest; React 19 + react-query. Run python via `/Users/sashad85/miniforge3/bin/python`.

**Spec:** `docs/superpowers/specs/2026-06-25-interaction-bundle-installer-design.md`

---

## File structure

```
services/ai/clinical_decision_support/interaction/bundle.py   # + validate_bundle_file, install_bundle
services/platform/routers/cds.py                              # + install/status endpoints
frontend/workstation/src/lib/api.ts                           # + getBundleStatus, installBundle
frontend/workstation/src/dashboards/InteractionBundleAdmin.tsx # NEW dashboard
frontend/workstation/src/DashboardShell.tsx                   # register dashboard
tests/unit/test_interaction_bundle_installer.py
```

---

## Task 1: `validate_bundle_file` + `install_bundle`

**Files:**
- Modify: `services/ai/clinical_decision_support/interaction/bundle.py`
- Test: `tests/unit/test_interaction_bundle_installer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_interaction_bundle_installer.py
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
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_bundle_installer.py -q`
Expected: FAIL — `validate_bundle_file` / `install_bundle` missing

- [ ] **Step 3: Add to `bundle.py`.** Add `import os` at the top (with the other imports), then append:

```python
_REQUIRED_TABLES = ("interaction_rules", "drug_attributes", "bundle_meta")


def validate_bundle_file(path: Path) -> tuple[bool, str, dict]:
    """(ok, reason, stats). Never raises."""
    if not path.exists():
        return False, "file not found", {}
    con = None
    try:
        con = _open(path)
    except sqlite3.Error:
        return False, "not a valid SQLite database", {}
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for req in _REQUIRED_TABLES:
            if req not in tables:
                return False, f"missing table: {req}", {}
        row = con.execute("SELECT value FROM bundle_meta WHERE key='schema_version'").fetchone()
        got = row[0] if row else None
        if got != SCHEMA_VERSION:
            return False, f"schema version mismatch (got {got}, expected {SCHEMA_VERSION})", {}
        stats = {k: v for k, v in con.execute("SELECT key, value FROM bundle_meta")}
        return True, "ok", stats
    except sqlite3.Error as exc:
        return False, f"unreadable bundle: {exc}", {}
    finally:
        if con is not None:
            con.close()


def install_bundle(temp_path: Path) -> None:
    """Atomically move a validated bundle into place (same-fs os.replace)."""
    BUNDLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    os.replace(temp_path, BUNDLE_PATH)
```

- [ ] **Step 4: Run to verify pass**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_bundle_installer.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add services/ai/clinical_decision_support/interaction/bundle.py tests/unit/test_interaction_bundle_installer.py
git commit -m "feat(cds): bundle validate_bundle_file + atomic install_bundle"
```

---

## Task 2: Install + status endpoints

**Files:**
- Modify: `services/platform/routers/cds.py`
- Test: `tests/unit/test_interaction_bundle_installer.py`

- [ ] **Step 1: Write the failing test (append)**

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_bundle_installer.py -q`
Expected: FAIL — `install_interaction_bundle` / `interaction_bundle_status` missing

- [ ] **Step 3: Add the endpoints to `cds.py`.** Add imports at the top (with the others):

```python
import os
import tempfile

from fastapi import File, Form, UploadFile

from services.ai.clinical_decision_support.interaction import bundle as ix_bundle
from services.ai.clinical_decision_support.interaction.engine import reload_indexes
```

Append the endpoints:

```python
async def _stream_to_bundle_dir(file) -> "Path":
    """Stream an upload to a temp file inside the bundle's own directory, so the
    later os.replace into BUNDLE_PATH is a same-filesystem atomic move."""
    from pathlib import Path
    ix_bundle.BUNDLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=ix_bundle.BUNDLE_PATH.parent, suffix=".sqlite.tmp")
    with os.fdopen(fd, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            out.write(chunk)
    return Path(name)


@router.post("/interaction-bundle/install")
async def install_interaction_bundle(
    file: UploadFile = File(...),
    confirm_replace: bool = Form(False),
    staff: Staff = Depends(require_permission("clinical:write")),
):
    tmp = await _stream_to_bundle_dir(file)
    try:
        if ix_bundle.bundle_stats() and not confirm_replace:
            raise HTTPException(status_code=409, detail="a bundle is already installed; confirm replacement")
        ok, reason, _stats = ix_bundle.validate_bundle_file(tmp)
        if not ok:
            raise HTTPException(status_code=422, detail=reason)
        ix_bundle.install_bundle(tmp)         # moves tmp → BUNDLE_PATH
        return {"installed": True, "stats": reload_indexes()}
    finally:
        if tmp.exists():                      # not installed (install moved it on success)
            os.unlink(tmp)


@router.get("/interaction-bundle/status")
async def interaction_bundle_status(
    staff: Staff = Depends(require_permission("clinical:read")),
):
    stats = ix_bundle.bundle_stats()
    return {"installed": bool(stats), "stats": stats}
```

- [ ] **Step 4: Run to verify pass + routes register**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_bundle_installer.py -q && /Users/sashad85/miniforge3/bin/python -c "import services.platform.main; import services.platform.routers.cds as c; print(sorted(p for p in [r.path for r in c.router.routes] if 'interaction-bundle' in p))"`
Expected: PASS (9 passed) + `['/interaction-bundle/install', '/interaction-bundle/status']`

- [ ] **Step 5: Full interaction suite (no regressions)**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_*.py -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add services/platform/routers/cds.py tests/unit/test_interaction_bundle_installer.py
git commit -m "feat(cds): interaction-bundle install (confirm-replace + validate) + status endpoints"
```

---

## Task 3: Frontend — API + admin dashboard

**Files:**
- Modify: `frontend/workstation/src/lib/api.ts`
- Create: `frontend/workstation/src/dashboards/InteractionBundleAdmin.tsx`
- Modify: `frontend/workstation/src/DashboardShell.tsx`

- [ ] **Step 1: Add API methods** to `clinicalApi` in `api.ts` (after `getInteractionAudit`)

```ts
  getBundleStatus: () => apiClient.get('/cds/interaction-bundle/status'),
  installBundle: (file: File, confirmReplace = false) => {
    const form = new FormData()
    form.append('file', file)
    form.append('confirm_replace', String(confirmReplace))
    return apiClient.post('/cds/interaction-bundle/install', form,
      { headers: { 'Content-Type': 'multipart/form-data' } })
  },
```

- [ ] **Step 2: Create the dashboard**

```tsx
// frontend/workstation/src/dashboards/InteractionBundleAdmin.tsx
import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { clinicalApi } from '../lib/api'

interface Status { installed: boolean; stats: Record<string, string> }

export default function InteractionBundleAdmin() {
  const qc = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  const { data } = useQuery<Status>({
    queryKey: ['bundle-status'],
    queryFn: () => clinicalApi.getBundleStatus().then(r => r.data),
  })
  const stats = data?.stats ?? {}

  const install = async () => {
    const file = fileRef.current?.files?.[0]
    if (!file) { setMsg({ kind: 'err', text: 'Pick a .sqlite bundle first.' }); return }
    if (data?.installed &&
        !window.confirm(`This replaces the current bundle (${stats.schema_version ?? '?'}). Continue?`)) return
    setBusy(true); setMsg(null)
    try {
      const { data: res } = await clinicalApi.installBundle(file, data?.installed ?? false)
      setMsg({ kind: 'ok', text: `Installed: ${res.stats.rule_count ?? '?'} rules, ${res.stats.attribute_count ?? '?'} attributes.` })
      qc.invalidateQueries({ queryKey: ['bundle-status'] })
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setMsg({ kind: 'err', text: detail || 'Install failed.' })
    } finally { setBusy(false) }
  }

  return (
    <div className="p-4 space-y-4 text-slate-100">
      <h2 className="text-lg font-bold">Interaction Knowledge Bundle</h2>

      <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 space-y-1 text-sm">
        <p className="font-semibold mb-1">Current bundle</p>
        {data?.installed ? (
          <>
            <p>Schema: {stats.schema_version}</p>
            <p>Datasets: {stats.datasets ?? '—'}</p>
            <p>Rules: {stats.rule_count ?? '—'} · Attributes: {stats.attribute_count ?? '—'}</p>
            <p>Built: {stats.built_at ?? '—'}</p>
            {stats.checksum && <p className="text-slate-500">checksum {stats.checksum.slice(0, 12)}…</p>}
          </>
        ) : (
          <p className="text-slate-400">No bundle installed — the engine is running on curated rules only.</p>
        )}
      </div>

      <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 space-y-3">
        <p className="font-semibold text-sm">Install a bundle (.sqlite from the Colab ingestion notebook)</p>
        <input ref={fileRef} type="file" accept=".sqlite,.db,.sqlite3"
          className="text-sm text-slate-300" />
        <button onClick={install} disabled={busy}
          className="block px-4 py-2 bg-indigo-600 text-white text-sm rounded-lg disabled:opacity-50">
          {busy ? 'Installing…' : 'Install bundle'}
        </button>
        {msg && <p className={`text-sm ${msg.kind === 'ok' ? 'text-emerald-400' : 'text-red-400'}`}>{msg.text}</p>}
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Register in `DashboardShell.tsx`.** Read the file. Add the import near the other
dashboard imports:

```ts
import InteractionBundleAdmin from './dashboards/InteractionBundleAdmin'
```

Add a `SECTIONS` entry (after the `interaction-audit` entry added in #2c):

```ts
  { id:'interaction-bundle', key:'b', label:'Interaction Bundle', icon:Database, description:'Install the DDI knowledge bundle', shortcut:'Alt+B' },
```

(`Database` is a lucide-react icon — confirm it's imported at the top of DashboardShell; if not, add
`Database` to the existing `import { … } from 'lucide-react'` line.)

Add to the component map (where `'interaction-audit': InteractionAuditView` lives):

```ts
  'interaction-bundle': InteractionBundleAdmin,
```

- [ ] **Step 4: Type-check**

Run: `cd frontend/workstation && npx tsc --noEmit 2>&1 | grep -E "InteractionBundleAdmin|DashboardShell|api.ts" | head; echo "exit ${PIPESTATUS[0]}"`
Expected: no errors (exit 0)

- [ ] **Step 5: Commit**

```bash
git add frontend/workstation/src/lib/api.ts \
        frontend/workstation/src/dashboards/InteractionBundleAdmin.tsx \
        frontend/workstation/src/DashboardShell.tsx
git commit -m "feat(web): interaction bundle admin installer dashboard"
```

---

## Task 4: End-to-end verification

- [ ] **Step 1: Full backend suite**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_*.py tests/unit/test_cds_engine.py -q`
Expected: all pass

- [ ] **Step 2: Full frontend type-check**

Run: `cd frontend/workstation && npx tsc --noEmit; echo "exit $?"`
Expected: exit 0

- [ ] **Step 3: Live API smoke** (restart the API, then with an auth token):
  - `GET /cds/interaction-bundle/status` → `{installed:false,...}` (no bundle installed normally).
  - Build a tiny valid bundle locally, `POST` it (multipart) with `confirm_replace=false` → installed:true + stats.
  - `POST` again without confirm → 409. With `confirm_replace=true` → installed.
  - `POST` a garbage file → 422.
  Then remove the installed test bundle file so the engine returns to curated-only.

- [ ] **Step 4: Browser smoke** (preview). Open Dashboards → "Interaction Bundle", confirm the status
  card renders ("No bundle installed…"), pick a `.sqlite`, Install, see the success note + the status
  refresh. Capture a screenshot.

- [ ] **Step 5: Refresh graphify + final commit**

```bash
graphify update . >/dev/null 2>&1 &
git add -A && git commit -m "chore(cds): finalize interaction bundle installer (#3c)" || echo "nothing to commit"
```

---

## Notes for the implementer

- **Reference `bundle.BUNDLE_PATH` via the module** (`ix_bundle.BUNDLE_PATH`), never a value import —
  so tests can monkeypatch it and the same-dir temp/replace stays correct.
- **Same-fs atomic install:** the upload streams to a temp file *inside the bundle's directory*, so
  `os.replace` is atomic. Don't stream to the system temp dir.
- **Validate-before-install + 409 guardrail:** a bad upload (422) or an unconfirmed replace (409) must
  never move a file onto `BUNDLE_PATH`; the `finally` unlinks the leftover temp.
- **Cache discipline in tests:** every test clears both loader caches after monkeypatching
  `bundle.BUNDLE_PATH` and again at the end (the `_reset` helper) — caches are process-global.
- **Out of scope:** the Colab notebook (#3b) that produces the bundle.
```
