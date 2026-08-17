"""Occlusion stratum from landmark visibility.

The per-stratum calibration design needs to know WHICH stratum a probe is in
before it can pick the right ImpostorStats. Nothing computed that. This does it
from the 5 landmarks the aligner already produces — no model, no training data.

Landmark order is the ArcFace convention:
    0 = left eye, 1 = right eye, 2 = nose tip, 3 = left mouth, 4 = right mouth
"""
from __future__ import annotations

import numpy as np
import pytest

from services.biometric.occlusion import (
    OcclusionStratum, STRATUM_LANDMARKS, classify_occlusion)


def conf(left_eye=0.95, right_eye=0.95, nose=0.95, mouth_l=0.95, mouth_r=0.95):
    return np.array([left_eye, right_eye, nose, mouth_l, mouth_r], dtype=np.float32)


def test_all_landmarks_visible_is_clear():
    assert classify_occlusion(conf(), detect_score=0.95) == OcclusionStratum.CLEAR


def test_lost_mouth_and_nose_is_a_mask():
    """A surgical mask destroys three of the five alignment landmarks."""
    got = classify_occlusion(conf(nose=0.15, mouth_l=0.10, mouth_r=0.12),
                             detect_score=0.90)
    assert got == OcclusionStratum.MASK


def test_low_detection_score_with_intact_mouth_is_a_scarf():
    """A headscarf leaves the mouth but removes hair, ears and jawline context,
    which shows up as a depressed overall detection score."""
    got = classify_occlusion(conf(), detect_score=0.55)
    assert got == OcclusionStratum.SCARF


def test_scarf_and_mask_together():
    got = classify_occlusion(conf(nose=0.12, mouth_l=0.09, mouth_r=0.11),
                             detect_score=0.52)
    assert got == OcclusionStratum.SCARF_MASK


def test_unusable_landmarks_are_unknown_not_guessed():
    """Refusing to classify is safer than guessing: an UNKNOWN stratum has no
    calibration, so fusion excludes the reading rather than scoring it against
    the wrong impostor distribution."""
    got = classify_occlusion(conf(0.05, 0.05, 0.05, 0.05, 0.05), detect_score=0.2)
    assert got == OcclusionStratum.UNKNOWN


def test_wrong_landmark_count_raises():
    with pytest.raises(ValueError, match="5 landmark"):
        classify_occlusion(np.array([0.9, 0.9], dtype=np.float32), detect_score=0.9)


def test_every_stratum_declares_which_landmarks_it_loses():
    for stratum in ("mask", "scarf", "scarf_mask"):
        assert stratum in STRATUM_LANDMARKS
    assert STRATUM_LANDMARKS["mask"] == (2, 3, 4)


def test_stratum_values_match_the_calibration_key_vocabulary():
    """These strings become the ImpostorStats.population key, so they are a
    stored vocabulary, not a display label."""
    assert OcclusionStratum.CLEAR.value == "clear"
    assert OcclusionStratum.SCARF_MASK.value == "scarf_mask"


def test_every_stratum_names_the_modalities_that_survive_it():
    """Occlusion is not only a reason to trust face less — it says what to lean
    on instead. A covered face must route to the survivors, not shrug."""
    from services.biometric.occlusion import SURVIVING_MODALITIES

    for stratum in OcclusionStratum:
        assert stratum.value in SURVIVING_MODALITIES, stratum
        assert SURVIVING_MODALITIES[stratum.value], f"{stratum} names no survivor"


def test_a_mask_routes_to_periocular_because_the_eyes_remain():
    from services.biometric.occlusion import SURVIVING_MODALITIES

    survivors = SURVIVING_MODALITIES["mask"]
    assert "periocular" in survivors
    assert "face" not in survivors        # the lower face is gone


def test_a_chador_routes_to_voice_and_gait():
    """Full-body covering leaves speech and movement untouched."""
    from services.biometric.occlusion import SURVIVING_MODALITIES

    survivors = SURVIVING_MODALITIES["scarf_mask"]
    assert "voice" in survivors and "gait" in survivors


def test_periocular_is_a_registered_fusion_modality():
    """It appeared only in design prose. A survivor the fusion engine does not
    recognise would be excluded as an unknown modality."""
    from services.biometric.fusion import FUSION_FLOORS

    assert "periocular" in FUSION_FLOORS
    assert 0.0 < FUSION_FLOORS["periocular"].min_quality <= 1.0


def test_every_survivor_named_anywhere_is_a_real_fusion_modality():
    from services.biometric.fusion import FUSION_FLOORS
    from services.biometric.occlusion import SURVIVING_MODALITIES

    for stratum, survivors in SURVIVING_MODALITIES.items():
        for m in survivors:
            assert m in FUSION_FLOORS, f"{stratum} names unknown modality {m}"
