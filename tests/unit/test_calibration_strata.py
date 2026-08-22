"""Calibration is per occlusion stratum, not per modality.

A single pooled ImpostorStats averages veiled faces in with unoccluded ones and
returns a threshold that is too LOW for the veiled subset — so the FPIR
guarantee silently fails for exactly the group it most affects. ImpostorStats
carries a `population` field for this and nothing has ever populated it.
"""
from __future__ import annotations

import re
from pathlib import Path

MIGRATION = Path("data/migrations/versions/0048_calibration_strata.py")


def test_migration_exists_and_chains_from_the_current_head():
    src = MIGRATION.read_text(encoding="utf-8")
    assert re.search(r'^revision\s*=\s*"0048"', src, re.M)
    assert re.search(r'^down_revision\s*=\s*"0047"', src, re.M)


def test_stratum_is_added_to_the_score_stats_table():
    src = MIGRATION.read_text(encoding="utf-8")
    assert "biometric_score_stats" in src
    assert "stratum" in src


def test_the_unique_key_includes_the_stratum():
    """Without this a second stratum's calibration overwrites the first, and the
    table silently holds one row where it should hold four."""
    src = MIGRATION.read_text(encoding="utf-8")
    body = src[src.index("def upgrade"):src.index("def downgrade")]
    assert "stratum" in body
    # the old three-column key must be dropped, not merely supplemented
    assert "drop_constraint" in body or "DROP CONSTRAINT" in body


def test_templates_gain_a_shadow_flag():
    """A shadow model must be enrollable and measurable without voting."""
    src = MIGRATION.read_text(encoding="utf-8")
    assert "biometric_templates" in src
    assert "shadow" in src


def test_downgrade_is_written():
    src = MIGRATION.read_text(encoding="utf-8")
    body = src[src.index("def downgrade"):]
    assert "stratum" in body and "shadow" in body


# ── stratified measurement ───────────────────────────────────────────────

import numpy as np
import pytest

from services.biometric.identity_resolution import vector_store as V
from services.biometric.identity_resolution.strata import (
    POOLED, measure_all_strata, split_by_stratum, to_impostor_stats)


def _vec(seed, dim=512):
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def _index(rows):
    """rows: [(identity_id, stratum, vector)]"""
    idx = V.ModalityIndex(modality="face", dim=512)
    idx.load([{"identity_id": i, "template_id": f"t{n}", "quality": 0.9,
               "embedding": v, "capture_context": {"occlusion_stratum": s}}
              for n, (i, s, v) in enumerate(rows)])
    return idx


def test_a_gallery_splits_into_its_strata():
    idx = _index([("a", "clear", _vec(1)), ("b", "clear", _vec(2)),
                  ("c", "mask", _vec(3))])
    parts = split_by_stratum(idx)
    assert set(parts) >= {"clear", "mask"}
    assert len(parts["clear"]) == 2
    assert len(parts["mask"]) == 1


def test_templates_with_no_recorded_stratum_go_to_the_pooled_bucket():
    """Everything enrolled before phase 1 has an empty capture_context. It is
    still usable as the pooled calibration; it is just not stratified."""
    idx = V.ModalityIndex(modality="face", dim=512)
    idx.load([{"identity_id": "a", "template_id": "t0", "quality": 0.9,
               "embedding": _vec(1), "capture_context": {}}])
    parts = split_by_stratum(idx)
    assert POOLED in parts and len(parts[POOLED]) == 1


def test_a_stratum_with_too_few_pairs_is_not_usable():
    """The honest failure. A thin stratum must not borrow the pooled figure —
    that is precisely how a threshold too low for veiled faces gets applied."""
    rows = [(f"id{i}", "clear", _vec(i)) for i in range(30)]
    rows += [("x", "chador", _vec(100)), ("y", "chador", _vec(101))]
    cals = measure_all_strata(_index(rows), min_pairs=50)
    assert cals["chador"].usable is False
    assert "pair" in cals["chador"].reason.lower()


def test_each_stratum_is_measured_independently():
    rows = [(f"c{i}", "clear", _vec(i)) for i in range(20)]
    rows += [(f"m{i}", "mask", _vec(200 + i)) for i in range(20)]
    cals = measure_all_strata(_index(rows), min_pairs=10)
    assert set(cals) >= {"clear", "mask"}
    assert cals["clear"].modality == "face"
    assert cals["clear"].pairs > 0 and cals["mask"].pairs > 0


def test_the_stratum_travels_into_the_impostor_stats_population():
    """ImpostorStats.population has existed unused since phase 1. It is what
    tells a reviewer which subset a threshold was derived from.

    50 identities gives 1225 cross-identity pairs — ImpostorStats enforces its
    own floor of 1000, because the tail is what the threshold is derived from
    and 200 pairs cannot estimate it."""
    rows = [(f"c{i}", "clear", _vec(i)) for i in range(50)]
    cal = measure_all_strata(_index(rows), min_pairs=100)["clear"]
    assert cal.pairs >= 1000, cal.as_dict()
    stats = to_impostor_stats(cal, stratum="clear", sample_floor=100)
    assert stats is not None
    assert stats.population == "clear"


def test_a_local_sample_floor_cannot_relax_the_impostor_stats_floor():
    """A caller passing a permissive sample_floor must not be able to talk the
    system into a threshold derived from a tail nobody could measure.
    ImpostorStats' own 1000-pair floor is authoritative."""
    rows = [(f"c{i}", "clear", _vec(i)) for i in range(20)]   # 190 pairs
    cal = measure_all_strata(_index(rows), min_pairs=10)["clear"]
    assert cal.usable is True and cal.pairs < 1000
    # permissive local floor, still refused
    assert to_impostor_stats(cal, stratum="clear", sample_floor=1) is None


def test_an_unusable_calibration_yields_no_stats_at_all():
    """None is the correct return: fusion excludes an uncalibrated stream, and
    a placeholder here would licence a vote that was never measured."""
    from services.biometric.identity_resolution.repository import Calibration

    bad = Calibration("face", 0.0, 0.0, 0, None, None, 0, False, "too few pairs")
    assert to_impostor_stats(bad, stratum="mask") is None
