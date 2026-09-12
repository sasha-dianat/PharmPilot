"""Gait embeddings from pose sequences.

The tests that matter are discrimination tests: the same person walking twice
must score higher against themselves than against someone built differently.
A descriptor that fails that is decoration, however clean its code.

Skeletons are synthesised rather than recorded, so the properties under test are
the ones the descriptor claims — limb proportion, joint range, cadence — and not
an artefact of one video.
"""
from __future__ import annotations

import numpy as np
import pytest

from services.biometric.gait import encoder as G


def walk(*, frames: int = 60, thigh: float = 0.45, shin: float = 0.42,
         shoulder_w: float = 0.40, hip_w: float = 0.26, cadence: float = 0.18,
         swing: float = 0.35, arm: float = 0.30, seed: int = 0,
         noise: float = 0.004, moving: bool = True) -> np.ndarray:
    """Synthesise a COCO-17 keypoint sequence for one walking person.

    Body proportions are constants of the person; the phase term is the gait
    cycle. Two calls with the same proportions and different seeds are the same
    person walking twice.
    """
    rng = np.random.default_rng(seed)
    out = np.zeros((frames, 17, 3), dtype=np.float64)
    for t in range(frames):
        ph = 2 * np.pi * cadence * t
        sw = (swing * np.sin(ph)) if moving else 0.0
        kn = (0.25 * swing * np.sin(ph + 0.6)) if moving else 0.0

        pelvis = np.array([0.0, 0.0])
        neck = pelvis + np.array([0.0, -0.55])
        pts = {
            G.NOSE: neck + [0.0, -0.18],
            1: neck + [-0.04, -0.20], 2: neck + [0.04, -0.20],
            3: neck + [-0.08, -0.17], 4: neck + [0.08, -0.17],
            G.L_SHOULDER: neck + [-shoulder_w / 2, 0.0],
            G.R_SHOULDER: neck + [shoulder_w / 2, 0.0],
            G.L_ELBOW: neck + [-shoulder_w / 2 - 0.02, arm * 0.55 + 0.02 * sw],
            G.R_ELBOW: neck + [shoulder_w / 2 + 0.02, arm * 0.55 - 0.02 * sw],
            G.L_WRIST: neck + [-shoulder_w / 2 - 0.03, arm * 1.05 + 0.03 * sw],
            G.R_WRIST: neck + [shoulder_w / 2 + 0.03, arm * 1.05 - 0.03 * sw],
            G.L_HIP: pelvis + [-hip_w / 2, 0.0],
            G.R_HIP: pelvis + [hip_w / 2, 0.0],
            G.L_KNEE: pelvis + [-hip_w / 2 + sw * 0.5, thigh - abs(kn)],
            G.R_KNEE: pelvis + [hip_w / 2 - sw * 0.5, thigh - abs(kn)],
            G.L_ANKLE: pelvis + [-hip_w / 2 + sw, thigh + shin],
            G.R_ANKLE: pelvis + [hip_w / 2 - sw, thigh + shin],
        }
        for idx, xy in pts.items():
            out[t, idx, :2] = np.asarray(xy) + rng.normal(0, noise, 2)
            out[t, idx, 2] = 0.9
    return out


# ── it must produce a usable embedding at all ─────────────────────────────
def test_a_normal_walk_yields_a_unit_length_embedding():
    vec, quality = G.encode(walk())
    assert vec.shape == (G.EMBED_DIM,)
    assert np.isclose(np.linalg.norm(vec), 1.0, atol=1e-5)
    assert quality.usable and quality.is_moving


def test_the_embedding_is_deterministic():
    """Same input, same vector — a biometric that drifts between calls cannot
    be enrolled."""
    seq = walk(seed=7)
    a, _ = G.encode(seq)
    b, _ = G.encode(seq)
    assert np.array_equal(a, b)


# ── discrimination: the property the module exists for ────────────────────
def test_the_same_person_walking_twice_matches_better_than_a_different_build():
    me_1, _ = G.encode(walk(seed=1))
    me_2, _ = G.encode(walk(seed=2))                       # same body, new walk
    other, _ = G.encode(walk(thigh=0.30, shin=0.55, shoulder_w=0.52,
                             hip_w=0.34, arm=0.24, seed=3))
    same = G.similarity(me_1, me_2)
    diff = G.similarity(me_1, other)
    assert same > diff, f"genuine {same:.4f} did not beat impostor {diff:.4f}"
    assert same > 0.95


def test_body_proportions_separate_people_who_walk_identically():
    """Anthropometry is the view- and speed-stable part of the descriptor."""
    a, _ = G.encode(walk(thigh=0.50, shin=0.38, seed=4))
    b, _ = G.encode(walk(thigh=0.34, shin=0.54, seed=4))   # identical gait phase
    assert G.similarity(a, b) < 0.999


def test_cadence_separates_people_with_the_same_build():
    slow, _ = G.encode(walk(cadence=0.10, seed=5))
    fast, _ = G.encode(walk(cadence=0.28, seed=5))
    assert G.similarity(slow, fast) < 1.0


def _population(n=8):
    """Distinct builds AND distinct walks. Real people differ in range of
    motion as well as proportion; a population that varies only proportion
    would let a proportion-only descriptor pass a test it should fail."""
    return [dict(thigh=0.32 + 0.03 * i, shin=0.56 - 0.03 * i,
                 shoulder_w=0.32 + 0.025 * i, hip_w=0.20 + 0.018 * i,
                 arm=0.22 + 0.014 * i, cadence=0.10 + 0.022 * i,
                 swing=0.22 + 0.035 * i)
            for i in range(n)]


