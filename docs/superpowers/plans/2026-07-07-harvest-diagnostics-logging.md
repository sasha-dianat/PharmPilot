# Harvest Diagnostics Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Model directive (owner):** implementation executed by **Opus 4.8** subagents.

**Goal:** Make every دارونامه/NFI fetch attempt self-explaining — capture status, headers, a body snippet, timing, the real transport exception, and a classified category+hint — persisted to the DB, a JSONL file, and the GUI, so a failed harvest behind the Iran proxy can be diagnosed offline.

**Architecture:** A shared, stdlib-only `harvest_diagnostics` module provides a pure `classify()` taxonomy, a `DiagnosticRecorder` (writes JSONL + summary, bounds memory), and `wrap_fetch()` that times/records each call while preserving the callers' `(status, body, ct)` contract. The two crawlers get a **raising** raw-fetch primitive that keeps the HTTP-error body (the actual bug fix), wrapped with a recorder. Coverage persists `recorder.to_db()` onto `CoverageRun.diagnostics`; NFI surfaces `recorder.summary()` in its status.

**Tech Stack:** Python stdlib (urllib, logging, dataclasses), SQLAlchemy async + Alembic (Postgres 127.0.0.1:5433), FastAPI, React 19 + Vite + Tailwind (RTL), pytest (`python3 -m pytest` — pytest is the miniforge python3, NOT `.venv`).

**Spec:** `docs/superpowers/specs/2026-07-07-harvest-diagnostics-logging-design.md`

**Conventions:** Backend on :8001 (no `--reload`; restart to load backend changes). Dev DB `postgresql+asyncpg://pharmpilot:pharmpilot_dev@127.0.0.1:5433/pharmpilot`. Migrations in `data/migrations/versions/` (head `0018`). `coverage`+`drug_catalog` are registered in `shared/models/__init__.py`, so `coverage_runs` IS parity-checked — model and migration must match. Commit after every green step with a `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer. Branch: `feat/darunameh-crawler` (this stacks on the coverage crawler).

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `services/core/drug_catalog/harvest_diagnostics.py` | Create | `classify`, `FetchAttempt`, `DiagnosticRecorder`, `wrap_fetch` |
| `tests/unit/test_harvest_diagnostics.py` | Create | pure tests for all of the above |
| `services/core/drug_catalog/coverage_harvest.py` | Modify | raising `make_raw_fetch`; `make_fetcher(recorder=)`; recorder in `_run` |
| `services/core/drug_catalog/nfi.py` | Modify | raising `fetch_detail_raw` |
| `services/core/drug_catalog/nfi_harvest_service.py` | Modify | recorder (errors_only) in `_run`; `diagnostics` in state |
| `shared/models/coverage.py` | Modify | `CoverageRun.diagnostics` JSONB |
| `data/migrations/versions/0019_coverage_run_diagnostics.py` | Create | add the column |
| `services/platform/routers/pricing.py` | Modify | `diag_summary`/`diagnostics` in run endpoints; probe failure detail |
| `frontend/workstation/src/dashboards/CoverageAdmin.tsx` | Modify | diagnostics expander + probe-fail rendering |
| `frontend/workstation/src/dashboards/DrugCatalogAdmin.tsx` | Modify | worst-category in the harvest strip |

---

### Task 1: `harvest_diagnostics.py` — classify + recorder + wrap_fetch (TDD, pure)

**Files:** Create `services/core/drug_catalog/harvest_diagnostics.py`, `tests/unit/test_harvest_diagnostics.py`

- [ ] **Step 1.1: Write the failing tests**

Create `tests/unit/test_harvest_diagnostics.py`:

```python
"""Harvest diagnostics — deterministic fetch-failure classification, a recorder
that streams JSONL + bounds memory, and a fetch wrapper that preserves the
(status, body, ct) contract while capturing every attempt."""
import json
from pathlib import Path

from services.core.drug_catalog.harvest_diagnostics import (
    classify, DiagnosticRecorder, wrap_fetch, FetchAttempt,
)


# ── classify (category, severity, hint) ──────────────────────────────────────
def test_classify_proxy_exception():
    cat, sev, hint = classify(0, {}, b"", "ProxyError('Cannot connect to proxy')")
    assert cat == "proxy_unreachable" and sev == "error" and hint

def test_classify_dns_exception():
    assert classify(0, {}, b"", "gaierror(-2, 'Name or service not known')")[0] == "dns_fail"

def test_classify_tls_exception():
    assert classify(0, {}, b"", "SSLCertVerificationError")[0] == "tls_fail"

def test_classify_timeout_exception():
    assert classify(0, {}, b"", "The read operation timed out")[0] == "timeout"

def test_classify_transport_error_generic():
    assert classify(0, {}, b"", "URLError(ConnectionResetError())")[0] in ("transport_error", "proxy_unreachable")

def test_classify_403_waf_vs_plain():
    assert classify(403, {}, b"<html>Attention Required! | Cloudflare</html>", None)[0] == "http_403_waf"
    assert classify(403, {}, b"<html>nope</html>", None)[0] == "http_403"

def test_classify_404_and_429_and_5xx():
    assert classify(404, {}, b"", None)[0] == "http_404"
    assert classify(429, {}, b"", None)[0] == "rate_limited"
    assert classify(503, {}, b"", None)[0] == "server_error"

def test_classify_login_redirect():
    assert classify(302, {"Location": "https://x/Account/Login?ret=/nfi"}, b"", None)[0] == "login_redirect"

