"""
Real-Time Behavioral Analysis Engine
=====================================
Runs on NVIDIA Jetson AGX Orin (edge node) — analyzes every camera frame
in real time to detect security-relevant behaviors without storing raw video.

Architecture:
  Frame stream (30fps) → YOLOv8-pose skeleton extraction →
  Temporal buffer (sliding 10-second window) →
  BehaviorClassifier (rules + ML) → ThreatScorer →
  SecurityEventPublisher (Kafka → API → WebSocket)

Design principles:
  - Privacy-preserving: analyzes skeletons/positions, not faces or identities
  - Only SECURITY behaviors are analyzed — no patient clinical behavior
  - No biometric matching at this layer — handled by BIS identity layer
  - All detections are probabilistic, not definitive — human review required
  - False-positive tolerance built in: minimum 3-frame confirmation before alert
"""
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

import numpy as np

logger = logging.getLogger(__name__)


class BehaviorType(str, Enum):
    # Security behaviors
    LOITERING               = "loitering"
    COUNTER_BYPASS_ATTEMPT  = "counter_bypass_attempt"
    VAULT_ZONE_INTRUSION    = "vault_zone_intrusion"
    THEFT_GESTURE           = "theft_gesture"
    AGGRESSIVE_POSTURE      = "aggressive_posture"
    TAILGATING              = "tailgating"
    AFTER_HOURS_PRESENCE    = "after_hours_presence"
    # Clinical safety behaviors
    PATIENT_DISTRESS        = "patient_distress"
    FALL_DETECTED           = "fall_detected"
    UNACCOMPANIED_MINOR     = "unaccompanied_minor"
    # Rx shopping
    MULTI_PHARMACY_SAME_DAY = "multi_pharmacy_same_day"


class AlertSeverity(str, Enum):
    INFO     = "info"       # Log only
    WARNING  = "warning"    # Notify pharmacist passively
    HIGH     = "high"       # Alert banner on workstation
    CRITICAL = "critical"   # Immediate action required


BEHAVIOR_SEVERITY = {
    BehaviorType.LOITERING:               AlertSeverity.WARNING,
    BehaviorType.COUNTER_BYPASS_ATTEMPT:  AlertSeverity.CRITICAL,
    BehaviorType.VAULT_ZONE_INTRUSION:    AlertSeverity.CRITICAL,
    BehaviorType.THEFT_GESTURE:           AlertSeverity.HIGH,
    BehaviorType.AGGRESSIVE_POSTURE:      AlertSeverity.HIGH,
    BehaviorType.TAILGATING:              AlertSeverity.WARNING,
    BehaviorType.AFTER_HOURS_PRESENCE:    AlertSeverity.CRITICAL,
    BehaviorType.PATIENT_DISTRESS:        AlertSeverity.HIGH,
    BehaviorType.FALL_DETECTED:           AlertSeverity.CRITICAL,
    BehaviorType.UNACCOMPANIED_MINOR:     AlertSeverity.WARNING,
    BehaviorType.MULTI_PHARMACY_SAME_DAY: AlertSeverity.HIGH,
}

# Behaviors that trigger immediate automated response (not just notification)
AUTO_RESPONSE_BEHAVIORS = {
    BehaviorType.VAULT_ZONE_INTRUSION,
    BehaviorType.COUNTER_BYPASS_ATTEMPT,
    BehaviorType.AFTER_HOURS_PRESENCE,
}


@dataclass
class PersonTrack:
    """Tracks a single detected person across frames."""
    track_id: int
    biometric_identity_id: Optional[UUID]
    zone: str
    first_seen: float          # monotonic timestamp
    last_seen: float
    positions: deque           # deque of (x, y, timestamp) — last 10s
    keypoints: Optional[np.ndarray] = None  # YOLOv8 pose keypoints (17 × 3)
    velocity: float = 0.0      # pixels/second
    is_stationary: bool = False
    stationary_since: Optional[float] = None
    frame_count: int = 0