def test_every_probe_ranks_its_own_enrolment_first():
    """Rank-1 identification on the raw descriptor, with no fitted population.
    This is the weakest useful claim and it must hold unconditionally."""
    people = _population()
    enrolled = [G.encode(walk(seed=10 + i, **p))[0] for i, p in enumerate(people)]
    probes = [G.encode(walk(seed=100 + i, **p))[0] for i, p in enumerate(people)]
    for i, probe in enumerate(probes):
        best = int(np.argmax([G.similarity(probe, t) for t in enrolled]))
        assert best == i, f"probe {i} matched enrolment {best}"


def test_genuine_and_impostor_score_distributions_do_not_overlap():
    """Rank-1 alone cannot set a threshold; for that the two distributions must
    separate. Measured margin on this population is about +0.18, and a
    regression here means a threshold can no longer be chosen at all."""
    people = _population()
    enrolled = [G.encode(walk(seed=10 + i, **p))[0] for i, p in enumerate(people)]
    probes = [G.encode(walk(seed=100 + i, **p))[0] for i, p in enumerate(people)]

    genuine, impostor = [], []
    for i, probe in enumerate(probes):
        for j, tmpl in enumerate(enrolled):
            (genuine if i == j else impostor).append(G.similarity(probe, tmpl))

    assert min(genuine) > max(impostor), (
        f"distributions overlap: worst genuine {min(genuine):.4f} "
        f"<= best impostor {max(impostor):.4f}")
    assert min(genuine) - max(impostor) > 0.05


def test_the_cycle_description_survives_starting_mid_stride():
    """A camera catches a walk at an arbitrary point in the cycle. FFT *phase*
    was used here once and scored -0.15 for the same person under a time shift;
    magnitude plus autocorrelation scores about 0.99."""
    people = _population()
    seq = walk(seed=10, **people[0])
    full, _ = G.encode(seq)
    mid, _ = G.encode(seq[7:])
    assert G.similarity(full, mid) > 0.95


# ── refusing to answer is a feature ───────────────────────────────────────
def test_standing_still_is_refused_rather_than_scored():
    """A stationary person has no gait. Emitting a confident vector here is how
    a bench acquires an identity."""
    with pytest.raises(G.GaitError, match="not walking"):
        G.encode(walk(moving=False))
    assert G.assess(walk(moving=False)).usable is False


def test_too_few_frames_is_refused():
    with pytest.raises(G.GaitError, match="usable frames"):
        G.encode(walk(frames=5))


def test_occluded_legs_make_the_sequence_unusable():
    seq = walk()
    seq[:, G.L_ANKLE, 2] = 0.05           # ankle never confidently seen
    q = G.assess(seq)
    assert q.usable is False and q.usable_frames == 0


def test_partial_occlusion_still_works_if_enough_frames_survive():
    seq = walk(frames=60)
    seq[:20, G.R_KNEE, 2] = 0.05          # a third of the walk is obscured
    vec, q = G.encode(seq)
    assert q.usable and q.usable_frames == 40
    assert np.isclose(np.linalg.norm(vec), 1.0, atol=1e-5)


def test_a_malformed_array_is_rejected_not_guessed():
    with pytest.raises(G.GaitError, match="frames, 17, 3"):
        G.encode(np.zeros((10, 5)))


def test_a_degenerate_skeleton_is_rejected():
    seq = walk(frames=30)
    seq[:, :, :2] = 0.0                   # everything collapsed to one point
    with pytest.raises(G.GaitError):
        G.encode(seq)


# ── invariances the descriptor claims ─────────────────────────────────────
def test_camera_distance_does_not_change_the_embedding():
    """Scale normalisation by torso length: the same person filmed closer must
    not read as a different person."""
    near = walk(seed=9)
    far = near.copy()
    far[:, :, :2] *= 0.4                  # same person, further from the camera
    a, _ = G.encode(near)
    b, _ = G.encode(far)
    assert G.similarity(a, b) > 0.999


def test_position_in_frame_does_not_change_the_embedding():
    left = walk(seed=11)
    right = left.copy()
    right[:, :, 0] += 3.0                 # walked across the room
    a, _ = G.encode(left)
    b, _ = G.encode(right)
    assert G.similarity(a, b) > 0.999


def test_similarity_rejects_a_dimension_mismatch():
    vec, _ = G.encode(walk())
    with pytest.raises(G.GaitError, match="dimension mismatch"):
        G.similarity(vec, np.zeros(32, dtype=np.float32))


def test_quality_is_reportable_for_audit():
    _, q = G.encode(walk())
    d = q.as_dict()
    assert d["usable"] is True and d["frames"] == 60 and d["mean_visibility"] > 0.8


# ── feeding the fusion engine ─────────────────────────────────────────────
def test_quality_score_is_zero_when_the_sequence_is_unusable():
    """`services.biometric.fusion` admits gait only above 0.75. An unusable
    capture must score 0, not a small positive number that might squeak in."""
    assert G.assess(walk(moving=False)).quality_score() == 0.0
    assert G.assess(walk(frames=5)).quality_score() == 0.0


def test_a_good_long_walk_clears_the_fusion_floor():
    from services.biometric.fusion import FUSION_FLOORS
    _, q = G.encode(walk(frames=90))
    assert q.quality_score() >= FUSION_FLOORS["gait"].min_quality


def test_quality_is_weakest_link_not_an_average():
    """A long, clear sequence with barely any gait cycle must not pass on the
    strength of its other two terms."""
    _, q = G.encode(walk(frames=90, cadence=0.012))   # long, clear, almost no cycle
    assert q.cycles_observed <= 1.0
    assert q.quality_score() < 0.75


def test_the_quality_score_is_reported_for_audit():
    _, q = G.encode(walk(frames=90))
    assert q.as_dict()["quality_score"] == q.quality_score()
