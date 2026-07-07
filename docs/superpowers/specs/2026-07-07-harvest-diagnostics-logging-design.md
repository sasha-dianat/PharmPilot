# Harvest Diagnostics Logging — Design

**Date:** 2026-07-07 · **Status:** approved by owner · **Depends on:** `feat/darunameh-crawler` (coverage crawler + NFI extraction)

## Problem

The Iran proxy lives on the owner's machine, so when a دارونامه harvest or NFI
crawl runs, the *real* reason a formulary source fails happens server-side and
is lost:

- `coverage_harvest.make_fetcher`'s inner `fetch` does `except Exception:
  return 0, b"", ""` — the actual transport error (proxy refused / DNS / TLS /
  timeout) is discarded.
- On an HTTP error it returns `(e.code, b"", "")` — the **response body is
  thrown away**, which is exactly the diagnostic payload (a login redirect, a
  WAF/region-block page, a JS-only shell).
- `nfi.py:fetch_detail` has the same swallow-the-error shape.
- `CoverageRun.error` is a single summary string; there is no per-attempt record.

Result: after a proxy session, neither the owner nor Claude can tell *why* a
given insurer portal yielded nothing. This feature makes each fetch attempt
self-explaining and persists it in three sinks so it can be diagnosed offline.

## Decisions (owner-confirmed)

1. **Three sinks:** structured per-run diagnostics in the DB (queryable) + a
   GUI panel + a JSONL log file (attachable). Belt-and-suspenders.
2. **Full diagnostics per attempt:** URL, status, key headers, ~2KB body
   snippet, timing, real exception, classified category.
3. **Classify + actionable hint:** deterministic rule engine (no LLM) maps each
   attempt to a category and a concrete next-step hint.
4. **Shared layer, both crawlers:** one diagnostic fetch wrapper used by the
   دارونامه harvest AND the NFI crawl.
5. **Professional touches (owner: "go for the more professional choice"):**
   file at the standard `logs/harvest/…` location; DB row bounded (cap 500
   attempts) while the file keeps everything; each failed attempt also emits a
   structured `logger.warning`; NFI records **errors-only** (a 60k-page crawl
   must not produce a 60k-line file), coverage records every attempt.
6. **Workflow:** this spec + the implementation plan are authored on Fable 5;
   the implementation is executed by Opus 4.8 subagents.

---

## Component 1 — `services/core/drug_catalog/harvest_diagnostics.py`

Self-contained, stdlib-only, no DB import (keeps the CLI NFI script lightweight).

### 1.1 FetchAttempt

```python
@dataclass
class FetchAttempt:
    seq: int
    url: str
    status: int                 # 0 = transport failure (no HTTP response)
    content_type: str | None
    server: str | None
    location: str | None        # redirect target, if any
    body_snippet: str           # ≤ SNIPPET_BYTES, utf-8 errors=replace
    body_bytes: int             # full length before truncation
    elapsed_ms: int
    exception: str | None       # repr of the transport exception, if any
    category: str               # see taxonomy
    severity: str               # "ok" | "warn" | "error"
    hint: str
```

`SNIPPET_BYTES = 2048`.

### 1.2 Classification taxonomy — `classify()`

```python
def classify(status: int, headers: dict[str, str], body: bytes,
             exception: str | None) -> tuple[str, str, str]:
    """→ (category, severity, hint). Pure and deterministic."""
