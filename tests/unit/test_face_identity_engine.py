"""The five defects that made face identification unsafe, as regression tests.

Read these as the specification for what "identify every visitor without making
mistakes" actually requires. Each test names the defect it locks down:

  D1 thresholds were probability-shaped constants applied to raw cosine
  D2 k=1 search made the top1/top2 margin test structurally impossible
  D3 the FAISS position -> identity map was never persisted, so identification
     silently resolved to None after a restart
  D4 no remove path: an enrolled template could never be withdrawn
  D5 liveness failed OPEN — and with skimage absent it failed open every time

No ONNX or FAISS required: the matching logic is exercised on embeddings
directly, which is also how it should have been testable in the first place.
"""
from __future__ import annotations

from uuid import uuid4

import numpy as np
import pytest

from services.biometric.identity_resolution.engine import (
    IdentityResolutionEngine, LivenessDetector)
from services.biometric.identity_resolution.gallery import VectorGallery
from services.biometric.identity_resolution.thresholds import (
    ImpostorStats, threshold_for_gallery)

DIM = 512


def unit(rng, d: int = DIM) -> np.ndarray:
    v = rng.standard_normal(d).astype(np.float32)
    return (v / np.linalg.norm(v)).astype(np.float32)


def at_cosine(rng, anchor: np.ndarray, sim: float) -> np.ndarray:
    """A unit vector at (almost exactly) cosine `sim` from `anchor`."""
    n = unit(rng, anchor.shape[0])
    n = n - float(np.dot(n, anchor)) * anchor      # orthogonal component
    n = n / np.linalg.norm(n)
    w = sim * anchor + np.sqrt(max(0.0, 1.0 - sim * sim)) * n
    return (w / np.linalg.norm(w)).astype(np.float32)


@pytest.fixture
def rng():
    return np.random.default_rng(20260727)


# Measured impostor statistics stand in for a real calibration run. Values are
# deliberately explicit — the engine refuses to auto-accept without them.
CALIBRATED = ImpostorStats(mean=0.02, std=0.075, sample_size=250_000)


# ── D3 · D4: the gallery must persist its mapping and support withdrawal ──

def test_gallery_survives_save_and_load(rng, tmp_path):
    """D3 — the restart bug. Persisting vectors without the identity map made
    every post-restart match resolve to None: silent, not loud."""
    g = VectorGallery(dim=DIM)
    alice, bob = uuid4(), uuid4()
    va, vb = unit(rng), unit(rng)
    g.add(alice, va)
    g.add(bob, vb)

    path = tmp_path / "gallery"
    g.save(path)

    restored = VectorGallery(dim=DIM)
    restored.load(path)
    assert restored.size == 2 and restored.identity_count == 2
    hit = restored.search(va, k=5)[0]
    assert hit.identity_id == alice          # NOT None
    assert hit.similarity == pytest.approx(1.0, abs=1e-5)


def test_removed_identity_is_no_longer_matchable(rng, tmp_path):
    """D4 — an enrolled template must be withdrawable, for un-enrolment and for
    an erasure request. Positional ids in a flat index made this impossible."""
    g = VectorGallery(dim=DIM)
    alice, bob = uuid4(), uuid4()
    va = unit(rng)
    g.add(alice, va)
    g.add(bob, unit(rng))

    assert g.remove(alice) == 1
    assert g.identity_count == 1
    assert all(h.identity_id != alice for h in g.search(va, k=5))
    # and the removal must survive a round-trip, not reappear from disk
    g.save(tmp_path / "g")
    restored = VectorGallery(dim=DIM)
    restored.load(tmp_path / "g")
    assert all(h.identity_id != alice for h in restored.search(va, k=5))


def test_removing_an_identity_drops_all_of_its_templates(rng):
    """Multiple enrolment images per person is how accuracy is won; erasure must
    still be complete."""
    g = VectorGallery(dim=DIM)
    alice = uuid4()
    anchor = unit(rng)
    for sim in (1.0, 0.82, 0.77):
        g.add(alice, at_cosine(rng, anchor, sim))
    assert g.size == 3 and g.identity_count == 1
    assert g.remove(alice) == 3
    assert g.size == 0


