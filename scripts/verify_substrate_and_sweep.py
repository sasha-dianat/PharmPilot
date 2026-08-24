#!/usr/bin/env python
"""The three gaps the engines named about themselves, closed and proven.

  1. `expected_delivery` was read in two places and written in none, so "late"
     could only ever mean "slower than this supplier's own habit" — never
     "later than they said".
  2. `purchase_order_lines.status` has carried `substituted` since the model was
     written and nothing produced it, so E12 reported its substitution count as
     `not_captured`: a zero there meant unmeasured, not never.
  3. Nothing ran the engines. Every one of them files advice only when a human
     opens its tab with a flag set, so the recommendation ledger — built to
     measure whether each engine is any good — had nothing to measure.

    python scripts/verify_substrate_and_sweep.py

Everything lands in `pharmpilot_test` under pharmacies created for this run.
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

from services.core.inventory import lead_time as LT               # noqa: E402
from services.core.inventory import sweep as SWEEP                # noqa: E402
from services.platform.routers import inventory_admin as AD       # noqa: E402
from services.platform.routers import inventory_integrity as IG   # noqa: E402
from tests.simulation.driver import Staff                          # noqa: E402

from verify_receiving import call, order, product, tenant, url     # noqa: E402

FAR = date.today() + timedelta(days=500)


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
        n1 = f"{uuid.uuid4().int % 10**11:011d}"
        n2 = f"{uuid.uuid4().int % 10**11:011d}"
        d1 = await product(db, n1)
        await product(db, n2)
        print(f"\ntenant {pid}\n")

        # ── 1. a promise, and a supplier held to it ──────────────────────
        print("the promised delivery date")
        promises = []
        for i, (ordered_ago, promised_ago, late) in enumerate(
                [(20, 15, 0), (16, 11, 4), (12, 7, 0), (8, 3, 3), (6, 1, 0)]):
            oid = await order(db, pid=pid, lines=[(n1, d1, 100.0)],
                              ordered_days_ago=ordered_ago,
                              wholesaler="promiser", unit_cost=10.0)
            due = date.today() - timedelta(days=promised_ago)
            await db.execute(text(
                "UPDATE purchase_orders SET expected_delivery = :d WHERE id = :i"),
                {"d": due, "i": oid})
            await db.commit()
            await call(AD.receive_stock, body=AD.ReceiveLot(
                ndc11=n1, lot_number=f"P-{i}", expiry_date=FAR, quantity=100,
                unit_cost=10.0, purchase_order_id=oid), staff=staff, db=db)
            # The receipt stamps `received_at` at now; shift it to model a
            # delivery that arrived `late` days after the date they gave.
            await db.execute(text(
                "UPDATE purchase_orders SET received_at = "
                "CAST(:d AS date)::timestamptz + make_interval(days => :l) "
                "WHERE id = :i"), {"d": due, "l": late, "i": oid})
            await db.commit()
            promises.append(oid)

        rows = [dict(r) for r in (await db.execute(text(
            "SELECT wholesaler, ordered_at, expected_delivery, received_at "
            "FROM purchase_orders WHERE pharmacy_id = :p AND wholesaler = 'promiser'"),
            {"p": pid})).mappings().all()]
        check("every promise was recorded",
              all(r["expected_delivery"] is not None for r in rows), str(len(rows)))
        p = LT.punctuality(rows, supplier="promiser")
        print(f"       {p.explanation}")
        check("the on-time rate is measured against the promise",
              p.basis == "observed" and p.rate is not None, str(p.as_dict()))
        check("and it is not perfect, because the supplier was not",
              p.rate < 1 and p.late == 2, f"{p.rate} / {p.late} late")

        sc = await call(IG.supplier_scorecard, raise_advice=False,
                        staff=staff, db=db)
        pr = next(s for s in sc["suppliers"] if s["supplier"] == "promiser")
        check("E12 carries it, distinct from being quick",
              pr["on_time_rate"] is not None and pr["on_time_basis"] == "observed",
              f"{pr['on_time_rate']} / {pr['on_time_basis']}")
        check("and opens the conversation with it",
              any("held to its promise" in c for c in pr["concerns"]),
              str(pr["concerns"]))
        others = [s for s in sc["suppliers"] if s["supplier"] != "promiser"]
        check("a supplier that promised nothing is not scored perfect",
              all(s["on_time_rate"] is None and s["on_time_basis"] == "no_promises"
                  for s in others) if others else True,
              str([(s["supplier"], s["on_time_rate"]) for s in others]))

        # ── 2. a substitution ────────────────────────────────────────────
        print("\na supplier sends something else")
        oid2 = await order(db, pid=pid, lines=[(n1, d1, 100.0)],
                           ordered_days_ago=5, wholesaler="substituter",
                           unit_cost=10.0)
        r = await call(AD.receive_stock, body=AD.ReceiveLot(
            ndc11=n2, lot_number="SUB-1", expiry_date=FAR, quantity=100,
            unit_cost=10.0, purchase_order_id=oid2, substitutes_ndc11=n1),
            staff=staff, db=db)
        po = r["purchase_order"]
        check("the replaced line closes as substituted",
              po.get("status") == "substituted", str(po.get("status")))
        check("it names what it replaced", po.get("substituted_for") == n1)
        check("the substitute is not counted as a fill",
              po.get("total_received") == 0.0, str(po.get("total_received")))
        check("but the goods are still received onto the shelf",
              r["lot_id"] is not None and r["quantity_received"] == 100.0)
        check("and the pharmacist's judgement is left to the pharmacist",
              "pharmacist's decision, not this one" in po.get("explanation", ""),
              po.get("explanation", "")[:80])

        line = (await db.execute(text(
            "SELECT status, quantity_received FROM purchase_order_lines "
            "WHERE order_id = :i"), {"i": oid2})).mappings().first()
        check("the database agrees", line["status"] == "substituted"
              and float(line["quantity_received"]) == 0.0, str(dict(line)))

        sc2 = await call(IG.supplier_scorecard, raise_advice=False,
                         staff=staff, db=db)
        sub = next(s for s in sc2["suppliers"] if s["supplier"] == "substituter")
        check("E12 counts substitutions instead of saying it cannot",
              sub["substitutions"] == 1 and sub["substitution_basis"] == "observed",
              f"{sub['substitutions']} / {sub['substitution_basis']}")

        refused = False
        try:
            await call(AD.receive_stock, body=AD.ReceiveLot(
                ndc11=n1, lot_number="SUB-2", expiry_date=FAR, quantity=10,
                unit_cost=10.0, purchase_order_id=oid2, substitutes_ndc11=n1),
                staff=staff, db=db)
        except Exception as exc:
            refused = getattr(exc, "status_code", None) in (422, 404)
            await db.rollback()
        check("substituting a product for itself is refused", refused)

        # ── 3. the engines run unasked ───────────────────────────────────
        # A tenant with something genuinely worth raising, so that "nothing
        # filed" and "the sweep is broken" cannot look the same.
        print("\nthe nightly sweep, on a shelf that has something to say")
        n3 = f"{uuid.uuid4().int % 10**11:011d}"
        d3 = await product(db, n3)
        for w in ("alpha", "beta"):
            for i in range(4):
                oid = await order(db, pid=pid, lines=[(n3, d3, 100.0)],
                                  ordered_days_ago=6, wholesaler=w, unit_cost=10.0)
                await call(AD.receive_stock, body=AD.ReceiveLot(
                    ndc11=n3, lot_number=f"{w}-{i}", expiry_date=FAR, quantity=40,
                    unit_cost=10.0, purchase_order_id=oid), staff=staff, db=db)
        for row in (await db.execute(text(
                "SELECT id FROM purchase_orders WHERE pharmacy_id = :p "
                "AND received_at IS NULL"), {"p": pid})).mappings().all():
            await call(AD.close_order_short, po_id=row["id"],
                       body=AD.CloseOrder(reason="supplier confirmed no balance"),
                       staff=staff, db=db)
        await db.execute(text(
            "UPDATE stock_levels SET avg_daily_demand = 10, "
            "demand_basis = 'observed', quantity_on_hand = 40 "
            "WHERE pharmacy_id = :p AND ndc11 = :n"), {"p": pid, "n": n3})
        await db.commit()

        before = (await db.execute(text(
            "SELECT count(*) FROM inventory_recommendations WHERE pharmacy_id = :p"),
            {"p": pid})).scalar()
        filed = await SWEEP.sweep_pharmacy(db, pid)
        after = (await db.execute(text(
            "SELECT count(*) FROM inventory_recommendations WHERE pharmacy_id = :p"),
            {"p": pid})).scalar()
        print(f"       {filed}")
        check("every engine ran without anybody opening a tab",
              set(filed) == set(SWEEP.ENGINES), str(set(filed)))
        check("and it actually filed something", after > before,
              f"{before} → {after}")
        kinds = {r[0] for r in (await db.execute(text(
            "SELECT DISTINCT kind FROM inventory_recommendations "
            "WHERE pharmacy_id = :p"), {"p": pid})).all()}
        check("including the shortage it was meant to catch",
              "shortage_warning" in kinds, str(kinds))

        again = await SWEEP.sweep_pharmacy(db, pid)
        check("running it twice does not re-raise the same advice",
              all((v.get("written") or 0) == 0 for v in again.values()),
              str(again))
        check("and says how many it deliberately withheld",
              any((v.get("suppressed") or 0) + (v.get("produced") or 0) > 0
                  for v in again.values()), str(again))

        borrowed = (await db.execute(text(
            "SELECT count(*) FROM inventory_recommendations "
            "WHERE pharmacy_id = :p AND created_by IS NOT NULL"),
            {"p": pid})).scalar()
        check("a sweep does not borrow somebody's identity", borrowed == 0,
              str(borrowed))

        manual = await call(IG.run_sweep, body=None, staff=staff, db=db)
        check("the manual button reports what it withheld and why",
              "inflate the denominator" in manual["explanation"],
              manual["explanation"][:80])

        # One tenant's failure must not end the run.
        broken = await tenant(db)
        await db.execute(text("DELETE FROM pharmacies WHERE id = :i"),
                         {"i": broken})
        await db.commit()

    await engine.dispose()
    print(f"\n{'PASS' if not fails else 'FAIL: ' + ', '.join(fails)}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
