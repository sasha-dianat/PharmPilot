"""
Face Alignment — Phase 22-B
============================
Transforms a raw face crop into a 112×112 aligned face image for ArcFace input.

Alignment method: similarity transform using 5-point landmarks.
The 5 source landmarks are mapped to the canonical ArcFace reference positions
(derived from MS1MV2 training set normalization).

If landmarks are unavailable (Haar backend), falls back to simple resize
with mild histogram equalization.
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

# ── ArcFace Reference Landmarks (112×112 canonical positions) ─────────────────
# Based on insightface/ArcFace alignment standard
ARCFACE_REF_5PTS = np.array([
    [38.2946, 51.6963],   # left eye
    [73.5318, 51.5014],   # right eye
    [56.0252, 71.7366],   # nose tip
    [41.5493, 92.3655],   # left mouth corner
    [70.7299, 92.2041],   # right mouth corner
], dtype=np.float32)

OUTPUT_SIZE = 112


class FaceAligner:
    """
    Aligns a face crop to the ArcFace 112×112 canonical space using
    a similarity (5-DOF) transform from 5-point landmarks.

    Alignment is critical for ArcFace accuracy: a misaligned crop
    degrades recognition rate by up to 30%.
    """

    def __init__(self, output_size: int = OUTPUT_SIZE):
        self.output_size = output_size

    def align(
        self,
        frame: np.ndarray,
        bbox: tuple[int, int, int, int],
        landmarks_5: Optional[np.ndarray],
    ) -> Optional[np.ndarray]:
        """
        Extract and align a face from a frame.

        frame:       full BGR image
        bbox:        (x1, y1, x2, y2) bounding box
        landmarks_5: 5×2 float32 array (left_eye, right_eye, nose, mouth_l, mouth_r)

        Returns: aligned 112×112 BGR ndarray, or None if alignment fails.
        """
        import cv2

        x1, y1, x2, y2 = bbox
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(frame.shape[1], x2), min(frame.shape[0], y2)

        if x2 <= x1 or y2 <= y1:
            return None

        if landmarks_5 is not None and len(landmarks_5) == 5:
            return self._align_with_landmarks(frame, landmarks_5)
        else:
            return self._align_fallback(frame[y1:y2, x1:x2])

    def _align_with_landmarks(
        self,
        frame: np.ndarray,
        landmarks_5: np.ndarray,
    ) -> Optional[np.ndarray]:
        """Similarity transform from source landmarks to ArcFace reference."""
        import cv2

        src = landmarks_5.astype(np.float32)
        dst = ARCFACE_REF_5PTS.copy()

        # Scale reference landmarks to output size (default is 112)
        scale = self.output_size / 112.0
        dst *= scale

        # Estimate similarity transform (rotation + scale + translation, no shear)
        M, inliers = cv2.estimateAffinePartial2D(
            src, dst,
            method=cv2.LMEDS,
            confidence=0.99,
        )

        if M is None:
            log.debug("Affine estimation failed — falling back to crop resize")
            h, w = frame.shape[:2]
            # Use bounding box from landmarks as fallback
            x_min = int(max(0, landmarks_5[:, 0].min() - 20))
            y_min = int(max(0, landmarks_5[:, 1].min() - 20))
            x_max = int(min(w,  landmarks_5[:, 0].max() + 20))
            y_max = int(min(h,  landmarks_5[:, 1].max() + 30))
            crop = frame[y_min:y_max, x_min:x_max]
            return self._align_fallback(crop)

        aligned = cv2.warpAffine(
            frame, M,
            (self.output_size, self.output_size),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0),
        )
        return aligned

    def _align_fallback(self, crop: np.ndarray) -> Optional[np.ndarray]:
        """Resize-only alignment when landmarks are unavailable (e.g., Haar backend)."""
        import cv2

        if crop is None or crop.size == 0:
            return None

        resized = cv2.resize(crop, (self.output_size, self.output_size), interpolation=cv2.INTER_LINEAR)

        # Mild CLAHE for contrast normalization (helps in uneven lighting)
        lab = cv2.cvtColor(resized, cv2.COLOR_BGR2LAB)
        l_ch, a_ch, b_ch = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
        l_eq = clahe.apply(l_ch)
        lab_eq = cv2.merge([l_eq, a_ch, b_ch])
        return cv2.cvtColor(lab_eq, cv2.COLOR_LAB2BGR)

    def align_batch(
        self,
        frame: np.ndarray,
        detections: list,   # list[DetectedFace]
    ) -> list[Optional[np.ndarray]]:
        """Align multiple faces from the same frame in one call."""
        return [
            self.align(frame, det.bbox, det.landmarks_5)
            for det in detections
        ]
