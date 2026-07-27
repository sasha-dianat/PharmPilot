"""Retry only the pages a crawl actually lost, not the whole 70,000-page sweep."""
import asyncio
import os

import pytest

from services.core.drug_catalog import nfi_harvest_service as svc


def test_only_transport_failures_are_retryable():
    """A 5xx here overwhelmingly means the id does not exist — they arrive in
    long contiguous blocks (58,979–70,000 in one observed run). Replaying those
    would re-burn the hours the retry pass exists to save."""
    for c in ("dns_fail", "timeout", "tls_fail", "transport_error",
              "proxy_unreachable", "rate_limited"):
        assert c in svc.RETRYABLE
    for c in ("server_error", "http_404", "js_shell_no_table", "login_wall", "ok"):
        assert c not in svc.RETRYABLE


def test_failures_recorded_deduped_and_resolved():
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

    from sqlalchemy import delete, select
    from services.platform.database import AsyncSessionLocal
    from shared.models.harvest_failure import HarvestFailure

    A, B, C = 990001, 990002, 990003

    async def run():
        async with AsyncSessionLocal() as db:
            await db.execute(delete(HarvestFailure).where(
                HarvestFailure.page_id.in_([A, B, C])))
            await db.commit()

        await svc.record_failures([
            {"page_id": A, "category": "dns_fail", "http_status": None},
            {"page_id": B, "category": "timeout", "http_status": None},
            {"page_id": C, "category": "server_error", "http_status": 500},
        ])
        pend = await svc.pending_failures()
        assert A in pend and B in pend
        assert C not in pend, "a 5xx (nonexistent id) must not be queued for retry"

        # a second failing pass increments attempts instead of duplicating
        await svc.record_failures([{"page_id": A, "category": "dns_fail",
                                    "http_status": None}])
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(HarvestFailure).where(
                HarvestFailure.page_id == A))).scalars().all()
            assert len(rows) == 1 and rows[0].attempts == 2

        # a later successful pass resolves it — kept, not deleted, so a
        # chronically unreachable page stays visible
        assert await svc.resolve_failures([A]) == 1
        pend2 = await svc.pending_failures()
        assert A not in pend2 and B in pend2
        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(HarvestFailure).where(
                HarvestFailure.page_id == A))).scalar_one()
            assert row.resolved_at is not None
            await db.execute(delete(HarvestFailure).where(
                HarvestFailure.page_id.in_([A, B, C])))
            await db.commit()

    asyncio.get_event_loop().run_until_complete(run())
