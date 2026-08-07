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


# ── the dispense hook ─────────────────────────────────────────────────────
async def _make_rx(db, pid, ndc, qty=30, status="will_call"):
    """A prescription ready to dispense, with the patient/prescriber rows it needs."""
    pat = (await db.execute(text("SELECT id FROM patients WHERE pharmacy_id=:p LIMIT 1"),
                            {"p": pid})).scalar()
    if not pat:
        pat = uuid.uuid4()
        await db.execute(text("""
            INSERT INTO patients (id, pharmacy_id, first_name, last_name,
              date_of_birth, gender, created_at, updated_at, is_deleted)
            VALUES (:i,:p,'Test','Patient','1980-01-01','O',now(),now(),false)"""),
            {"i": pat, "p": pid})
    prescriber = (await db.execute(text("SELECT id FROM prescribers LIMIT 1"))).scalar()
    if not prescriber:
        prescriber = uuid.uuid4()
        await db.execute(text("""
            INSERT INTO prescribers (id, npi, first_name, last_name,
              created_at, updated_at, is_deleted)
            VALUES (:i,:n,'Test','Prescriber',now(),now(),false)"""),
            {"i": prescriber, "n": uuid.uuid4().hex[:10]})
    rx_id, rx_no = uuid.uuid4(), f"RX{uuid.uuid4().hex[:10].upper()}"
    await db.execute(text("""
        INSERT INTO prescriptions (id, pharmacy_id, patient_id, prescriber_id,
          rx_number, ndc, drug_name, sig_text, quantity_prescribed, days_supply,
          refills_authorized, refills_remaining, status, source, written_date,
          created_at, updated_at, is_deleted)
        VALUES (:i,:ph,:pa,:pr,:rn,:n,'testolol','1 tab daily',:q,30,1,1,:st,
                'paper',CURRENT_DATE,now(),now(),false)"""),
        {"i": rx_id, "ph": pid, "pa": pat, "pr": prescriber, "rn": rx_no,
         "n": ndc, "q": qty, "st": status})
    await db.commit()
    return rx_id, rx_no


async def test_dispensing_decrements_stock_and_records_the_lot(env):
    """The defect this whole workstream exists to close: 46 fills against 8
    movements, because the transition only ever logged 'trigger inventory
    deduction'."""
    from services.core.pharmacy_workflow.state_machine import RxStateMachine
    from shared.models.prescription import RxStatus

    db, alice, _bob, _carl, pid, ndc = env
    await _receive(db, alice, ndc, qty=100)
    rx_id, rx_no = await _make_rx(db, pid, ndc, qty=30)

    await RxStateMachine(db).transition(
        prescription_id=rx_id, to_status=RxStatus.DISPENSED,
        triggered_by_id=alice.id, triggered_by_type="staff", reason="pickup")
    await db.commit()

    on_hand = (await db.execute(text(
        "SELECT SUM(quantity_on_hand) FROM inventory_lots "
        "WHERE pharmacy_id=:p AND ndc11=:n"), {"p": pid, "n": ndc})).scalar()
    assert float(on_hand) == 70.0            # 100 dispensed 30

    mv = (await db.execute(text(
        "SELECT quantity_delta, prescription_fill_id, event_hash FROM inventory_movements "
        "WHERE ndc11=:n AND movement_type='DISPENSE'"), {"n": ndc})).mappings().all()
    assert len(mv) == 1 and float(mv[0]["quantity_delta"]) == -30.0
    assert mv[0]["prescription_fill_id"] is not None    # traceable to the fill
    assert mv[0]["event_hash"]                          # and chained

    fill = (await db.execute(text(
        "SELECT inventory_lot_id, lot_number FROM prescription_fills WHERE id=:f"),
        {"f": mv[0]["prescription_fill_id"]})).mappings().one()
    assert fill["inventory_lot_id"] is not None and fill["lot_number"] == "L-1"


async def test_the_aggregate_follows_the_lots(env):
    from services.core.pharmacy_workflow.state_machine import RxStateMachine
    from shared.models.prescription import RxStatus

    db, alice, _bob, _carl, pid, ndc = env
    await _receive(db, alice, ndc, qty=50)
    rx_id, _ = await _make_rx(db, pid, ndc, qty=20)
    await RxStateMachine(db).transition(
        prescription_id=rx_id, to_status=RxStatus.DISPENSED,
        triggered_by_id=alice.id, triggered_by_type="staff", reason="pickup")
    await db.commit()
    agg = (await db.execute(text(
        "SELECT quantity_on_hand FROM stock_levels WHERE pharmacy_id=:p AND ndc11=:n"),
        {"p": pid, "n": ndc})).scalar()
    lots = (await db.execute(text(
        "SELECT SUM(quantity_on_hand) FROM inventory_lots WHERE pharmacy_id=:p AND ndc11=:n"),
        {"p": pid, "n": ndc})).scalar()
    assert float(agg) == float(lots) == 30.0


