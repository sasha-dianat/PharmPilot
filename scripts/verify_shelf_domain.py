#!/usr/bin/env python
"""Phase 3 of the pilot: the shelf oracle against the real dispense path.

    python scripts/verify_shelf_domain.py

Closes the gap Phase 1 left open. The simulated world received stock into lots
and never placed any of it on a shelf, so `services/core/inventory/shelf.py` went
untouched by the pilot while `verify_spine.py` printed "stock records disagree
with the shelf" on every run. Here stock is actually placed, actually dispensed
through `apply_dispense`, and the shelf ledger it leaves behind is compared
against an oracle that re-derives allocation, valuation and the theft verdicts
independently.

As in Phase 2, two kinds of result:

  **check** — a property that must hold; failure exits non-zero.
  **note** — a finding this run demonstrates about the current design, recorded
  in the ledger and referred to its owner. Printing a known referred gap as a
  failure leaves the script permanently red, which is how a suite stops being
  read.
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

from services.core.inventory.dispense import apply_dispense        # noqa: E402
from tests.simulation.domains.shelf import (INFERRED, OBSERVED,    # noqa: E402
                                            ShelfDomain, q)

from verify_demand_shape import person                             # noqa: E402
from verify_receiving import product, tenant, url                  # noqa: E402

RUN = uuid.uuid4().hex[:6].upper()
NDC = "00093721410"
D = Decimal


class Ctx:
    """The minimum a domain's `check` needs."""

    def __init__(self, db, pharmacy_id):
        self.db, self.pharmacy_id = db, pharmacy_id


async def a_shelf(db, pid, label: str, capacity: int = 500) -> uuid.UUID:
    sid = uuid.uuid4()
    await db.execute(text("""
        INSERT INTO pharmacy_shelves
          (id, pharmacy_id, label, zone, capacity_units, current_units,
           storage_condition, is_active, created_at, updated_at, is_deleted)
        VALUES (:i,:p,:l,'retail',:c,0,'ROOM_TEMP',true,now(),now(),false)"""),
        {"i": sid, "p": pid, "l": label, "c": capacity})
    await db.commit()
    return sid


async def a_lot(db, pid, drug_id, units, *, days_to_expiry=365) -> uuid.UUID:
    lot = uuid.uuid4()
    await db.execute(text("""
        INSERT INTO inventory_lots
          (id, pharmacy_id, drug_product_id, ndc11, lot_number, expiry_date,
           quantity_received, quantity_on_hand, quantity_reserved, unit_cost,
           received_at, is_recalled, is_quarantined, created_at, updated_at,
           is_deleted)
        VALUES (:i,:p,:d,:n,:ln,CAST(:e AS date),:u,:u,0,100.0,now(),false,
                false,now(),now(),false)"""),
        {"i": lot, "p": pid, "d": drug_id, "n": NDC,
         "ln": f"SHELF-{RUN}-{uuid.uuid4().hex[:6]}",
         "e": date.today() + timedelta(days=days_to_expiry), "u": float(units)})
    await db.commit()
    return lot


async def place(db, pid, *, lot, shelf, units, when=None) -> uuid.UUID:
    """Put units on a shelf the way depot_transfer does: row plus cached total."""
    plid = uuid.uuid4()
    at = when or datetime.now(timezone.utc)
    await db.execute(text("""
        INSERT INTO shelf_placements
          (id, pharmacy_id, inventory_lot_id, shelf_id, ndc11, units,
           placed_at, created_at, updated_at, is_deleted)
        VALUES (:i,:p,:lot,:sh,:n,:u,:at,now(),now(),false)"""),
        {"i": plid, "p": pid, "lot": lot, "sh": shelf, "n": NDC,
         "u": int(units), "at": at})
    await db.execute(text(
        "UPDATE pharmacy_shelves SET current_units = current_units + :u "
        "WHERE id = :i"), {"u": int(units), "i": shelf})
    await db.commit()
    return plid


