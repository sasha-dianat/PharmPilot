"""
Cloud Write Outbox — PART 2 / §1.3 of the Offline-First Doctrine
=================================================================
Read-side intelligence always degrades live to the local brain. But WRITE-side
actions that genuinely need the cloud — submitting a claim, ordering buffer stock
via EDI, verifying a prescriber against an external registry — cannot be faked
offline. Instead of failing, they are PARKED here and reconciled when the network
returns.

Each outbox entry records the intent (action_type + payload). A reconciliation
worker, triggered when connectivity is restored, replays pending entries through
the registered handler for that action_type.

Stored in PostgreSQL (`intelligence_outbox`) so entries survive restarts.

Public surface:
  enqueue(db, action_type, payload, dedup_key=None, pharmacy_id=None) -> str
  register_handler(action_type, async_fn)            # fn(payload) -> bool (success)
  reconcile(db, limit=50)                            -> ReconcileReport
  pending_count(db)                                  -> int
  list_pending(db, limit)                            -> list[dict]
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .tier_resolver import network_up

logger = logging.getLogger(__name__)

# action_type → async handler that performs the real cloud write.
# handler(payload: dict) -> bool   (True = success, entry is marked done)
_HANDLERS: dict[str, Callable[[dict], Awaitable[bool]]] = {}

MAX_ATTEMPTS = 8


def register_handler(action_type: str, fn: Callable[[dict], Awaitable[bool]]) -> None:
    _HANDLERS[action_type] = fn
    logger.info("[outbox] handler registered for '%s'", action_type)


@dataclass
class ReconcileReport:
    attempted: int = 0
    succeeded: int = 0
    failed:    int = 0
    skipped_no_handler: int = 0
    still_offline: bool = False


async def _ensure_table(db: AsyncSession) -> None:
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS intelligence_outbox (
            id           TEXT PRIMARY KEY,
            action_type  TEXT NOT NULL,
            payload      JSONB NOT NULL,
            dedup_key    TEXT,
            pharmacy_id  TEXT,
            status       TEXT NOT NULL DEFAULT 'pending',   -- pending | done | failed
            attempts     INTEGER NOT NULL DEFAULT 0,
            last_error   TEXT,
            created_at   TIMESTAMPTZ DEFAULT now(),
            updated_at   TIMESTAMPTZ DEFAULT now()
        )
    """))
    await db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_outbox_status ON intelligence_outbox(status)
    """))
    await db.commit()


# ─── enqueue ──────────────────────────────────────────────────────────────────

async def enqueue(
    db: AsyncSession,
    action_type: str,
    payload: dict,
    *,
    dedup_key: Optional[str] = None,
    pharmacy_id: Optional[str] = None,
) -> str:
    """
    Park a cloud-write intent. If a pending entry with the same dedup_key already
    exists, returns that existing id instead of creating a duplicate.
    """
    await _ensure_table(db)

    if dedup_key:
        existing = await db.execute(text("""
            SELECT id FROM intelligence_outbox
            WHERE dedup_key = :k AND status = 'pending' LIMIT 1
        """), {"k": dedup_key})
        row = existing.mappings().first()
        if row:
            return row["id"]

    entry_id = str(uuid.uuid4())
    await db.execute(text("""
        INSERT INTO intelligence_outbox
            (id, action_type, payload, dedup_key, pharmacy_id, status, attempts)
        VALUES (:id, :at, CAST(:payload AS JSONB), :dk, :pid, 'pending', 0)
    """), {
        "id": entry_id, "at": action_type, "payload": json.dumps(payload),
        "dk": dedup_key, "pid": pharmacy_id,
    })
    await db.commit()
    logger.info("[outbox] enqueued %s (%s)", action_type, entry_id)
    return entry_id


# ─── reconcile ────────────────────────────────────────────────────────────────

async def reconcile(db: AsyncSession, limit: int = 50) -> ReconcileReport:
    """
    Replay pending entries through their handlers. Call this when connectivity is
    restored (network event, periodic beat). No-op + still_offline when offline.
    """
    report = ReconcileReport()
    await _ensure_table(db)

    if not network_up():
        report.still_offline = True
        return report

    rows = await db.execute(text("""
        SELECT id, action_type, payload, attempts
        FROM   intelligence_outbox
        WHERE  status = 'pending'
        ORDER  BY created_at
        LIMIT  :lim
    """), {"lim": limit})

    for row in rows.mappings().all():
        action_type = row["action_type"]
        handler = _HANDLERS.get(action_type)
        if handler is None:
            report.skipped_no_handler += 1
            continue

        report.attempted += 1
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)

        try:
            ok = await handler(payload)
        except Exception as exc:  # noqa: BLE001
            ok = False
            await db.execute(text("""
                UPDATE intelligence_outbox
                SET attempts = attempts + 1, last_error = :err, updated_at = now()
                WHERE id = :id
            """), {"err": str(exc)[:500], "id": row["id"]})

        if ok:
            report.succeeded += 1
            await db.execute(text("""
                UPDATE intelligence_outbox
                SET status = 'done', updated_at = now()
                WHERE id = :id
            """), {"id": row["id"]})
        else:
            report.failed += 1
            new_attempts = row["attempts"] + 1
            new_status = "failed" if new_attempts >= MAX_ATTEMPTS else "pending"
            await db.execute(text("""
                UPDATE intelligence_outbox
                SET attempts = :a, status = :s, updated_at = now()
                WHERE id = :id
            """), {"a": new_attempts, "s": new_status, "id": row["id"]})

    await db.commit()
    logger.info("[outbox] reconcile: attempted=%d ok=%d failed=%d no_handler=%d",
                report.attempted, report.succeeded, report.failed, report.skipped_no_handler)
    return report


# ─── inspection ───────────────────────────────────────────────────────────────

async def pending_count(db: AsyncSession) -> int:
    await _ensure_table(db)
    r = await db.execute(text(
        "SELECT COUNT(*) FROM intelligence_outbox WHERE status = 'pending'"))
    return int(r.scalar() or 0)


async def list_pending(db: AsyncSession, limit: int = 100) -> list[dict]:
    await _ensure_table(db)
    rows = await db.execute(text("""
        SELECT id, action_type, dedup_key, attempts, last_error, created_at
        FROM   intelligence_outbox
        WHERE  status = 'pending'
        ORDER  BY created_at
        LIMIT  :lim
    """), {"lim": limit})
    return [dict(r) for r in rows.mappings()]
