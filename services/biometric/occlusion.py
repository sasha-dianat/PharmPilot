"""Occlusion stratum from landmark visibility, and what survives it.

Why this is not a trained classifier: the face pipeline already fits a
similarity transform to five landmarks, and their confidences carry the
occlusion signal directly. A surgical mask destroys the nose tip and both mouth
corners — three of the five. A headscarf leaves the mouth intact but removes
hair, ears and jawline, which depresses the detector's overall score without
killing individual points. Reading what we already compute is deterministic,
explainable, needs no training data and no GPU, and can say UNKNOWN.

The stratum matters twice over.

CALIBRATION. The impostor distribution is measured per stratum: a single pooled
ImpostorStats returns a threshold that is too LOW for veiled faces, so the FPIR
guarantee silently fails for exactly the group it most affects.

ROUTING. Occlusion is not only a reason to distrust the face — it is the
instruction for which other evidence to gather. That half is easy to miss, and
missing it is what turns a covered face into an "unknown visitor" shrug instead
of a successful identification through the modalities that still work.
"""
from __future__ import annotations

from enum import Enum

import numpy as np

# ArcFace landmark order: left eye, right eye, nose tip, left mouth, right mouth
LM_LEFT_EYE, LM_RIGHT_EYE, LM_NOSE, LM_MOUTH_L, LM_MOUTH_R = range(5)

# Which landmarks each stratum is expected to lose. Declared rather than
# implied so a reviewer can check the rule against a photograph.
STRATUM_LANDMARKS: dict[str, tuple[int, ...]] = {
    "mask": (LM_NOSE, LM_MOUTH_L, LM_MOUTH_R),
    "scarf": (),          # scarf takes context, not individual points
    "scarf_mask": (LM_NOSE, LM_MOUTH_L, LM_MOUTH_R),
}

# A landmark below this is not located.
LANDMARK_VISIBLE = 0.40
# Below this the detector is struggling with context loss — the scarf signature.
DETECT_CONTEXT_OK = 0.70
# Below this nothing is trustworthy enough to stratify.
DETECT_USABLE = 0.30


class OcclusionStratum(str, Enum):
    CLEAR = "clear"
    MASK = "mask"
    SCARF = "scarf"
    SCARF_MASK = "scarf_mask"
    UNKNOWN = "unknown"


# What to lean on when the face is compromised. This is the half of occlusion
# handling that is easy to miss: the stratum is not only a reason to distrust
# the face, it is the instruction for which other evidence to gather. A mask
# leaves the eyes, so periocular is the designated survivor. A full-body
# covering leaves speech and movement entirely untouched.
SURVIVING_MODALITIES: dict[str, tuple[str, ...]] = {
    "clear":      ("face", "periocular", "voice", "gait"),
    "mask":       ("periocular", "voice", "gait"),
    "scarf":      ("face", "periocular", "voice", "gait"),
    "scarf_mask": ("periocular", "voice", "gait"),
    # Nothing about the face is trustworthy, but a person still walks and
    # speaks. Never an empty tuple: an empty survivor set is how a covered face
    # becomes a silent dead end.
    "unknown":    ("voice", "gait"),
}


def classify_occlusion(landmark_conf: np.ndarray,
                       detect_score: float) -> OcclusionStratum:
    """Which calibration stratum this probe belongs to."""
    conf = np.asarray(landmark_conf, dtype=np.float32).reshape(-1)
    if conf.shape[0] != 5:
        raise ValueError(f"expected 5 landmark confidences, got {conf.shape[0]}")

    eyes_visible = (conf[LM_LEFT_EYE] >= LANDMARK_VISIBLE
                    and conf[LM_RIGHT_EYE] >= LANDMARK_VISIBLE)
    if not eyes_visible or detect_score < DETECT_USABLE:
        # Without the eyes there is no periocular signal either, so there is
        # nothing to stratify. Say so rather than guess: an UNKNOWN stratum has
        # no calibration, and fusion excludes an uncalibrated reading.
        return OcclusionStratum.UNKNOWN

    lower_lost = sum(1 for i in STRATUM_LANDMARKS["mask"]
                     if conf[i] < LANDMARK_VISIBLE)
    masked = lower_lost >= 2
    scarfed = detect_score < DETECT_CONTEXT_OK

    if masked and scarfed:
        return OcclusionStratum.SCARF_MASK
    if masked:
        return OcclusionStratum.MASK
    if scarfed:
        return OcclusionStratum.SCARF
    return OcclusionStratum.CLEAR