async def test_dispensing_picks_the_earliest_expiry(env):
    from services.core.pharmacy_workflow.state_machine import RxStateMachine
    from shared.models.prescription import RxStatus

    db, alice, _bob, _carl, pid, ndc = env
    await _receive(db, alice, ndc, qty=40, lot="LATE", days=400)
    await _receive(db, alice, ndc, qty=40, lot="SOON", days=40)
    rx_id, _ = await _make_rx(db, pid, ndc, qty=10)
    await RxStateMachine(db).transition(
        prescription_id=rx_id, to_status=RxStatus.DISPENSED,
        triggered_by_id=alice.id, triggered_by_type="staff", reason="pickup")
    await db.commit()
    rows = dict((await db.execute(text(
        "SELECT lot_number, quantity_on_hand FROM inventory_lots "
        "WHERE pharmacy_id=:p AND ndc11=:n"), {"p": pid, "n": ndc})).all())
    assert float(rows["SOON"]) == 30.0 and float(rows["LATE"]) == 40.0


async def test_a_dispense_the_shelf_cannot_cover_still_succeeds(env):
    """A bookkeeping error must never stop a patient receiving their medicine.
    The gap is recorded for reconciliation instead."""
    from services.core.pharmacy_workflow.state_machine import RxStateMachine
    from shared.models.prescription import RxStatus

    db, alice, _bob, _carl, pid, ndc = env
    await _receive(db, alice, ndc, qty=5)
    rx_id, _ = await _make_rx(db, pid, ndc, qty=30)
    rx = await RxStateMachine(db).transition(
        prescription_id=rx_id, to_status=RxStatus.DISPENSED,
        triggered_by_id=alice.id, triggered_by_type="staff", reason="pickup")
    await db.commit()
    assert str(rx.status) in ("RxStatus.DISPENSED", "dispensed")
    on_hand = (await db.execute(text(
        "SELECT SUM(quantity_on_hand) FROM inventory_lots WHERE pharmacy_id=:p AND ndc11=:n"),
        {"p": pid, "n": ndc})).scalar()
    assert float(on_hand) == 0.0          # took everything it could, never negative


async def test_expired_stock_is_not_handed_to_a_patient(env):
    """Coming up short is acceptable. Dispensing expired stock is not."""
    from services.core.inventory import dispense as D

    db, alice, _bob, _carl, pid, ndc = env
    r = await _receive(db, alice, ndc, qty=100)
    await db.execute(text("UPDATE inventory_lots SET expiry_date = CURRENT_DATE - 1 "
                          "WHERE id = :i"), {"i": r["lot_id"]})
    await db.commit()
    lots = await D._lots_for(db, pid, ndc)
    from services.core.inventory import ledger as L
    alloc = L.plan_dispense(lots, 10, reason="rx")
    assert alloc.plans == [] and float(alloc.shortfall) == 10.0


async def test_dispensing_twice_does_not_decrement_twice(env):
    """A retried transition or re-delivered event must not take the stock again."""
    from services.core.inventory import dispense as D

    db, alice, _bob, _carl, pid, ndc = env
    await _receive(db, alice, ndc, qty=100)
    rx_id, _ = await _make_rx(db, pid, ndc, qty=25)
    rx = (await db.execute(text("SELECT * FROM prescriptions WHERE id=:i"),
                           {"i": rx_id})).mappings().one()

    class RxView:
        pass
    v = RxView()
    for k, val in rx.items():
        setattr(v, k, val)

    first = await D.apply_dispense(db, v, staff_id=alice.id)
    await db.commit()
    second = await D.apply_dispense(db, v, staff_id=alice.id)
    await db.commit()

    assert first.skipped is None and float(first.allocated) == 25.0
    assert second.skipped is not None          # recognised as already done
    on_hand = (await db.execute(text(
        "SELECT SUM(quantity_on_hand) FROM inventory_lots WHERE pharmacy_id=:p AND ndc11=:n"),
        {"p": pid, "n": ndc})).scalar()
    assert float(on_hand) == 75.0              # not 50


