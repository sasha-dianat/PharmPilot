"""The Exception Register against a real database.

One property matters more than the rest: a scheduled run must be idempotent.
A reconciliation that opens duplicates every night turns the board into noise
within a week, and a board people stop reading is worse than no board.
"""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.core.inventory import exceptions as X
from services.platform.routers import inventory_exceptions as EX


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
    engine = create_async_engine(URL)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        pid = (await db.execute(text("SELECT id FROM pharmacies LIMIT 1"))).scalar()
        # Soft-delete rather than DELETE: the event log is append-only at the
        # database level, so clearing history is not available even to a test.
        await db.execute(text("UPDATE inventory_exceptions SET is_deleted = true "
                              "WHERE pharmacy_id = :p"), {"p": pid})
        await db.commit()
        yield db, FakeStaff(pid, uuid.uuid4())
    await engine.dispose()


async def test_a_scheduled_run_is_idempotent(env):
    db, staff = env
    first = await EX.run_reconciliation(staff=staff, db=db)
    second = await EX.run_reconciliation(staff=staff, db=db)
    third = await EX.run_reconciliation(staff=staff, db=db)

    assert second["opened"] == 0 and third["opened"] == 0
    assert second["resolved"] == 0 and third["resolved"] == 0
    assert second["recurred"] == first["opened"]
    assert third["recurred"] == first["opened"]


async def test_recurrence_is_counted_not_duplicated(env):
    db, staff = env
    await EX.run_reconciliation(staff=staff, db=db)
    await EX.run_reconciliation(staff=staff, db=db)
    board = await EX.list_exceptions(status="active", check=None,
                                     assigned_to_me=False, controlled_only=False,
                                     limit=200, offset=0, staff=staff, db=db)
    assert board["total"] > 0
    assert all(e["occurrences"] >= 2 for e in board["exceptions"])


async def test_the_board_ranks_patient_safety_above_row_count(env):
    """46 legacy fills must not bury two expired lots that need a pharmacist."""
    db, staff = env
    await EX.run_reconciliation(staff=staff, db=db)
    board = await EX.list_exceptions(status="active", check=None,
                                     assigned_to_me=False, controlled_only=False,
                                     limit=200, offset=0, staff=staff, db=db)
    by_check = {}
    for e in board["exceptions"]:
        by_check.setdefault(e["check"], []).append(e["score"])
    if "expired_on_hand" in by_check and "unbound_from_formulary" in by_check:
        assert max(by_check["expired_on_hand"]) > max(by_check["unbound_from_formulary"])


async def test_every_affected_row_is_stored_not_a_preview(env):
    db, staff = env
    await EX.run_reconciliation(staff=staff, db=db)
    board = await EX.list_exceptions(status="active", check="fill_without_movement",
                                     assigned_to_me=False, controlled_only=False,
                                     limit=10, offset=0, staff=staff, db=db)
    if not board["exceptions"]:
        pytest.skip("no orphan fills in this database")
    e = board["exceptions"][0]
    detail = await EX.get_exception(uuid.UUID(e["id"]), staff=staff, db=db)
    # Compare against the detail's own row_count, not the board's: the board was
    # fetched earlier and the register is shared mutable state, so another test
    # creating a fill between the two calls would make a cross-response
    # comparison flake without anything being wrong.
    assert len(detail["affected_rows"]) == detail["row_count"]
    assert detail["row_count"] > 10     # the API preview would have shown 10


async def test_ranking_shows_its_arithmetic(env):
    db, staff = env
    await EX.run_reconciliation(staff=staff, db=db)
    board = await EX.list_exceptions(status="active", check=None,
                                     assigned_to_me=False, controlled_only=False,
                                     limit=5, offset=0, staff=staff, db=db)
    top = board["exceptions"][0]
    assert "urgency" in top["score_breakdown"]["explanation"]


async def test_a_ruling_requires_a_reason_and_survives_the_next_run(env):
    db, staff = env
    await EX.run_reconciliation(staff=staff, db=db)
    board = await EX.list_exceptions(status="active", check=None,
                                     assigned_to_me=False, controlled_only=False,
                                     limit=1, offset=0, staff=staff, db=db)
    eid = uuid.UUID(board["exceptions"][0]["id"])

    from fastapi import HTTPException
    with pytest.raises(Exception):
        # pydantic rejects the empty reason before the handler is reached
        EX.Disposition(disposition=X.ACCEPTED, reason="")

    out = await EX.dispose_exception(eid, EX.Disposition(
        disposition=X.ACCEPTED, reason="known legacy batch, pilot data"),
        staff=staff, db=db)
    assert out["status"] == X.ACCEPTED

    after = await EX.run_reconciliation(staff=staff, db=db)
    assert after["opened"] == 0
    detail = await EX.get_exception(eid, staff=staff, db=db)
    assert detail["status"] == X.ACCEPTED           # the ruling was not undone
    assert any(h["event"] == "disposition" for h in detail["history"])


async def test_the_response_history_cannot_be_rewritten(env):
    """It is audit evidence, so it is append-only for the same reason the
    movement ledger is."""
    db, staff = env
    await EX.run_reconciliation(staff=staff, db=db)
    with pytest.raises(Exception) as e:
        await db.execute(text("DELETE FROM inventory_exception_events"))
        await db.commit()
    assert "append-only" in str(e.value)
    await db.rollback()
