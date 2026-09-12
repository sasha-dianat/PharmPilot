"""The seed, exercised against real SQL.

A static grep proves nothing about SQL. These run against pharmpilot_test.

The failure mode this whole task prevents is SILENCE: a pharmacy created after
phase 5 gets zero zones, so every zoned observation it sends is refused and
nothing anywhere reports it. That is indistinguishable from a clean shop.
"""
from __future__ import annotations

import os
import re
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.asyncio


def _test_url() -> str | None:
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        return url
    env = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    try:
        with open(env, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("DATABASE_URL="):
                    raw = line.split("=", 1)[1].strip()
                    return re.sub(r"/pharmpilot$", "/pharmpilot_test", raw)
    except OSError:
        return None
    return None


@pytest.fixture
async def db():
    url = _test_url()
    if not url:
        pytest.skip("no test database URL")
    engine = create_async_engine(url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest.fixture
async def fresh_pharmacy(db):
    """A pharmacy that has never been seeded — the new-tenant case."""
    pid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO pharmacies (id, name) VALUES (:i, 'zone seed test')"),
        {"i": pid})
    await db.commit()
    yield pid
    await db.execute(text("DELETE FROM vision_zone WHERE pharmacy_id = :p"), {"p": pid})
    await db.execute(text("DELETE FROM pharmacies WHERE id = :p"), {"p": pid})
    await db.commit()


async def test_seeding_a_fresh_pharmacy_gives_it_every_doc_zone(db, fresh_pharmacy):
    from scripts.seed_vision_zones import seed_zones

    n = await seed_zones(db, fresh_pharmacy)
    await db.commit()
    assert n >= 12, f"only {n} zones seeded"

    codes = (await db.execute(text(
        "SELECT code FROM vision_zone WHERE pharmacy_id = :p"),
        {"p": fresh_pharmacy})).scalars().all()
    for expected in ("Z-ENT", "Z-WAIT", "Z-CDS", "Z-CAGE", "Z-COLD",
                     "Z-DOCK", "Z-STAFFDOOR", "Z-CONSULT"):
        assert expected in codes, expected


async def test_seeding_is_idempotent(db, fresh_pharmacy):
    """Run twice, get one set. A seed that duplicates on re-run cannot be put
    in a startup path or a provisioning script."""
    from scripts.seed_vision_zones import seed_zones

    await seed_zones(db, fresh_pharmacy)
    await db.commit()
    first = (await db.execute(text(
        "SELECT count(*) FROM vision_zone WHERE pharmacy_id = :p"),
        {"p": fresh_pharmacy})).scalar()
    await seed_zones(db, fresh_pharmacy)
    await db.commit()
    second = (await db.execute(text(
        "SELECT count(*) FROM vision_zone WHERE pharmacy_id = :p"),
        {"p": fresh_pharmacy})).scalar()
    assert first == second


async def test_the_seeded_zones_actually_accept_an_observation(db, fresh_pharmacy):
    """The point of the whole task: after seeding, a zoned observation is
    accepted. Before it, the same insert is refused by fk_surv_obs_zone."""
    from scripts.seed_vision_zones import seed_zones

    ins = ("INSERT INTO surveillance_observations "
           "(pharmacy_id, observed_at, site, action_type, zone_id) "
           "VALUES (:p, now(), 'depot', 'movement', 'Z-CAGE')")

    with pytest.raises(Exception):
        await db.execute(text(ins), {"p": fresh_pharmacy})
    await db.rollback()

    await seed_zones(db, fresh_pharmacy)
    await db.commit()
    await db.execute(text(ins), {"p": fresh_pharmacy})
    await db.execute(text(
        "DELETE FROM surveillance_observations WHERE pharmacy_id = :p"),
        {"p": fresh_pharmacy})
    await db.commit()


async def test_no_seeded_zone_carries_an_unmeasured_policy():
    """Provenance: retention_days and armed_schedule must be NULL, because
    nothing purges and no schedule evaluator exists. A seeded 30 would be a
    fabricated constant on a compliance-facing column."""
    from scripts.seed_vision_zones import DOC_ZONES
    for z in DOC_ZONES:
        assert z.get("retention_days") is None, z["code"]
        assert z.get("armed_schedule") is None, z["code"]


async def test_every_alias_target_is_a_zone_the_seed_registers():
    """An alias pointing at a code nobody registers is a dangling rename: the
    legacy spelling resolves to something the FK will then refuse."""
    from scripts.seed_vision_zones import DOC_ZONES
    from services.core.vision.zones import LEGACY_ZONE_ALIAS
    seeded = {z["code"] for z in DOC_ZONES}
    for legacy, canonical in LEGACY_ZONE_ALIAS.items():
        assert canonical in seeded, f"{legacy} -> {canonical} is not seeded"


async def test_the_high_risk_zones_are_classed_as_such():
    """Z-CAGE and Z-COLD drive WH-03's real-time routing in phase 5b. Misclass
    them and controlled substances fall into the daily batch."""
    from scripts.seed_vision_zones import DOC_ZONES
    by_code = {z["code"]: z for z in DOC_ZONES}
    assert by_code["Z-CAGE"]["zone_class"] == "high_risk"
    assert by_code["Z-COLD"]["zone_class"] == "high_risk"
    # And the one the design doc marks "No camera".
    assert by_code["Z-CONSULT"]["zone_class"] == "prohibited"
