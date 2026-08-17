"""Voice embedding: what it must encode, and what it must ignore.

The gait encoder's documented lesson applies directly. There, FFT absolute
phase was in the descriptor and actively harmful — it moves with the start
frame, so one person captured twice matched at cosine 1.000 on magnitude and
-0.318 on phase. Voice's analogue is c0, the log-energy coefficient: it tracks
microphone gain and speaker distance, not the speaker.
"""
from __future__ import annotations

import numpy as np
import pytest

from services.audio.voice.encoder import DIM, MIN_DURATION_S, encode
from services.audio.voice.features import SAMPLE_RATE


def voiced(f0=120.0, seconds=2.0, sr=SAMPLE_RATE, amp=0.4, formants=(700, 1220, 2600)):
    """A crude voiced sound: a glottal buzz shaped by three formants."""
    t = np.arange(int(sr * seconds)) / sr
    sig = np.zeros_like(t)
    for k in range(1, 25):                       # harmonics of f0
        sig += np.sin(2 * np.pi * f0 * k * t) / k
    shaped = np.zeros_like(t)
    for f in formants:                           # crude formant emphasis
        shaped += np.sin(2 * np.pi * f * t) * 0.3
    out = (sig / np.abs(sig).max()) * 0.7 + shaped * 0.3
    return (amp * out / np.abs(out).max()).astype(np.float32)


def cos(a, b):
    return float(np.dot(a, b))


def test_embedding_is_unit_length_and_the_declared_dimension():
    e = encode(voiced())
    assert e.vector.shape == (DIM,)
    assert np.linalg.norm(e.vector) == pytest.approx(1.0, abs=1e-5)


def test_same_voice_twice_matches_itself():
    a = encode(voiced(seconds=2.0)).vector
    b = encode(voiced(seconds=2.5)).vector          # different length, same voice
    assert cos(a, b) > 0.9


def test_different_pitch_and_formants_separate():
    a = encode(voiced(f0=110.0, formants=(700, 1220, 2600))).vector
    b = encode(voiced(f0=210.0, formants=(400, 2000, 2800))).vector
    assert cos(a, b) < 0.9


def test_microphone_gain_does_not_change_the_embedding():
    """The c0 lesson. A voice recorded quietly and loudly is the same voice; an
    embedding that moves with gain is encoding the microphone."""
    quiet = encode(voiced(amp=0.05)).vector
    loud = encode(voiced(amp=0.9)).vector
    assert cos(quiet, loud) > 0.98


def test_a_fixed_channel_colouring_is_normalised_away():
    """Cepstral mean normalisation: a constant spectral tilt is the microphone,
    not the speaker. Without it the embedding largely encodes which mic."""
    clean = voiced()
    tilted = np.convolve(clean, np.array([1.0, -0.6], dtype=np.float32),
                         mode="same").astype(np.float32)
    assert cos(encode(clean).vector, encode(tilted).vector) > 0.9


def test_quality_falls_with_noise():
    clean = voiced()
    rng = np.random.default_rng(0)
    noisy = (clean + rng.normal(0, 0.25, clean.shape)).astype(np.float32)
    assert encode(noisy).quality < encode(clean).quality


def test_silence_scores_near_zero_quality():
    q = encode(np.zeros(int(SAMPLE_RATE * 2), dtype=np.float32)).quality
    assert q < 0.2


def test_too_short_audio_is_refused():
    """Below the minimum there is not enough evidence for a speaker claim, and
    an embedding computed anyway would be admitted by a caller that trusts it."""
    with pytest.raises(ValueError, match="at least"):
        encode(voiced(seconds=MIN_DURATION_S / 2))


def test_quality_and_diagnostics_are_reported():
    e = encode(voiced())
    assert 0.0 <= e.quality <= 1.0
    assert e.duration_s == pytest.approx(2.0, abs=0.1)
    assert 0.0 <= e.voiced_ratio <= 1.0
    assert np.isfinite(e.snr_db)


def test_quality_separates_silence_noise_and_voice_without_pauses():
    """The bug this replaced: quality was built on voiced-frame ratio and a
    pause-based SNR, and continuous audio has no pauses. Measured, ALL THREE of
    these had zero frames below the voiced floor — so silence scored like
    speech. Ordering must now hold on pause-free audio."""
    rng = np.random.default_rng(0)
    clean = voiced()
    noisy = (clean + rng.normal(0, 0.25, clean.shape)).astype(np.float32)
    noise = rng.normal(0, 0.3, clean.shape).astype(np.float32)
    silence = np.zeros(int(SAMPLE_RATE * 2), dtype=np.float32)

    q = {n: encode(x).quality for n, x in
         (("clean", clean), ("noisy", noisy), ("noise", noise), ("silence", silence))}
    assert q["clean"] > q["noisy"] > q["noise"], q
    assert q["silence"] == pytest.approx(0.0, abs=1e-6), q
    # and every one of them has voiced_ratio 1.0, which is why that signal alone
    # could never have separated them
    assert encode(silence).voiced_ratio == pytest.approx(1.0)


def test_a_long_recording_of_silence_does_not_score_well():
    """Why the terms multiply rather than average: length alone must not rescue
    a capture with nothing in it."""
    long_silence = np.zeros(int(SAMPLE_RATE * 30), dtype=np.float32)
    assert encode(long_silence).quality == pytest.approx(0.0, abs=1e-6)
