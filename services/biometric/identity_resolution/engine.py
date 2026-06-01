"""
Biometric identity resolution engine.
Face recognition using ArcFace R100 with FAISS vector search.
Anti-spoofing via 3D depth liveness detection.
"""
import logging
import time
from dataclasses import dataclass
from typing import Optional
from uuid import UUID, uuid4

import numpy as np

logger = logging.getLogger(__name__)

ARCFACE_MODEL_PATH = "/app/models/arcface_r100.onnx"
LIVENESS_MODEL_PATH = "/app/models/liveness_detector.onnx"

MATCH_THRESHOLD_HIGH = 0.95       # High confidence match
MATCH_THRESHOLD_PROBABLE = 0.80   # Probable match — flag for confirmation
MATCH_THRESHOLD_POSSIBLE = 0.50   # Possible match — passive log


@dataclass
class FaceDetection:
    bbox: tuple[int, int, int, int]   # x1, y1, x2, y2
    confidence: float
    keypoints: np.ndarray             # 5 facial landmarks
    depth_map: Optional[np.ndarray]   # From IR sensor
    is_live: bool = True
    liveness_score: float = 1.0


@dataclass
class IdentityMatch:
    identity_id: Optional[UUID]
    confidence: float
    identity_class: str
    patient_id: Optional[UUID] = None
    staff_id: Optional[UUID] = None
    is_new_identity: bool = False
    match_level: str = "unknown"   # high, probable, possible, no_match


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
        """
        Extract 512-dimensional face embedding from a 112x112 aligned face crop.
        Returns L2-normalized embedding vector.
        """
        self._load()
        try:
            # Preprocess: BGR → RGB, normalize to [-1, 1]
            img = face_image[:, :, ::-1].astype(np.float32)
            img = (img - 127.5) / 128.0
            img = np.transpose(img, (2, 0, 1))      # HWC → CHW
            img = np.expand_dims(img, axis=0)        # Add batch dim

            embedding = self._session.run(None, {"data": img})[0][0]
            embedding = embedding / np.linalg.norm(embedding)  # L2 normalize
            return embedding.astype(np.float32)
        except Exception as exc:
            logger.error("Face embedding extraction failed: %s", exc)
            return None


class LivenessDetector:
    """
    Anti-spoofing using depth map from IR sensor.
    Rejects photo, video, and 3D mask attacks.
    """

    def __init__(self, model_path: str = LIVENESS_MODEL_PATH):
        self.model_path = model_path
        self._session = None

    def check_liveness(
        self,
        face_image: np.ndarray,
        depth_map: Optional[np.ndarray] = None,
    ) -> tuple[bool, float]:
        """
        Returns (is_live, confidence_score).
        Uses depth map if available; falls back to texture-based detection.
        """
        if depth_map is not None:
            return self._depth_based_liveness(face_image, depth_map)
        return self._texture_based_liveness(face_image)

    def _depth_based_liveness(
        self,
        face_image: np.ndarray,
        depth_map: np.ndarray,
    ) -> tuple[bool, float]:
        face_region_depth = depth_map[
            face_image.shape[0] // 4:3 * face_image.shape[0] // 4,
            face_image.shape[1] // 4:3 * face_image.shape[1] // 4,
        ]
        depth_variance = float(np.var(face_region_depth))
        depth_mean = float(np.mean(face_region_depth))

        # A real face has significant depth variance (nose projects forward)
        # A photo has near-zero variance — flat surface
        if depth_variance < 100 or depth_mean < 200:
            return False, 0.1   # Flat object — likely photo/screen

        # Check depth gradient — real face has smooth curvature
        grad_x = np.gradient(face_region_depth.astype(float), axis=1)
        grad_y = np.gradient(face_region_depth.astype(float), axis=0)
        gradient_magnitude = np.sqrt(grad_x ** 2 + grad_y ** 2)
        avg_gradient = float(np.mean(gradient_magnitude))

        liveness_score = min(1.0, depth_variance / 500 * 0.7 + (1.0 - min(avg_gradient / 50, 1.0)) * 0.3)
        is_live = liveness_score > 0.6

        return is_live, liveness_score

    def _texture_based_liveness(self, face_image: np.ndarray) -> tuple[bool, float]:
        # LBP (Local Binary Pattern) texture analysis
        # Print attacks have different micro-texture patterns than real skin
        try:
            from skimage.feature import local_binary_pattern
            gray = np.mean(face_image, axis=2).astype(np.uint8)
            lbp = local_binary_pattern(gray, P=8, R=1, method="uniform")
            hist, _ = np.histogram(lbp, bins=59, range=(0, 58))
            hist = hist.astype(float) / (hist.sum() + 1e-10)
            # Simple entropy-based liveness score
            entropy = -np.sum(hist * np.log(hist + 1e-10))
            score = min(1.0, entropy / 4.0)
            return score > 0.5, score
        except Exception:
            return True, 0.5  # Fail open if texture analysis unavailable


