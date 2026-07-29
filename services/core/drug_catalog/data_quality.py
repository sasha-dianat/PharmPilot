"""سلامت داده — the standing reconciliation engine.

Every defect this platform has ever shipped was found the same way: a manual
SQL session after something looked wrong. This module makes that session a
permanent, runnable artifact — the exact invariant checks from the 2026-07-29
full audit, executable at any time, so the NEXT formulary update or NFI crawl
is verified by machinery instead of by suspicion.

Design rules:
  * Checks are read-only and deterministic. Running the report changes nothing.
  * Every check names its severity, the invariant it guards, and what to do
    when it fires — a report line is an instruction, not a number.
  * fix() applies ONLY the repairs that are provably safe (normalizations with
    no information loss). Anything requiring judgement stays a report line.

Severities:
  critical — the invariant protects money or clinical meaning; act now
  warn     — drift that will compound if ignored
  info     — expected states worth watching (backlogs, source gaps)
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text

# Each check: (key, severity, invariant, action, SQL returning one count)
# SQL only — these must stay runnable against the live DB without model drift.
CHECKS: list[tuple[str, str, str, str, str]] = [
    # ── catalog identity ────────────────────────────────────────────────────
    ("catalog_duplicate_irc", "critical",
     "IRC is the anchor every decided fact hangs on — one row per IRC",
     "deduplicate immediately; decisions may be pointing at either copy",
     "SELECT count(*) FROM (SELECT irc FROM drug_catalog GROUP BY irc HAVING count(*)>1) t"),
    ("catalog_empty_irc", "critical",
     "a row without an IRC can never be matched, priced, or corrected",
     "delete or repair the rows; find where they were inserted",
     "SELECT count(*) FROM drug_catalog WHERE coalesce(btrim(irc),'')=''"),
    ("catalog_coverage_json_null", "warn",
     "coverage is either a real object or SQL NULL — a JSON null makes "
     "'IS NOT NULL' queries lie",
     "fix() normalizes these to SQL NULL",
     "SELECT count(*) FROM drug_catalog WHERE coverage IS NOT NULL AND jsonb_typeof(coverage)='null'"),
    ("catalog_spliced_unrepaired", "warn",
     "a quarantined monograph is a page that contradicted itself; its clinical "
     "text is withheld until repaired",
     "review in «مغایرت داخلی NFI» and apply repairs; a genuine splice with no "
     "donor is ruled 'accepted' there and stops counting here",
     # A ruled item must stop firing, or the operator learns to ignore the
     # report. The ruling itself stays auditable in issue_dispositions.
     "SELECT count(*) FROM drug_catalog dc "
     "WHERE dc.monograph->'integrity'->>'spliced_page'='true' "
     "AND coalesce(dc.monograph->'integrity'->>'repaired','')<>'true' "
     "AND NOT EXISTS (SELECT 1 FROM issue_dispositions d "
     "  WHERE d.issue_type='nfi_spliced' AND d.subject_key=dc.irc "
     "  AND d.disposition IN ('accepted','wont_fix'))"),

    # ── decided layer ───────────────────────────────────────────────────────
    ("crosswalk_confirmed_without_irc", "critical",
     "a confirmed decision must point somewhere",
     "these rows assert a match to nothing — reopen them",
     "SELECT count(*) FROM crosswalk_entries WHERE status='confirmed' AND coalesce(btrim(irc),'')=''"),
    ("crosswalk_dangling_irc", "critical",
     "decisions must point at products that exist",
     "the product was removed after the ruling — candidates for succession",
     "SELECT count(*) FROM crosswalk_entries c WHERE c.irc IS NOT NULL "
     "AND NOT EXISTS (SELECT 1 FROM drug_catalog d WHERE d.irc=c.irc)"),
    ("crosswalk_owner_conflicts", "warn",
     "one code, one current owner ruling — two contradicting rows made the "
     "linker's answer depend on dict iteration order until 0030's read rule",
     "resolve in «بازبینی تصمیم‌ها»; until then the LATEST ruling wins deterministically",
     "SELECT count(*) FROM (SELECT insurer, source_code FROM crosswalk_entries "
     "WHERE source_code IS NOT NULL AND origin='owner' GROUP BY 1,2 "
     "HAVING count(DISTINCT status)>1) t"),
    ("overrides_dangling_irc", "warn",
     "an override on a vanished IRC silently protects nothing",
     "candidates for succession carry-over, then delete",
     "SELECT count(*) FROM field_overrides o WHERE NOT EXISTS "
     "(SELECT 1 FROM drug_catalog d WHERE d.irc=o.irc)"),

    # ── observed layer ──────────────────────────────────────────────────────
    ("snapshots_orphaned", "warn",
     "every snapshot belongs to a run — orphans mean a run was deleted "
     "without its observations",
     "attribute or archive them; never train on unattributed rows",
     "SELECT count(*) FROM formulary_snapshots s WHERE s.run_id IS NOT NULL "
     "AND NOT EXISTS (SELECT 1 FROM coverage_runs r WHERE r.id=s.run_id)"),
    ("runs_stale_parsed", "info",
     "a parsed run is a staged proposal awaiting a human — a growing backlog "
     "means updates are arriving faster than review",
     "review or reject them in «اجراها»; staged results go stale as the engine improves",
     "SELECT count(*) FROM coverage_runs WHERE status='parsed' "
     "AND started_at < now() - interval '7 days'"),
    ("runs_mislabeled_insurer", "critical",
     "a run's rows must belong to the insurer it is filed under (the guard "
     "now blocks this at upload; this catches anything already inside)",
     "reject the run; its codes poison the national-code registry",
     "SELECT count(DISTINCT r.id) FROM coverage_runs r WHERE r.status IN ('parsed') "
     "AND (SELECT count(*) FROM formulary_snapshots s2 JOIN formulary_snapshots o "
     "  ON o.insurer<>r.insurer AND o.run_id<>r.id AND o.raw_name=s2.raw_name "
     "  WHERE s2.run_id=r.id) > "
     "  3 * greatest(1,(SELECT count(*) FROM formulary_snapshots s3 "
     "  JOIN formulary_snapshots o2 ON o2.insurer=r.insurer AND o2.run_id<>r.id "
     "  AND o2.raw_name=s3.raw_name WHERE s3.run_id=r.id))"),

    # ── price & coverage semantics ──────────────────────────────────────────
    ("coverage_share_over_100", "critical",
     "an insurer share above 100% is arithmetic nonsense and corrupts quotes",
     "trace the run that wrote it; the multi-tier cell parser guards this",
     "SELECT count(*) FROM drug_catalog d, jsonb_each(d.coverage) e "
     "WHERE jsonb_typeof(d.coverage)='object' AND coalesce((e.value->>'share_pct')::numeric,0)>100"),
    ("price_refreshed_without_history", "critical",
     "every announced-price change carries an SCD-2 row — a price that moved "
     "without history cannot be explained or rolled back",
     "backfill via price_history.record_price; never UPDATE announced_price directly",
     "SELECT count(*) FROM drug_catalog dc "
     "WHERE dc.monograph->'price_provenance'->>'source' IS NOT NULL "
     "AND NOT EXISTS (SELECT 1 FROM price_history ph WHERE ph.irc=dc.irc "
     "  AND ph.price_type='announced' AND ph.insurer IS NULL AND ph.valid_to IS NULL "
     "  AND ph.value = dc.announced_price::bigint)"),
    ("price_gap_extreme_strong_identity", "warn",
     "a >10x gap on a CERTAIN match means our announced price is stale — the "
     "identity is not in doubt, only the number",
     "refresh from the insurer reference (increases only, provenance stamped)",
     "SELECT count(DISTINCT dc.irc) FROM drug_catalog dc, jsonb_each(dc.coverage) e "
     "WHERE dc.announced_price>0 AND jsonb_typeof(dc.coverage)='object' "
     "AND jsonb_typeof(e.value)='object' "
     # the SAME predicate the issue registry counts price_gap_extreme with —
     # a reconciliation engine that disagrees with the board is worse than none
     "AND abs((e.value->>'reference_price')::numeric - dc.announced_price) "
     "    > 10*dc.announced_price "
     "AND (e.value->>'reference_price')::numeric > dc.announced_price "
     "AND (coalesce((e.value->>'match_confidence')::numeric,0) >= 0.90 "
     "     OR e.value->>'match_method' IN ('code','crosswalk','irc')) "
     "AND coalesce(dc.package_count,0) <= 1"),
    ("coverage_ref_price_no_announced", "info",
     "an insurer prices a product NFI does not — usually a lapsed registration "
     "still covered, or an NFI source gap",
     "candidates for the insurer-derived price flow (group B)",
     "SELECT count(*) FROM drug_catalog d, jsonb_each(d.coverage) e "
     "WHERE jsonb_typeof(d.coverage)='object' "
     "AND coalesce((e.value->>'reference_price')::numeric,0)>0 AND coalesce(d.announced_price,0)=0"),

    # ── operational backlogs ────────────────────────────────────────────────
    ("harvest_failures_pending", "info",
     "pages lost to transport failures, waiting for a targeted retry",
     "run «تلاش دوباره» when the proxy is up",
     "SELECT count(*) FROM harvest_failures WHERE resolved_at IS NULL"),
    ("succession_proposals_pending", "info",
     "re-registrations detected but not yet carried over",
     "review in «جانشینی IRC»",
     "SELECT count(*) FROM catalog_succession WHERE status='proposed'"),
]


async def report(db) -> dict:
    """Run every check. Read-only."""
    out: list[dict] = []
    for key, severity, invariant, action, sql in CHECKS:
        try:
            n = int((await db.execute(text(sql))).scalar() or 0)
            out.append({"check": key, "severity": severity, "count": n,
                        "ok": n == 0 or severity == "info",
                        "invariant": invariant, "action": action})
        except Exception as e:                      # a broken check is itself a finding
            out.append({"check": key, "severity": "critical", "count": -1,
                        "ok": False, "invariant": invariant,
                        "action": f"the check itself failed: {type(e).__name__}: {e}"})
    sev_rank = {"critical": 0, "warn": 1, "info": 2}
    out.sort(key=lambda c: (sev_rank.get(c["severity"], 9), -c["count"]))
    firing = [c for c in out if c["count"] != 0 and c["severity"] != "info"]
    return {"at": datetime.now(timezone.utc).isoformat(),
            "healthy": not firing,
            "firing": len(firing),
            "checks": out}


async def fix(db) -> dict:
    """Apply only the provably-safe normalizations. Everything else is a
    report line for a human."""
    # JSON null → SQL NULL: no information exists in a JSON null, so this is
    # lossless — and it makes every `coverage IS NOT NULL` query honest again.
    res = await db.execute(text(
        "UPDATE drug_catalog SET coverage=NULL "
        "WHERE coverage IS NOT NULL AND jsonb_typeof(coverage)='null'"))
    await db.commit()
    return {"coverage_json_null_normalized": res.rowcount or 0}