async def test_returning_to_stock_puts_the_units_back_without_erasing_history(env):
    from services.core.pharmacy_workflow.state_machine import RxStateMachine
    from shared.models.prescription import RxStatus

    db, alice, _bob, _carl, pid, ndc = env
    await _receive(db, alice, ndc, qty=100)
    rx_id, _ = await _make_rx(db, pid, ndc, qty=30)
    await RxStateMachine(db).transition(
        prescription_id=rx_id, to_status=RxStatus.DISPENSED,
        triggered_by_id=alice.id, triggered_by_type="staff", reason="pickup")
    await db.commit()

    from services.core.inventory import dispense as D
    fill_id = (await db.execute(text(
        "SELECT id FROM prescription_fills WHERE prescription_id=:r"),
        {"r": rx_id})).scalar()
    res = await D.reverse_dispense(db, fill_id, staff_id=alice.id)
    await db.commit()

    assert res.ok and float(res.allocated) == 30.0
    on_hand = (await db.execute(text(
        "SELECT SUM(quantity_on_hand) FROM inventory_lots WHERE pharmacy_id=:p AND ndc11=:n"),
        {"p": pid, "n": ndc})).scalar()
    assert float(on_hand) == 100.0
    # the original DISPENSE row survives — history is added to, never rewritten
    kinds = [r[0] for r in (await db.execute(text(
        "SELECT movement_type FROM inventory_movements WHERE ndc11=:n ORDER BY created_at"),
        {"n": ndc})).all()]
    assert kinds == ["RECEIPT", "DISPENSE", "RETURN_FROM_PATIENT"]


# ── damage moves stock into the bucket, end to end ────────────────────────
async def test_damage_relocates_units_rather_than_deleting_them(env):
    db, alice, _bob, _carl, pid, ndc = env
    r = await _receive(db, alice, ndc, qty=100)
    out = await AD.record_damage(AD.RecordDamage(
        lot_id=uuid.UUID(r["lot_id"]), quantity=20,
        reason="carton crushed in transit"), staff=alice, db=db)

    assert out["on_hand"] == 80.0 and out["damaged"] == 20.0
    row = (await db.execute(text(
        "SELECT quantity_on_hand, quantity_damaged FROM inventory_lots WHERE id=:i"),
        {"i": r["lot_id"]})).mappings().one()
    assert float(row["quantity_on_hand"]) == 80.0
    assert float(row["quantity_damaged"]) == 20.0
    # the lot still physically holds 100
    assert float(row["quantity_on_hand"]) + float(row["quantity_damaged"]) == 100.0


async def test_the_sellable_aggregate_falls_when_stock_is_damaged(env):
    db, alice, _bob, _carl, pid, ndc = env
    r = await _receive(db, alice, ndc, qty=50)
    await AD.record_damage(AD.RecordDamage(
        lot_id=uuid.UUID(r["lot_id"]), quantity=15, reason="water damage"),
        staff=alice, db=db)
    agg = (await db.execute(text(
        "SELECT quantity_on_hand FROM stock_levels WHERE pharmacy_id=:p AND ndc11=:n"),
        {"p": pid, "n": ndc})).scalar()
    assert float(agg) == 35.0          # damaged units are not sellable


async def test_damaged_stock_cannot_be_dispensed(env):
    db, alice, _bob, _carl, pid, ndc = env
    r = await _receive(db, alice, ndc, qty=30)
    await AD.record_damage(AD.RecordDamage(
        lot_id=uuid.UUID(r["lot_id"]), quantity=30, reason="all crushed"),
        staff=alice, db=db)
    from services.core.inventory import dispense as D, ledger as L
    lots = await D._lots_for(db, pid, ndc)
    alloc = L.plan_dispense(lots, 5, reason="rx")
    assert alloc.plans == [] and float(alloc.shortfall) == 5.0