def test_classify_js_shell_no_table():
    body = b'<html><body><div id="root"></div><script src="/app.js"></script></body></html>'
    assert classify(200, {}, body, None)[0] == "js_shell_no_table"

def test_classify_login_wall():
    body = b'<html><form><input type="password" name="pw"></form></html>'
    assert classify(200, {}, body, None)[0] == "login_wall"

def test_classify_ok_with_table():
    assert classify(200, {}, b"<html><table><tr><td>x</td></tr></table></html>", None) == (
        "ok", "ok", classify(200, {}, b"<table>", None)[2])


# ── wrap_fetch preserves contract + records ──────────────────────────────────
def _rec(tmp_path, **kw):
    return DiagnosticRecorder("coverage", "tamin", log_dir=str(tmp_path), **kw)

def test_wrap_fetch_success_preserves_tuple_and_records(tmp_path):
    rec = _rec(tmp_path)
    raw = lambda url: (200, b"<table></table>", "text/html", {"Server": "nginx"})
    fetch = wrap_fetch(raw, rec)
    assert fetch("https://x/l") == (200, b"<table></table>", "text/html")
    assert rec.summary()["total"] == 1 and rec.summary()["ok"] == 1

def test_wrap_fetch_transport_exception_returns_zero_tuple_and_records(tmp_path):
    rec = _rec(tmp_path)
    def raw(url): raise OSError("ProxyError('refused')")
    status, body, ct = wrap_fetch(raw, rec)("https://x/l")
    assert (status, body, ct) == (0, b"", "")
    s = rec.summary()
    assert s["failed"] == 1 and s["worst_category"] == "proxy_unreachable"

def test_wrap_fetch_keeps_http_error_body(tmp_path):
    rec = _rec(tmp_path)
    raw = lambda url: (403, b"Access Denied by WAF", "text/html", {})
    wrap_fetch(raw, rec)("https://x/l")
    att = rec.to_db()["attempts"][0]
    assert att["status"] == 403 and "Access Denied" in att["body_snippet"]
    assert att["category"] == "http_403_waf"


# ── recorder file + memory bounds ────────────────────────────────────────────
def test_recorder_writes_jsonl_lines_and_summary(tmp_path):
    rec = _rec(tmp_path)
    rec.record("https://x/1", 200, {}, b"<table>", 5, None)
    rec.record("https://x/2", 404, {}, b"", 3, None)
    rec.close()
    f = Path(rec.summary()["log_file"])
    lines = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 3                          # 2 attempts + final summary
    assert lines[-1]["summary"]["failed"] == 1

def test_recorder_errors_only_mode_bounds_memory(tmp_path):
    rec = _rec(tmp_path, mode="errors_only")
    for i in range(1000):
        rec.record(f"https://x/{i}", 200, {}, b"<table>", 1, None)   # ok → not retained
    rec.record("https://x/bad", 500, {}, b"", 1, None)
    s = rec.summary()
    assert s["total"] == 1001 and s["ok"] == 1000 and s["failed"] == 1
    assert len(rec.to_db()["attempts"]) == 1        # only the error retained in memory

def test_recorder_to_db_caps_attempts(tmp_path):
    rec = _rec(tmp_path)
    for i in range(700):
        rec.record(f"https://x/{i}", 404, {}, b"", 1, None)
    assert len(rec.to_db(cap=500)["attempts"]) == 500
    assert rec.summary()["failed"] == 700           # summary counts all

def test_recorder_note_adds_synthetic_attempt(tmp_path):
    rec = _rec(tmp_path)
    rec.note("empty_result", "no rows", url="https://x/l")
    assert rec.summary()["categories"]["empty_result"] == 1
