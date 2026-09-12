#!/usr/bin/env python
"""The shelf, on a second day — which nothing had ever tested.

`depot_transfer` created placements and added to `current_units`; nothing
anywhere subtracted. Every verification of the morning round (E10) used a fresh
tenant where `on_shelf` was 0, so the defect was invisible: after the first
dispense the shelf believes it still holds everything ever brought to it, the
round proposes nothing, and a count has no honest "expected" to compare against.

This drives two consecutive days through the real endpoints and asserts what
only a second day can show.

    python scripts/verify_shelf_position.py

Everything lands in `pharmpilot_test` under a pharmacy created for this run.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text                                       # noqa: E402
from sqlalchemy.ext.asyncio import (async_sessionmaker,           # noqa: E402
                                    create_async_engine)

from services.core.inventory import dispense as DISP              # noqa: E402
from services.platform.routers import inventory_admin as AD       # noqa: E402
from services.platform.routers import inventory_integrity as IG   # noqa: E402
from tests.simulation.driver import Rx, Staff                      # noqa: E402

from verify_receiving import call, order, product, tenant, url     # noqa: E402

FAR = date.today() + timedelta(days=500)


async def shelf(db, pid, *, label, cond="ROOM_TEMP", cap=1000, zone="front"):
    sid = uuid.uuid4()
    await db.execute(text("""
        INSERT INTO pharmacy_shelves
          (id, pharmacy_id, label, zone, capacity_units, current_units,
           storage_condition, is_active, created_at, updated_at, is_deleted)
        VALUES (:i,:p,:l,:z,:c,0,:sc,true,now(),now(),false)"""),
        {"i": sid, "p": pid, "l": label, "z": zone, "c": cap, "sc": cond})
    await db.commit()
    return sid


async def place(db, pid, *, shelf_id, lot_id, ndc, units, staff_id, days_ago=0):
    await db.execute(text("""
        INSERT INTO shelf_placements
          (id, pharmacy_id, inventory_lot_id, shelf_id, ndc11, units, placed_by,
           placed_at, created_at, updated_at, is_deleted)
        VALUES (gen_random_uuid(),:p,:lot,:s,:n,:u,:by,:at,now(),now(),false)"""),
        {"p": pid, "lot": lot_id, "s": shelf_id, "n": ndc, "u": units,
         "by": staff_id,
         "at": datetime.now(timezone.utc) - timedelta(days=days_ago)})
    await db.execute(text(
        "UPDATE pharmacy_shelves SET current_units = current_units + :u "
        "WHERE id = :s"), {"u": units, "s": shelf_id})
    await db.commit()


async def person(db, pid):
    pat = (await db.execute(text("SELECT * FROM patients LIMIT 1"))).mappings().first()
    cols = [c for c in pat.keys() if c not in ("id", "pharmacy_id")]
    new = uuid.uuid4()
    await db.execute(text(
        f"INSERT INTO patients (id, pharmacy_id, {', '.join(cols)}) "
        f"SELECT :nid, :ph, {', '.join(cols)} FROM patients WHERE id = :src"),
        {"nid": new, "ph": pid, "src": pat["id"]})
    pres = (await db.execute(text("SELECT id FROM prescribers LIMIT 1"))).scalar()
    await db.commit()
    return new, pres


async def dispense(db, *, pid, patient, prescriber, ndc, units, staff_id):
    rx_id = uuid.uuid4()
    await db.execute(text("""
        INSERT INTO prescriptions
          (id, pharmacy_id, patient_id, prescriber_id, rx_number, ndc, drug_name,
           sig_text, quantity_prescribed, days_supply, written_date, source,
           status, refills_authorized, refills_remaining, is_controlled,
           created_at, updated_at, is_deleted)
        VALUES (:i,:ph,:pa,:pr,:rn,:n,'shelf test','1 daily',:q,30,CURRENT_DATE,
                'escript','ready_to_fill',0,0,false,now(),now(),false)"""),
        {"i": rx_id, "ph": pid, "pa": patient, "pr": prescriber,
         "rn": f"SHF{uuid.uuid4().hex[:9].upper()}", "n": ndc, "q": units})
    await db.commit()
    rx = Rx(rx_id, pid, ndc, float(units), "SHELF")
    res = await DISP.apply_dispense(db, rx, staff_id=staff_id)
    await db.commit()
    return res


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
        ndc = f"{uuid.uuid4().int % 10**11:011d}"
        did = await product(db, ndc)
        front = await shelf(db, pid, label="A-01", zone="front")
        print(f"\ntenant {pid}\n")

        # Receive 200 into the depot, price it, and put 100 on the shelf.
        oid = await order(db, pid=pid, lines=[(ndc, did, 200.0)],
                          ordered_days_ago=3, wholesaler="steady", unit_cost=10.0)
        r = await call(AD.receive_stock, body=AD.ReceiveLot(
            ndc11=ndc, lot_number="SHELF-1", expiry_date=FAR, quantity=200,
            unit_cost=10.0, sell_price=25.0, purchase_order_id=oid),
            staff=staff, db=db)
        lot_id = r["lot_id"]
        await place(db, pid, shelf_id=front, lot_id=lot_id, ndc=ndc, units=100,
                    staff_id=staff.id)

        pos0 = await call(IG.shelf_position, staff=staff, db=db)
        print(f"  opening floor: {pos0['units']} units, worth {pos0['value']}")
        check("the floor reports what was put on it", pos0["units"] == 100.0,
              str(pos0["units"]))
        check("valued at the shelf price, not cost", pos0["value"] == 2500.0,
              str(pos0["value"]))
        check("and it is answerable at any instant", "at" in pos0)

        # ── DAY ONE: dispense 30 ─────────────────────────────────────────
        print("\nday one — dispensing 30")
        res = await dispense(db, pid=pid, patient=patient, prescriber=prescriber,
                             ndc=ndc, units=30, staff_id=staff.id)
        check("the dispense succeeded", res.ok, str(res.as_dict()))
        check("and it came off the shelf, not only off the lot",
              len(res.shelf_takes) == 1 and res.shelf_takes[0]["units"] == 30.0,
              str(res.shelf_takes))
        check("marked inferred, because nobody scanned the shelf",
              res.shelf_takes and res.shelf_takes[0]["basis"] == "inferred",
              str(res.shelf_takes[:1]))

        pos1 = await call(IG.shelf_position, staff=staff, db=db)
        print(f"  floor now: {pos1['units']} units, worth {pos1['value']}")
        check("the floor went DOWN — the defect this closes",
              pos1["units"] == 70.0, str(pos1["units"]))
        check("and the revenue on the floor fell with it",
              pos1["value"] == 1750.0, str(pos1["value"]))
        cached = (await db.execute(text(
            "SELECT current_units FROM pharmacy_shelves WHERE id = :s"),
            {"s": front})).scalar()
        check("the shelf's own cached count agrees", int(cached) == 70,
              str(cached))
        ev = (await db.execute(text(
            "SELECT count(*) FROM shelf_transfer_events "
            "WHERE pharmacy_id = :p AND quantity_delta < 0"), {"p": pid})).scalar()
        check("and the movement is on the transfer ledger", ev == 1, str(ev))

        # ── the count, which is the theft detector ───────────────────────
        print("\nthe count")
        agree = await call(IG.count_shelf, body=IG.ShelfCount(
            shelf_id=front, counted=[{"ndc11": ndc, "units": 70}]),
            staff=staff, db=db)
        f0 = agree["findings"][0]
        check("a shelf that agrees is not a finding", f0["verdict"] == "agrees",
              f0["verdict"])
        check("but the unscanned movement is declared anyway",
              any("without anybody scanning it" in c for c in f0["concerns"]),
              str(f0["concerns"]))

        over = await call(IG.count_shelf, body=IG.ShelfCount(
            shelf_id=front, counted=[{"ndc11": ndc, "units": 85}]),
            staff=staff, db=db)
        check("more on the shelf than the books is not a loss",
              over["findings"][0]["verdict"] == "surplus",
              over["findings"][0]["verdict"])

        near = await call(IG.count_shelf, body=IG.ShelfCount(
            shelf_id=front, counted=[{"ndc11": ndc, "units": 45}]),
            staff=staff, db=db)
        n0 = near["findings"][0]
        check("a gap no bigger than the unscanned movement is inconclusive",
              n0["verdict"] == "inconclusive", n0["verdict"])
        check("and it says why rather than accusing anybody",
              "next shelf along" in n0["explanation"], n0["explanation"][:70])

        theft = await call(IG.count_shelf, body=IG.ShelfCount(
            shelf_id=front, counted=[{"ndc11": ndc, "units": 20}]),
            staff=staff, db=db)
        t0 = theft["findings"][0]
        print(f"  {t0['explanation']}")
        check("a gap larger than the inference can explain is shrinkage",
              t0["verdict"] == "shrinkage", t0["verdict"])
        check("priced at what walked out the door",
              t0["value_at_risk"] == 1250.0, str(t0["value_at_risk"]))
        check("and the shelf total is reported", theft["value_at_risk"] == 1250.0,
              str(theft["value_at_risk"]))

        # A count must not quietly move stock.
        on_hand = (await db.execute(text(
            "SELECT quantity_on_hand FROM inventory_lots WHERE id = :i"),
            {"i": lot_id})).scalar()
        check("counting a shelf does not move stock on its own",
              float(on_hand) == 170.0, str(on_hand))

        # ── DAY TWO: the morning round, which never used to see a gap ────
        # Drawn down to almost nothing first, so the round has something to say.
        # With 70 of 100 still standing there E10 correctly proposes nothing —
        # the first version of this check asserted otherwise and was wrong.
        print("\nday two — the morning round")
        await dispense(db, pid=pid, patient=patient, prescriber=prescriber,
                       ndc=ndc, units=65, staff_id=staff.id)
        pos_low = await call(IG.shelf_position, staff=staff, db=db)
        check("the shelf is nearly empty now", pos_low["units"] == 5.0,
              str(pos_low["units"]))

        pick = await call(IG.morning_pick_list, cover_days=1, staff=staff, db=db)
        line = next((l for l in pick["lines"] if l["ndc11"] == ndc), None)
        skipped = next((s for s in pick["skipped"] if s["ndc11"] == ndc), None)
        print(f"  round: {line or skipped}")
        check("E10 reads the drawn-down shelf, not the opening figure",
              line is not None and line["on_shelf"] == 5.0,
              str(line or skipped))
        check("and proposes bringing the difference forward",
              line is not None and line["pull"] > 0, str(line))

        # ── a lot split across two shelves ───────────────────────────────
        print("\na lot standing on two shelves")
        back = await shelf(db, pid, label="B-01", zone="back")
        await place(db, pid, shelf_id=back, lot_id=lot_id, ndc=ndc, units=50,
                    staff_id=staff.id, days_ago=0)
        res2 = await dispense(db, pid=pid, patient=patient,
                              prescriber=prescriber, ndc=ndc, units=40,
                              staff_id=staff.id)
        shelves_hit = {t["shelf_id"] for t in res2.shelf_takes}
        print(f"  took from {len(shelves_hit)} shelf(s): "
              f"{[(t['shelf_id'][:8], t['units']) for t in res2.shelf_takes]}")
        check("the older placement is emptied first",
              res2.shelf_takes[0]["shelf_id"] == str(front),
              str(res2.shelf_takes[:1]))
        check("and the take spans both shelves", len(shelves_hit) == 2,
              str(len(shelves_hit)))
        total = sum(t["units"] for t in res2.shelf_takes)
        check("taking no more off the shelves than they held", total <= 55,
              str(total))

        pos2 = await call(IG.shelf_position, staff=staff, db=db)
        check("the floor never goes negative", pos2["units"] >= 0,
              str(pos2["units"]))
        check("and zones are reported so a walk can be planned",
              len(pos2["zones"]) >= 1, str(pos2["zones"]))

    await engine.dispose()
    print(f"\n{'PASS' if not fails else 'FAIL: ' + ', '.join(fails)}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