async def test_damage_is_immediate_but_writing_it_off_needs_two_people(env):
    db, alice, bob, carl, pid, ndc = env
    r = await _receive(db, alice, ndc, qty=100)
    lot_id = uuid.UUID(r["lot_id"])
    # immediate, no approval
    await AD.record_damage(AD.RecordDamage(
        lot_id=lot_id, quantity=20, reason="crushed"), staff=alice, db=db)

    req = await AD.request_write_off(AD.WriteOffRequest(
        lot_id=lot_id, movement_type="SUPPLIER_CREDIT", quantity=20,
        reason="supplier credit note", from_bucket="damaged"), staff=alice, db=db)
    assert req["stock_changed"] is False

    still = (await db.execute(text(
        "SELECT quantity_damaged FROM inventory_lots WHERE id=:i"),
        {"i": lot_id})).scalar()
    assert float(still) == 20.0        # untouched until approved

    await IG.decide_approval(uuid.UUID(req["approval_id"]),
                             IG.ApprovalDecision(approve=True, witness_id=carl),
                             staff=bob, db=db)
    row = (await db.execute(text(
        "SELECT quantity_on_hand, quantity_damaged FROM inventory_lots WHERE id=:i"),
        {"i": lot_id})).mappings().one()
    assert float(row["quantity_damaged"]) == 0.0
    # sellable stock was never touched by the write-off — it left when damaged
    assert float(row["quantity_on_hand"]) == 80.0


async def test_a_bucket_write_off_does_not_move_the_sellable_aggregate(env):
    """The units stopped being sellable when they were damaged; deducting the
    aggregate again would double-count."""
    db, alice, bob, carl, pid, ndc = env
    r = await _receive(db, alice, ndc, qty=60)
    lot_id = uuid.UUID(r["lot_id"])
    await AD.record_damage(AD.RecordDamage(lot_id=lot_id, quantity=10,
                                           reason="dented"), staff=alice, db=db)
    before = float((await db.execute(text(
        "SELECT quantity_on_hand FROM stock_levels WHERE pharmacy_id=:p AND ndc11=:n"),
        {"p": pid, "n": ndc})).scalar())

    req = await AD.request_write_off(AD.WriteOffRequest(
        lot_id=lot_id, movement_type="WASTE", quantity=10,
        reason="unsalvageable", from_bucket="damaged"), staff=alice, db=db)
    await IG.decide_approval(uuid.UUID(req["approval_id"]),
                             IG.ApprovalDecision(approve=True, witness_id=carl),
                             staff=bob, db=db)
    after = float((await db.execute(text(
        "SELECT quantity_on_hand FROM stock_levels WHERE pharmacy_id=:p AND ndc11=:n"),
        {"p": pid, "n": ndc})).scalar())
    assert after == before == 50.0


async def test_more_cannot_be_written_off_than_the_bucket_holds(env):
    db, alice, _bob, _carl, _pid, ndc = env
    r = await _receive(db, alice, ndc, qty=40)
    lot_id = uuid.UUID(r["lot_id"])
    await AD.record_damage(AD.RecordDamage(lot_id=lot_id, quantity=5,
                                           reason="chipped"), staff=alice, db=db)
    with pytest.raises(Exception) as e:
        await AD.request_write_off(AD.WriteOffRequest(
            lot_id=lot_id, movement_type="WASTE", quantity=25,
            reason="claimed", from_bucket="damaged"), staff=alice, db=db)
    assert getattr(e.value, "status_code", None) == 422


async def test_damage_is_chained_into_the_ledger_like_any_other_movement(env):
    db, alice, _bob, _carl, pid, ndc = env
    r = await _receive(db, alice, ndc, qty=25)
    out = await AD.record_damage(AD.RecordDamage(
        lot_id=uuid.UUID(r["lot_id"]), quantity=5, reason="broken seal"),
        staff=alice, db=db)
    assert out["event_hash"] and len(out["event_hash"]) == 64
    kinds = [x[0] for x in (await db.execute(text(
        "SELECT movement_type FROM inventory_movements WHERE ndc11=:n "
        "ORDER BY created_at"), {"n": ndc})).all()]
    assert kinds == ["RECEIPT", "DAMAGE"]


# ── transfers out and supplier returns, end to end ────────────────────────
async def test_transfer_out_holds_stock_in_transit(env):
    db, alice, _bob, _carl, pid, ndc = env
    r = await _receive(db, alice, ndc, qty=100)
    out = await AD.record_damage(AD.RecordDamage(
        lot_id=uuid.UUID(r["lot_id"]), quantity=30, movement_type="TRANSFER_OUT",
        reason="depot to shelf"), staff=alice, db=db)

    assert out["bucket"] == "in_transit" and out["bucket_quantity"] == 30.0
    row = (await db.execute(text(
        "SELECT quantity_on_hand, quantity_in_transit FROM inventory_lots WHERE id=:i"),
        {"i": r["lot_id"]})).mappings().one()
    assert float(row["quantity_on_hand"]) == 70.0
    assert float(row["quantity_in_transit"]) == 30.0
    assert float(row["quantity_on_hand"]) + float(row["quantity_in_transit"]) == 100.0


