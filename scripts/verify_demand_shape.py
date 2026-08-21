#!/usr/bin/env python
"""Prove E6 changes the reorder point, on data the refresh endpoint reads.

Three items with deliberately different demand shapes and roughly the same rate.
The point of the exercise is the last check: the lumpy item's cover comes out
several times larger than the sigma formula would have set, because a safety
stock derived from a daily average cannot serve a demand that arrives all at
once — and the smooth item is not inflated at all.

    python scripts/verify_demand_shape.py

Everything lands in `pharmpilot_test` under a pharmacy created for this run.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text                                       # noqa: E402
from sqlalchemy.ext.asyncio import (async_sessionmaker,           # noqa: E402
                                    create_async_engine)

from services.platform.routers import inventory_admin as AD       # noqa: E402
from services.platform.routers import inventory_integrity as IG   # noqa: E402
from tests.simulation.driver import Staff                          # noqa: E402

from verify_receiving import order, product, tenant, url           # noqa: E402

WINDOW = 84
FAR = date.today() + timedelta(days=500)

# Same-ish rate, entirely different shape. `smooth` and `lumpy` differ by less
# than half a unit a day and could not be told apart by a rate alone.
SHAPES = {
    "smooth":       {i: 2 for i in range(WINDOW)},               # 2.00/day
    "intermittent": {i * 7: 14 for i in range(12)},              # 2.00/day
    "lumpy":        {0: 5, 14: 60, 33: 12, 55: 80, 76: 8},       # 1.96/day
}


async def person(db, pid) -> tuple[uuid.UUID, uuid.UUID]:
    """A patient and a prescriber to hang the fills off."""
    pat = (await db.execute(text("SELECT * FROM patients LIMIT 1"))).mappings().first()
    cols = [c for c in pat.keys() if c not in ("id", "pharmacy_id")]
    pid_new = uuid.uuid4()
    await db.execute(text(
        f"INSERT INTO patients (id, pharmacy_id, {', '.join(cols)}) "
        f"SELECT :nid, :ph, {', '.join(cols)} FROM patients WHERE id = :src"),
        {"nid": pid_new, "ph": pid, "src": pat["id"]})
    pres = (await db.execute(text("SELECT id FROM prescribers LIMIT 1"))).scalar()
    await db.commit()
    return pid_new, pres


async def dispense_history(db, *, pid, patient, prescriber, ndc, pattern,
                           staff_id):
    """Write the fills directly — this stands in for months of dispensing.

    `refresh_demand` reads `prescription_fills` joined through `prescriptions`
    for the tenant scope, so both rows have to exist for the history to be
    visible to the engine at all.
    """
    today = date.today()
    for days_ago, units in pattern.items():
        rx = uuid.uuid4()
        await db.execute(text("""
            INSERT INTO prescriptions
              (id, pharmacy_id, patient_id, prescriber_id, rx_number, ndc,
               drug_name, sig_text, quantity_prescribed, days_supply,
               written_date, source, status, refills_authorized,
               refills_remaining, is_controlled, created_at, updated_at,
               is_deleted)
            VALUES (:i,:ph,:pa,:pr,:rn,:n,'shape','1 daily',:q,30,CURRENT_DATE,
                    'escript','completed',0,0,false,now(),now(),false)"""),
            {"i": rx, "ph": pid, "pa": patient, "pr": prescriber,
             "rn": f"SHP{uuid.uuid4().hex[:9].upper()}", "n": ndc, "q": units})
        await db.execute(text("""
            INSERT INTO prescription_fills
              (id, prescription_id, fill_number, ndc_dispensed,
               quantity_dispensed, days_supply, fill_date,
               dispensing_pharmacist_id, verifying_pharmacist_id,
               pickup_confirmed_by_biometric, created_at, updated_at,
               is_deleted)
            VALUES (:i,:rx,1,:n,:q,30,CAST(:d AS date),:st,:st,false,
                    CAST(:d AS date)::timestamptz,now(),false)"""),
            {"i": uuid.uuid4(), "rx": rx, "n": ndc, "q": units,
             "st": staff_id, "d": today - timedelta(days=days_ago)})
    await db.commit()


async def main() -> int:
    engine = create_async_engine(url(), echo=False)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    fails: list[str] = []

    def check(label: str, cond: bool, detail: str = "") -> None:
        print(f"  {'ok  ' if cond else 'FAIL'}  {label}"
              + (f"  — {detail}" if detail and not cond else ""))
        if not cond:
            fails.append(label)

    async with sm() as db:
        pid = await tenant(db)
        staff = Staff(pid)
        patient, prescriber = await person(db, pid)
        ndcs = {k: f"{uuid.uuid4().int % 10**11:011d}" for k in SHAPES}
        print(f"\ntenant {pid}\n")

        for key, ndc in ndcs.items():
            did = await product(db, ndc)
            # Stock it, and give the supplier a measured lead time so the
            # horizon in the reorder point is observed rather than assumed.
            for i in range(4):
                oid = await order(db, pid=pid, lines=[(ndc, did, 400.0)],
                                  ordered_days_ago=4, wholesaler="steady",
                                  unit_cost=10.0)
                await AD.receive_stock(AD.ReceiveLot(
                    ndc11=ndc, lot_number=f"{key}-{i}", expiry_date=FAR,
                    quantity=400, unit_cost=10.0,
                    purchase_order_id=oid), staff, db)
            await dispense_history(db, pid=pid, patient=patient,
                                   prescriber=prescriber, ndc=ndc,
                                   pattern=SHAPES[key], staff_id=staff.id)

        out = await IG.refresh_demand(False, WINDOW, staff, db)
        rows = {r["ndc11"]: r for r in out["rows"] if r["ndc11"] in ndcs.values()}
        by_key = {k: rows[v] for k, v in ndcs.items() if v in rows}

        print("  E6:")
        for k, r in by_key.items():
            p, s = r["pattern"], r["signals"]
            print(f"    {k:<13} class={p['demand_class']:<13} "
                  f"rate={p['mean_rate']} adi={p['adi']} cv2={p['cv2']} "
                  f"event={p['typical_event']} "
                  f"safety={s['safety_stock']} rop={s['reorder_point']} "
                  f"floor={'yes' if s['floor_applied'] else 'no'}")
        print()

        check("all three items were classified", len(by_key) == 3, str(list(by_key)))
        check("the steady seller is smooth",
              by_key["smooth"]["pattern"]["demand_class"] == "smooth",
              by_key["smooth"]["pattern"]["demand_class"])
        check("regular bursts of a similar size are intermittent",
              by_key["intermittent"]["pattern"]["demand_class"] == "intermittent",
              by_key["intermittent"]["pattern"]["demand_class"])
        check("rare and wildly uneven is lumpy",
              by_key["lumpy"]["pattern"]["demand_class"] == "lumpy",
              by_key["lumpy"]["pattern"]["demand_class"])

        rates = [by_key[k]["pattern"]["mean_rate"] for k in SHAPES]
        check("and the three rates are nearly identical, which is the point",
              max(rates) - min(rates) < 0.5, str(rates))

        check("the lumpy item is reported as not forecastable",
              by_key["lumpy"]["pattern"]["forecastable"] is False)
        check("and says why rather than producing a confident number",
              any("no method forecasts this well" in c
                  for c in by_key["lumpy"]["pattern"]["concerns"]))

        # ── the reorder point, which is what all of this is for ──────────
        # Every item that gets its demand in bursts must be able to serve one,
        # whichever term produced the cover.
        for k in ("intermittent", "lumpy"):
            s, p = by_key[k]["signals"], by_key[k]["pattern"]
            check(f"the {k} item can serve one whole event",
                  s["safety_stock"] >= p["typical_event"],
                  f"cover {s['safety_stock']} vs event {p['typical_event']}")

        check("the lumpy item needed the floor to get there",
              by_key["lumpy"]["signals"]["floor_applied"] is True,
              str(by_key["lumpy"]["signals"]))
        # A regular burst pattern does not: its event sizes barely vary, so the
        # daily-bucket sigma — with the zeros in it — already covers one. The
        # floor is a floor, not an override, and raising nothing here is right.
        check("the regular burst pattern got there on the sigma term alone",
              by_key["intermittent"]["signals"]["floor_applied"] is False,
              str(by_key["intermittent"]["signals"]["floor_applied"]))
        check("the smooth item is not inflated by a floor it does not need",
              by_key["smooth"]["signals"]["floor_applied"] is False,
              str(by_key["smooth"]["signals"]))

        smooth_rop = by_key["smooth"]["signals"]["reorder_point"]
        lumpy_rop = by_key["lumpy"]["signals"]["reorder_point"]
        check("so two items with the same rate get different reorder points",
              lumpy_rop > smooth_rop, f"{lumpy_rop} vs {smooth_rop}")
        print(f"       same rate, reorder points {smooth_rop} and {lumpy_rop}")

        check("the horizon is a measured lead time, not an assumption",
              by_key["smooth"]["signals"]["lead_time_basis"] == "observed",
              by_key["smooth"]["signals"]["lead_time_basis"])
        check("the summary counts the shapes",
              out["summary"]["by_demand_class"]["lumpy"] >= 1,
              str(out["summary"]["by_demand_class"]))
        check("and how many needed the event floor to reach it",
              out["summary"]["covered_for_one_event"] >= 1,
              str(out["summary"]["covered_for_one_event"]))

        # ── writing it ───────────────────────────────────────────────────
        applied = await IG.refresh_demand(True, WINDOW, staff, db)
        stored = {r["ndc11"]: dict(r) for r in (await db.execute(text(
            "SELECT ndc11, avg_daily_demand, safety_stock, reorder_point, "
            "demand_basis FROM stock_levels WHERE pharmacy_id = :p"),
            {"p": pid})).mappings().all()}
        check("the refresh wrote the reorder points", applied["written"] > 0,
              str(applied["written"]))
        check("and the lumpy item's stored cover is the event size",
              float(stored[ndcs["lumpy"]]["safety_stock"] or 0)
              == by_key["lumpy"]["pattern"]["typical_event"],
              f"{stored[ndcs['lumpy']]['safety_stock']} vs "
              f"{by_key['lumpy']['pattern']['typical_event']}")

        # ── an item with no dispensing at all ───────────────────────────
        bare = f"{uuid.uuid4().int % 10**11:011d}"
        bare_id = await product(db, bare)
        oid = await order(db, pid=pid, lines=[(bare, bare_id, 50.0)],
                          ordered_days_ago=4, wholesaler="steady", unit_cost=10.0)
        await AD.receive_stock(AD.ReceiveLot(
            ndc11=bare, lot_number="BARE-1", expiry_date=FAR, quantity=50,
            unit_cost=10.0, purchase_order_id=oid), staff, db)
        out2 = await IG.refresh_demand(False, WINDOW, staff, db)
        b = next(r for r in out2["rows"] if r["ndc11"] == bare)
        check("an item nobody has dispensed gets no shape and no rate",
              b["pattern"]["demand_class"] == "unknown"
              and b["pattern"]["mean_rate"] is None,
              str(b["pattern"]["demand_class"]))
        check("and no reorder point is invented for it",
              b["signals"]["reorder_point"] is None,
              str(b["signals"]["reorder_point"]))

    await engine.dispose()
    print(f"\n{'PASS' if not fails else 'FAIL: ' + ', '.join(fails)}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
