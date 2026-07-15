"""Background batch enrichment — walk the deterministic worklist, research each
drug via Mistral, and persist SUGGESTED rows for owner review.

Single-flight (one batch at a time) with an in-memory progress snapshot the GUI
polls, mirroring coverage_harvest's state pattern. Runs are advisory: everything
produced is status='suggested' and has zero effect until approved.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from services.core.drug_catalog.enrichment import build_worklist
from .mistral_researcher import MistralResearcher, save_suggestion


@dataclass
class EnrichmentBatchState:
    running: bool = False
    phase: str = "idle"          # idle | researching | done | failed
    total: int = 0
    done: int = 0
    saved: int = 0
    skipped: int = 0
    failed: int = 0
    workers: int = 1
    current: str = ""
    error: Optional[str] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    recent: list = field(default_factory=list)   # last few {name, result}

    def snapshot(self) -> dict:
        d = asdict(self)
        d["elapsed_sec"] = round((self.finished_at or time.time()) - self.started_at, 1) \
            if self.started_at else 0.0
        return d


_STATE = EnrichmentBatchState()
_LOCK = asyncio.Lock()

# Politeness delay between web-search calls (Mistral rate limits + web etiquette).
_THROTTLE_SEC = 3.0
# Escalating waits after a 429 from the web_search connector — its rate window
# is coarser than the chat API's, so short waits don't clear it.
_RATE_BACKOFFS = (20.0, 45.0, 90.0)


def status() -> dict:
    return _STATE.snapshot()


def _is_rate_limited(errors: list[str]) -> bool:
    txt = " ".join(errors).lower()
    return "429" in txt or "rate limit" in txt


class _RateGate:
    """Shared cooldown for concurrent workers: the FIRST worker to hit a 429
    closes the gate for everyone, so the pool waits the window out together
    instead of N workers independently hammering the same limit."""

    def __init__(self):
        self.open_at = 0.0

    def pause(self, seconds: float) -> None:
        self.open_at = max(self.open_at, time.time() + seconds)

    async def wait(self) -> None:
        while True:
            delta = self.open_at - time.time()
            if delta <= 0:
                return
            await asyncio.sleep(min(delta, 1.0))


async def _research_with_backoff(researcher: MistralResearcher, raw_name: str,
                                 backoffs=_RATE_BACKOFFS, gate: _RateGate | None = None):
    """research() once, retrying only 429/rate-limit failures with escalating
    waits. Non-rate-limit failures return immediately (retrying won't fix a
    bad name). With a gate, the cooldown is shared across the pool.
    → (suggestion|None, errors)."""
    if gate:
        await gate.wait()
    suggestion, errors = await asyncio.to_thread(researcher.research, raw_name)
    for wait in backoffs:
        if suggestion is not None or not _is_rate_limited(errors):
            break
        if gate:
            gate.pause(wait)
            _STATE.current = f"محدودیت نرخ؛ {int(wait)}s توقف همگانی…"
            await gate.wait()
        else:
            _STATE.current = f"{raw_name} — محدودیت نرخ؛ {int(wait)}s توقف…"
            await asyncio.sleep(wait)
        _STATE.current = raw_name
        suggestion, errors = await asyncio.to_thread(researcher.research, raw_name)
    return suggestion, errors


async def _run_pool(items: list[dict], researcher: MistralResearcher, *,
                    workers: int, throttle_sec: float, save) -> None:
    """Drain `items` through a pool of concurrent workers. `save` is an async
    (raw_name, suggestion) → row|None callable (injected so tests need no DB).
    Per-item exceptions are counted as failures, never abort the pool."""
    queue: asyncio.Queue = asyncio.Queue()
    for it in items:
        queue.put_nowait(it)
    gate = _RateGate()
    active: list[str] = []

    def show() -> None:
        _STATE.current = (f"{len(active)} فعال: " + "، ".join(active[-4:])) if active else ""

    async def worker() -> None:
        while True:
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            raw_name = item.get("raw_name") or ""
            active.append(raw_name)
            show()
            try:
                suggestion, errors = await _research_with_backoff(
                    researcher, raw_name, gate=gate)
                if suggestion is None:
                    _STATE.failed += 1
                    _push_recent(raw_name, "failed: " + "; ".join(errors[:2]))
                else:
                    row = await save(raw_name, suggestion)
                    if row is None:
                        _STATE.skipped += 1
                        _push_recent(raw_name, "skipped (already decided)")
                    else:
                        _STATE.saved += 1
                        _push_recent(raw_name, "suggested")
            except Exception as e:  # a save/DB error fails the item, not the run
                _STATE.failed += 1
                _push_recent(raw_name, f"failed: {type(e).__name__}: {e}")
            finally:
                if raw_name in active:
                    active.remove(raw_name)
                show()
                _STATE.done += 1
            if throttle_sec:
                await asyncio.sleep(throttle_sec)

    await asyncio.gather(*(worker() for _ in range(max(1, workers))))


async def run_batch(*, limit: int = 50, min_confidence: float = 0.7,
                    researcher: Optional[MistralResearcher] = None,
                    throttle_sec: float = _THROTTLE_SEC,
                    workers: int = 5) -> dict:
    """Research up to `limit` worklist items through `workers` concurrent
    researchers (clamped 1–15) and save suggestions. Returns the final
    snapshot. Raises RuntimeError if a batch is already running."""
    if _LOCK.locked():
        raise RuntimeError("یک اجرای پژوهش هم‌اکنون در حال انجام است")

    async with _LOCK:
        from services.platform.database import AsyncSessionLocal

        researcher = researcher or MistralResearcher()
        workers = max(1, min(int(workers), 15))
        _reset_state()
        _STATE.running = True
        _STATE.phase = "researching"
        _STATE.workers = workers
        _STATE.started_at = time.time()
        try:
            async with AsyncSessionLocal() as db:
                worklist = await build_worklist(db, min_confidence=min_confidence)
            _STATE.total = min(len(worklist), limit)

            async def save(raw_name: str, suggestion: dict):
                async with AsyncSessionLocal() as db:
                    return await save_suggestion(db, raw_name, suggestion)

            await _run_pool(worklist[:limit], researcher,
                            workers=workers, throttle_sec=throttle_sec, save=save)

            _STATE.phase = "done"
        except Exception as e:
            _STATE.phase = "failed"
            _STATE.error = f"{type(e).__name__}: {e}"
            raise
        finally:
            _STATE.running = False
            _STATE.current = ""
            _STATE.finished_at = time.time()
        return _STATE.snapshot()


def start_batch_background(**kwargs) -> dict:
    """Fire-and-forget a batch run; returns the initial snapshot immediately."""
    if _STATE.running or _LOCK.locked():
        raise RuntimeError("یک اجرای پژوهش هم‌اکنون در حال انجام است")
    asyncio.create_task(_run_guarded(**kwargs))
    return {"running": True, "phase": "researching"}


async def _run_guarded(**kwargs) -> None:
    try:
        await run_batch(**kwargs)
    except Exception:
        pass  # state already records the failure


def _reset_state() -> None:
    global _STATE
    _STATE = EnrichmentBatchState()


def _push_recent(name: str, result: str) -> None:
    _STATE.recent.append({"name": name, "result": result})
    if len(_STATE.recent) > 20:
        _STATE.recent = _STATE.recent[-20:]