async def test_stock_in_transit_is_not_dispensable(env):
    db, alice, _bob, _carl, pid, ndc = env
    r = await _receive(db, alice, ndc, qty=40)
    await AD.record_damage(AD.RecordDamage(
        lot_id=uuid.UUID(r["lot_id"]), quantity=40, movement_type="TRANSFER_OUT",
        reason="all moved"), staff=alice, db=db)
    from services.core.inventory import dispense as D, ledger as L
    alloc = L.plan_dispense(await D._lots_for(db, pid, ndc), 5, reason="rx")
    assert alloc.plans == [] and float(alloc.shortfall) == 5.0


async def test_damaged_stock_can_be_staged_for_return_then_credited(env):
    """The full two-hop lifecycle: on-hand → damaged → returned → off the books,
    with sellable stock touched exactly once."""
    db, alice, bob, carl, pid, ndc = env
    r = await _receive(db, alice, ndc, qty=100)
    lot_id = uuid.UUID(r["lot_id"])

    await AD.record_damage(AD.RecordDamage(
        lot_id=lot_id, quantity=20, reason="crushed"), staff=alice, db=db)
    staged = await AD.record_damage(AD.RecordDamage(
        lot_id=lot_id, quantity=20, movement_type="RETURN_TO_SUPPLIER",
        from_bucket="damaged", reason="claim raised"), staff=alice, db=db)
    assert staged["bucket"] == "returned" and staged["from_bucket"] == "damaged"

    mid = (await db.execute(text(
        "SELECT quantity_on_hand, quantity_damaged, quantity_returned "
        "FROM inventory_lots WHERE id=:i"), {"i": lot_id})).mappings().one()
    assert float(mid["quantity_on_hand"]) == 80.0     # untouched by the second hop
    assert float(mid["quantity_damaged"]) == 0.0
    assert float(mid["quantity_returned"]) == 20.0

    req = await AD.request_write_off(AD.WriteOffRequest(
        lot_id=lot_id, movement_type="SUPPLIER_CREDIT", quantity=20,
        reason="credit note 1182", from_bucket="returned"), staff=alice, db=db)
    await IG.decide_approval(uuid.UUID(req["approval_id"]),
                             IG.ApprovalDecision(approve=True, witness_id=carl),
                             staff=bob, db=db)
    end = (await db.execute(text(
        "SELECT quantity_on_hand, quantity_returned FROM inventory_lots WHERE id=:i"),
        {"i": lot_id})).mappings().one()
    assert float(end["quantity_on_hand"]) == 80.0
    assert float(end["quantity_returned"]) == 0.0


async def test_staging_a_return_does_not_move_the_sellable_aggregate_twice(env):
    db, alice, _bob, _carl, pid, ndc = env
    r = await _receive(db, alice, ndc, qty=60)
    lot_id = uuid.UUID(r["lot_id"])
    await AD.record_damage(AD.RecordDamage(lot_id=lot_id, quantity=10,
                                           reason="dented"), staff=alice, db=db)
    before = float((await db.execute(text(
        "SELECT quantity_on_hand FROM stock_levels WHERE pharmacy_id=:p AND ndc11=:n"),
        {"p": pid, "n": ndc})).scalar())
    await AD.record_damage(AD.RecordDamage(
        lot_id=lot_id, quantity=10, movement_type="RETURN_TO_SUPPLIER",
        from_bucket="damaged", reason="claim"), staff=alice, db=db)
    after = float((await db.execute(text(
        "SELECT quantity_on_hand FROM stock_levels WHERE pharmacy_id=:p AND ndc11=:n"),
        {"p": pid, "n": ndc})).scalar())
    assert after == before == 50.0


async def test_each_hop_is_chained_into_the_ledger(env):
    db, alice, _bob, _carl, pid, ndc = env
    r = await _receive(db, alice, ndc, qty=50)
    lot_id = uuid.UUID(r["lot_id"])
    await AD.record_damage(AD.RecordDamage(lot_id=lot_id, quantity=10,
                                           reason="crushed"), staff=alice, db=db)
    await AD.record_damage(AD.RecordDamage(
        lot_id=lot_id, quantity=5, movement_type="TRANSFER_OUT",
        reason="to shelf"), staff=alice, db=db)
    kinds = [x[0] for x in (await db.execute(text(
        "SELECT movement_type FROM inventory_movements WHERE ndc11=:n "
        "ORDER BY created_at"), {"n": ndc})).all()]
    assert kinds == ["RECEIPT", "DAMAGE", "TRANSFER_OUT"]
