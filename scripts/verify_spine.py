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

from services.core.pricing_ir import config as CFG                  # noqa: E402
from services.core.pricing_ir.engine import (                       # noqa: E402
    DrugPrice, LineInput, price_prescription)
from tests.simulation.domains.inventory import InventoryDomain     # noqa: E402
from tests.simulation.domains.money import MoneyDomain             # noqa: E402
from tests.simulation.driver import Driver                          # noqa: E402
from tests.simulation.spine.harness import Harness                  # noqa: E402
from tests.simulation.world import build                            # noqa: E402

from verify_demand_shape import person                              # noqa: E402
from verify_receiving import tenant, url                            # noqa: E402


class LedgerWitness:
    """Phase 0's wiring rule, kept: every unit money heard about moved stock.

    This was the whole of the second domain when Phase 0 landed — a stub, because
    standing up half a pricing oracle would have produced a module nobody trusts.
    Phase 1 supplies the real one (`domains.money.MoneyDomain`), so what remains
    here is the narrower property the stub actually proved and which is still
    worth asserting: the fan-out reaches two domains with the same event, and the
    count that reached them matches the movement ledger.
    """

    name = "ledger-witness"

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
        got = (await ctx.db.execute(text(
            "SELECT COALESCE(SUM(-quantity_delta), 0) FROM inventory_movements "
            "WHERE pharmacy_id = :p AND movement_type = 'DISPENSE'"),
            {"p": ctx.pharmacy_id})).scalar()
        if Decimal(str(got or 0)) != self.units_dispensed:
            return [f"the witness heard {self.units_dispensed} unit(s) dispensed; "
                    f"the ledger records {got}"]
        return []

    def violations(self) -> list[str]:
        return list(self._broken)


