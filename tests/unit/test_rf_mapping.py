"""Wi-Fi RSSI indoor positioning for the depot.

What this can and cannot do, stated up front because it shapes every test:

RSSI positioning locates a **device**, not a person. It is therefore appropriate
for staff carrying an issued handset or badge tag in the depot — which is what
it is for — and is not an identification mechanism. Nothing here feeds the
biometric fusion engine.

Trilateration from a log-distance path-loss model is the textbook approach and
is genuinely poor indoors: multipath, body attenuation and shelving push typical
error to 5-15 m, which in a depot the size of a few aisles is close to useless.
Fingerprinting against a surveyed radio map does substantially better (2-5 m)
because it learns the multipath rather than assuming it away. Both are
implemented; the tests pin the properties that make fingerprinting the default.
"""
from __future__ import annotations

import math

import pytest

from services.core.rf_mapping import (
    AccessPoint, RssiSample, Fingerprint, RadioMap,
    filter_samples, trilaterate, fingerprint_locate, occupancy_heatmap,
    rssi_to_distance)


APS = [
    AccessPoint(ap_id="ap-nw", x=0.0,  y=0.0,  tx_power_dbm=-40.0),
    AccessPoint(ap_id="ap-ne", x=20.0, y=0.0,  tx_power_dbm=-40.0),
    AccessPoint(ap_id="ap-sw", x=0.0,  y=15.0, tx_power_dbm=-40.0),
]
AP_BY_ID = {a.ap_id: a for a in APS}


def synth(ap: AccessPoint, x: float, y: float, n: float = 2.5) -> float:
    """The RSSI an ideal log-distance model would produce at (x, y)."""
    d = max(1.0, math.hypot(x - ap.x, y - ap.y))
    return ap.tx_power_dbm - 10.0 * n * math.log10(d)


# ── signal conditioning ──────────────────────────────────────────────────

def test_filter_drops_samples_below_the_noise_floor():
    samples = [RssiSample("ap-nw", -55.0), RssiSample("ap-ne", -98.0)]
    kept = filter_samples(samples, noise_floor_dbm=-92.0)
    assert [s.ap_id for s in kept] == ["ap-nw"]


def test_filter_averages_repeats_from_the_same_ap():
    """RSSI is noisy frame to frame; a single reading is not a measurement."""
    samples = [RssiSample("ap-nw", -60.0), RssiSample("ap-nw", -64.0),
               RssiSample("ap-nw", -62.0)]
    kept = filter_samples(samples)
    assert len(kept) == 1
    assert kept[0].rssi_dbm == pytest.approx(-62.0, abs=0.5)


def test_filter_rejects_an_outlier_before_averaging():
    """A single reflected frame can sit 20 dB off and drags a naive mean."""
    samples = [RssiSample("ap-nw", -60.0), RssiSample("ap-nw", -61.0),
               RssiSample("ap-nw", -59.0), RssiSample("ap-nw", -20.0)]
    kept = filter_samples(samples)
    assert kept[0].rssi_dbm < -55.0, "outlier was not rejected"


def test_distance_grows_as_signal_weakens():
    near = rssi_to_distance(-45.0, tx_power_dbm=-40.0)
    far = rssi_to_distance(-75.0, tx_power_dbm=-40.0)
    assert far > near > 0


# ── trilateration ────────────────────────────────────────────────────────

def test_trilateration_recovers_a_known_point_from_clean_signals():
    true_x, true_y = 8.0, 6.0
    samples = [RssiSample(ap.ap_id, synth(ap, true_x, true_y)) for ap in APS]
    fix = trilaterate(samples, AP_BY_ID, path_loss_exponent=2.5)
    assert fix is not None
    assert fix.x == pytest.approx(true_x, abs=1.5)
    assert fix.y == pytest.approx(true_y, abs=1.5)


def test_trilateration_needs_three_access_points():
    samples = [RssiSample("ap-nw", -60.0), RssiSample("ap-ne", -65.0)]
    assert trilaterate(samples, AP_BY_ID) is None