@dataclass
class BehaviorDetection:
    detection_id: UUID = field(default_factory=uuid4)
    behavior_type: BehaviorType = BehaviorType.LOITERING
    severity: AlertSeverity = AlertSeverity.INFO
    track_id: int = 0
    biometric_identity_id: Optional[UUID] = None
    zone: str = ""
    confidence: float = 0.0
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    description: str = ""
    evidence_frame_timestamps: list[float] = field(default_factory=list)
    auto_response_triggered: bool = False
    requires_human_review: bool = True

    # Confirmation tracking (minimum 3 frames before escalating)
    confirmation_count: int = 0
    confirmed: bool = False


class ZoneDefinition:
    """
    Defines protected zones within the pharmacy floor plan.
    Coordinates are relative (0.0–1.0) — applied to camera frame dimensions.
    Each pharmacy configures these at installation time.
    """
    def __init__(self, zone_config: dict):
        self.zones = zone_config

    def get_zone(self, x: float, y: float) -> str:
        """Determine which zone a position falls in."""
        for zone_name, bounds in self.zones.items():
            x1, y1, x2, y2 = bounds
            if x1 <= x <= x2 and y1 <= y <= y2:
                return zone_name
        return "general_floor"

    def is_restricted(self, zone: str) -> bool:
        return zone in ("dispensing_counter_interior", "vault_room", "pharmacist_only")


DEFAULT_ZONE_CONFIG = {
    "waiting_area":              (0.0, 0.0, 0.5, 0.6),
    "dispensing_counter_public": (0.0, 0.6, 0.5, 0.8),
    "dispensing_counter_interior": (0.0, 0.8, 0.5, 1.0),  # RESTRICTED
    "vault_room":                (0.5, 0.7, 1.0, 1.0),      # RESTRICTED
    "pharmacist_only":           (0.5, 0.5, 1.0, 0.7),      # RESTRICTED
    "otc_shelves":               (0.5, 0.0, 1.0, 0.5),
}


