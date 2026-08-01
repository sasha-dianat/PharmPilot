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

# Source-row columns that may carry an insurer's own identifier. generic_code
# is the NORMALIZED role both tamin (drug_code) and salamat (generic_code) map
# into — the shared national code space.
SOURCE_CODE_FIELDS = ("generic_code", "source_code", "drug_code", "insurer_code", "code")


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


async def load_crosswalk(db, insurer: str | None = None,
                         origin: str | None = "owner") -> dict[str, dict]:
    """Confirmed + rejected decisions as a lookup dict for the linker.
    → {"<insurer>|code:<x>": {"irc":…, "status":…}, "<insurer>|name:<k>": {...}}

    Defaults to OWNER rulings only. Auto-recorded beliefs are deliberately not
    consulted here: short-circuiting on them would freeze a 0.76 guess as a 1.0
    certainty and stop every future improvement to the matcher from ever being
    seen. They exist to be compared against — pass origin=None to read them."""
    from sqlalchemy import select
    from shared.models.crosswalk import CrosswalkEntry

    q = select(CrosswalkEntry)
    if insurer:
        q = q.where(CrosswalkEntry.insurer == insurer)
    if origin:
        q = q.where(CrosswalkEntry.origin == origin)
    # Deterministic precedence. The same code can legitimately carry several
    # historical rows (each keyed by a different spelling), and 6 of them held
    # CONTRADICTING owner rulings — which row won the dict was iteration order,
    # i.e. the linker's answer for «SIROLIMUS» depended on Postgres row order.
    # Rows load oldest-first so the LATEST ruling overwrites; on equal
    # timestamps a confirmed ruling outranks a rejected one (the confirmation
    # names a product, the rejection only refuses one pairing).
    q = q.order_by(CrosswalkEntry.decided_at.asc().nulls_first(),
                   (CrosswalkEntry.status == "confirmed").asc())
    out: dict[str, dict] = {}
    for e in (await db.execute(q)).scalars().all():
        payload = {"irc": e.irc, "status": e.status, "reason": e.reason}
        if e.source_code:
            # A CODED decision is about that coded product and nothing else.
            # Publishing a name key for it let one ruling sweep in every other
            # product sharing salamat's truncated name — measured on the
            # 2026-07-29 re-upload: 222 names pulled 613 distinct codes along,
            # 181 of them onto a single IRC («IOHEXOL»: 14 codes → 1 product),
            # all at confidence 1.0 and none of it reaching review. Migration
            # 0027 fixed this on the write side; this is the read side.
            out[f"{e.insurer}|code:{e.source_code}"] = payload
        elif e.raw_key:
            out[f"{e.insurer}|name:{e.raw_key}"] = payload
    return out


async def record_decision(db, *, insurer: str, raw_name: str, irc: str | None,
                          status: str, source_code: str | None = None,
                          reason: str | None = None, staff_id=None,
                          origin: str = "owner", confidence: float | None = None,
                          method: str | None = None) -> str:
    """Upsert one decision. Returns 'created' | 'updated' | 'skipped'. The
    raw_key is the spelling-proof key so re-spellings of the same name resolve
    identically.

    `origin='auto'` records what the engine concluded, so the next import reuses
    THIS answer instead of re-deriving one from a matcher that may have changed
    in between. An auto row never overwrites an owner ruling — that asymmetry is
    the whole point: the machine may propose, only the owner decides."""
    from sqlalchemy import select
    from shared.models.crosswalk import CrosswalkEntry
    from .enrichment import enrich_key

    raw_key = enrich_key(raw_name)
    if not raw_key or status not in ("confirmed", "rejected"):
        return "skipped"
    # Keyed per CODED PRODUCT: salamat prints one truncated name («CICLOSPORIN»)
    # for many distinct products, so a name-only key let each new decision
    # overwrite the previous product's and sent the rest back to review forever.
    q = select(CrosswalkEntry).where(CrosswalkEntry.insurer == insurer,
                                     CrosswalkEntry.raw_key == raw_key)
    q = q.where(CrosswalkEntry.source_code == source_code if source_code
                else CrosswalkEntry.source_code.is_(None))
    row = (await db.execute(q)).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if row is None:
        db.add(CrosswalkEntry(insurer=insurer, source_code=source_code, raw_key=raw_key,
                              raw_name=str(raw_name)[:300], irc=irc, status=status,
                              reason=reason, origin=origin, confidence=confidence,
                              method=method, decided_by=staff_id, decided_at=now))
        return "created"
    if origin == "auto" and (row.origin or "owner") == "owner":
        return "skipped"                  # the owner has spoken; do not touch it
    row.irc, row.status, row.reason = irc, status, reason
    row.origin, row.confidence, row.method = origin, confidence, method
    row.decided_by, row.decided_at = staff_id, now
    if source_code:
        row.source_code = source_code
    return "updated"


