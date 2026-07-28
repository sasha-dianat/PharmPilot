"""بازبینی تصمیم‌ها — re-run today's engine over every past decision.

A decision made months ago was made by a matcher that no longer exists: since
then the ingredient floor moved, structural matching arrived, price began
picking the dosage form, and the FS model was refitted. Nothing re-examined the
decisions those changes should have altered, and nothing told the owner when the
engine quietly changed its mind.

This module answers one question for every stored decision: **would the engine
still say this today?** It re-derives each pairing from scratch — the stored
crosswalk is deliberately NOT consulted, or the answer would just be itself —
and sorts the outcome into verdicts the owner can act on in bulk.

Revising a decision is also how the owner corrects the engine: every ruling here
is a labeled pair, and `match_intel.fit_from_db` trains on owner-origin
crosswalk entries. Keep/accept/reject → refit → the model moves.

Deterministic and offline: no LLM, no network. Scoring is the production linker.
"""
from __future__ import annotations

from datetime import datetime, timezone

# verdicts, worst-first — the order the board presents them in
AGREE = "agree"                  # engine still picks the stored product
MOVED = "moved"                  # engine now picks a DIFFERENT product
LOST = "lost"                    # engine no longer matches anything
STALE = "stale_target"           # the stored IRC is gone from the catalog
REVIVED = "revived"              # a REJECTED pair the engine now matches well
VERDICTS = (STALE, MOVED, LOST, REVIVED, AGREE)

VERDICT_FA = {
    AGREE: "هم‌نظر — موتور همان را می‌گوید",
    MOVED: "جابه‌جا شده — موتور اکنون محصول دیگری را می‌گوید",
    LOST: "بی‌نتیجه — موتور دیگر تطبیقی نمی‌یابد",
    STALE: "مقصد حذف شده — IRC ثبت‌شده در کاتالوگ نیست",
    REVIVED: "بازنگری رد — موتور اکنون تطبیق مطمئن می‌دهد",
}

# last full pass, so the panel can page through it without recomputing
_LAST: dict = {}


def _entry_row(e) -> dict:
    """A crosswalk entry as the linker sees a formulary row. `generic_code` is
    the normalized national-code role both insurers map into."""
    return {"drug_name": e.raw_name or "", "generic_code": e.source_code or None}


def classify(entry_status: str, stored_irc: str | None, picked_irc: str | None,
             confidence: float, *, target_exists: bool,
             min_confidence: float = 0.75) -> str:
    """Pure verdict rule — the whole comparison, testable without a database."""
    if entry_status == "rejected":
        # a refusal the engine now contradicts confidently is worth re-opening;
        # one it still can't match is simply holding
        return REVIVED if (picked_irc and confidence >= min_confidence) else AGREE
    if stored_irc and not target_exists:
        return STALE
    if not picked_irc or confidence < min_confidence:
        return LOST
    return AGREE if picked_irc == stored_irc else MOVED


async def rescore(db, *, insurer: str | None = None, limit: int | None = None,
                  min_confidence: float = 0.75) -> dict:
    """Re-derive every stored decision with the CURRENT engine.

    Returns {summary, items}. `items` carries both sides of each disagreement so
    the owner rules on evidence rather than on a name."""
    import asyncio
    from sqlalchemy import select
    from shared.models.crosswalk import CrosswalkEntry
    from . import repo
    from .coverage_import import link_rows
    from .crosswalk import build_code_registry
    from .enrichment import load_approved
    from .match_intel import extract_features, load_model, score

    q = select(CrosswalkEntry)
    if insurer:
        q = q.where(CrosswalkEntry.insurer == insurer)
    q = q.order_by(CrosswalkEntry.created_at)
    if limit:
        q = q.limit(limit)
    entries = list((await db.execute(q)).scalars().all())
    if not entries:
        return {"summary": {"total": 0}, "items": []}

    catalog = await repo.fetch_all(db)
    by_irc = {r.irc: r for r in catalog}
    enrichments = await load_approved(db)
    code_registry = await build_code_registry(db)
    model = load_model()

    # one linker pass per insurer — blocking makes this the same cost as an
    # import, and crosswalk=None forces a genuine re-derivation
    per_insurer: dict[str, list] = {}
    for e in entries:
        per_insurer.setdefault(e.insurer, []).append(e)

    items: list[dict] = []
    for ins, group in per_insurer.items():
        rows = [_entry_row(e) for e in group]
        links = await asyncio.to_thread(
            link_rows, rows, catalog, enrichments, None, ins, code_registry)
        for e, link in zip(group, links):
            picked = link.record.irc if link.record is not None else None
            conf = float(link.confidence or 0)
            verdict = classify(e.status, e.irc, picked, conf,
                               target_exists=(e.irc in by_irc) if e.irc else True,
                               min_confidence=min_confidence)
            stored_rec, picked_rec = by_irc.get(e.irc or ""), by_irc.get(picked or "")
            fs_stored = fs_picked = None
            if model and e.raw_name:
                if stored_rec is not None:
                    fs_stored = round(score(extract_features(e.raw_name, stored_rec), model), 2)
                if picked_rec is not None:
                    fs_picked = round(score(extract_features(e.raw_name, picked_rec), model), 2)
            items.append({
                "id": str(e.id), "insurer": ins, "verdict": verdict,
                "raw_name": e.raw_name, "code": e.source_code,
                "status": e.status, "origin": e.origin or "owner",
                "decided_at": e.decided_at.isoformat() if e.decided_at else None,
                "stored": _side(stored_rec, e.irc, e.confidence, e.method, fs_stored),
                "engine": _side(picked_rec, picked, conf, link.method, fs_picked),
            })

    order = {v: i for i, v in enumerate(VERDICTS)}
    items.sort(key=lambda it: (order.get(it["verdict"], 99), it["insurer"], it["raw_name"] or ""))
    summary = {"total": len(items), "at": datetime.now(timezone.utc).isoformat(),
               "insurer": insurer or "all"}
    for v in VERDICTS:
        summary[v] = sum(1 for it in items if it["verdict"] == v)
    summary["by_origin"] = {
        o: sum(1 for it in items if it["origin"] == o) for o in ("owner", "auto")}
    summary["disagreements"] = sum(summary.get(v, 0) for v in (STALE, MOVED, LOST, REVIVED))
    _LAST.clear()
    _LAST.update({"summary": summary, "items": items})
    return {"summary": summary, "items": items}


