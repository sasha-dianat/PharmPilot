# Voice Modality Implementation Plan — Phase 2 of 7

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make voice a real fusion modality — a diarized speech segment becomes a `ModalityReading` the fusion engine can admit — and make text-dependent verification work on the phrase the customer already speaks.

**Architecture:** `FUSION_FLOORS` has carried a `voice` entry since Phase 1 and nothing produces a voice reading. The existing diarizer imports `pyannote.audio`, which is not installed, so it cannot run at all. This phase builds a working encoder from what IS installed (numpy + scipy), behind a contract a neural encoder can later replace — the same pattern that worked for the gait encoder and for the face engine's injectable embeddings.

**The governing insight:** at the Rx desk the customer already speaks their national code for the insurance lookup. That makes this **text-dependent** verification on a fixed phrase, which is materially more accurate than text-independent, on an utterance produced anyway. Nothing has to be adopted.

**Tech Stack:** Python 3.12, numpy, scipy. Torch 2.2.2 is present but deliberately unused here.

## Global Constraints

- Python interpreter for all commands: `/Users/sashad85/miniforge3/bin/python3`
- Tests run from repo root: `/Users/sashad85/miniforge3/bin/python3 -m pytest`
- **No new third-party dependencies.** Verified absent and unusable: `librosa`, `soundfile`, `torchaudio`, `onnxruntime`, `speechbrain`, `pyannote.audio`, `webrtcvad`. Available: `numpy 1.26.4`, `scipy 1.17.1`, `torch 2.2.2`.
- Every new route MUST carry an auth dependency or be added to the allowlist in `tests/unit/test_route_authentication.py` with a written reason.
- An uncalibrated modality must not vote. `stats=None` means excluded, by design.
- Voice readings feed `services.biometric.fusion.ModalityReading`; the fusion engine's floors and exclusion rules are authoritative and are NOT relaxed for voice.
- Migration head is `0046`. No migration is expected in this phase; `biometric_templates` already admits `modality IN ('face','gait','voice','iris')`.
- Do not export `DATABASE_URL` when running the suite.
- Persian/RTL for user-facing strings; internal identifiers stay English.

---

## What already exists

| Component | Path | State |
|---|---|---|
| `voice` floor (min_quality 0.45, reliability 0.60) | `services/biometric/fusion/__init__.py` | Declared, unused |
| `SpeakerSegment` with `biometric_identity_id`, `voice_match_confidence` | `services/audio/speaker_diarization/diarizer.py` | Contract exists |
| `PharmacySpeakerDiarizer` | same | **Cannot run — imports `pyannote.audio`, not installed** |
| Whisper transcription engine | `services/audio/transcription/engine.py` | Lazy import; defaults to `medium.en` |
| `biometric_templates` accepts `voice` | migration `0033` | Ready |
| `to_readings` → `ModalityReading` | `services/biometric/identity_resolution/vector_store.py` | Ready |

## File Structure

| File | Responsibility |
|---|---|
| `services/audio/voice/__init__.py` (create) | Package marker. |
| `services/audio/voice/features.py` (create) | PCM → MFCC + frame quality. numpy/scipy only. |
| `services/audio/voice/encoder.py` (create) | MFCC → fixed-dim L2-normalised embedding + a quality score. |
| `services/audio/voice/text_dependent.py` (create) | Verify the spoken national code: digits AND voice, as one operation. |
| `services/audio/voice/readings.py` (create) | Diarized segment → `ModalityReading`. |
| `services/audio/transcription/engine.py` (modify) | Persian-capable default; language made explicit. |
| `services/audio/transcription/pipeline.py` (modify) | Same default. |
| `shared/models/audio.py` (modify) | `whisper_model_used` default. |
| `tests/unit/test_voice_features.py` (create) | MFCC correctness and invariances. |
| `tests/unit/test_voice_encoder.py` (create) | Embedding separates speakers; quality gates. |
| `tests/unit/test_voice_text_dependent.py` (create) | Digits + voice together; neither alone suffices. |
| `tests/unit/test_voice_readings.py` (create) | Fusion integration. |

---

### Task 1: Persian-capable transcription default

