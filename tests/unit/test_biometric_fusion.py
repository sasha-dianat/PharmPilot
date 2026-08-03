"""Multi-modal fusion: face + gait + iris (+ voice) into one identity decision.

The engine's job is not to combine every stream it is given. It is to combine
the streams that are *worth* combining and to say plainly which it discarded.
Three properties matter more than the arithmetic:

1. **A modality below its usable floor is EXCLUDED, not down-weighted.** Fusing
   a stream operating near chance drags a strong one — published face+voice
   fusion gains assume each modality is above its floor. Gait under a chador has
   no published evaluation at all, so it must be capable of contributing zero.
2. **Disagreement lowers confidence.** If face says A and iris says B, the
   fused result is a REVIEW, never a confident A. Naive score-summing hides
   this; grouping by identity before fusing exposes it.
3. **Every decision is explainable.** The output states each modality's weight,
   its calibrated contribution, and why anything was dropped.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from services.biometric.fusion import (
    FUSION_FLOORS, ModalityReading, fuse)
from services.biometric.identity_resolution.thresholds import ImpostorStats

# Measured impostor statistics stand in for a real calibration run.
FACE = ImpostorStats(mean=0.02, std=0.075, sample_size=250_000, model="face")
IRIS = ImpostorStats(mean=0.01, std=0.040, sample_size=50_000, model="iris")
GAIT = ImpostorStats(mean=0.05, std=0.150, sample_size=20_000, model="gait")


def reading(modality, ident, similarity, quality, stats, n=400):
    return ModalityReading(modality=modality, identity_id=ident,
                           similarity=similarity, quality=quality,
                           stats=stats, gallery_size=n)


# ── agreement raises confidence ──────────────────────────────────────────

def test_two_agreeing_modalities_beat_either_alone():
    alice = uuid4()
    face_only = fuse([reading("face", alice, 0.62, 0.9, FACE)])
    both = fuse([reading("face", alice, 0.62, 0.9, FACE),
                 reading("iris", alice, 0.55, 0.9, IRIS)])
    assert both.identity_id == alice
    assert both.confidence > face_only.confidence


def test_fused_confidence_is_a_probability():
    alice = uuid4()
    r = fuse([reading("face", alice, 0.70, 0.95, FACE),
              reading("iris", alice, 0.60, 0.95, IRIS)])
    assert 0.0 <= r.confidence <= 1.0


# ── disagreement must not produce a confident answer ─────────────────────

def test_modalities_naming_different_people_force_review():
    alice, bob = uuid4(), uuid4()
    r = fuse([reading("face", alice, 0.65, 0.9, FACE),
              reading("iris", bob, 0.62, 0.9, IRIS)])
    assert r.decision == "review"
    assert "disagree" in r.explanation.lower() or "margin" in r.explanation.lower()


def test_disagreement_is_less_confident_than_agreement():
    alice, bob = uuid4(), uuid4()
    agree = fuse([reading("face", alice, 0.65, 0.9, FACE),
                  reading("iris", alice, 0.62, 0.9, IRIS)])
    clash = fuse([reading("face", alice, 0.65, 0.9, FACE),
                  reading("iris", bob, 0.62, 0.9, IRIS)])
    assert clash.confidence < agree.confidence


# ── the floor rule: exclude, never dilute ────────────────────────────────

def test_low_quality_reading_is_excluded_with_a_reason():
    alice = uuid4()
    r = fuse([reading("face", alice, 0.70, 0.95, FACE),
              reading("gait", alice, 0.60, 0.05, GAIT)])     # quality below floor
    assert [e.modality for e in r.excluded] == ["gait"]
    assert "quality" in r.excluded[0].reason.lower()
    assert [c.modality for c in r.contributions] == ["face"]


def test_excluding_a_weak_stream_does_not_lower_confidence():
    """The whole point of exclusion over down-weighting: a stream operating near
    chance must not be able to drag a good one."""
    alice = uuid4()
    clean = fuse([reading("face", alice, 0.70, 0.95, FACE)])
    with_junk = fuse([reading("face", alice, 0.70, 0.95, FACE),
                      reading("gait", alice, 0.02, 0.02, GAIT)])
    assert with_junk.confidence == pytest.approx(clean.confidence, abs=1e-9)


def test_uncalibrated_modality_is_excluded():
    """No measured impostor distribution means no defensible score, so the
    stream contributes nothing rather than contributing a guess."""
    alice = uuid4()
    r = fuse([reading("face", alice, 0.70, 0.95, FACE),
              reading("iris", alice, 0.90, 0.95, None)])
    assert [e.modality for e in r.excluded] == ["iris"]
    assert "calibrat" in r.excluded[0].reason.lower()


def test_every_modality_has_a_declared_quality_floor():
    for m in ("face", "gait", "iris", "voice"):
        assert m in FUSION_FLOORS
        assert 0.0 < FUSION_FLOORS[m].min_quality <= 1.0


def test_gait_floor_is_stricter_than_face():
    """Grounded in evidence, not taste: CASIA-B rank-1 falls to 86.7% under the
    clothing covariate on 74 lab subjects, and no evaluation under a chador
    exists at all. Gait must clear a higher bar to be admitted."""
    assert FUSION_FLOORS["gait"].min_quality > FUSION_FLOORS["face"].min_quality


def test_all_readings_excluded_yields_no_match_not_a_crash():
    alice = uuid4()
    r = fuse([reading("gait", alice, 0.9, 0.01, GAIT)])
    assert r.decision == "no_match"
    assert r.identity_id is None
    assert r.confidence == 0.0


def test_no_readings_at_all():
    r = fuse([])
    assert r.decision == "no_match" and r.identity_id is None


# ── explainability ───────────────────────────────────────────────────────

def test_result_explains_each_contribution():
    alice = uuid4()
    r = fuse([reading("face", alice, 0.70, 0.95, FACE),
              reading("iris", alice, 0.60, 0.80, IRIS)])
    assert len(r.contributions) == 2
    for c in r.contributions:
        assert 0.0 <= c.calibrated <= 1.0
        assert c.weight > 0
        assert c.similarity is not None
    assert r.explanation


def test_single_modality_never_auto_accepts_on_its_own():
    """Face alone may not exceed IAL1 — the platform invariant. Fusion does not
    create an exception to it: a lone modality is a proposal, not a decision."""
    alice = uuid4()
    r = fuse([reading("face", alice, 0.95, 1.0, FACE)])
    assert r.decision != "auto"
    assert "single" in r.explanation.lower() or "one modality" in r.explanation.lower()


def test_two_strong_agreeing_modalities_can_auto_accept():
    alice = uuid4()
    r = fuse([reading("face", alice, 0.80, 0.95, FACE),
              reading("iris", alice, 0.70, 0.95, IRIS)])
    assert r.decision == "auto"
    assert r.identity_id == alice
