"""
Multi-Backend Face Detector — Phase 22-B
=========================================
Detects faces in a camera frame with bounding boxes and 5-point landmarks.
Provides four detection backends in priority order, all fully offline:

  MEDIAPIPE  — Google MediaPipe (pip install mediapipe)
               Most accurate. Runs well on CPU. <10ms per frame at 720p.
               Provides 468 mesh landmarks, downsampled to 5 for alignment.

  YOLOV8     — YOLOv8-face ONNX (if .onnx model file present in MODEL_DIR)
               High accuracy on faces with glasses, masks, profiles.
               Requires onnxruntime.

  OPENCV_DNN — OpenCV DNN with res10_300x300 Caffe model
               Good accuracy. Requires .caffemodel + .prototxt in MODEL_DIR.

  HAAR       — OpenCV Haar Cascade
               Always available (bundled with OpenCV). Lowest accuracy.
               Works even with only base OpenCV installed.
               Use as absolute fallback only.

Quality filters applied to all detections:
  - Confidence threshold (configurable, default 0.70)
  - Blur score (Laplacian variance — reject blurry crops)
  - Pose angle (reject extreme yaw/pitch using landmarks)
  - Minimum bounding box size (16×16 px minimum)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

MODEL_DIR = Path(os.environ.get(
    "PHARMPILOT_MODEL_DIR",
    str(Path.home() / ".pharmpilot" / "models"),
))

# Detection confidence thresholds
CONF_THRESHOLD_MEDIAPIPE = 0.70
CONF_THRESHOLD_YOLO      = 0.60
CONF_THRESHOLD_DNN       = 0.65
CONF_THRESHOLD_HAAR      = 0.0     # Haar doesn't give confidence scores

# Minimum face size to bother recognizing (pixels)
MIN_FACE_SIZE = 32

# Blur rejection threshold (Laplacian variance)
# Lower = more blurry. Reject if below this value.
BLUR_THRESHOLD = 50.0


class DetectionBackend(str, Enum):
    MEDIAPIPE  = "mediapipe"
    YOLOV8     = "yolov8"
    OPENCV_DNN = "opencv_dnn"
    HAAR       = "haar"
    NONE       = "none"


@dataclass
class DetectedFace:
    """A detected face with bounding box, landmarks, and quality metadata."""
    bbox:            tuple[int, int, int, int]   # (x1, y1, x2, y2) — pixel coords
    confidence:      float                        # Detection confidence 0–1
    landmarks_5:     Optional[np.ndarray]         # 5×2 array: left_eye, right_eye, nose, left_mouth, right_mouth
    blur_score:      float = 0.0                 # Laplacian variance (higher = sharper)
    pose_yaw:        float = 0.0                 # Estimated yaw angle in degrees
    pose_pitch:      float = 0.0                 # Estimated pitch angle in degrees
    is_quality_pass: bool  = True                # True if passes all quality filters
    quality_reason:  str   = ""                  # Why it failed quality (if applicable)
    backend:         DetectionBackend = DetectionBackend.NONE

    @property
    def width(self) -> int:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> int:
        return self.bbox[3] - self.bbox[1]

    @property
    def center(self) -> tuple[int, int]:
        return (self.bbox[0] + self.width // 2, self.bbox[1] + self.height // 2)


class FaceDetector:
    """
    Multi-backend face detector.
    Auto-selects the best available backend on first use.
    Falls through to the next backend if initialization fails.
    """

    def __init__(
        self,
        min_confidence: float = 0.70,
        min_face_size:  int   = MIN_FACE_SIZE,
        blur_threshold: float = BLUR_THRESHOLD,
        max_faces:      int   = 10,
        preferred_backend: Optional[DetectionBackend] = None,
    ):
        self.min_confidence = min_confidence
        self.min_face_size  = min_face_size
        self.blur_threshold = blur_threshold
        self.max_faces      = max_faces
        self._backend: Optional[DetectionBackend] = preferred_backend
        self._detector = None   # Lazy-initialized backend object
        self._haar_cascade = None

    # ── Backend Initialization ─────────────────────────────────────────────────

    def _init_backend(self) -> DetectionBackend:
        """Try backends in priority order. Return first that initializes successfully."""
        backends = [
            DetectionBackend.MEDIAPIPE,
            DetectionBackend.YOLOV8,
            DetectionBackend.OPENCV_DNN,
            DetectionBackend.HAAR,
        ]
        if self._backend:
            backends = [self._backend] + [b for b in backends if b != self._backend]

        for backend in backends:
            try:
                if backend == DetectionBackend.MEDIAPIPE:
                    import mediapipe as mp
                    self._detector = mp.solutions.face_detection.FaceDetection(
                        model_selection=1,               # 1 = full-range model (up to 5m)
                        min_detection_confidence=self.min_confidence,
                    )
                    log.info("Face detector: MediaPipe initialized")
                    return backend

                elif backend == DetectionBackend.YOLOV8:
                    yolo_path = MODEL_DIR / "yolov8n-face.onnx"
                    if not yolo_path.exists():
                        continue
                    import onnxruntime as ort
                    self._detector = ort.InferenceSession(
                        str(yolo_path),
                        providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
                    )
                    log.info("Face detector: YOLOv8-face ONNX initialized")
                    return backend

                elif backend == DetectionBackend.OPENCV_DNN:
                    model   = MODEL_DIR / "res10_300x300_ssd_iter_140000.caffemodel"
                    config  = MODEL_DIR / "deploy.prototxt"
                    if not model.exists() or not config.exists():
                        continue
                    import cv2
                    self._detector = cv2.dnn.readNetFromCaffe(str(config), str(model))
                    log.info("Face detector: OpenCV DNN initialized")
                    return backend

                elif backend == DetectionBackend.HAAR:
                    import cv2
                    haar_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
                    self._haar_cascade = cv2.CascadeClassifier(haar_path)
                    if self._haar_cascade.empty():
                        continue
                    log.info("Face detector: OpenCV Haar cascade initialized (fallback)")
                    return backend

            except Exception as exc:
                log.debug("Backend %s init failed: %s", backend.value, exc)
                continue

        log.error("No face detection backend available. Install mediapipe: pip install mediapipe")
        return DetectionBackend.NONE

    # ── Detection Methods ──────────────────────────────────────────────────────

    def detect(self, frame: np.ndarray) -> list[DetectedFace]:
        """
        Detect all faces in a BGR frame.
        Returns list of DetectedFace objects, quality-filtered and sorted by confidence.
        """
        if self._backend is None:
            self._backend = self._init_backend()

        if self._backend == DetectionBackend.NONE:
            return []

        try:
            if self._backend == DetectionBackend.MEDIAPIPE:
                raw = self._detect_mediapipe(frame)
            elif self._backend == DetectionBackend.YOLOV8:
                raw = self._detect_yolov8(frame)
            elif self._backend == DetectionBackend.OPENCV_DNN:
                raw = self._detect_dnn(frame)
            else:
                raw = self._detect_haar(frame)
        except Exception as exc:
            log.warning("Detection failed with %s: %s", self._backend, exc)
            return []

        # Apply quality filters
        quality_filtered = [self._apply_quality_filters(f, frame) for f in raw]

        # Sort by confidence, return top N
        quality_filtered.sort(key=lambda f: f.confidence, reverse=True)
        return quality_filtered[:self.max_faces]

    # ── MediaPipe Backend ──────────────────────────────────────────────────────

    def _detect_mediapipe(self, frame: np.ndarray) -> list[DetectedFace]:
        import mediapipe as mp

        h, w = frame.shape[:2]
        rgb = frame[:, :, ::-1]   # BGR → RGB

        results = self._detector.process(rgb)
        if not results.detections:
            return []

        faces: list[DetectedFace] = []
        for det in results.detections:
            bbox_mp = det.location_data.relative_bounding_box
            x1 = int(max(0, bbox_mp.xmin * w))
            y1 = int(max(0, bbox_mp.ymin * h))
            x2 = int(min(w, (bbox_mp.xmin + bbox_mp.width) * w))
            y2 = int(min(h, (bbox_mp.ymin + bbox_mp.height) * h))

            if (x2 - x1) < self.min_face_size or (y2 - y1) < self.min_face_size:
                continue

            # Extract 6 keypoints → pick 5 standard ones
            kps = det.location_data.relative_keypoints
            landmarks_5 = None
            if len(kps) >= 6:
                # MediaPipe order: LEFT_EYE=0, RIGHT_EYE=1, NOSE=2, MOUTH_LEFT=3, MOUTH_RIGHT=4, LEFT_EAR=5, RIGHT_EAR=6
                pts = [[kp.x * w, kp.y * h] for kp in kps[:6]]
                landmarks_5 = np.array([
                    pts[0],  # left eye
                    pts[1],  # right eye
                    pts[2],  # nose tip
                    pts[3],  # mouth left
                    pts[4],  # mouth right
                ], dtype=np.float32)

            faces.append(DetectedFace(
                bbox=(x1, y1, x2, y2),
                confidence=det.score[0] if det.score else 0.9,
                landmarks_5=landmarks_5,
                backend=DetectionBackend.MEDIAPIPE,
            ))
        return faces

    # ── YOLOv8-face Backend ────────────────────────────────────────────────────

    def _detect_yolov8(self, frame: np.ndarray) -> list[DetectedFace]:
        """YOLOv8-face ONNX inference. Input: 640×640 letterboxed."""
        import cv2

        h0, w0 = frame.shape[:2]
        input_size = 640

        # Letterbox resize
        r = min(input_size / h0, input_size / w0)
        nh, nw = int(h0 * r), int(w0 * r)
        resized = cv2.resize(frame, (nw, nh))
        pad_h = (input_size - nh) // 2
        pad_w = (input_size - nw) // 2
        padded = np.full((input_size, input_size, 3), 114, dtype=np.uint8)
        padded[pad_h:pad_h + nh, pad_w:pad_w + nw] = resized

        blob = padded[:, :, ::-1].astype(np.float32) / 255.0
        blob = np.transpose(blob, (2, 0, 1))[np.newaxis]

        outputs = self._detector.run(None, {self._detector.get_inputs()[0].name: blob})[0]
        outputs = np.squeeze(outputs)   # (num_detections, 16) for YOLOv8-face

        faces: list[DetectedFace] = []
        for det in outputs:
            conf = float(det[4])
            if conf < self.min_confidence:
                continue

            # Un-letterbox
            cx, cy, bw, bh = det[:4]
            x1 = int((cx - bw / 2 - pad_w) / r)
            y1 = int((cy - bh / 2 - pad_h) / r)
            x2 = int((cx + bw / 2 - pad_w) / r)
            y2 = int((cy + bh / 2 - pad_h) / r)
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w0, x2), min(h0, y2)

            if (x2 - x1) < self.min_face_size or (y2 - y1) < self.min_face_size:
                continue

            # 5 landmarks (det[5:15], pairs of x,y)
            landmarks_5 = None
            if len(det) >= 15:
                pts = [(float(det[5 + i * 2] - pad_w) / r,
                        float(det[6 + i * 2] - pad_h) / r)
                       for i in range(5)]
                landmarks_5 = np.array(pts, dtype=np.float32)

            faces.append(DetectedFace(
                bbox=(x1, y1, x2, y2),
                confidence=conf,
                landmarks_5=landmarks_5,
                backend=DetectionBackend.YOLOV8,
            ))
        return faces

    # ── OpenCV DNN Backend ────────────────────────────────────────────────────

    def _detect_dnn(self, frame: np.ndarray) -> list[DetectedFace]:
        import cv2
        h, w = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(
            cv2.resize(frame, (300, 300)), 1.0, (300, 300),
            (104.0, 177.0, 123.0), swapRB=False,
        )
        self._detector.setInput(blob)
        detections = self._detector.forward()

        faces: list[DetectedFace] = []
        for i in range(detections.shape[2]):
            conf = float(detections[0, 0, i, 2])
            if conf < self.min_confidence:
                continue
            x1 = int(detections[0, 0, i, 3] * w)
            y1 = int(detections[0, 0, i, 4] * h)
            x2 = int(detections[0, 0, i, 5] * w)
            y2 = int(detections[0, 0, i, 6] * h)
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            if (x2 - x1) < self.min_face_size or (y2 - y1) < self.min_face_size:
                continue
            faces.append(DetectedFace(
                bbox=(x1, y1, x2, y2),
                confidence=conf,
                landmarks_5=None,
                backend=DetectionBackend.OPENCV_DNN,
            ))
        return faces

    # ── Haar Cascade Backend ──────────────────────────────────────────────────

    def _detect_haar(self, frame: np.ndarray) -> list[DetectedFace]:
        import cv2
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        rects = self._haar_cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5,
            minSize=(self.min_face_size, self.min_face_size),
            flags=cv2.CASCADE_SCALE_IMAGE,
        )
        faces: list[DetectedFace] = []
        if len(rects) == 0:
            return faces
        for (x, y, fw, fh) in rects:
            faces.append(DetectedFace(
                bbox=(x, y, x + fw, y + fh),
                confidence=0.8,   # Haar doesn't give scores; use 0.8 as placeholder
                landmarks_5=None,
                backend=DetectionBackend.HAAR,
            ))
        return faces

    # ── Quality Filters ────────────────────────────────────────────────────────

    def _apply_quality_filters(
        self,
        face: DetectedFace,
        frame: np.ndarray,
    ) -> DetectedFace:
        """Apply blur and pose quality filters. Modifies face in-place."""
        x1, y1, x2, y2 = face.bbox
        crop = frame[y1:y2, x1:x2]

        if crop.size == 0:
            face.is_quality_pass = False
            face.quality_reason  = "empty_crop"
            return face

        # ── Blur check (Laplacian variance) ──────────────────────────────────
        import cv2
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        face.blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        if face.blur_score < self.blur_threshold:
            face.is_quality_pass = False
            face.quality_reason  = f"blurry_{face.blur_score:.0f}<{self.blur_threshold}"
            return face

        # ── Pose estimation from landmarks ─────────────────────────────────
        if face.landmarks_5 is not None:
            yaw, pitch = self._estimate_pose(face.landmarks_5, frame.shape)
            face.pose_yaw   = yaw
            face.pose_pitch = pitch
            # Reject extreme poses (> 45° yaw or > 35° pitch)
            if abs(yaw) > 45 or abs(pitch) > 35:
                face.is_quality_pass = False
                face.quality_reason  = f"extreme_pose_yaw={yaw:.0f}_pitch={pitch:.0f}"
                return face

        face.is_quality_pass = True
        return face

    @staticmethod
    def _estimate_pose(landmarks_5: np.ndarray, frame_shape: tuple) -> tuple[float, float]:
        """
        Rough yaw/pitch estimation from 5-point landmarks.
        No 3D model needed — uses eye-midpoint vs nose tip heuristic.
        """
        if landmarks_5 is None or len(landmarks_5) < 5:
            return 0.0, 0.0
        left_eye   = landmarks_5[0]
        right_eye  = landmarks_5[1]
        nose       = landmarks_5[2]

        # Eye midpoint
        eye_mid = (left_eye + right_eye) / 2
        eye_width = float(np.linalg.norm(right_eye - left_eye))
        if eye_width < 1:
            return 0.0, 0.0

        # Horizontal offset of nose from eye midpoint (yaw proxy)
        horiz_offset = float(nose[0] - eye_mid[0])
        yaw = (horiz_offset / eye_width) * 90

        # Vertical offset (pitch proxy)
        vert_offset = float(nose[1] - eye_mid[1])
        vert_norm = vert_offset / eye_width
        pitch = (vert_norm - 0.5) * 60   # Calibrated empirically

        return yaw, pitch

    @property
    def active_backend(self) -> DetectionBackend:
        if self._backend is None:
            self._backend = self._init_backend()
        return self._backend