def _side(rec, irc, confidence, method, fs) -> dict:
    return {"irc": irc,
            "name_fa": getattr(rec, "name_fa", None),
            "generic": getattr(rec, "generic_name", None),
            "strength": getattr(rec, "strength", None),
            "dosage_form": getattr(rec, "dosage_form", None),
            "price": (int(rec.announced_price) if getattr(rec, "announced_price", None) else None),
            "confidence": (round(float(confidence), 3) if confidence is not None else None),
            "method": method, "fs": fs, "exists": rec is not None}


def last(verdict: str | None = None, limit: int = 100, offset: int = 0) -> dict:
    """Page through the last pass without recomputing it."""
    items = _LAST.get("items") or []
    if verdict:
        items = [it for it in items if it["verdict"] == verdict]
    return {"summary": _LAST.get("summary") or {"total": 0},
            "total": len(items), "items": items[offset:offset + limit]}


# ── revision ────────────────────────────────────────────────────────────────
ACTIONS = ("keep", "accept_engine", "reject", "reopen")


async def revise(db, ids: list[str], action: str, *, staff_id=None) -> dict:
    """Apply one action to a set of decisions.

    keep          — re-affirm the stored pairing; an auto belief becomes an
                    owner ruling, which is what makes it a training label.
    accept_engine — adopt what the engine says now; the old IRC is kept in
                    `revised_from_irc` so the change stays explainable.
    reject        — this pairing is wrong and must never be proposed again.
    reopen        — delete the decision so the next import reviews it afresh.
    """
    from sqlalchemy import select
    from shared.models.crosswalk import CrosswalkEntry

    if action not in ACTIONS:
        raise RuntimeError(f"کنش ناشناخته: {action}")
    engine_pick = {it["id"]: it for it in (_LAST.get("items") or [])}
    rows = list((await db.execute(
        select(CrosswalkEntry).where(CrosswalkEntry.id.in_(list(ids))))).scalars().all())
    now = datetime.now(timezone.utc)
    changed = skipped = 0
    for r in rows:
        if action == "reopen":
            await db.delete(r)
            changed += 1
            continue
        if action == "accept_engine":
            pick = (engine_pick.get(str(r.id)) or {}).get("engine") or {}
            if not pick.get("irc") or pick["irc"] == r.irc:
                skipped += 1          # nothing to adopt
                continue
            r.revised_from_irc, r.irc = r.irc, pick["irc"]
            r.status, r.confidence = "confirmed", pick.get("confidence")
            r.method = str(pick.get("method") or "")[:32] or None
        elif action == "reject":
            r.status, r.revised_from_irc = "rejected", r.irc
        # keep: the pairing stands as recorded
        r.origin = "owner"            # a person has now looked at it
        r.revised_at = now
        r.decided_by, r.decided_at = staff_id, now
        changed += 1
    await db.commit()
    # keep the cached pass honest — a revised row is no longer a disagreement
    done = {str(i) for i in ids}
    for it in (_LAST.get("items") or []):
        if it["id"] in done:
            it["verdict"] = AGREE if action != "reopen" else LOST
            it["origin"] = "owner"
    return {"changed": changed, "skipped": skipped, "action": action}


