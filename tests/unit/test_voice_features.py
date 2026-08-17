"""MFCC from PCM, in numpy and scipy only.

librosa, soundfile and torchaudio are not installed and this phase adds no
dependencies. MFCC is a defined transform over scipy's FFT and DCT.
"""
from __future__ import annotations

import numpy as np
import pytest

from services.audio.voice.features import (
    FRAME_MS, HOP_MS, SAMPLE_RATE, frame_energy_db, mfcc)


def tone(freq=220.0, seconds=1.0, sr=SAMPLE_RATE, amp=0.5):
    t = np.arange(int(sr * seconds)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_mfcc_shape_follows_the_hop():
    pcm = tone(seconds=1.0)
    m = mfcc(pcm, SAMPLE_RATE)
    expected = 1 + (len(pcm) - int(SAMPLE_RATE * FRAME_MS / 1000)) // int(
        SAMPLE_RATE * HOP_MS / 1000)
    assert m.shape == (expected, 13)


def test_mfcc_is_finite_for_silence():
    """A silent frame must not produce NaN or -inf from log(0)."""
    m = mfcc(np.zeros(SAMPLE_RATE, dtype=np.float32), SAMPLE_RATE)
    assert np.isfinite(m).all()


def test_different_tones_give_different_cepstra():
    a = mfcc(tone(220.0), SAMPLE_RATE).mean(axis=0)
    b = mfcc(tone(880.0), SAMPLE_RATE).mean(axis=0)
    assert not np.allclose(a, b, atol=1e-3)


def test_gain_changes_c0_but_barely_moves_the_rest():
    """c0 is log energy — it tracks microphone gain, not the speaker. The higher
    coefficients carry the spectral shape and must be far more stable."""
    quiet = mfcc(tone(amp=0.1), SAMPLE_RATE).mean(axis=0)
    loud = mfcc(tone(amp=0.8), SAMPLE_RATE).mean(axis=0)
    c0_shift = abs(loud[0] - quiet[0])
    rest_shift = np.abs(loud[1:] - quiet[1:]).max()
    assert c0_shift > rest_shift


def test_energy_is_reported_in_db_per_frame():
    e = frame_energy_db(tone(), SAMPLE_RATE)
    assert e.ndim == 1 and len(e) > 10
    assert np.isfinite(e).all()
    assert frame_energy_db(np.zeros(SAMPLE_RATE, np.float32),
                           SAMPLE_RATE).max() < -60


def test_too_short_input_raises_rather_than_returning_an_empty_array():
    with pytest.raises(ValueError, match="too short"):
        mfcc(np.zeros(10, dtype=np.float32), SAMPLE_RATE)
