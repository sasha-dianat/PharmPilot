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
