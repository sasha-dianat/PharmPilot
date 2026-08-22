"""Voice enters fusion through the same contract as every other modality.

No special case: the fusion engine's floors, calibration requirement and
exclusion rules are authoritative, and voice is subject to all of them.
"""
from __future__ import annotations

from uuid import uuid4

import numpy as np
import pytest

from services.audio.voice.readings import segment_to_reading
from services.biometric.fusion import FUSION_FLOORS, ModalityReading, fuse
from services.biometric.identity_resolution.thresholds import ImpostorStats

VOICE_STATS = ImpostorStats(mean=0.03, std=0.09, sample_size=40_000, model="voice")


class FakeEmbedding:
    def __init__(self, quality=0.8):
        self.vector = np.ones(48, dtype=np.float32) / np.sqrt(48)
        self.quality = quality


def test_a_hit_becomes_a_voice_modality_reading():
    alice = uuid4()
    r = segment_to_reading(FakeEmbedding(), [(alice, 0.72)], VOICE_STATS, 400)
    assert isinstance(r, ModalityReading)
    assert r.modality == "voice"
    assert r.identity_id == alice
    assert r.similarity == pytest.approx(0.72)
    assert r.gallery_size == 400


def test_capture_quality_travels_into_the_reading():
    """The fusion floor acts on this number, so it must be the measured one."""
    r = segment_to_reading(FakeEmbedding(quality=0.31), [(uuid4(), 0.7)],
                           VOICE_STATS, 400)
    assert r.quality == pytest.approx(0.31)


def test_no_gallery_hit_yields_no_reading():
    assert segment_to_reading(FakeEmbedding(), [], VOICE_STATS, 400) is None


def test_an_uncalibrated_voice_reading_is_excluded_by_fusion():
    """stats=None is meaningful: an uncalibrated stream may not vote."""
    r = segment_to_reading(FakeEmbedding(), [(uuid4(), 0.9)], None, 400)
    result = fuse([r])
    assert [e.modality for e in result.excluded] == ["voice"]
    assert "calibrat" in result.excluded[0].reason.lower()


def test_a_low_quality_voice_reading_is_excluded_by_the_floor():
    floor = FUSION_FLOORS["voice"].min_quality
    r = segment_to_reading(FakeEmbedding(quality=floor - 0.1),
                           [(uuid4(), 0.9)], VOICE_STATS, 400)
    result = fuse([r])
    assert [e.modality for e in result.excluded] == ["voice"]
    assert "quality" in result.excluded[0].reason.lower()


def test_voice_corroborates_face_rather_than_replacing_it():
    """The point of the modality: an independent second stream, so a single
    biometric never has to carry a decision alone."""
    alice = uuid4()
    face_stats = ImpostorStats(mean=0.02, std=0.075, sample_size=250_000)
    face = ModalityReading(modality="face", identity_id=alice, similarity=0.70,
                           quality=0.9, stats=face_stats, gallery_size=400)
    voice = segment_to_reading(FakeEmbedding(quality=0.8), [(alice, 0.68)],
                               VOICE_STATS, 400)

    alone = fuse([face])
    together = fuse([face, voice])
    assert alone.decision == "review"          # one modality never auto-accepts
    assert together.confidence >= alone.confidence
    assert {c.modality for c in together.contributions} == {"face", "voice"}