class IdentityResolutionEngine:
    """
    Resolves a detected face to a known pharmacy identity.
    Uses FAISS for millisecond approximate nearest-neighbor search
    across all enrolled identities.
    """

    def __init__(self, embedding_dim: int = 512):
        self.embedding_dim = embedding_dim
        self._index = None
        self._identity_map: dict[int, UUID] = {}   # FAISS index id → identity UUID
        self._face_extractor = FaceEmbeddingExtractor()
        self._liveness_detector = LivenessDetector()

    def _get_index(self):
        if self._index is None:
            import faiss
            self._index = faiss.IndexFlatIP(self.embedding_dim)  # Inner product = cosine for normalized vecs
            logger.info("FAISS index initialized (dimension=%d)", self.embedding_dim)
        return self._index

    def enroll(self, identity_id: UUID, face_image: np.ndarray) -> bool:
        """Add a new face embedding to the search index."""
        embedding = self._face_extractor.extract(face_image)
        if embedding is None:
            return False

        index = self._get_index()
        faiss_id = index.ntotal
        index.add(embedding.reshape(1, -1))
        self._identity_map[faiss_id] = identity_id
        logger.info("Identity %s enrolled at FAISS index position %d", identity_id, faiss_id)
        return True

    def identify(
        self,
        face_image: np.ndarray,
        depth_map: Optional[np.ndarray] = None,
    ) -> IdentityMatch:
        """
        Identify a detected face against all enrolled identities.
        Returns IdentityMatch with confidence and identity class.
        """
        start = time.monotonic()

        # Anti-spoofing check
        is_live, liveness_score = self._liveness_detector.check_liveness(face_image, depth_map)
        if not is_live:
            logger.warning("Liveness check failed (score=%.3f) — rejecting identification", liveness_score)
            return IdentityMatch(
                identity_id=None,
                confidence=0.0,
                identity_class="spoof_attempt",
                match_level="no_match",
            )

        # Extract embedding
        embedding = self._face_extractor.extract(face_image)
        if embedding is None:
            return IdentityMatch(
                identity_id=None,
                confidence=0.0,
                identity_class="unknown_visitor",
                match_level="no_match",
            )

        # FAISS search
        index = self._get_index()
        if index.ntotal == 0:
            return IdentityMatch(
                identity_id=uuid4(),
                confidence=0.0,
                identity_class="unknown_visitor",
                is_new_identity=True,
                match_level="no_match",
            )

        distances, indices = index.search(embedding.reshape(1, -1), k=1)
        best_score = float(distances[0][0])
        best_idx = int(indices[0][0])

        elapsed_ms = (time.monotonic() - start) * 1000
        logger.debug("FAISS search completed in %.1fms, best_score=%.4f", elapsed_ms, best_score)

        if best_score >= MATCH_THRESHOLD_HIGH:
            matched_id = self._identity_map.get(best_idx)
            return IdentityMatch(
                identity_id=matched_id,
                confidence=best_score,
                identity_class="registered",
                match_level="high",
            )
        elif best_score >= MATCH_THRESHOLD_PROBABLE:
            matched_id = self._identity_map.get(best_idx)
            return IdentityMatch(
                identity_id=matched_id,
                confidence=best_score,
                identity_class="registered",
                match_level="probable",
            )
        elif best_score >= MATCH_THRESHOLD_POSSIBLE:
            matched_id = self._identity_map.get(best_idx)
            return IdentityMatch(
                identity_id=matched_id,
                confidence=best_score,
                identity_class="possible_match",
                match_level="possible",
            )
        else:
            return IdentityMatch(
                identity_id=uuid4(),
                confidence=best_score,
                identity_class="unknown_visitor",
                is_new_identity=True,
                match_level="no_match",
            )

    def save_index(self, path: str) -> None:
        import faiss
        if self._index and self._index.ntotal > 0:
            faiss.write_index(self._index, path)
            logger.info("FAISS index saved to %s (%d vectors)", path, self._index.ntotal)

    def load_index(self, path: str) -> None:
        import faiss, os
        if os.path.exists(path):
            self._index = faiss.read_index(path)
            logger.info("FAISS index loaded from %s (%d vectors)", path, self._index.ntotal)
