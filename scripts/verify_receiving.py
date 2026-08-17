#!/usr/bin/env python
"""Prove the delivery→order loop actually closes, against a real database.

The unit tests pin the arithmetic in `services.core.inventory.receiving`. This
drives the receiving endpoint itself, on a throwaway tenant in the *test*
database, and then reads the purchase-order rows back — because the defect this
work exists to fix was never in the arithmetic. It was that nothing ever wrote
those columns at all, and only a round trip through the endpoint can show that
they are written now.

    python scripts/verify_receiving.py

Every write lands in `pharmpilot_test` under a pharmacy created for this run.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text                                       # noqa: E402
from sqlalchemy.ext.asyncio import (async_sessionmaker,           # noqa: E402
                                    create_async_engine)

from services.core.inventory import lead_time as LT               # noqa: E402
from services.platform.routers import inventory_admin as AD       # noqa: E402
from tests.simulation.driver import Staff                          # noqa: E402


def url() -> str:
    if os.getenv("TEST_DATABASE_URL"):
        return os.environ["TEST_DATABASE_URL"]
    dotenv = Path(__file__).resolve().parents[1] / ".env"
    for line in dotenv.read_text().splitlines():
        m = re.match(r"^DATABASE_URL=(.+)$", line.strip())
        if m:
            # Anchored: an unanchored replacement rewrites the *first* match and
            # points a test run at production.
            return re.sub(r"/pharmpilot$", "/pharmpilot_test", m.group(1))
    raise SystemExit("no test database configured")


async def tenant(db) -> uuid.UUID:
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
    await db.commit()
    return pid


async def product(db, ndc: str) -> uuid.UUID:
    did = uuid.uuid4()
    await db.execute(text("""
        INSERT INTO drug_products
          (id, ndc11, generic_name, brand_name, strength, dosage_form,
           package_quantity, is_controlled, requires_refrigeration, is_active,
           discontinued, is_generic, is_otc, is_hazardous, high_risk_flag,
           drug_db_metadata, created_at, updated_at, is_deleted)
        VALUES (:i,:n,'verify drug','Verify','10mg','tablet',30,false,false,
                true,false,true,false,false,false,'{}',now(),now(),false)
        ON CONFLICT (ndc11) DO NOTHING"""), {"i": did, "n": ndc})
    got = (await db.execute(text("SELECT id FROM drug_products WHERE ndc11 = :n"),
                            {"n": ndc})).scalar()
    await db.commit()
    return got


async def order(db, *, pid, lines: list[tuple[str, uuid.UUID, float]],
                ordered_days_ago: int, wholesaler: str = "verify-supplier") -> uuid.UUID:
    oid = uuid.uuid4()
    ordered_at = datetime.now(timezone.utc) - timedelta(days=ordered_days_ago)
    await db.execute(text("""
        INSERT INTO purchase_orders
          (id, pharmacy_id, wholesaler, po_number, status, ordered_at,
           created_at, updated_at, is_deleted, ai_generated)
        VALUES (:i,:p,:w,:n,'submitted',:o,now(),now(),false,false)"""),
        {"i": oid, "p": pid, "w": wholesaler,
         "n": f"VER-{uuid.uuid4().hex[:10]}", "o": ordered_at})
    for i, (ndc, did, qty) in enumerate(lines):
        await db.execute(text("""
            INSERT INTO purchase_order_lines
              (id, order_id, drug_product_id, ndc11, quantity_ordered,
               quantity_received, status, created_at, updated_at, is_deleted)
            VALUES (:i,:o,:d,:n,:q,0,'ordered',:c,now(),false)"""),
            {"i": uuid.uuid4(), "o": oid, "d": did, "n": ndc, "q": qty,
             "c": ordered_at + timedelta(seconds=i)})
    await db.commit()
    return oid


async def po_state(db, oid) -> dict:
    o = (await db.execute(text(
        "SELECT status, ordered_at, received_at FROM purchase_orders WHERE id = :i"),
        {"i": oid})).mappings().first()
    ls = (await db.execute(text(
        "SELECT ndc11, quantity_ordered, quantity_received, status "
        "FROM purchase_order_lines WHERE order_id = :i ORDER BY created_at"),
        {"i": oid})).mappings().all()
    return {"order": dict(o), "lines": [dict(r) for r in ls]}


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
        n1, n2 = f"{uuid.uuid4().int % 10**11:011d}", f"{uuid.uuid4().int % 10**11:011d}"
        d1, d2 = await product(db, n1), await product(db, n2)
        oid = await order(db, pid=pid, lines=[(n1, d1, 100.0), (n2, d2, 60.0)],
                          ordered_days_ago=6)
        far = date.today() + timedelta(days=500)

        print(f"\ntenant {pid}\norder  {oid}\n")

        # ── what the receiving bench is offered ──────────────────────────
        print("the open-order picker")
        listed = await AD.open_orders(None, staff, db)
        mine = [o for o in listed["orders"] if o["purchase_order_id"] == str(oid)]
        check("the placed order is offered", len(mine) == 1, str(listed["count"]))
        check("both lines outstanding",
              sorted(l["outstanding"] for l in mine[0]["lines"]) == [60.0, 100.0],
              str(mine[0]["lines"]))
        narrowed = await AD.open_orders(n2, staff, db)
        check("filtering by NDC returns only that line",
              all(l["ndc11"] == n2 for o in narrowed["orders"] for l in o["lines"]))

        # ── a short delivery ─────────────────────────────────────────────
        print("\na short first delivery")
        r = await AD.receive_stock(AD.ReceiveLot(
            ndc11=n1, lot_number="LOT-A", expiry_date=far, quantity=60,
            unit_cost=100.0, purchase_order_id=oid), staff, db)
        po = r["purchase_order"]
        check("line matched", po["matched"] is True, str(po))
        check("shape is short", po.get("shape") == "short", str(po.get("shape")))
        check("quantity_received written", po.get("total_received") == 60.0,
              str(po.get("total_received")))
        st = await po_state(db, oid)
        check("order is partial", st["order"]["status"] == "partial",
              st["order"]["status"])
        check("received_at withheld while outstanding",
              st["order"]["received_at"] is None, str(st["order"]["received_at"]))
        check("the column is actually written",
              float(st["lines"][0]["quantity_received"]) == 60.0,
              str(st["lines"][0]["quantity_received"]))

        # ── the rest of that line ────────────────────────────────────────
        print("\nthe balance of the first line")
        r = await AD.receive_stock(AD.ReceiveLot(
            ndc11=n1, lot_number="LOT-B", expiry_date=far, quantity=40,
            unit_cost=100.0, purchase_order_id=oid), staff, db)
        po = r["purchase_order"]
        check("line completes", po.get("status") == "complete", str(po.get("status")))
        st = await po_state(db, oid)
        check("order still partial while line 2 is outstanding",
              st["order"]["status"] == "partial", st["order"]["status"])
        check("received_at still withheld", st["order"]["received_at"] is None)

        # ── an over-delivery that completes the order ────────────────────
        print("\nan over-delivery on the second line")
        r = await AD.receive_stock(AD.ReceiveLot(
            ndc11=n2, lot_number="LOT-C", expiry_date=far, quantity=75,
            unit_cost=50.0, purchase_order_id=oid), staff, db)
        po = r["purchase_order"]
        check("shape is over", po.get("shape") == "over", str(po.get("shape")))
        check("over-delivery is named, not absorbed",
              "more than asked for" in po.get("explanation", ""),
              po.get("explanation", ""))
        st = await po_state(db, oid)
        check("order complete", st["order"]["status"] == "complete",
              st["order"]["status"])
        check("received_at stamped on completion",
              st["order"]["received_at"] is not None)
        after = await AD.open_orders(None, staff, db)
        check("a completed order leaves the picker",
              not [o for o in after["orders"] if o["purchase_order_id"] == str(oid)])

        # ── the fact this whole exercise exists to produce ───────────────
        lead = (await db.execute(text("""
            SELECT EXTRACT(EPOCH FROM (received_at - ordered_at)) / 86400.0
            FROM purchase_orders WHERE id = :i"""), {"i": oid})).scalar()
        check("lead time is now measurable", lead is not None and 5.5 < float(lead) < 6.5,
              f"{lead} days")
        print(f"       lead time observed: {float(lead):.2f} days")

        # E11 already reads these columns; the only thing missing was rows. The
        # demand refresh feeds its estimate into every reorder point, so this is
        # where the capture stops being bookkeeping and starts changing orders.
        pos = [dict(r) for r in (await db.execute(text(
            "SELECT wholesaler, ordered_at, received_at FROM purchase_orders "
            "WHERE pharmacy_id = :pid AND received_at IS NOT NULL"),
            {"pid": pid})).mappings().all()]
        est = LT.estimate(pos)
        check("E11 is off the declared default and onto measurement",
              est.basis == "sparse", f"{est.basis} / {est.days}d")
        check("and it says one order is thin evidence",
              "provisional" in est.explanation, est.explanation)
        print(f"       E11 after 1 order: {est.days}d, basis={est.basis}")

        # Three deliveries is where E11 stops hedging. Proving that transition
        # matters more than proving the arithmetic: it is the difference between
        # a reorder point built on a constant and one built on this supplier.
        for i, days_ago in enumerate((9, 4, 7)):
            ndc = f"{uuid.uuid4().int % 10**11:011d}"
            did = await product(db, ndc)
            o2 = await order(db, pid=pid, lines=[(ndc, did, 25.0)],
                             ordered_days_ago=days_ago)
            await AD.receive_stock(AD.ReceiveLot(
                ndc11=ndc, lot_number=f"LOT-R{i}", expiry_date=far, quantity=25,
                unit_cost=10.0, purchase_order_id=o2), staff, db)
        pos = [dict(r) for r in (await db.execute(text(
            "SELECT wholesaler, ordered_at, received_at FROM purchase_orders "
            "WHERE pharmacy_id = :pid AND received_at IS NOT NULL"),
            {"pid": pid})).mappings().all()]
        est = LT.estimate(pos)
        check("with four delivered orders E11 reports observed",
              est.basis == "observed", f"{est.basis} / {est.samples} samples")
        print(f"       E11 after 4 orders: {est.days}d, basis={est.basis}, "
              f"n={est.samples} — {est.explanation}")

        # ── a delivery for something never ordered ───────────────────────
        print("\na delivery for something that was never ordered")
        n3 = f"{uuid.uuid4().int % 10**11:011d}"
        await product(db, n3)
        r = await AD.receive_stock(AD.ReceiveLot(
            ndc11=n3, lot_number="LOT-D", expiry_date=far, quantity=10,
            unit_cost=10.0, purchase_order_id=oid), staff, db)
        po = r["purchase_order"]
        check("goods still recorded", r["lot_id"] is not None)
        check("but not attributed to the supplier", po["matched"] is False, str(po))
        st = await po_state(db, oid)
        check("order untouched by it", st["order"]["status"] == "complete")

        # ── another pharmacy's order ─────────────────────────────────────
        print("\nanother pharmacy's order")
        other = await tenant(db)
        theirs = await order(db, pid=other, lines=[(n1, d1, 10.0)],
                             ordered_days_ago=1)
        refused = False
        try:
            await AD.receive_stock(AD.ReceiveLot(
                ndc11=n1, lot_number="LOT-E", expiry_date=far, quantity=10,
                unit_cost=100.0, purchase_order_id=theirs), staff, db)
        except Exception as exc:                      # HTTPException
            refused = getattr(exc, "status_code", None) == 404
            await db.rollback()
        check("refused rather than corrupting their history", refused)

    await engine.dispose()
    print(f"\n{'PASS' if not fails else 'FAIL: ' + ', '.join(fails)}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
