"""The reservation lifecycle against a real database.

The behaviour worth proving end to end is the one that was impossible before:
two prescriptions cannot both be promised the same last box. Everything else
here exists to show the units come back — on cancellation, on a lapsed hold, and
on dispensing — because a reservation that is never released is just a slower
way to lose stock.
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.core.inventory import reservation_service as RS
from services.core.inventory import reservations as RSV
from services.platform.routers import inventory_admin as AD


def _url() -> str | None:
    if os.getenv("TEST_DATABASE_URL"):
        return os.environ["TEST_DATABASE_URL"]
    dotenv = Path(__file__).resolve().parents[2] / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            m = re.match(r"^DATABASE_URL=(.+)$", line.strip())
            if m:
                return re.sub(r"/pharmpilot$", "/pharmpilot_test", m.group(1))
    return None


URL = _url()
pytestmark = [pytest.mark.asyncio,
              pytest.mark.skipif(not URL, reason="no test database configured")]


class FakeStaff:
    def __init__(self, pharmacy_id, staff_id):
        self.pharmacy_id, self.id = pharmacy_id, staff_id


class FakeRx:
    """The subset of a Prescription the reservation service reads.

    Backed by a real `prescriptions` row: the reservation carries a foreign key
    to it, and a fabricated id would only prove the test can bypass the schema.
    """
    def __init__(self, rx_id, pharmacy_id, ndc, qty, rx_number="RX-TEST"):
        self.id = rx_id
        self.pharmacy_id, self.ndc = pharmacy_id, ndc
        self.quantity_dispensed = qty
        self.quantity_prescribed = qty
        self.rx_number = rx_number


async def make_rx(db, pharmacy_id, ndc, qty) -> FakeRx:
    """Insert a real prescription and return a handle to it."""
    rx_id = uuid.uuid4()
    pat = (await db.execute(text(
        "SELECT id FROM patients WHERE pharmacy_id = :p LIMIT 1"),
        {"p": pharmacy_id})).scalar()
    pres = (await db.execute(text("SELECT id FROM prescribers LIMIT 1"))).scalar()
    await db.execute(text("""
        INSERT INTO prescriptions
          (id, pharmacy_id, patient_id, prescriber_id, rx_number, ndc, drug_name,
           sig_text, quantity_prescribed, days_supply, written_date, source,
           status, refills_authorized, refills_remaining, is_controlled,
           created_at, updated_at, is_deleted)
        VALUES (:i,:ph,:pa,:pr,:rn,:n,'reservium','1 tab daily',:q,30,
                CURRENT_DATE,'escript','ready_to_fill',0,0,false,
                now(),now(),false)"""), {
        "i": rx_id, "ph": pharmacy_id, "pa": pat, "pr": pres,
        "rn": f"RX{uuid.uuid4().hex[:10].upper()}", "n": ndc, "q": qty})
    return FakeRx(rx_id, pharmacy_id, ndc, qty)


@pytest.fixture
async def env():
    engine = create_async_engine(URL)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    ndc = "96" + uuid.uuid4().int.__str__()[:9]
    async with sm() as db:
        pid = (await db.execute(text("SELECT id FROM pharmacies LIMIT 1"))).scalar()
        dp = uuid.uuid4()
        await db.execute(text("""
            INSERT INTO drug_products (id,ndc11,generic_name,strength,dosage_form,
              is_controlled,is_active,discontinued,is_generic,is_otc,is_hazardous,
              requires_refrigeration,high_risk_flag,drug_db_metadata,
              created_at,updated_at,is_deleted)
            VALUES (:i,:n,'reservium','5 mg','TAB',false,true,false,true,false,
                    false,false,false,'{}',now(),now(),false)"""),
            {"i": dp, "n": ndc})
        await db.commit()
        yield db, FakeStaff(pid, uuid.uuid4()), ndc
    await engine.dispose()


async def _receive(db, staff, ndc, lot, qty=100, days=300):
    return await AD.receive_stock(AD.ReceiveLot(
        ndc11=ndc, lot_number=lot, expiry_date=date.today() + timedelta(days=days),
        quantity=qty, unit_cost=10, storage_location="SHELF-V1"), staff=staff, db=db)


async def _reserved_on_lots(db, staff, ndc) -> Decimal:
    return Decimal(str((await db.execute(text(
        "SELECT COALESCE(SUM(quantity_reserved),0) FROM inventory_lots "
        "WHERE pharmacy_id = :p AND ndc11 = :n AND is_deleted = false"),
        {"p": staff.pharmacy_id, "n": ndc})).scalar()))


# ── the double-promise this feature exists to prevent ─────────────────────
async def test_two_prescriptions_cannot_be_promised_the_same_units(env):
    """Before reservations existed, `available` always equalled on-hand, so both
    of these succeeded and the second patient found an empty shelf."""
    db, staff, ndc = env
    await _receive(db, staff, ndc, f"L{uuid.uuid4().hex[:6]}", qty=100)

    first = await RS.reserve(db, await make_rx(db, staff.pharmacy_id, ndc, 80), staff_id=staff.id)
    assert first.ok and first.reserved == Decimal("80.000")

    second = await RS.reserve(db, await make_rx(db, staff.pharmacy_id, ndc, 80), staff_id=staff.id)
    assert second.ok is False
    assert "insufficient stock" in (second.error or "")
    assert "allocatable 20" in (second.error or "")


async def test_reserving_raises_the_counter_on_the_lot_and_the_stock_level(env):
    db, staff, ndc = env
    await _receive(db, staff, ndc, f"L{uuid.uuid4().hex[:6]}", qty=100)
    await RS.reserve(db, await make_rx(db, staff.pharmacy_id, ndc, 30), staff_id=staff.id)

    assert await _reserved_on_lots(db, staff, ndc) == Decimal("30.000")
    agg = (await db.execute(text(
        "SELECT quantity_reserved FROM stock_levels WHERE pharmacy_id=:p AND ndc11=:n"),
        {"p": staff.pharmacy_id, "n": ndc})).scalar()
    assert Decimal(str(agg)) == Decimal("30.000")


async def test_a_reservation_takes_the_soonest_expiring_lot(env):
    db, staff, ndc = env
    await _receive(db, staff, ndc, "FAR", qty=50, days=400)
    await _receive(db, staff, ndc, "NEAR", qty=50, days=30)
    await RS.reserve(db, await make_rx(db, staff.pharmacy_id, ndc, 40), staff_id=staff.id)

    rows = (await db.execute(text("""
        SELECT il.lot_number, il.quantity_reserved FROM inventory_lots il
        WHERE il.pharmacy_id=:p AND il.ndc11=:n AND il.quantity_reserved > 0"""),
        {"p": staff.pharmacy_id, "n": ndc})).mappings().all()
    assert [(r["lot_number"], float(r["quantity_reserved"])) for r in rows] == \
        [("NEAR", 40.0)]


async def test_reserving_twice_for_one_prescription_does_not_double_hold(env):
    """A re-entered state or a redelivered event must not sterilise the shelf."""
    db, staff, ndc = env
    await _receive(db, staff, ndc, f"L{uuid.uuid4().hex[:6]}", qty=100)
    rx = await make_rx(db, staff.pharmacy_id, ndc, 30)
    await RS.reserve(db, rx, staff_id=staff.id)
    again = await RS.reserve(db, rx, staff_id=staff.id)

    assert again.skipped == "already reserved"
    assert await _reserved_on_lots(db, staff, ndc) == Decimal("30.000")


# ── giving the units back ─────────────────────────────────────────────────
async def test_cancelling_returns_the_units_to_availability(env):
    db, staff, ndc = env
    await _receive(db, staff, ndc, f"L{uuid.uuid4().hex[:6]}", qty=100)
    rx = await make_rx(db, staff.pharmacy_id, ndc, 60)
    await RS.reserve(db, rx, staff_id=staff.id)

    out = await RS.release_for_transition(db, rx, "CANCELLED")
    assert out.released == 1
    assert await _reserved_on_lots(db, staff, ndc) == Decimal("0.000")

    status, reason = (await db.execute(text(
        "SELECT status, reason FROM inventory_reservations WHERE prescription_id=:r"),
        {"r": rx.id})).first()
    assert (status, reason) == ("released", "prescription cancelled")


async def test_a_transition_that_still_intends_to_dispense_keeps_the_hold(env):
    db, staff, ndc = env
    await _receive(db, staff, ndc, f"L{uuid.uuid4().hex[:6]}", qty=100)
    rx = await make_rx(db, staff.pharmacy_id, ndc, 60)
    await RS.reserve(db, rx, staff_id=staff.id)

    out = await RS.release_for_transition(db, rx, "FILLING")
    assert out.skipped == "transition holds stock"
    assert await _reserved_on_lots(db, staff, ndc) == Decimal("60.000")


async def test_a_lapsed_hold_is_swept_and_the_stock_comes_back(env):
    """An uncollected will-call would otherwise hold its units out of
    availability forever — the shelf shows stock FEFO refuses to hand out."""
    db, staff, ndc = env
    await _receive(db, staff, ndc, f"L{uuid.uuid4().hex[:6]}", qty=100)
    rx = await make_rx(db, staff.pharmacy_id, ndc, 45)
    await RS.reserve(db, rx, staff_id=staff.id,
                     now=datetime.now(timezone.utc) - timedelta(days=30))
    assert await _reserved_on_lots(db, staff, ndc) == Decimal("45.000")

    out = await RS.sweep_expired(db, staff.pharmacy_id)
    assert out["expired"] >= 1
    assert str(rx.id) in out["prescriptions"]
    assert await _reserved_on_lots(db, staff, ndc) == Decimal("0.000")


async def test_a_live_hold_survives_the_sweep(env):
    db, staff, ndc = env
    await _receive(db, staff, ndc, f"L{uuid.uuid4().hex[:6]}", qty=100)
    rx = await make_rx(db, staff.pharmacy_id, ndc, 45)
    await RS.reserve(db, rx, staff_id=staff.id)

    await RS.sweep_expired(db, staff.pharmacy_id)
    assert await _reserved_on_lots(db, staff, ndc) == Decimal("45.000")


async def test_consuming_a_hold_frees_the_units_for_its_own_dispense(env):
    """The dispense hook calls this before allocating; without it FEFO would
    refuse to give the prescription the stock reserved for it."""
    db, staff, ndc = env
    await _receive(db, staff, ndc, f"L{uuid.uuid4().hex[:6]}", qty=100)
    rx = await make_rx(db, staff.pharmacy_id, ndc, 100)
    await RS.reserve(db, rx, staff_id=staff.id)

    out = await RS.consume(db, rx)
    assert out.released == 1
    assert await _reserved_on_lots(db, staff, ndc) == Decimal("0.000")
    assert (await db.execute(text(
        "SELECT status FROM inventory_reservations WHERE prescription_id=:r"),
        {"r": rx.id})).scalar() == "consumed"


# ── the counter is checkable ──────────────────────────────────────────────
async def test_a_counter_out_of_step_with_its_rows_is_reported(env):
    """Hand-editing the counter is exactly what the drift check must catch."""
    db, staff, ndc = env
    await _receive(db, staff, ndc, f"L{uuid.uuid4().hex[:6]}", qty=100)
    rx = await make_rx(db, staff.pharmacy_id, ndc, 20)
    await RS.reserve(db, rx, staff_id=staff.id)
    assert await RS.counter_drift(db, staff.pharmacy_id) == []

    await db.execute(text(
        "UPDATE inventory_lots SET quantity_reserved = 55 "
        "WHERE pharmacy_id=:p AND ndc11=:n"), {"p": staff.pharmacy_id, "n": ndc})
    drift = await RS.counter_drift(db, staff.pharmacy_id)
    assert any(d["drift"] == 35.0 for d in drift)


async def test_releasing_below_zero_is_refused_rather_than_clamped(env):
    """The old GREATEST(0, ...) turned a corrupted counter into a plausible one."""
    db, staff, ndc = env
    await _receive(db, staff, ndc, f"L{uuid.uuid4().hex[:6]}", qty=100)
    rx = await make_rx(db, staff.pharmacy_id, ndc, 40)
    await RS.reserve(db, rx, staff_id=staff.id)
    await db.execute(text(
        "UPDATE inventory_lots SET quantity_reserved = 5 "
        "WHERE pharmacy_id=:p AND ndc11=:n"), {"p": staff.pharmacy_id, "n": ndc})

    out = await RS.release_for_transition(db, rx, "CANCELLED")
    assert out.ok is False
    assert "below zero" in (out.error or "")


async def test_the_database_refuses_a_nonpositive_reservation(env):
    """The service checks it, and so does the table — the guarantee should not
    depend on every future caller going through the service."""
    db, staff, ndc = env
    await _receive(db, staff, ndc, f"L{uuid.uuid4().hex[:6]}", qty=10)
    lot_id = (await db.execute(text(
        "SELECT id FROM inventory_lots WHERE pharmacy_id=:p AND ndc11=:n"),
        {"p": staff.pharmacy_id, "n": ndc})).scalar()
    rx = await make_rx(db, staff.pharmacy_id, ndc, 1)
    with pytest.raises(Exception) as e:
        await db.execute(text("""
            INSERT INTO inventory_reservations
              (id,pharmacy_id,prescription_id,inventory_lot_id,ndc11,quantity,
               status,expires_at)
            VALUES (:i,:p,:r,:l,:n,0,'active',now())"""),
            {"i": uuid.uuid4(), "p": staff.pharmacy_id, "r": rx.id,
             "l": lot_id, "n": ndc})
    assert "ck_reservation_quantity_positive" in str(e.value)
    await db.rollback()


async def test_the_aggregate_is_derived_from_the_lots_not_incremented(env):
    """An incremental aggregate needed a GREATEST(0, ...) to stay sane — the
    same clamp this module removed from the dispense hook. Summing the lots
    cannot drift, so the lot can raise on an inconsistency without its own
    mirror quietly absorbing one."""
    db, staff, ndc = env
    await _receive(db, staff, ndc, f"L{uuid.uuid4().hex[:6]}", qty=100)
    rx = await make_rx(db, staff.pharmacy_id, ndc, 40)
    await RS.reserve(db, rx, staff_id=staff.id)

    # Corrupt the aggregate behind the service's back.
    await db.execute(text(
        "UPDATE stock_levels SET quantity_reserved = 999 "
        "WHERE pharmacy_id = :p AND ndc11 = :n"),
        {"p": staff.pharmacy_id, "n": ndc})

    # Any further reservation activity re-derives it from the lots.
    await RS.release_for_transition(db, rx, "CANCELLED")
    agg = (await db.execute(text(
        "SELECT quantity_reserved FROM stock_levels WHERE pharmacy_id=:p AND ndc11=:n"),
        {"p": staff.pharmacy_id, "n": ndc})).scalar()
    assert Decimal(str(agg)) == Decimal("0.000")


async def test_damaging_reserved_stock_releases_the_promise_it_broke(env):
    """Found by the simulator: a crushed carton left `reserved` above `on_hand`.

    The carton really is crushed, so the movement is a fact and is recorded.
    What must not survive it is a reservation still claiming units that have
    gone — `available` would be negative and FEFO would refuse to fill the very
    prescription the stock was held for.
    """
    db, staff, ndc = env
    r = await _receive(db, staff, ndc, f"L{uuid.uuid4().hex[:6]}", qty=10)
    rx = await make_rx(db, staff.pharmacy_id, ndc, 10)
    await RS.reserve(db, rx, staff_id=staff.id)
    assert await _reserved_on_lots(db, staff, ndc) == Decimal("10.000")

    await AD.record_damage(AD.RecordDamage(
        lot_id=uuid.UUID(r["lot_id"]), quantity=10,
        reason="carton crushed in transit"), staff=staff, db=db)

    row = (await db.execute(text(
        "SELECT quantity_on_hand, quantity_reserved, quantity_damaged "
        "FROM inventory_lots WHERE id = :i"), {"i": r["lot_id"]})).mappings().first()
    assert Decimal(str(row["quantity_damaged"])) == Decimal("10.000")
    assert Decimal(str(row["quantity_on_hand"])) == Decimal("0.000")
    # The invariant the simulator caught breaking.
    assert Decimal(str(row["quantity_reserved"])) <= Decimal(str(row["quantity_on_hand"]))

    status, reason = (await db.execute(text(
        "SELECT status, reason FROM inventory_reservations WHERE prescription_id = :r"),
        {"r": rx.id})).first()
    assert status == "released"
    assert "no longer available" in reason


async def test_a_partial_damage_keeps_the_promises_the_stock_still_covers(env):
    """Only what cannot be backed is released — the rest of the queue stands."""
    db, staff, ndc = env
    r = await _receive(db, staff, ndc, f"L{uuid.uuid4().hex[:6]}", qty=20)
    first = await make_rx(db, staff.pharmacy_id, ndc, 8)
    await RS.reserve(db, first, staff_id=staff.id)
    second = await make_rx(db, staff.pharmacy_id, ndc, 8)
    await RS.reserve(db, second, staff_id=staff.id)

    # Down to 9 units on hand: the first promise fits, the second does not.
    await AD.record_damage(AD.RecordDamage(
        lot_id=uuid.UUID(r["lot_id"]), quantity=11,
        reason="water damage on the lower shelf"), staff=staff, db=db)

    def status_of(rx_id):
        return db.execute(text(
            "SELECT status FROM inventory_reservations WHERE prescription_id = :r"),
            {"r": rx_id})
    assert (await status_of(first.id)).scalar() == "active"
    assert (await status_of(second.id)).scalar() == "released"
    assert await _reserved_on_lots(db, staff, ndc) == Decimal("8.000")
