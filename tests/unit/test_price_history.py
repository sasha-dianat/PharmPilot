"""Phase C price-history — SCD type-2 recording, current lookup, stale query."""
import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest


def _db_or_skip():
    url = os.environ.get("DATABASE_URL",
        "postgresql+asyncpg://pharmpilot:pharmpilot_dev@127.0.0.1:5433/pharmpilot_test")
    try:
        from sqlalchemy.ext.asyncio import create_async_engine
        eng = create_async_engine(url)
        async def ping():
            async with eng.connect():
                pass
            await eng.dispose()
        asyncio.get_event_loop().run_until_complete(ping())
    except Exception:
        pytest.skip("dev DB unreachable")
    return url


def test_scd2_record_current_and_stale():
    url = _db_or_skip()
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from shared.models.price_history import PriceHistory
    from services.core.drug_catalog.price_history import (
        record_price, current_price, stale_prices)

    irc = "__TESTPH__"

    async def run():
        eng = create_async_engine(url)
        S = async_sessionmaker(eng, expire_on_commit=False)
        async with S() as db:
            await db.execute(delete(PriceHistory).where(PriceHistory.irc == irc))
            await db.commit()
            t0 = datetime.now(timezone.utc) - timedelta(days=400)
            t1 = datetime.now(timezone.utc)
            # first record → inserted; same value → unchanged; new value → changed
            assert await record_price(db, irc, "announced", 100000, at=t0) == "inserted"
            assert await record_price(db, irc, "announced", 100000, at=t1) == "unchanged"
            assert await record_price(db, irc, "announced", 120000, at=t1) == "changed"
            await db.commit()
            assert await current_price(db, irc, "announced") == 120000   # newest open row
            # bad inputs skipped
            assert await record_price(db, irc, "announced", 0) == "skipped"
            assert await record_price(db, irc, "bogus", 5) == "skipped"

            # a stale one (open row from 400 days ago) surfaces; the fresh one doesn't
            await db.execute(delete(PriceHistory).where(PriceHistory.irc == irc))
            await record_price(db, irc, "announced", 100000, at=t0)
            await db.commit()
            st = await stale_prices(db, max_age_days=180)
            assert any(s["irc"] == irc for s in st["samples"]) and st["count"] >= 1

            await db.execute(delete(PriceHistory).where(PriceHistory.irc == irc))
            await db.commit()
        await eng.dispose()

    asyncio.get_event_loop().run_until_complete(run())
