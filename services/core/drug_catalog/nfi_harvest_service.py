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

from .importer import build_records, upsert_catalog
from .nfi import fetch_detail, make_opener, parse_detail


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
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    message: str = ""

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
    return _STATE.snapshot()


def is_running() -> bool:
    return _STATE.running


def request_stop() -> bool:
    if _STATE.running:
        _STATE.cancel = True
        _STATE.message = "stopping…"
        return True
    return False


async def _flush(batch: list[dict], source: str) -> int:
    from services.platform.database import AsyncSessionLocal
    records = build_records(batch)
    if not records:
        return 0
    async with AsyncSessionLocal() as db:
        return await upsert_catalog(db, records, source=source)


async def _run(start: int, end: int, delay: float, proxy: str | None, source: str) -> None:
    opener = make_opener(proxy or os.getenv("HTTPS_PROXY"))
    batch: list[dict] = []
    try:
        for pid in range(start, end + 1):
            if _STATE.cancel:
                _STATE.message = "cancelled"
                break
            status_code, html = await asyncio.to_thread(fetch_detail, pid, opener)
            _STATE.scanned += 1
            _STATE.last_id = pid
            if status_code == 200 and html:
                rec = parse_detail(html, pid)
                if rec:
                    batch.append(rec)
                    _STATE.products += 1
            if len(batch) >= 100:
                _STATE.ingested += await _flush(batch, source)
                batch = []
            if delay:
                await asyncio.sleep(delay)
        if batch:
            _STATE.ingested += await _flush(batch, source)
        if not _STATE.cancel:
            _STATE.message = "done"
    except Exception as e:  # pragma: no cover
        _STATE.error = f"{type(e).__name__}: {e}"
        _STATE.message = "failed"
    finally:
        _STATE.running = False
        _STATE.finished_at = time.time()


def start(start_id: int, end_id: int, *, delay: float = 0.25,
          proxy: str | None = None, source: str = "nfi-harvest") -> dict:
    """Kick off a background harvest. Raises if one is already running."""
    global _TASK, _STATE
    if _STATE.running:
        raise RuntimeError("A harvest is already running.")
    _STATE = HarvestState(running=True, start_id=start_id, end_id=end_id,
                          last_id=start_id - 1, started_at=time.time(),
                          message="running")
    _TASK = asyncio.create_task(_run(start_id, end_id, delay, proxy, source))
    return _STATE.snapshot()
