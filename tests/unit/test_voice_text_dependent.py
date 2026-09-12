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
