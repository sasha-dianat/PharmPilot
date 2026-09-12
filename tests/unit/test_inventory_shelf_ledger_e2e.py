"""The shelf ledger through the real dispense path, in exact arithmetic.

`tests/unit/test_inventory_shelf.py` pins `shelf.allocate` as a pure function,
and it was always right: it computes in `Decimal` and quantises to three places.
The defect these pin is downstream of it, in how `_take_off_shelf` *writes* that
answer, and it was invisible to every pure-function test because no pure function
is involved.

`scripts/verify_shelf_domain.py` demonstrated it: 2.9 units dispensed off a
placement of 30 left `shelf_placements.units` reading 25 and
`pharmacy_shelves.current_units` reading 29, against a truth of 27.1. Two copies
of one number, four apart, neither correct. Both columns were `INTEGER`, the
placement was written as `float(take.after)` and the cache decremented by
`int(take.units)` — which is 0 for any take below one unit.

It matters because the shelf ledger is the "expected" side of the theft detector
in `services/core/inventory/shelf.py:reconcile`. An under-reported placement makes
a correct count read as SURPLUS; an over-reported cache makes the same count read
as MISSING. Liquids, creams and paediatric doses are ordinary and
`prescription_fills.quantity_dispensed` is `Numeric(10,3)`, so this is the common
path, not an edge case.

These run against the disposable test database because the bug lives in the DDL
and the SQL, which is exactly the layer a unit test with a fake session cannot
see.
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.core.inventory.dispense import apply_dispense


def _url() -> str | None:
    if os.getenv("TEST_DATABASE_URL"):
        return os.environ["TEST_DATABASE_URL"]
    dotenv = Path(__file__).resolve().parents[2] / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            m = re.match(r"^DATABASE_URL=(.+)$", line.strip())
            if m:
                # Anchored: an unanchored replacement points a test at production.
                return re.sub(r"/pharmpilot$", "/pharmpilot_test", m.group(1))
    return None


URL = _url()
pytestmark = [pytest.mark.asyncio,
              pytest.mark.skipif(not URL, reason="no test database configured")]

D = Decimal
RUN = uuid.uuid4().hex[:6].upper()


class Staff:
    def __init__(self, pharmacy_id, staff_id):
        self.pharmacy_id, self.id = pharmacy_id, staff_id


async def _a_shelf(db, pid, label, capacity=500) -> uuid.UUID:
    sid = uuid.uuid4()
    await db.execute(text("""
        INSERT INTO pharmacy_shelves
          (id, pharmacy_id, label, zone, capacity_units, current_units,
           storage_condition, is_active, created_at, updated_at, is_deleted)
        VALUES (:i,:p,:l,'retail',:c,0,'ROOM_TEMP',true,now(),now(),false)"""),
        {"i": sid, "p": pid, "l": label, "c": capacity})
    await db.commit()
    return sid


async def _a_lot(db, pid, drug_id, ndc, units) -> uuid.UUID:
    lot = uuid.uuid4()
    await db.execute(text("""
        INSERT INTO inventory_lots
          (id, pharmacy_id, drug_product_id, ndc11, lot_number, expiry_date,
           quantity_received, quantity_on_hand, quantity_reserved, unit_cost,
           received_at, is_recalled, is_quarantined, created_at, updated_at,
           is_deleted)
        VALUES (:i,:p,:d,:n,:ln,CAST(:e AS date),:u,:u,0,100.0,now(),false,
                false,now(),now(),false)"""),
        {"i": lot, "p": pid, "d": drug_id, "n": ndc,
         "ln": f"SHL-{RUN}-{uuid.uuid4().hex[:6]}",
         "e": date.today() + timedelta(days=365), "u": float(units)})
    await db.commit()
    return lot


async def _place(db, pid, *, lot, shelf, ndc, units) -> uuid.UUID:
    """Put units on a shelf the way `depot_transfer` does: row plus cached total."""
    plid = uuid.uuid4()
    await db.execute(text("""
        INSERT INTO shelf_placements
          (id, pharmacy_id, inventory_lot_id, shelf_id, ndc11, units,
           placed_at, created_at, updated_at, is_deleted)
        VALUES (:i,:p,:lot,:sh,:n,:u,:at,now(),now(),false)"""),
        {"i": plid, "p": pid, "lot": lot, "sh": shelf, "n": ndc,
         "u": float(units), "at": datetime.now(timezone.utc)})
    await db.execute(text(
        "UPDATE pharmacy_shelves SET current_units = current_units + :u "
        "WHERE id = :i"), {"u": float(units), "i": shelf})
    await db.commit()
    return plid


async def _an_rx(db, *, pid, patient, prescriber, ndc, qty):
    from shared.models.prescription import Prescription

    rid = uuid.uuid4()
    await db.execute(text("""
        INSERT INTO prescriptions
          (id, pharmacy_id, patient_id, prescriber_id, rx_number, ndc,
           drug_name, sig_text, quantity_prescribed, days_supply, written_date,
           source, status, refills_authorized, refills_remaining, is_controlled,
           created_at, updated_at, is_deleted)
        VALUES (:i,:ph,:pa,:pr,:rn,:n,'shelf drug','1 daily',:q,30,CURRENT_DATE,
                'escript','filled',0,0,false,now(),now(),false)"""),
        {"i": rid, "ph": pid, "pa": patient, "pr": prescriber,
         "rn": f"SHL-{RUN}-{uuid.uuid4().hex[:8]}", "n": ndc, "q": float(qty)})
    await db.commit()
    return (await db.execute(
        select(Prescription).where(Prescription.id == rid))).scalar_one()


async def _placement_units(db, plid) -> Decimal:
    return D(str((await db.execute(text(
        "SELECT units FROM shelf_placements WHERE id = :i"), {"i": plid})).scalar()))


async def _cached_units(db, sid) -> Decimal:
    return D(str((await db.execute(text(
        "SELECT current_units FROM pharmacy_shelves WHERE id = :i"),
        {"i": sid})).scalar()))


@pytest.fixture
async def env():
    engine = create_async_engine(URL)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    ndc = "95" + uuid.uuid4().int.__str__()[:9]
    async with sm() as db:
        pid = (await db.execute(text(
            "SELECT pharmacy_id FROM patients WHERE is_deleted = false "
            "LIMIT 1"))).scalar()
        patient = (await db.execute(text(
            "SELECT id FROM patients WHERE pharmacy_id = :p LIMIT 1"),
            {"p": pid})).scalar()
        prescriber = (await db.execute(text(
            "SELECT id FROM prescribers LIMIT 1"))).scalar()
        dp = uuid.uuid4()
        await db.execute(text("""
            INSERT INTO drug_products (id,ndc11,generic_name,strength,dosage_form,
              is_controlled,is_active,discontinued,is_generic,is_otc,is_hazardous,
              requires_refrigeration,high_risk_flag,drug_db_metadata,
              created_at,updated_at,is_deleted)
            VALUES (:i,:n,'shelfium','5 mg','SOLN',false,true,false,true,false,
                    false,false,false,'{}',now(),now(),false)"""),
            {"i": dp, "n": ndc})
        await db.commit()
        yield db, Staff(pid, uuid.uuid4()), ndc, dp, patient, prescriber
    await engine.dispose()


# ── the defect: one number, two copies, both wrong ────────────────────────
async def test_a_fractional_dispense_leaves_both_copies_of_the_shelf_exact(env):
    """2.9 off 30 is 27.1 in both columns, or the theft detector is fiction.

    This is the whole finding in one assertion. Before the fix the placement read
    25 and the cache read 29.
    """
    db, staff, ndc, dp, patient, prescriber = env
    shelf = await _a_shelf(db, staff.pharmacy_id, f"A-01-{RUN}")
    lot = await _a_lot(db, staff.pharmacy_id, dp, ndc, 100)
    plid = await _place(db, staff.pharmacy_id, lot=lot, shelf=shelf, ndc=ndc,
                        units=30)

    rx = await _an_rx(db, pid=staff.pharmacy_id, patient=patient,
                      prescriber=prescriber, ndc=ndc, qty=D("2.9"))
    res = await apply_dispense(db, rx, staff_id=staff.id,
                               now=datetime.now(timezone.utc))
    await db.commit()

    assert res.ok, res.error
    assert await _placement_units(db, plid) == D("27.100")
    assert await _cached_units(db, shelf) == D("27.100")


async def test_a_take_below_one_unit_still_moves_both_copies(env):
    """`int(take.units)` was 0 for any take under a unit, so half a bottle left
    the lot and the cached shelf total did not move at all."""
    db, staff, ndc, dp, patient, prescriber = env
    shelf = await _a_shelf(db, staff.pharmacy_id, f"A-02-{RUN}")
    lot = await _a_lot(db, staff.pharmacy_id, dp, ndc, 100)
    plid = await _place(db, staff.pharmacy_id, lot=lot, shelf=shelf, ndc=ndc,
                        units=10)

    rx = await _an_rx(db, pid=staff.pharmacy_id, patient=patient,
                      prescriber=prescriber, ndc=ndc, qty=D("0.5"))
    res = await apply_dispense(db, rx, staff_id=staff.id,
                               now=datetime.now(timezone.utc))
    await db.commit()

    assert res.ok, res.error
    assert await _placement_units(db, plid) == D("9.500")
    assert await _cached_units(db, shelf) == D("9.500")


async def test_the_movement_ledger_records_the_exact_fractional_delta(env):
    """`shelf_transfer_events` is the audit trail for shelf movement. Writing
    `-int(take.units)` recorded 0 for a fractional take, so the one record that
    could reconstruct the drift was itself rounded away."""
    db, staff, ndc, dp, patient, prescriber = env
    shelf = await _a_shelf(db, staff.pharmacy_id, f"A-03-{RUN}")
    lot = await _a_lot(db, staff.pharmacy_id, dp, ndc, 100)
    await _place(db, staff.pharmacy_id, lot=lot, shelf=shelf, ndc=ndc, units=30)

    rx = await _an_rx(db, pid=staff.pharmacy_id, patient=patient,
                      prescriber=prescriber, ndc=ndc, qty=D("2.9"))
    await apply_dispense(db, rx, staff_id=staff.id,
                         now=datetime.now(timezone.utc))
    await db.commit()

    delta = (await db.execute(text(
        "SELECT quantity_delta FROM shelf_transfer_events "
        " WHERE inventory_lot_id = :l AND shelf_id = :s"),
        {"l": lot, "s": shelf})).scalar()
    assert D(str(delta)) == D("-2.900")


async def test_many_small_takes_do_not_accumulate_error(env):
    """The drift compounds: each take rounded independently, so a day of
    paediatric doses walked the two copies steadily further apart."""
    db, staff, ndc, dp, patient, prescriber = env
    shelf = await _a_shelf(db, staff.pharmacy_id, f"A-04-{RUN}")
    lot = await _a_lot(db, staff.pharmacy_id, dp, ndc, 100)
    plid = await _place(db, staff.pharmacy_id, lot=lot, shelf=shelf, ndc=ndc,
                        units=40)

    for qty in (D("0.5"), D("0.4"), D("0.5"), D("1.5")):
        rx = await _an_rx(db, pid=staff.pharmacy_id, patient=patient,
                          prescriber=prescriber, ndc=ndc, qty=qty)
        await apply_dispense(db, rx, staff_id=staff.id,
                             now=datetime.now(timezone.utc))
        await db.commit()

    assert await _placement_units(db, plid) == D("37.100")
    assert await _cached_units(db, shelf) == D("37.100")


# ── the clamp: a plausible number in place of an unknown one ──────────────
async def test_a_cached_total_driven_below_zero_is_reported_not_clamped(env):
    """`GREATEST(0, ...)` wrote a plausible-looking zero over an unknown number.

    A cached total that cannot cover the take means the cache already disagreed
    with its own rows. Clamping it to zero is the fallback-constant pattern
    CLAUDE.md's provenance rule forbids: the shelf reads 0 and no row says why.
    The repaired path records the real arithmetic and flags it, so reconciliation
    can find it.
    """
    db, staff, ndc, dp, patient, prescriber = env
    shelf = await _a_shelf(db, staff.pharmacy_id, f"A-05-{RUN}")
    lot = await _a_lot(db, staff.pharmacy_id, dp, ndc, 100)
    await _place(db, staff.pharmacy_id, lot=lot, shelf=shelf, ndc=ndc, units=30)

    # The cache is now knowingly out of step with the rows it caches.
    await db.execute(text(
        "UPDATE pharmacy_shelves SET current_units = 3 WHERE id = :i"),
        {"i": shelf})
    await db.commit()

    rx = await _an_rx(db, pid=staff.pharmacy_id, patient=patient,
                      prescriber=prescriber, ndc=ndc, qty=10)
    res = await apply_dispense(db, rx, staff_id=staff.id,
                               now=datetime.now(timezone.utc))
    await db.commit()

    assert res.ok, res.error
    assert await _cached_units(db, shelf) == D("-7.000"), (
        "the arithmetic was clamped rather than recorded")

    takes = [t for t in res.shelf_takes if t["shelf_id"] == str(shelf)]
    assert takes and takes[0]["cached_total_inconsistent"] is True
    assert D(str(takes[0]["cached_total_after"])) == D("-7.000")


async def test_an_inconsistent_cached_total_is_recorded_on_the_movement(env):
    """The flag has to survive the request. A reconciliation weeks later reads
    rows, not a response body."""
    db, staff, ndc, dp, patient, prescriber = env
    shelf = await _a_shelf(db, staff.pharmacy_id, f"A-06-{RUN}")
    lot = await _a_lot(db, staff.pharmacy_id, dp, ndc, 100)
    await _place(db, staff.pharmacy_id, lot=lot, shelf=shelf, ndc=ndc, units=30)
    await db.execute(text(
        "UPDATE pharmacy_shelves SET current_units = 3 WHERE id = :i"),
        {"i": shelf})
    await db.commit()

    rx = await _an_rx(db, pid=staff.pharmacy_id, patient=patient,
                      prescriber=prescriber, ndc=ndc, qty=10)
    await apply_dispense(db, rx, staff_id=staff.id,
                         now=datetime.now(timezone.utc))
    await db.commit()

    basis = (await db.execute(text(
        "SELECT barcode_verification_result FROM shelf_transfer_events "
        " WHERE shelf_id = :s"), {"s": shelf})).scalar()
    assert basis["cached_total_inconsistent"] is True
    assert D(str(basis["cached_total_after"])) == D("-7.000")


async def test_a_cached_total_that_covers_the_take_is_not_flagged(env):
    """The flag must mean something. An ordinary dispense off a healthy shelf
    carries no inconsistency, or the signal is noise."""
    db, staff, ndc, dp, patient, prescriber = env
    shelf = await _a_shelf(db, staff.pharmacy_id, f"A-07-{RUN}")
    lot = await _a_lot(db, staff.pharmacy_id, dp, ndc, 100)
    await _place(db, staff.pharmacy_id, lot=lot, shelf=shelf, ndc=ndc, units=30)

    rx = await _an_rx(db, pid=staff.pharmacy_id, patient=patient,
                      prescriber=prescriber, ndc=ndc, qty=D("2.9"))
    res = await apply_dispense(db, rx, staff_id=staff.id,
                               now=datetime.now(timezone.utc))
    await db.commit()

    takes = [t for t in res.shelf_takes if t["shelf_id"] == str(shelf)]
    assert takes and takes[0]["cached_total_inconsistent"] is False