async def record_auto_decisions(db, *, insurer: str, verdicts: list[dict],
                                min_confidence: float = 0.75) -> dict:
    """Persist the engine's own accepted links as origin='auto' decisions.

    Gap this closes: only rows that reached the review queue ever became
    decisions. Everything auto-applied above the threshold was re-derived from
    scratch on every import, so a change to the matcher could silently move a
    link with nothing to compare against. Recording the belief makes the next
    import reuse it — and makes any disagreement a visible diff instead.

    verdicts: [{code, name, irc, confidence, method}] — one per linked row.
    Returns counts plus `moved`: rows the engine now sends somewhere else than
    it did last time. That list is the drift report — the thing that used to be
    invisible.
    """
    from .enrichment import enrich_key

    prior = await load_crosswalk(db, insurer, origin="auto")
    created = updated = skipped = 0
    moved: list[dict] = []
    for v in verdicts or []:
        name, irc = v.get("name"), v.get("irc")
        conf = float(v.get("confidence") or 0)
        if not name or not irc or conf < min_confidence:
            skipped += 1
            continue
        code = v.get("code") or None
        before = None
        for k in lookup_key(insurer, code, enrich_key(name)):
            if k in prior:
                before = prior[k]
                break
        if before and before.get("irc") and before["irc"] != irc:
            moved.append({"name": name, "code": code,
                          "from_irc": before["irc"], "to_irc": irc,
                          "confidence": round(conf, 3)})
        res = await record_decision(
            db, insurer=insurer, raw_name=name, irc=irc, status="confirmed",
            source_code=code, origin="auto",
            confidence=round(conf, 3), method=str(v.get("method") or "")[:32] or None)
        if res == "created":
            created += 1
        elif res == "updated":
            updated += 1
        else:
            skipped += 1
    return {"auto_created": created, "auto_updated": updated,
            "auto_skipped": skipped, "auto_moved": moved[:200],
            "auto_moved_total": len(moved)}


async def record_run_decisions(db, run, accepted: set, *, staff_id=None) -> dict:
    """Turn one approved coverage run into durable crosswalk decisions:
    accepted review pairs → confirmed; refused pairs → rejected (with the
    owner's reason code). Called from apply_run, inside its transaction."""
    created = updated = skipped = 0
    for item in (run.review or []):
        if not isinstance(item, dict):
            continue
        row = item.get("row") if isinstance(item.get("row"), dict) else {}
        raw_name = row.get("drug_name") or item.get("name")
        if not raw_name:
            continue
        is_accepted = item.get("id") in accepted
        # A REJECTION MUST BE AN ACT, NOT A DEFAULT. Every non-accepted item used
        # to become a durable owner rejection — including the ones nobody had
        # looked at. Applying the 2026-07-29 runs would have written ~437 of
        # them: each one permanently blocks that pairing (rejected is a hard 0.0
        # in the linker) and teaches the FS model a negative it never earned.
        # An untouched review row stays undecided and waits in the queue.
        if not is_accepted and not (item.get("reject_reason") or item.get("reject_note")):
            skipped += 1
            continue
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
    return {"crosswalk_created": created, "crosswalk_updated": updated,
            "review_left_undecided": skipped}


# ── national-code registry: the cross-insurer Rosetta stone ─────────────────
# Discovery (2026-07-22): salamat's generic_code and tamin's drug_code are the
# SAME national code space. salamat truncates names to the bare generic
# («CICLOSPORIN» ×16 rows) while tamin publishes the full identity
# («CICLOSPORIN 100 mg CAPSULE…») — so the richest observed name per code lets
# a truncated row match with full form/strength signals, and one owner
# confirmation on a code resolves it for EVERY insurer that uses that code.

async def build_code_registry(db) -> dict[str, dict]:
    """→ {code: {"name": richest observed name, "irc": owner-confirmed irc?}}.
    Names come from formulary snapshots across ALL insurers (longest = most
    specified); IRCs only from confirmed crosswalk entries — never guessed."""
    from sqlalchemy import select
    from shared.models.crosswalk import CrosswalkEntry
    from shared.models.formulary_snapshot import FormularySnapshot

    reg: dict[str, dict] = {}
    snaps = (await db.execute(
        select(FormularySnapshot.source_code, FormularySnapshot.raw_name)
        .where(FormularySnapshot.source_code.isnot(None)))).all()
    for code, name in snaps:
        if not code or not name:
            continue
        cur = reg.setdefault(str(code), {})
        if len(str(name)) > len(cur.get("name") or ""):
            cur["name"] = str(name)
    confirmed = (await db.execute(
        select(CrosswalkEntry.source_code, CrosswalkEntry.irc)
        .where(CrosswalkEntry.status == "confirmed",
               CrosswalkEntry.source_code.isnot(None),
               CrosswalkEntry.irc.isnot(None)))).all()
    for code, irc in confirmed:
        reg.setdefault(str(code), {})["irc"] = irc
    return reg


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