def test_trilateration_reports_its_own_uncertainty():
    """A fix without an error estimate invites false precision on a heatmap."""
    samples = [RssiSample(ap.ap_id, synth(ap, 8.0, 6.0)) for ap in APS]
    fix = trilaterate(samples, AP_BY_ID, path_loss_exponent=2.5)
    assert fix.uncertainty_m > 0
    assert fix.method == "trilateration"


# ── fingerprinting ───────────────────────────────────────────────────────

def _radio_map() -> RadioMap:
    """A coarse survey: one fingerprint per 5 m grid node."""
    prints = []
    for gx in range(0, 21, 5):
        for gy in range(0, 16, 5):
            prints.append(Fingerprint(
                x=float(gx), y=float(gy),
                rssi={ap.ap_id: synth(ap, gx, gy) for ap in APS}))
    return RadioMap(prints)


def test_fingerprinting_locates_a_surveyed_point_exactly():
    rmap = _radio_map()
    probe = [RssiSample(ap.ap_id, synth(ap, 10.0, 5.0)) for ap in APS]
    fix = fingerprint_locate(probe, rmap, k=1)
    assert (fix.x, fix.y) == (10.0, 5.0)
    assert fix.method == "fingerprint"


def test_fingerprinting_interpolates_between_survey_points():
    rmap = _radio_map()
    probe = [RssiSample(ap.ap_id, synth(ap, 7.5, 5.0)) for ap in APS]
    fix = fingerprint_locate(probe, rmap, k=4)
    assert 5.0 <= fix.x <= 10.0
    assert fix.uncertainty_m > 0


def test_fingerprinting_beats_trilateration_on_the_same_signals():
    """The reason fingerprinting is the default: it learns the environment
    instead of assuming a free-space path loss that a depot does not have."""
    rmap = _radio_map()
    tx, ty = 10.0, 5.0
    # a realistic environment: shelving attenuates one AP by 12 dB
    probe = [RssiSample(ap.ap_id,
                        synth(ap, tx, ty) - (12.0 if ap.ap_id == "ap-sw" else 0.0))
             for ap in APS]
    # the same distortion is present in the survey, because it is physical
    for fp in rmap.fingerprints:
        fp.rssi["ap-sw"] -= 12.0

    fp_fix = fingerprint_locate(probe, rmap, k=3)
    tri_fix = trilaterate(probe, AP_BY_ID, path_loss_exponent=2.5)
    fp_err = math.hypot(fp_fix.x - tx, fp_fix.y - ty)
    tri_err = math.hypot(tri_fix.x - tx, tri_fix.y - ty)
    assert fp_err < tri_err


def test_empty_radio_map_returns_no_fix():
    assert fingerprint_locate([RssiSample("ap-nw", -60.0)], RadioMap([]), k=3) is None


# ── heatmap ──────────────────────────────────────────────────────────────

def test_heatmap_counts_density_per_cell():
    points = [(1.0, 1.0), (1.5, 1.2), (18.0, 14.0)]
    grid = occupancy_heatmap(points, width_m=20.0, height_m=15.0, cell_m=5.0)
    assert grid[0][0] == 2
    assert grid[2][3] == 1
    assert sum(sum(row) for row in grid) == 3


def test_heatmap_ignores_points_outside_the_floor_plan():
    grid = occupancy_heatmap([(-5.0, 2.0), (99.0, 2.0), (2.0, 2.0)],
                             width_m=20.0, height_m=15.0, cell_m=5.0)
    assert sum(sum(row) for row in grid) == 1


def test_heatmap_dimensions_follow_the_floor_plan():
    grid = occupancy_heatmap([], width_m=20.0, height_m=15.0, cell_m=5.0)
    assert len(grid) == 3           # 15 / 5 rows
    assert len(grid[0]) == 4        # 20 / 5 cols