def test_search_returns_best_hit_per_identity(rng):
    """D2 — the margin test compares DISTINCT people. If one person's three
    templates occupy the top three slots, a naive top1/top2 margin is ~0 and
    every good match is thrown away."""
    g = VectorGallery(dim=DIM)
    alice, bob = uuid4(), uuid4()
    anchor = unit(rng)
    for sim in (0.91, 0.88, 0.85):
        g.add(alice, at_cosine(rng, anchor, sim))
    g.add(bob, at_cosine(rng, anchor, 0.40))

    hits = g.search(anchor, k=10)
    assert [h.identity_id for h in hits] == [alice, bob]     # one row per person
    assert hits[0].similarity == pytest.approx(0.91, abs=1e-3)


def test_rebuild_from_database_rows(rng):
    """The map's source of truth is the DB (biometric_identities.faiss_index_id),
    so a cold start must be able to reconstruct without the sidecar file."""
    alice, bob = uuid4(), uuid4()
    va, vb = unit(rng), unit(rng)
    g = VectorGallery(dim=DIM)
    g.rebuild([(alice, 1, va), (bob, 2, vb)])
    assert g.size == 2
    assert g.search(vb, k=1)[0].identity_id == bob


# ── D1: thresholds are cosine operating points, not probabilities ─────────

def test_threshold_rises_with_gallery_size(rng):
    """FPIR ~= N * FMR. A threshold that is safe against 1,000 enrolled people
    is not safe against 50,000, and the code must express that."""
    s = CALIBRATED
    t_small = threshold_for_gallery(s, gallery_size=1_000, target_fpir=1e-3)
    t_large = threshold_for_gallery(s, gallery_size=50_000, target_fpir=1e-3)
    assert t_large > t_small
    # and a stricter FPIR target must also tighten it
    assert threshold_for_gallery(s, 50_000, target_fpir=1e-4) > t_large


def test_threshold_is_a_cosine_not_a_probability():
    """The original constants (0.95/0.80/0.50) read as confidences but were
    applied to raw cosine. Whatever we compute must live in cosine space."""
    t = threshold_for_gallery(CALIBRATED, gallery_size=20_000, target_fpir=1e-3)
    assert -1.0 <= t <= 1.0
    assert t > CALIBRATED.mean          # strictly out in the impostor tail


def test_empty_gallery_never_auto_accepts(rng):
    e = IdentityResolutionEngine(embedding_dim=DIM, impostor_stats=CALIBRATED)
    m = e.identify_embedding(unit(rng))
    assert m.identity_id is None and m.match_level == "no_match"
    assert m.is_new_identity is True


def test_uncalibrated_engine_refuses_to_auto_accept(rng):
    """D1's real safeguard. Without measured impostor statistics there is no
    defensible threshold, so every match must fall to human review — however
    high the similarity."""
    e = IdentityResolutionEngine(embedding_dim=DIM, impostor_stats=None)
    alice = uuid4()
    v = unit(rng)
    e.enroll_embedding(alice, v)

    m = e.identify_embedding(v)                  # cosine 1.0, a perfect match
    assert m.similarity == pytest.approx(1.0, abs=1e-5)
    assert m.match_level == "review"
    assert m.requires_review is True
    assert "calibrat" in m.explanation.lower()


def test_similarity_and_confidence_are_separate_fields(rng):
    """Conflating them is what produced `biometric_confidence >= 0.80` being
    treated as 80% certainty in the patient resolver."""
    e = IdentityResolutionEngine(embedding_dim=DIM, impostor_stats=CALIBRATED)
    alice = uuid4()
    v = unit(rng)
    e.enroll_embedding(alice, v)
    m = e.identify_embedding(v)
    assert hasattr(m, "similarity") and hasattr(m, "confidence")
    assert 0.0 <= m.confidence <= 1.0
    assert m.threshold_used is not None and m.gallery_size == 1