**Why first, and why it belongs to this phase:** `medium.en` is an **English-only** Whisper variant. On Persian speech it does not degrade gracefully — it hallucinates plausible English. Every transcript produced under this default is unreliable, and this phase is about to build identity signals on top of the same audio path. The best Persian fine-tunes of `large-v3` report roughly 14% WER on *clean* speech; that is the honest ceiling and it is only reachable with a multilingual model.

Three places carry the default and all must move together, or the one that lags silently reintroduces it.

**Files:**
- Modify: `services/audio/transcription/engine.py:74,78`
- Modify: `services/audio/transcription/pipeline.py:37`
- Modify: `shared/models/audio.py` (`whisper_model_used` default)
- Test: `tests/unit/test_voice_transcription_language.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `PharmacyTranscriptionEngine(model_size="large-v3", language="fa")`; `DEFAULT_MODEL = "large-v3"` and `DEFAULT_LANGUAGE = "fa"` module constants in `engine.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_voice_transcription_language.py`:

```python
"""The transcription default must be able to transcribe Persian.

`medium.en` is an English-ONLY Whisper variant. On Persian speech it does not
degrade gracefully — it emits plausible English that was never said. A pharmacy
in Iran running that default produces transcripts that read fine and are
fiction, and this phase is about to build identity signals on the same audio.
"""
from __future__ import annotations

import re
from pathlib import Path

ENGINE = Path("services/audio/transcription/engine.py")
PIPELINE = Path("services/audio/transcription/pipeline.py")
MODEL = Path("shared/models/audio.py")


def test_no_english_only_model_is_a_default_anywhere():
    """`.en` suffixed models cannot transcribe Persian at all."""
    for p in (ENGINE, PIPELINE, MODEL):
        src = p.read_text(encoding="utf-8")
        for m in re.finditer(r'=\s*"(\w+\.en)"', src):
            raise AssertionError(
                f"{p}: {m.group(1)!r} is an English-only model used as a default")


def test_engine_declares_its_default_model_and_language():
    src = ENGINE.read_text(encoding="utf-8")
    assert 'DEFAULT_MODEL = "large-v3"' in src
    assert 'DEFAULT_LANGUAGE = "fa"' in src


def test_engine_accepts_and_stores_a_language():
    from services.audio.transcription.engine import (
        DEFAULT_LANGUAGE, DEFAULT_MODEL, PharmacyTranscriptionEngine)

    e = PharmacyTranscriptionEngine()
    assert e.model_size == DEFAULT_MODEL
    assert e.language == DEFAULT_LANGUAGE

    e2 = PharmacyTranscriptionEngine(model_size="large-v3", language="en")
    assert e2.language == "en"


def test_language_is_passed_to_whisper_not_left_to_autodetect():
    """Auto-detect on a short, noisy counter utterance frequently guesses wrong,
    and a wrong guess produces confident nonsense rather than an error."""
    src = ENGINE.read_text(encoding="utf-8")
    body = src[src.index("def transcribe"):]
    assert "language=" in body, "the transcribe call must pin the language"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_voice_transcription_language.py -q`
Expected: FAIL — the first test raises on `medium.en`

- [ ] **Step 3: Write the implementation**

In `services/audio/transcription/engine.py`, add module constants above the class:

```python
# `medium.en` was the default here. `.en` models are English-ONLY: on Persian
# they do not fail, they hallucinate plausible English. Persian requires a
# multilingual model, and the language is pinned rather than auto-detected
# because a short noisy counter utterance is exactly the case auto-detect gets
# wrong — and a wrong guess yields confident nonsense, not an error.
DEFAULT_MODEL = "large-v3"
DEFAULT_LANGUAGE = "fa"
```

Change the constructor:

```python
    def __init__(self, model_size: str = DEFAULT_MODEL,
                 language: str = DEFAULT_LANGUAGE):
        self.model_size = model_size
        self.language = language
        self._model = None
