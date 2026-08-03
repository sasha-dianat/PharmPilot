"""The multi-template, multi-modal vector store.

The properties under test are the ones the accuracy claim rests on: enrolling a
person more than once must actually help, a person must never be their own
runner-up, and a template must never cross between modalities.

Deciding what a match *means* is not tested here — that belongs to
`services.biometric.fusion`, which already owns it.
"""
from __future__ import annotations

import uuid

import numpy as np
import pytest

from services.biometric.identity_resolution import vector_store as V


def unit(seed: int, dim: int = 512, drift: float = 0.0) -> np.ndarray:
    """A reproducible unit vector; `drift` moves it slightly off the original."""
    rng = np.random.default_rng(seed)
    v = rng.normal(size=dim)
    if drift:
        v = v / np.linalg.norm(v) + drift * rng.normal(size=dim)
    return (v / np.linalg.norm(v)).astype(np.float32)


def row(identity, vec, quality=1.0):
    return {"identity_id": identity, "template_id": uuid.uuid4(),
            "embedding": vec, "quality": quality}


def index_with(rows, modality=V.FACE, dim=512):
    idx = V.ModalityIndex(modality=modality, dim=dim)
    idx.load(rows)
    return idx


# ── the limit this module exists to lift ──────────────────────────────────
def test_a_person_can_hold_many_templates_in_one_modality():
    """The old model had one embedding column per modality, so enrolment could
    never improve — each capture overwrote the last."""
    alice = uuid.uuid4()
    idx = index_with([row(alice, unit(1)), row(alice, unit(1, drift=0.3)),
                      row(alice, unit(1, drift=0.6))])
    assert len(idx) == 3 and idx.identity_count == 1


def test_extra_enrolments_improve_the_match_they_are_meant_to():
    """A probe resembling the second capture should score better once that
    capture is enrolled. This is the whole reason multi-template exists."""
    alice = uuid.uuid4()
    probe = unit(1, drift=0.55)
    one = index_with([row(alice, unit(1))])
    many = index_with([row(alice, unit(1)), row(alice, unit(1, drift=0.5))])
    assert many.search(probe)[0].similarity > one.search(probe)[0].similarity


def test_a_person_is_never_their_own_runner_up():
    """Best hit PER IDENTITY. Otherwise someone enrolled five times fills the
    top five results and the margin test becomes impossible to compute."""
    alice, bob = uuid.uuid4(), uuid.uuid4()
    idx = index_with([row(alice, unit(1)), row(alice, unit(1, drift=0.1)),
                      row(alice, unit(1, drift=0.2)), row(bob, unit(9))])
    hits = idx.search(unit(1), top_k=5)
    assert len(hits) == 2                       # two people, not four templates
    assert [h.identity_id for h in hits] == [alice, bob]


def test_search_ranks_the_right_person_first():
    people = [uuid.uuid4() for _ in range(6)]
    idx = index_with([row(p, unit(10 + i)) for i, p in enumerate(people)])
    for i, p in enumerate(people):
        assert idx.search(unit(10 + i))[0].identity_id == p


def test_an_empty_index_returns_nothing_rather_than_guessing():
    assert V.ModalityIndex(modality=V.FACE, dim=512).search(unit(1)) == []


# ── dimension discipline ──────────────────────────────────────────────────
def test_a_wrong_sized_template_is_refused_at_enrolment():
    with pytest.raises(V.VectorStoreError, match="dimension"):
        index_with([row(uuid.uuid4(), unit(1, dim=128))])


def test_a_wrong_sized_probe_is_refused():
    idx = index_with([row(uuid.uuid4(), unit(1))])
    with pytest.raises(V.VectorStoreError, match="probe dimension"):
        idx.search(unit(1, dim=64))


def test_a_gait_vector_cannot_be_enrolled_into_the_face_index():
    """Gait is 64-d, face 512-d. Mixing them would produce similarities that
    mean nothing at all."""
    with pytest.raises(V.VectorStoreError):
        index_with([row(uuid.uuid4(), unit(1, dim=V.EXPECTED_DIM[V.GAIT]))])


def test_a_degenerate_template_is_refused():
    with pytest.raises(V.VectorStoreError, match="degenerate"):
        index_with([row(uuid.uuid4(), np.zeros(512, np.float32))])


# ── serialisation must be lossless ────────────────────────────────────────
def test_a_template_survives_a_storage_round_trip_exactly():
    v = unit(3)
    assert np.array_equal(V.decode_vector(V.encode_vector(v), 512), v)


def test_a_truncated_template_is_detected_not_reshaped():
    raw = V.encode_vector(unit(3))
    with pytest.raises(V.VectorStoreError, match="decoded dimension"):
        V.decode_vector(raw[:-8], 512)


def test_non_finite_values_never_reach_storage():
    bad = unit(3).copy()
    bad[0] = np.nan
    with pytest.raises(V.VectorStoreError, match="non-finite"):
        V.encode_vector(bad)


# ── handing off to the existing fusion engine ─────────────────────────────
def test_hits_convert_into_readings_the_fusion_engine_accepts():
    """This module stores and searches; `services.biometric.fusion` decides.
    An earlier draft grew a second fusion implementation here and it was removed
    — two engines disagreeing about one identity is worse than either alone."""
    from services.biometric.fusion import ModalityReading, fuse

    alice = uuid.uuid4()
    readings = V.to_readings(
        {V.FACE: [V.Hit(alice, 0.82, uuid.uuid4(), V.FACE, 0.9)]},
        stats_by_modality={}, quality_by_modality={V.FACE: 0.9})
    assert len(readings) == 1
    assert isinstance(readings[0], ModalityReading)
    assert readings[0].modality == V.FACE and readings[0].identity_id == alice
    # uncalibrated (stats=None) must be excluded by the engine, not silently used
    assert fuse(readings).decision == "no_match"


def test_an_uncalibrated_modality_is_passed_through_as_uncalibrated():
    readings = V.to_readings(
        {V.GAIT: [V.Hit(uuid.uuid4(), 0.9, uuid.uuid4(), V.GAIT, 0.8)]},
        stats_by_modality={})
    assert readings[0].stats is None
