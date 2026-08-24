"""The nightly round: every engine, unasked, for every pharmacy.

The recommendation ledger was built to answer one question — *is each of these
engines any good?* — and it cannot, because nothing has ever run them. Every
engine files advice only when a human opens its tab with `raise_advice=true`, so
the acceptance rate the ledger exists to measure has no rows to measure, and a
detector that fires forty times a week is indistinguishable from one that has
never fired at all.

That is the same shape as the defect this whole section began with: machinery
built to be measured, never actually measured, quietly assumed to be working.

So this runs the engines the way a pharmacy would want them run: overnight,
before anybody arrives, so the morning starts with the queue already populated
and the expiry exposure already priced. Three properties matter more than the
scheduling:

**It decides nothing.** Each engine files *proposals*. Applying any of them still
goes through the ordinary approval and ledger paths, and the deterministic-first
rule is unchanged — rules decide, models advise.

**It cannot spam.** Every proposal carries a fingerprint, and `_record` skips
what is already open or was decided inside its cooldown. A sweep that re-raised
last night's advice would inflate the denominator the fingerprint exists to
protect, which would corrupt the very measurement this exists to enable.

**One pharmacy's failure is not the others'.** Each tenant is swept in its own
transaction and an exception is recorded against that tenant rather than ending
the run. A sweep that dies on tenant three and silently skips the rest would be
worse than no sweep, because the empty queues would look like clean shelves.

Follows the shape of `services/core/drug_catalog/scheduler.py`: an asyncio loop
started from the app lifespan behind an environment flag, plus a `run_once()` the
manual button and the tests both call.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import structlog

log = structlog.get_logger(__name__)

# Overnight, in the pharmacy's own small hours. Deliberately not midnight: the
# ledger's day boundary lives there and a sweep racing it would price the expiry
# exposure against a date that changes underneath it.
DEFAULT_HOUR = 3

# Engines that raise advice unasked. Each is (label, callable) resolved late, so
# importing this module does not drag every router into the process.
ENGINES = ("expiry_exposure", "shortage_warning", "supplier_scorecard")


@dataclass
class SweepResult:
    pharmacies: int = 0
    engines_run: int = 0
    written: int = 0
    suppressed: int = 0
    superseded: int = 0
    failures: list[dict] = field(default_factory=list)
    per_engine: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"pharmacies": self.pharmacies, "engines_run": self.engines_run,
                "written": self.written, "suppressed": self.suppressed,
                "superseded": self.superseded, "failures": list(self.failures),
                "per_engine": dict(self.per_engine),
                "explanation": (
                    f"{self.written} new recommendation(s) across "
                    f"{self.pharmacies} pharmacy(ies); {self.suppressed} withheld "
                    f"because somebody already decided them, {self.superseded} "
                    f"closed because the engine no longer proposes them."
                    + (f" {len(self.failures)} pharmacy(ies) failed and are named "
                       f"below — an empty queue must not be mistaken for a clean "
                       f"shelf." if self.failures else ""))}


class _Sweeper:
    """A staff stand-in for calling read endpoints without a logged-in person.

    The engines take the pharmacy from the caller's `Staff`, and a nightly job
    has no caller. Attribution matters, so recommendations written by a sweep
    carry `created_by = None` rather than borrowing somebody's identity: nobody
    was at the keyboard, and the row should say so.
    """
    def __init__(self, pharmacy_id):
        self.pharmacy_id = pharmacy_id
        self.id = None


async def sweep_pharmacy(db, pharmacy_id) -> dict:
    """Run every unasked engine for one pharmacy. Returns what it filed."""
    from services.platform.routers import inventory_integrity as IG

    staff = _Sweeper(pharmacy_id)
    calls = {
        "expiry_exposure": lambda: IG.expiry_exposure(
            return_window=90, raise_advice=True, staff=staff, db=db),
        "shortage_warning": lambda: IG.shortage_warning(
            window_days=IG.SUPPLIER_WINDOW_DAYS, raise_advice=True,
            staff=staff, db=db),
        "supplier_scorecard": lambda: IG.supplier_scorecard(
            raise_advice=True, window_days=IG.SUPPLIER_WINDOW_DAYS,
            staff=staff, db=db),
    }
    out: dict = {}
    for name in ENGINES:
        result = await calls[name]()
        out[name] = result.get("recommendations") or {
            "produced": 0, "written": 0, "superseded": 0, "suppressed": 0}
    return out


async def run_once(source: str = "nightly") -> SweepResult:
    """Sweep every pharmacy. One transaction each, and one failure is one row."""
    from sqlalchemy import text

    from services.platform.database import AsyncSessionLocal

    res = SweepResult()
    async with AsyncSessionLocal() as db:
        ids = [r[0] for r in (await db.execute(text(
            "SELECT id FROM pharmacies WHERE COALESCE(is_deleted, false) = false "
            "ORDER BY id"))).all()]

    for pid in ids:
        # A session per pharmacy: a rollback on one tenant must not discard
        # another's writes, and a long-lived session across all of them would
        # hold every row it touched until the last one finished.
        async with AsyncSessionLocal() as db:
            try:
                filed = await sweep_pharmacy(db, pid)
            except Exception as exc:                        # noqa: BLE001
                await db.rollback()
                log.warning("inventory_sweep.pharmacy_failed",
                            pharmacy=str(pid)[:8], error=str(exc))
                res.failures.append({"pharmacy_id": str(pid),
                                     "error": f"{type(exc).__name__}: {exc}"})
                continue
        res.pharmacies += 1
        for name, counts in filed.items():
            res.engines_run += 1
            slot = res.per_engine.setdefault(
                name, {"written": 0, "suppressed": 0, "superseded": 0})
            for k in ("written", "suppressed", "superseded"):
                slot[k] += int(counts.get(k) or 0)
                setattr(res, k, getattr(res, k) + int(counts.get(k) or 0))

    log.info("inventory_sweep.done", source=source, **{
        k: v for k, v in res.as_dict().items() if k != "explanation"})
    return res


def _seconds_until(hour: int) -> float:
    now = datetime.now()
    nxt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return (nxt - now).total_seconds()


async def nightly_loop() -> None:
    """Started from the app lifespan when INVENTORY_SWEEP_ENABLED is truthy."""
    hour = int(os.getenv("INVENTORY_SWEEP_HOUR", DEFAULT_HOUR))
    log.info("inventory_sweep.scheduled", hour=hour)
    while True:
        await asyncio.sleep(_seconds_until(hour))
        try:
            await run_once()
        except Exception as exc:                            # noqa: BLE001
            # The loop outlives a bad night. A scheduler that dies on one
            # exception stops silently, and nobody notices until somebody asks
            # why the queue has been empty for a fortnight.
            log.error("inventory_sweep.failed", error=str(exc))