```

In the `transcribe` method, pass the language into the Whisper call — locate the
existing `self._model.transcribe(` call and add `language=self.language,` to its
keyword arguments.

In `services/audio/transcription/pipeline.py`, change `whisper_model="medium.en"`
to `whisper_model="large-v3"`.

In `shared/models/audio.py`, change the `whisper_model_used` column default from
`"medium.en"` to `"large-v3"`.

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_voice_transcription_language.py -q`
Expected: PASS — 4 passed

- [ ] **Step 5: Commit**

```bash
git add services/audio/transcription/engine.py services/audio/transcription/pipeline.py shared/models/audio.py tests/unit/test_voice_transcription_language.py
git commit -m "fix(audio): the transcription default could not transcribe Persian

medium.en is an English-ONLY Whisper variant. On Persian it does not degrade
gracefully — it emits plausible English that was never said, so every transcript
produced under this default reads fine and is fiction. Three places carried it
and all move together, or the laggard silently reintroduces it.

The language is now pinned rather than auto-detected: a short, noisy counter
utterance is exactly the case auto-detect gets wrong, and a wrong guess yields
confident nonsense rather than an error."
```

---

### Task 2: Voice features — PCM to MFCC

**Why numpy/scipy and not librosa:** librosa, soundfile and torchaudio are all absent, and this phase adds no dependencies. MFCC is a well-defined transform that scipy's FFT and DCT already provide; implementing it directly is ~80 lines and removes an install from the deployment path.

**Files:**
- Create: `services/audio/voice/__init__.py`, `services/audio/voice/features.py`
- Test: `tests/unit/test_voice_features.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `mfcc(pcm: np.ndarray, sample_rate: int, n_mfcc: int = 13) -> np.ndarray` returning `(frames, n_mfcc)`; `frame_energy_db(pcm, sample_rate) -> np.ndarray`; `SAMPLE_RATE = 16000`, `FRAME_MS = 25`, `HOP_MS = 10`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_voice_features.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_voice_features.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.audio.voice'`

- [ ] **Step 3: Write the implementation**

Create `services/audio/voice/__init__.py`:

```python
"""Voice as a biometric modality: features, embedding, and fusion readings."""
```

Create `services/audio/voice/features.py`:

```python
"""PCM to MFCC, in numpy and scipy only.

librosa, soundfile and torchaudio are not installed and this phase adds no
dependencies. MFCC is a defined transform and scipy already provides the FFT
and DCT it needs, so implementing it directly costs ~80 lines and removes an
install from the deployment path.
"""
from __future__ import annotations

import numpy as np
from scipy.fftpack import dct

SAMPLE_RATE = 16000
FRAME_MS = 25
HOP_MS = 10
N_FILTERS = 26
PRE_EMPHASIS = 0.97
_EPS = 1e-10


def _frames(pcm: np.ndarray, sample_rate: int) -> np.ndarray:
    n = int(sample_rate * FRAME_MS / 1000)
    hop = int(sample_rate * HOP_MS / 1000)
    if len(pcm) < n:
        raise ValueError(f"signal too short: {len(pcm)} samples, need >= {n}")
    count = 1 + (len(pcm) - n) // hop
    idx = np.arange(n)[None, :] + hop * np.arange(count)[:, None]
    return pcm[idx] * np.hamming(n)[None, :]


def _hz_to_mel(f):
    return 2595.0 * np.log10(1.0 + f / 700.0)


def _mel_to_hz(m):
    return 700.0 * (10.0 ** (m / 2595.0) - 1.0)


def _filterbank(sample_rate: int, n_fft: int) -> np.ndarray:
    lo, hi = _hz_to_mel(0.0), _hz_to_mel(sample_rate / 2)
    points = _mel_to_hz(np.linspace(lo, hi, N_FILTERS + 2))
    bins = np.floor((n_fft + 1) * points / sample_rate).astype(int)
    fb = np.zeros((N_FILTERS, n_fft // 2 + 1), dtype=np.float64)
    for i in range(N_FILTERS):
        l, c, r = bins[i], bins[i + 1], bins[i + 2]
        if c == l:
            c = l + 1
        if r == c:
            r = c + 1
        r = min(r, fb.shape[1] - 1)
        c = min(c, r)
        if c > l:
            fb[i, l:c] = (np.arange(l, c) - l) / (c - l)
        if r > c:
            fb[i, c:r] = (r - np.arange(c, r)) / (r - c)
    return fb


def mfcc(pcm: np.ndarray, sample_rate: int = SAMPLE_RATE,
         n_mfcc: int = 13) -> np.ndarray:
    """(frames, n_mfcc) cepstral coefficients."""
    x = np.asarray(pcm, dtype=np.float64).reshape(-1)
    x = np.append(x[0], x[1:] - PRE_EMPHASIS * x[:-1])   # pre-emphasis
    fr = _frames(x, sample_rate)
    n_fft = 1 << (fr.shape[1] - 1).bit_length()
    power = (np.abs(np.fft.rfft(fr, n=n_fft)) ** 2) / n_fft
    energies = power @ _filterbank(sample_rate, n_fft).T
    # +EPS before the log: a silent frame must yield a finite number, not -inf.
    return dct(np.log(energies + _EPS), type=2, axis=1, norm="ortho")[:, :n_mfcc]


def frame_energy_db(pcm: np.ndarray,
                    sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Per-frame energy in dBFS — the basis of the voiced-frame and SNR checks."""
    fr = _frames(np.asarray(pcm, dtype=np.float64).reshape(-1), sample_rate)
    return 10.0 * np.log10(np.mean(fr ** 2, axis=1) + _EPS)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_voice_features.py -q`
Expected: PASS — 6 passed

- [ ] **Step 5: Commit**

```bash
git add services/audio/voice/ tests/unit/test_voice_features.py
git commit -m "feat(voice): MFCC from PCM in numpy and scipy only

librosa, soundfile and torchaudio are absent and this phase adds no
dependencies. MFCC is a defined transform over scipy's FFT and DCT, so it costs
~80 lines directly and removes an install from the deployment path. A silent
frame yields a finite number rather than -inf from log(0)."
```

---

### Task 3: Voice embedding and quality

**The design decision that matters, and its precedent:** the gait encoder found that FFT *absolute phase* was actively harmful — it moves with the start frame, so the same person captured twice matched at cosine 1.000 on magnitude and −0.318 on phase. Voice has the exact analogue: **c0 is log energy**, which tracks microphone gain and speaker distance rather than speaker identity. Including it makes the embedding encode *how loud* instead of *who*. It is dropped.

The second is **cepstral mean normalisation**: subtracting the per-utterance mean removes the channel's fixed spectral colouring, which is what lets a counter mic and a waiting-area mic be compared at all. Without it the embedding largely encodes which microphone was used.

**Files:**
- Create: `services/audio/voice/encoder.py`
- Test: `tests/unit/test_voice_encoder.py`

**Interfaces:**
- Consumes: `features.mfcc`, `features.frame_energy_db` (Task 2).
- Produces: `encode(pcm, sample_rate=SAMPLE_RATE) -> VoiceEmbedding` where `VoiceEmbedding` is a frozen dataclass with `vector: np.ndarray` (L2-normalised, `DIM` long), `quality: float` in [0,1], `duration_s: float`, `voiced_ratio: float`, `snr_db: float`; plus `DIM: int` and `MIN_DURATION_S = 1.5`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_voice_encoder.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_voice_encoder.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.audio.voice.encoder'`

- [ ] **Step 3: Write the implementation**

Create `services/audio/voice/encoder.py`:

```python
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

from .features import HOP_MS, SAMPLE_RATE, frame_energy_db, mfcc

N_MFCC = 13
# c0 excluded, so 12 coefficients; mean + std of the coefficients and of their
# deltas gives 12 * 4.
DIM = (N_MFCC - 1) * 4
MIN_DURATION_S = 1.5
# A frame this far below the utterance peak is silence or room tone.
VOICED_FLOOR_DB = 25.0


@dataclass(frozen=True)
class VoiceEmbedding:
    vector: np.ndarray          # L2-normalised, DIM long
    quality: float              # 0-1, feeds ModalityReading.quality
    duration_s: float
    voiced_ratio: float
    snr_db: float


def _quality(voiced_ratio: float, snr_db: float, duration_s: float) -> float:
    """How much this capture deserves to be believed.

    Three independent ways a capture fails: too little speech in it, too much
    noise, too short. The product, not the mean — a capture that is fine on two
    counts and hopeless on the third is hopeless.
    """
    speech = min(1.0, voiced_ratio / 0.35)
    noise = float(np.clip((snr_db - 5.0) / 20.0, 0.0, 1.0))
    length = float(np.clip(duration_s / (2 * MIN_DURATION_S), 0.0, 1.0))
    return float(np.clip(speech * noise * length, 0.0, 1.0))


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
    snr_db = float(peak - np.median(energy[~voiced])) if (~voiced).any() else 40.0

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
        quality=_quality(voiced_ratio, snr_db, duration_s),
        duration_s=duration_s, voiced_ratio=voiced_ratio, snr_db=snr_db)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_voice_encoder.py -q`
Expected: PASS — 9 passed

If `test_a_fixed_channel_colouring_is_normalised_away` or
`test_same_voice_twice_matches_itself` fails, the cause is almost always the
voiced-frame selection dropping too many frames on synthetic audio; check
`voiced_ratio` in the failure output before changing thresholds.

- [ ] **Step 5: Commit**

```bash
git add services/audio/voice/encoder.py tests/unit/test_voice_encoder.py
git commit -m "feat(voice): speaker embedding from cepstral statistics

Classical, not a trained speaker network — materially weaker than an ECAPA-TDNN
and what the installed dependencies actually allow. Its job is to make voice a
real modality end to end behind a contract a neural encoder can replace; the
fusion engine's quality floor then decides per capture whether it may vote,
which is a weak stream being excluded rather than diluting a strong one.

Two removals, both with precedent. c0 is dropped: it is log energy, so it tracks
microphone gain and speaker distance rather than the speaker — the same mistake
the gait encoder found in FFT absolute phase, which moved with the start frame.
And cepstral mean normalisation removes the channel's fixed colouring, without
which the embedding largely encodes which microphone was used."
```

---

### Task 4: Text-dependent verification on the national code

**Why this is the phase's centrepiece:** at the Rx desk the customer already speaks their national code for the insurance lookup. Verifying *that* utterance is text-dependent — materially more accurate than text-independent — on speech produced anyway. One action then serves three purposes: the insurance lookup, the IAL2 non-biometric factor, and a voice sample.

**The rule this encodes:** the digits and the voice are checked **together**, and neither alone suffices. Matching digits with a stranger's voice is someone reading a code they were told. A matching voice with wrong digits is the right person misspeaking, or the ASR erring at ~14% WER. Both are *review*, not acceptance.

**Files:**
- Create: `services/audio/voice/text_dependent.py`
- Test: `tests/unit/test_voice_text_dependent.py`

**Interfaces:**
- Consumes: `encoder.encode`, `encoder.VoiceEmbedding` (Task 3).
- Produces: `verify_spoken_code(transcript: str, expected_code: str, embedding, enrolled_vector, *, min_similarity: float = 0.65) -> CodeVerification`, where `CodeVerification` is a frozen dataclass with `digits_match: bool`, `voice_similarity: float`, `outcome: str` (`"verified" | "review" | "rejected"`), `spoken_digits: str`, `reason: str`. Also `normalise_digits(text: str) -> str` folding Persian/Arabic-Indic digits to ASCII.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_voice_text_dependent.py`:

```python
"""Verifying the national code the customer already speaks.

Text-dependent verification on a fixed phrase, which is more accurate than
text-independent — and the phrase is one the customer produces anyway for the
insurance lookup, so nothing has to be adopted.

The rule under test: digits and voice are checked TOGETHER. Matching digits in a
stranger's voice is someone reading a code they were told. A matching voice with
wrong digits is a misspeak or the ASR erring at ~14% WER on clean Persian. Both
are review, never acceptance.
"""
from __future__ import annotations

import numpy as np
import pytest

from services.audio.voice.text_dependent import (
    CodeVerification, normalise_digits, verify_spoken_code)

CODE = "0079542685"


def vec(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(48).astype(np.float32)
    return v / np.linalg.norm(v)


def near(v: np.ndarray, sim: float, seed: int = 99) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = rng.standard_normal(v.shape).astype(np.float32)
    n = n - float(np.dot(n, v)) * v
    n = n / np.linalg.norm(n)
    w = sim * v + np.sqrt(max(0.0, 1 - sim * sim)) * n
    return (w / np.linalg.norm(w)).astype(np.float32)


class FakeEmbedding:
    def __init__(self, vector, quality=0.8):
        self.vector, self.quality = vector, quality


# ── digit normalisation ──────────────────────────────────────────────────

def test_persian_digits_fold_to_ascii():
    assert normalise_digits("۰۰۷۹۵۴۲۶۸۵") == "0079542685"


def test_arabic_indic_digits_fold_to_ascii():
    assert normalise_digits("٠٠٧٩٥٤٢٦٨٥") == "0079542685"


def test_separators_and_words_are_stripped():
    assert normalise_digits("کد ملی من ۰۰۷۹-۵۴۲ ۶۸۵ است") == "0079542685"


# ── the two factors together ─────────────────────────────────────────────

def test_matching_digits_and_matching_voice_verifies():
    enrolled = vec(1)
    r = verify_spoken_code("۰۰۷۹۵۴۲۶۸۵", CODE,
                           FakeEmbedding(near(enrolled, 0.88)), enrolled)
    assert isinstance(r, CodeVerification)
    assert r.outcome == "verified"
    assert r.digits_match is True


def test_matching_digits_with_a_strangers_voice_is_not_verified():
    """Someone reading out a code they were told."""
    enrolled = vec(1)
    r = verify_spoken_code("۰۰۷۹۵۴۲۶۸۵", CODE,
                           FakeEmbedding(vec(2)), enrolled)
    assert r.outcome != "verified"
    assert r.digits_match is True
    assert "voice" in r.reason.lower()


def test_matching_voice_with_wrong_digits_is_review_not_rejection():
    """A misspeak, or the ASR erring — the person may well be right."""
    enrolled = vec(1)
    r = verify_spoken_code("۰۰۷۹۵۴۲۶۸۴", CODE,
                           FakeEmbedding(near(enrolled, 0.9)), enrolled)
    assert r.outcome == "review"
    assert r.digits_match is False


def test_both_wrong_is_rejected():
    enrolled = vec(1)
    r = verify_spoken_code("۱۲۳۴۵۶۷۸۹۰", CODE, FakeEmbedding(vec(3)), enrolled)
    assert r.outcome == "rejected"


def test_a_poor_capture_never_verifies_however_well_it_scores():
    """Below the capture-quality floor the similarity is not evidence."""
    enrolled = vec(1)
    r = verify_spoken_code("۰۰۷۹۵۴۲۶۸۵", CODE,
                           FakeEmbedding(near(enrolled, 0.99), quality=0.05),
                           enrolled)
    assert r.outcome != "verified"
    assert "quality" in r.reason.lower()


def test_no_enrolment_yields_review_not_rejection():
    """A first-time caller has no voice on file. That is not a failure."""
    r = verify_spoken_code("۰۰۷۹۵۴۲۶۸۵", CODE,
                           FakeEmbedding(vec(4)), enrolled_vector=None)
    assert r.outcome == "review"
    assert "enrol" in r.reason.lower()


def test_spoken_digits_are_reported_for_the_audit_trail():
    enrolled = vec(1)
    r = verify_spoken_code("کد من ۰۰۷۹۵۴۲۶۸۵", CODE,
                           FakeEmbedding(near(enrolled, 0.9)), enrolled)
    assert r.spoken_digits == CODE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_voice_text_dependent.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.audio.voice.text_dependent'`

- [ ] **Step 3: Write the implementation**

Create `services/audio/voice/text_dependent.py`:

```python
"""Verify the national code the customer already speaks.

At the Rx desk the customer states their national code for the insurance
lookup. Verifying that utterance is TEXT-DEPENDENT — a fixed phrase, which is
materially more accurate than text-independent verification — on speech
produced anyway. One action serves the insurance lookup, the IAL2 non-biometric
factor, and a voice sample, and nothing has to be adopted.

THE RULE: the digits and the voice are checked TOGETHER and neither alone
suffices.

  matching digits + stranger's voice  -> someone reading a code they were told
  matching voice + wrong digits       -> a misspeak, or ASR error (~14% WER on
                                         clean Persian is the honest ceiling)

Both are review. Only both-correct verifies, and even then this is one factor
toward IAL2 — never an identity assertion on its own.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

VERIFIED = "verified"
REVIEW = "review"
REJECTED = "rejected"

# Below this the capture is not evidence, whatever it scores. Mirrors the voice
# floor in services.biometric.fusion.
MIN_CAPTURE_QUALITY = 0.45

_PERSIAN = "۰۱۲۳۴۵۶۷۸۹"
_ARABIC = "٠١٢٣٤٥٦٧٨٩"
_FOLD = {ord(c): str(i) for i, c in enumerate(_PERSIAN)}
_FOLD.update({ord(c): str(i) for i, c in enumerate(_ARABIC)})


@dataclass(frozen=True)
class CodeVerification:
    digits_match: bool
    voice_similarity: float
    outcome: str
    spoken_digits: str
    reason: str


def normalise_digits(text: str) -> str:
    """Persian and Arabic-Indic digits folded to ASCII, everything else dropped."""
    return re.sub(r"\D", "", (text or "").translate(_FOLD))


def verify_spoken_code(transcript: str, expected_code: str, embedding,
                       enrolled_vector, *,
                       min_similarity: float = 0.65) -> CodeVerification:
    """Check the digits and the voice together."""
    spoken = normalise_digits(transcript)
    expected = normalise_digits(expected_code)
    digits_match = bool(spoken) and spoken == expected

    quality = float(getattr(embedding, "quality", 0.0))
    sim = 0.0
    if enrolled_vector is not None:
        a = np.asarray(embedding.vector, dtype=np.float32).reshape(-1)
        b = np.asarray(enrolled_vector, dtype=np.float32).reshape(-1)
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        if na > 0 and nb > 0:
            sim = float(np.dot(a / na, b / nb))

    if enrolled_vector is None:
        return CodeVerification(
            digits_match, sim, REVIEW, spoken,
            "no voice enrolment on file for this person — a first-time caller "
            "is not a failure; verify by other means and offer enrolment")

    if quality < MIN_CAPTURE_QUALITY:
        return CodeVerification(
            digits_match, sim, REVIEW, spoken,
            f"capture quality {quality:.2f} is below the "
            f"{MIN_CAPTURE_QUALITY} floor, so the similarity is not evidence")

    voice_ok = sim >= min_similarity
    if digits_match and voice_ok:
        return CodeVerification(
            digits_match, sim, VERIFIED, spoken,
            f"digits match and voice similarity {sim:.2f} clears "
            f"{min_similarity}")
    if digits_match and not voice_ok:
        return CodeVerification(
            digits_match, sim, REVIEW, spoken,
            f"digits match but voice similarity {sim:.2f} is below "
            f"{min_similarity} — consistent with someone reading out a code "
            f"they were given")
    if voice_ok and not digits_match:
        return CodeVerification(
            digits_match, sim, REVIEW, spoken,
            f"voice matches ({sim:.2f}) but the spoken digits {spoken!r} differ "
            f"from {expected!r} — a misspeak or a transcription error")
    return CodeVerification(
        digits_match, sim, REJECTED, spoken,
        f"neither the digits nor the voice match (similarity {sim:.2f})")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_voice_text_dependent.py -q`
Expected: PASS — 10 passed

- [ ] **Step 5: Commit**

```bash
git add services/audio/voice/text_dependent.py tests/unit/test_voice_text_dependent.py
git commit -m "feat(voice): text-dependent verification on the spoken national code

At the Rx desk the customer already states their national code for the insurance
lookup, so verifying that utterance is text-dependent — more accurate than
text-independent — on speech produced anyway.

Digits and voice are checked together and neither alone suffices. Matching
digits in a stranger's voice is someone reading a code they were told; a
matching voice with wrong digits is a misspeak or the ASR erring at ~14% WER.
Both are review, never acceptance. A capture below the quality floor cannot
verify however well it scores, and a first-time caller with no enrolment is a
review rather than a rejection."
```

---

### Task 5: Diarized segment to fusion reading

**Files:**
- Create: `services/audio/voice/readings.py`
- Test: `tests/unit/test_voice_readings.py`

**Interfaces:**
- Consumes: `encoder.VoiceEmbedding` (Task 3), `services.biometric.fusion.ModalityReading`, `services.biometric.identity_resolution.thresholds.ImpostorStats`.
- Produces: `segment_to_reading(embedding, gallery_hits, stats, gallery_size) -> ModalityReading | None`, where `gallery_hits` is a list of `(identity_id, similarity)` sorted best-first; returns `None` when there is no hit to report.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_voice_readings.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_voice_readings.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.audio.voice.readings'`

- [ ] **Step 3: Write the implementation**

Create `services/audio/voice/readings.py`:

```python
"""Diarized speech segment → a fusion reading.

Voice enters fusion through exactly the same contract as face, gait and
periocular. There is no special case: the floors, the calibration requirement
and the exclusion rules in services.biometric.fusion are authoritative, and a
voice capture below its floor is dropped like any other.

The measured capture quality travels into the reading unchanged, because that
number is what the floor acts on. Substituting an optimistic constant here
would defeat the exclusion rule the fusion engine depends on.
"""
from __future__ import annotations

from uuid import UUID

MODALITY = "voice"


def segment_to_reading(embedding, gallery_hits: list[tuple[UUID, float]],
                       stats, gallery_size: int):
    """Best gallery hit for this segment as a `ModalityReading`, or None.

    `stats=None` is passed through deliberately: the fusion engine treats an
    uncalibrated modality as one that may not vote, which is the correct
    default and must not be papered over here.
    """
    from services.biometric.fusion import ModalityReading

    if not gallery_hits:
        return None
    identity_id, similarity = max(gallery_hits, key=lambda h: h[1])
    return ModalityReading(
        modality=MODALITY,
        identity_id=identity_id,
        similarity=float(similarity),
        quality=float(getattr(embedding, "quality", 0.0)),
        stats=stats,
        gallery_size=int(gallery_size))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_voice_readings.py tests/unit/test_biometric_fusion.py -q`
Expected: PASS — 6 new plus the existing fusion tests

- [ ] **Step 5: Commit**

```bash
git add services/audio/voice/readings.py tests/unit/test_voice_readings.py
git commit -m "feat(voice): diarized segment becomes a fusion reading

Voice enters fusion through the same contract as every other modality — no
special case. The floors, the calibration requirement and the exclusion rules
are authoritative, and the measured capture quality travels into the reading
unchanged because that number is what the floor acts on."
```

---

### Task 6: Full-suite verification and ledger

- [ ] **Step 1: Run the whole unit suite**

Do **not** export `DATABASE_URL`. If the run exceeds a few minutes, check
`pharmpilot_test` for accumulated `inventory_exceptions` rows before assuming a
code fault — that table's fixtures soft-delete rather than truncate and it
reached 6.1 GB during phase 1.

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit -q --no-header`
Expected: all pass except the known order-dependent
`test_integrations_sandbox.py::test_notifications_sandbox_success_shape_no_network_and_masked_logs`,
which is green in isolation.

- [ ] **Step 2: Confirm no new unauthenticated routes**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_route_authentication.py -q`
Expected: PASS

- [ ] **Step 3: Append the ledger entry and commit**

Append to `docs/ai-context/AI_COLLABORATION.md` using the template at the bottom
of that file: branch/commit, files and behaviour changed, checks actually run,
remaining risks, next action.

```bash
git add docs/ai-context/AI_COLLABORATION.md
git commit -m "docs: ledger entry for the voice modality"
```

---

## What this phase deliberately does not do

**No neural speaker encoder.** ECAPA-TDNN needs `speechbrain` or `onnxruntime`; neither is installed, and this phase adds no dependencies. The classical embedding is real and testable, and `encoder.encode` is the seam a neural model replaces without touching anything downstream.

**No far-field voice.** Measured speaker EER is 2.33% at 0.5 m and 14.66% at 5 m with RT60 1.5 s. The waiting-area microphone stays scoped to non-identifying security signals, as recorded in `SURVEILLANCE_PLATFORM.md` invariant I-8.

**No enrolment UI.** Storing a voice template uses the existing `repository.enrol_template` with `modality="voice"`, which migration 0033 already admits. The interface for capturing enrolment consent belongs with the consent ledger, which is phase 3.

**No calibration.** Voice `ImpostorStats` must be measured on real captures at the real counters. Until then `stats=None` and the fusion engine excludes voice from voting — which is the correct default, not a gap.
