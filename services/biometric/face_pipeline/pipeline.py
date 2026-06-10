"""
Face Identification Pipeline — Phase 22-B
==========================================
Integrates FaceDetector → FaceAligner → FaceEmbeddingExtractor
→ IdentityResolutionEngine in one callable class.

Designed for:
  1. Real-time camera stream (30fps) — returns identities in <50ms on CPU
  2. Enrollment — register a new identity from a single good-quality photo
  3. Batch processing — identify faces in a list of captured frames

All operations are 100% offline. The pipeline composes the existing
ArcFace + FAISS identity engine with the new face detection layer.

Thread safety: create one FaceIdentificationPipeline per thread/process.
The FAISS index is loaded from disk and is read-only during inference.
Enrollment requires an exclusive write lock (use enroll() from one thread).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import numpy as np

from .detector import FaceDetector, DetectedFace, DetectionBackend
from .aligner  import FaceAligner
from services.biometric.identity_resolution.engine import (
    IdentityResolutionEngine,
    IdentityMatch,
    LivenessDetector,
)

log = logging.getLogger(__name__)

# Latency budget per frame (ms) — log warning if exceeded
LATENCY_BUDGET_MS = 80


@dataclass
class PipelineResult:
    """Result for one detected face in a frame."""
    detection:   DetectedFace
    aligned_img: Optional[np.ndarray]   # 112×112 BGR crop
    identity:    Optional[IdentityMatch]
    latency_ms:  float = 0.0
    skipped:     bool  = False          # True if quality check failed
    skip_reason: str   = ""

    @property
    def is_identified(self) -> bool:
        return (
            self.identity is not None and
            self.identity.match_level in ("high", "probable")
        )


@dataclass
class FrameResult:
    """Aggregated result for one camera frame."""
    frame_id:      int
    captured_at:   datetime
    face_count:    int
    results:       list[PipelineResult]
    total_ms:      float = 0.0
    backend_used:  str   = ""

    @property
    def identified_faces(self) -> list[PipelineResult]:
        return [r for r in self.results if r.is_identified]

    @property
    def unknown_faces(self) -> list[PipelineResult]:
        return [r for r in self.results if not r.is_identified and not r.skipped]


class FaceIdentificationPipeline:
    """
    End-to-end face identification pipeline:
    frame → detect → filter → align → embed → identify

    Usage (streaming):
        pipeline = FaceIdentificationPipeline()
        pipeline.load_index("/var/lib/pharmpilot/models/faces.index")

        for frame_id, frame in camera_stream():
            result = pipeline.process_frame(frame, frame_id=frame_id)
            for face_result in result.identified_faces:
                handle_identified(face_result.identity)
            for face_result in result.unknown_faces:
                handle_unknown(face_result.detection)

    Usage (enrollment):
        pipeline.enroll_identity(staff_photo, identity_id=staff_uuid)
        pipeline.save_index("/var/lib/pharmpilot/models/faces.index")
    """

    def __init__(
        self,
        min_confidence:    float = 0.70,
        skip_quality_fail: bool  = True,
        preferred_backend: Optional[DetectionBackend] = None,
    ):
        self._detector    = FaceDetector(
            min_confidence=min_confidence,
            preferred_backend=preferred_backend,
        )
        self._aligner     = FaceAligner()
        self._id_engine   = IdentityResolutionEngine()
        self._liveness    = LivenessDetector()
        self._skip_qual   = skip_quality_fail
        self._frame_count = 0

    # ── Index Management ───────────────────────────────────────────────────────

    def load_index(self, path: str) -> bool:
        """Load FAISS identity index from disk. Returns True on success."""
        try:
            self._id_engine.load_index(path)
            log.info("FAISS identity index loaded from %s", path)
            return True
        except Exception as exc:
            log.warning("Failed to load FAISS index: %s", exc)
            return False

    def save_index(self, path: str) -> bool:
        """Persist FAISS identity index to disk. Returns True on success."""
        try:
            self._id_engine.save_index(path)
            log.info("FAISS identity index saved to %s", path)
            return True
        except Exception as exc:
            log.error("Failed to save FAISS index: %s", exc)
            return False

    # ── Enrollment ─────────────────────────────────────────────────────────────

    def enroll_identity(
        self,
        photo: np.ndarray,
        identity_id: UUID,
        depth_map: Optional[np.ndarray] = None,
        require_liveness: bool = True,
    ) -> dict:
        """
        Enroll a new identity from a photo.

        Best practice: capture 3–5 photos from slightly different angles,
        call enroll_identity() for each, then save_index().

        Returns: {"success": bool, "reason": str, "liveness_score": float}
        """
        # Detect face in photo
        detections = self._detector.detect(photo)
        if not detections:
            return {"success": False, "reason": "no_face_detected", "liveness_score": 0.0}

        # Use highest-confidence detection
        best = detections[0]
        if not best.is_quality_pass:
            return {
                "success": False,
                "reason": f"quality_fail_{best.quality_reason}",
                "liveness_score": 0.0,
            }

        # Align
        aligned = self._aligner.align(photo, best.bbox, best.landmarks_5)
        if aligned is None:
            return {"success": False, "reason": "alignment_failed", "liveness_score": 0.0}

        # Liveness check (optional for enrollment — photos are expected)
        liveness_score = 1.0
        if require_liveness and depth_map is not None:
            is_live, liveness_score = self._liveness.check_liveness(aligned, depth_map)
            if not is_live:
                return {
                    "success": False,
                    "reason": "liveness_fail",
                    "liveness_score": float(liveness_score),
                }

        # Enroll in FAISS
        enrolled = self._id_engine.enroll(identity_id, aligned)
        if not enrolled:
            return {"success": False, "reason": "embedding_extraction_failed", "liveness_score": float(liveness_score)}

        log.info("Identity %s enrolled successfully", identity_id)
        return {
            "success":         True,
            "reason":          "enrolled",
            "liveness_score":  float(liveness_score),
            "identity_id":     str(identity_id),
            "detection_conf":  float(best.confidence),
            "blur_score":      float(best.blur_score),
        }

    # ── Inference ──────────────────────────────────────────────────────────────

    def process_frame(
        self,
        frame: np.ndarray,
        frame_id: Optional[int] = None,
        depth_map: Optional[np.ndarray] = None,
    ) -> FrameResult:
        """
        Process one camera frame end-to-end.
        Returns a FrameResult with per-face identification results.
        """
        t0 = time.monotonic()
        fid = frame_id if frame_id is not None else self._frame_count
        self._frame_count += 1

        # ── Detect ──────────────────────────────────────────────────────────
        detections = self._detector.detect(frame)
        results: list[PipelineResult] = []

        for det in detections:
            t_face = time.monotonic()

            # Quality gate
            if self._skip_qual and not det.is_quality_pass:
                results.append(PipelineResult(
                    detection=det, aligned_img=None, identity=None,
                    skipped=True, skip_reason=det.quality_reason,
                ))
                continue

            # ── Align ────────────────────────────────────────────────────────
            aligned = self._aligner.align(frame, det.bbox, det.landmarks_5)
            if aligned is None:
                results.append(PipelineResult(
                    detection=det, aligned_img=None, identity=None,
                    skipped=True, skip_reason="alignment_failed",
                ))
                continue

            # ── Identify ─────────────────────────────────────────────────────
            identity = self._id_engine.identify(aligned, depth_map=depth_map)

            face_ms = (time.monotonic() - t_face) * 1000
            results.append(PipelineResult(
                detection=det,
                aligned_img=aligned,
                identity=identity,
                latency_ms=face_ms,
            ))

        total_ms = (time.monotonic() - t0) * 1000
        if total_ms > LATENCY_BUDGET_MS:
            log.debug("Frame %d exceeded latency budget: %.1fms (faces=%d)", fid, total_ms, len(detections))

        return FrameResult(
            frame_id=fid,
            captured_at=datetime.now(timezone.utc),
            face_count=len([r for r in results if not r.skipped]),
            results=results,
            total_ms=total_ms,
            backend_used=self._detector.active_backend.value,
        )

    def process_batch(
        self,
        frames: list[np.ndarray],
    ) -> list[FrameResult]:
        """Process a list of frames sequentially."""
        return [self.process_frame(f, frame_id=i) for i, f in enumerate(frames)]

    # ── Diagnostics ────────────────────────────────────────────────────────────

    def benchmark(self, frame: np.ndarray, runs: int = 10) -> dict:
        """
        Run inference N times and report latency statistics.
        Useful for hardware-specific performance tuning.
        """
        times: list[float] = []
        for _ in range(runs):
            t = time.monotonic()
            self.process_frame(frame)
            times.append((time.monotonic() - t) * 1000)
        return {
            "backend":   self._detector.active_backend.value,
            "runs":      runs,
            "mean_ms":   round(float(np.mean(times)), 2),
            "p50_ms":    round(float(np.percentile(times, 50)), 2),
            "p95_ms":    round(float(np.percentile(times, 95)), 2),
            "max_ms":    round(float(np.max(times)), 2),
            "fps_est":   round(1000 / float(np.mean(times)), 1),
        }
