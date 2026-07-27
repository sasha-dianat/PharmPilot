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
    "redirect_loop": "تغییرمسیر حل‌نشده (احتمالاً کوکی‌محور) — پشتیبانی کوکی فعال است؛ دوباره امتحان کنید یا آدرس نهایی را مستقیم بگذارید.",
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
    # Structure verdicts must look at the WHOLE page (bounded), not the stored
    # snippet: NFI detail pages open with kilobytes of stylesheets/nav scripts,
    # and judging the head alone cried «SPA shell» on perfectly server-rendered
    # drug pages (52KB, real content far past the snippet).
    full = (body[:400_000].decode("utf-8", "replace").lower()) if body else ""
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
    if status in (301, 302, 303, 307, 308):
        return ("redirect_loop", "error", HINTS["redirect_loop"])
    if status >= 500:
        return ("server_error", "error", HINTS["server_error"])
    if status == 200:
        # server-rendered content anywhere in the page ⇒ not a JS shell
        rendered = ("<table" in full or "<label" in full or "نام عمومی" in full)
        if not rendered and re.search(r'id="root"|id="app"|window\.__|<script', full):
            return ("js_shell_no_table", "warn", HINTS["js_shell_no_table"])
        if not rendered and re.search(r'type="password"', full):
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
            body_snippet=body[:SNIPPET_BYTES].decode("utf-8", "replace").replace("\x00", ""),
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
