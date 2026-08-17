#!/usr/bin/env python
"""Prove E13 and the negotiation mentor on data the application produced.

Five molecules, each shaped to exercise one verdict, and two suppliers with
deliberately different prices and reliability. Every delivery goes through the
real receiving endpoint and every short order is closed through the real
close-short endpoint, so the fill rates the engines read are ones the platform
wrote.

    python scripts/verify_shortage_and_negotiation.py

Everything lands in `pharmpilot_test` under a pharmacy created for this run.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal
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
W = IG.SUPPLIER_WINDOW_DAYS

# Two suppliers. `dear` charges more and delivers worse — the case the
# negotiation brief is built for, and both of its cards are real.
DEAR, CHEAP = "dear", "cheap"

# Each molecule is shaped to land on exactly one verdict.
#   market   — short from BOTH suppliers: the molecule is out, not the relationship
#   onesup   — short from `dear`, filled by `cheap`: move the order, do not buy cover
#   creep    — a long clean history then a recent trim: the average still looks fine
#   thin     — arrives in full, but there is not enough of it on the shelf
#   fine     — nothing to say
SHAPES = {
    "market": {DEAR: [50] * 4, CHEAP: [55] * 4},
    "onesup": {DEAR: [45] * 4, CHEAP: [100] * 4},
    "creep":  {DEAR: [100] * 20 + [85] * 4},
    "thin":   {CHEAP: [100] * 4},
    "fine":   {CHEAP: [100] * 5},
}
COST = {DEAR: 12.0, CHEAP: 10.0}


async def build(db, staff, pid, ndcs, drug_ids) -> int:
    """Place, receive and settle every order. Returns how many were placed."""
    placed = 0
    for key, per_supplier in SHAPES.items():
        ndc = ndcs[key]
        for supplier, receipts in per_supplier.items():
            for i, got in enumerate(receipts):
                oid = await order(db, pid=pid, lines=[(ndc, drug_ids[ndc], 100.0)],
                                  ordered_days_ago=6, wholesaler=supplier,
                                  unit_cost=COST[supplier])
                await AD.receive_stock(AD.ReceiveLot(
                    ndc11=ndc, lot_number=f"{key}-{supplier}-{i}",
                    expiry_date=FAR, quantity=got, unit_cost=COST[supplier],
                    sell_price=COST[supplier] * 1.4,
                    purchase_order_id=oid), staff, db)
                placed += 1

    # Close every order that arrived short. Without this the lines never settle,
    # no fill rate exists, and E13 correctly reports `unknown` for the very
    # molecules it exists to warn about.
    open_orders = [r["id"] for r in (await db.execute(text(
        "SELECT id FROM purchase_orders WHERE pharmacy_id = :p "
        "AND received_at IS NULL"), {"p": pid})).mappings().all()]
    for oid in open_orders:
        await AD.close_order_short(
            oid, AD.CloseOrder(reason="supplier confirmed no balance"), staff, db)
    print(f"  placed {placed} orders, closed {len(open_orders)} short\n")
    return placed


async def set_demand(db, pid, ndc, *, adq, basis="observed", on_hand=None):
    """Stand in for a demand refresh — this tenant has no dispensing history.

    Written explicitly rather than left to a COALESCE: the point of the exercise
    is that an item with no measured demand reports unknown cover, so the ones
    that are supposed to have a measurement need one on the record.
    """
    await db.execute(text(
        "UPDATE stock_levels SET avg_daily_demand = :a, demand_basis = :b, "
        "safety_stock = 120 "
        + (", quantity_on_hand = :oh " if on_hand is not None else "")
        + "WHERE pharmacy_id = :p AND ndc11 = :n"),
        {"a": adq, "b": basis, "p": pid, "n": ndc,
         **({"oh": on_hand} if on_hand is not None else {})})
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
        ndcs = {k: f"{uuid.uuid4().int % 10**11:011d}" for k in SHAPES}
        drug_ids = {n: await product(db, n) for n in ndcs.values()}
        print(f"\ntenant {pid}\n")
        await build(db, staff, pid, ndcs, drug_ids)

        # A measured demand rate for most, and deliberately none for `fine`, so
        # the unknown-cover path is exercised too.
        # 80 units against 10/day is eight days of cover on a 20-day horizon:
        # the market is out AND the shelf is nearly empty, which is the one case
        # that stops being a purchasing problem and becomes a clinical one.
        await set_demand(db, pid, ndcs["market"], adq=10, on_hand=80)
        await set_demand(db, pid, ndcs["onesup"], adq=10)
        await set_demand(db, pid, ndcs["creep"], adq=1)
        await set_demand(db, pid, ndcs["thin"], adq=20, on_hand=100)

        # ── E13 ──────────────────────────────────────────────────────────
        rep = await IG.shortage_warning(W, False, staff, db)
        by = {s["ndc11"]: s for s in rep["signals"]}
        print("  E13:")
        for s in rep["signals"]:
            label = next(k for k, v in ndcs.items() if v == s["ndc11"])
            print(f"    {label:<7} {s['verdict']:<18} {s['action']:<18} "
                  f"fill={s['fill_rate']} cover={s['days_of_cover']} "
                  f"sev={s['severity']}")

        check("short from every supplier reads as the market",
              by[ndcs["market"]]["verdict"] == "market_shortage",
              by[ndcs["market"]]["verdict"])
        check("short from one while another delivers reads as that supplier",
              by[ndcs["onesup"]]["verdict"] == "supplier_shortage",
              by[ndcs["onesup"]]["verdict"])
        check("and the two get different actions",
              by[ndcs["market"]]["action"] != by[ndcs["onesup"]]["action"],
              f"{by[ndcs['market']]['action']} vs {by[ndcs['onesup']]['action']}")
        check("the switch is named, not a purchase",
              by[ndcs["onesup"]]["action"] == "switch_supplier",
              by[ndcs["onesup"]]["action"])
        check("the failing supplier is named",
              by[ndcs["onesup"]]["short_suppliers"] == [DEAR],
              str(by[ndcs["onesup"]]["short_suppliers"]))
        check("and so is the one that can cover it",
              by[ndcs["onesup"]]["filling_suppliers"] == [CHEAP],
              str(by[ndcs["onesup"]]["filling_suppliers"]))
        check("a long clean history hides the recent trim from the average",
              by[ndcs["creep"]]["fill_rate"] > 0.95,
              str(by[ndcs["creep"]]["fill_rate"]))
        check("but the creep detector catches it",
              by[ndcs["creep"]]["verdict"] == "watch",
              by[ndcs["creep"]]["verdict"])
        check("arriving fine with too little on the shelf is a reorder",
              by[ndcs["thin"]]["verdict"] == "thin_cover",
              by[ndcs["thin"]]["verdict"])
        check("and it says how much to buy",
              by[ndcs["thin"]]["suggested_buffer"] is not None,
              str(by[ndcs["thin"]]["suggested_buffer"]))
        check("no measured demand leaves cover unknown, not infinite",
              by[ndcs["fine"]]["days_of_cover"] is None,
              str(by[ndcs["fine"]]["days_of_cover"]))
        check("and says so rather than dropping the item",
              any("unknown rather than as plenty" in c
                  for c in by[ndcs["fine"]]["concerns"]))
        check("the horizon comes from E11, measured",
              by[ndcs["market"]]["lead_basis"] == "observed",
              by[ndcs["market"]]["lead_basis"])
        check("the two causes are counted separately",
              (rep["market_shortages"], rep["supplier_shortages"]) == (1, 1),
              f"{rep['market_shortages']}/{rep['supplier_shortages']}")
        check("nothing coming and nothing on the shelf escalates to prescribers",
              by[ndcs["market"]]["action"] == "alert_prescribers"
              and by[ndcs["market"]]["severity"] == "critical",
              f"{by[ndcs['market']]['action']}/{by[ndcs['market']]['severity']}")
        check("worst first", rep["signals"][0]["severity"] == "critical",
              rep["signals"][0]["severity"])

        filed = await IG.shortage_warning(W, True, staff, db)
        recs = filed.get("recommendations") or {}
        subjects = {r["ndc11"] for r in (await db.execute(text(
            "SELECT ndc11 FROM inventory_recommendations "
            "WHERE pharmacy_id = :p AND kind = 'shortage_warning'"),
            {"p": pid})).mappings().all()}
        print(f"  filed: {recs}")
        check("both shortages are filed for a decision",
              {ndcs["market"], ndcs["onesup"]} <= subjects, str(subjects))
        check("the healthy molecule is not", ndcs["fine"] not in subjects)
        again = await IG.shortage_warning(W, True, staff, db)
        check("running it twice does not re-raise the same advice",
              (again.get("recommendations") or {}).get("written") == 0,
              str(again.get("recommendations")))

        # ── ⑳ ───────────────────────────────────────────────────────────
        brief = await IG.negotiation_brief(DEAR, W, staff, db)
        print(f"\n  ⑳ brief for '{DEAR}':")
        print(f"    standing={brief['leverage']['standing']} "
              f"spend={brief['leverage']['spend']} "
              f"share={brief['leverage']['share']}")
        for g in brief["gaps"]:
            print(f"    gap {g['ndc11']}: {g['our_cost']} vs {g['best_cost']} "
                  f"({g['best_supplier']}) → {g['annual_value']}/yr")
        for a in brief["asks"]:
            print(f"    ask: {a['ask']} (worth {a['worth']}, {a['basis']})")

        check("the gap is measured against a price this pharmacy already pays",
              all(g["best_supplier"] == CHEAP for g in brief["gaps"]),
              str([g["best_supplier"] for g in brief["gaps"]]))
        check("and there is real money in it", brief["negotiable_annual"] > 0,
              str(brief["negotiable_annual"]))
        check("the strongest ask leads",
              brief["asks"] and brief["asks"][0]["ask"].startswith("match cheap"),
              str(brief["asks"][:1]))
        check("unreliability is priced from margin actually recorded",
              brief["reliability"]["forgone_margin"] is not None
              and brief["reliability"]["forgone_margin"] > 0,
              str(brief["reliability"]))
        check("undelivered units are counted",
              brief["reliability"]["undelivered_units"] > 0,
              str(brief["reliability"]["undelivered_units"]))
        check("a service-level ask follows the price ask",
              any("service-level" in a["ask"] for a in brief["asks"]))
        check("the payment window is never priced",
              all(c["worth"] is None for c in brief["concessions"]
                  if "payment window" in c["ask"]))
        check("no cross-pharmacy benchmark is claimed",
              brief["cloud"]["available"] is False
              and any("cross-pharmacy benchmark" in c
                      for c in brief["cannot_say"]))
        check("every ask carries a number or admits it cannot",
              all(a["worth"] is not None or a["basis"] == "unpriced"
                  for a in brief["asks"]))

        # The molecule only `cheap` supplies must not appear as a gap for
        # `cheap` — there is nothing to compare it against, and inventing a
        # market rate is the one thing this engine must never do.
        cheap_brief = await IG.negotiation_brief(CHEAP, W, staff, db)
        check("the cheaper supplier is offered no fabricated gap",
              cheap_brief["gaps"] == [], str(cheap_brief["gaps"]))
        check("and its brief still states what cannot be known",
              any("payment terms" in c for c in cheap_brief["cannot_say"]))

        nobody = await IG.negotiation_brief("never-used", W, staff, db)
        check("a supplier with no history gets a brief that says so",
              nobody["basis"] == "volume_only" and nobody["gaps"] == [],
              f"{nobody['basis']} / {nobody['gaps']}")
        check("and no asks are manufactured for it",
              all(a["worth"] is not None or a["basis"] == "unpriced"
                  for a in nobody["asks"]), str(nobody["asks"]))

    await engine.dispose()
    print(f"\n{'PASS' if not fails else 'FAIL: ' + ', '.join(fails)}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