# ── D2: margin between distinct identities gates auto-acceptance ──────────

def test_ambiguous_match_between_two_people_is_never_auto_accepted(rng):
    """Two enrolled people both close to the probe — the doppelganger case.
    High top-1 similarity alone must not be enough."""
    e = IdentityResolutionEngine(embedding_dim=DIM, impostor_stats=CALIBRATED,
                                 min_margin=0.10)
    alice, bob = uuid4(), uuid4()
    probe = unit(rng)
    e.enroll_embedding(alice, at_cosine(rng, probe, 0.88))
    e.enroll_embedding(bob, at_cosine(rng, probe, 0.86))     # margin 0.02

    m = e.identify_embedding(probe)
    assert m.identity_id == alice            # still the best candidate…
    assert m.match_level == "review"         # …but a human decides
    assert m.margin == pytest.approx(0.02, abs=1e-2)
    assert "margin" in m.explanation.lower()


def test_clear_winner_above_threshold_is_auto_accepted(rng):
    e = IdentityResolutionEngine(embedding_dim=DIM, impostor_stats=CALIBRATED,
                                 min_margin=0.10)
    alice, bob = uuid4(), uuid4()
    probe = unit(rng)
    e.enroll_embedding(alice, at_cosine(rng, probe, 0.92))
    e.enroll_embedding(bob, at_cosine(rng, probe, 0.30))

    m = e.identify_embedding(probe)
    assert m.identity_id == alice
    assert m.match_level == "auto"
    assert m.requires_review is False


def test_match_below_threshold_is_a_new_visitor_not_a_weak_link(rng):
    """A low-scoring nearest neighbour is not a person — it is noise. The old
    code returned it as a `possible_match` carrying an identity id."""
    e = IdentityResolutionEngine(embedding_dim=DIM, impostor_stats=CALIBRATED)
    e.enroll_embedding(uuid4(), unit(rng))
    m = e.identify_embedding(unit(rng))          # orthogonal-ish probe
    assert m.match_level == "no_match"
    assert m.identity_id is None
    assert m.is_new_identity is True


def test_no_match_does_not_mint_an_unpersisted_uuid(rng):
    """The old identify() returned uuid4() as identity_id for unknown visitors —
    an id nothing had stored. Minting the provisional identity belongs to the
    caller that can persist it."""
    e = IdentityResolutionEngine(embedding_dim=DIM, impostor_stats=CALIBRATED)
    a = e.identify_embedding(unit(rng))
    b = e.identify_embedding(unit(rng))
    assert a.identity_id is None and b.identity_id is None


# ── D5: liveness must fail closed ────────────────────────────────────────

def test_liveness_fails_closed_when_texture_analysis_is_unavailable():
    """skimage is not installed in this environment, so the texture path has
    ALWAYS taken its exception branch — which returned (True, 0.5). Every
    capture without a depth map was silently treated as a live face."""
    live, score = LivenessDetector().check_liveness(
        np.zeros((112, 112, 3), dtype=np.uint8), depth_map=None)
    assert live is False
    assert score == 0.0


def test_liveness_failure_blocks_identification(rng):
    class AlwaysSpoof:
        def check_liveness(self, *_a, **_k):
            return False, 0.0

    e = IdentityResolutionEngine(embedding_dim=DIM, impostor_stats=CALIBRATED,
                                 liveness=AlwaysSpoof())
    alice = uuid4()
    v = unit(rng)
    e.enroll_embedding(alice, v)
    m = e.identify(np.zeros((112, 112, 3), dtype=np.uint8))
    assert m.match_level == "spoof_attempt"
    assert m.identity_id is None


def test_flat_depth_map_is_rejected():
    """A printed photo or a screen has near-zero depth variance."""
    flat = np.full((112, 112), 300, dtype=np.uint16)
    live, _ = LivenessDetector().check_liveness(
        np.zeros((112, 112, 3), dtype=np.uint8), depth_map=flat)
    assert live is False
