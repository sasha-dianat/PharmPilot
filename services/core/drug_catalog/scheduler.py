"""Daily price-sync scheduler.

A lightweight asyncio loop (started from the app lifespan when
PRICE_SYNC_ENABLED is truthy) that, once a day at PRICE_SYNC_HOUR, pulls the
price feed and turns it into pending proposals — it never auto-applies prices,
so a manager still approves. Also exposes run_once() for the manual 'Run sync'
button and for tests.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta

import structlog

log = structlog.get_logger(__name__)


async def run_once(source: str = "daily-auto") -> dict:
    """Fetch the feed and create proposals in one DB session."""
    from services.platform.database import AsyncSessionLocal
    from .feed import fetch_daily_feed
    from .sync_service import run_sync

    feed = await fetch_daily_feed()
    if not feed:
        return {"feed_rows": 0, "proposals_created": 0, "note": "empty feed"}
    async with AsyncSessionLocal() as db:
        return await run_sync(db, feed, source=source)


def _seconds_until(hour: int) -> float:
    now = datetime.now()
    nxt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return (nxt - now).total_seconds()


async def daily_loop() -> None:
    hour = int(os.getenv("PRICE_SYNC_HOUR", "3"))
    log.info("price-sync scheduler armed", hour=hour)
    while True:
        try:
            await asyncio.sleep(_seconds_until(hour))
            result = await run_once("daily-auto")
            log.info("daily price-sync ran", **result)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("daily price-sync failed")
        await asyncio.sleep(60)   # guard against a same-minute double fire
