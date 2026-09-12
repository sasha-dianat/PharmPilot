"""Escalating phases, from known answers to adversarial noise.

Each phase raises the diversity of what the application is asked to survive
rather than merely repeating more of the same. The order matters: a defect
found in phase 1 is a arithmetic error with a two-line reproduction, and the
same defect found first in phase 5 arrives wrapped in four hundred concurrent
events.

Every phase returns findings, not assertions, so a run keeps going and reports
everything it found instead of stopping at the first disagreement.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from . import invariants as INV
from .driver import Driver
from .oracle import q
from .world import World, build, daily_demand


@dataclass
class Finding:
    phase: str
    seed: int
    scenario: str
    detail: str
    events: int

    def __str__(self) -> str:
        return (f"[{self.phase}] seed={self.seed} after {self.events} events "
                f"— {self.scenario}: {self.detail}")


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    events: int = 0
    checks: int = 0
    scenarios: list[str] = field(default_factory=list)

    def add(self, phase, seed, scenario, details: list[str], events: int) -> None:
        for d in details:
            self.findings.append(Finding(phase, seed, scenario, d, events))

    @property
    def ok(self) -> bool:
        return not self.findings


async def check_all(drv: Driver, rep: Report, phase: str, scenario: str) -> None:
    """Compare both worlds and the database's own rules."""
    rep.checks += 1
    app_lots = await drv.app_lot_state()
    rep.add(phase, drv.world.seed, scenario,
            INV.lots_agree(app_lots, drv.oracle), rep.events)
    rep.add(phase, drv.world.seed, scenario,
            INV.sku_totals_agree(await drv.app_stock_levels(), drv.oracle),
            rep.events)
    rep.add(phase, drv.world.seed, scenario,
            await INV.structural(drv.db, drv.pid), rep.events)
    # The oracle policing itself: if this fires the simulation is at fault.
    rep.add(phase, drv.world.seed, scenario,
            INV.valuation_agrees(await drv.app_valuation(), drv.oracle),
            rep.events)
    rep.add(phase, drv.world.seed, "oracle self-check",
            drv.oracle.violations(), rep.events)


# ── phase 1: known answers ────────────────────────────────────────────────

async def phase_known_answers(drv: Driver, rep: Report) -> None:
    """Hand-computable cases. If these fail nothing later means anything."""
    p = drv.world.products[0]
    far = drv.world.as_of + timedelta(days=400)

    await drv.receive(p.ndc11, "KA-1", 100, expiry=far, unit_cost=Decimal("10"))
    rep.events += 1
    await check_all(drv, rep, "1-known", "receive 100")

    await drv.dispense(p.ndc11, 30)
    rep.events += 1
    await check_all(drv, rep, "1-known", "dispense 30 of 100")

    await drv.to_bucket("KA-1", 10, movement_type="DAMAGE")
    rep.events += 1
    await check_all(drv, rep, "1-known", "damage 10 — units move, none vanish")

    await drv.write_off("KA-1", 10, movement_type="WASTE", from_bucket="damaged")
    rep.events += 1
    await check_all(drv, rep, "1-known", "approved write-off of the damaged 10")

    rep.scenarios.append("known-answer arithmetic")


# ── phase 2: ordinary trading ─────────────────────────────────────────────

async def phase_normal(drv: Driver, rep: Report, *, days: int = 20) -> None:
    """A stretch of unremarkable days, so the boring path is exercised too."""
    rng = random.Random(drv.world.seed ^ 0x2)
    for day in range(days):
        for prod in drv.world.products:
            want = daily_demand(prod, day, rng)
            if want:
                await drv.dispense(prod.ndc11, want)
                rep.events += 1
        if day % 5 == 0:
            await check_all(drv, rep, "2-normal", f"day {day}")
    await check_all(drv, rep, "2-normal", f"{days} trading days")
    rep.scenarios.append(f"{days} days of ordinary dispensing")


# ── phase 3: edges ────────────────────────────────────────────────────────

async def phase_edges(drv: Driver, rep: Report) -> None:
    """The values that break arithmetic and the states that break rules."""
    p = drv.world.products[1]
    far = drv.world.as_of + timedelta(days=500)

    cases = [
        ("zero receipt refused", lambda: drv.receive(p.ndc11, "EDGE-0", 0, expiry=far, unit_cost=1)),
        ("negative receipt refused", lambda: drv.receive(p.ndc11, "EDGE-N", -5, expiry=far, unit_cost=1)),
        ("expired receipt refused", lambda: drv.receive(
            p.ndc11, "EDGE-X", 10, expiry=drv.world.as_of - timedelta(days=1), unit_cost=1)),
        ("fractional receipt", lambda: drv.receive(p.ndc11, "EDGE-F", Decimal("0.125"), expiry=far, unit_cost=Decimal("3.3333"))),
        ("huge receipt", lambda: drv.receive(p.ndc11, "EDGE-BIG", Decimal("9999999.999"), expiry=far, unit_cost=Decimal("0.0001"))),
        ("one unit", lambda: drv.receive(p.ndc11, "EDGE-1", 1, expiry=far, unit_cost=Decimal("7"))),
    ]
    for name, run in cases:
        await run()
        rep.events += 1
        await check_all(drv, rep, "3-edge", name)

    # Over-dispensing the shelf: the hook must record a shortfall, never a
    # negative balance.
    await drv.dispense(p.ndc11, 9_000_000)
    rep.events += 1
    await check_all(drv, rep, "3-edge", "dispense far beyond stock")

    # Writing off more than exists, from both sellable stock and a bucket.
    await drv.write_off("EDGE-1", 999, movement_type="WASTE")
    rep.events += 1
    await check_all(drv, rep, "3-edge", "write off more than the lot holds")
    await drv.write_off("EDGE-1", 5, movement_type="WASTE", from_bucket="damaged")
    rep.events += 1
    await check_all(drv, rep, "3-edge", "write off from an empty bucket")

    # Bucket round trip: units must survive it exactly.
    await drv.receive(p.ndc11, "EDGE-RT", 50, expiry=far, unit_cost=Decimal("2"))
    await drv.to_bucket("EDGE-RT", 50, movement_type="TRANSFER_OUT")
    rep.events += 2
    await check_all(drv, rep, "3-edge", "whole lot into transit")

    rep.scenarios.append("boundary and extreme values")


