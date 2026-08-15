"""Crash recovery — what survives when the process dies mid-write.

The simulator's other phases test what the application does when it is *told*
no. This tests what happens when nobody tells it anything: the backend is
terminated from a second connection while a transaction is open, which is what
a killed pod, an OOM, or a severed network actually looks like.

The claim under test is narrow and important: **a torn write leaves nothing
behind.** Either the whole movement is there — lot balance, ledger row, hash
chain link, aggregate — or none of it is. A half-applied receipt is worse than
a refused one, because the refusal is visible and the half is not.

Postgres gives atomicity per transaction; the question is whether the
application actually puts each logical operation inside one. It is possible to
write perfectly correct SQL and still tear, by committing twice.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


async def backend_pid(db) -> int:
    return (await db.execute(text("SELECT pg_backend_pid()"))).scalar()


async def kill(engine_url: str, pid: int) -> None:
    """Terminate someone else's backend, from a connection of our own."""
    killer = create_async_engine(engine_url)
    try:
        async with killer.connect() as c:
            await c.execute(text("SELECT pg_terminate_backend(:p)"), {"p": pid})
            await c.commit()
    finally:
        await killer.dispose()



async def _abandon(engine, sm, work, url: str):
    """Run `work(db)`, kill its backend, and abandon the connection.

    The dying session cannot be closed politely — asyncpg raises from inside
    its own rollback when the socket has gone. That is exactly what a crash
    looks like from the application's side, so it is swallowed here rather than
    tidied away, and the pool is disposed without trying to return anything.
    """
    db = sm()
    pid = await backend_pid(db)
    try:
        await work(db)
    except BaseException:
        pass
    try:
        await kill(url, pid)
    except BaseException:
        pass
    try:
        await db.close()
    except BaseException:
        pass
    try:
        await engine.dispose(close=False)
    except BaseException:
        pass


class CrashResult:
    def __init__(self, name: str, torn: list[str], detail: str = ""):
        self.name, self.torn, self.detail = name, torn, detail

    @property
    def ok(self) -> bool:
        return not self.torn


async def receive_then_crash(url: str, pid_pharmacy, ndc: str) -> CrashResult:
    """Kill the backend after a receipt is written but before it commits.

    A receipt touches three things: the lot, the aggregate, and the movement
    ledger. If any one of them survives alone, the books gain stock that no
    movement explains, or a movement that no stock backs.
    """
    from services.platform.routers import inventory_admin as AD

    class Staff:
        def __init__(self, p):
            self.pharmacy_id, self.id = p, uuid.uuid4()

    lot_number = f"CRASH-{uuid.uuid4().hex[:6].upper()}"
    engine = create_async_engine(url)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    async def work(db):
        # Everything the endpoint does, up to and including its commit. The
        # kill lands immediately after, so whether the commit had already
        # flushed is exactly the race a real crash runs.
        await AD.receive_stock(AD.ReceiveLot(
            ndc11=ndc, lot_number=lot_number,
            expiry_date=date.today() + timedelta(days=300),
            quantity=500, unit_cost=3, storage_location="SIM"),
            staff=Staff(pid_pharmacy), db=db)

    await _abandon(engine, sm, work, url)

    # Reconnect and ask what survived.
    engine = create_async_engine(url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    torn: list[str] = []
    try:
        async with sm() as db:
            lot = (await db.execute(text(
                "SELECT id, quantity_on_hand FROM inventory_lots "
                "WHERE pharmacy_id = :p AND lot_number = :l"),
                {"p": pid_pharmacy, "l": lot_number})).mappings().first()
            # Joined on the lot id, not on the reason text: the receipt's
            # default reason is "goods receipt" and never contains the lot
            # number, so matching on it reported a tear that had not happened.
            mv = (await db.execute(text(
                "SELECT count(*) FROM inventory_movements m "
                "JOIN inventory_lots il ON il.id = m.inventory_lot_id "
                "WHERE m.pharmacy_id = :p AND il.lot_number = :l"),
                {"p": pid_pharmacy, "l": lot_number})).scalar()
            if lot is not None and not mv:
                torn.append(f"lot {lot_number} exists holding "
                            f"{lot['quantity_on_hand']} units with no movement "
                            f"explaining it")
            if mv and lot is None:
                torn.append(f"{mv} movement(s) reference lot {lot_number}, "
                            f"which does not exist")
    finally:
        await engine.dispose()
    return CrashResult("receive-then-crash", torn)


async def crash_between_reserve_and_commit(url: str, pid_pharmacy, ndc: str,
                                           rx_id) -> CrashResult:
    """Kill the backend after reservations are written but before commit.

    Reservations touch two tables plus a denormalised counter. A tear here would
    leave a hold with no counter, or a counter with no hold — the drift the
    reconciliation check exists to find, arriving without anyone doing anything
    wrong.
    """
    from services.core.inventory import reservation_service as RS

    class Rx:
        def __init__(self, i, p, n, qty):
            self.id, self.pharmacy_id, self.ndc = i, p, n
            self.quantity_dispensed = self.quantity_prescribed = qty
            self.rx_number = "CRASH"

    engine = create_async_engine(url)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    async def work(db):
        # Deliberately no commit: the reservation service leaves that to its
        # caller, so this is the window where a crash could tear the rows from
        # the counter they keep in step.
        await RS.reserve(db, Rx(rx_id, pid_pharmacy, ndc, 5))

    await _abandon(engine, sm, work, url)

    engine = create_async_engine(url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    torn: list[str] = []
    try:
        async with sm() as db:
            rows = (await db.execute(text(
                "SELECT count(*) FROM inventory_reservations "
                "WHERE prescription_id = :r"), {"r": rx_id})).scalar()
            counters = (await db.execute(text(
                "SELECT COALESCE(SUM(quantity_reserved),0) FROM inventory_lots "
                "WHERE pharmacy_id = :p AND ndc11 = :n"),
                {"p": pid_pharmacy, "n": ndc})).scalar()
            if rows and not counters:
                torn.append(f"{rows} reservation row(s) survived with the "
                            f"reserved counter still at zero")
            if counters and not rows:
                torn.append(f"reserved counter holds {counters} with no "
                            f"reservation row claiming it")
    finally:
        await engine.dispose()
    return CrashResult("reserve-then-crash", torn)
