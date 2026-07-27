"""ناسازگاری‌ها triage board: root causes, lanes, and converging dispositions."""
import asyncio
import os

import pytest

from services.core.drug_catalog import issue_registry as ir


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


def test_every_cause_declares_a_lane_and_an_action():
    """A cause the owner cannot act on is just noise — each must name the lane
    that closes it, why it happens, and the concrete next step."""
    assert ir.CAUSES, "no causes registered"
    for key, meta in ir.CAUSES.items():
        assert meta["lane"] in ir.LANE_FA, f"{key}: unknown lane {meta['lane']}"
        for field in ("title", "why", "action", "route"):
            assert meta.get(field), f"{key}: missing {field}"
        assert len(meta["title"]) < 60


def test_lanes_cover_the_ways_an_issue_can_end():
    # the six lanes are exhaustive by design: the platform fixes it, one ruling
    # covers the group, research is needed, judgement is needed, it is normal,
    # or a prerequisite is missing
    assert set(ir.LANE_FA) == {ir.LANE_AUTO, ir.LANE_BULK, ir.LANE_RESEARCH,
                               ir.LANE_JUDGEMENT, ir.LANE_EXPECTED, ir.LANE_BLOCKED}
    assert set(ir.DISPOSITIONS) == {"accepted", "wont_fix", "resolved", "deferred"}


def test_board_groups_causes_and_dispositions_make_it_converge():
    """The whole point: a ruling must move a group OUT of the open count and
    survive the next harvest, so the backlog can actually shrink."""
    url = _db_or_skip()
    from decimal import Decimal
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from shared.models.drug_catalog import DrugCatalogItem
    from shared.models.issue_disposition import IssueDisposition

    async def run():
        eng = create_async_engine(url)
        S = async_sessionmaker(eng, expire_on_commit=False)
        async with S() as db:
            await db.execute(delete(IssueDisposition))
            await db.execute(delete(DrugCatalogItem).where(
                DrugCatalogItem.source == "issue-itest"))
            # a product with no country → guarantees nfi_missing_country > 0
            db.add(DrugCatalogItem(
                irc="__ISSUE_ITEST__", name_fa="آزمون", generic_name="metformin",
                ingredient_key="metformin|500 mg|tablet", dosage_form="TABLET",
                strength="500 mg", atc="A10BA02", country=None,
                announced_price=Decimal("1000"), source="issue-itest"))
            await db.commit()

            b1 = await ir.board(db)
            assert b1["causes"] and b1["totals"]["acknowledged"] == 0
            assert set(b1["lanes"]) == set(ir.LANE_FA)
            # every listed cause is known and carries its lane label
            for c in b1["causes"]:
                assert c["cause"] in ir.CAUSES
                assert c["lane_fa"] == ir.LANE_FA[c["lane"]]
                assert c["closed"] is False and c["disposition"] is None
            open_before = b1["totals"]["open"]

            # rule on a cause that actually has rows, so the count must move
            target = next((c["cause"] for c in b1["causes"] if c["count"] > 0), None)
            assert target, "expected at least one populated cause"
            moved = next(c["count"] for c in b1["causes"] if c["cause"] == target)

            res = await ir.set_disposition(
                db, issue_type=target, subject_key="*", disposition="accepted",
                reason="ساختاری و طبیعی")
            await db.commit()
            assert res == "created"

            b2 = await ir.board(db)
            ruled = next(c for c in b2["causes"] if c["cause"] == target)
            assert ruled["closed"] and ruled["disposition"] == "accepted"
            assert ruled["reason"] == "ساختاری و طبیعی" and ruled["decided_at"]
            # the open count went DOWN by exactly that group
            assert b2["totals"]["open"] == open_before - moved
            assert b2["totals"]["acknowledged"] == moved
            assert b2["totals"]["all"] == b1["totals"]["all"]
            # a closed cause no longer contributes to any lane bucket
            assert target not in {c["cause"] for c in b2["causes"]
                                  if not c["closed"] and c["cause"] == target}

            # re-ruling updates in place (no duplicate rows)
            assert await ir.set_disposition(
                db, issue_type=target, subject_key="*",
                disposition="deferred", reason="بعداً") == "updated"
            await db.commit()
            b3 = await ir.board(db)
            assert next(c for c in b3["causes"]
                        if c["cause"] == target)["disposition"] == "deferred"

            # an unknown disposition is refused rather than silently stored
            with pytest.raises(ValueError):
                await ir.set_disposition(db, issue_type=target, subject_key="*",
                                         disposition="banana")

            await db.execute(delete(IssueDisposition))
            await db.commit()
            # reopening restores the original open count
            b4 = await ir.board(db)
            assert b4["totals"]["open"] == open_before
            await db.execute(delete(DrugCatalogItem).where(
                DrugCatalogItem.source == "issue-itest"))
            await db.commit()
        await eng.dispose()

    asyncio.get_event_loop().run_until_complete(run())


def test_jalali_year_month_and_licence_cutoff():
    """NFI licence dates are Jalali and no converter is installed, so the
    registry computes its own. Nowruz falls on 20 or 21 March depending on the
    year, so dates ON a month boundary may land one month either side — stated
    rather than hidden, and irrelevant for expiry (the licences that matter are
    years stale). Non-boundary dates must be exact."""
    from datetime import date
    for g, expected in ((date(2026, 7, 26), (1405, 5)),
                        (date(2026, 3, 25), (1405, 1)),
                        (date(2021, 7, 27), (1400, 5)),
                        (date(2025, 1, 1), (1403, 10)),
                        (date(2026, 3, 15), (1404, 12))):
        assert ir.jalali_year_month(g) == expected, g
    cut = ir.licence_cutoff()
    assert len(cut) == 7 and cut[4] == "/"
    jy, jm = ir.jalali_year_month()
    assert cut == f"{jy:04d}/{jm:02d}"
    # a years-old licence sorts below the cutoff; a far-future one above
    assert "1395/11" < cut < "1499/01"


def test_missing_price_is_split_by_licence_validity():
    """One 4,126-item cause overstated the problem: 84% of the already-crawled
    rows carrying a licence date are EXPIRED, and a deregistered product having
    no tariff is correct source data, not a gap. The expired half must sit in
    the 'expected' lane (acknowledgeable) and the rest stay actionable."""
    exp = ir.CAUSES["nfi_price_expired_registration"]
    act = ir.CAUSES["nfi_missing_price_active"]
    assert exp["lane"] == ir.LANE_EXPECTED and exp["route"] == "acknowledge"
    assert act["lane"] == ir.LANE_BLOCKED and act["route"] == "nfi_harvest"
    assert "nfi_missing_price" not in ir.CAUSES, "the merged cause must be gone"
