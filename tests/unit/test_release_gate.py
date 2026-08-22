"""The release gate: per demographic cell, and no averaging.

A mean hides the failure that matters. And the failure most likely to be hidden
is not the matcher's: differential performance usually enters through the
DETECTOR and the QUALITY GATE. If detection recall is lower on chador — dark
garment, low contrast, tight face aperture — those customers never reach the
matcher, per-cell matcher metrics look fine, and the SYSTEM discriminates while
every matcher number passes.
"""
from __future__ import annotations

import pytest

from services.biometric.release_gate import CellMetrics, evaluate_gate


def cell(name, captured=100, detected=100, quality_passed=100, identified=95):
    return CellMetrics(cell=name, captured=captured, detected=detected,
                       quality_passed=quality_passed, identified=identified)


# ── the rates ────────────────────────────────────────────────────────────

def test_rates_are_computed_end_to_end():
    c = cell("clear", captured=200, detected=180, quality_passed=150,
             identified=120)
    assert c.detection_rate == pytest.approx(0.90)
    assert c.quality_rate == pytest.approx(150 / 180)
    # the headline number is captured -> identified, NOT matcher-only
    assert c.identification_rate == pytest.approx(120 / 200)


def test_a_cell_that_captured_nothing_does_not_divide_by_zero():
    c = cell("empty", captured=0, detected=0, quality_passed=0, identified=0)
    assert c.identification_rate == 0.0
    assert c.detection_rate == 0.0


# ── the gate ─────────────────────────────────────────────────────────────

def test_uniformly_good_cells_pass():
    r = evaluate_gate([cell("clear"), cell("hijab"), cell("chador")])
    assert r.passed is True
    assert r.failures == []


def test_one_failing_cell_blocks_release_however_good_the_mean():
    """No averaging. Two excellent cells cannot carry a third."""
    cells = [cell("clear", identified=99), cell("hijab", identified=99),
             cell("chador", identified=40)]
    mean = sum(c.identification_rate for c in cells) / 3
    assert mean > 0.75                       # the average looks acceptable
    r = evaluate_gate(cells)
    assert r.passed is False
    assert r.worst_cell == "chador"


def test_the_detector_channel_is_gated_separately():
    """The failure this gate exists for. Everyone the detector FINDS is matched
    almost perfectly, so matcher-only metrics look fine — but half the chador
    captures never reach the matcher at all."""
    cells = [
        cell("clear", captured=100, detected=100, quality_passed=100, identified=98),
        cell("chador", captured=100, detected=50, quality_passed=50, identified=48),
    ]
    # matcher-on-what-it-sees is 48/50 = 96% for chador — indistinguishable
    # from clear if you only measure the matcher
    assert 48 / 50 > 0.95
    r = evaluate_gate(cells)
    assert r.passed is False
    assert any("detection" in f.lower() for f in r.failures), r.failures


def test_the_ratio_between_best_and_worst_cell_is_bounded():
    """Even when every cell clears the floor, a wide spread is a finding."""
    cells = [cell("clear", identified=99), cell("chador", identified=86)]
    r = evaluate_gate(cells, min_identification_rate=0.80, max_ratio=1.1)
    assert r.passed is False
    assert any("spread" in f.lower() or "ratio" in f.lower() for f in r.failures)


def test_a_cell_too_small_to_judge_is_a_failure_not_a_pass():
    """An empty or tiny cell is missing evidence. Treating it as a pass is how a
    group with no test data is declared safe."""
    cells = [cell("clear"), cell("chador", captured=3, detected=3,
                                 quality_passed=3, identified=3)]
    r = evaluate_gate(cells, min_cell_size=30)
    assert r.passed is False
    assert any("too few" in f.lower() or "sample" in f.lower()
               for f in r.failures), r.failures


def test_no_cells_at_all_fails_closed():
    r = evaluate_gate([])
    assert r.passed is False


def test_the_report_names_every_cell_and_its_numbers():
    r = evaluate_gate([cell("clear"), cell("hijab")])
    names = {row["cell"] for row in r.report}
    assert names == {"clear", "hijab"}
    for row in r.report:
        assert "identification_rate" in row and "detection_rate" in row
