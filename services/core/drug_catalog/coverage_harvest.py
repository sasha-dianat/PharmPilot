"""دارونامه acquisition engine — probe a configured insurer source, harvest it
with a pluggable strategy, run the smart coverage extractor, and stage the
result as a CoverageRun for human approval. Repeatable weekly: each run diffs
against the catalog's live coverage so the admin sees exactly what changed.

Deterministic, no LLM. Network I/O is isolated behind a fetcher callable so
every strategy is testable offline. Shares the global harvest_lock with the
NFI crawler (system-wide Iran proxy ⇒ one crawl at a time).
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
import urllib.request
from dataclasses import asdict, dataclass

from .coverage_import import build_coverage, infer_columns, link_rows, normalize_rows
from .excel_import import read_table
from .nfi import UA, make_opener
from . import harvest_lock

# entry fields that constitute a real coverage change (match metadata excluded)
_DIFF_FIELDS = ("covered", "share_pct", "reference_price", "ceiling")
_SAMPLE_CAP = 50

STRATEGIES = ("auto", "file_url", "html_table", "paginated_html", "json_api")


# ── fetching ─────────────────────────────────────────────────────────────────
def make_fetcher(proxy: str | None = None, timeout: int = 30):
    """fetch(url) -> (status, body_bytes, content_type). Proxy falls back to the
    server's HTTPS_PROXY (the Iran system proxy)."""
    opener = make_opener(proxy or os.getenv("HTTPS_PROXY"))

    def fetch(url: str) -> tuple[int, bytes, str]:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Accept": "*/*", "Accept-Language": "fa,en;q=0.8"})
        try:
            with opener.open(req, timeout=timeout) as resp:
                return resp.status, resp.read(), resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            return e.code, b"", ""
        except Exception:
            return 0, b"", ""
    return fetch


# ── strategy sniffing ────────────────────────────────────────────────────────
def sniff_strategy(body: bytes, content_type: str, url: str) -> str:
    ct = (content_type or "").lower()
    if body[:4] == b"PK\x03\x04" or "spreadsheet" in ct or "excel" in ct:
        return "file_url"
    stripped = body.lstrip()[:1]
    if stripped in (b"{", b"[") or "json" in ct:
        return "json_api"
    text = body[:200_000].decode("utf-8", errors="replace").lower()
    if "<table" in text:
        return "paginated_html" if "{page}" in url else "html_table"
    return "file_url"                       # CSV & anything table-file-like


# ── row extraction per strategy ──────────────────────────────────────────────
def _rows_from_bytes(body: bytes, content_type: str, url: str) -> list[dict]:
    """Write to a temp file with the right suffix and reuse read_table."""
    ct = (content_type or "").lower()
    if body[:4] == b"PK\x03\x04" or "spreadsheet" in ct:
        suffix = ".xlsx"
    elif "<table" in body[:200_000].decode("utf-8", errors="replace").lower():
        suffix = ".html"
    else:
        m = re.search(r"\.(xlsx|xlsm|xls|csv|tsv|html?|txt)(?:$|[?#])", url.lower())
        suffix = f".{m.group(1)}" if m else ".csv"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(body)
        tmp.close()
        return read_table(tmp.name)
    finally:
        os.unlink(tmp.name)


def _descend(obj, path: str):
    for part in [p for p in (path or "").split(".") if p]:
        if not isinstance(obj, dict) or part not in obj:
            return []
        obj = obj[part]
    return obj if isinstance(obj, list) else []


def _page_url(url_template: str, settings: dict, page: int) -> str:
    if "{page}" in url_template:
        return url_template.replace("{page}", str(page))
    sep = "&" if "?" in url_template else "?"
    return f"{url_template}{sep}{settings.get('page_param', 'page')}={page}"


def fetch_rows(url: str, strategy: str, settings: dict, fetch) -> tuple[list[dict], int]:
    """Run a strategy → (raw rows, pages fetched). Raises RuntimeError on a
    transport-dead first page so the caller can fail the run with a clear cause."""
    settings = settings or {}
    if strategy in ("file_url", "html_table"):
        status, body, ct = fetch(url)
        if status != 200 or not body:
            raise RuntimeError(f"HTTP {status} از مقصد — پروکسی ایران در دسترس نیست یا آدرس اشتباه است")
        rows = _rows_from_bytes(body, ct, url)
        if strategy == "html_table":
            idx = int(settings.get("table_index", 0) or 0)
            _ = idx  # read_table returns the largest table; index reserved for future selector work
        return rows, 1

    # paginated strategies
    page = int(settings.get("page_start", 1) or 1)
    max_pages = int(settings.get("max_pages", 500) or 500)
    delay = float(settings.get("delay_sec", 0.5) or 0)
    all_rows: list[dict] = []
    prev_fingerprint = None
    pages = 0
    while pages < max_pages:
        status, body, ct = fetch(_page_url(url, settings, page))
        pages += 1
        if status != 200 or not body:
            if pages == 1:
                raise RuntimeError(f"HTTP {status} از مقصد — پروکسی ایران در دسترس نیست یا آدرس اشتباه است")
            break
        if strategy == "json_api":
            try:
                doc = json.loads(body)
            except json.JSONDecodeError:
                break
            rows = _descend(doc, settings.get("record_path", ""))
        else:
            rows = _rows_from_bytes(body, ct, url)
        if not rows:
            break
        fingerprint = json.dumps(rows[:3], sort_keys=True, ensure_ascii=False, default=str)
        if fingerprint == prev_fingerprint:
            break                            # site ignores the page param → stop
        prev_fingerprint = fingerprint
        all_rows.extend(rows)
        page += 1
        if delay:
            time.sleep(delay)
    return all_rows, pages


# ── column-role resolution (saved overrides beat inference) ──────────────────
def resolve_roles(rows: list[dict], overrides: dict[str, str] | None) -> dict[str, str]:
    roles = infer_columns(rows)
    for col, role in (overrides or {}).items():
        if role == "ignore":
            roles.pop(col, None)
        elif rows and col in rows[0]:
            for existing_col, r in list(roles.items()):
                if r == role and existing_col != col:
                    del roles[existing_col]     # override claims the role uniquely
            roles[col] = role
    return roles


# ── diff (staged vs live coverage for one insurer) ───────────────────────────
def compute_diff(staged: dict, current: dict[str, dict], *, insurer: str) -> dict:
    """staged: irc → {insurer: entry}; current: irc → entry (live coverage[insurer])."""
    added, changed, removed = [], [], []
    for irc, per_insurer in staged.items():
        new = per_insurer.get(insurer) or {}
        old = current.get(irc)
        if old is None:
            added.append(irc)
            continue
        fields = [{"field": f, "old": old.get(f), "new": new.get(f)}
                  for f in _DIFF_FIELDS if old.get(f) != new.get(f)]
        if fields:
            changed.append({"irc": irc, "fields": fields})
    for irc in current:
        if irc not in staged:
            removed.append(irc)
    return {
        "added": len(added), "changed": len(changed), "removed": len(removed),
        "samples": {"added": added[:_SAMPLE_CAP],
                    "changed": changed[:_SAMPLE_CAP],
                    "removed": removed[:_SAMPLE_CAP]},
    }
