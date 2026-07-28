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
from .harvest_diagnostics import DiagnosticRecorder, wrap_fetch

# entry fields that constitute a real coverage change (match metadata excluded)
_DIFF_FIELDS = ("covered", "share_pct", "reference_price", "ceiling")
_SAMPLE_CAP = 50

STRATEGIES = ("auto", "file_url", "html_table", "paginated_html", "json_api", "manual")


# ── fetching ─────────────────────────────────────────────────────────────────
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
            ct = e.headers.get_content_type() if hasattr(e.headers, "get_content_type") else ""
            return e.code, body, ct, dict(e.headers or {})
    return raw_fetch


def make_fetcher(proxy: str | None = None, recorder=None, timeout: int = 30):
    """fetch(url) -> (status, body, ct). Records every attempt when a recorder is
    given; preserves the (0, b'', '') transport-failure contract either way."""
    return wrap_fetch(make_raw_fetch(proxy, timeout), recorder)


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


def _json_rows(doc) -> list[dict]:
    """Record list from an uploaded JSON document: a bare list of dicts, or the
    LARGEST list-of-dicts found one level deep (harvest exports wrap rows in a
    container key we shouldn't have to know the name of)."""
    if isinstance(doc, list):
        return [r for r in doc if isinstance(r, dict)]
    best: list[dict] = []
    if isinstance(doc, dict):
        for v in doc.values():
            if isinstance(v, list):
                rows = [r for r in v if isinstance(r, dict)]
                if len(rows) > len(best):
                    best = rows
    return best


def rows_from_upload(filename: str, body: bytes) -> list[dict]:
    """Rows from a manually uploaded file. Adds .json to the formats the URL
    strategies already handle (Excel/CSV/HTML via read_table)."""
    name = (filename or "").lower()
    head = body.lstrip()[:1]
    if name.endswith(".json") or head in (b"{", b"["):
        try:
            return _json_rows(json.loads(body))
        except json.JSONDecodeError:
            if name.endswith(".json"):
                return []
    return _rows_from_bytes(body, "", filename or "upload.csv")


def merge_row_sets(row_sets: list[list[dict]]) -> list[dict]:
    """Merge rows from several files of the SAME formulary (e.g. tamin's .json
    + .csv exports): rows sharing an insurer code become ONE row whose empty
    fields are filled from the later file — earlier files win conflicts, so
    select the richest file first. Codeless rows are kept, deduped by name."""
    from .crosswalk import row_source_code
    if len(row_sets) <= 1:
        return row_sets[0] if row_sets else []
    by_code: dict[str, dict] = {}
    codeless: list[dict] = []
    seen_names: set[str] = set()
    for rows in row_sets:
        for r in rows:
            code = row_source_code(r)
            if code:
                base = by_code.get(code)
                if base is None:
                    by_code[code] = dict(r)
                else:
                    for k, v in r.items():
                        if v not in (None, "") and base.get(k) in (None, ""):
                            base[k] = v
            else:
                name = next((str(r[f]).strip() for f in
                             ("drug_name", "name", "generic_name", "title")
                             if r.get(f)), "")
                if name and name in seen_names:
                    continue
                if name:
                    seen_names.add(name)
                codeless.append(r)
    return list(by_code.values()) + codeless


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


# ── data-quality: reference-vs-announced price divergence ────────────────────
def price_conflict(irc, name_fa, announced, reference, threshold_pct):
    """Flag an insurer reference price that diverges from the announced (NFI)
    price by at least ``threshold_pct``.

    Returns ``{irc, name_fa, announced_price:int, reference_price:int,
    gap_pct:float}`` when ``abs((reference-announced)/announced*100) >=
    threshold_pct``, else ``None``. ``gap_pct`` is signed and rounded to 1 dp.
    Returns ``None`` when ``announced`` is falsy/<=0, ``reference`` is None, or
    either value is non-numeric. Pure — no I/O."""
    if not announced:
        return None
    try:
        if reference is None:
            return None
        a = float(announced)
        r = float(reference)
    except (TypeError, ValueError):
        return None
    if a <= 0:
        return None
    gap = (r - a) / a * 100.0
    if abs(gap) < float(threshold_pct):
        return None
    return {"irc": irc, "name_fa": name_fa,
            "announced_price": int(a), "reference_price": int(r),
            "gap_pct": round(gap, 1)}