def price_through_the_real_engine(ndc: str, qty: Decimal, unit_price: Decimal,
                                  reference: Decimal | None, plan_code: str):
    """Run the application's pricing engine and describe the result as a fact.

    The money domain re-derives this from the regulation and imports none of it,
    so what travels in the payload is the *inputs* plus what the engine
    concluded — the domain then disagrees or does not.
    """
    plan = CFG.get_plan(plan_code)
    out = price_prescription(
        [LineInput(drug=DrugPrice(irc=ndc, name=ndc, consumer_price=unit_price,
                                  insurer_reference_price=reference),
                   quantity=qty)],
        plan, technical_fee=CFG.DEFAULT_TECHNICAL_FEE_RIAL, setting="outpatient")
    return out, {
        "plan": plan_code, "setting": "outpatient",
        "technical_fee": str(CFG.DEFAULT_TECHNICAL_FEE_RIAL),
        "lines": [{"irc": ndc, "quantity": str(qty),
                   "consumer_price": str(unit_price),
                   "reference_price": None if reference is None else str(reference),
                   "category": "drug", "is_covered": True}],
        "engine": {"gross": str(out.totals.gross),
                   "insurer": str(out.totals.insurer),
                   "patient": str(out.totals.patient),
                   "differential": str(out.totals.differential),
                   "vat": str(out.totals.vat),
                   "fee_insurer": str(out.technical_fee.insurer)},
    }


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

        money = MoneyDomain()
        witness = LedgerWitness()
        h = Harness(db, start=date(2026, 1, 1), pharmacy_id=pid, domains=[
            InventoryDomain(drv.oracle,
                            app_state=drv.app_lot_state,
                            app_totals=drv.app_stock_levels,
                            app_valuation=drv.app_valuation),
            money,
            witness,
        ])
        check("the spine carries three domains",
              h.report.domains == ["inventory", "money", "ledger-witness"],
              str(h.report.domains))
        check("the money oracle's tariffs match the deployment's",
              not money.tariff_drift(), str(money.tariff_drift()))

        # ── the existing simulator, unchanged, under the spine ───────────
        print("pricing and dispensing across simulated days")
        dispensed = Decimal("0")
        collected = Decimal("0")
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
                    rx = f"RX-{day}-{p.ndc11}"
                    # Price it through the REAL engine, then tell the spine. A
                    # dispense the counter never priced is the cross-domain
                    # failure this harness exists to find, so the run has to
                    # exercise the priced path rather than assume it.
                    priced, payload = price_through_the_real_engine(
                        p.ndc11, took, Decimal("120000"), Decimal("90000"),
                        "tamin")
                    payload["rx"] = rx
                    h.fact("priced", subject=rx, money=priced.totals.grand_total,
                           payload=payload)
                    h.fact("dispensed", subject=p.ndc11, quantity=took,
                           actor="alice", payload={"rx": rx})
                    h.fact("paid", subject=rx, money=priced.totals.patient,
                           actor="cashier", payload={"rx": rx})
                    h.fact("adjudicated", subject=rx,
                           money=priced.totals.insurer, payload={"rx": rx})
                    collected += priced.totals.patient
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
        check("all three domains were checked every round",
              h.report.checks >= 5, str(h.report.checks))
        check("the witness heard the dispenses",
              witness.events > 0 and witness.units_dispensed == dispensed,
              f"{witness.events} events, {witness.units_dispensed} vs {dispensed}")
        check("the money oracle priced every dispense",
              len(money.priced_rx) == witness.events,
              f"{len(money.priced_rx)} priced vs {witness.events} dispensed")
        check("and it agrees with the engine on all of them",
              not money._disagreements, str(money._disagreements[:3]))
        check("what was collected equals what the regulation says was owed",
              sum(money.paid_rx.values()) == collected,
              f"{sum(money.paid_rx.values())} vs {collected}")
        print(f"  {h.report.summary()}")
        print(f"  collected {collected} Rial from the patient across the run")

        # ── the spine must report a disagreement, not hide it ────────────
        print("\na domain that disagrees with the database")
        witness.units_dispensed += Decimal("999")
        found = await h.check("injected ledger disagreement")
        check("a cross-domain disagreement is reported",
              any(f.domain == "ledger-witness" for f in found), str(found))
        check("and it names both numbers",
              any("the ledger records" in f.detail for f in found),
              str([f.detail for f in found]))
        check("while the inventory domain still agrees",
              not any(f.domain == "inventory" for f in found),
              str([f.domain for f in found]))
        witness.units_dispensed -= Decimal("999")

        # ── a real money defect, not a stubbed one ───────────────────────
        print("\nmoney that does not add up")
        short_rx = "RX-SHORTCHANGED"
        _, payload = price_through_the_real_engine(
            world.products[0].ndc11, Decimal("1"), Decimal("120000"),
            Decimal("90000"), "tamin")
        payload["rx"] = short_rx
        h.fact("priced", subject=short_rx, payload=payload)
        # The counter collects a round number instead of what was owed.
        h.fact("paid", subject=short_rx, money=Decimal("500000"),
               actor="cashier", payload={"rx": short_rx})
        found = await h.check("undercollection")
        check("collecting less than was owed is reported",
              any(f.domain == "money" and "collected 500000" in f.detail
                  for f in found), str([f.detail for f in found][:3]))
        money.paid_rx.pop(short_rx, None)
        money.quotes.pop(short_rx, None)
        money.priced_rx.discard(short_rx)

        # ── units that left with nobody pricing them ─────────────────────
        print("\nstock says the units left; money says nothing was owed")
        h.fact("dispensed", subject=world.products[0].ndc11,
               quantity=Decimal("5"), actor="alice",
               payload={"rx": "RX-NEVER-PRICED"})
        found = await h.check("dispense without a price")
        check("an unpriced dispense is reported by money",
              any(f.domain == "money" and "never priced" in f.detail
                  for f in found), str([f.detail for f in found][:3]))
        money.dispensed_units.pop("RX-NEVER-PRICED", None)
        witness.units_dispensed += Decimal("5")   # the witness heard it too

        # ── the oracle policing itself ───────────────────────────────────
        print("\nan oracle that broke its own rule")
        # An unknown plan code: the engine falls back to self-pay by design, so
        # the oracle must say it cannot judge the line rather than quietly
        # charging the patient 100% and calling that agreement.
        money.price_line(
            __import__('tests.simulation.domains.money', fromlist=['Line']).Line(
                "X", Decimal("1"), Decimal("1000"), None, "drug", True),
            "tamiin", "outpatient")
        found = await h.check("self-check")
        check("the simulation reports itself rather than blaming the platform",
              any(f.scenario == "oracle self-check" and f.domain == "money"
                  for f in found),
              str([(f.domain, f.scenario) for f in found]))

    await engine.dispose()
    print(f"\n{'PASS' if not fails else 'FAIL: ' + ', '.join(fails)}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
