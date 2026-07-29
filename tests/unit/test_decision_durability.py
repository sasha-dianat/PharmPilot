"""Decisions must outlive both a re-harvest and a change to the matcher.

The invariants under test:
  1. an auto-recorded belief NEVER overwrites an owner ruling, and never
     short-circuits the linker (or a 0.76 guess would freeze as a certainty);
  2. re-scoring re-derives from scratch and names its disagreements;
  3. revising is what teaches the engine — owner rulings become labels;
  4. succession carries decided data forward additively, never destructively;
  5. the succession detector stays silent without real evidence.
"""
from __future__ import annotations

import json
import os

import pytest

from services.core.drug_catalog import decision_review as dr
from services.core.drug_catalog import succession

# The project's DB-test convention: talk to the real dev database, skip cleanly
# when it is not up, and clean up after yourself. Every fixture row below uses a
# T29- prefix so a failed run can never poison real data.
PREFIX = "T29-"


DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://pharmpilot:pharmpilot_dev@127.0.0.1:5433/pharmpilot_test")


@pytest.fixture
async def db_session():
    """A real session against the dev DB, scrubbed before and after.

    NullPool on purpose: pytest gives each test its own event loop, and a
    pooled asyncpg connection opened on a loop that has since closed fails at
    teardown instead of at the assertion you care about."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from sqlalchemy import text

    engine = create_async_engine(DB_URL, poolclass=NullPool)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as probe:
            await probe.execute(text("SELECT 1"))
    except Exception:
        await engine.dispose()
        pytest.skip("dev DB unreachable")
    async with Session() as db:
        await _cleanup(db)
        try:
            yield db
        finally:
            await _cleanup(db)
    await engine.dispose()


async def _cleanup(db):
    from sqlalchemy import delete
    from shared.models.catalog_succession import CatalogSuccession
    from shared.models.crosswalk import CrosswalkEntry, FieldOverride
    from shared.models.drug_catalog import DrugCatalogItem
    await db.execute(delete(CrosswalkEntry).where(
        CrosswalkEntry.raw_name.like(f"{PREFIX}%")))
    await db.execute(delete(FieldOverride).where(FieldOverride.irc.like(f"{PREFIX}%")))
    await db.execute(delete(CatalogSuccession).where(
        CatalogSuccession.old_irc.like(f"{PREFIX}%")))
    await db.execute(delete(DrugCatalogItem).where(DrugCatalogItem.irc.like(f"{PREFIX}%")))
    await db.commit()


# ── verdict rule (pure) ─────────────────────────────────────────────────────
@pytest.mark.parametrize("stored,picked,conf,exists,expected", [
    ("A", "A", 0.90, True, dr.AGREE),
    ("A", "B", 0.90, True, dr.MOVED),
    ("A", None, 0.00, True, dr.LOST),
    ("A", "A", 0.40, True, dr.LOST),        # below threshold is not agreement
    ("A", "A", 0.90, False, dr.STALE),      # the stored product is gone
])
def test_classify_confirmed(stored, picked, conf, exists, expected):
    assert dr.classify("confirmed", stored, picked, conf,
                       target_exists=exists) == expected


def test_classify_rejected_only_revives_on_a_confident_match():
    # a refusal the engine now contradicts is worth re-opening…
    assert dr.classify("rejected", None, "B", 0.95, target_exists=True) == dr.REVIVED
    # …but a refusal it still cannot match is simply holding
    assert dr.classify("rejected", None, None, 0.0, target_exists=True) == dr.AGREE
    assert dr.classify("rejected", None, "B", 0.50, target_exists=True) == dr.AGREE


# ── auto beliefs vs owner rulings ───────────────────────────────────────────
async def test_auto_never_overwrites_an_owner_ruling(db_session):
    from services.core.drug_catalog.crosswalk import (
        load_crosswalk, record_auto_decisions, record_decision)

    await record_decision(db_session, insurer="tamin", raw_name=f"{PREFIX}ACME 10 mg TAB",
                          irc=f"{PREFIX}OWNER", status="confirmed", source_code="777")
    await db_session.commit()
    res = await record_auto_decisions(db_session, insurer="tamin", verdicts=[
        {"code": "777", "name": f"{PREFIX}ACME 10 mg TAB", "irc": f"{PREFIX}ENGINE",
         "confidence": 0.99, "method": "structural"}])
    await db_session.commit()

    assert res["auto_skipped"] == 1 and res["auto_updated"] == 0
    cw = await load_crosswalk(db_session, "tamin")
    assert cw["tamin|code:777"]["irc"] == f"{PREFIX}OWNER"


async def test_auto_beliefs_are_recorded_but_not_consulted_by_the_linker(db_session):
    from services.core.drug_catalog.crosswalk import load_crosswalk, record_auto_decisions

    await record_auto_decisions(db_session, insurer="salamat", verdicts=[
        {"code": "888", "name": f"{PREFIX}BETA 5 mg CAP", "irc": f"{PREFIX}AUTO",
         "confidence": 0.80, "method": "ingredient"}])
    await db_session.commit()

    # the linker's view (owner only) must not see it — otherwise every future
    # improvement to the matcher is short-circuited by its own past guess
    assert "salamat|code:888" not in await load_crosswalk(db_session, "salamat")
    # but the record exists, which is what makes drift visible
    everything = await load_crosswalk(db_session, "salamat", origin=None)
    assert everything["salamat|code:888"]["irc"] == f"{PREFIX}AUTO"


async def test_a_moved_link_is_reported_not_silently_swapped(db_session):
    from services.core.drug_catalog.crosswalk import record_auto_decisions

    v = {"code": "999", "name": f"{PREFIX}GAMMA 20 mg", "confidence": 0.9, "method": "structural"}
    await record_auto_decisions(db_session, insurer="tamin", verdicts=[{**v, "irc": f"{PREFIX}IRC1"}])
    await db_session.commit()
    res = await record_auto_decisions(db_session, insurer="tamin",
                                      verdicts=[{**v, "irc": f"{PREFIX}IRC2"}])
    await db_session.commit()

    assert res["auto_moved_total"] == 1
    assert res["auto_moved"][0]["from_irc"] == f"{PREFIX}IRC1"
    assert res["auto_moved"][0]["to_irc"] == f"{PREFIX}IRC2"


async def test_below_threshold_links_are_never_recorded(db_session):
    from services.core.drug_catalog.crosswalk import load_crosswalk, record_auto_decisions
    res = await record_auto_decisions(db_session, insurer="tamin", verdicts=[
        {"code": "111", "name": f"{PREFIX}WEAK", "irc": f"{PREFIX}X", "confidence": 0.5, "method": "ingredient"}])
    await db_session.commit()
    assert res["auto_skipped"] == 1
    assert "tamin|code:111" not in await load_crosswalk(db_session, "tamin", origin=None)


# ── revision feeds the model ────────────────────────────────────────────────
async def test_revision_promotes_the_row_and_stamps_the_audit(db_session):
    from sqlalchemy import select
    from shared.models.crosswalk import CrosswalkEntry
    from services.core.drug_catalog.crosswalk import record_auto_decisions

    await record_auto_decisions(db_session, insurer="tamin", verdicts=[
        {"code": "222", "name": f"{PREFIX}DELTA 1 mg", "irc": f"{PREFIX}OLD", "confidence": 0.9,
         "method": "structural"}])
    await db_session.commit()
    row = (await db_session.execute(select(CrosswalkEntry).where(
        CrosswalkEntry.source_code == "222"))).scalar_one()

    dr._LAST.clear()
    dr._LAST.update({"items": [{"id": str(row.id), "verdict": dr.MOVED,
                                "engine": {"irc": f"{PREFIX}NEW", "confidence": 0.88,
                                           "method": "structural"}}]})
    out = await dr.revise(db_session, [str(row.id)], "accept_engine")

    assert out["changed"] == 1
    await db_session.refresh(row)
    assert (row.irc, row.revised_from_irc) == (f"{PREFIX}NEW", f"{PREFIX}OLD")
    # an auto belief a person has now examined is an owner ruling — which is
    # exactly what makes it a training label
    assert row.origin == "owner" and row.revised_at is not None


async def test_reopen_removes_the_decision_so_it_returns_to_review(db_session):
    from sqlalchemy import select
    from shared.models.crosswalk import CrosswalkEntry
    from services.core.drug_catalog.crosswalk import record_decision

    await record_decision(db_session, insurer="tamin", raw_name=f"{PREFIX}EPS 2 mg",
                          irc=f"{PREFIX}Z", status="confirmed", source_code="333")
    await db_session.commit()
    row = (await db_session.execute(select(CrosswalkEntry).where(
        CrosswalkEntry.source_code == "333"))).scalar_one()
    await dr.revise(db_session, [str(row.id)], "reopen")
    gone = (await db_session.execute(select(CrosswalkEntry).where(
        CrosswalkEntry.source_code == "333"))).scalar_one_or_none()
    assert gone is None


async def test_an_unknown_action_is_refused(db_session):
    with pytest.raises(RuntimeError, match="کنش ناشناخته"):
        await dr.revise(db_session, [], "delete_everything")


# ── succession ──────────────────────────────────────────────────────────────
def test_detector_is_silent_on_a_single_pass(tmp_path):
    """One observation per page is the normal state — 10,487 audited pages, zero
    with two IRCs. A detector that invented proposals here would be noise."""
    idx = tmp_path / "index.jsonl"
    idx.write_text("\n".join(json.dumps(r) for r in [
        {"page_id": 1, "irc": "A", "flags": []},
        {"page_id": 2, "irc": "B", "flags": []},
        {"page_id": 3, "error": "URLError"},
    ]), encoding="utf-8")
    assert succession.detect(idx) == []


def test_detector_fires_when_a_page_changes_its_irc(tmp_path):
    idx = tmp_path / "index.jsonl"
    idx.write_text("\n".join(json.dumps(r) for r in [
        {"page_id": 7, "irc": "OLD", "flags": []},
        {"page_id": 7, "irc": "OLD", "flags": []},     # unchanged re-observation
        {"page_id": 7, "irc": "NEW", "flags": []},     # re-registration
    ]), encoding="utf-8")
    found = succession.detect(idx)
    assert len(found) == 1
    assert (found[0]["old_irc"], found[0]["new_irc"]) == ("OLD", "NEW")
    assert found[0]["evidence"]["url"].endswith("/NFI/Detail/7")


async def test_succession_carries_decisions_forward_without_overwriting(db_session):
    from sqlalchemy import select
    from shared.models.catalog_succession import CatalogSuccession
    from shared.models.crosswalk import CrosswalkEntry, FieldOverride
    from shared.models.drug_catalog import DrugCatalogItem
    from services.core.drug_catalog.crosswalk import record_decision, set_override

    db_session.add(DrugCatalogItem(
        irc=f"{PREFIX}OLD1", name_fa="قدیمی", generic_name="ALPHA",
        ingredient_key="alpha|5 mg|tablet", country="آلمان",
        coverage={"tamin": {"covered": True, "share_pct": 70},
                  "salamat": {"covered": True, "share_pct": 50}}))
    db_session.add(DrugCatalogItem(
        irc=f"{PREFIX}NEW1", name_fa="جدید", generic_name="ALPHA",
        ingredient_key="alpha|5 mg|tablet",
        coverage={"salamat": {"covered": False, "share_pct": 0}}))
    await set_override(db_session, f"{PREFIX}OLD1", "country", "سوئیس", reason="owner fix")
    await record_decision(db_session, insurer="tamin", raw_name=f"{PREFIX}ALPHA 5 mg",
                          irc=f"{PREFIX}OLD1", status="confirmed", source_code="444")
    s = CatalogSuccession(page_id=7, old_irc=f"{PREFIX}OLD1", new_irc=f"{PREFIX}NEW1", status="proposed")
    db_session.add(s)
    await db_session.commit()

    out = await succession.apply(db_session, [str(s.id)])
    assert out["applied"] == 1

    new = (await db_session.execute(select(DrugCatalogItem).where(
        DrugCatalogItem.irc == f"{PREFIX}NEW1"))).scalar_one()
    # the insurer the successor lacked is carried; the one it HAS is untouched
    assert new.coverage["tamin"]["share_pct"] == 70
    assert new.coverage["tamin"]["carried_from"] == f"{PREFIX}OLD1"
    assert new.coverage["salamat"]["covered"] is False
    # the owner's correction follows the product
    ov = (await db_session.execute(select(FieldOverride).where(
        FieldOverride.irc == f"{PREFIX}NEW1", FieldOverride.field == "country"))).scalar_one()
    assert ov.value == "سوئیس"
    # and every formulary decision now points at the successor
    e = (await db_session.execute(select(CrosswalkEntry).where(
        CrosswalkEntry.source_code == "444"))).scalar_one()
    assert (e.irc, e.revised_from_irc) == (f"{PREFIX}NEW1", f"{PREFIX}OLD1")
    # both rows keep a trail
    old = (await db_session.execute(select(DrugCatalogItem).where(
        DrugCatalogItem.irc == f"{PREFIX}OLD1"))).scalar_one()
    assert old.monograph["superseded_by"] == f"{PREFIX}NEW1"
    assert new.monograph["succeeds"] == f"{PREFIX}OLD1"


async def test_succession_without_a_successor_row_is_skipped(db_session):
    from shared.models.catalog_succession import CatalogSuccession
    s = CatalogSuccession(old_irc=f"{PREFIX}GHOST-OLD", new_irc=f"{PREFIX}GHOST-NEW", status="proposed")
    db_session.add(s)
    await db_session.commit()
    out = await succession.apply(db_session, [str(s.id)])
    assert (out["applied"], out["skipped"]) == (0, 1)


async def test_backfill_stamps_the_page_id_on_catalog_rows(tmp_path, db_session):
    from sqlalchemy import select
    from shared.models.drug_catalog import DrugCatalogItem

    db_session.add(DrugCatalogItem(irc=f"{PREFIX}BF1", name_fa="نمونه",
                                   generic_name="BETA", ingredient_key="beta||"))
    await db_session.commit()
    csv_path = tmp_path / "irc_page_map.csv"
    csv_path.write_text(f"irc,page_id,name_fa\n{PREFIX}BF1,4242,نمونه\n{PREFIX}MISSING,99,x\n",
                        encoding="utf-8")

    out = await succession.backfill_nfi_id(db_session, csv_path)
    assert out["updated"] == 1 and out["missing"] == 1
    row = (await db_session.execute(select(DrugCatalogItem).where(
        DrugCatalogItem.irc == f"{PREFIX}BF1"))).scalar_one()
    assert row.monograph["nfi_id"] == 4242


# ── the 2026-07-29 re-upload found these two ────────────────────────────────
async def test_a_coded_decision_never_spreads_by_truncated_name(db_session):
    """salamat prints ONE truncated name for many distinct products, so a
    decision keyed by name swept them all onto a single product: «IOHEXOL» —
    14 codes, 14 different strengths and volumes — resolved to 1 IRC at
    confidence 1.0, never reaching review."""
    from services.core.drug_catalog.crosswalk import load_crosswalk, record_decision

    await record_decision(db_session, insurer="salamat", raw_name=f"{PREFIX}IOHEXOL",
                          irc=f"{PREFIX}IOHEXOL-300", status="confirmed",
                          source_code="60001")
    await db_session.commit()
    cw = await load_crosswalk(db_session, "salamat")

    assert cw[f"salamat|code:60001"]["irc"] == f"{PREFIX}IOHEXOL-300"
    # the sibling codes must NOT inherit it — they are different products
    from services.core.drug_catalog.enrichment import enrich_key
    assert f"salamat|name:{enrich_key(f'{PREFIX}IOHEXOL')}" not in cw


async def test_a_codeless_decision_still_resolves_by_name(db_session):
    """Insurers that publish no code have nothing else to key on."""
    from services.core.drug_catalog.crosswalk import load_crosswalk, record_decision
    from services.core.drug_catalog.enrichment import enrich_key

    await record_decision(db_session, insurer="armed", raw_name=f"{PREFIX}SOLO PRODUCT",
                          irc=f"{PREFIX}SOLO", status="confirmed", source_code=None)
    await db_session.commit()
    cw = await load_crosswalk(db_session, "armed")
    assert cw[f"armed|name:{enrich_key(f'{PREFIX}SOLO PRODUCT')}"]["irc"] == f"{PREFIX}SOLO"


def test_insurer_guard_reads_the_name_column_whatever_it_is_called():
    """A real دارونامه names its columns in Persian. Reading r['drug_name'] off
    the RAW rows saw 0 names in 3,679, fell under the 20-name floor and passed
    the file through — which is how a salamat export was staged as tamin."""
    from services.core.drug_catalog.coverage_harvest import (
        detect_insurer_mismatch, normalize_rows, resolve_roles)

    salamat_like = [{"رديف": i, "کد_ژنريک": f"{i:05d}", "عنوان": n,
                     "شرايط_تعهد": "", "شکل": "TAB", "دوز": "10 mg"}
                    for i, n in enumerate(
                        [f"DRUG {i}" for i in range(40)], start=1)]
    raw_names = [r.get("drug_name") or r.get("name") or "" for r in salamat_like]
    assert not any(raw_names)                      # the old guard saw nothing
    names = [r.get("drug_name") or "" for r in
             normalize_rows(salamat_like, resolve_roles(salamat_like, None))]
    assert sum(1 for n in names if n) >= 40        # the fixed guard sees them

    known = {"salamat": set(names), "tamin": {"SOMETHING ELSE ENTIRELY 5 mg TAB"}}
    assert detect_insurer_mismatch(names, known, "tamin") == {
        "looks_like": "salamat", "match_pct": 100, "selected_pct": 0}
    assert detect_insurer_mismatch(names, known, "salamat") is None


# ── the standing reconciliation engine ──────────────────────────────────────
async def test_data_quality_report_runs_every_check(db_session):
    from services.core.drug_catalog.data_quality import CHECKS, report
    rep = await report(db_session)
    assert len(rep["checks"]) == len(CHECKS)
    # a broken check must surface as a finding, never crash the report
    assert all(c["count"] >= 0 for c in rep["checks"]), \
        [c for c in rep["checks"] if c["count"] < 0]
    assert all(c["invariant"] and c["action"] for c in rep["checks"])


async def test_data_quality_fix_normalizes_json_null_coverage(db_session):
    from sqlalchemy import select, text
    from shared.models.drug_catalog import DrugCatalogItem
    from services.core.drug_catalog.data_quality import fix

    db_session.add(DrugCatalogItem(irc=f"{PREFIX}JN", name_fa="x", generic_name="X",
                                   ingredient_key="x||"))
    await db_session.commit()
    # force the pathological state: a JSON null, not SQL NULL
    await db_session.execute(text(
        "UPDATE drug_catalog SET coverage='null'::jsonb WHERE irc=:irc"),
        {"irc": f"{PREFIX}JN"})
    await db_session.commit()
    out = await fix(db_session)
    assert out["coverage_json_null_normalized"] >= 1
    row = (await db_session.execute(select(DrugCatalogItem).where(
        DrugCatalogItem.irc == f"{PREFIX}JN"))).scalar_one()
    assert row.coverage is None                      # SQL NULL now


async def test_conflicting_owner_rulings_resolve_to_the_latest(db_session):
    """Two owner rows on one code (different spellings, opposite verdicts) made
    the linker's answer depend on Postgres row order. The latest ruling must
    win, deterministically."""
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import select
    from shared.models.crosswalk import CrosswalkEntry
    from services.core.drug_catalog.crosswalk import load_crosswalk, record_decision

    await record_decision(db_session, insurer="tamin", raw_name=f"{PREFIX}SIRO BRAND X",
                          irc=None, status="rejected", source_code="55055")
    await record_decision(db_session, insurer="tamin", raw_name=f"{PREFIX}SIROLIMUS GEN",
                          irc=f"{PREFIX}SIRO", status="confirmed", source_code="55055")
    await db_session.commit()
    # make the CONFIRMED row decidedly newer
    rows = (await db_session.execute(select(CrosswalkEntry).where(
        CrosswalkEntry.source_code == "55055"))).scalars().all()
    base = datetime.now(timezone.utc)
    for r in rows:
        r.decided_at = base + (timedelta(hours=1) if r.status == "confirmed"
                               else timedelta(hours=-1))
    await db_session.commit()

    for _ in range(3):                               # stable across reads
        cw = await load_crosswalk(db_session, "tamin")
        assert cw["tamin|code:55055"]["status"] == "confirmed"
        assert cw["tamin|code:55055"]["irc"] == f"{PREFIX}SIRO"
