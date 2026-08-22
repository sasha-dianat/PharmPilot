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
