"""Biometric identity resolution engine.

Face recognition over ArcFace R100 embeddings with exact gallery search and
calibrated, gallery-size-aware thresholds.

What this engine will and will not assert
-----------------------------------------
It returns a *candidate* with a similarity, a calibrated confidence, and the
margin to the runner-up. It never returns a decision. Auto-acceptance here means
"good enough to present without a review prompt", not "identity established" —
establishing identity is the job of the insurance prescription system at the Rx
desk, or of a person at the OTC desk.

Three properties are load-bearing and are covered by
`tests/unit/test_face_identity_engine.py`:

1. **Uncalibrated means review.** Without measured impostor statistics there is
   no defensible threshold, so every match falls to human review no matter how
   high it scores. There is no default number to accidentally trust.
2. **Margin gates acceptance.** A high top-1 score with a close runner-up is the
   doppelgänger case and is never auto-accepted.
3. **Liveness fails closed.** An error during anti-spoofing rejects the frame.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

import numpy as np

from .gallery import VectorGallery
from .thresholds import (ImpostorStats, confidence_from_similarity,
                         expected_false_matches, threshold_for_gallery)

logger = logging.getLogger(__name__)

ARCFACE_MODEL_PATH = "/app/models/arcface_r100.onnx"
LIVENESS_MODEL_PATH = "/app/models/liveness_detector.onnx"

# Match levels. "high"/"probable"/"possible" are gone: they described a
# confidence that was never computed, over a threshold that was never calibrated.
MATCH_AUTO = "auto"            # present without a review prompt
MATCH_REVIEW = "review"        # a person decides
MATCH_NONE = "no_match"        # nobody in the gallery — a new visitor
MATCH_SPOOF = "spoof_attempt"

DEFAULT_TARGET_FPIR = 1e-3
DEFAULT_MIN_MARGIN = 0.10      # cosine gap to the next distinct identity
DEFAULT_SEARCH_K = 10
MAX_MARGIN = 2.0               # widest possible cosine gap; used when alone


@dataclass
class FaceDetection:
    bbox: tuple[int, int, int, int]
    confidence: float
    keypoints: np.ndarray
    depth_map: Optional[np.ndarray]
    is_live: bool = True
    liveness_score: float = 1.0


@dataclass
class IdentityMatch:
    """`similarity` and `confidence` are deliberately separate fields.

    Collapsing them is what let `biometric_confidence >= 0.80` be read as 80%
    certainty when it was a raw cosine. Callers that need a probability must use
    `confidence`; callers comparing against a threshold must use `similarity`.
    """

    identity_id: Optional[UUID]
    similarity: float                    # raw cosine in [-1, 1]
    confidence: float                    # P(no gallery impostor scores this high)
    margin: float                        # gap to the next DISTINCT identity
    match_level: str
    identity_class: str
    requires_review: bool
    explanation: str
    gallery_size: int = 0                # distinct identities — the N in N·f
    threshold_used: Optional[float] = None
    expected_false_matches: Optional[float] = None
    runner_up_id: Optional[UUID] = None
    patient_id: Optional[UUID] = None
    staff_id: Optional[UUID] = None
    is_new_identity: bool = False


class FaceEmbeddingExtractor:

    def __init__(self, model_path: str = ARCFACE_MODEL_PATH):
        self.model_path = model_path
        self._session = None

    def _load(self):
        if self._session is None:
            import onnxruntime as ort
            self._session = ort.InferenceSession(
                self.model_path,
                providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
            )
            logger.info("ArcFace model loaded from %s", self.model_path)

    def extract(self, face_image: np.ndarray) -> Optional[np.ndarray]:
        """512-d L2-normalised embedding from a 112x112 aligned face crop."""
        try:
            self._load()
            img = face_image[:, :, ::-1].astype(np.float32)
            img = (img - 127.5) / 128.0
            img = np.transpose(img, (2, 0, 1))
            img = np.expand_dims(img, axis=0)

            embedding = self._session.run(None, {"data": img})[0][0]
            embedding = embedding / np.linalg.norm(embedding)
            return embedding.astype(np.float32)
        except Exception as exc:
            logger.error("Face embedding extraction failed: %s", exc)
            return None


class LivenessDetector:
    """Anti-spoofing. Rejects photo, video and mask presentations.

    Fails CLOSED. The previous implementation returned `(True, 0.5)` from its
    exception handler, and since `skimage` is not installed the texture path
    took that handler on every call — anti-spoofing was absent, not degraded,
    for any capture without a depth map.
    """

    def __init__(self, model_path: str = LIVENESS_MODEL_PATH):
        self.model_path = model_path
        self._session = None

    def check_liveness(
        self,
        face_image: np.ndarray,
        depth_map: Optional[np.ndarray] = None,
    ) -> tuple[bool, float]:
        if depth_map is not None:
            return self._depth_based_liveness(face_image, depth_map)
        return self._texture_based_liveness(face_image)

    def _depth_based_liveness(
        self,
        face_image: np.ndarray,
        depth_map: np.ndarray,
    ) -> tuple[bool, float]:
        try:
            face_region_depth = depth_map[
                face_image.shape[0] // 4:3 * face_image.shape[0] // 4,
                face_image.shape[1] // 4:3 * face_image.shape[1] // 4,
            ]
            depth_variance = float(np.var(face_region_depth))
            depth_mean = float(np.mean(face_region_depth))

            # A real face projects: the nose carries real depth variance.
            # A photograph or a screen is flat.
            if depth_variance < 100 or depth_mean < 200:
                return False, 0.1

            grad_x = np.gradient(face_region_depth.astype(float), axis=1)
            grad_y = np.gradient(face_region_depth.astype(float), axis=0)
            gradient_magnitude = np.sqrt(grad_x ** 2 + grad_y ** 2)
            avg_gradient = float(np.mean(gradient_magnitude))

            score = min(1.0, depth_variance / 500 * 0.7
                        + (1.0 - min(avg_gradient / 50, 1.0)) * 0.3)
            return score > 0.6, score
        except Exception as exc:
            logger.error("Depth liveness check failed, rejecting frame: %s", exc)
            return False, 0.0

    def _texture_based_liveness(self, face_image: np.ndarray) -> tuple[bool, float]:
        try:
            from skimage.feature import local_binary_pattern
            gray = np.mean(face_image, axis=2).astype(np.uint8)
            lbp = local_binary_pattern(gray, P=8, R=1, method="uniform")
            hist, _ = np.histogram(lbp, bins=59, range=(0, 58))
            hist = hist.astype(float) / (hist.sum() + 1e-10)
            entropy = -np.sum(hist * np.log(hist + 1e-10))
            score = min(1.0, entropy / 4.0)
            return score > 0.5, score
        except Exception as exc:
            # Fail CLOSED. Texture-only PAD is weak anyway; the deployment
            # answer is an NIR or depth channel at the desk, not this fallback.
            logger.warning(
                "Texture liveness unavailable (%s) — rejecting frame. Install "
                "scikit-image, or supply a depth/NIR channel.", exc)
            return False, 0.0


class IdentityResolutionEngine:
    """Resolves a face to a gallery candidate, with an auditable rationale."""

    def __init__(
        self,
        embedding_dim: int = 512,
        gallery: VectorGallery | None = None,
        impostor_stats: ImpostorStats | None = None,
        target_fpir: float = DEFAULT_TARGET_FPIR,
        min_margin: float = DEFAULT_MIN_MARGIN,
        liveness=None,
        extractor=None,
    ):
        self.embedding_dim = embedding_dim
        self.gallery = gallery or VectorGallery(dim=embedding_dim)
        self.impostor_stats = impostor_stats
        self.target_fpir = target_fpir
        self.min_margin = min_margin
        self._face_extractor = extractor or FaceEmbeddingExtractor()
        self._liveness_detector = liveness or LivenessDetector()

    # ── enrolment ────────────────────────────────────────────────────────
    def enroll_embedding(self, identity_id: UUID, embedding: np.ndarray,
                         vector_id: int | None = None) -> int:
        """Enrol a template directly. Returns the vector id to persist in
        `biometric_identities.faiss_index_id`."""
        return self.gallery.add(identity_id, embedding, vector_id)

    def enroll(self, identity_id: UUID, face_image: np.ndarray) -> Optional[int]:
        embedding = self._face_extractor.extract(face_image)
        if embedding is None:
            return None
        vid = self.enroll_embedding(identity_id, embedding)
        logger.info("Identity %s enrolled as vector %d", identity_id, vid)
        return vid

    def remove(self, identity_id: UUID) -> int:
        """Withdraw every template for an identity — un-enrolment, or erasure."""
        n = self.gallery.remove(identity_id)
        logger.info("Removed %d template(s) for identity %s", n, identity_id)
        return n

    def calibrate(self, stats: ImpostorStats) -> None:
        self.impostor_stats = stats

    # ── identification ───────────────────────────────────────────────────
    def identify_embedding(self, embedding: np.ndarray) -> IdentityMatch:
        started = time.monotonic()
        n_identities = self.gallery.identity_count
        hits = self.gallery.search(embedding, k=DEFAULT_SEARCH_K)

        if not hits:
            return IdentityMatch(
                identity_id=None, similarity=0.0, confidence=0.0,
                margin=MAX_MARGIN, match_level=MATCH_NONE,
                identity_class="unknown_visitor", requires_review=False,
                explanation="Gallery is empty — no enrolled identity to match.",
                gallery_size=n_identities, is_new_identity=True)

        top = hits[0]
        runner = hits[1] if len(hits) > 1 else None
        margin = (top.similarity - runner.similarity) if runner else MAX_MARGIN

        # Without measured impostor statistics no threshold is defensible, so
        # nothing is auto-accepted regardless of score.
        if self.impostor_stats is None:
            return IdentityMatch(
                identity_id=top.identity_id, similarity=top.similarity,
                confidence=0.0, margin=margin, match_level=MATCH_REVIEW,
                identity_class="registered", requires_review=True,
                explanation=(
                    "No impostor calibration for this model and capture point, "
                    "so no threshold can be justified — every match is referred "
                    "for review. Run a calibration pass to enable auto-accept."),
                gallery_size=n_identities,
                runner_up_id=runner.identity_id if runner else None)

        threshold = threshold_for_gallery(
            self.impostor_stats, n_identities, self.target_fpir)
        confidence = confidence_from_similarity(
            self.impostor_stats, top.similarity, n_identities)
        efm = expected_false_matches(
            self.impostor_stats, top.similarity, n_identities)

        elapsed_ms = (time.monotonic() - started) * 1000
        logger.debug("gallery search %.1fms · top=%.4f margin=%.4f τ=%.4f",
                     elapsed_ms, top.similarity, margin, threshold)

        if top.similarity < threshold:
            return IdentityMatch(
                identity_id=None, similarity=top.similarity,
                confidence=confidence, margin=margin, match_level=MATCH_NONE,
                identity_class="unknown_visitor", requires_review=False,
                explanation=(
                    f"Best similarity {top.similarity:.3f} is below the "
                    f"threshold {threshold:.3f} required to hold FPIR at "
                    f"{self.target_fpir:g} over {n_identities} enrolled "
                    f"identities. Treated as a new visitor."),
                gallery_size=n_identities, threshold_used=threshold,
                expected_false_matches=efm, is_new_identity=True)

        if margin < self.min_margin:
            return IdentityMatch(
                identity_id=top.identity_id, similarity=top.similarity,
                confidence=confidence, margin=margin, match_level=MATCH_REVIEW,
                identity_class="registered", requires_review=True,
                explanation=(
                    f"Similarity {top.similarity:.3f} clears the threshold "
                    f"{threshold:.3f}, but the margin to the next distinct "
                    f"identity is only {margin:.3f} (minimum "
                    f"{self.min_margin:.2f}). Two enrolled people are close "
                    f"enough that a person must choose."),
                gallery_size=n_identities, threshold_used=threshold,
                expected_false_matches=efm,
                runner_up_id=runner.identity_id if runner else None)

        return IdentityMatch(
            identity_id=top.identity_id, similarity=top.similarity,
            confidence=confidence, margin=margin, match_level=MATCH_AUTO,
            identity_class="registered", requires_review=False,
            explanation=(
                f"Similarity {top.similarity:.3f} exceeds the threshold "
                f"{threshold:.3f} for {n_identities} enrolled identities, with "
                f"a {margin:.3f} margin over the runner-up. About {efm:.2g} "
                f"enrolled people would be expected to score this high by "
                f"chance."),
            gallery_size=n_identities, threshold_used=threshold,
            expected_false_matches=efm,
            runner_up_id=runner.identity_id if runner else None)

    def identify(
        self,
        face_image: np.ndarray,
        depth_map: Optional[np.ndarray] = None,
    ) -> IdentityMatch:
        is_live, liveness_score = self._liveness_detector.check_liveness(
            face_image, depth_map)
        if not is_live:
            logger.warning("Liveness failed (%.3f) — rejecting", liveness_score)
            return IdentityMatch(
                identity_id=None, similarity=0.0, confidence=0.0,
                margin=0.0, match_level=MATCH_SPOOF,
                identity_class="spoof_attempt", requires_review=True,
                explanation=(
                    f"Presentation-attack check failed (score "
                    f"{liveness_score:.2f}). No identification attempted."),
                gallery_size=self.gallery.identity_count)

        embedding = self._face_extractor.extract(face_image)
        if embedding is None:
            return IdentityMatch(
                identity_id=None, similarity=0.0, confidence=0.0,
                margin=0.0, match_level=MATCH_NONE,
                identity_class="unknown_visitor", requires_review=False,
                explanation="No usable face embedding could be extracted.",
                gallery_size=self.gallery.identity_count, is_new_identity=True)

        return self.identify_embedding(embedding)

    # ── persistence ──────────────────────────────────────────────────────
    def save_index(self, path: str) -> None:
        """Persists vectors AND the identity mapping together."""
        self.gallery.save(path)
        logger.info("Gallery saved to %s (%d templates, %d identities)",
                    path, self.gallery.size, self.gallery.identity_count)

    def load_index(self, path: str) -> None:
        n = self.gallery.load(path)
        logger.info("Gallery loaded from %s (%d templates)", path, n)

    def rebuild_from_db(self, rows) -> int:
        """Cold start from `biometric_identities` — the authoritative mapping."""
        return self.gallery.rebuild(rows)