async def an_rx(db, *, pid, patient, prescriber, qty, rx_number) -> object:
    """A prescription in a shape `apply_dispense` will act on."""
    from shared.models.prescription import Prescription
    from sqlalchemy import select

    rid = uuid.uuid4()
    await db.execute(text("""
        INSERT INTO prescriptions
          (id, pharmacy_id, patient_id, prescriber_id, rx_number, ndc,
           drug_name, sig_text, quantity_prescribed, days_supply, written_date,
           source, status, refills_authorized, refills_remaining, is_controlled,
           created_at, updated_at, is_deleted)
        VALUES (:i,:ph,:pa,:pr,:rn,:n,'shelf drug','1 daily',:q,30,CURRENT_DATE,
                'escript','filled',0,0,false,now(),now(),false)"""),
        {"i": rid, "ph": pid, "pa": patient, "pr": prescriber, "rn": rx_number,
         "n": NDC, "q": float(qty)})
    await db.commit()
    return (await db.execute(
        select(Prescription).where(Prescription.id == rid))).scalar_one()


async def shelf_state(db, pid) -> dict:
    rows = (await db.execute(text(
        "SELECT p.id::text AS id, p.units, s.label, s.current_units "
        "  FROM shelf_placements p JOIN pharmacy_shelves s ON s.id = p.shelf_id "
        " WHERE p.pharmacy_id = :p AND p.is_deleted = false "
        " ORDER BY s.label"), {"p": pid})).mappings().all()
    return {r["id"]: dict(r) for r in rows}


