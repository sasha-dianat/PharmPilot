"""Audit mode + resume: the two guarantees the GUI now depends on.

1. An audit pass writes evidence to disk and NOTHING to the catalog, and never
   re-fetches a page it already holds — the whole point of a slow-proxy scan.
2. A stopped run leaves a resume point on disk that survives a backend restart,
   while a targeted retry (driven by the failure registry) leaves none.
"""
from __future__ import annotations

import json

import pytest

from services.core.drug_catalog import nfi_audit
from services.core.drug_catalog import nfi_harvest_service as svc


@pytest.fixture
def audit_dir(tmp_path, monkeypatch):
    out = tmp_path / "nfi_audit"
    monkeypatch.setattr(nfi_audit, "OUT", out)
    monkeypatch.setattr(nfi_audit, "PAGES", out / "pages")
    monkeypatch.setattr(nfi_audit, "INDEX", out / "index.jsonl")
    monkeypatch.setattr(nfi_audit, "IRCMAP", out / "irc_page_map.csv")
    return out


CLEAN = {"irc": "1234", "name_fa": "رانیتیدین", "generic_name": "RANITIDINE",
         "manufacturer": "تولیددارو", "country": "ایران", "announced_price": 5000,
         "strength": "150 mg", "atc": "A02BA02", "brands": [{"name": "x"}]}
HTML_OK = "<html>قیمت محصولات مشابه</html>"


def test_clean_page_is_indexed_without_saving_source(audit_dir):
    assert nfi_audit.write_page(212, CLEAN, HTML_OK) is False
    assert not (audit_dir / "pages" / "212.html").exists()
    row = json.loads(nfi_audit.INDEX.read_text(encoding="utf-8").strip())
    assert row["flags"] == [] and row["url"].endswith("/NFI/Detail/212")
    # the irc → page-id link is the durable output: it is what makes a bad
    # catalog row traceable back to its own source page
    assert "1234,212" in nfi_audit.IRCMAP.read_text(encoding="utf-8")


def test_flagged_page_keeps_the_raw_source(audit_dir):
    rec = dict(CLEAN, country=None, brands=[])
    assert nfi_audit.write_page(99, rec, "<html>قیمت</html>") is True
    saved = (audit_dir / "pages" / "99.html").read_text(encoding="utf-8")
    assert saved == "<html>قیمت</html>"
    flags = json.loads(nfi_audit.INDEX.read_text(encoding="utf-8").strip())["flags"]
    # "the source never published it" is recorded apart from "the field is empty"
    assert {"no_country", "no_brands_table", "section_similar_absent"} <= set(flags)


def test_failures_are_not_counted_as_done(audit_dir):
    nfi_audit.write_page(1, CLEAN, HTML_OK)
    nfi_audit.write_failure(2, http=502)
    nfi_audit.write_failure(3, error="URLError")
    assert nfi_audit.load_done() == {1}          # 2 and 3 must be retried
    s = nfi_audit.summary()
    assert (s["pages"], s["failed"]) == (1, 2)


def test_summary_histograms_flags(audit_dir):
    nfi_audit.write_page(1, dict(CLEAN, announced_price=None, package_price=None), HTML_OK)
    nfi_audit.write_page(2, dict(CLEAN, atc=None), HTML_OK)
    assert nfi_audit.summary()["by_flag"] == {"no_price": 1, "no_atc": 1}


# ── resume ────────────────────────────────────────────────────────────────
@pytest.fixture
def progress(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "PROGRESS", tmp_path / "nfi_progress.json")
    return svc.PROGRESS


def test_resume_point_survives_a_restart(progress):
    svc.save_progress(20_890, 70_000, "ingest", "nfi-harvest")
    p = svc.load_progress()                      # fresh read from disk
    assert p["resume_from"] == 20_891 and p["end_id"] == 70_000
    assert p["mode"] == "ingest" and p["saved_at"]


def test_finished_run_offers_nothing_to_resume(progress):
    svc.save_progress(70_000, 70_000, "audit", "nfi-audit")
    assert svc.load_progress() is None
    svc.clear_progress()
    assert svc.load_progress() is None


def test_status_exposes_resume_when_idle(progress, monkeypatch):
    svc.save_progress(500, 5_000, "audit", "nfi-audit")
    monkeypatch.setattr(svc, "_STATE", svc.HarvestState(running=False))
    assert svc.status()["resume"]["resume_from"] == 501
    # a running harvest must never advertise a resume button
    monkeypatch.setattr(svc, "_STATE", svc.HarvestState(running=True))
    assert svc.status()["resume"] is None


def test_start_rejects_an_unknown_mode():
    with pytest.raises(RuntimeError, match="حالت ناشناخته"):
        svc.start(1, 10, mode="delete-everything")


def test_resume_without_a_saved_point_is_an_error(progress):
    svc.clear_progress()
    with pytest.raises(RuntimeError, match="ادامه"):
        svc.resume()


def test_progress_counts_skipped_ids_as_covered():
    """An audit pass skips ids it already holds; the bar must still advance —
    otherwise a mostly-done range looks stalled at 0٪."""
    st = svc.HarvestState(running=True, start_id=1, end_id=100,
                          last_id=75, scanned=5, started_at=1.0)
    assert st.snapshot()["progress_pct"] == 75.0


def test_a_rejected_mode_does_not_hold_the_harvest_lock():
    """Validation happens before the lock is taken — otherwise one bad request
    from the GUI would block every later harvest until a restart."""
    from services.core.drug_catalog import harvest_lock
    with pytest.raises(RuntimeError, match="حالت ناشناخته"):
        svc.start(1, 10, mode="nonsense")
    assert harvest_lock.holder() is None
