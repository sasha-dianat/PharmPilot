"""The simulator, run against a real database as a test.

Phases 1-4 run here on every suite invocation with fixed seeds, so a regression
in stock arithmetic fails CI rather than waiting to be noticed in a pharmacy.
The longer, noisier runs (phase 5 concurrency, phase 6 long-horizon) live in
`scripts/inv_simulate.py`, because they take minutes rather than seconds.

A failure prints the seed, the phase, the event count and the exact
disagreement, which is everything needed to reproduce it.
"""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.simulation.driver import Driver
from tests.simulation.runner import run_all
from tests.simulation.world import build


def _url() -> str | None:
    if os.getenv("TEST_DATABASE_URL"):
        return os.environ["TEST_DATABASE_URL"]
    dotenv = Path(__file__).resolve().parents[2] / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            m = re.match(r"^DATABASE_URL=(.+)$", line.strip())
            if m:
                return re.sub(r"/pharmpilot$", "/pharmpilot_test", m.group(1))
    return None


URL = _url()
pytestmark = [pytest.mark.asyncio,
              pytest.mark.skipif(not URL, reason="no test database configured")]


async def _tenant(db):
    """A pharmacy of this simulation's own, so a run cannot disturb another.

    Isolation matters more than it looks: the invariants compare *every* lot in
    the tenant against the oracle, so a stray row from another test would read
    as a defect in the application.
    """
    pid = uuid.uuid4()
    src = (await db.execute(text("SELECT * FROM pharmacies LIMIT 1"))).mappings().first()
    # Columns carrying a unique constraint have to be regenerated; copying them
    # verbatim collides with the row being copied from.
    unique = {"npi": str(uuid.uuid4().int)[:10],
              "ncpdp_id": str(uuid.uuid4().int)[:7],
              "dea_number": f"S{uuid.uuid4().hex[:8].upper()}",
              "nabp_number": str(uuid.uuid4().int)[:7]}
    cols = [c for c in src.keys() if c != "id"]
    select_list = ", ".join(f":{c}" if c in unique else c for c in cols)
    await db.execute(text(
        f"INSERT INTO pharmacies (id, {', '.join(cols)}) "
        f"SELECT :nid, {select_list} FROM pharmacies WHERE id = :src"),
        {"nid": pid, "src": src["id"],
         **{k: v for k, v in unique.items() if k in cols}})
    # Patients and prescribers are needed for dispensing; borrow one of each.
    pat = (await db.execute(text("SELECT * FROM patients LIMIT 1"))).mappings().first()
    pcols = [c for c in pat.keys() if c not in ("id", "pharmacy_id")]
    await db.execute(text(
        f"INSERT INTO patients (id, pharmacy_id, {', '.join(pcols)}) "
        f"SELECT :nid, :ph, {', '.join(pcols)} FROM patients WHERE id = :src"),
        {"nid": uuid.uuid4(), "ph": pid, "src": pat["id"]})
    await db.commit()
    return pid


@pytest.fixture
async def sim(request):
    seed = getattr(request, "param", 7)
    engine = create_async_engine(URL)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        pid = await _tenant(db)
        world = build(seed=seed, n_products=12)
        drv = Driver(db, world, pharmacy_id=pid)
        await drv.install_catalogue()
        await drv.receive_all()
        yield drv
    await engine.dispose()


@pytest.mark.parametrize("sim", [7], indirect=True)
async def test_phases_one_to_four_hold_every_invariant(sim):
    """The whole escalating run, on one seed, as a gate."""
    report = await run_all(sim, days=8, rounds=60)

    # A simulation that exercises nothing passes trivially. These assert the
    # run actually happened before its silence is treated as evidence.
    lots = sim.oracle.state()
    moved = sum(1 for st in lots.values() if st.on_hand or st.damaged
                or st.returned or st.in_transit)
    assert report.events >= 100, f"only {report.events} events were generated"
    assert report.checks >= 10, f"only {report.checks} check rounds ran"
    assert len(lots) >= 10, f"only {len(lots)} lots existed"
    assert moved >= 5, f"only {moved} lots ever held stock"
    assert sim.oracle.movement_count("DISPENSE") >= 20, "almost nothing dispensed"

    if report.findings:
        lines = "\n".join(f"  {f}" for f in report.findings[:40])
        pytest.fail(
            f"{len(report.findings)} invariant failure(s) across "
            f"{report.events} events and {report.checks} check rounds "
            f"(seed={sim.world.seed}):\n{lines}")


@pytest.mark.parametrize("sim", [11, 23], indirect=True)
async def test_other_seeds_hold_too(sim):
    """Different catalogues, different lot shapes, same rules.

    One seed proves the code survives one arrangement of stock. Several prove
    it survives the arrangement, which is a different claim.
    """
    report = await run_all(sim, days=5, rounds=40)
    if report.findings:
        lines = "\n".join(f"  {f}" for f in report.findings[:40])
        pytest.fail(f"seed={sim.world.seed}: {len(report.findings)} failure(s):\n{lines}")