```

Rules, evaluated top-down (first match wins):

| # | Condition | category | severity | hint |
|---|---|---|---|---|
| 1 | exception matches `proxy`/`tunnel`/`ProxyError` (case-insensitive) | `proxy_unreachable` | error | «پروکسی ایران در دسترس نیست یا رد شد — اتصال/اعتبار پروکسی را بررسی کنید.» |
| 2 | exception matches `getaddrinfo`/`name or service not known`/`nodename` | `dns_fail` | error | «DNS مقصد حل نشد — دامنه درست است؟ از پشت پروکسی قابل‌حل است؟» |
| 3 | exception matches `ssl`/`certificate`/`tls` | `tls_fail` | error | «خطای TLS/گواهی — ممکن است پروکسی MITM کند یا دامنه گواهی نامعتبر دارد.» |
| 4 | exception matches `timed out`/`timeout` | `timeout` | error | «مهلت پاسخ تمام شد — سایت کند است یا پروکسی throttle می‌کند؛ delay را بالا ببرید.» |
| 5 | status == 0 (any other exception) | `transport_error` | error | «اتصال برقرار نشد — پیام استثنا را ببینید.» |
| 6 | status == 429 | `rate_limited` | error | «محدودیت نرخ — delay را افزایش دهید یا بعداً دوباره امتحان کنید.» |
| 7 | status == 404 | `http_404` | error | «آدرس یافت نشد — مسیر دارونامه تغییر کرده؛ URL را در GUI اصلاح کنید.» |
| 8 | status == 403 AND body matches `cloudflare`/`access denied`/`captcha`/`attention required` | `http_403_waf` | error | «۴۰۳ با نشانهٔ WAF/Cloudflare — IP خروجی پروکسی مسدود است؛ exit دیگری امتحان کنید.» |
| 9 | status == 403 | `http_403` | error | «۴۰۳ ممنوع — نیازمند احراز هویت/کوکی نشست است.» |
| 10 | status in {301,302,303,307,308} AND Location matches `login`/`account`/`sso`/`auth` | `login_redirect` | error | «تغییرمسیر به صفحهٔ ورود — منبع پشت لاگین است؛ URL دادهٔ پس از ورود را ثبت کنید.» |
| 11 | status >= 500 | `server_error` | error | «خطای سرور مقصد — بعداً دوباره امتحان کنید.» |
| 12 | status == 200 AND body has no `<table` AND (`id="root"`/`id="app"`/`window.__`/`<script` heavy) | `js_shell_no_table` | warn | «صفحه بارگذاری شد ولی جدولی نیست و ظاهراً SPA/JS است — احتمالاً API پشت‌صحنه (json_api) یا استراتژی مرورگری لازم است.» |
| 13 | status == 200 AND body has a `<form` with a password field | `login_wall` | warn | «صفحهٔ ورود بازگشت — منبع عمومی نیست؛ اعتبارنامه/نشست لازم است.» |
| 14 | status == 200 | `ok` | ok | «دریافت موفق.» |
| 15 | else | `unknown` | error | «دسته‌بندی نشد — پیام و بدنه را بررسی کنید.» |

`empty_result` and `parse_fail` are **not** fetch-level; they are recorded by
the caller (`stage_run_payload`/`_run`) as synthetic attempts when a 200 fetch
yields zero rows or `read_table` raises, so the timeline is complete.

### 1.3 DiagnosticRecorder

```python
class DiagnosticRecorder:
    def __init__(self, crawler: str, label: str, *, mode: str = "all",
                 log_dir: str | Path = "logs/harvest"): ...
    #   crawler: "coverage" | "nfi"; label: insurer or "nfi"; mode: "all" | "errors_only"

    def record(self, url, status, headers, body, elapsed_ms, exception=None) -> FetchAttempt
    def note(self, category, hint, *, url="", severity="error") -> None   # synthetic (parse_fail/empty_result)
    def summary(self) -> dict     # {total, ok, failed, categories:{}, worst_category, top_hint, log_file}
    def to_db(self, cap: int = 500) -> dict   # {attempts:[…last cap…], summary}
    def close(self) -> None
```

Behaviour:
- Every `record()` classifies, appends a `FetchAttempt`, and — when `mode=="all"`
  or the attempt is non-`ok` — streams one JSON line to the file and, for
  non-`ok`, emits `logger.warning("harvest_fetch_failed", extra={…})`.
- File opened lazily on first written line at
  `logs/harvest/<crawler>/<label>-<opened_at>.jsonl`; a final `{"summary":…}`
  line is written on `close()`.
- All I/O is best-effort: any recorder exception is swallowed (a diagnostics
  failure must never abort a crawl) — guarded and logged at debug level.
- `DIAG_CAP_ATTEMPTS_DB = 500`, `DIAG_KEEP_FILES = 50` (prune older files per
  crawler dir on `__init__`).

### 1.4 wrap_fetch

```python
def wrap_fetch(raw_fetch, recorder: DiagnosticRecorder | None):
    """Return fetch(url)->(status, body, ct) that times raw_fetch, records the
    attempt, and preserves the exact return contract callers already expect —
    including returning (0, b"", "") after a transport exception."""
