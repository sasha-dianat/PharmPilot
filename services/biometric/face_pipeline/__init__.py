"""
Face Detection & Alignment Pipeline — Phase 22-B
=================================================
Fully offline. No external API calls. No cloud model hosting.

Pipeline:
  frame (BGR ndarray) → FaceDetector → FaceAligner → quality filter
                      → aligned 112×112 crops → FaceEmbeddingExtractor
                      → IdentityResolutionEngine

Detection backends (auto-selected, in priority order):
  1. MediaPipe Face Detection   (recommended — fast, accurate, pure Python)
  2. YOLOv8-face ONNX           (if model file present at MODEL_DIR)
  3. OpenCV DNN (face detection model) (if .caffemodel present)
  4. OpenCV Haar Cascade         (always available, lowest accuracy — true fallback)
"""
from .detector  import FaceDetector, DetectedFace, DetectionBackend
from .aligner   import FaceAligner
from .pipeline  import FaceIdentificationPipeline

__all__ = [
    "FaceDetector",
    "FaceAligner",
    "FaceIdentificationPipeline",
    "DetectedFace",
    "DetectionBackend",
]
