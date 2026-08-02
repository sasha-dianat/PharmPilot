"""End-to-end: receive → correct → approve → write-off → chain, on a real database.

The pure rules are covered by `test_inventory_admin_rules.py`. This proves the
wiring: that the policy actually reaches the database, that an approval really
does gate the change, and that the movement chain stays intact across the whole
sequence.

Skipped when the disposable test database is unreachable, so the unit suite still
runs on a machine with no Postgres.
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.core.inventory.ledger import verify_chain
from services.platform.routers import inventory_admin as AD
from services.platform.routers import inventory_integrity as IG

pytestmark = pytest.mark.asyncio


def _test_db_url() -> str | None:
    env = os.getenv("TEST_DATABASE_URL")
    if env:
        return env
    dotenv = Path(__file__).resolve().parents[2] / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            m = re.match(r"^DATABASE_URL=(.+)$", line.strip())
            if m:
                return re.sub(r"/pharmpilot$", "/pharmpilot_test", m.group(1))
    return None


URL = _test_db_url()
pytestmark = [pytest.mark.asyncio,
              pytest.mark.skipif(not URL, reason="no test database configured")]

# Each test gets its own NDC. The ledger is append-only at the database level —
# the trigger blocked this fixture's first attempt to DELETE movements between
# tests, which is exactly the guarantee under test. So isolation comes from a
# fresh item per test rather than from erasing history.
def _fresh_ndc() -> str:
    return "99" + uuid.uuid4().int.__str__()[:9]


class FakeStaff:
    """Only the two attributes the handlers read."""
    def __init__(self, pharmacy_id, staff_id):
        self.pharmacy_id, self.id = pharmacy_id, staff_id


@pytest.fixture
async def env():
    engine = create_async_engine(URL)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    ndc = _fresh_ndc()
    async with sm() as db:
        pid = (await db.execute(text("SELECT id FROM pharmacies LIMIT 1"))).scalar()
        if not pid:
            pid = uuid.uuid4()
            await db.execute(text(
                "INSERT INTO pharmacies (id,name,npi,address_line1,city,state,"
                "zip_code,phone,created_at,updated_at) VALUES "
                "(:i,'T','1','a','b','c','d','e',now(),now())"), {"i": pid})
        dp = uuid.uuid4()
        await db.execute(text("""
            INSERT INTO drug_products (id,ndc11,generic_name,strength,dosage_form,
              is_controlled,is_active,discontinued,is_generic,is_otc,is_hazardous,
              requires_refrigeration,high_risk_flag,drug_db_metadata,
              created_at,updated_at,is_deleted)
            VALUES (:i,:n,'testolol','10 mg','TAB',true,true,false,true,false,
                    false,false,false,'{}',now(),now(),false)"""), {"i": dp, "n": ndc})
        await db.commit()
        alice, bob, carl = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        yield db, FakeStaff(pid, alice), FakeStaff(pid, bob), carl, pid, ndc
    await engine.dispose()


async def _receive(db, staff, ndc, qty=100, lot="L-1", days=200):
    return await AD.receive_stock(AD.ReceiveLot(
        ndc11=ndc, lot_number=lot, expiry_date=date.today() + timedelta(days=days),
        quantity=qty, unit_cost=50, storage_location="SHELF-A1"), staff=staff, db=db)


async def test_receiving_writes_a_chained_ledger_row(env):
    db, alice, _bob, _carl, _pid, ndc = env
    r = await _receive(db, alice, ndc)
    assert r["lot_on_hand"] == 100.0
    assert r["event_hash"] and len(r["event_hash"]) == 64


async def test_receiving_expired_stock_is_refused(env):
    """Accepting it as sellable is how expired stock ends up dispensed."""
    db, alice, _bob, _carl, _pid, ndc = env
    with pytest.raises(Exception) as e:
        await AD.receive_stock(AD.ReceiveLot(
            ndc11=ndc, lot_number="OLD", expiry_date=date.today() - timedelta(days=1),
            quantity=10), staff=alice, db=db)
    assert getattr(e.value, "status_code", None) == 422


async def test_one_lot_number_cannot_carry_two_expiries(env):
    db, alice, _bob, _carl, _pid, ndc = env
    await _receive(db, alice, ndc, lot="L-9", days=100)
    with pytest.raises(Exception) as e:
        await _receive(db, alice, ndc, lot="L-9", days=300)
    assert getattr(e.value, "status_code", None) == 422


async def test_the_panel_cannot_change_a_quantity(env):
    db, alice, _bob, _carl, _pid, ndc = env
    r = await _receive(db, alice, ndc)
    with pytest.raises(Exception) as e:
        await AD.edit_lot(uuid.UUID(r["lot_id"]),
                          AD.LotEdit(field="quantity_on_hand", value=99999,
                                     reason="attempt"), staff=alice, db=db)
    assert getattr(e.value, "status_code", None) == 422


async def test_a_plain_correction_applies_immediately(env):
    db, alice, _bob, _carl, _pid, ndc = env
    r = await _receive(db, alice, ndc)
    out = await AD.edit_lot(uuid.UUID(r["lot_id"]),
                            AD.LotEdit(field="storage_location", value="SHELF-B9",
                                       reason="shelf reorganised"), staff=alice, db=db)
    assert out["applied"] is True and out["new"] == "SHELF-B9"


async def test_extending_an_expiry_becomes_an_approval_not_an_edit(env):
    db, alice, bob, carl, _pid, ndc = env
    r = await _receive(db, alice, ndc)
    out = await AD.edit_lot(uuid.UUID(r["lot_id"]), AD.LotEdit(
        field="expiry_date", value=str(date.today() + timedelta(days=900)),
        reason="supplier letter"), staff=alice, db=db)
    assert out["applied"] is False and out["approval_id"]

    # the requester cannot sign their own
    with pytest.raises(Exception) as e:
        await IG.decide_approval(uuid.UUID(out["approval_id"]),
                                 IG.ApprovalDecision(approve=True, witness_id=carl),
                                 staff=alice, db=db)
    assert getattr(e.value, "status_code", None) == 403

    # controlled drug ⇒ a witness is required
    with pytest.raises(Exception) as e:
        await IG.decide_approval(uuid.UUID(out["approval_id"]),
                                 IG.ApprovalDecision(approve=True), staff=bob, db=db)
    assert getattr(e.value, "status_code", None) == 422

    done = await IG.decide_approval(uuid.UUID(out["approval_id"]),
                                    IG.ApprovalDecision(approve=True, witness_id=carl),
                                    staff=bob, db=db)
    assert done["status"] == "applied" and done["stock_changed"] is False
    assert done["field_changed"]["field"] == "expiry_date"


async def test_a_write_off_moves_nothing_until_it_is_approved(env):
    db, alice, bob, carl, _pid, ndc = env
    r = await _receive(db, alice, ndc)
    lot_id = r["lot_id"]

    req = await AD.request_write_off(AD.WriteOffRequest(
        lot_id=uuid.UUID(lot_id), movement_type="WASTE", quantity=30,
        reason="water damage"), staff=alice, db=db)
    assert req["stock_changed"] is False and req["requires_witness"] is True

    still = (await db.execute(text(
        "SELECT quantity_on_hand FROM inventory_lots WHERE id = :i"),
        {"i": lot_id})).scalar()
    assert float(still) == 100.0

    applied = await IG.decide_approval(uuid.UUID(req["approval_id"]),
                                       IG.ApprovalDecision(approve=True, witness_id=carl),
                                       staff=bob, db=db)
    assert applied["stock_changed"] is True
    after = (await db.execute(text(
        "SELECT quantity_on_hand FROM inventory_lots WHERE id = :i"),
        {"i": lot_id})).scalar()
    assert float(after) == 70.0


async def test_a_rejected_write_off_leaves_stock_alone(env):
    db, alice, bob, carl, _pid, ndc = env
    r = await _receive(db, alice, ndc)
    req = await AD.request_write_off(AD.WriteOffRequest(
        lot_id=uuid.UUID(r["lot_id"]), movement_type="WASTE", quantity=10,
        reason="maybe damaged"), staff=alice, db=db)
    out = await IG.decide_approval(uuid.UUID(req["approval_id"]),
                                   IG.ApprovalDecision(approve=False, note="looks fine",
                                                       witness_id=carl),
                                   staff=bob, db=db)
    assert out["status"] == "rejected" and out["stock_changed"] is False
    qty = (await db.execute(text(
        "SELECT quantity_on_hand FROM inventory_lots WHERE id = :i"),
        {"i": r["lot_id"]})).scalar()
    assert float(qty) == 100.0


async def test_more_cannot_be_written_off_than_is_on_hand(env):
    db, alice, _bob, _carl, _pid, ndc = env
    r = await _receive(db, alice, ndc, qty=5)
    with pytest.raises(Exception) as e:
        await AD.request_write_off(AD.WriteOffRequest(
            lot_id=uuid.UUID(r["lot_id"]), movement_type="WASTE", quantity=50,
            reason="claimed breakage"), staff=alice, db=db)
    assert getattr(e.value, "status_code", None) == 422


async def test_bulk_relocation_respects_the_tenant_boundary(env):
    db, alice, _bob, _carl, _pid, ndc = env
    r = await _receive(db, alice, ndc)
    foreign = uuid.uuid4()          # a lot id that is not this pharmacy's
    out = await AD.bulk_edit(AD.BulkEdit(
        lot_ids=[uuid.UUID(r["lot_id"]), foreign], field="storage_location",
        value="COLD-1", reason="fridge move"), staff=alice, db=db)
    assert out["changed"] == 1 and out["not_found_or_other_pharmacy"] == 1


async def test_the_chain_stays_intact_across_the_whole_sequence(env):
    db, alice, bob, carl, pid, ndc = env
    r = await _receive(db, alice, ndc)
    req = await AD.request_write_off(AD.WriteOffRequest(
        lot_id=uuid.UUID(r["lot_id"]), movement_type="EXPIRY_REMOVAL", quantity=20,
        reason="expiry sweep"), staff=alice, db=db)
    await IG.decide_approval(uuid.UUID(req["approval_id"]),
                             IG.ApprovalDecision(approve=True, witness_id=carl),
                             staff=bob, db=db)
    rows = (await db.execute(text("""
        SELECT id, pharmacy_id, irc, inventory_lot_id, movement_type,
               quantity_delta, quantity_after, created_by, prev_hash, event_hash,
               created_at
        FROM inventory_movements
        WHERE pharmacy_id = :p AND event_hash IS NOT NULL
        ORDER BY created_at ASC, id ASC"""), {"p": pid})).mappings().all()
    result = verify_chain(IG.chain_rows_for(rows))
    assert result["intact"] is True and result["verified"] >= 2
