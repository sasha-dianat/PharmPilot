#!/usr/bin/env python
"""Prove E11 and E12 on data the application itself produced.

The unit tests pin the arithmetic. This builds four suppliers with deliberately
different habits, drives every delivery through the real receiving endpoint, and
then asks `/inventory/suppliers` what it makes of them — so the scorecard is
computed from rows the platform wrote, not from a fixture shaped to agree with
it.

    python scripts/verify_supplier_scorecard.py

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

FAR = date.today() + timedelta(days=500)

# Four habits worth telling apart. The pair that matters is `steady` and
# `erratic`: `steady` is much the slower supplier and should still win, because
# eleven predictable days is already in the reorder point and four-days-usually-
# but-sometimes-sixteen is what nothing can be planned around.
HABITS = {
    "steady":   {"lead": [11] * 8, "fill": 1.0},
    "erratic":  {"lead": [2, 3, 16, 2, 15, 3, 2, 14], "fill": 1.0},
    "shorter":  {"lead": [5] * 8, "fill": 0.55},
    "newcomer": {"lead": [4], "fill": 1.0},
}


async def run_supplier(db, *, pid, staff, name: str, habit: dict, ndcs: list[str],
                       drug_ids: dict[str, uuid.UUID]) -> None:
    """Place and receive this supplier's orders through the real endpoints."""
    for i, days in enumerate(habit["lead"]):
        ndc = ndcs[i % len(ndcs)]
        oid = await order(db, pid=pid, lines=[(ndc, drug_ids[ndc], 100.0)],
                          ordered_days_ago=days, wholesaler=name)
        qty = round(100 * habit["fill"])
        await AD.receive_stock(AD.ReceiveLot(
            ndc11=ndc, lot_number=f"{name}-{i}", expiry_date=FAR,
            quantity=qty, unit_cost=10.0, purchase_order_id=oid), staff, db)


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
        # A shared basket, so the head-to-head is about the suppliers rather
        # than about who happens to carry the difficult products.
        ndcs = [f"{uuid.uuid4().int % 10**11:011d}" for _ in range(6)]
        drug_ids = {n: await product(db, n) for n in ndcs}
        print(f"\ntenant {pid}\n")

        for name, habit in HABITS.items():
            await run_supplier(db, pid=pid, staff=staff, name=name, habit=habit,
                               ndcs=ndcs, drug_ids=drug_ids)
        print(f"placed and received {sum(len(h['lead']) for h in HABITS.values())} "
              f"orders across {len(HABITS)} suppliers\n")

        out = await IG.supplier_scorecard(False, IG.SUPPLIER_WINDOW_DAYS, staff, db)
        table = {s["supplier"]: s for s in out["suppliers"]}
        for s in out["suppliers"]:
            print(f"  {s['supplier']:<9} score={str(s['score']):<6} "
                  f"grade={str(s['grade']):<11} lead={s['lead_days']}d "
                  f"fill={s['fill_rate']} basis={s['basis']}")
        print()

        # ── the claim the engine is built on ─────────────────────────────
        check("slow-and-steady outranks fast-and-erratic",
              table["steady"]["score"] > table["erratic"]["score"],
              f"{table['steady']['score']} vs {table['erratic']['score']}")
        check("and it really is the slower one",
              table["steady"]["lead_days"] > table["erratic"]["lead_days"],
              f"{table['steady']['lead_days']}d vs {table['erratic']['lead_days']}d")
        check("E11 measured the steady supplier at 11 days",
              table["steady"]["lead_days"] == 11,
              str(table["steady"]["lead_days"]))
        check("E11 reports it as observed, per supplier",
              table["steady"]["lead_time"]["basis"] == "observed",
              table["steady"]["lead_time"]["basis"])
        check("and gives the spread, not only the average",
              table["steady"]["lead_time"]["stdev_days"] == 0.0
              and table["erratic"]["lead_time"]["stdev_days"] > 5,
              f"{table['steady']['lead_time']['stdev_days']} / "
              f"{table['erratic']['lead_time']['stdev_days']}")

        # ── the blind spot, and the way out of it ────────────────────────
        # `shorter` never fills an order completely, so none of its orders ever
        # completes, so it has no lead time — the worst supplier on the roster
        # would look exactly like one that had never delivered. That is what
        # closing an order short exists to fix.
        check("a chronic short-shipper cannot be scored while its orders stay open",
              table["shorter"]["score"] is None, str(table["shorter"]["score"]))
        check("its lead time is the declared default, not a measurement",
              table["shorter"]["lead_time"]["basis"] == "declared_default",
              table["shorter"]["lead_time"]["basis"])
        check("but the reason is named rather than hidden",
              any("never closed out" in c for c in table["shorter"]["concerns"]),
              str(table["shorter"]["concerns"]))
        check("and it is filed for a decision without waiting for a score",
              "shorter" in {s["supplier"] for s in out["suppliers"]
                            if s["concerns"] and "never closed out" in
                            " ".join(s["concerns"])})

        outstanding = [dict(r) for r in (await db.execute(text(
            "SELECT o.id FROM purchase_orders o WHERE o.pharmacy_id = :p "
            "AND o.wholesaler = 'shorter' AND o.received_at IS NULL"),
            {"p": pid})).mappings().all()]
        print(f"\n  closing {len(outstanding)} short order(s) for 'shorter'")
        for row in outstanding:
            await AD.close_order_short(
                row["id"], AD.CloseOrder(reason="supplier confirmed no balance"),
                staff, db)

        after = await IG.supplier_scorecard(False, IG.SUPPLIER_WINDOW_DAYS, staff, db)
        t2 = {s["supplier"]: s for s in after["suppliers"]}
        check("once closed, the short filler becomes measurable",
              t2["shorter"]["score"] is not None, str(t2["shorter"]["score"]))
        check("its lead time is now observed",
              t2["shorter"]["lead_time"]["basis"] == "observed",
              t2["shorter"]["lead_time"]["basis"])
        check("closing the orders did not launder the shortfall",
              0.5 < t2["shorter"]["fill_rate"] < 0.6,
              str(t2["shorter"]["fill_rate"]))
        check("and it grades worst of the roster", t2["shorter"]["grade"] == "poor",
              str(t2["shorter"]["grade"]))
        for s in after["suppliers"]:
            print(f"  {s['supplier']:<9} score={str(s['score']):<6} "
                  f"grade={str(s['grade']):<11} lead={s['lead_days']}d "
                  f"fill={s['fill_rate']} basis={s['basis']}")
        out, table = after, t2

        # ── the trap this engine exists to avoid ─────────────────────────
        check("the newcomer is not scored", table["newcomer"]["score"] is None,
              str(table["newcomer"]["score"]))
        check("and it sorts last, not first",
              out["suppliers"][-1]["supplier"] == "newcomer",
              out["suppliers"][-1]["supplier"])
        check("its lead time is still reported, labelled sparse",
              table["newcomer"]["lead_time"]["basis"] == "sparse",
              table["newcomer"]["lead_time"]["basis"])
        check("three of four suppliers are measured once the books are closed",
              out["measured"] == 3, str(out["measured"]))

        # ── the head-to-head ─────────────────────────────────────────────
        h2h = out["head_to_head"]
        check("a head-to-head is offered between the top two measured",
              h2h is not None and h2h["verdict"] in ("steady", "too_close"),
              str(h2h))
        check("over a shared basket", h2h and h2h["shared_products"] >= 6,
              str(h2h and h2h["shared_products"]))

        # ── the point of a distribution *per supplier* ───────────────────
        # A blended average across every wholesaler is worth very little: an
        # item bought from the eleven-day supplier gets covered for four days
        # because someone else is quick. The reorder point is planned against
        # whoever last supplied that item.
        refresh = await IG.refresh_demand(False, 28, staff, db)
        per_sup = refresh["lead_time_by_supplier"]
        print(f"\n  lead time per supplier: "
              f"{ {k: v['days'] for k, v in per_sup.items()} }")
        check("the refresh knows each supplier separately",
              {"steady", "erratic", "shorter"} <= set(per_sup), str(set(per_sup)))
        check("and they are not the same number",
              len({v["days"] for v in per_sup.values()}) > 1,
              str({k: v["days"] for k, v in per_sup.items()}))
        check("every item is attributed to whoever last supplied it",
              all(r["supplier"] in per_sup for r in refresh["rows"]),
              str({r["supplier"] for r in refresh["rows"]}))

        # ── what reaches a person ────────────────────────────────────────
        filed = await IG.supplier_scorecard(True, IG.SUPPLIER_WINDOW_DAYS, staff, db)
        recs = filed.get("recommendations") or {}
        print(f"\n  recommendations: {recs}")
        rows = [dict(r) for r in (await db.execute(text(
            "SELECT proposal->>'supplier' AS supplier, ndc11, inventory_lot_id, "
            "       kind, severity, status "
            "FROM inventory_recommendations WHERE pharmacy_id = :p"),
            {"p": pid})).mappings().all()]
        subjects = {r["supplier"] for r in rows}
        check("a supplier is not filed as though it were a drug",
              all(r["ndc11"] is None and r["inventory_lot_id"] is None
                  for r in rows), str(rows[:1]))
        check("the short filler is filed for a decision", "shorter" in subjects,
              str(subjects))
        check("the newcomer is not — every supplier starts there",
              "newcomer" not in subjects, str(subjects))
        check("nor is the dependable one", "steady" not in subjects, str(subjects))

        again = await IG.supplier_scorecard(True, IG.SUPPLIER_WINDOW_DAYS, staff, db)
        check("running it twice does not raise the same advice twice",
              (again.get("recommendations") or {}).get("written") == 0,
              str(again.get("recommendations")))

    await engine.dispose()
    print(f"\n{'PASS' if not fails else 'FAIL: ' + ', '.join(fails)}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
