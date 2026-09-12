#!/usr/bin/env python
"""Run the inventory simulator harder than the test suite does.

The pytest entry runs a few seeds at small scale so CI stays fast. This runs a
sweep: many seeds, longer horizons, concurrency, and a defect-rate summary that
says whether another batch is still worth running.

    python scripts/inv_simulate.py --seeds 20 --days 30 --rounds 200
    python scripts/inv_simulate.py --concurrency
    python scripts/inv_simulate.py --long-horizon 720

Every failure prints its seed. Re-running that seed alone reproduces it.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import uuid
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text                                     # noqa: E402
from sqlalchemy.ext.asyncio import (async_sessionmaker,          # noqa: E402
                                    create_async_engine)

from tests.simulation.driver import Driver                       # noqa: E402
from tests.simulation import invariants as INV                   # noqa: E402
from tests.simulation.runner import (Report, check_all, run_all,  # noqa: E402
                                     phase_normal)
from tests.simulation.world import build                          # noqa: E402


def url() -> str:
    if os.getenv("TEST_DATABASE_URL"):
        return os.environ["TEST_DATABASE_URL"]
    dotenv = Path(__file__).resolve().parents[1] / ".env"
    for line in dotenv.read_text().splitlines():
        m = re.match(r"^DATABASE_URL=(.+)$", line.strip())
        if m:
            return re.sub(r"/pharmpilot$", "/pharmpilot_test", m.group(1))
    raise SystemExit("no test database configured")


async def tenant(db):
    pid = uuid.uuid4()
    src = (await db.execute(text("SELECT * FROM pharmacies LIMIT 1"))).mappings().first()
    unique = {"npi": str(uuid.uuid4().int)[:10], "ncpdp_id": str(uuid.uuid4().int)[:7],
              "dea_number": f"S{uuid.uuid4().hex[:8].upper()}",
              "nabp_number": str(uuid.uuid4().int)[:7]}
    cols = [c for c in src.keys() if c != "id"]
    sel = ", ".join(f":{c}" if c in unique else c for c in cols)
    await db.execute(text(
        f"INSERT INTO pharmacies (id, {', '.join(cols)}) "
        f"SELECT :nid, {sel} FROM pharmacies WHERE id = :src"),
        {"nid": pid, "src": src["id"],
         **{k: v for k, v in unique.items() if k in cols}})
    pat = (await db.execute(text("SELECT * FROM patients LIMIT 1"))).mappings().first()
    pcols = [c for c in pat.keys() if c not in ("id", "pharmacy_id")]
    await db.execute(text(
        f"INSERT INTO patients (id, pharmacy_id, {', '.join(pcols)}) "
        f"SELECT :nid, :ph, {', '.join(pcols)} FROM patients WHERE id = :src"),
        {"nid": uuid.uuid4(), "ph": pid, "src": pat["id"]})
    await db.commit()
    return pid


async def one_seed(engine, seed: int, *, days: int, rounds: int, products: int):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        pid = await tenant(db)
        drv = Driver(db, build(seed=seed, n_products=products), pharmacy_id=pid)
        await drv.install_catalogue()
        await drv.receive_all()
        rep = await run_all(drv, days=days, rounds=rounds)
        return rep, drv


async def sweep(seeds: int, days: int, rounds: int, products: int, start: int):
    engine = create_async_engine(url())
    all_findings, totals = [], Counter()
    events = checks = 0
    new_by_batch = []

    for i in range(seeds):
        seed = start + i
        rep, _ = await one_seed(engine, seed, days=days, rounds=rounds,
                                products=products)
        events += rep.events
        checks += rep.checks
        before = len(totals)
        for f in rep.findings:
            # A defect *class* is the shape of the disagreement, not the row it
            # happened to hit. Counting rows would make one bug look like fifty.
            klass = re.sub(r"[0-9a-f-]{8,}", "<id>", f.detail)
            klass = re.sub(r"-?\d+\.\d+", "<n>", klass)
            totals[klass] += 1
        all_findings.extend(rep.findings)
        new_by_batch.append(len(totals) - before)
        print(f"  seed {seed:>4}: {rep.events:>4} events, "
              f"{len(rep.findings):>3} findings, {len(totals)} distinct classes")

    await engine.dispose()
    print(f"\n{events} events, {checks} check rounds, {len(all_findings)} findings, "
          f"{len(totals)} distinct defect classes")
    print(f"new classes per batch: {new_by_batch}")
    if totals:
        print("\nclasses by frequency:")
        for klass, n in totals.most_common(25):
            print(f"  {n:>4}x  {klass[:150]}")
    else:
        print("\nno invariant violated in this sweep.")
    return all_findings, totals


async def concurrency(seed: int, workers: int, each: int):
    """Two sessions racing for the same units.

    The case that matters is the last box: several dispenses of the final unit
    submitted at once. Exactly one should succeed in full; the rest must record
    a shortfall. What must never happen is negative stock, or two dispenses each
    believing they took the same unit.
    """
    engine = create_async_engine(url())
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        pid = await tenant(db)
        drv = Driver(db, build(seed=seed, n_products=3), pharmacy_id=pid)
        await drv.install_catalogue()
        from datetime import timedelta
        ndc = drv.world.products[0].ndc11
        await drv.receive(ndc, "RACE-1", each,
                          expiry=drv.world.as_of + timedelta(days=300),
                          unit_cost=5)
    print(f"stocked {each} units of {ndc}; {workers} sessions will each try to "
          f"dispense {each}")

    async def worker(n: int):
        async with sm() as wdb:
            w = Driver(wdb, build(seed=seed, n_products=3), pharmacy_id=pid)
            w.lot_ids = dict(drv.lot_ids)
            try:
                out = await w.dispense(ndc, each)
                return ("ok", getattr(out.payload, "allocated", None),
                        getattr(out.payload, "shortfall", None))
            except Exception as exc:
                return ("raised", type(exc).__name__, str(exc)[:90])

    results = await asyncio.gather(*(worker(i) for i in range(workers)),
                                   return_exceptions=True)
    for r in results:
        print("   ", r)

    async with sm() as db:
        rows = (await db.execute(text(
            "SELECT lot_number, quantity_on_hand FROM inventory_lots "
            "WHERE pharmacy_id = :p"), {"p": pid})).mappings().all()
        moved = (await db.execute(text(
            "SELECT COALESCE(SUM(-quantity_delta),0) FROM inventory_movements "
            "WHERE pharmacy_id = :p AND movement_type = 'DISPENSE'"),
            {"p": pid})).scalar()
        print("final lots:", [(r["lot_number"], float(r["quantity_on_hand"])) for r in rows])
        print("total dispensed by the ledger:", moved)
        problems = await INV.structural(db, pid)
        over = float(moved or 0) > each
        if over:
            problems.append(
                f"the ledger recorded {moved} units dispensed from a shelf that "
                f"only ever held {each} — units were created by the race")
        for p in problems:
            print("   DEFECT:", p)
        if not problems:
            print("   no invariant violated under the race")
    await engine.dispose()


async def long_horizon(seed: int, days: int, products: int):
    """Months of trading, checked at the end of every simulated month."""
    engine = create_async_engine(url())
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        pid = await tenant(db)
        drv = Driver(db, build(seed=seed, n_products=products), pharmacy_id=pid)
        await drv.install_catalogue()
        await drv.receive_all()
        rep = Report()
        for month in range(max(1, days // 30)):
            await phase_normal(drv, rep, days=30)
            await check_all(drv, rep, "6-long", f"month {month + 1}")
            print(f"  month {month + 1:>2}: {rep.events:>5} events, "
                  f"{len(rep.findings):>3} findings")
        print(f"\n{rep.events} events over {days} simulated days, "
              f"{len(rep.findings)} findings")
        for f in rep.findings[:20]:
            print("   ", f)
    await engine.dispose()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--start", type=int, default=100)
    ap.add_argument("--days", type=int, default=15)
    ap.add_argument("--rounds", type=int, default=120)
    ap.add_argument("--products", type=int, default=14)
    ap.add_argument("--concurrency", action="store_true")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--units", type=int, default=1)
    ap.add_argument("--long-horizon", type=int, default=0)
    a = ap.parse_args()

    if a.concurrency:
        asyncio.run(concurrency(a.start, a.workers, a.units))
        return 0
    if a.long_horizon:
        asyncio.run(long_horizon(a.start, a.long_horizon, a.products))
        return 0
    findings, _ = asyncio.run(
        sweep(a.seeds, a.days, a.rounds, a.products, a.start))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
