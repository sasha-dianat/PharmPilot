"""Recall workflow against a real database.

The scenario worth proving end to end is the ugly one: a lot is recalled, some
of it is still on the shelf, some went to a patient we can trace, and some went
out before the lot link existed. A recall that reports only what it can see
would report the middle case and stay silent about the third.
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.core.inventory import recall as RC
from services.platform.routers import inventory_admin as AD
from services.platform.routers import inventory_recall as RR


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


@pytest.fixture
async def env():
    """A fresh product with two lots, one of which has been dispensed."""
    engine = create_async_engine(URL)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    ndc = "97" + uuid.uuid4().int.__str__()[:9]
    async with sm() as db:
        pid = (await db.execute(text("SELECT id FROM pharmacies LIMIT 1"))).scalar()
        dp = uuid.uuid4()
        await db.execute(text("""
            INSERT INTO drug_products (id,ndc11,generic_name,strength,dosage_form,
              is_controlled,is_active,discontinued,is_generic,is_otc,is_hazardous,
              requires_refrigeration,high_risk_flag,drug_db_metadata,
              created_at,updated_at,is_deleted)
            VALUES (:i,:n,'recallium','5 mg','TAB',false,true,false,true,false,
                    false,false,false,'{}',now(),now(),false)"""), {"i": dp, "n": ndc})
        await db.commit()
        staff = FakeStaff(pid, uuid.uuid4())
        yield db, staff, ndc
    await engine.dispose()


async def _receive(db, staff, ndc, lot, qty=100, days=300):
    return await AD.receive_stock(AD.ReceiveLot(
        ndc11=ndc, lot_number=lot, expiry_date=date.today() + timedelta(days=days),
        quantity=qty, unit_cost=10, storage_location="SHELF-R1"), staff=staff, db=db)


async def _open(db, staff, lot, severity="II", ref=None):
    return await RR.open_recall(RR.OpenRecall(
        reference=ref or f"REC-{uuid.uuid4().hex[:8]}", scope_type=RC.BY_LOT,
        scope_value=lot, severity=severity,
        reason="manufacturer notice: particulate contamination",
        source="manufacturer"), staff=staff, db=db)


# ── resolution ────────────────────────────────────────────────────────────
async def test_opening_a_recall_immediately_reports_what_is_still_sellable(env):
    """The first thing anyone needs is the number of units still on the shelf."""
    db, staff, ndc = env
    lot = f"L-{uuid.uuid4().hex[:6].upper()}"
    await _receive(db, staff, ndc, lot, qty=100)
    out = await _open(db, staff, lot)
    assert out["on_shelf"] == 100.0
    assert "quarantine before anything else" in out["urgent_action"]
    assert out["lots_affected"] == 1


async def test_a_recall_scoped_to_stock_we_do_not_hold_finds_nothing(env):
    db, staff, _ = env
    out = await _open(db, staff, "LOT-WE-NEVER-HAD")
    assert out["on_shelf"] == 0.0 and out["lots_affected"] == 0
    assert out["urgent_action"] is None


async def test_a_duplicate_reference_is_refused_not_silently_merged(env):
    db, staff, ndc = env
    lot = f"L-{uuid.uuid4().hex[:6].upper()}"
    await _receive(db, staff, ndc, lot)
    ref = f"REC-{uuid.uuid4().hex[:8]}"
    await _open(db, staff, lot, ref=ref)
    with pytest.raises(HTTPException) as e:
        await _open(db, staff, lot, ref=ref)
    assert e.value.status_code == 409


async def test_an_unknown_scope_or_class_is_rejected(env):
    db, staff, _ = env
    with pytest.raises(HTTPException):
        await RR.open_recall(RR.OpenRecall(
            reference="X1", scope_type="vibes", scope_value="v",
            reason="because"), staff=staff, db=db)
    with pytest.raises(HTTPException):
        await RR.open_recall(RR.OpenRecall(
            reference="X2", scope_type=RC.BY_LOT, scope_value="v",
            severity="IV", reason="because"), staff=staff, db=db)


# ── containment ───────────────────────────────────────────────────────────
async def test_quarantine_takes_every_affected_lot_off_the_shelf_at_once(env):
    """Blocking stock never waits for a signature; destroying it does."""
    db, staff, ndc = env
    lot = f"L-{uuid.uuid4().hex[:6].upper()}"
    r = await _receive(db, staff, ndc, lot, qty=80)
    opened = await _open(db, staff, lot)

    out = await RR.quarantine_affected(uuid.UUID(opened["recall_id"]),
                                       staff=staff, db=db)
    assert out["lots_quarantined"] == 1
    row = (await db.execute(text(
        "SELECT is_quarantined, is_recalled, recall_reference FROM inventory_lots "
        "WHERE id = :i"), {"i": r["lot_id"]})).mappings().one()
    assert row["is_quarantined"] and row["is_recalled"]
    assert row["recall_reference"] == opened["reference"]

    case = await RR.get_recall(uuid.UUID(opened["recall_id"]), staff=staff, db=db)
    assert case["status"] == RC.CONTAINED
    assert case["outstanding"]["lots_to_quarantine"] == 0


async def test_quarantined_stock_can_no_longer_be_dispensed(env):
    """The point of the whole exercise."""
    from services.core.inventory import dispense as D
    db, staff, ndc = env
    lot = f"L-{uuid.uuid4().hex[:6].upper()}"
    await _receive(db, staff, ndc, lot, qty=50)
    opened = await _open(db, staff, lot)
    await RR.quarantine_affected(uuid.UUID(opened["recall_id"]), staff=staff, db=db)
    await db.commit()

    lots = await D._lots_for(db, staff.pharmacy_id, ndc)
    from services.core.inventory import ledger as L
    alloc = L.plan_dispense(lots, 10, reason="post-recall attempt")
    assert alloc.plans == [] and float(alloc.shortfall) == 10.0


async def test_a_closed_recall_refuses_further_containment(env):
    db, staff, ndc = env
    lot = f"L-{uuid.uuid4().hex[:6].upper()}"
    await _receive(db, staff, ndc, lot, qty=10)
    opened = await _open(db, staff, lot)
    rid = uuid.UUID(opened["recall_id"])
    await RR.quarantine_affected(rid, staff=staff, db=db)
    await RR.close_recall(rid, RR.CloseRecall(force_reason="destroyed on site"),
                          staff=staff, db=db)
    with pytest.raises(HTTPException) as e:
        await RR.quarantine_affected(rid, staff=staff, db=db)
    assert e.value.status_code == 409


# ── the blind spot, end to end ────────────────────────────────────────────
async def test_a_recall_reports_dispenses_it_cannot_tie_to_a_lot(env):
    """The failure this guards: 46 legacy fills carry no lot, so a naive recall
    resolves zero patients and reports a clean sheet."""
    db, staff, ndc = env
    lot = f"L-{uuid.uuid4().hex[:6].upper()}"
    await _receive(db, staff, ndc, lot, qty=100)

    # three dispenses of the product recorded the old way: no lot link at all
    pat = (await db.execute(text("SELECT id FROM patients LIMIT 1"))).scalar()
    for i in range(3):
        await db.execute(text("""
            INSERT INTO prescription_fills
                (id, prescription_id, fill_number, ndc_dispensed, quantity_dispensed,
                 days_supply, fill_date, dispensing_pharmacist_id,
                 verifying_pharmacist_id, created_at, updated_at, is_deleted)
            SELECT gen_random_uuid(), p.id, :n, :ndc, 30, 30, CURRENT_DATE,
                   :s, :s, now(), now(), false
            FROM prescriptions p LIMIT 1"""),
            {"n": 90 + i, "ndc": ndc, "s": staff.id})
    await db.commit()

    out = await _open(db, staff, lot)
    comp = out["completeness"]
    assert comp["dispensed_fills_total"] == 3
    assert comp["fills_untraceable"] == 3
    assert comp["complete"] is False
    assert out["patients_identified"] == 0
    # ...and it says so, rather than implying nobody was affected
    assert out["warning"] and "incomplete" in out["warning"]


async def test_a_class_one_recall_tells_you_to_widen_the_notification(env):
    db, staff, ndc = env
    lot = f"L-{uuid.uuid4().hex[:6].upper()}"
    await _receive(db, staff, ndc, lot, qty=10)
    await db.execute(text("""
        INSERT INTO prescription_fills
            (id, prescription_id, fill_number, ndc_dispensed, quantity_dispensed,
             days_supply, fill_date, dispensing_pharmacist_id,
             verifying_pharmacist_id, created_at, updated_at, is_deleted)
        SELECT gen_random_uuid(), p.id, 77, :ndc, 30, 30, CURRENT_DATE, :s, :s,
               now(), now(), false FROM prescriptions p LIMIT 1"""),
        {"ndc": ndc, "s": staff.id})
    await db.commit()
    out = await _open(db, staff, lot, severity=RC.CLASS_I)
    assert "every patient who received this product" in out["warning"]


async def test_the_patient_list_never_travels_without_its_warning(env):
    db, staff, ndc = env
    lot = f"L-{uuid.uuid4().hex[:6].upper()}"
    await _receive(db, staff, ndc, lot, qty=10)
    await db.execute(text("""
        INSERT INTO prescription_fills
            (id, prescription_id, fill_number, ndc_dispensed, quantity_dispensed,
             days_supply, fill_date, dispensing_pharmacist_id,
             verifying_pharmacist_id, created_at, updated_at, is_deleted)
        SELECT gen_random_uuid(), p.id, 78, :ndc, 30, 30, CURRENT_DATE, :s, :s,
               now(), now(), false FROM prescriptions p LIMIT 1"""),
        {"ndc": ndc, "s": staff.id})
    await db.commit()
    opened = await _open(db, staff, lot)
    out = await RR.affected_patients(uuid.UUID(opened["recall_id"]),
                                     staff=staff, db=db)
    assert out["trace_complete"] is False
    assert out["warning"] and "incomplete" in out["warning"]
    assert out["traceable_pct"] == 0.0


# ── closure ───────────────────────────────────────────────────────────────
async def test_a_recall_will_not_close_while_stock_is_sellable(env):
    db, staff, ndc = env
    lot = f"L-{uuid.uuid4().hex[:6].upper()}"
    await _receive(db, staff, ndc, lot, qty=25)
    opened = await _open(db, staff, lot)
    out = await RR.close_recall(uuid.UUID(opened["recall_id"]),
                                RR.CloseRecall(), staff=staff, db=db)
    assert out["closed"] is False
    assert any("sellable" in b for b in out["blockers"])


async def test_a_contained_recall_with_nothing_dispensed_closes_cleanly(env):
    db, staff, ndc = env
    lot = f"L-{uuid.uuid4().hex[:6].upper()}"
    await _receive(db, staff, ndc, lot, qty=25)
    opened = await _open(db, staff, lot)
    rid = uuid.UUID(opened["recall_id"])
    await RR.quarantine_affected(rid, staff=staff, db=db)
    out = await RR.close_recall(rid, RR.CloseRecall(), staff=staff, db=db)
    assert out["closed"] is True and out["closed_over_blockers"] is False


async def test_closing_over_blockers_keeps_them_on_the_record(env):
    """Sometimes the stock genuinely was destroyed on site. It is never silent."""
    db, staff, ndc = env
    lot = f"L-{uuid.uuid4().hex[:6].upper()}"
    await _receive(db, staff, ndc, lot, qty=25)
    opened = await _open(db, staff, lot)
    out = await RR.close_recall(uuid.UUID(opened["recall_id"]),
                                RR.CloseRecall(force_reason="destroyed on site, witnessed"),
                                staff=staff, db=db)
    assert out["closed"] is True and out["closed_over_blockers"] is True
    assert out["blockers_at_closure"]

    case = await RR.get_recall(uuid.UUID(opened["recall_id"]), staff=staff, db=db)
    assert case["closed_over_blockers"] is True
    assert case["closure_reason"] == "destroyed on site, witnessed"


async def test_closure_re_resolves_rather_than_trusting_the_opening_snapshot(env):
    """Stock moves while a recall is worked; closing on a stale picture is the
    failure this guards.

    The scenario is a lot released back to the shelf after containment — which
    is the only way affected stock becomes sellable again, since the ledger
    refuses to receive into a recalled lot at all.
    """
    db, staff, ndc = env
    lot = f"L-{uuid.uuid4().hex[:6].upper()}"
    r = await _receive(db, staff, ndc, lot, qty=10)
    opened = await _open(db, staff, lot)
    rid = uuid.UUID(opened["recall_id"])
    await RR.quarantine_affected(rid, staff=staff, db=db)

    # someone releases it again — the snapshot still says "contained"
    await db.execute(text(
        "UPDATE inventory_lots SET is_quarantined = false, is_recalled = false "
        "WHERE id = :i"), {"i": r["lot_id"]})
    await db.commit()

    out = await RR.close_recall(rid, RR.CloseRecall(), staff=staff, db=db)
    assert out["closed"] is False
    assert any("sellable" in b for b in out["blockers"])


async def test_a_recall_from_another_pharmacy_is_invisible(env):
    db, staff, _ = env
    with pytest.raises(HTTPException) as e:
        await RR.get_recall(uuid.uuid4(), staff=staff, db=db)
    assert e.value.status_code == 404
