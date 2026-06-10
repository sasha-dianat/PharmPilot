"""
Package Feature Extractor — Phase 24
======================================
Extracts visual feature vectors from medication packaging images.

Backend priority (auto-selected at runtime):
  1. MobileNetV3-Large (torchvision) — 960-dim, ~2 ms/frame, excellent accuracy
  2. ResNet-18         (torchvision) — 512-dim, ~4 ms/frame, good accuracy
  3. HOG + Color Histogram (OpenCV/sklearn) — always available, decent accuracy

The embedding is L2-normalized so cosine similarity == dot product.
All models run fully offline after the first load.
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Optional

# cv2 and numpy are optional ML runtime dependencies.
# Import gracefully so the FastAPI router can always load even when
# OpenCV is not yet installed; methods will raise at call-time if missing.
try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None  # type: ignore

logger = logging.getLogger(__name__)

MODEL_DIR = Path(os.environ.get("PHARMPILOT_MODEL_DIR", str(Path.home() / ".pharmpilot" / "models")))

# Embedding dimension per backend
DIMS = {
    "mobilenet_v3": 960,
    "resnet18":     512,
    "hog_color":    4416,   # 4096 HOG + 320 color histogram
}


class PackageFeatureExtractor:
    """
    Extracts L2-normalized embedding vectors from package images.

    Usage::

        extractor = PackageFeatureExtractor()
        vec = extractor.extract(image_bgr)   # numpy array, shape (dim,)
    """

    def __init__(self) -> None:
        self.backend: str = "hog_color"
        self._model = None
        self._transform = None
        self._dim: int = DIMS["hog_color"]
        self._init_backend()

    # ------------------------------------------------------------------
    # Backend initialisation
    # ------------------------------------------------------------------

    def _init_backend(self) -> None:
        """Try deep-learning backends; fall back to HOG+Color gracefully."""
        if self._try_mobilenet_v3():
            return
        if self._try_resnet18():
            return
        logger.info("PackageFeatureExtractor: using HOG+Color histogram fallback")
        self.backend = "hog_color"
        self._dim    = DIMS["hog_color"]

    def _try_mobilenet_v3(self) -> bool:
        try:
            import torch
            import torchvision.models as models
            import torchvision.transforms as T

            model = models.mobilenet_v3_large(weights=models.MobileNet_V3_Large_Weights.DEFAULT)
            # Strip the classifier; keep the feature extractor (outputs 960-dim)
            model.classifier = torch.nn.Identity()
            model.eval()

            self._transform = T.Compose([
                T.ToPILImage(),
                T.Resize((224, 224)),
                T.ToTensor(),
                T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
            ])
            self._model  = model
            self.backend = "mobilenet_v3"
            self._dim    = DIMS["mobilenet_v3"]
            logger.info("PackageFeatureExtractor: MobileNetV3-Large loaded (offline)")
            return True
        except Exception as e:
            logger.debug("MobileNetV3 not available: %s", e)
            return False

    def _try_resnet18(self) -> bool:
        try:
            import torch
            import torchvision.models as models
            import torchvision.transforms as T

            model = models.resnet18(weights=models.ResNet18_Weights.DEFAULT)
            # Remove final FC → 512-dim avgpool output
            model.fc = torch.nn.Identity()
            model.eval()

            self._transform = T.Compose([
                T.ToPILImage(),
                T.Resize((224, 224)),
                T.ToTensor(),
                T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
            ])
            self._model  = model
            self.backend = "resnet18"
            self._dim    = DIMS["resnet18"]
            logger.info("PackageFeatureExtractor: ResNet-18 loaded (offline)")
            return True
        except Exception as e:
            logger.debug("ResNet-18 not available: %s", e)
            return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def dim(self) -> int:
        return self._dim

    def extract(self, image_bgr: np.ndarray) -> np.ndarray:
        """
        Returns L2-normalised embedding vector from a BGR image.

        Args:
            image_bgr: OpenCV BGR image (H×W×3)
        Returns:
            numpy float32 array of shape (self.dim,)
        """
        if image_bgr is None or image_bgr.size == 0:
            return np.zeros(self._dim, dtype=np.float32)

        if self.backend in ("mobilenet_v3", "resnet18"):
            return self._extract_deep(image_bgr)
        return self._extract_hog_color(image_bgr)

    # ------------------------------------------------------------------
    # Deep-learning path
    # ------------------------------------------------------------------

    def _extract_deep(self, image_bgr: np.ndarray) -> np.ndarray:
        try:
            import torch
            rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
            tensor = self._transform(rgb).unsqueeze(0)   # (1, 3, 224, 224)
            with torch.no_grad():
                vec = self._model(tensor).squeeze().numpy()
            return _l2_norm(vec)
        except Exception as e:
            logger.warning("Deep feature extraction failed: %s — using HOG fallback", e)
            return self._extract_hog_color(image_bgr)

    # ------------------------------------------------------------------
    # HOG + Color histogram fallback (always available via OpenCV + numpy)
    # ------------------------------------------------------------------

    def _extract_hog_color(self, image_bgr: np.ndarray) -> np.ndarray:
        # Resize to 128×128 for consistent HOG
        img = cv2.resize(image_bgr, (128, 128))

        # ── HOG features (4096-dim) ──
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        win_size     = (128, 128)
        block_size   = (16, 16)
        block_stride = (8, 8)
        cell_size    = (8, 8)
        nbins        = 9
        hog = cv2.HOGDescriptor(win_size, block_size, block_stride, cell_size, nbins)
        hog_vec = hog.compute(gray).flatten()     # (3780,) for 128×128

        # ── Color histograms in HSV (320-dim: 8×(16+16+8) bins) ──
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        h_hist = cv2.calcHist([hsv], [0], None, [16], [0, 180]).flatten()
        s_hist = cv2.calcHist([hsv], [1], None, [16], [0, 256]).flatten()
        v_hist = cv2.calcHist([hsv], [2], None, [8],  [0, 256]).flatten()
        color_vec = np.concatenate([h_hist, s_hist, v_hist])

        combined = np.concatenate([hog_vec, color_vec]).astype(np.float32)
        self._dim = len(combined)
        return _l2_norm(combined)

    # ------------------------------------------------------------------
    # Batch extraction (enrollment with multiple angles)
    # ------------------------------------------------------------------

    def extract_mean(self, images: list[np.ndarray]) -> np.ndarray:
        """
        Extract embeddings from multiple images of the same product,
        return their mean (L2-normalised). Used for enrollment.
        """
        if not images:
            return np.zeros(self._dim, dtype=np.float32)
        vecs = np.stack([self.extract(img) for img in images], axis=0)
        mean = vecs.mean(axis=0)
        return _l2_norm(mean)

    # ------------------------------------------------------------------
    # Frame preprocessing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def decode_base64_frame(b64_data: str) -> Optional[np.ndarray]:
        """Decode base64-encoded image (JPEG/PNG) to OpenCV BGR array."""
        import base64
        try:
            header_end = b64_data.find(",")
            if header_end != -1:
                b64_data = b64_data[header_end + 1:]
            data = base64.b64decode(b64_data)
            arr  = np.frombuffer(data, dtype=np.uint8)
            img  = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            return img
        except Exception as e:
            logger.warning("Failed to decode base64 frame: %s", e)
            return None

    @staticmethod
    def preprocess(image_bgr: np.ndarray,
                   crop_center: bool = True,
                   clahe: bool = True) -> np.ndarray:
        """
        Pre-process image before embedding:
          - Optional center crop (remove background clutter)
          - Optional CLAHE histogram equalisation (improves consistency
            under different pharmacy lighting conditions)
        """
        if image_bgr is None:
            return image_bgr

        h, w = image_bgr.shape[:2]

        if crop_center:
            # Crop inner 80% to remove label borders / scanner edges
            cy, cx = int(h * 0.1), int(w * 0.1)
            image_bgr = image_bgr[cy:h - cy, cx:w - cx]

        if clahe:
            lab = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2LAB)
            cl = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            lab[:, :, 0] = cl.apply(lab[:, :, 0])
            image_bgr = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

        return image_bgr


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _l2_norm(vec: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(vec)
    if norm < 1e-10:
        return vec.astype(np.float32)
    return (vec / norm).astype(np.float32)