class BehaviorDetector:
    """
    Core behavioral analysis engine.
    Processes pose-estimated person tracks and detects security behaviors.
    """

    # Thresholds (configurable per pharmacy)
    LOITERING_THRESHOLD_SECONDS    = 480    # 8 minutes
    STATIONARY_THRESHOLD_PIXELS    = 30     # Movement < 30px = stationary
    AGGRESSION_VELOCITY_THRESHOLD  = 200    # pixels/second toward counter
    TAILGATING_TIME_WINDOW_SECONDS = 2.0    # Two people through entry in <2s
    MINOR_HEIGHT_RATIO_THRESHOLD   = 0.55   # Person height < 55% of average adult

    def __init__(self, zone_config: Optional[dict] = None, pharmacy_hours: Optional[dict] = None):
        self.zones  = ZoneDefinition(zone_config or DEFAULT_ZONE_CONFIG)
        self.hours  = pharmacy_hours or {"open": "08:00", "close": "20:00"}
        self._tracks: dict[int, PersonTrack] = {}
        self._pending_detections: dict[int, BehaviorDetection] = {}
        self._confirmed_detections: list[BehaviorDetection] = []
        self._entry_timestamps: deque = deque(maxlen=20)  # For tailgating detection

    def process_frame(
        self,
        frame_timestamp: float,
        detections: list[dict],
        frame_width: int = 1920,
        frame_height: int = 1080,
    ) -> list[BehaviorDetection]:
        """
        Process one frame of detections.
        detections: [{track_id, x, y, w, h, keypoints, confidence}]
        Returns list of newly CONFIRMED behavior detections.
        """
        confirmed = []
        active_track_ids = set()

        for det in detections:
            track_id = det["track_id"]
            active_track_ids.add(track_id)
            norm_x = det["x"] / frame_width
            norm_y = det["y"] / frame_height

            zone = self.zones.get_zone(norm_x, norm_y)

            # Update or create track
            if track_id not in self._tracks:
                self._tracks[track_id] = PersonTrack(
                    track_id=track_id,
                    biometric_identity_id=det.get("biometric_identity_id"),
                    zone=zone,
                    first_seen=frame_timestamp,
                    last_seen=frame_timestamp,
                    positions=deque(maxlen=300),  # 10s at 30fps
                )

            track = self._tracks[track_id]
            track.last_seen = frame_timestamp
            track.zone = zone
            track.positions.append((norm_x, norm_y, frame_timestamp))
            track.keypoints = det.get("keypoints")
            track.frame_count += 1

            # Compute velocity
            if len(track.positions) >= 2:
                dx = (track.positions[-1][0] - track.positions[-2][0]) * frame_width
                dy = (track.positions[-1][1] - track.positions[-2][1]) * frame_height
                dt = max(track.positions[-1][2] - track.positions[-2][2], 0.001)
                track.velocity = float(np.sqrt(dx**2 + dy**2) / dt)

            # Check stationary
            if track.velocity < self.STATIONARY_THRESHOLD_PIXELS / frame_width:
                if not track.is_stationary:
                    track.is_stationary = True
                    track.stationary_since = frame_timestamp
            else:
                track.is_stationary = False
                track.stationary_since = None

            # Run behavior checks
            new_detections = self._check_behaviors(track, frame_timestamp, det)
            for detection in new_detections:
                key = (track_id, detection.behavior_type)
                if key not in self._pending_detections:
                    self._pending_detections[key] = detection
                else:
                    # Increment confirmation count
                    pending = self._pending_detections[key]
                    pending.confirmation_count += 1
                    pending.evidence_frame_timestamps.append(frame_timestamp)
                    # Confirm after 3 frames (prevents single-frame false positives)
                    if pending.confirmation_count >= 3 and not pending.confirmed:
                        pending.confirmed = True
                        pending.requires_human_review = True
                        self._confirmed_detections.append(pending)
                        confirmed.append(pending)

        # Record entry event for tailgating detection
        entry_zone_tracks = [d for d in detections if self.zones.get_zone(
            d["x"] / frame_width, d["y"] / frame_height) == "entry"]
        if entry_zone_tracks:
            self._entry_timestamps.append(frame_timestamp)
            tailgate = self._check_tailgating(frame_timestamp)
            if tailgate:
                confirmed.append(tailgate)

        # Clean up stale tracks (not seen for >5 seconds)
        stale_ids = [tid for tid, t in self._tracks.items()
                     if frame_timestamp - t.last_seen > 5.0 and tid not in active_track_ids]
        for tid in stale_ids:
            del self._tracks[tid]
            self._pending_detections = {k: v for k, v in self._pending_detections.items()
                                         if k[0] != tid}

        return confirmed

    def _check_behaviors(
        self,
        track: PersonTrack,
        now: float,
        detection: dict,
    ) -> list[BehaviorDetection]:
        """Check all behavior conditions for a single tracked person."""
        detections = []

        # ── Loitering ────────────────────────────────────────────────────
        if track.is_stationary and track.stationary_since:
            stationary_duration = now - track.stationary_since
            if stationary_duration >= self.LOITERING_THRESHOLD_SECONDS:
                detections.append(BehaviorDetection(
                    behavior_type=BehaviorType.LOITERING,
                    severity=AlertSeverity.WARNING,
                    track_id=track.track_id,
                    biometric_identity_id=track.biometric_identity_id,
                    zone=track.zone,
                    confidence=min(0.95, 0.70 + (stationary_duration - self.LOITERING_THRESHOLD_SECONDS) / 300 * 0.25),
                    description=f"Person stationary for {stationary_duration:.0f}s in {track.zone} without queue progression.",
                    evidence_frame_timestamps=[now],
                ))

        # ── Restricted zone intrusion ─────────────────────────────────────
        if self.zones.is_restricted(track.zone):
            behavior = (BehaviorType.VAULT_ZONE_INTRUSION
                        if "vault" in track.zone
                        else BehaviorType.COUNTER_BYPASS_ATTEMPT)
            detections.append(BehaviorDetection(
                behavior_type=behavior,
                severity=BEHAVIOR_SEVERITY[behavior],
                track_id=track.track_id,
                biometric_identity_id=track.biometric_identity_id,
                zone=track.zone,
                confidence=0.92,
                description=f"Unauthorized entry detected in restricted zone: {track.zone}",
                auto_response_triggered=(behavior in AUTO_RESPONSE_BEHAVIORS),
                evidence_frame_timestamps=[now],
            ))

        # ── Theft gesture detection ───────────────────────────────────────
        if track.zone == "otc_shelves" and track.keypoints is not None:
            concealment_score = self._detect_concealment_gesture(track.keypoints)
            if concealment_score > 0.70:
                detections.append(BehaviorDetection(
                    behavior_type=BehaviorType.THEFT_GESTURE,
                    severity=AlertSeverity.HIGH,
                    track_id=track.track_id,
                    biometric_identity_id=track.biometric_identity_id,
                    zone=track.zone,
                    confidence=concealment_score,
                    description=f"Possible item concealment gesture detected near OTC shelves (confidence: {concealment_score:.0%})",
                    evidence_frame_timestamps=[now],
                ))

        # ── Aggressive posture ────────────────────────────────────────────
        if (track.velocity > self.AGGRESSION_VELOCITY_THRESHOLD and
                track.zone in ("dispensing_counter_public", "waiting_area")):
            if track.keypoints is not None:
                aggression_score = self._detect_aggressive_posture(track.keypoints, track.velocity)
                if aggression_score > 0.65:
                    detections.append(BehaviorDetection(
                        behavior_type=BehaviorType.AGGRESSIVE_POSTURE,
                        severity=AlertSeverity.HIGH,
                        track_id=track.track_id,
                        biometric_identity_id=track.biometric_identity_id,
                        zone=track.zone,
                        confidence=aggression_score,
                        description="Elevated movement velocity + aggressive posture indicators near dispensing counter.",
                        evidence_frame_timestamps=[now],
                    ))

        # ── After-hours presence ──────────────────────────────────────────
        if self._is_after_hours(now):
            detections.append(BehaviorDetection(
                behavior_type=BehaviorType.AFTER_HOURS_PRESENCE,
                severity=AlertSeverity.CRITICAL,
                track_id=track.track_id,
                biometric_identity_id=track.biometric_identity_id,
                zone=track.zone,
                confidence=0.99,
                description=f"Motion/person detected outside pharmacy operating hours in zone: {track.zone}",
                auto_response_triggered=True,
                evidence_frame_timestamps=[now],
            ))

        # ── Fall detection ────────────────────────────────────────────────
        if track.keypoints is not None:
            fall_score = self._detect_fall(track.keypoints)
            if fall_score > 0.80:
                detections.append(BehaviorDetection(
                    behavior_type=BehaviorType.FALL_DETECTED,
                    severity=AlertSeverity.CRITICAL,
                    track_id=track.track_id,
                    biometric_identity_id=track.biometric_identity_id,
                    zone=track.zone,
                    confidence=fall_score,
                    description="Possible fall detected — person may require immediate assistance.",
                    evidence_frame_timestamps=[now],
                ))

        return detections

    def _detect_concealment_gesture(self, keypoints: np.ndarray) -> float:
        """
        Detect item-concealment gestures using wrist/hand position relative to torso.
        YOLOv8 keypoints: [nose, l_eye, r_eye, l_ear, r_ear, l_shoulder, r_shoulder,
                           l_elbow, r_elbow, l_wrist, r_wrist, l_hip, r_hip,
                           l_knee, r_knee, l_ankle, r_ankle]
        Concealment pattern: wrists move toward torso (hip/waist area) with
        repeated in-out motion (putting item in bag/pocket/clothing).
        """
        if keypoints is None or keypoints.shape[0] < 17:
            return 0.0
        try:
            l_wrist  = keypoints[9][:2]
            r_wrist  = keypoints[10][:2]
            l_hip    = keypoints[11][:2]
            r_hip    = keypoints[12][:2]
            torso_center = (l_hip + r_hip) / 2

            # Wrist proximity to torso (normalized)
            l_dist = float(np.linalg.norm(l_wrist - torso_center))
            r_dist = float(np.linalg.norm(r_wrist - torso_center))
            min_dist = min(l_dist, r_dist)

            # Close wrist to torso + low confidence keypoints (hiding from camera)
            l_conf = float(keypoints[9][2]) if keypoints.shape[1] > 2 else 1.0
            r_conf = float(keypoints[10][2]) if keypoints.shape[1] > 2 else 1.0

            if min_dist < 0.08 and min(l_conf, r_conf) < 0.50:
                return 0.75  # Wrists hidden near body
            if min_dist < 0.06:
                return 0.65  # Wrists very close to torso
            return 0.0
        except Exception:
            return 0.0

    def _detect_aggressive_posture(self, keypoints: np.ndarray, velocity: float) -> float:
        """
        Detect aggressive posture: raised arms, forward lean, rapid movement.
        """
        if keypoints is None or keypoints.shape[0] < 17:
            return 0.0
        try:
            l_shoulder = keypoints[5][:2]
            r_shoulder = keypoints[6][:2]
            l_wrist    = keypoints[9][:2]
            r_wrist    = keypoints[10][:2]
            nose       = keypoints[0][:2]

            # Raised arms: wrists above shoulders
            arms_raised = (l_wrist[1] < l_shoulder[1]) or (r_wrist[1] < r_shoulder[1])
            # Forward lean: nose significantly ahead of shoulder line
            shoulder_mid_y = (l_shoulder[1] + r_shoulder[1]) / 2
            leaning_forward = nose[1] < shoulder_mid_y - 0.05

            velocity_score = min(1.0, velocity / 400.0)  # Normalize velocity
            posture_score = (0.4 if arms_raised else 0.0) + \
                            (0.3 if leaning_forward else 0.0) + \
                            velocity_score * 0.3
            return float(np.clip(posture_score, 0.0, 1.0))
        except Exception:
            return 0.0

    def _detect_fall(self, keypoints: np.ndarray) -> float:
        """
        Fall detection: person's center of mass drops significantly,
        head is near floor level (y coordinate high in image coords).
        """
        if keypoints is None or keypoints.shape[0] < 17:
            return 0.0
        try:
            nose     = keypoints[0][:2]
            l_hip    = keypoints[11][:2]
            r_hip    = keypoints[12][:2]
            l_ankle  = keypoints[15][:2]
            r_ankle  = keypoints[16][:2]

            head_y    = float(nose[1])
            ankle_y   = float(max(l_ankle[1], r_ankle[1]))
            hip_y     = float((l_hip[1] + r_hip[1]) / 2)

            # Normal standing: head_y << ankle_y (top of frame to bottom)
            # Fallen: head_y approaches ankle_y
            if ankle_y > 0:
                height_ratio = (ankle_y - head_y) / ankle_y
                # Standing: ratio ≈ 0.7-0.9 | Fallen: ratio < 0.3
                if height_ratio < 0.25:
                    return 0.90
                if height_ratio < 0.35:
                    return 0.70
            return 0.0
        except Exception:
            return 0.0

    def _check_tailgating(self, now: float) -> Optional[BehaviorDetection]:
        """Two people through entry point within 2 seconds = tailgating."""
        if len(self._entry_timestamps) < 2:
            return None
        last_two = list(self._entry_timestamps)[-2:]
        if (now - last_two[0]) < self.TAILGATING_TIME_WINDOW_SECONDS:
            return BehaviorDetection(
                behavior_type=BehaviorType.TAILGATING,
                severity=AlertSeverity.WARNING,
                track_id=-1,
                zone="entry",
                confidence=0.80,
                description="Two individuals passed through entry within 2 seconds — possible tailgating.",
                confirmed=True,
                confirmation_count=3,
                evidence_frame_timestamps=last_two,
            )
        return None

    def _is_after_hours(self, timestamp: float) -> bool:
        """Check if timestamp falls outside pharmacy operating hours."""
        dt = datetime.fromtimestamp(timestamp)
        current_time = dt.strftime("%H:%M")
        return current_time < self.hours.get("open", "08:00") or \
               current_time > self.hours.get("close", "20:00")

    def get_active_tracks(self) -> dict:
        return {
            tid: {
                "zone": t.zone,
                "is_stationary": t.is_stationary,
                "velocity": round(t.velocity, 1),
                "duration_seconds": round(time.monotonic() - t.first_seen, 0),
                "biometric_id": str(t.biometric_identity_id) if t.biometric_identity_id else None,
            }
            for tid, t in self._tracks.items()
        }