```

`raw_fetch` is the **raising** primitive (below). `wrap_fetch` try/excepts it,
captures the exception repr, records, and returns `(0, b"", "")` on failure or
`(status, body, ct)` on success — so `fetch_rows`, `probe_payload`, and the NFI
loop need **no logic changes**, only to be handed a wrapped fetch.

---

## Component 2 — raising fetch primitives (the actual bug fix)

### 2.1 coverage `make_fetcher`

Split into:
- `raw_fetch(url) -> (status, body, ct)` — **raises** `URLError`/`HTTPError`/
  `socket.timeout` on transport failure, and on `HTTPError` returns
  `(e.code, e.read(), e.headers.get_content_type())` so the **error-page body is
  kept**.
- `make_fetcher(proxy=None, recorder=None)` — builds `raw_fetch` and returns
  `wrap_fetch(raw_fetch, recorder)` (a bare wrapper with `recorder=None` still
  yields the old `(0,b"",""`)-on-failure contract, so existing callers/tests are
  unaffected).

### 2.2 NFI `fetch_detail`

Add `fetch_detail_raw(page_id, opener, timeout)` that raises / keeps the error
body, and have `nfi_harvest_service._run` wrap it with a recorder
(`mode="errors_only"`). The existing `fetch_detail` (swallowing) stays for the
CLI script's simple path, or the CLI opts in via a `--diagnose` flag writing the
same JSONL. (CLI flag is optional; GUI path is the priority.)

---

## Component 3 — persistence

### 3.1 DB — migration 0019

`coverage_runs.diagnostics` JSONB, nullable:
`{attempts: […≤500…], summary: {total, ok, failed, categories, worst_category, top_hint, log_file}}`.
Single Alembic head (down_revision = "0018"). Register nothing new (column on an
existing, already-registered table).

`coverage_harvest._run` builds `DiagnosticRecorder("coverage", insurer,
mode="all")`, passes its wrapped fetch through `fetch_rows`, and on completion
(success OR failure) sets `run.diagnostics = recorder.to_db()`. On the "auto"
strategy pre-fetch and on probe, the same recorder is used so the very first
reachability failure is captured.

### 3.2 File

`logs/harvest/coverage/<insurer>-<run_started>.jsonl` and
`logs/harvest/nfi/nfi-<started>.jsonl`. Add `logs/` to `.gitignore`. Keep last
50 files per crawler dir.

### 3.3 NFI in-memory

`HarvestState` gains `diagnostics: dict` = `recorder.summary()`, refreshed as the
crawl runs; surfaced by `/catalog/nfi/status`.

---

## Component 4 — API

- `GET /pricing/coverage/runs` list items gain `diag_summary` =
  `{worst_category, failed, top_hint}` (from `stats`/`diagnostics.summary`).
- `GET /pricing/coverage/runs/{id}` gains `diagnostics` (attempts + summary).
- `GET /pricing/coverage/sources/{id}/probe` — on failure, raise
  `HTTPException(422, detail={"message": <Persian>, "diagnostics": <one failed
  attempt: category, hint, snippet>})` (FastAPI serializes an object detail), so
  the modal renders the classified reason instead of a bare string.
- `GET /pricing/catalog/nfi/status` gains `diagnostics` (summary only).

No new permissions (reuse `inventory:read`).

---

## Component 5 — GUI

- **CoverageAdmin run row** → a **"تشخیص خطاها"** disclosure: colored category
  chips with counts, the top hint, and a scrollable list of failed attempts
  (`status · category · hint`, with the body snippet in a `<details>`/monospace
  block). Fed by the run detail's `diagnostics`.
- **Probe modal** → when probe fails, show the category + hint + snippet instead
  of a bare error string.
- **DrugCatalogAdmin harvest strip** → when `diagnostics.failed > 0`, show the
  worst category + top hint inline.
- Severity → color: `error` red, `warn` amber, `ok` emerald. RTL, fa-IR, same
  idiom as the existing panels.

---

## Error handling / caps

- Body snippet 2048 bytes, `utf-8` `errors="replace"`.
- DB attempts capped at 500 (`to_db`); the file keeps everything.
- Files pruned to the last 50 per crawler dir.
- Recorder I/O is best-effort and never propagates into the crawl.
- `logs/` gitignored; created on demand (`mkdir -p`).

## Testing (TDD)

- **`classify()`** — table-driven: one case per category (synthetic
  status/headers/body/exception), incl. 403-WAF vs plain-403, login-redirect vs
  other 3xx, js-shell vs real table, timeout/DNS/TLS/proxy exception strings.
- **`wrap_fetch`** — preserves `(status, body, ct)` on success; returns
  `(0,b"","")` and records the exception on a raising `raw_fetch`; captures the
  **HTTP-error body** (403 page bytes present in the snippet).
- **`raw_fetch`** — on `HTTPError`, body is `e.read()` (mock opener).
- **`DiagnosticRecorder`** — writes one JSONL line per recorded attempt + a
  final summary line; `mode="errors_only"` skips `ok` lines; `to_db(cap)` caps
  attempts but keeps the full summary; `summary()` computes worst_category /
  top_hint / counts; retention prunes to last N (tmp dir).
- **coverage `_run`** — on a failing fetch, `run.diagnostics` is populated and
  `run.status == "failed"` (integration via the injected-fetcher path; keep the
  DB parts thin).
- **API** — run detail returns `diagnostics`; runs list returns `diag_summary`.
- **GUI** — verified in-browser (diagnostics expander renders on a failed run).
- Migration 0019 applies; zero parity drift.

## Out of scope

- Live streaming of attempts to the browser (poll the run/status as today).
- A browser-rendering fetch strategy (still the escape hatch if `js_shell_no_table`
  turns out to be the common case — the hint points there).
- Alerting/paging integrations beyond the `logger.warning` line.
- Diagnosing the daily price-sync feed (separate module).

## Rollout

1. `harvest_diagnostics.py` + classify/recorder/wrap_fetch (TDD, pure).
2. Raising `raw_fetch` in coverage + NFI; wire recorders.
3. Migration 0019 + `_run` persistence.
4. API fields.
5. GUI diagnostics surfacing.
6. Verify: unit suite green; an injected-failure harvest produces a populated
   `diagnostics` + JSONL file; browser shows the expander.
