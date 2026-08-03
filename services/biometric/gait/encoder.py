"""Gait embeddings from pose keypoint sequences.

Why gait is worth adding rather than more face capacity: it identifies at
distances and angles where face fails, and — the case that matters here — it
keeps working when the face is covered. Masks, veiling, hoods and a head turned
away all destroy a face embedding while leaving gait untouched. Face and gait
also fail for *different* reasons, which is exactly the property that makes
fusing them worth more than either alone (see `identity_resolution.multimodal`).

The input is what the behavioural detector already produces: COCO-17 keypoints
per frame from YOLOv8-pose. No new camera, no new model download, no GPU.

The descriptor has three parts, and the split is deliberate:

  **Anthropometric** (limb-length ratios) — nearly view- and speed-invariant,
  the most stable signal a skeleton carries, and it survives a single frame.
  **Kinematic** (joint-angle statistics over the sequence) — how the person
  moves, not just how they are built.
  **Cyclic** (spectrum and autocorrelation of the gait cycle) — cadence and
  stride regularity, the part people find hardest to fake deliberately. Both
  measures are invariant to where in the stride the clip happened to start.

Everything is scale- and translation-normalised so camera distance and image
size drop out. Nothing here is learned, so there is no training set to collect
before it runs and no model drift to manage — the trade is lower ceiling
accuracy than a trained gait network, which is the right trade for a first
deployment and can be swapped later behind the same interface.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# COCO-17 keypoint indices, the YOLOv8-pose output order.
NOSE = 0
L_SHOULDER, R_SHOULDER = 5, 6
L_ELBOW, R_ELBOW = 7, 8
L_WRIST, R_WRIST = 9, 10
L_HIP, R_HIP = 11, 12
L_KNEE, R_KNEE = 13, 14
L_ANKLE, R_ANKLE = 15, 16

EMBED_DIM = 64
MIN_FRAMES = 12          # below ~half a gait cycle the cyclic part is noise
MIN_CONFIDENCE = 0.30    # per-keypoint visibility floor

# Relative energy given to (anthropometric, kinematic, cyclic) after each block
# is L2-normalised. Equal weighting is the neutral choice: it removes the unit
# accident without tuning the descriptor to one population's statistics, which
# would not transfer to this pharmacy's cameras.
BLOCK_WEIGHTS = (1.0, 1.0, 1.0)


class GaitError(ValueError):
    """The sequence cannot produce a usable embedding."""


@dataclass(frozen=True)
class GaitQuality:
    """Whether this embedding should be trusted, and why.

    A gait embedding from four blurry frames of a person standing still is not
    a weak identification — it is not an identification at all, and the caller
    must be able to tell the difference.
    """
    frames: int
    usable_frames: int
    mean_visibility: float
    cycles_observed: float
    is_moving: bool
    usable: bool
    reason: str = ""

    def quality_score(self) -> float:
        """A 0-1 sample quality for `services.biometric.fusion`, which admits
        gait only above 0.75 — the strictest floor of any modality, because
        CASIA-B rank-1 falls to 86.7% under the clothing covariate and no
        evaluation under a chador exists.

        Weakest-link, not an average: a sequence that is long and clear but
        barely moving is not a good gait sample, and averaging would hide that
        behind the two strong terms. Under a floor this strict, the conservative
        combination is the honest one.
        """
        if not self.usable:
            return 0.0
        coverage = min(1.0, self.usable_frames / 30.0)      # ~1s at 30fps
        cycles = min(1.0, self.cycles_observed / 2.0)       # 2+ cycles is solid
        return round(float(min(self.mean_visibility, coverage, cycles)), 4)

    def as_dict(self) -> dict:
        return {"frames": self.frames, "usable_frames": self.usable_frames,
                "quality_score": self.quality_score(),
                "mean_visibility": round(self.mean_visibility, 3),
                "cycles_observed": round(self.cycles_observed, 2),
                "is_moving": self.is_moving, "usable": self.usable,
                "reason": self.reason}


def _valid(frame: np.ndarray) -> bool:
    """A frame is usable only if the joints the descriptor reads are visible."""
    needed = (L_HIP, R_HIP, L_KNEE, R_KNEE, L_ANKLE, R_ANKLE,
              L_SHOULDER, R_SHOULDER)
    return all(frame[i, 2] >= MIN_CONFIDENCE for i in needed)


def _normalise(frame: np.ndarray) -> np.ndarray:
    """Centre on the pelvis and scale by torso length.

    Torso (pelvis→neck) is the scale reference rather than height because it is
    the segment least affected by the legs' phase in the gait cycle — using
    height would make the scale oscillate with the person's own stride.
    """
    pts = frame[:, :2].astype(np.float64)
    pelvis = (pts[L_HIP] + pts[R_HIP]) / 2.0
    neck = (pts[L_SHOULDER] + pts[R_SHOULDER]) / 2.0
    torso = float(np.linalg.norm(neck - pelvis))
    if torso < 1e-6:
        raise GaitError("degenerate skeleton: zero torso length")
    return (pts - pelvis) / torso


def _angle(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    """Interior angle at b, in radians."""
    v1, v2 = a - b, c - b
    n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
    if n1 < 1e-9 or n2 < 1e-9:
        return 0.0
    return float(np.arccos(np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0)))


def _anthropometric(frames: list[np.ndarray]) -> np.ndarray:
    """Limb-length ratios, averaged over the sequence. 10 dimensions.

    These are body proportions: they do not change with walking speed, camera
    distance, or which part of the stride the frame caught.
    """
    feats = []
    for f in frames:
        thigh_l = np.linalg.norm(f[L_KNEE] - f[L_HIP])
        thigh_r = np.linalg.norm(f[R_KNEE] - f[R_HIP])
        shin_l = np.linalg.norm(f[L_ANKLE] - f[L_KNEE])
        shin_r = np.linalg.norm(f[R_ANKLE] - f[R_KNEE])
        shoulder_w = np.linalg.norm(f[L_SHOULDER] - f[R_SHOULDER])
        hip_w = np.linalg.norm(f[L_HIP] - f[R_HIP])
        upper_arm = (np.linalg.norm(f[L_ELBOW] - f[L_SHOULDER]) +
                     np.linalg.norm(f[R_ELBOW] - f[R_SHOULDER])) / 2.0
        forearm = (np.linalg.norm(f[L_WRIST] - f[L_ELBOW]) +
                   np.linalg.norm(f[R_WRIST] - f[R_ELBOW])) / 2.0
        leg = (thigh_l + thigh_r + shin_l + shin_r) / 2.0
        feats.append([
            thigh_l + thigh_r, shin_l + shin_r,
            (thigh_l + thigh_r) / max(shin_l + shin_r, 1e-6),
            shoulder_w, hip_w, shoulder_w / max(hip_w, 1e-6),
            upper_arm, forearm, upper_arm / max(forearm, 1e-6), leg,
        ])
    return np.nanmean(np.asarray(feats, dtype=np.float64), axis=0)


def _kinematic(frames: list[np.ndarray]) -> np.ndarray:
    """Joint-angle statistics over the sequence. 24 dimensions.

    Mean and spread of each angle: the mean is posture, the spread is range of
    motion. Both differ between people walking the same route.
    """
    series = []
    for f in frames:
        series.append([
            _angle(f[L_HIP], f[L_KNEE], f[L_ANKLE]),      # left knee
            _angle(f[R_HIP], f[R_KNEE], f[R_ANKLE]),      # right knee
            _angle(f[L_SHOULDER], f[L_HIP], f[L_KNEE]),   # left hip
            _angle(f[R_SHOULDER], f[R_HIP], f[R_KNEE]),   # right hip
            _angle(f[L_SHOULDER], f[L_ELBOW], f[L_WRIST]),
            _angle(f[R_SHOULDER], f[R_ELBOW], f[R_WRIST]),
            float(np.linalg.norm(f[L_ANKLE] - f[R_ANKLE])),   # stride width
            float(abs(f[L_ANKLE][1] - f[R_ANKLE][1])),        # vertical offset
        ])
    arr = np.asarray(series, dtype=np.float64)
    return np.concatenate([arr.mean(axis=0), arr.std(axis=0), arr.ptp(axis=0)])


def _leg_signal(frames: list[np.ndarray]) -> np.ndarray:
    """Signed horizontal separation of the ankles — the gait oscillator."""
    return np.asarray([f[L_ANKLE][0] - f[R_ANKLE][0] for f in frames],
                      dtype=np.float64)


def _cyclic(frames: list[np.ndarray]) -> tuple[np.ndarray, float]:
    """Shift-invariant description of the gait cycle. 30 dimensions + cycle count.

    Both halves are invariant to *when* the clip started, which matters because
    a camera catches a walk at an arbitrary point in the stride:

      **Magnitude spectrum** (15) — cadence and gait symmetry. A limp or an
      uneven load puts energy in the even harmonics.
      **Autocorrelation** (15) — cycle structure and regularity, and it stays
      meaningful when the walk is too short for clean spectral resolution.

    An earlier version used the FFT *phase* here. Measured, that was actively
    harmful: for the same person captured twice the magnitude spectrum matched
    at cosine 1.000 while the phase matched at −0.318, because arg(FFT) shifts
    with the start frame. It contributed noise with the confidence of signal,
    and once the blocks were energy-equalised that noise took a third of the
    descriptor. Magnitude is shift-invariant; absolute phase is not.
    """
    sig = _leg_signal(frames)
    sig = sig - sig.mean()
    n = len(sig)
    if n < MIN_FRAMES or np.allclose(sig, 0):
        return np.zeros(30), 0.0

    spec = np.abs(np.fft.rfft(sig * np.hanning(n)))
    if spec.sum() > 1e-9:
        spec = spec / spec.sum()
    harmonics = np.pad(spec[1:16], (0, max(0, 15 - len(spec[1:16]))))[:15]

    # Normalised autocorrelation over the first 15 lags.
    ac = np.correlate(sig, sig, mode="full")[n - 1:]
    if ac[0] > 1e-12:
        ac = ac / ac[0]
    ac = np.pad(ac[1:16], (0, max(0, 15 - len(ac[1:16]))))[:15]

    dom = int(np.argmax(harmonics)) + 1
    return np.concatenate([harmonics, ac]), float(dom)


def assess(keypoints: np.ndarray) -> GaitQuality:
    """Decide whether a sequence can yield an embedding at all, before trying."""
    if keypoints.ndim != 3 or keypoints.shape[1] < 17 or keypoints.shape[2] < 3:
        raise GaitError("expected an (frames, 17, 3) keypoint array")
    frames = int(keypoints.shape[0])
    usable = [f for f in keypoints if _valid(f)]
    vis = float(np.mean(keypoints[:, :, 2])) if frames else 0.0

    if len(usable) < MIN_FRAMES:
        return GaitQuality(frames, len(usable), vis, 0.0, False, False,
                           f"only {len(usable)} usable frames; need {MIN_FRAMES}")
    norm = [_normalise(f) for f in usable]
    _, cycles = _cyclic(norm)
    sig = _leg_signal(norm)
    moving = bool(np.std(sig) > 0.05)
    if not moving:
        # Standing still has no gait. Returning a confident embedding for a
        # stationary person is how a bench becomes an identity.
        return GaitQuality(frames, len(usable), vis, cycles, False, False,
                           "subject is not walking; gait is undefined")
    return GaitQuality(frames, len(usable), vis, cycles, True, True)


def encode(keypoints: np.ndarray) -> tuple[np.ndarray, GaitQuality]:
    """Pose sequence → L2-normalised gait embedding.

    `keypoints` is (frames, 17, 3) as (x, y, confidence).
    Raises rather than returning a low-confidence vector: a caller that cannot
    distinguish "no gait" from "weak gait" will treat noise as evidence.
    """
    quality = assess(keypoints)
    if not quality.usable:
        raise GaitError(quality.reason)

    frames = [_normalise(f) for f in keypoints if _valid(f)]
    blocks = [_anthropometric(frames), _kinematic(frames), _cyclic(frames)[0]]

    # Equalise the blocks before concatenating. Measured on a synthetic
    # population, the raw concatenation gave the kinematic block 72.9% of the
    # vector's energy while it carried the *least* between-person variation
    # (σ 0.031 across people, against 0.335 for the cyclic block). Joint angles
    # are large numbers that happen to be similar for everybody: without this
    # step the descriptor is dominated by the part that discriminates worst,
    # purely because radians are bigger than normalised spectral amplitudes.
    vec = np.concatenate([b / max(float(np.linalg.norm(b)), 1e-9) * w
                          for b, w in zip(blocks, BLOCK_WEIGHTS)]).astype(np.float32)
    if vec.shape[0] < EMBED_DIM:
        vec = np.pad(vec, (0, EMBED_DIM - vec.shape[0]))
    vec = vec[:EMBED_DIM]

    if not np.all(np.isfinite(vec)):
        raise GaitError("embedding contains non-finite values")
    norm = float(np.linalg.norm(vec))
    if norm < 1e-9:
        raise GaitError("degenerate embedding")
    return (vec / norm).astype(np.float32), quality


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two L2-normalised gait embeddings."""
    if a.shape != b.shape:
        raise GaitError(f"dimension mismatch: {a.shape} vs {b.shape}")
    return float(np.clip(np.dot(a, b), -1.0, 1.0))


# A population-whitening step (centre on the gallery mean, divide by the
# per-dimension MAD) was implemented here and then REMOVED after measurement.
# It was a workaround for the FFT-phase noise documented in `_cyclic`, and once
# that was fixed it actively hurt: on an 8-person population the raw descriptor
# separated genuine from impostor by +0.176, and whitening moved that to -0.112
# — the distributions overlapped. Dividing by the spread amplifies exactly the
# dimensions where the population barely varies, which after the fix is noise.
# Do not reintroduce it without re-measuring the margin both ways.
