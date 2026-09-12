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