# ── phase 4: adversarial ──────────────────────────────────────────────────

async def phase_adversarial(drv: Driver, rep: Report, *, rounds: int = 120) -> None:
    """Randomised, contradictory, replayed and out-of-order traffic."""
    rng = random.Random(drv.world.seed ^ 0xADD)
    far = drv.world.as_of + timedelta(days=365)
    known = list(drv.lot_ids.keys())

    for i in range(rounds):
        pick = rng.random()
        prod = rng.choice(drv.world.products)

        if pick < 0.30:
            await drv.dispense(prod.ndc11, rng.choice((1, 3, 30, 500)))
        elif pick < 0.45:
            await drv.receive(prod.ndc11, f"ADV-{drv.world.seed}-{i}",
                              rng.choice((1, 10, 250)), expiry=far,
                              unit_cost=Decimal(rng.choice(("0.5", "3", "99"))))
            known = list(drv.lot_ids.keys())
        elif pick < 0.60 and known:
            await drv.to_bucket(rng.choice(known), rng.choice((1, 5, 40)),
                                movement_type=rng.choice(
                                    ("DAMAGE", "TRANSFER_OUT", "RETURN_TO_SUPPLIER")))
        elif pick < 0.72 and known:
            await drv.write_off(rng.choice(known), rng.choice((1, 7, 100)),
                                movement_type=rng.choice(("WASTE", "EXPIRY_REMOVAL")))
        elif pick < 0.86:
            out = await drv.reserve(prod.ndc11, rng.choice((1, 5, 60)))
            # Half of the reservations are then cancelled, so the release path
            # gets as much traffic as the reserve path.
            if out.ok and out.payload and rng.random() < 0.5:
                await drv.release(out.payload[1])
        else:
            # A replay: the same dispense submitted twice. The second must not
            # move stock again.
            await drv.dispense(prod.ndc11, 2)
        rep.events += 1

        if i % 20 == 19:
            await check_all(drv, rep, "4-adversarial", f"round {i}")

    await check_all(drv, rep, "4-adversarial", f"{rounds} adversarial rounds")
    rep.scenarios.append(f"{rounds} rounds of mixed adversarial traffic")


# ── phase 5: replay, reversal, counting, and money ────────────────────────

async def phase_replay_and_counts(drv: Driver, rep: Report) -> None:
    """The paths the other phases never touch.

    Each of these is a place where an inventory system classically loses or
    invents stock: a retried request, a physical count, units coming back out
    of a holding bucket, and the valuation that has to follow all of them.
    """
    p = drv.world.products[2]
    far = drv.world.as_of + timedelta(days=450)

    await drv.receive(p.ndc11, "RPL-1", 200, expiry=far, unit_cost=Decimal("4.5"))
    rep.events += 1

    # A dispense, then the identical request again. The second must move nothing.
    out, rx = await drv.dispense_tracked(p.ndc11, 40)
    rep.events += 1
    await check_all(drv, rep, "5-replay", "first dispense of 40")

    if rx is not None:
        again = await drv.dispense_again(rx)
        rep.events += 1
        moved = again.payload[1] if again.payload else None
        if moved and moved != 0:
            rep.add("5-replay", drv.world.seed, "duplicate dispense",
                    [f"a replayed dispense moved {moved} more units; a retried "
                     f"request must be a no-op"], rep.events)
        await check_all(drv, rep, "5-replay", "the same dispense submitted twice")

    # A count that finds fewer units than the book says, and one that finds more.
    await drv.count("RPL-1", 150)
    rep.events += 1
    await check_all(drv, rep, "5-replay", "counted short — approved COUNT_LOSS")

    await drv.count("RPL-1", 175)
    rep.events += 1
    await check_all(drv, rep, "5-replay", "counted over — approved COUNT_GAIN")

    # Units into a bucket and back out again: none may be lost in the round trip.
    await drv.to_bucket("RPL-1", 60, movement_type="TRANSFER_OUT")
    rep.events += 1
    await check_all(drv, rep, "5-replay", "60 units into transit")

    await drv.release_bucket("RPL-1", 60, from_bucket="in_transit")
    rep.events += 1
    await check_all(drv, rep, "5-replay", "the same 60 units back on the shelf")

    rep.scenarios.append("replay, counting, bucket round trip, valuation")


async def run_all(drv: Driver, *, days: int = 20, rounds: int = 120) -> Report:
    rep = Report()
    await phase_known_answers(drv, rep)
    await phase_normal(drv, rep, days=days)
    await phase_edges(drv, rep)
    await phase_adversarial(drv, rep, rounds=rounds)
    await phase_replay_and_counts(drv, rep)
    return rep