def _as_num(v):
    """Coerce to float, tolerating Decimal/str; None/non-numeric → None."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def diagnose_discrepancy(catalog: dict, entry: dict, siblings: list) -> list[str]:
    """Human Persian root-cause hints for one drug's coverage entry. Pure.

    ``catalog``: {announced_price, strength, country, generic_name, atc, ...}
    ``entry``:   the insurer coverage dict {reference_price, match_confidence,
                 match_method, ...} or {} when the drug has no entry for the insurer.
    ``siblings``:[{name_fa, strength, announced_price, reference_price}] — same
                 generic, other products (reference_price is this insurer's ref).

    Appends a hint per condition that holds; returns [] when everything is clean.
    """
    catalog = catalog or {}
    entry = entry or {}
    siblings = siblings or []
    hints: list[str] = []

    announced = _as_num(catalog.get("announced_price"))
    reference = _as_num(entry.get("reference_price"))
    strength = catalog.get("strength")

    # ── price conflict: reference diverges from the announced NFI price ──
    if announced and announced > 0 and reference is not None:
        ratio = reference / announced
        if ratio > 2 or ratio < 0.5:
            ratio_s = round(ratio, 1)
            ref_s = f"{int(reference):,}"
            cross = None
            for sib in siblings:
                sib_ref = _as_num(sib.get("reference_price"))
                if sib_ref is None or sib_ref <= 0:
                    continue
                if abs(sib_ref - reference) / reference <= 0.05 \
                        and sib.get("strength") != strength:
                    cross = sib
                    break
            conf = _as_num(entry.get("match_confidence"))
            if cross is not None:
                hints.append(
                    f"مرجع بیمه با هم‌مولکول {cross.get('strength')} یکسان است "
                    f"({ref_s}) ولی قدرت این قلم {strength} است — احتمال تطبیق بین‌قدرتی.")
            elif conf is not None and conf < 0.75:
                hints.append(
                    f"تطبیق کم‌اطمینان ({entry.get('match_confidence')}، "
                    f"{entry.get('match_method')}) — بازبینی شود.")
            else:
                ann_s = f"{int(announced):,}"
                hints.append(
                    f"مرجع بیمه {ref_s} در برابر قیمت اعلامی {ann_s} ({ratio_s}× ) "
                    f"با تطبیق دقیق — احتمالاً قیمت اعلامی قدیمی است یا ردیف منبع خراب.")

    # ── catalog completeness gaps ──
    if catalog.get("country") is None:
        hints.append("کشور نامشخص — نیازمند خزش کامل NFI.")
    gen = catalog.get("generic_name")
    if gen is None or (isinstance(gen, str) and gen.strip() == ""):
        hints.append("ژنریک نامشخص.")
    if catalog.get("atc") is None:
        hints.append("کد ATC موجود نیست.")

    return hints


# ── probe (synchronous, one fetch, persists nothing) ─────────────────────────
def probe_payload(url: str, *, settings: dict, fetch) -> dict:
    status, body, ct = fetch(url)
    if status != 200 or not body:
        raise RuntimeError(f"HTTP {status} از مقصد — پروکسی ایران در دسترس نیست یا آدرس اشتباه است")
    strategy = sniff_strategy(body, ct, url)
    if strategy == "json_api":
        rows = _descend(json.loads(body), (settings or {}).get("record_path", ""))[:20]
    else:
        rows = _rows_from_bytes(body, ct, url)[:20]
    return {
        "detected_strategy": strategy,
        "proposed_settings": {"page_start": 1, "max_pages": 500, "delay_sec": 0.5},
        "sample_rows": rows[:20],
        "row_count_sampled": len(rows),
        "inferred_columns": resolve_roles(rows, (settings or {}).get("column_overrides")),
    }


# ── staging (pure: rows + catalog + current coverage → run payload) ──────────
def stage_run_payload(rows: list[dict], catalog: list, *, insurer: str,
                      overrides: dict | None, current: dict[str, dict],
                      min_confidence: float = 0.75,
                      enrichments: dict | None = None,
                      crosswalk: dict | None = None,
                      code_registry: dict | None = None) -> dict:
    roles = resolve_roles(rows, overrides)
    if "drug_name" not in roles.values() and "irc" not in roles.values():
        raise RuntimeError(
            f"ستون نام دارو یا IRC شناسایی نشد — ستون‌ها: {list(rows[0].keys())[:12] if rows else []}")
    normalized = normalize_rows(rows, roles)
    links = link_rows(normalized, catalog, enrichments=enrichments,
                      crosswalk=crosswalk, insurer=insurer,
                      code_registry=code_registry)
    # هوش تطبیق: the fitted model (owner-decision-calibrated) demotes matches
    # it distrusts — FS score or learned price band — into the review queue.
    from .match_intel import annotate_review_fs, load_model, verify_links
    model = load_model()
    intel_demoted = verify_links(links, model, insurer)
    cov = build_coverage(links, insurer=insurer, min_confidence=min_confidence,
                         catalog=catalog)
    by_irc = {r.irc: r for r in catalog}
    review = [{"id": i, **item, "accepted": False}
              for i, item in enumerate(annotate_review_fs(cov.review, by_irc, model))]
    # Ingredient-family pre-pass: group THIS run's rows by canonical active,
    # surfacing bulk candidates (a bare row beside finished-form siblings) and
    # multi-form families for grouped review. Deterministic, no DB.
    from .enrichment import classify_ingredient_groups
    row_names = [str(r.get("drug_name")) for r in normalized if r.get("drug_name")]
    groups = classify_ingredient_groups(row_names)
    bulk_candidates = sorted({m["name"] for g in groups.values()
                              for m in g["members"] if m["is_bulk"]})
    multiform = {k: g["forms"] for k, g in groups.items() if len(g["forms"]) > 1}
    return {
        "stats": {**cov.stats, "columns": roles, "intel_demoted": intel_demoted,
                  "ingredient_groups": len(groups),
                  "bulk_candidates": len(bulk_candidates)},
        "staged": cov.applied,
        "review": review,
        "unmatched": cov.unmatched[:200],
        "diff": compute_diff(cov.applied, current, insurer=insurer),
        "groups": {"bulk_candidates": bulk_candidates[:200],
                   "multiform": dict(list(multiform.items())[:200])},
        # X4: the FULL normalized row set for the observed-layer snapshot —
        # popped by the caller before the payload is stored on the run.
        "_normalized_rows": normalized,
        # X5: what the engine concluded per row. Stamped onto the snapshot so a
        # past run's behaviour stays comparable with today's, and turned into
        # origin='auto' decisions at apply time.
        "_verdicts": link_verdicts(links),
    }


def link_verdicts(links) -> list[dict]:
    """[{code, name, irc, confidence, method}] — one per linked row, matched or
    not. Pure; the row itself carries the insurer's own code."""
    from .crosswalk import row_source_code
    out = []
    for l in links:
        name = (l.row or {}).get("drug_name")
        if not name:
            continue
        out.append({"code": row_source_code(l.row), "name": str(name),
                    "irc": l.record.irc if l.record is not None else None,
                    "confidence": l.confidence, "method": l.method})
    return out


async def save_snapshots(db, run_id, insurer: str, normalized: list[dict],
                         verdicts: list[dict] | None = None) -> int:
    """Observed layer (X4): keep every row of this import, uncapped, with the
    insurer's own code and the raw mapped row — so any publication can later be
    replayed/diffed against the decided layer. Append-only."""
    from shared.models.formulary_snapshot import FormularySnapshot
    from .coverage_import import _to_bool, parse_conditions
    from .crosswalk import row_source_code

    # first numeric token — matches coverage_import._num so a multi-value cell
    # (tamin "70%\r90%") is captured as 70, not dropped to None
    from .coverage_import import _num

    # verdict per row, keyed the same way the linker sees the row
    by_row: dict[tuple, dict] = {}
    for v in verdicts or []:
        by_row[(v.get("code"), v.get("name"))] = v

    n = 0
    for r in normalized or []:
        if not isinstance(r, dict) or not r.get("drug_name"):
            continue
        v = by_row.get((row_source_code(r), str(r.get("drug_name")))) or {}
        rp = _num(r.get("reference_price"))
        # the شرایط تعهد text is authoritative for both flags: it is where a row
        # is declared غیر بیمه‌ای, and where the real organization share appears
        cond = parse_conditions(r.get("conditions"))
        if cond.get("covered") is not None:
            covered = cond["covered"]
        elif r.get("covered") not in (None, ""):
            covered = _to_bool(r.get("covered"))
        else:
            covered = None
        share = cond.get("share_pct")
        if share is None:
            share = _num(r.get("share_pct"))
        db.add(FormularySnapshot(
            run_id=run_id, insurer=insurer,
            source_code=row_source_code(r),
            raw_name=str(r.get("drug_name"))[:300],
            reference_price=int(rp) if rp is not None else None,
            share_pct=share,
            covered=covered,
            row=r,
            matched_irc=v.get("irc"),
            match_confidence=v.get("confidence"),
            match_method=(str(v.get("method"))[:32] if v.get("method") else None)))
        n += 1
    return n


# ── background run state (mirrors nfi_harvest_service) ───────────────────────
@dataclass
class CoverageHarvestState:
    running: bool = False
    source_id: str = ""
    insurer: str = ""
    phase: str = ""              # fetching | linking | diffing | saving | done | failed
    pages: int = 0
    rows: int = 0
    run_id: str | None = None
    error: str | None = None
    started_at: float | None = None
    finished_at: float | None = None

    def snapshot(self) -> dict:
        d = asdict(self)
        d["lock_holder"] = harvest_lock.holder()
        d["elapsed_sec"] = round((self.finished_at or time.time()) - self.started_at, 1) if self.started_at else 0.0
        return d


_STATE = CoverageHarvestState()
_UPLOAD_ROWS: list[dict] | None = None      # handoff: start_upload → _run("manual")


def status() -> dict:
    return _STATE.snapshot()


async def _current_coverage(db, insurer: str) -> dict[str, dict]:
    from sqlalchemy import select
    from shared.models.drug_catalog import DrugCatalogItem
    rows = (await db.execute(
        select(DrugCatalogItem.irc, DrugCatalogItem.coverage)
        .where(DrugCatalogItem.coverage.isnot(None)))).all()
    out = {}
    for irc, cov in rows:
        if isinstance(cov, dict) and insurer in cov:
            out[irc] = cov[insurer]
    return out


async def _run(source_id) -> None:
    from datetime import datetime, timezone
    from sqlalchemy import select
    from services.platform.database import AsyncSessionLocal
    from shared.models.coverage import CoverageRun, CoverageSource
    from . import repo

    owner = f"coverage:{_STATE.insurer}"
    recorder = None
    run_id = None
    try:
        async with AsyncSessionLocal() as db:
            src = (await db.execute(select(CoverageSource)
                                    .where(CoverageSource.id == source_id))).scalar_one()
            run = CoverageRun(source_id=src.id, insurer=src.insurer, status="running",
                              started_at=datetime.now(timezone.utc))
            db.add(run)
            await db.commit()
            await db.refresh(run)
            run_id = run.id
            _STATE.run_id = str(run_id)

            settings = src.settings or {}
            recorder = DiagnosticRecorder("coverage", src.insurer, mode="all")
            import asyncio
            if src.strategy == "manual":
                # uploaded rows were handed off by start_upload — no network
                global _UPLOAD_ROWS
                rows, pages = (_UPLOAD_ROWS or []), 0
                _UPLOAD_ROWS = None
                recorder.note("manual_upload",
                              f"{len(rows)} ردیف از فایل(های) بارگذاری‌شده: "
                              f"{settings.get('last_upload', '')}")
            else:
                fetch = make_fetcher(settings.get("proxy"), recorder=recorder)
                strategy = src.strategy
                if strategy == "auto":
                    s, body, ct = fetch(src.url)
                    if s != 200 or not body:
                        raise RuntimeError(f"HTTP {s} از مقصد — پروکسی ایران در دسترس نیست یا آدرس اشتباه است")
                    strategy = sniff_strategy(body, ct, src.url)

                _STATE.phase = "fetching"
                rows, pages = await asyncio.to_thread(fetch_rows, src.url, strategy, settings, fetch)
            _STATE.pages, _STATE.rows = pages, len(rows)
            if not rows:
                recorder.note("empty_result", "دریافت شد ولی هیچ ردیفی استخراج نشد.")

            _STATE.phase = "linking"
            catalog = await repo.fetch_all(db)
            current = await _current_coverage(db, src.insurer)
            from .crosswalk import build_code_registry, load_crosswalk
            from .enrichment import load_approved
            enrichments = await load_approved(db)
            crosswalk = await load_crosswalk(db, src.insurer)
            code_registry = await build_code_registry(db)
            _STATE.phase = "diffing"
            try:
                payload = await asyncio.to_thread(
                    stage_run_payload, rows, catalog,
                    insurer=src.insurer, overrides=settings.get("column_overrides"),
                    current=current, enrichments=enrichments, crosswalk=crosswalk,
                    code_registry=code_registry)
            except Exception as pe:
                recorder.note("parse_fail", f"{type(pe).__name__}: {pe}")
                raise

            _STATE.phase = "saving"
            # X4: persist the observed layer before the payload is stored
            snap_rows = payload.pop("_normalized_rows", None)
            verdicts = payload.pop("_verdicts", None)
            snapped = await save_snapshots(db, run.id, src.insurer, snap_rows, verdicts)
            payload["stats"]["snapshot_rows"] = snapped
            run.status = "parsed"
            run.finished_at = datetime.now(timezone.utc)
            run.stats, run.staged = payload["stats"], payload["staged"]
            run.review, run.unmatched, run.diff = payload["review"], payload["unmatched"], payload["diff"]
            recorder.close()
            run.diagnostics = recorder.to_db()
            src.last_run_at, src.last_run_status = run.finished_at, "parsed"
            await db.commit()
            _STATE.phase = "done"
    except Exception as e:
        _STATE.error = f"{type(e).__name__}: {e}"
        _STATE.phase = "failed"
        try:
            async with AsyncSessionLocal() as db:
                from shared.models.coverage import CoverageRun as CR, CoverageSource as CS
                from sqlalchemy import select as _sel
                if run_id is not None:
                    r = (await db.execute(_sel(CR).where(CR.id == run_id))).scalar_one_or_none()
                    if r:
                        r.status, r.error = "failed", _STATE.error
                    if r and recorder:
                        try:
                            recorder.close()
                            r.diagnostics = recorder.to_db()
                        except Exception:
                            pass
                s = (await db.execute(_sel(CS).where(CS.id == source_id))).scalar_one_or_none()
                if s:
                    from datetime import datetime as _dt, timezone as _tz
                    s.last_run_at, s.last_run_status = _dt.now(_tz.utc), "failed"
                await db.commit()
        except Exception:
            pass
    finally:
        harvest_lock.release(owner)
        _STATE.running = False
        _STATE.finished_at = time.time()


def start_harvest(source_id, insurer: str) -> dict:
    """Kick off a background دارونامه harvest. Raises RuntimeError if the global
    harvest lock is held (NFI crawl or another coverage job)."""
    global _STATE
    import asyncio
    if _STATE.running:
        raise RuntimeError("یک برداشت پوشش در حال اجراست.")
    owner = f"coverage:{insurer}"
    if not harvest_lock.acquire(owner):
        raise RuntimeError(f"قفل برداشت در اختیار دیگری است: {harvest_lock.holder()}")
    _STATE = CoverageHarvestState(running=True, source_id=str(source_id),
                                  insurer=insurer, phase="starting",
                                  started_at=time.time())
    asyncio.create_task(_run(source_id))
    return _STATE.snapshot()


def detect_insurer_mismatch(names: list[str], known: dict[str, set[str]],
                            selected: str) -> dict | None:
    """Does this file's content look like ANOTHER insurer's دارونامه?

    A mislabeled upload is silently corrosive: its rows enter the observed layer
    under the wrong insurer and pollute the national-code registry the resolver
    reads (a salamat file was once staged as tamin — 3,679 rows, every code
    salamat's). Exact NAME overlap discriminates cleanly, because each insurer
    writes product names its own way (salamat truncates, tamin is structured).
    Returns None when the selection is consistent."""
    probe = {str(n).strip().upper() for n in names if str(n).strip()}
    if len(probe) < 20:
        return None                       # too small to judge
    frac = {ins: len(probe & {v.upper() for v in vals}) / len(probe)
            for ins, vals in (known or {}).items() if vals}
    if not frac:
        return None
    best = max(frac, key=lambda k: frac[k])
    mine = frac.get(selected, 0.0)
    if best != selected and frac[best] >= 0.5 and frac[best] >= mine + 0.25:
        return {"looks_like": best, "match_pct": round(100 * frac[best]),
                "selected_pct": round(100 * mine)}
    return None


async def start_upload(db, insurer: str, rows: list[dict], filenames: list[str],
                       *, force: bool = False) -> dict:
    """Stage a manually uploaded دارونامه through the SAME pipeline as a crawl
    (crosswalk → code registry → linking → snapshots → review run) instead of
    the legacy instant-apply path. Uses a per-insurer synthetic source
    (strategy='manual', disabled) so the run has a home in اجراها."""
    global _STATE, _UPLOAD_ROWS
    import asyncio
    from sqlalchemy import select
    from shared.models.coverage import CoverageSource
    from shared.models.formulary_snapshot import FormularySnapshot

    if _STATE.running:
        raise RuntimeError("یک برداشت پوشش در حال اجراست.")
    if not rows:
        raise RuntimeError("هیچ ردیفی از فایل(ها) استخراج نشد.")
    if not force:
        known: dict[str, set[str]] = {}
        for ins, nm in (await db.execute(select(
                FormularySnapshot.insurer, FormularySnapshot.raw_name).distinct())).all():
            if nm:
                known.setdefault(ins, set()).add(nm)
        names = [r.get("drug_name") or r.get("name") or "" for r in rows
                 if isinstance(r, dict)]
        bad = detect_insurer_mismatch(names, known, insurer)
        if bad:
            raise RuntimeError(
                f"محتوای فایل با بیمه‌گر انتخاب‌شده هم‌خوان نیست: "
                f"{bad['match_pct']}٪ نام‌ها با «{bad['looks_like']}» مطابقت دارد "
                f"و تنها {bad['selected_pct']}٪ با «{insurer}». "
                f"بیمه‌گر را اصلاح کنید یا با تأیید اجباری ادامه دهید.")
    src = (await db.execute(select(CoverageSource).where(
        CoverageSource.insurer == insurer,
        CoverageSource.strategy == "manual"))).scalar_one_or_none()
    if src is None:
        src = CoverageSource(insurer=insurer, name=f"بارگذاری دستی ({insurer})",
                             url=None, strategy="manual", enabled=False,
                             settings={})
        db.add(src)
    src.settings = {**(src.settings or {}), "last_upload": "، ".join(filenames)[:300]}
    await db.commit()
    await db.refresh(src)

    owner = f"coverage:{insurer}"
    if not harvest_lock.acquire(owner):
        raise RuntimeError(f"قفل برداشت در اختیار دیگری است: {harvest_lock.holder()}")
    _UPLOAD_ROWS = rows
    _STATE = CoverageHarvestState(running=True, source_id=str(src.id),
                                  insurer=insurer, phase="starting",
                                  rows=len(rows), started_at=time.time())
    asyncio.create_task(_run(src.id))
    return _STATE.snapshot()


# ── approve / reject ─────────────────────────────────────────────────────────
# Owner-teachable rejection vocabulary — codes map 1:1 onto هوش تطبیق features,
# so a coded rejection is a feature-targeted training label, not just a "no".
REJECT_REASONS = ("wrong_product", "wrong_strength", "wrong_form",
                  "wrong_brand", "wrong_pack", "price_implausible", "other")


def stamp_reject_reasons(review: list, accepted: set,
                         reasons: dict | None) -> list:
    """Return a new review list with reject_reason/reject_note stamped onto
    non-accepted items (JSONB → must reassign, never mutate in place)."""
    out = []
    for item in (review or []):
        if isinstance(item, dict) and item.get("id") not in accepted:
            r = (reasons or {}).get(str(item.get("id"))) or {}
            code = r.get("code")
            if code in REJECT_REASONS or r.get("note"):
                item = {**item,
                        **({"reject_reason": code} if code in REJECT_REASONS else {}),
                        **({"reject_note": str(r["note"])[:300]} if r.get("note") else {})}
        out.append(item)
    return out


async def apply_run(db, run_id, *, remove_missing: bool = False,
                    accepted_review_ids: list[int] | None = None,
                    reject_reasons: dict | None = None,
                    staff_id=None) -> dict:
    from datetime import datetime, timezone
    from sqlalchemy import select
    from shared.models.coverage import CoverageRun
    from shared.models.drug_catalog import DrugCatalogItem
    from .coverage_import import apply_coverage

    run = (await db.execute(select(CoverageRun).where(CoverageRun.id == run_id))).scalar_one()
    if run.status != "parsed":
        raise RuntimeError(f"فقط اجرای parsed قابل اعمال است (وضعیت فعلی: {run.status})")

    staged = dict(run.staged or {})
    accepted = set(accepted_review_ids or [])
    review_applied = 0
    for item in (run.review or []):
        if item["id"] in accepted:
            # spread the accepted entry across the matched product's ingredient group
            target = (await db.execute(select(DrugCatalogItem).where(
                DrugCatalogItem.irc == item["irc"]))).scalar_one_or_none()
            if not target:
                continue
            group = (await db.execute(select(DrugCatalogItem.irc).where(
                DrugCatalogItem.ingredient_key == target.ingredient_key))).scalars().all()
            for irc in group or [item["irc"]]:
                staged.setdefault(irc, {})[run.insurer] = item["entry"]
            review_applied += 1

    updated = await apply_coverage(db, staged)

    removed_cleared = 0
    if remove_missing:
        for irc in (run.diff or {}).get("samples", {}).get("removed", []):
            row = (await db.execute(select(DrugCatalogItem).where(
                DrugCatalogItem.irc == irc))).scalar_one_or_none()
            if row and isinstance(row.coverage, dict) and run.insurer in row.coverage:
                cov = dict(row.coverage)
                cov.pop(run.insurer)
                row.coverage = cov or None
                removed_cleared += 1

    run.review = stamp_reject_reasons(run.review, accepted, reject_reasons)
    # X1: the owner's verdicts become DURABLE decisions — the next import of the
    # same rows resolves from the crosswalk instead of re-running the matcher.
    from .crosswalk import record_auto_decisions, record_run_decisions
    cw = await record_run_decisions(db, run, accepted, staff_id=staff_id)
    # X5: and so do the engine's OWN accepted links. Before this, only reviewed
    # rows left a trace, so ~38,000 auto-applied links were re-derived on every
    # import and could move without anyone seeing it.
    auto = await record_auto_decisions(db, insurer=run.insurer,
                                       verdicts=await run_verdicts(db, run.id))
    run.status = "approved"
    run.applied_by = staff_id
    run.applied_at = datetime.now(timezone.utc)
    await db.commit()
    return {"products_updated": updated, "review_applied": review_applied,
            "removed_cleared": removed_cleared, **cw, **auto}


async def run_verdicts(db, run_id) -> list[dict]:
    """The engine's per-row conclusions for one run, read back from the observed
    layer. Runs staged before the snapshot carried a verdict simply yield
    nothing — the backfill covers those."""
    from sqlalchemy import select
    from shared.models.formulary_snapshot import FormularySnapshot
    rows = (await db.execute(
        select(FormularySnapshot.source_code, FormularySnapshot.raw_name,
               FormularySnapshot.matched_irc, FormularySnapshot.match_confidence,
               FormularySnapshot.match_method)
        .where(FormularySnapshot.run_id == run_id,
               FormularySnapshot.matched_irc.isnot(None)))).all()
    return [{"code": c, "name": n, "irc": irc, "confidence": conf, "method": m}
            for c, n, irc, conf, m in rows]


async def reject_run(db, run_id) -> None:
    from sqlalchemy import select
    from shared.models.coverage import CoverageRun
    run = (await db.execute(select(CoverageRun).where(CoverageRun.id == run_id))).scalar_one()
    if run.status != "parsed":
        raise RuntimeError(f"فقط اجرای parsed قابل رد است (وضعیت فعلی: {run.status})")
    run.status = "rejected"
    await db.commit()