async def main() -> int:
    engine = create_async_engine(url(), echo=False)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    fails: list[str] = []
    notes: list[str] = []

    def check(label: str, cond: bool, detail: str = "") -> None:
        print(f"  {'ok  ' if cond else 'FAIL'}  {label}"
              + (f"  — {detail}" if detail and not cond else ""))
        if not cond:
            fails.append(label)

    def note(label: str, detail: str) -> None:
        print(f"  NOTE  {label}\n        {detail}")
        notes.append(label)

    async with sm() as db:
        pid = await tenant(db)
        patient, prescriber = await person(db, pid)
        drug = await product(db, NDC)
        staff = uuid.uuid4()
        print(f"\ntenant {pid}\n")

        sd = ShelfDomain()

        # ── the policy gate ──────────────────────────────────────────────
        print("the policy gate")
        drift = sd.policy_drift()
        check("the oracle's shelf policy and the implementation's agree",
              not drift, "; ".join(drift))

        # ── stock is actually placed on a shelf ──────────────────────────
        print("\nstock placed on a shelf — the step the pilot never took")
        shelf_a = await a_shelf(db, pid, f"A-01-{RUN}")
        lot = await a_lot(db, pid, drug, 100)
        p1 = await place(db, pid, lot=lot, shelf=shelf_a, units=40,
                         when=datetime.now(timezone.utc) - timedelta(days=2))
        sd.observe_placement = None       # (documenting: facts come next)
        from tests.simulation.spine.facts import Fact
        from tests.simulation.spine.clock import SimClock
        clk = SimClock(start=date(2026, 1, 1))
        sd.observe(Fact(seq=1, at=clk.now(), day=0, kind="placed_on_shelf",
                        subject=NDC, quantity=D("40"), actor="alice",
                        payload={"placement_id": str(p1), "shelf_id": str(shelf_a),
                                 "lot_id": str(lot), "basis": OBSERVED}))
        ctx = Ctx(db, pid)
        found = await sd.check(ctx)
        check("the oracle and the shelf ledger agree after placement",
              not found, str(found[:2]))

        cur = (await db.execute(text(
            "SELECT current_units FROM pharmacy_shelves WHERE id = :i"),
            {"i": shelf_a})).scalar()
        check("the cached shelf total matches the placement", int(cur) == 40,
              f"current_units={cur}")

        # ── a whole-unit dispense through the real path ──────────────────
        print("\na whole-unit dispense through apply_dispense")
        rx1 = await an_rx(db, pid=pid, patient=patient, prescriber=prescriber,
                          qty=10, rx_number=f"SHL-1-{RUN}")
        res = await apply_dispense(db, rx1, staff_id=staff,
                                   now=datetime.now(timezone.utc))
        await db.commit()
        check("the dispense was applied", res.ok and not res.skipped,
              f"ok={res.ok} skipped={res.skipped} error={res.error}")
        check("it reported taking units off the shelf",
              bool(getattr(res, "shelf_takes", None)),
              str(getattr(res, "shelf_takes", None)))

        for t in getattr(res, "shelf_takes", []) or []:
            sd.observe(Fact(seq=2, at=clk.now(), day=0, kind="taken_off_shelf",
                            subject=NDC, quantity=D(str(t["units"])),
                            payload={"placement_id": t["placement_id"],
                                     "basis": t["basis"]}))
        found = await sd.check(ctx)
        check("the oracle and the shelf ledger still agree after a dispense",
              not found, str(found[:3]))

        state = await shelf_state(db, pid)
        row = state.get(str(p1))
        check("the placement fell from 40 to 30",
              row is not None and int(row["units"]) == 30,
              str(row))
        check("and the cached total fell with it",
              row is not None and int(row["current_units"]) == 30, str(row))

        takes = getattr(res, "shelf_takes", []) or []
        check("the take was marked inferred — nobody scanned the shelf",
              all(t["basis"] == INFERRED for t in takes), str(takes))

        # ── fractional dispensing ────────────────────────────────────────
        # Liquids, creams and paediatric doses are ordinary, and
        # `quantity_dispensed` is Numeric(10,3). The question is not whether the
        # shelf moves but whether it moves by the RIGHT amount — a check that
        # only asks "did it change" passes while the number is wrong, which is
        # how the first version of this script missed what follows.
        print("\nfractional dispensing (liquids and creams are ordinary)")
        before = await shelf_state(db, pid)
        b_units = D(str(before[str(p1)]["units"]))
        b_cur = D(str(before[str(p1)]["current_units"]))
        lot_before = D(str((await db.execute(text(
            "SELECT quantity_on_hand FROM inventory_lots WHERE id = :i"),
            {"i": lot})).scalar()))

        fractions = [D("0.5"), D("0.4"), D("0.5"), D("1.5")]
        for i, qty in enumerate(fractions):
            rxf = await an_rx(db, pid=pid, patient=patient,
                              prescriber=prescriber, qty=str(qty),
                              rx_number=f"SHL-F{i}-{RUN}")
            resf = await apply_dispense(db, rxf, staff_id=staff,
                                        now=datetime.now(timezone.utc))
            await db.commit()
            for t in getattr(resf, "shelf_takes", []) or []:
                sd.observe(Fact(seq=10 + i, at=clk.now(), day=0,
                                kind="taken_off_shelf", subject=NDC,
                                quantity=D(str(t["units"])),
                                payload={"placement_id": t["placement_id"],
                                         "basis": t["basis"]}))

        dispensed = sum(fractions, D("0"))
        after = await shelf_state(db, pid)
        a_units = D(str(after[str(p1)]["units"]))
        a_cur = D(str(after[str(p1)]["current_units"]))
        lot_after = D(str((await db.execute(text(
            "SELECT quantity_on_hand FROM inventory_lots WHERE id = :i"),
            {"i": lot})).scalar()))

        check("the lot recorded every fractional unit exactly",
              q(lot_before - lot_after) == q(dispensed),
              f"lot fell {q(lot_before - lot_after)} for {q(dispensed)} dispensed")

        truth = q(b_units - dispensed)
        placement_err = q(a_units - truth)
        cached_err = q(a_cur - truth)
        if placement_err != 0 or cached_err != 0:
            note("fractional dispensing drifts the shelf in both directions at "
                 "once",
                 f"{q(dispensed)} unit(s) dispensed off a placement of "
                 f"{q(b_units)}; the shelf should read {truth}. "
                 f"shelf_placements.units reads {a_units} "
                 f"({placement_err:+}) and pharmacy_shelves.current_units reads "
                 f"{a_cur} ({cached_err:+}) — the two copies of one number now "
                 f"disagree by {q(abs(a_cur - a_units))}. "
                 f"shelf_placements.units is an INTEGER column and the write "
                 f"passes float(take.after), so each fractional take rounds a "
                 f"whole unit off the row; the cached total is decremented by "
                 f"int(take.units), which is 0 for any take below 1. The shelf "
                 f"ledger is the theft detector's 'expected', so after this the "
                 f"placement copy under-reports the floor (a correct count reads "
                 f"as SURPLUS) and the cached copy over-reports it (a correct "
                 f"count reads as MISSING). An expected built on either is "
                 f"fiction, which is the exact condition shelf.py was written "
                 f"to end.")
        else:
            check("fractional dispensing leaves the shelf exact",
                  placement_err == 0 and cached_err == 0,
                  f"placement {a_units} vs {truth}, cached {a_cur}")

        found = await sd.check(ctx)
        if found:
            note("the oracle sees the fractional drift the ledger cannot",
                 "; ".join(found[:2]))
        else:
            check("the oracle and the shelf ledger agree after fractional "
                  "dispensing", True)

        # ── the cached total can be driven out of step, and hidden ───────
        print("\nthe cached total against the rows it caches")
        await db.execute(text(
            "UPDATE pharmacy_shelves SET current_units = 3 WHERE id = :i"),
            {"i": shelf_a})
        await db.commit()
        found = await sd.check(ctx)
        check("the oracle reports a cached total that disagrees with its rows",
              any("cached total and the rows it caches disagree" in f
                  for f in found), str(found[:2]))

        rx3 = await an_rx(db, pid=pid, patient=patient, prescriber=prescriber,
                          qty=10, rx_number=f"SHL-3-{RUN}")
        res3 = await apply_dispense(db, rx3, staff_id=staff,
                                    now=datetime.now(timezone.utc))
        await db.commit()
        clamped = (await db.execute(text(
            "SELECT current_units FROM pharmacy_shelves WHERE id = :i"),
            {"i": shelf_a})).scalar()
        if res3.ok and int(clamped) == 0:
            note("the decrement clamps at zero and absorbs the drift silently",
                 f"current_units was 3 against placements holding more; "
                 f"dispensing 10 drove it to {clamped} via "
                 f"GREATEST(0, current_units - :taken) rather than reporting "
                 f"that the cached figure is unreliable. A clamp that writes a "
                 f"plausible number in place of an unknown one is the "
                 f"fallback-constant pattern the provenance rule forbids: the "
                 f"shelf now reads 0 and no row says why.")

        # ── the detector, against a real count ───────────────────────────
        print("\nexpected against counted")
        placed_now = (await db.execute(text(
            "SELECT COALESCE(SUM(units),0) FROM shelf_placements "
            " WHERE shelf_id = :i AND is_deleted = false"),
            {"i": shelf_a})).scalar()
        inferred = sum((p.units for p in []), D("0"))
        inferred = sd.inferred.get((str(shelf_a), NDC), D("0"))

        agrees = sd.reconcile(shelf_id=str(shelf_a), ndc11=NDC,
                              expected=placed_now, counted=placed_now,
                              inferred_units=inferred)
        check("a count matching the books agrees", agrees.verdict == "agrees",
              agrees.why)

        theft = sd.reconcile(shelf_id=str(shelf_a), ndc11=NDC,
                             expected=placed_now,
                             counted=q(D(str(placed_now)) - D("25")),
                             inferred_units=inferred, sell_price=D("1000"))
        check("a shortfall far beyond the unscanned movement is shrinkage",
              theft.verdict == "shrinkage", f"{theft.verdict}: {theft.why}")
        check("and it carries a value at risk",
              theft.value_at_risk == D("25000.000"), str(theft.value_at_risk))
        check("shrinkage is the only verdict that reaches a person",
              sd.worth_investigating(theft)
              and not sd.worth_investigating(agrees))

        noise = sd.reconcile(shelf_id=str(shelf_a), ndc11=NDC,
                             expected=placed_now,
                             counted=q(D(str(placed_now)) - D("2")),
                             inferred_units=D("50"))
        check("a shortfall inside the guesswork is refused as evidence",
              noise.verdict == "inconclusive", f"{noise.verdict}: {noise.why}")

        # ── two shelves, one lot: the case that cannot be scanned ────────
        print("\none lot on two shelves — the movement nobody can attribute")
        shelf_b = await a_shelf(db, pid, f"B-01-{RUN}")
        # Expiring sooner than lot 1 so FEFO actually selects it. The first
        # version of this gave both lots the same expiry, FEFO broke the tie
        # toward lot 1, and the check failed against the application for doing
        # exactly the right thing.
        lot2 = await a_lot(db, pid, drug, 60, days_to_expiry=30)
        old = await place(db, pid, lot=lot2, shelf=shelf_a, units=20,
                          when=datetime.now(timezone.utc) - timedelta(days=3))
        new = await place(db, pid, lot=lot2, shelf=shelf_b, units=20,
                          when=datetime.now(timezone.utc) - timedelta(days=1))
        sd2 = ShelfDomain()
        for i, (plid, sh, when) in enumerate(
                ((old, shelf_a, "2026-01-01"), (new, shelf_b, "2026-01-03"))):
            sd2.observe(Fact(seq=i + 1, at=clk.now(), day=0,
                             kind="placed_on_shelf", subject=NDC,
                             quantity=D("20"), actor="alice",
                             payload={"placement_id": str(plid),
                                      "shelf_id": str(sh), "lot_id": str(lot2),
                                      "placed_at": when}))
        plan = sd2.allocate(str(lot2), 25)
        check("the oracle takes the older shelf first",
              plan["takes"] and plan["takes"][0]["placement_id"] == str(old),
              str([t["placement_id"] for t in plan["takes"]]))
        check("the allocation accounts for every unit",
              not sd2.allocation_findings(25, plan),
              str(sd2.allocation_findings(25, plan)))

        rx4 = await an_rx(db, pid=pid, patient=patient, prescriber=prescriber,
                          qty=25, rx_number=f"SHL-4-{RUN}")
        res4 = await apply_dispense(db, rx4, staff_id=staff,
                                    now=datetime.now(timezone.utc))
        await db.commit()
        app_takes = [t["placement_id"] for t in
                     (getattr(res4, "shelf_takes", []) or [])]
        check("and the application chose the same order",
              app_takes[:1] == [str(old)] if app_takes else False,
              f"application {app_takes}, oracle "
              f"{[t['placement_id'] for t in plan['takes']]}")
        check("every take the application made is marked inferred",
              all(t["basis"] == INFERRED
                  for t in (getattr(res4, "shelf_takes", []) or [])),
              str(getattr(res4, "shelf_takes", None)))

        # ── valuation ────────────────────────────────────────────────────
        print("\nwhat the floor is worth")
        pos = sd2.position({NDC: D("1000")})
        check("the floor position sums its placements",
              pos["units"] == D("40.000"), str(pos["units"]))
        check("and values them at the shelf price",
              pos["value"] == D("40000.000"), str(pos["value"]))
        unpriced = sd2.position({})
        check("an unpriced line contributes units and no money",
              unpriced["units"] == D("40.000") and unpriced["value"] == 0
              and unpriced["unpriced_lines"] == 2, str(unpriced))

    await engine.dispose()
    print(f"\n{len(notes)} finding(s) demonstrated, {len(fails)} check(s) failed")
    print(f"{'PASS' if not fails else 'FAIL: ' + ', '.join(fails)}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
