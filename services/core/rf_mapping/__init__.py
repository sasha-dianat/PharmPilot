"""Wi-Fi RSSI indoor positioning for the depot.

**Scope, stated first because it constrains everything else.** RSSI positioning
locates a *device*, not a person. It is appropriate for staff carrying an issued
handset or badge tag in the depot — movement, occupancy density, zone dwell —
and it is not an identification mechanism. Nothing here feeds the biometric
fusion engine, and a device fix must never be presented as "who" was somewhere.
Customer phones are out of scope: modern handsets randomise MAC addresses per
network, so the data would be both unreliable and collected without any basis.

**Why two algorithms.** Trilateration from a log-distance path-loss model is the
textbook method and is honestly poor indoors — multipath, body attenuation and
steel shelving push typical error to 5-15 m. In a depot a few aisles wide that
is close to useless for anything but "which end of the building". Fingerprinting
against a surveyed radio map does substantially better (roughly 2-5 m) because
it *learns* the multipath rather than assuming it away. Fingerprinting is
therefore the default; trilateration is the fallback when no survey exists for a
zone, and it reports a correspondingly large uncertainty.

Every fix carries `uncertainty_m`. A coordinate without one invites false
precision on a heatmap, and false precision is how a positioning system ends up
being cited as evidence it cannot support.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

# Below this an association is noise rather than a measurement.
DEFAULT_NOISE_FLOOR_DBM = -92.0
# Free space is ~2.0; a warehouse with racking is meaningfully worse.
DEFAULT_PATH_LOSS_EXPONENT = 2.5
# Trilateration's honest floor, from the multipath literature.
TRILATERATION_FLOOR_M = 5.0


@dataclass(frozen=True)
class AccessPoint:
    ap_id: str
    x: float
    y: float
    tx_power_dbm: float = -40.0        # calibrated RSSI at 1 m


@dataclass(frozen=True)
class RssiSample:
    ap_id: str
    rssi_dbm: float


@dataclass
class Fingerprint:
    """One surveyed point in the radio map."""
    x: float
    y: float
    rssi: dict[str, float]


@dataclass
class RadioMap:
    fingerprints: list[Fingerprint] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.fingerprints)


@dataclass(frozen=True)
class PositionFix:
    x: float
    y: float
    uncertainty_m: float
    method: str                        # fingerprint | trilateration
    ap_count: int = 0


# ── signal conditioning ──────────────────────────────────────────────────

def filter_samples(samples: list[RssiSample],
                   noise_floor_dbm: float = DEFAULT_NOISE_FLOOR_DBM,
                   ) -> list[RssiSample]:
    """Drop sub-floor associations, reject outliers, average per AP.

    A single frame is not a measurement: RSSI varies several dB frame to frame,
    and one reflected frame can sit 20 dB off true. Outliers are rejected by
    median absolute deviation before averaging, because a plain mean is exactly
    what a reflection corrupts.
    """
    by_ap: dict[str, list[float]] = {}
    for s in samples:
        if s.rssi_dbm >= noise_floor_dbm:
            by_ap.setdefault(s.ap_id, []).append(s.rssi_dbm)

    out: list[RssiSample] = []
    for ap_id, values in by_ap.items():
        if len(values) >= 3:
            med = statistics.median(values)
            mad = statistics.median([abs(v - med) for v in values]) or 1.0
            values = [v for v in values if abs(v - med) <= 3.0 * mad] or [med]
        out.append(RssiSample(ap_id, statistics.fmean(values)))
    return out


def rssi_to_distance(rssi_dbm: float, tx_power_dbm: float = -40.0,
                     path_loss_exponent: float = DEFAULT_PATH_LOSS_EXPONENT,
                     ) -> float:
    """Log-distance path loss: d = 10 ** ((P_tx - RSSI) / (10 n)).

    Accurate in free space and systematically wrong indoors, which is the whole
    reason fingerprinting exists.
    """
    return 10.0 ** ((tx_power_dbm - rssi_dbm) / (10.0 * path_loss_exponent))


# ── trilateration ────────────────────────────────────────────────────────

def trilaterate(samples: list[RssiSample], aps: dict[str, AccessPoint],
                path_loss_exponent: float = DEFAULT_PATH_LOSS_EXPONENT,
                ) -> PositionFix | None:
    """Least-squares multilateration from >= 3 access points.

    Linearised by subtracting the reference AP's circle equation from the rest,
    which turns intersecting circles into a linear system solvable in closed
    form — no iteration, no dependency beyond the standard library.
    """
    clean = [s for s in filter_samples(samples) if s.ap_id in aps]
    if len(clean) < 3:
        return None

    pts = [(aps[s.ap_id], rssi_to_distance(
        s.rssi_dbm, aps[s.ap_id].tx_power_dbm, path_loss_exponent))
        for s in clean]

    (ap0, d0) = pts[0]
    rows, rhs = [], []
    for ap, d in pts[1:]:
        rows.append([2.0 * (ap.x - ap0.x), 2.0 * (ap.y - ap0.y)])
        rhs.append(d0 ** 2 - d ** 2 + ap.x ** 2 - ap0.x ** 2
                   + ap.y ** 2 - ap0.y ** 2)

    # Normal equations for the 2x2 least-squares solution.
    a = sum(r[0] * r[0] for r in rows)
    b = sum(r[0] * r[1] for r in rows)
    c = sum(r[1] * r[1] for r in rows)
    d_ = sum(r[0] * v for r, v in zip(rows, rhs))
    e_ = sum(r[1] * v for r, v in zip(rows, rhs))

    det = a * c - b * b
    if abs(det) < 1e-9:            # collinear APs — geometry is degenerate
        return None
    x = (c * d_ - b * e_) / det
    y = (a * e_ - b * d_) / det

    # Uncertainty from residual disagreement between the circles, floored at the
    # method's realistic indoor limit so a heatmap cannot imply better.
    residuals = [abs(math.hypot(x - ap.x, y - ap.y) - d) for ap, d in pts]
    uncertainty = max(TRILATERATION_FLOOR_M, statistics.fmean(residuals))
    return PositionFix(x=x, y=y, uncertainty_m=uncertainty,
                       method="trilateration", ap_count=len(pts))


# ── fingerprinting ───────────────────────────────────────────────────────

def fingerprint_locate(samples: list[RssiSample], radio_map: RadioMap,
                       k: int = 3) -> PositionFix | None:
    """Weighted k-nearest-neighbour over a surveyed radio map.

    Distance is Euclidean in RSSI space over the APs the probe and the
    fingerprint share; a fingerprint sharing no AP with the probe is skipped
    rather than treated as infinitely far, which would silently bias the result
    toward whichever survey points happen to be dense.
    """
    if len(radio_map) == 0:
        return None
    clean = {s.ap_id: s.rssi_dbm for s in filter_samples(samples)}
    if not clean:
        return None

    scored: list[tuple[float, Fingerprint]] = []
    for fp in radio_map.fingerprints:
        shared = [ap for ap in clean if ap in fp.rssi]
        if not shared:
            continue
        dist = math.sqrt(sum((clean[ap] - fp.rssi[ap]) ** 2 for ap in shared)
                         / len(shared))
        scored.append((dist, fp))
    if not scored:
        return None

    scored.sort(key=lambda t: t[0])
    top = scored[: max(1, k)]

    # Inverse-distance weighting; an exact match dominates rather than dividing
    # by zero.
    weights = [1.0 / max(d, 1e-6) for d, _ in top]
    total = sum(weights)
    x = sum(w * fp.x for w, (_, fp) in zip(weights, top)) / total
    y = sum(w * fp.y for w, (_, fp) in zip(weights, top)) / total

    # Spread of the contributing survey points is the natural uncertainty: if
    # the k nearest fingerprints are far apart, the fix is genuinely vague.
    spread = [math.hypot(fp.x - x, fp.y - y) for _, fp in top]
    uncertainty = max(0.5, statistics.fmean(spread)) if len(top) > 1 else 0.5
    return PositionFix(x=x, y=y, uncertainty_m=uncertainty,
                       method="fingerprint", ap_count=len(clean))


def locate(samples: list[RssiSample], aps: dict[str, AccessPoint],
           radio_map: RadioMap | None = None, k: int = 3) -> PositionFix | None:
    """Fingerprint where a survey exists, trilaterate where it does not."""
    if radio_map is not None and len(radio_map) > 0:
        fix = fingerprint_locate(samples, radio_map, k=k)
        if fix is not None:
            return fix
    return trilaterate(samples, aps)


# ── occupancy ────────────────────────────────────────────────────────────

def occupancy_heatmap(points: list[tuple[float, float]], width_m: float,
                      height_m: float, cell_m: float = 2.0) -> list[list[int]]:
    """Count fixes per grid cell. Row 0 is y=0; column 0 is x=0.

    Deliberately a raw count rather than a smoothed density: a count is
    explainable to a reviewer ("four fixes fell in this cell"), and a smoothed
    surface invites reading structure into positioning noise.
    """
    cols = max(1, int(math.ceil(width_m / cell_m)))
    rows = max(1, int(math.ceil(height_m / cell_m)))
    grid = [[0] * cols for _ in range(rows)]
    for x, y in points:
        if not (0.0 <= x < width_m and 0.0 <= y < height_m):
            continue
        grid[int(y // cell_m)][int(x // cell_m)] += 1
    return grid
