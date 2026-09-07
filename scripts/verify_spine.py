#!/usr/bin/env python
"""The spine, hosting the existing ledger simulation against a real database.

Phase 0 of the pilot plan. The acceptance test is deliberately conservative: the
existing simulator must keep working *unchanged* while the spine drives it, and
the spine must be able to carry a second domain in the same pass — because the
failures a whole-pharmacy pilot exists to find are the ones between domains,
where stock says the units left and money says nothing was owed.

    python scripts/verify_spine.py

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

from tests.simulation.domains.inventory import InventoryDomain     # noqa: E402
from tests.simulation.driver import Driver                          # noqa: E402
from tests.simulation.spine.harness import Harness                  # noqa: E402
from tests.simulation.world import build                            # noqa: E402

from verify_demand_shape import person                              # noqa: E402
from verify_receiving import tenant, url                            # noqa: E402


class MoneyStub:
    """A second domain, to prove the spine carries more than one.

    Deliberately a stub with a single real rule rather than a pricing oracle:
    Phase 1 builds that, and standing up half of it here would produce a module
    nobody trusts and everybody has to maintain. What it proves today is the
    property Phase 1 depends on — that a `dispensed` fact reaches money and stock
    identically, from one account of events.
    """

    name = "money"

    def __init__(self):
        self.units_dispensed = Decimal("0")
        self.events = 0
        self._broken: list[str] = []

    def observe(self, fact):
        if fact.kind != "dispensed":
            return
        self.events += 1
        qty = fact.quantity or Decimal("0")
        if qty < 0:
            # The oracle policing itself: a negative dispense is the simulation
            # being wrong, not the platform.
            self._broken.append(f"dispensed {qty} of {fact.subject}, which is "
                                f"not a quantity a dispense can have")
            return
        self.units_dispensed += qty

    async def check(self, ctx) -> list[str]:
        # Every unit this domain heard about must have a movement behind it.
        # Not a money rule yet — a wiring rule, which is what Phase 0 owes.
        got = (await ctx.db.execute(text(
            "SELECT COALESCE(SUM(-quantity_delta), 0) FROM inventory_movements "
            "WHERE pharmacy_id = :p AND movement_type = 'DISPENSE'"),
            {"p": ctx.pharmacy_id})).scalar()
        if Decimal(str(got or 0)) != self.units_dispensed:
            return [f"money heard {self.units_dispensed} unit(s) dispensed; the "
                    f"ledger records {got}"]
        return []

    def violations(self) -> list[str]:
        return list(self._broken)


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
        # The driver hangs every simulated prescription off a patient, and a
        # tenant created for receiving has none — the insert then fails on a
        # NULL patient_id and every dispense is refused, which is what the first
        # run of this script actually hit.
        await person(db, pid)
        world = build(seed=7, n_products=12)
        drv = Driver(db, world, pharmacy_id=pid)
        await drv.install_catalogue()
        await drv.receive_all()
        print(f"\ntenant {pid}\n")

        money = MoneyStub()
        h = Harness(db, start=date(2026, 1, 1), pharmacy_id=pid, domains=[
            InventoryDomain(drv.oracle,
                            app_state=drv.app_lot_state,
                            app_totals=drv.app_stock_levels,
                            app_valuation=drv.app_valuation),
            money,
        ])
        check("the spine carries more than one domain",
              h.report.domains == ["inventory", "money"], str(h.report.domains))

        # ── the existing simulator, unchanged, under the spine ───────────
        print("dispensing across simulated days")
        dispensed = Decimal("0")
        for day in range(6):
            for p in world.products[:6]:
                out = await drv.dispense(p.ndc11, 2)
                # `Outcome.payload` is the DispenseResult; the units that
                # actually moved are on it. There is no `.skipped` on Outcome —
                # the first version of this checked one and short-circuited every
                # row, so the money domain heard nothing and the run still passed
                # its other checks. A fan-out that silently carries no facts is
                # exactly the failure this script exists to catch.
                took = Decimal(str(getattr(out.payload, "allocated", 0) or 0))
                if not out.ok:
                    print(f"    dispense refused: {out.detail}")
                if took > 0:
                    # One action, described once. Inventory already recorded it
                    # as the driver acted — it alone knew which lot the
                    # application chose — so this is the money domain hearing
                    # the same event rather than a second version of it.
                    dispensed += took
                    h.fact("dispensed", subject=p.ndc11, quantity=took,
                           actor="alice")
            found = await h.check(f"day {day}")
            if found:
                for f in found:
                    print(f"    {f}")
                break
            h.advance()

        check("the ledger invariants still hold under the spine",
              h.report.ok, h.report.summary())
        check("and the run covered several simulated days",
              h.report.days >= 5, str(h.report.days))
        check("both domains were checked every round",
              h.report.checks >= 5, str(h.report.checks))
        check("the money domain heard the dispenses",
              money.events > 0 and money.units_dispensed == dispensed,
              f"{money.events} events, {money.units_dispensed} vs {dispensed}")
        print(f"  {h.report.summary()}")

        # ── the spine must report a disagreement, not hide it ────────────
        print("\na domain that disagrees with the database")
        money.units_dispensed += Decimal("999")
        found = await h.check("injected disagreement")
        check("a cross-domain disagreement is reported",
              any(f.domain == "money" for f in found), str(found))
        check("and it names both numbers",
              any("the ledger records" in f.detail for f in found),
              str([f.detail for f in found]))
        check("while the inventory domain still agrees",
              not any(f.domain == "inventory" for f in found),
              str([f.domain for f in found]))
        money.units_dispensed -= Decimal("999")

        # ── the oracle policing itself ───────────────────────────────────
        print("\nan oracle that broke its own rule")
        money.observe(type("F", (), {
            "kind": "dispensed", "quantity": Decimal("-5"), "subject": "N1"})())
        found = await h.check("self-check")
        check("the simulation reports itself rather than blaming the platform",
              any(f.scenario == "oracle self-check" for f in found),
              str([f.scenario for f in found]))

    await engine.dispose()
    print(f"\n{'PASS' if not fails else 'FAIL: ' + ', '.join(fails)}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