async def backfill_auto(db, *, insurer: str | None = None,
                        min_confidence: float = 0.75) -> dict:
    """Turn the links the engine already applied into recorded beliefs.

    Two sources, best first: the snapshots of past runs (what the engine
    actually concluded then), and — for runs staged before snapshots carried a
    verdict — a fresh derivation over the latest snapshot rows. Without this the
    decision board would start empty even though ~38,000 links are live."""
    from sqlalchemy import select
    from shared.models.formulary_snapshot import FormularySnapshot
    from .crosswalk import record_auto_decisions

    q = select(FormularySnapshot.insurer, FormularySnapshot.source_code,
               FormularySnapshot.raw_name, FormularySnapshot.matched_irc,
               FormularySnapshot.match_confidence, FormularySnapshot.match_method
               ).where(FormularySnapshot.matched_irc.isnot(None))
    if insurer:
        q = q.where(FormularySnapshot.insurer == insurer)
    stamped: dict[str, list[dict]] = {}
    for ins, code, name, irc, conf, method in (await db.execute(q)).all():
        stamped.setdefault(ins, []).append(
            {"code": code, "name": name, "irc": irc,
             "confidence": conf, "method": method})

    out: dict = {"from_snapshots": {}, "derived": {}}
    for ins, verdicts in stamped.items():
        out["from_snapshots"][ins] = await record_auto_decisions(
            db, insurer=ins, verdicts=verdicts, min_confidence=min_confidence)

    # insurers whose snapshots predate the verdict columns → derive once
    all_ins = [i for (i,) in (await db.execute(
        select(FormularySnapshot.insurer).distinct())).all()]
    for ins in all_ins:
        if insurer and ins != insurer:
            continue
        if stamped.get(ins):
            continue
        out["derived"][ins] = await _derive_and_record(db, ins, min_confidence)
    return out


async def _derive_and_record(db, insurer: str, min_confidence: float) -> dict:
    """Re-derive the engine's verdicts for one insurer and record them.
    Deterministic — the same code path an import runs.

    Every row this insurer ever published is covered, not just the newest run:
    the live coverage was built up across several imports, and a backfill that
    only read the last one would leave most of it unrecorded. Rows are deduped
    by the insurer's own code (falling back to the name), newest wins."""
    import asyncio
    from sqlalchemy import select
    from shared.models.formulary_snapshot import FormularySnapshot
    from . import repo
    from .coverage_import import link_rows
    from .crosswalk import build_code_registry, record_auto_decisions, row_source_code
    from .enrichment import load_approved
    from .coverage_harvest import link_verdicts

    seen: dict[str, dict] = {}
    for (r,) in (await db.execute(
            select(FormularySnapshot.row)
            .where(FormularySnapshot.insurer == insurer)
            .order_by(FormularySnapshot.created_at.desc()))).all():
        if not isinstance(r, dict) or not r.get("drug_name"):
            continue
        key = row_source_code(r) or f"name:{r['drug_name']}"
        seen.setdefault(key, r)          # first seen = newest publication
    rows = list(seen.values())
    if not rows:
        return {"rows": 0}
    catalog = await repo.fetch_all(db)
    enrichments = await load_approved(db)
    code_registry = await build_code_registry(db)
    links = await asyncio.to_thread(
        link_rows, rows, catalog, enrichments, None, insurer, code_registry)
    res = await record_auto_decisions(db, insurer=insurer,
                                      verdicts=link_verdicts(links),
                                      min_confidence=min_confidence)
    return {"rows": len(rows), **res}


async def board(db) -> dict:
    """Cheap counts for the panel header — no rescoring."""
    from sqlalchemy import func, select
    from shared.models.crosswalk import CrosswalkEntry
    rows = (await db.execute(
        select(CrosswalkEntry.insurer, CrosswalkEntry.origin, CrosswalkEntry.status,
               func.count()).group_by(CrosswalkEntry.insurer, CrosswalkEntry.origin,
                                      CrosswalkEntry.status))).all()
    out: dict = {"total": 0, "by_insurer": {}, "by_origin": {}, "by_status": {}}
    for insurer, origin, status, n in rows:
        out["total"] += n
        out["by_insurer"][insurer] = out["by_insurer"].get(insurer, 0) + n
        o = origin or "owner"
        out["by_origin"][o] = out["by_origin"].get(o, 0) + n
        out["by_status"][status] = out["by_status"].get(status, 0) + n
    revised = (await db.execute(select(func.count()).select_from(CrosswalkEntry)
                                .where(CrosswalkEntry.revised_at.isnot(None)))).scalar()
    out["revised"] = int(revised or 0)
    out["last_pass"] = _LAST.get("summary")
    return out
