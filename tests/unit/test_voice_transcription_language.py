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


def test_no_english_only_model_is_named_anywhere_in_services():
    """`.en` suffixed models cannot transcribe Persian at all.

    Scans the whole services tree rather than a hand-listed set of files: the
    first version of this test listed three files and missed two — a zone config
    and a router call site. A defect class needs a search, not a list.
    """
    offenders = []
    for p in sorted(Path("services").rglob("*.py")):
        src = p.read_text(encoding="utf-8")
        for m in re.finditer(r'=\s*"(tiny|base|small|medium|large)\.en"', src):
            offenders.append(f"{p}: {m.group(0)}")
    # MODEL_SIZES documents that the .en variants exist; it does not select one.
    offenders = [o for o in offenders if "MODEL_SIZES" not in o]
    assert not offenders, (
        "English-only Whisper models selected here; on Persian they emit "
        "invented English rather than failing:\n  " + "\n  ".join(offenders))


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
