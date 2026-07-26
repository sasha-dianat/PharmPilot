"""Admin-dashboard NFI harvest: crawl /NFI/Detail/{id} in the background and
upsert products into the catalog as they arrive, with pol/pollable progress.

Single in-process job (one pharmacy workstation drives this). Requires an Iran
proxy — passed per-request from the GUI or taken from HTTPS_PROXY.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field, asdict

from . import harvest_lock
from .harvest_diagnostics import DiagnosticRecorder
from .importer import build_records, upsert_catalog
from .nfi import BASE, fetch_detail, fetch_detail_raw, make_opener, parse_detail


@dataclass
class HarvestState:
    running: bool = False
    cancel: bool = False
    start_id: int = 0
    end_id: int = 0
    last_id: int = 0
    scanned: int = 0
    products: int = 0
    ingested: int = 0
    quarantined: int = 0         # spliced-page monographs stripped this run
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    message: str = ""
    diagnostics: dict = field(default_factory=dict)

    def snapshot(self) -> dict:
        d = asdict(self)
        total = max(1, self.end_id - self.start_id + 1)
        d["progress_pct"] = round(100 * self.scanned / total, 1) if self.running or self.finished_at else 0.0
        d["elapsed_sec"] = round((self.finished_at or time.time()) - self.started_at, 1) if self.started_at else 0.0
        if self.running and self.scanned and self.started_at:
            rate = self.scanned / max(1e-6, time.time() - self.started_at)
            remaining = max(0, self.end_id - self.last_id)
            d["eta_sec"] = round(remaining / rate, 0) if rate else None
        else:
            d["eta_sec"] = None
        return d


_STATE = HarvestState()
_TASK: asyncio.Task | None = None


def status() -> dict:
    d = _STATE.snapshot()
    d["lock_holder"] = harvest_lock.holder()
    return d


def is_running() -> bool:
    return _STATE.running


def request_stop() -> bool:
    if _STATE.running:
        _STATE.cancel = True
        _STATE.message = "stopping…"
        return True
    return False


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


# Transport failures worth re-fetching. A 5xx is excluded on purpose: here it
# overwhelmingly means the id does not exist (they arrive in long contiguous
# blocks — 58,979–70,000 in one run), so replaying them would re-burn the hours
# the retry pass exists to save.
RETRYABLE = ("dns_fail", "timeout", "tls_fail", "transport_error",
             "proxy_unreachable", "rate_limited")


async def record_failures(rows: list[dict], crawler: str = "nfi") -> int:
    """Upsert the pages this pass could not fetch, so a later pass can target
    only them. rows: [{page_id, category, http_status, error}]"""
    from datetime import datetime, timezone
    from sqlalchemy.dialects.postgresql import insert
    from services.platform.database import AsyncSessionLocal
    from shared.models.harvest_failure import HarvestFailure
    if not rows:
        return 0
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        for r in rows:
            stmt = insert(HarvestFailure).values(
                crawler=crawler, page_id=int(r["page_id"]),
                category=str(r.get("category") or "unknown")[:40],
                http_status=r.get("http_status"), attempts=1,
                last_error=(str(r.get("error"))[:300] if r.get("error") else None),
                first_seen=now, last_seen=now, resolved_at=None)
            await db.execute(stmt.on_conflict_do_update(
                constraint="uq_harvest_failure_page",
                set_={"category": stmt.excluded.category,
                      "http_status": stmt.excluded.http_status,
                      "last_error": stmt.excluded.last_error,
                      "last_seen": now, "resolved_at": None,
                      "attempts": HarvestFailure.attempts + 1}))
        await db.commit()
    return len(rows)


async def resolve_failures(page_ids: list[int], crawler: str = "nfi") -> int:
    """Mark ids that a later pass fetched successfully (kept, not deleted, so
    chronically unreachable pages stay visible)."""
    from datetime import datetime, timezone
    from sqlalchemy import update
    from services.platform.database import AsyncSessionLocal
    from shared.models.harvest_failure import HarvestFailure
    if not page_ids:
        return 0
    async with AsyncSessionLocal() as db:
        res = await db.execute(update(HarvestFailure)
                               .where(HarvestFailure.crawler == crawler,
                                      HarvestFailure.page_id.in_(page_ids),
                                      HarvestFailure.resolved_at.is_(None))
                               .values(resolved_at=datetime.now(timezone.utc)))
        await db.commit()
        return res.rowcount or 0


async def pending_failures(crawler: str = "nfi",
                           categories: tuple[str, ...] = RETRYABLE) -> list[int]:
    """Unresolved page ids worth retrying, oldest first."""
    from sqlalchemy import select
    from services.platform.database import AsyncSessionLocal
    from shared.models.harvest_failure import HarvestFailure
    async with AsyncSessionLocal() as db:
        q = select(HarvestFailure.page_id).where(
            HarvestFailure.crawler == crawler,
            HarvestFailure.resolved_at.is_(None))
        if categories:
            q = q.where(HarvestFailure.category.in_(list(categories)))
        return list((await db.execute(q.order_by(HarvestFailure.page_id))).scalars().all())


async def _flush(batch: list[dict], source: str) -> int:
    from services.platform.database import AsyncSessionLocal
    records = build_records(batch)
    if not records:
        return 0
    async with AsyncSessionLocal() as db:
        # X3: owner corrections re-assert themselves over each crawl's values.
        from .crosswalk import load_overrides
        overrides = await load_overrides(db)
        return await upsert_catalog(db, records, source=source, overrides=overrides)


async def _run(start: int, end: int, delay: float, proxy: str | None, source: str,
               page_ids: list[int] | None = None) -> None:
    from .nfi import page_coherence, quarantine_monograph
    from .nfi_integrity import load_vocab
    from .harvest_diagnostics import classify
    opener = make_opener(proxy or os.getenv("HTTPS_PROXY"))
    recorder = DiagnosticRecorder("nfi", "nfi", mode="errors_only")
    batch: list[dict] = []
    failures: list[dict] = []       # pages this pass lost, for a targeted retry
    fetched_ok: list[int] = []      # pages that succeeded, to clear prior failures
    try:
        from services.platform.database import AsyncSessionLocal
        async with AsyncSessionLocal() as _vdb:
            vocab, families = await load_vocab(_vdb)
        # `page_ids` drives a retry pass over only the previously-lost pages;
        # otherwise sweep the range.
        for pid in (page_ids if page_ids is not None else range(start, end + 1)):
            if _STATE.cancel:
                _STATE.message = "cancelled"
                break
            status_code, html = await asyncio.to_thread(_fetch_and_record, pid, opener, recorder)
            _STATE.diagnostics = recorder.summary()
            _STATE.scanned += 1
            _STATE.last_id = pid
            if status_code == 200 and html:
                fetched_ok.append(pid)
            else:
                cat, _sev, _hint = classify(status_code, {},
                                            html.encode("utf-8", "replace") if html else b"",
                                            None if status_code else "transport")
                if cat in RETRYABLE:
                    failures.append({"page_id": pid, "category": cat,
                                     "http_status": status_code or None})
            if len(failures) >= 200:
                await record_failures(failures); failures = []
            if len(fetched_ok) >= 500:
                await resolve_failures(fetched_ok); fetched_ok = []
            if status_code == 200 and html:
                rec = parse_detail(html, pid)
                if rec:
                    reasons = page_coherence(rec, vocab, families)
                    if reasons:
                        # the page contradicts itself (reused generic-entity id
                        # on legacy registrations) — keep the product block,
                        # drop the foreign monograph, let review sort it out
                        rec = quarantine_monograph(rec, reasons)
                        recorder.note("spliced_page", f"id {pid}: {'; '.join(reasons)}")
                        _STATE.quarantined += 1
                    batch.append(rec)
                    _STATE.products += 1
            if len(batch) >= 100:
                _STATE.ingested += await _flush(batch, source)
                batch = []
            if delay:
                await asyncio.sleep(delay)
        if batch:
            _STATE.ingested += await _flush(batch, source)
        await record_failures(failures)
        await resolve_failures(fetched_ok)
        if not _STATE.cancel:
            _STATE.message = "done"
    except Exception as e:  # pragma: no cover
        _STATE.error = f"{type(e).__name__}: {e}"
        _STATE.message = "failed"
    finally:
        try:
            recorder.close()
            _STATE.diagnostics = recorder.summary()
        except Exception:
            pass
        harvest_lock.release("nfi")
        _STATE.running = False
        _STATE.finished_at = time.time()


def start(start_id: int, end_id: int, *, delay: float = 0.25,
          proxy: str | None = None, source: str = "nfi-harvest",
          page_ids: list[int] | None = None) -> dict:
    """Kick off a background harvest. Raises if one is already running.
    `page_ids` runs a targeted RETRY over exactly those pages instead of a
    range — the whole point of the failure registry: recovering a few thousand
    proxy-dropped pages must not cost another full 70,000-page sweep."""
    global _TASK, _STATE
    if _STATE.running:
        raise RuntimeError("A harvest is already running.")
    if not harvest_lock.acquire("nfi"):
        raise RuntimeError(f"قفل برداشت در اختیار دیگری است: {harvest_lock.holder()}")
    if page_ids:
        start_id, end_id = min(page_ids), max(page_ids)
    _STATE = HarvestState(running=True, start_id=start_id, end_id=end_id,
                          last_id=start_id - 1, started_at=time.time(),
                          message=f"retry {len(page_ids)} failed pages" if page_ids
                                  else "running")
    _TASK = asyncio.create_task(_run(start_id, end_id, delay, proxy, source,
                                     page_ids=page_ids))
    return _STATE.snapshot()
