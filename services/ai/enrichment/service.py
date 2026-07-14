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
_THROTTLE_SEC = 1.5


def status() -> dict:
    return _STATE.snapshot()


async def run_batch(*, limit: int = 50, min_confidence: float = 0.7,
                    researcher: Optional[MistralResearcher] = None,
                    throttle_sec: float = _THROTTLE_SEC) -> dict:
    """Research up to `limit` worklist items and save suggestions. Returns the
    final snapshot. Raises RuntimeError if a batch is already running."""
    if _LOCK.locked():
        raise RuntimeError("یک اجرای پژوهش هم‌اکنون در حال انجام است")

    async with _LOCK:
        from services.platform.database import AsyncSessionLocal

        researcher = researcher or MistralResearcher()
        _reset_state()
        _STATE.running = True
        _STATE.phase = "researching"
        _STATE.started_at = time.time()
        try:
            async with AsyncSessionLocal() as db:
                worklist = await build_worklist(db, min_confidence=min_confidence)
            _STATE.total = min(len(worklist), limit)

            for item in worklist[:limit]:
                raw_name = item.get("raw_name") or ""
                _STATE.current = raw_name
                suggestion, errors = await asyncio.to_thread(researcher.research, raw_name)
                if suggestion is None:
                    _STATE.failed += 1
                    _push_recent(raw_name, "failed: " + "; ".join(errors[:2]))
                else:
                    async with AsyncSessionLocal() as db:
                        row = await save_suggestion(db, raw_name, suggestion)
                    if row is None:
                        _STATE.skipped += 1
                        _push_recent(raw_name, "skipped (already decided)")
                    else:
                        _STATE.saved += 1
                        _push_recent(raw_name, "suggested")
                _STATE.done += 1
                if throttle_sec:
                    await asyncio.sleep(throttle_sec)

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
