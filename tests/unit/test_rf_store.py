"""Access points and a surveyed radio map, stored.

The RF engine has had fingerprinting since phase 1 and it has never run in
production: the live endpoint passes RadioMap([]), so locate() always falls
through to trilateration — the method the module itself calls 5-15m and "close
to useless" in a depot. This phase supplies the missing data.
"""
from __future__ import annotations

import re
from pathlib import Path

MIGRATION = Path("data/migrations/versions/0050_rf_survey.py")
MODEL = Path("shared/models/rf_survey.py")


def test_migration_chains_from_the_current_head():
    src = MIGRATION.read_text(encoding="utf-8")
    assert re.search(r'^revision\s*=\s*"0050"', src, re.M)
    assert re.search(r'^down_revision\s*=\s*"0049"', src, re.M)


def test_both_tables_are_created():
    src = MIGRATION.read_text(encoding="utf-8")
    assert "rf_access_points" in src
    assert "rf_fingerprints" in src


def test_an_access_point_is_unique_per_site():
    """The same BSSID can legitimately appear at two sites; within one site a
    duplicate would give the trilateration two contradictory positions."""
    src = MIGRATION.read_text(encoding="utf-8")
    assert "UniqueConstraint" in src or "unique_constraint" in src
    assert "ap_id" in src


def test_a_fingerprint_records_when_it_was_surveyed():
    """A radio map is a photograph of a building's RF environment. Move a
    shelving run and it is wrong in ways that produce confident bad fixes."""
    src = MIGRATION.read_text(encoding="utf-8")
    assert "surveyed_at" in src


def test_a_fingerprint_records_how_many_aps_it_saw():
    """A survey point that heard one AP cannot constrain a position. Storing the
    count makes a thin fingerprint filterable instead of silently weightless."""
    src = MIGRATION.read_text(encoding="utf-8")
    assert "ap_count" in src


def test_the_models_exist_and_are_registered():
    src = MODEL.read_text(encoding="utf-8")
    assert "class RfAccessPoint" in src
    assert "class RfFingerprint" in src
    init = Path("shared/models/__init__.py").read_text(encoding="utf-8")
    assert "rf_survey" in init


# ── loading and filtering ────────────────────────────────────────────────

import pytest

from services.core.rf_mapping import store as S


def test_thin_fingerprints_are_excluded_from_the_map():
    """A survey point that heard fewer than three APs cannot constrain a
    position, and including it drags the weighted average toward wherever it
    happened to be. Filtered, not down-weighted."""
    rows = [
        {"x": 1.0, "y": 2.0, "rssi": {"a": -50, "b": -60, "c": -70}, "ap_count": 3},
        {"x": 9.0, "y": 9.0, "rssi": {"a": -80}, "ap_count": 1},
    ]
    fps = S.rows_to_fingerprints(rows, min_aps=3)
    assert len(fps) == 1
    assert (fps[0].x, fps[0].y) == (1.0, 2.0)


def test_a_fingerprint_keeps_every_ap_it_heard():
    rows = [{"x": 0.0, "y": 0.0, "rssi": {"a": -50, "b": -60, "c": -70},
             "ap_count": 3}]
    fp = S.rows_to_fingerprints(rows)[0]
    assert set(fp.rssi) == {"a", "b", "c"}


def test_an_empty_map_is_returned_rather_than_none():
    """locate() takes a RadioMap and checks its length. Returning None here
    would move the emptiness check into every caller."""
    assert len(S.rows_to_fingerprints([])) == 0


def test_staleness_is_reported_in_days():
    from datetime import datetime, timedelta, timezone
    old = datetime.now(timezone.utc) - timedelta(days=200)
    assert S.age_days(old) == pytest.approx(200, abs=1)
    assert S.age_days(None) is None


def test_a_map_older_than_the_threshold_is_flagged_stale():
    """Not blocked — flagged. A stale map still beats trilateration; the
    operator needs to know it is aging, not be locked out of positioning."""
    from datetime import datetime, timedelta, timezone
    old = datetime.now(timezone.utc) - timedelta(days=S.STALE_AFTER_DAYS + 10)
    fresh = datetime.now(timezone.utc) - timedelta(days=5)
    assert S.is_stale(old) is True
    assert S.is_stale(fresh) is False
    assert S.is_stale(None) is True          # never surveyed counts as stale


def test_the_cache_is_keyed_by_site():
    """The pharmacy and the depot have different floor plans and different APs.
    Sharing a slot would hand one site's radio map to the other."""
    S._CACHE.clear()
    from services.core.rf_mapping import RadioMap
    S._CACHE[("p1", "depot")] = (RadioMap([]), None)
    S._CACHE[("p1", "pharmacy")] = (RadioMap([]), None)
    assert len(S._CACHE) == 2
    assert S.invalidate("p1", "depot") == 1
    assert ("p1", "pharmacy") in S._CACHE


def test_invalidating_a_pharmacy_clears_every_site():
    S._CACHE.clear()
    from services.core.rf_mapping import RadioMap
    S._CACHE[("p1", "depot")] = (RadioMap([]), None)
    S._CACHE[("p1", "pharmacy")] = (RadioMap([]), None)
    assert S.invalidate("p1") == 2
    assert S._CACHE == {}


