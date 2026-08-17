"""Speaker embedding from MFCC statistics.

WHAT THIS IS AND IS NOT. This is a classical embedding: cepstral statistics over
the utterance, not a trained speaker network. It is materially weaker than an
ECAPA-TDNN, and it is what can be built from the dependencies actually present
(numpy, scipy). Its purpose is to make voice a real modality end to end behind a
contract a neural encoder can replace — the fusion engine's quality floor then
decides, per capture, whether the reading is good enough to vote. That is the
design working: a weak stream is excluded rather than diluting a strong one.

TWO THINGS ARE DELIBERATELY REMOVED, and both have precedent.

c0 IS DROPPED. The gait encoder found FFT absolute phase actively harmful
because it moves with the start frame. c0 is the same kind of mistake: it is log
energy, so it tracks microphone gain and speaker distance rather than the
speaker. Keeping it would make the embedding encode how loud, not who.

CEPSTRAL MEAN NORMALISATION. Subtracting the per-utterance mean removes the
channel's fixed spectral colouring. Without it the embedding largely encodes
which microphone was used, which is exactly wrong when the whole point is
comparing a counter capture against an enrolment.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .features import SAMPLE_RATE, frame_energy_db, mfcc, spectral_flatness

N_MFCC = 13
# c0 excluded, so 12 coefficients; mean + std of the coefficients and of their
# deltas gives 12 * 4.
DIM = (N_MFCC - 1) * 4
MIN_DURATION_S = 1.5
# A frame this far below the utterance peak is silence or room tone.
VOICED_FLOOR_DB = 25.0
# Absolute level, not relative: a capture whose loudest frame sits near the
# digital floor contains no speech however internally consistent it is. Measured
# peaks: clean speech -19 dBFS, digital silence -100 dBFS.
SILENT_DBFS = -60.0
AUDIBLE_DBFS = -30.0
# Spectral flatness above this is noise rather than voice. Measured: 0.0005 for
# a clean voiced sound, 0.45 with noise added, 0.57 pure noise, 1.0 silence.
FLATNESS_VOICED = 0.10
FLATNESS_NOISE = 0.50


@dataclass(frozen=True)
class VoiceEmbedding:
    vector: np.ndarray          # L2-normalised, DIM long
    quality: float              # 0-1, feeds ModalityReading.quality
    duration_s: float
    voiced_ratio: float
    snr_db: float              # diagnostic only — inert without pauses
    peak_dbfs: float = -100.0
    flatness: float = 1.0


def _quality(peak_dbfs: float, flatness: float, duration_s: float) -> float:
    """How much this capture deserves to be believed.

    Three independent ways a capture fails: nothing audible in it, too noisy to
    be voice, too short. The PRODUCT, not the mean — a capture that is fine on
    two counts and hopeless on the third is hopeless, and averaging would let a
    long clear recording of silence score well.

    An earlier version used voiced-frame ratio and a pause-based SNR. Both are
    inert on continuous audio: measured on a tone, a noisy tone and silence, all
    three had zero frames below the voiced floor, so all three scored alike.
    """
    level = float(np.clip((peak_dbfs - SILENT_DBFS)
                          / (AUDIBLE_DBFS - SILENT_DBFS), 0.0, 1.0))
    voice = float(np.clip((FLATNESS_NOISE - flatness)
                          / (FLATNESS_NOISE - FLATNESS_VOICED), 0.0, 1.0))
    length = float(np.clip(duration_s / (2 * MIN_DURATION_S), 0.0, 1.0))
    return float(np.clip(level * voice * length, 0.0, 1.0))


def encode(pcm: np.ndarray, sample_rate: int = SAMPLE_RATE) -> VoiceEmbedding:
    """Utterance → speaker embedding with a quality score."""
    x = np.asarray(pcm, dtype=np.float32).reshape(-1)
    duration_s = len(x) / float(sample_rate)
    if duration_s < MIN_DURATION_S:
        raise ValueError(
            f"need at least {MIN_DURATION_S}s of audio for a speaker claim, "
            f"got {duration_s:.2f}s")

    energy = frame_energy_db(x, sample_rate)
    peak = float(energy.max())
    voiced = energy >= (peak - VOICED_FLOOR_DB)
    voiced_ratio = float(voiced.mean())
    flatness = spectral_flatness(x, sample_rate)
    # Reported for diagnostics. Only meaningful when the capture actually has
    # pauses; see _quality for why it does not drive the score.
    snr_db = float(peak - np.median(energy[~voiced])) if (~voiced).any() else 0.0

    c = mfcc(x, sample_rate, n_mfcc=N_MFCC)
    if voiced.any() and voiced.sum() >= 3:
        c = c[voiced[: len(c)]] if len(voiced) >= len(c) else c
    # Drop c0 (log energy) and remove the channel's fixed colouring.
    c = c[:, 1:]
    c = c - c.mean(axis=0, keepdims=True)

    d = np.diff(c, axis=0) if len(c) > 1 else np.zeros_like(c)
    parts = [c.mean(axis=0), c.std(axis=0), d.mean(axis=0), d.std(axis=0)]
    v = np.concatenate(parts).astype(np.float32)

    n = float(np.linalg.norm(v))
    v = v / n if n > 0 else np.zeros(DIM, dtype=np.float32)

    return VoiceEmbedding(
        vector=v.astype(np.float32),
        quality=_quality(peak, flatness, duration_s),
        duration_s=duration_s, voiced_ratio=voiced_ratio, snr_db=snr_db,
        peak_dbfs=peak, flatness=flatness)
