"""The decision crosswalk — owner decisions that survive re-import.

Why this exists: every previous fix landed somewhere the next import erased.
Coverage approvals were re-derived from scratch each harvest (so the matcher
re-failed on the same ambiguous names), and manual NFI edits were overwritten
by the next crawl. Splitting *observed* (re-importable) from *decided*
(owner-owned) is what stops the recurrence.

Lookup priority is deliberate:
  1. the insurer's OWN code (tamin's drug_code) — stable across publications,
     so one confirmation makes every future import of that row exact;
  2. the spelling-proof name key — for insurers that publish no code.

Deterministic and offline-testable; no LLM anywhere in this path.
"""
from __future__ import annotations

from datetime import datetime, timezone

# Source-row columns that may carry an insurer's own identifier.
SOURCE_CODE_FIELDS = ("source_code", "drug_code", "insurer_code", "code")


def row_source_code(row: dict | None) -> str | None:
    """The insurer's own code for this row, if the source publishes one."""
    for f in SOURCE_CODE_FIELDS:
        v = (row or {}).get(f)
        if v not in (None, ""):
            return str(v).strip()
    return None


def lookup_key(insurer: str, source_code: str | None, raw_key: str | None) -> list[str]:
    """Candidate crosswalk keys for a row, most-specific first."""
    keys = []
    if source_code:
        keys.append(f"{insurer}|code:{source_code}")
    if raw_key:
        keys.append(f"{insurer}|name:{raw_key}")
    return keys


async def load_crosswalk(db, insurer: str | None = None) -> dict[str, dict]:
    """Confirmed + rejected decisions as a lookup dict for the linker.
    → {"<insurer>|code:<x>": {"irc":…, "status":…}, "<insurer>|name:<k>": {...}}"""
    from sqlalchemy import select
    from shared.models.crosswalk import CrosswalkEntry

    q = select(CrosswalkEntry)
    if insurer:
        q = q.where(CrosswalkEntry.insurer == insurer)
    out: dict[str, dict] = {}
    for e in (await db.execute(q)).scalars().all():
        payload = {"irc": e.irc, "status": e.status, "reason": e.reason}
        if e.source_code:
            out[f"{e.insurer}|code:{e.source_code}"] = payload
        if e.raw_key:
            out[f"{e.insurer}|name:{e.raw_key}"] = payload
    return out


async def record_decision(db, *, insurer: str, raw_name: str, irc: str | None,
                          status: str, source_code: str | None = None,
                          reason: str | None = None, staff_id=None) -> str:
    """Upsert one decision. Returns 'created' | 'updated'. The raw_key is the
    spelling-proof key so re-spellings of the same name resolve identically."""
    from sqlalchemy import select
    from shared.models.crosswalk import CrosswalkEntry
    from .enrichment import enrich_key

    raw_key = enrich_key(raw_name)
    if not raw_key or status not in ("confirmed", "rejected"):
        return "skipped"
    row = (await db.execute(select(CrosswalkEntry).where(
        CrosswalkEntry.insurer == insurer,
        CrosswalkEntry.raw_key == raw_key))).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if row is None:
        db.add(CrosswalkEntry(insurer=insurer, source_code=source_code, raw_key=raw_key,
                              raw_name=str(raw_name)[:300], irc=irc, status=status,
                              reason=reason, decided_by=staff_id, decided_at=now))
        return "created"
    row.irc, row.status, row.reason = irc, status, reason
    row.decided_by, row.decided_at = staff_id, now
    if source_code:
        row.source_code = source_code
    return "updated"


async def record_run_decisions(db, run, accepted: set, *, staff_id=None) -> dict:
    """Turn one approved coverage run into durable crosswalk decisions:
    accepted review pairs → confirmed; refused pairs → rejected (with the
    owner's reason code). Called from apply_run, inside its transaction."""
    created = updated = 0
    for item in (run.review or []):
        if not isinstance(item, dict):
            continue
        row = item.get("row") if isinstance(item.get("row"), dict) else {}
        raw_name = row.get("drug_name") or item.get("name")
        if not raw_name:
            continue
        is_accepted = item.get("id") in accepted
        res = await record_decision(
            db, insurer=run.insurer, raw_name=raw_name,
            irc=item.get("irc") if is_accepted else None,
            status="confirmed" if is_accepted else "rejected",
            source_code=row_source_code(row),
            reason=None if is_accepted else item.get("reject_reason"),
            staff_id=staff_id)
        if res == "created":
            created += 1
        elif res == "updated":
            updated += 1
    return {"crosswalk_created": created, "crosswalk_updated": updated}


# ── field overrides: corrections that re-apply after every crawl ─────────────

async def load_overrides(db) -> dict[str, dict]:
    """→ {irc: {field: value}} — applied on every catalog upsert."""
    from sqlalchemy import select
    from shared.models.crosswalk import FieldOverride
    out: dict[str, dict] = {}
    for o in (await db.execute(select(FieldOverride))).scalars().all():
        out.setdefault(o.irc, {})[o.field] = o.value
    return out


async def set_override(db, irc: str, field: str, value, *, reason: str | None = None,
                       staff_id=None) -> str:
    from sqlalchemy import select
    from shared.models.crosswalk import FieldOverride
    row = (await db.execute(select(FieldOverride).where(
        FieldOverride.irc == irc, FieldOverride.field == field))).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    val = None if value in (None, "") else str(value)
    if row is None:
        db.add(FieldOverride(irc=irc, field=field, value=val, reason=reason,
                             decided_by=staff_id, decided_at=now))
        return "created"
    row.value, row.reason, row.decided_by, row.decided_at = val, reason, staff_id, now
    return "updated"


def apply_overrides(rec, overrides: dict[str, dict]):
    """Re-assert owner corrections over freshly imported source values. This is
    what makes a manual NFI fix permanent policy instead of a value the next
    crawl erases. Returns a (possibly new) record."""
    import dataclasses
    ov = (overrides or {}).get(getattr(rec, "irc", None))
    if not ov:
        return rec
    patch: dict = {}
    for field, value in ov.items():
        if not hasattr(rec, field):
            continue
        if field in ("announced_price", "last_invoice_price"):
            from decimal import Decimal, InvalidOperation
            try:
                patch[field] = None if value is None else Decimal(str(value))
            except (InvalidOperation, ValueError):
                continue
        elif field == "package_count":
            try:
                patch[field] = None if value is None else int(value)
            except (TypeError, ValueError):
                continue
        else:
            patch[field] = value
    return dataclasses.replace(rec, **patch) if patch else rec