# ── the whole point of the phase, measured ───────────────────────────────

import math

from services.core.rf_mapping import (AccessPoint, RssiSample, locate,
                                      trilaterate)

APS = {a.ap_id: a for a in [
    AccessPoint("ap-nw", 0.0, 0.0, -40.0),
    AccessPoint("ap-ne", 20.0, 0.0, -40.0),
    AccessPoint("ap-sw", 0.0, 15.0, -40.0),
]}


def _rssi(ap, x, y, n=2.5, attenuate=0.0):
    d = max(1.0, math.hypot(x - ap.x, y - ap.y))
    return ap.tx_power_dbm - 10.0 * n * math.log10(d) - attenuate


def _survey_rows(attenuated_ap="ap-sw", loss_db=12.0):
    """A 5m grid, with one AP attenuated by shelving — the physical distortion
    a free-space model cannot know about and a survey captures for free."""
    rows = []
    for gx in range(0, 21, 5):
        for gy in range(0, 16, 5):
            rssi = {ap.ap_id: _rssi(ap, gx, gy,
                                    attenuate=loss_db if ap.ap_id == attenuated_ap else 0.0)
                    for ap in APS.values()}
            rows.append({"x": float(gx), "y": float(gy), "rssi": rssi,
                         "ap_count": len(rssi)})
    return rows


def test_fingerprinting_beats_trilateration_through_the_store():
    """The justification for this whole phase, measured end to end rather than
    asserted. Shelving attenuates one AP; trilateration's free-space assumption
    cannot know that, and the survey captured it without being told."""
    from services.core.rf_mapping import RadioMap

    tx, ty = 10.0, 5.0
    probe = [RssiSample(ap.ap_id,
                        _rssi(ap, tx, ty, attenuate=12.0 if ap.ap_id == "ap-sw" else 0.0))
             for ap in APS.values()]

    rmap = RadioMap(S.rows_to_fingerprints(_survey_rows()))
    assert len(rmap) > 0, "the survey grid produced no usable fingerprints"

    fp_fix = locate(probe, APS, rmap)
    assert fp_fix.method == "fingerprint"
    fp_err = math.hypot(fp_fix.x - tx, fp_fix.y - ty)
    assert fp_err < 3.0, f"fingerprint was {fp_err:.2f}m off a 5m survey grid"

    # Measured, not assumed: under 12 dB of shelving attenuation trilateration
    # does not merely do worse here, it diverges to y = -49 in a 15m-deep room
    # and the bounds check refuses the result. Accept either, but never a
    # trilateration fix that beats the survey.
    tri_fix = trilaterate(probe, APS)
    if tri_fix is not None:
        tri_err = math.hypot(tri_fix.x - tx, tri_fix.y - ty)
        assert fp_err < tri_err, (
            f"fingerprint {fp_err:.2f}m vs trilateration {tri_err:.2f}m")


def test_with_no_survey_the_system_still_positions_by_trilateration():
    """Degradation, not failure. A depot with APs but no survey yet must still
    get a fix — with the larger uncertainty that method honestly carries."""
    from services.core.rf_mapping import RadioMap

    probe = [RssiSample(ap.ap_id, _rssi(ap, 8.0, 6.0)) for ap in APS.values()]
    fix = locate(probe, APS, RadioMap([]))
    assert fix is not None
    assert fix.method == "trilateration"
    assert fix.uncertainty_m >= 5.0     # the honest indoor floor


def test_a_trilateration_fix_outside_the_building_is_refused():
    """Measured on this layout: with one AP attenuated 12 dB by shelving the
    linear system puts the device at y = -49 in a room 15m deep, and reports an
    uncertainty that understates the error by 1-3x. A coordinate that confident
    and that wrong is worse than no coordinate — it reaches the observation log
    and the heatmap indistinguishable from a good one."""
    from services.core.rf_mapping import ap_bounds

    probe = [RssiSample(ap.ap_id,
                        _rssi(ap, 10.0, 5.0,
                               attenuate=12.0 if ap.ap_id == "ap-sw" else 0.0))
             for ap in APS.values()]
    assert trilaterate(probe, APS) is None

    box = ap_bounds(APS)
    assert box[1] < 0.0 < box[3], "the margin must still admit the room itself"


def test_a_plausible_but_inaccurate_fix_is_still_returned():
    """The bounds check rejects divergence, not inaccuracy. Trilateration is
    honestly 5-15m indoors and must be allowed to be that bad."""
    probe = [RssiSample(ap.ap_id, _rssi(ap, 8.0, 6.0)) for ap in APS.values()]
    fix = trilaterate(probe, APS)
    assert fix is not None and fix.method == "trilateration"


def test_bounds_need_two_access_points_to_mean_anything():
    """One AP has no extent, so there is no plausible region to test against and
    the check must not invent one."""
    from services.core.rf_mapping import ap_bounds
    assert ap_bounds({"a": APS["ap-nw"]}) is None
    assert ap_bounds({}) is None