```

- [ ] **Step 1.2: Run to verify failure**

Run: `python3 -m pytest tests/unit/test_harvest_diagnostics.py -q`
Expected: FAIL — module does not exist.

- [ ] **Step 1.3: Implement `services/core/drug_catalog/harvest_diagnostics.py`**

```python
"""Smart harvest diagnostics — make every fetch attempt self-explaining.

Deterministic (no LLM): classify a fetch outcome into a category + a concrete
Persian next-step hint, record attempts to a JSONL file and an in-memory buffer
(bounded), and wrap any raw fetch so callers keep their (status, body, ct)
contract while every attempt is captured. Shared by the دارونامه and NFI crawlers.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger("pharmpilot.harvest")

SNIPPET_BYTES = 2048
DIAG_CAP_ATTEMPTS_DB = 500
DIAG_KEEP_FILES = 50

HINTS = {
    "proxy_unreachable": "پروکسی ایران در دسترس نیست یا اتصال رد شد — اتصال/اعتبار پروکسی را بررسی کنید.",
    "dns_fail": "DNS مقصد حل نشد — دامنه درست است و از پشت پروکسی قابل‌حل است؟",
    "tls_fail": "خطای TLS/گواهی — ممکن است پروکسی MITM کند یا گواهی دامنه نامعتبر باشد.",
    "timeout": "مهلت پاسخ تمام شد — سایت کند است یا پروکسی throttle می‌کند؛ delay را بالا ببرید.",
    "transport_error": "اتصال برقرار نشد — پیام استثنا را ببینید.",
    "rate_limited": "محدودیت نرخ (۴۲۹) — delay را افزایش دهید یا بعداً دوباره امتحان کنید.",
    "http_404": "آدرس یافت نشد (۴۰۴) — مسیر دارونامه تغییر کرده؛ URL را در GUI اصلاح کنید.",
    "http_403_waf": "۴۰۳ با نشانهٔ WAF/Cloudflare — IP خروجی پروکسی مسدود است؛ exit دیگری امتحان کنید.",
    "http_403": "۴۰۳ ممنوع — نیازمند احراز هویت یا کوکی نشست است.",
    "login_redirect": "تغییرمسیر به صفحهٔ ورود — منبع پشت لاگین است؛ URL دادهٔ پس از ورود را ثبت کنید.",
    "server_error": "خطای سرور مقصد (۵xx) — بعداً دوباره امتحان کنید.",
    "js_shell_no_table": "صفحه بارگذاری شد ولی جدولی نیست و ظاهراً SPA/JS است — احتمالاً API پشت‌صحنه (json_api) لازم است.",
    "login_wall": "فرم ورود بازگشت — منبع عمومی نیست؛ اعتبارنامه/نشست لازم است.",
    "ok": "دریافت موفق.",
    "empty_result": "دریافت شد ولی هیچ ردیفی استخراج نشد — استراتژی/تنظیمات ستون را بررسی کنید.",
    "parse_fail": "خطای تجزیهٔ جدول — قالب فایل پشتیبانی نمی‌شود یا خراب است.",
    "unknown": "دسته‌بندی نشد — پیام و بدنه را بررسی کنید.",
}
_WAF = ("cloudflare", "access denied", "captcha", "attention required")


def classify(status: int, headers: dict, body: bytes, exception: str | None) -> tuple[str, str, str]:
    """(category, severity, hint). Pure and deterministic; first match wins."""
    exc = (exception or "").lower()
    if exc:
        if "proxy" in exc or "tunnel" in exc:
            return ("proxy_unreachable", "error", HINTS["proxy_unreachable"])
        if "getaddrinfo" in exc or "name or service not known" in exc or "nodename" in exc or "name resolution" in exc:
            return ("dns_fail", "error", HINTS["dns_fail"])
        if "ssl" in exc or "certificate" in exc or "tls" in exc:
            return ("tls_fail", "error", HINTS["tls_fail"])
        if "timed out" in exc or "timeout" in exc:
            return ("timeout", "error", HINTS["timeout"])
    text = (body[:SNIPPET_BYTES].decode("utf-8", "replace").lower()) if body else ""
    loc = str(headers.get("Location") or headers.get("location") or "").lower()
    if status == 0:
        return ("transport_error", "error", HINTS["transport_error"])
    if status == 429:
        return ("rate_limited", "error", HINTS["rate_limited"])
    if status == 404:
        return ("http_404", "error", HINTS["http_404"])
    if status == 403:
        if any(w in text for w in _WAF):
            return ("http_403_waf", "error", HINTS["http_403_waf"])
        return ("http_403", "error", HINTS["http_403"])
    if status in (301, 302, 303, 307, 308) and any(k in loc for k in ("login", "account", "sso", "auth")):
        return ("login_redirect", "error", HINTS["login_redirect"])
    if status >= 500:
        return ("server_error", "error", HINTS["server_error"])
    if status == 200:
        if "<table" not in text and re.search(r'id="root"|id="app"|window\.__|<script', text):
            return ("js_shell_no_table", "warn", HINTS["js_shell_no_table"])
        if "<table" not in text and re.search(r'type="password"', text):
            return ("login_wall", "warn", HINTS["login_wall"])
        return ("ok", "ok", HINTS["ok"])
    return ("unknown", "error", HINTS["unknown"])


@dataclass
class FetchAttempt:
    seq: int
    url: str
    status: int
    content_type: str | None
    server: str | None
    location: str | None
    body_snippet: str
    body_bytes: int
    elapsed_ms: int
    exception: str | None
    category: str
    severity: str
    hint: str


class DiagnosticRecorder:
    """Collects FetchAttempts, streams non-ok (and, in 'all' mode, ok) lines to a
    JSONL file, and bounds memory in 'errors_only' mode (NFI's 60k-page crawl)."""

    def __init__(self, crawler: str, label: str, *, mode: str = "all",
                 log_dir: str | Path = "logs/harvest"):
        self.crawler, self.label, self.mode = crawler, label, mode
        self._dir = Path(log_dir) / crawler
        self._attempts: list[FetchAttempt] = []
        self._ok_count = 0
        self._seq = 0
        self._fh = None
        self.log_file: str | None = None
        self._t0 = int(time.time())
        self._prune()

    def _prune(self) -> None:
        try:
            files = sorted(self._dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
            for p in files[DIAG_KEEP_FILES:]:
                p.unlink()
        except Exception:
            pass

    def _file(self):
        if self._fh is None:
            try:
                self._dir.mkdir(parents=True, exist_ok=True)
                path = self._dir / f"{self.label}-{self._t0}.jsonl"
                self._fh = open(path, "a", encoding="utf-8")
                self.log_file = str(path)
            except Exception:
                self._fh = None
        return self._fh

    def _write(self, obj: dict) -> None:
        try:
            fh = self._file()
            if fh:
                fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
                fh.flush()
        except Exception:
            pass

    def record(self, url, status, headers, body, elapsed_ms, exception=None) -> FetchAttempt:
        self._seq += 1
        headers = headers or {}
        body = body or b""
        cat, sev, hint = classify(status, headers, body, exception)
        att = FetchAttempt(
            seq=self._seq, url=url, status=status,
            content_type=headers.get("Content-Type") or headers.get("content-type"),
            server=headers.get("Server") or headers.get("server"),
            location=headers.get("Location") or headers.get("location"),
            body_snippet=body[:SNIPPET_BYTES].decode("utf-8", "replace"),
            body_bytes=len(body), elapsed_ms=elapsed_ms, exception=exception,
            category=cat, severity=sev, hint=hint,
        )
        if self.mode == "errors_only" and sev == "ok":
            self._ok_count += 1
            return att
        self._attempts.append(att)
        if self.mode == "all" or sev != "ok":
            self._write(asdict(att))
        if sev != "ok":
            logger.warning("harvest_fetch_failed", extra={
                "crawler": self.crawler, "label": self.label, "url": url,
                "status": status, "category": cat, "hint": hint})
        return att

    def note(self, category: str, hint: str, *, url: str = "", severity: str = "error") -> None:
        self._seq += 1
        att = FetchAttempt(self._seq, url, 0, None, None, None, "", 0, 0, None,
                           category, severity, hint)
        self._attempts.append(att)
        self._write(asdict(att))

    def summary(self) -> dict:
        cats: dict[str, int] = {}
        for a in self._attempts:
            cats[a.category] = cats.get(a.category, 0) + 1
        if self._ok_count:
            cats["ok"] = cats.get("ok", 0) + self._ok_count
        failed = sum(1 for a in self._attempts if a.severity != "ok")
        total = len(self._attempts) + self._ok_count
        worst = next((a for a in self._attempts if a.severity == "error"), None) \
            or next((a for a in self._attempts if a.severity == "warn"), None)
        return {"total": total, "ok": total - failed, "failed": failed,
                "categories": cats,
                "worst_category": worst.category if worst else None,
                "top_hint": worst.hint if worst else None,
                "log_file": self.log_file}

    def to_db(self, cap: int = DIAG_CAP_ATTEMPTS_DB) -> dict:
        return {"attempts": [asdict(a) for a in self._attempts[-cap:]], "summary": self.summary()}

    def close(self) -> None:
        if self._fh is not None:
            self._write({"summary": self.summary()})
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None


def wrap_fetch(raw_fetch, recorder: "DiagnosticRecorder | None"):
    """raw_fetch(url) -> (status, body, ct, headers); raises on transport failure.
    Returns fetch(url) -> (status, body, ct) that records every attempt and keeps
    the callers' existing contract (0, b"", "" on transport failure)."""
    def fetch(url):
        t = time.time()
        try:
            status, body, ct, headers = raw_fetch(url)
        except Exception as e:
            if recorder:
                recorder.record(url, 0, {}, b"", int((time.time() - t) * 1000), repr(e))
            return 0, b"", ""
        if recorder:
            recorder.record(url, status, headers, body, int((time.time() - t) * 1000), None)
        return status, body, ct
    return fetch
```

- [ ] **Step 1.4: Run to verify pass**

Run: `python3 -m pytest tests/unit/test_harvest_diagnostics.py -q`
Expected: all pass (16).

- [ ] **Step 1.5: Commit**

```bash
git add services/core/drug_catalog/harvest_diagnostics.py tests/unit/test_harvest_diagnostics.py
git commit -m "feat(harvest): diagnostics — classify taxonomy, recorder, wrap_fetch"
```

---

### Task 2: coverage raising fetch + recorder in `_run`/probe

**Files:** Modify `services/core/drug_catalog/coverage_harvest.py`, append to `tests/unit/test_harvest_diagnostics.py`

- [ ] **Step 2.1: Write the failing test** — append to `tests/unit/test_harvest_diagnostics.py`:

```python
def test_coverage_make_raw_fetch_keeps_http_error_body(monkeypatch):
    """make_raw_fetch must return (code, error-body, ct, headers) on HTTPError,
    not swallow it — that body is the diagnostic payload."""
    import io, urllib.error
    from services.core.drug_catalog import coverage_harvest as ch

    class FakeResp(io.BytesIO):
        status = 200
        class headers:
            @staticmethod
            def get_content_type(): return "text/html"
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class FakeOpener:
        def open(self, req, timeout=30):
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden",
                                         {"Server": "cloudflare"}, io.BytesIO(b"Access Denied"))
    monkeypatch.setattr(ch, "make_opener", lambda proxy=None: FakeOpener())
    raw = ch.make_raw_fetch()
    status, body, ct, headers = raw("https://x/l")
    assert status == 403 and body == b"Access Denied"
```

Run: `python3 -m pytest tests/unit/test_harvest_diagnostics.py::test_coverage_make_raw_fetch_keeps_http_error_body -q`
Expected: FAIL — `make_raw_fetch` doesn't exist.

- [ ] **Step 2.2: Replace `make_fetcher` in `coverage_harvest.py`**

Add the import near the other local imports (top of file, after `from . import harvest_lock`):

```python
from .harvest_diagnostics import DiagnosticRecorder, wrap_fetch
```

Replace the entire existing `make_fetcher` function with:

```python
def make_raw_fetch(proxy: str | None = None, timeout: int = 30):
    """raw_fetch(url) -> (status, body, ct, headers). Raises on transport failure;
    KEEPS the HTTP-error response body (the diagnostic payload)."""
    opener = make_opener(proxy or os.getenv("HTTPS_PROXY"))

    def raw_fetch(url: str):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Accept": "*/*", "Accept-Language": "fa,en;q=0.8"})
        try:
            with opener.open(req, timeout=timeout) as resp:
                return resp.status, resp.read(), resp.headers.get_content_type(), dict(resp.headers)
        except urllib.error.HTTPError as e:
            body = e.read() if hasattr(e, "read") else b""
            ct = e.headers.get_content_type() if e.headers else ""
            return e.code, body, ct, dict(e.headers or {})
    return raw_fetch


def make_fetcher(proxy: str | None = None, recorder=None, timeout: int = 30):
    """fetch(url) -> (status, body, ct). Records every attempt when a recorder is
    given; preserves the (0, b'', '') transport-failure contract either way."""
    return wrap_fetch(make_raw_fetch(proxy, timeout), recorder)
```

(`import urllib.error` is pulled in transitively by `import urllib.request`, already at the top — as in `nfi.py`.)

- [ ] **Step 2.3: Thread a recorder through `_run`**

In `coverage_harvest._run`, replace the `fetch = make_fetcher(settings.get("proxy"))` line and wrap the staging + persistence so diagnostics are recorded and saved on BOTH success and failure. The new body of the `try:` (inside `async with AsyncSessionLocal() as db:`) becomes:

```python
            settings = src.settings or {}
            recorder = DiagnosticRecorder("coverage", src.insurer, mode="all")
            fetch = make_fetcher(settings.get("proxy"), recorder=recorder)
            strategy = src.strategy
            if strategy == "auto":
                s, body, ct = fetch(src.url)
                if s != 200 or not body:
                    raise RuntimeError(f"HTTP {s} از مقصد — پروکسی ایران در دسترس نیست یا آدرس اشتباه است")
                strategy = sniff_strategy(body, ct, src.url)

            _STATE.phase = "fetching"
            import asyncio
            rows, pages = await asyncio.to_thread(fetch_rows, src.url, strategy, settings, fetch)
            _STATE.pages, _STATE.rows = pages, len(rows)
            if not rows:
                recorder.note("empty_result", "دریافت شد ولی هیچ ردیفی استخراج نشد.")

            _STATE.phase = "linking"
            catalog = await repo.fetch_all(db)
            current = await _current_coverage(db, src.insurer)
            _STATE.phase = "diffing"
            try:
                payload = await asyncio.to_thread(
                    stage_run_payload, rows, catalog,
                    insurer=src.insurer, overrides=settings.get("column_overrides"),
                    current=current)
            except Exception as pe:
                recorder.note("parse_fail", f"{type(pe).__name__}: {pe}")
                raise

            _STATE.phase = "saving"
            run.status = "parsed"
            run.finished_at = datetime.now(timezone.utc)
            run.stats, run.staged = payload["stats"], payload["staged"]
            run.review, run.unmatched, run.diff = payload["review"], payload["unmatched"], payload["diff"]
            recorder.close()
            run.diagnostics = recorder.to_db()
            src.last_run_at, src.last_run_status = run.finished_at, "parsed"
            await db.commit()
            _STATE.phase = "done"
```

In the `except Exception as e:` block that follows, after setting `r.status, r.error = "failed", _STATE.error`, also persist diagnostics — add inside that block right after the `if r:` assignment:

```python
                    if r:
                        r.status, r.error = "failed", _STATE.error
                        try:
                            recorder.close()
                            r.diagnostics = recorder.to_db()
                        except Exception:
                            pass
```

(`recorder` is defined at the top of the `try`; if the failure happened before it was created, guard with `recorder = locals().get("recorder")` — add `recorder = None` immediately before `run_id = None` at the top of `_run`, and change the guard to `if r and recorder:` for the diagnostics lines.)

- [ ] **Step 2.4: Run to verify pass** (the model column comes in Task 4; this step only needs the raw-fetch test)

Run: `python3 -m pytest tests/unit/test_harvest_diagnostics.py -q`
Expected: all pass (17). Also `python3 -c "import services.core.drug_catalog.coverage_harvest"` imports clean.

- [ ] **Step 2.5: Commit**

```bash
git add services/core/drug_catalog/coverage_harvest.py tests/unit/test_harvest_diagnostics.py
git commit -m "feat(coverage): raising raw-fetch that keeps error bodies; record run diagnostics"
```

---

### Task 3: NFI raising fetch + recorder (errors-only)

**Files:** Modify `services/core/drug_catalog/nfi.py`, `services/core/drug_catalog/nfi_harvest_service.py`, append test

- [ ] **Step 3.1: Write the failing test** — append to `tests/unit/test_harvest_diagnostics.py`:

```python
def test_nfi_fetch_detail_raw_keeps_error_body(monkeypatch):
    import io, urllib.error
    from services.core.drug_catalog import nfi

    class FakeOpener:
        def open(self, req, timeout=25):
            raise urllib.error.HTTPError(req.full_url, 500, "err", {"Server": "iis"},
                                         io.BytesIO(b"server boom"))
    monkeypatch.setattr(nfi, "make_opener", lambda proxy=None: FakeOpener())
    status, body, headers = nfi.fetch_detail_raw(17248, FakeOpener())
    assert status == 500 and body == b"server boom" and headers.get("Server") == "iis"
```

Run it → FAIL (`fetch_detail_raw` missing).

- [ ] **Step 3.2: Add `fetch_detail_raw` to `nfi.py`** (after `fetch_detail`):

```python
def fetch_detail_raw(page_id: int, opener: urllib.request.OpenerDirector | None = None,
                     timeout: int = 25) -> tuple[int, bytes, dict]:
    """Like fetch_detail but returns raw bytes + headers and KEEPS the HTTP-error
    body; raises on transport failure. For the diagnostics-recording crawl path."""
    opener = opener or make_opener()
    req = urllib.request.Request(f"{BASE}/NFI/Detail/{page_id}", headers={
        "User-Agent": UA, "Accept": "text/html", "Accept-Language": "fa,en;q=0.8",
        "Referer": f"{BASE}/nfi"})
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, (e.read() if hasattr(e, "read") else b""), dict(e.headers or {})
```

- [ ] **Step 3.3: Wire the recorder into `nfi_harvest_service._run`**

Add imports at the top: `import time` (already present) and
```python
from .harvest_diagnostics import DiagnosticRecorder
from .nfi import BASE, fetch_detail_raw
```

Add a `diagnostics` field to `HarvestState` (after `message: str = ""`):
```python
    diagnostics: dict = field(default_factory=dict)
```
(`field` is already imported from dataclasses.)

Add a module-level helper (near `_flush`):
```python
def _fetch_and_record(page_id: int, opener, recorder) -> tuple[int, str]:
    url = f"{BASE}/NFI/Detail/{page_id}"
    t = time.time()
    try:
        status, body, headers = fetch_detail_raw(page_id, opener)
    except Exception as e:
        recorder.record(url, 0, {}, b"", int((time.time() - t) * 1000), repr(e))
        return 0, ""
    recorder.record(url, status, headers, body, int((time.time() - t) * 1000), None)
    return status, body.decode("utf-8", "replace") if body else ""
```

In `_run`, create the recorder and use the helper. Replace:
```python
    opener = make_opener(proxy or os.getenv("HTTPS_PROXY"))
    batch: list[dict] = []
```
with:
```python
    opener = make_opener(proxy or os.getenv("HTTPS_PROXY"))
    recorder = DiagnosticRecorder("nfi", "nfi", mode="errors_only")
    batch: list[dict] = []
```
Replace the fetch line:
```python
            status_code, html = await asyncio.to_thread(fetch_detail, pid, opener)
```
with:
```python
            status_code, html = await asyncio.to_thread(_fetch_and_record, pid, opener, recorder)
            _STATE.diagnostics = recorder.summary()
```
In the `finally:` of `_run`, before `harvest_lock.release("nfi")`, add:
```python
        try:
            recorder.close()
            _STATE.diagnostics = recorder.summary()
        except Exception:
            pass
```

- [ ] **Step 3.4: Run to verify pass**

Run: `python3 -m pytest tests/unit/test_harvest_diagnostics.py -q && python3 -c "import services.core.drug_catalog.nfi_harvest_service"`
Expected: all pass (18); import clean.

- [ ] **Step 3.5: Commit**

```bash
git add services/core/drug_catalog/nfi.py services/core/drug_catalog/nfi_harvest_service.py tests/unit/test_harvest_diagnostics.py
git commit -m "feat(nfi): raising fetch_detail_raw; record crawl diagnostics (errors-only)"
```

---

### Task 4: model column + migration 0019

**Files:** Modify `shared/models/coverage.py`, create `data/migrations/versions/0019_coverage_run_diagnostics.py`

- [ ] **Step 4.1: Add the column** to `CoverageRun` in `shared/models/coverage.py` (after `unmatched`):

```python
    diagnostics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)   # {attempts:[…], summary:{…}}
```

- [ ] **Step 4.2: Create the migration** `data/migrations/versions/0019_coverage_run_diagnostics.py`:

```python
"""coverage_runs.diagnostics — per-run fetch diagnostics (attempts + summary)"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("coverage_runs", sa.Column("diagnostics", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("coverage_runs", "diagnostics")
```

- [ ] **Step 4.3: Apply + verify parity (my column adds zero drift)**

```bash
python3 -m pytest tests/unit/test_alembic_schema_parity.py -rx 2>&1 > /tmp/parity.txt; grep -c "diagnostics\|coverage_runs" /tmp/parity.txt   # expect 0 — my column not in the drift
DATABASE_URL="postgresql+asyncpg://pharmpilot:pharmpilot_dev@127.0.0.1:5433/pharmpilot" python3 -m alembic -c alembic.ini upgrade head
PGPASSWORD=pharmpilot_dev psql -h 127.0.0.1 -p 5433 -U pharmpilot -d pharmpilot -t -c "SELECT column_name FROM information_schema.columns WHERE table_name='coverage_runs' AND column_name='diagnostics';"
```
Expected: grep prints `0`; alembic upgrades 0018→0019; psql prints `diagnostics`.

- [ ] **Step 4.4: Commit**

```bash
git add shared/models/coverage.py data/migrations/versions/0019_coverage_run_diagnostics.py
git commit -m "feat(db): coverage_runs.diagnostics column (migration 0019)"
```

---

### Task 5: API — surface diagnostics

**Files:** Modify `services/platform/routers/pricing.py`

- [ ] **Step 5.1: Add `diag_summary` to the runs list.** In `list_coverage_runs`, change the per-row dict to include (after the `"diff_counts"` key):

```python
                      "diag_summary": (r.diagnostics or {}).get("summary") if r.diagnostics else None,
```

- [ ] **Step 5.2: Add `diagnostics` to the run detail.** In `get_coverage_run`'s return dict, add:

```python
            "diagnostics": r.diagnostics,
```

- [ ] **Step 5.3: Make a failed probe self-explaining.** Replace the body of `probe_coverage_source` (the `fetch`/`try` section) with a recorder-backed version:

```python
    import asyncio
    from sqlalchemy import select
    from shared.models.coverage import CoverageSource
    from services.core.drug_catalog import coverage_harvest as ch
    from services.core.drug_catalog.harvest_diagnostics import DiagnosticRecorder
    s = (await db.execute(select(CoverageSource)
                          .where(CoverageSource.id == source_id))).scalar_one_or_none()
    if not s or not s.url:
        raise HTTPException(status_code=404, detail="Source (or its URL) not found")
    recorder = DiagnosticRecorder("coverage", f"{s.insurer}-probe", mode="all")
    fetch = ch.make_fetcher((s.settings or {}).get("proxy"), recorder=recorder)
    try:
        return await asyncio.to_thread(ch.probe_payload, s.url,
                                       settings=s.settings or {}, fetch=fetch)
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail={
            "message": str(e), "diagnostics": recorder.to_db()})
    finally:
        recorder.close()
```

This requires `probe_payload` to accept the wrapped `fetch` (it already takes a `fetch` arg). Note `probe_payload` currently calls `fetch(url)` once — good, the recorder captures that attempt.

- [ ] **Step 5.4: Verify live.** Restart the backend and hit a deliberately-bad probe:

```bash
kill $(pgrep -f "uvicorn services.platform.main:app") 2>/dev/null; sleep 2
nohup /Users/sashad85/miniforge3/bin/python -m uvicorn services.platform.main:app --host 127.0.0.1 --port 8001 > /tmp/pharmpilot_backend.log 2>&1 &
for i in $(seq 1 30); do [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8001/health)" = "200" ] && break; sleep 1; done
TOKEN=$(curl -s -X POST http://127.0.0.1:8001/api/v1/auth/login -H "Content-Type: application/json" -d '{"username":"admin","password":"PharmPilot2024!"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
SRC=$(curl -s http://127.0.0.1:8001/api/v1/pricing/coverage/sources -H "Authorization: Bearer $TOKEN" | python3 -c "import sys,json;print([s for s in json.load(sys.stdin)['sources'] if s['insurer']=='tamin'][0]['id'])")
curl -s -X PUT http://127.0.0.1:8001/api/v1/pricing/coverage/sources/$SRC -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"insurer":"tamin","name":"t","url":"http://127.0.0.1:9/nope","strategy":"auto","settings":{},"check_interval_days":7,"enabled":true}' >/dev/null
curl -s -X POST http://127.0.0.1:8001/api/v1/pricing/coverage/sources/$SRC/probe -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | head -30
# restore the seed url
curl -s -X PUT http://127.0.0.1:8001/api/v1/pricing/coverage/sources/$SRC -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"insurer":"tamin","name":"دارونامه تأمین اجتماعی","url":"https://darman.tamin.ir","strategy":"auto","settings":{},"check_interval_days":7,"enabled":true}' >/dev/null
```
Expected: 422 with `detail.message` (Persian) and `detail.diagnostics.summary.worst_category` in (`transport_error`, `proxy_unreachable`, `dns_fail`), proving the failed probe now classifies itself.

- [ ] **Step 5.5: Commit**

```bash
git add services/platform/routers/pricing.py
git commit -m "feat(api): surface run diagnostics + classified probe failures"
```

---

### Task 6: GUI — diagnostics surfacing

**Files:** Modify `frontend/workstation/src/dashboards/CoverageAdmin.tsx`, `frontend/workstation/src/dashboards/DrugCatalogAdmin.tsx`

- [ ] **Step 6.1: Show diagnostics in the run preview.** In `CoverageAdmin.tsx`, inside `RunPreview`, after the diff block (before the review block), add a diagnostics disclosure fed by `run.diagnostics`:

```tsx
      {run.diagnostics?.summary && (run.diagnostics.summary.failed > 0 || (run.diagnostics.attempts||[]).length > 0) && (
        <details className="border border-slate-700 rounded p-2">
          <summary className="cursor-pointer text-slate-300">
            تشخیص خطاها — {fa(run.diagnostics.summary.failed)} ناموفق از {fa(run.diagnostics.summary.total)}
            {run.diagnostics.summary.worst_category && ` · ${run.diagnostics.summary.worst_category}`}
          </summary>
          {run.diagnostics.summary.top_hint && <p className="text-amber-300 mt-1">{run.diagnostics.summary.top_hint}</p>}
          <div className="max-h-48 overflow-y-auto space-y-1 mt-1">
            {(run.diagnostics.attempts || []).filter((a: any) => a.severity !== 'ok').map((a: any, i: number) => (
              <div key={i} className="font-mono text-[11px] text-slate-400 border-b border-slate-700/50 pb-1">
                <span className={a.severity === 'error' ? 'text-red-300' : 'text-amber-300'}>[{a.category}]</span>{' '}
                HTTP {a.status} · {a.url}
                {a.exception && <div className="text-red-400">{a.exception}</div>}
                <div className="text-slate-500">{a.hint}</div>
                {a.body_snippet && <details><summary className="cursor-pointer text-slate-600">بدنهٔ پاسخ</summary>
                  <pre className="whitespace-pre-wrap text-slate-500">{a.body_snippet.slice(0, 500)}</pre></details>}
              </div>))}
          </div>
        </details>)}
```

- [ ] **Step 6.2: Show a classified probe failure.** In `CoverageAdmin`'s `doProbe`, replace the `catch` so a 422 with a diagnostics object surfaces the category+hint:

```tsx
    } catch (e: any) {
      const d = e?.response?.data?.detail
      if (d && typeof d === 'object' && d.diagnostics?.summary) {
        setMsg({ kind: 'err', text: `تشخیص ناموفق [${d.diagnostics.summary.worst_category || '—'}]: ${d.diagnostics.summary.top_hint || d.message}` })
      } else {
        setMsg({ kind: 'err', text: (typeof d === 'string' ? d : 'تشخیص ناموفق بود.') })
      }
    }
```

- [ ] **Step 6.3: Worst-category in the NFI harvest strip.** In `DrugCatalogAdmin.tsx`, extend the `HarvestStatus` interface with `diagnostics?: { failed: number; worst_category: string | null; top_hint: string | null }` and, inside the progress block (after the `{hs.error || hs.message}` span), add:

```tsx
              {hs.diagnostics && hs.diagnostics.failed > 0 && (
                <span className="text-amber-300">تشخیص: {hs.diagnostics.worst_category} — {hs.diagnostics.top_hint}</span>
              )}
```

- [ ] **Step 6.4: Typecheck (no new errors over baseline).**

Run: `cd frontend/workstation && npx tsc -b 2>&1 | grep -E "CoverageAdmin|DrugCatalogAdmin"`
Expected: no output (my two files add no errors; the pre-existing DashboardShell/useAIProvider/MedReconciliation baseline is unchanged).

- [ ] **Step 6.5: Commit**

```bash
git add frontend/workstation/src/dashboards/CoverageAdmin.tsx frontend/workstation/src/dashboards/DrugCatalogAdmin.tsx
git commit -m "feat(gui): surface harvest diagnostics — run panel, probe failures, NFI strip"
```

---

### Task 7: End-to-end verification (injected failure)

**Files:** none — exercises the full path.

- [ ] **Step 7.1: Full unit suite + import sanity**

```bash
python3 -m pytest tests/unit/test_harvest_diagnostics.py tests/unit/test_coverage_harvest.py tests/unit/test_harvest_lock.py -q
python3 -c "import services.core.drug_catalog.coverage_harvest, services.core.drug_catalog.nfi_harvest_service; print('imports OK')"
```
Expected: all green; imports OK. (The pre-existing 7 baseline failures in `test_intake_precompute`/`test_integrations_sandbox` are unrelated.)

- [ ] **Step 7.2: Real harvest against an unreachable URL → diagnostics persisted**

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8001/api/v1/auth/login -H "Content-Type: application/json" -d '{"username":"admin","password":"PharmPilot2024!"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
SRC=$(curl -s http://127.0.0.1:8001/api/v1/pricing/coverage/sources -H "Authorization: Bearer $TOKEN" | python3 -c "import sys,json;print([s for s in json.load(sys.stdin)['sources'] if s['insurer']=='tamin'][0]['id'])")
curl -s -X PUT http://127.0.0.1:8001/api/v1/pricing/coverage/sources/$SRC -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"insurer":"tamin","name":"t","url":"http://127.0.0.1:9/nope","strategy":"auto","settings":{},"check_interval_days":7,"enabled":true}' >/dev/null
curl -s -X POST http://127.0.0.1:8001/api/v1/pricing/coverage/sources/$SRC/harvest -H "Authorization: Bearer $TOKEN" >/dev/null
for i in $(seq 1 15); do r=$(curl -s http://127.0.0.1:8001/api/v1/pricing/coverage/harvest/status -H "Authorization: Bearer $TOKEN" | python3 -c "import sys,json;print(json.load(sys.stdin)['running'])"); [ "$r" = "False" ] && break; sleep 1; done
RUN=$(curl -s http://127.0.0.1:8001/api/v1/pricing/coverage/runs -H "Authorization: Bearer $TOKEN" | python3 -c "import sys,json;print(json.load(sys.stdin)['runs'][0]['id'])")
curl -s http://127.0.0.1:8001/api/v1/pricing/coverage/runs/$RUN -H "Authorization: Bearer $TOKEN" | python3 -c "import sys,json; d=json.load(sys.stdin); print('status', d['status']); print('diag', d['diagnostics']['summary'])"
ls -la logs/harvest/coverage/ | tail -3
# cleanup: reset the seed + remove the failed run
curl -s -X PUT http://127.0.0.1:8001/api/v1/pricing/coverage/sources/$SRC -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"insurer":"tamin","name":"دارونامه تأمین اجتماعی","url":"https://darman.tamin.ir","strategy":"auto","settings":{},"check_interval_days":7,"enabled":true}' >/dev/null
PGPASSWORD=pharmpilot_dev psql -h 127.0.0.1 -p 5433 -U pharmpilot -d pharmpilot -c "DELETE FROM coverage_runs; UPDATE coverage_sources SET last_run_at=NULL,last_run_status=NULL;" >/dev/null
```
Expected: run `status=failed`; `diag.summary` shows `failed>=1` with `worst_category` in (`transport_error`,`proxy_unreachable`,`dns_fail`) and a `top_hint`; a `.jsonl` file exists under `logs/harvest/coverage/`.

- [ ] **Step 7.3: Browser check** — via preview tools: open the workstation (server `workstation`), log in (admin/PharmPilot2024!), Insurance Coverage (Alt+B). Trigger a probe on an unreachable URL and confirm the message shows `[category]: hint`. `preview_console_logs` level=error: none. Screenshot for the record.

- [ ] **Step 7.4: gitignore + graph + commit**

```bash
grep -q "^logs/" .gitignore || printf "\n# harvest diagnostics logs (runtime)\nlogs/\n" >> .gitignore
git add .gitignore
git commit -m "chore: gitignore harvest diagnostics logs"
graphify update .
```

---

## Post-plan (owner, behind the Iran proxy)

Run each insurer probe/harvest as normal. When a source fails, the run's **تشخیص خطاها** panel (and `logs/harvest/coverage/<insurer>-*.jsonl`) now names the exact category (WAF block / login wall / JS shell / DNS / proxy) and the next step — hand me the JSONL (or the run id) and I can fix that portal's strategy without needing the proxy myself.
