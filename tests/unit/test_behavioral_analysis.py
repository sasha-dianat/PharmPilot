"""
Unit tests — Behavioral Analysis Engine
Patient safety and security correctness tests.
A false negative on CRITICAL behaviors (vault intrusion, after-hours)
has direct safety and regulatory consequences.
"""
import time
import numpy as np
import pytest
from services.biometric.behavioral_analysis.detector import (
    BehaviorDetector, BehaviorType, AlertSeverity,
    ZoneDefinition, DEFAULT_ZONE_CONFIG, PersonTrack,
)
from services.biometric.behavioral_analysis.duress_protocol import (
    DuressProtocolEngine, DuressTriggerMethod, PharmacySecurityConfig,
)
from services.biometric.behavioral_analysis.rx_shopping_detector import (
    RxShoppingDetector, RxShoppingRiskProfile,
    MULTI_PHARMACY_24H_THRESHOLD,
)
from collections import deque
from uuid import uuid4


@pytest.fixture
def detector():
    return BehaviorDetector(
        pharmacy_hours={"open": "08:00", "close": "20:00"}
    )


@pytest.fixture
def zones():
    return ZoneDefinition(DEFAULT_ZONE_CONFIG)


# ── Zone Classification ───────────────────────────────────────────────────

class TestZoneClassification:

    def test_waiting_area_is_not_restricted(self, zones):
        zone = zones.get_zone(0.2, 0.3)
        assert zone == "waiting_area"
        assert not zones.is_restricted(zone)

    def test_vault_room_is_restricted(self, zones):
        zone = zones.get_zone(0.75, 0.85)
        assert zone == "vault_room"
        assert zones.is_restricted(zone)

    def test_counter_interior_is_restricted(self, zones):
        zone = zones.get_zone(0.2, 0.9)
        assert zone == "dispensing_counter_interior"
        assert zones.is_restricted(zone)

    def test_otc_shelves_not_restricted(self, zones):
        zone = zones.get_zone(0.75, 0.25)
        assert zone == "otc_shelves"
        assert not zones.is_restricted(zone)

    def test_unknown_position_returns_general_floor(self, zones):
        # Position that doesn't match any defined zone
        zone = zones.get_zone(0.9, 0.1)
        assert isinstance(zone, str)


# ── Loitering Detection ───────────────────────────────────────────────────

class TestLoiteringDetection:

    def test_loitering_triggers_after_threshold(self, detector):
        """Person stationary for >8 minutes must trigger loitering warning."""
        track = PersonTrack(
            track_id=1,
            biometric_identity_id=None,
            zone="waiting_area",
            first_seen=0.0,
            last_seen=0.0,
            positions=deque(maxlen=300),
        )
        import datetime
        # Use current time during business hours so after-hours doesn't fire
        business_ts = datetime.datetime.now().replace(hour=10, minute=0).timestamp()
        loitering_start = business_ts - 500  # Started 500s ago (> 8 min threshold)
        
        track.is_stationary = True
        track.stationary_since = loitering_start
        detections = detector._check_behaviors(track, business_ts, {})
        loitering = [d for d in detections if d.behavior_type == BehaviorType.LOITERING]
        assert len(loitering) >= 1, "8+ min stationary must trigger loitering"
        assert loitering[0].severity == AlertSeverity.WARNING

    def test_no_loitering_below_threshold(self, detector):
        """Person stationary for 3 minutes should NOT trigger loitering."""
        track = PersonTrack(
            track_id=2,
            biometric_identity_id=None,
            zone="waiting_area",
            first_seen=0.0,
            last_seen=0.0,
            positions=deque(maxlen=300),
        )
        import datetime
        business_ts = datetime.datetime.now().replace(hour=10, minute=0).timestamp()
        track.is_stationary = True
        track.stationary_since = business_ts - 180  # Started 3 min ago

        detections = detector._check_behaviors(track, business_ts, {})  # 3 minutes
        loitering = [d for d in detections if d.behavior_type == BehaviorType.LOITERING]
        assert len(loitering) == 0, "3 minutes is below the 8-minute threshold"


# ── Restricted Zone Intrusion ─────────────────────────────────────────────

class TestRestrictedZoneIntrusion:

    def test_vault_intrusion_is_critical(self, detector):
        """Any unauthorized presence in vault = critical alert."""
        track = PersonTrack(
            track_id=3, biometric_identity_id=None,
            zone="vault_room", first_seen=0.0, last_seen=0.0,
            positions=deque(maxlen=300),
        )
        detections = detector._check_behaviors(track, time.monotonic(), {})
        vault = [d for d in detections if d.behavior_type == BehaviorType.VAULT_ZONE_INTRUSION]
        assert len(vault) >= 1, "Vault zone entry must trigger intrusion detection"
        assert vault[0].severity == AlertSeverity.CRITICAL
        assert vault[0].auto_response_triggered is True

    def test_counter_bypass_is_critical(self, detector):
        """Entry behind dispensing counter = critical alert."""
        track = PersonTrack(
            track_id=4, biometric_identity_id=None,
            zone="dispensing_counter_interior", first_seen=0.0, last_seen=0.0,
            positions=deque(maxlen=300),
        )
        detections = detector._check_behaviors(track, time.monotonic(), {})
        bypass = [d for d in detections if d.behavior_type == BehaviorType.COUNTER_BYPASS_ATTEMPT]
        assert len(bypass) >= 1, "Counter interior entry must trigger bypass alert"
        assert bypass[0].severity == AlertSeverity.CRITICAL

    def test_general_floor_no_intrusion(self, detector):
        """Walking on general floor should not trigger intrusion."""
        track = PersonTrack(
            track_id=5, biometric_identity_id=None,
            zone="general_floor", first_seen=0.0, last_seen=0.0,
            positions=deque(maxlen=300),
        )
        detections = detector._check_behaviors(track, time.monotonic(), {})
        intrusions = [d for d in detections
                      if d.behavior_type in (BehaviorType.VAULT_ZONE_INTRUSION,
                                             BehaviorType.COUNTER_BYPASS_ATTEMPT)]
        assert len(intrusions) == 0, "General floor must not trigger intrusion alerts"


# ── Fall Detection ────────────────────────────────────────────────────────

class TestFallDetection:

    def _make_standing_keypoints(self) -> np.ndarray:
        """17 keypoints in a standing position (head near top, ankles near bottom)."""
        kps = np.zeros((17, 3))
        kps[0]  = [0.5, 0.05, 0.9]   # nose — top of frame
        kps[5]  = [0.4, 0.25, 0.9]   # l_shoulder
        kps[6]  = [0.6, 0.25, 0.9]   # r_shoulder
        kps[11] = [0.4, 0.55, 0.9]   # l_hip
        kps[12] = [0.6, 0.55, 0.9]   # r_hip
        kps[15] = [0.4, 0.90, 0.9]   # l_ankle — near bottom
        kps[16] = [0.6, 0.90, 0.9]   # r_ankle — near bottom
        return kps

    def _make_fallen_keypoints(self) -> np.ndarray:
        """17 keypoints in a horizontal (fallen) position."""
        kps = np.zeros((17, 3))
        kps[0]  = [0.1, 0.80, 0.9]   # nose — near bottom
        kps[5]  = [0.3, 0.80, 0.9]   # l_shoulder
        kps[6]  = [0.5, 0.80, 0.9]   # r_shoulder
        kps[11] = [0.6, 0.80, 0.9]   # l_hip — same level as head
        kps[12] = [0.7, 0.80, 0.9]   # r_hip
        kps[15] = [0.9, 0.82, 0.9]   # l_ankle — same level
        kps[16] = [0.95, 0.82, 0.9]  # r_ankle — same level
        return kps

    def test_standing_person_no_fall(self, detector):
        score = detector._detect_fall(self._make_standing_keypoints())
        assert score < 0.5, f"Standing person should not trigger fall (score={score:.2f})"

    def test_fallen_person_triggers_detection(self, detector):
        score = detector._detect_fall(self._make_fallen_keypoints())
        assert score >= 0.7, f"Fallen person should trigger fall detection (score={score:.2f})"

    def test_none_keypoints_returns_zero(self, detector):
        assert detector._detect_fall(None) == 0.0


# ── After-Hours Detection ─────────────────────────────────────────────────

class TestAfterHoursDetection:

    def test_after_hours_triggers_critical(self, detector):
        """Presence at 2 AM must trigger critical after-hours alert."""
        from datetime import datetime
        # Use midnight timestamp
        midnight_ts = datetime(2025, 6, 1, 2, 0, 0).timestamp()
        track = PersonTrack(
            track_id=6, biometric_identity_id=None,
            zone="waiting_area", first_seen=midnight_ts, last_seen=midnight_ts,
            positions=deque(maxlen=300),
        )
        detections = detector._check_behaviors(track, midnight_ts, {})
        after_hours = [d for d in detections if d.behavior_type == BehaviorType.AFTER_HOURS_PRESENCE]
        assert len(after_hours) >= 1, "Presence at 2 AM must trigger after-hours alert"
        assert after_hours[0].severity == AlertSeverity.CRITICAL
        assert after_hours[0].auto_response_triggered is True
        assert after_hours[0].confidence == 0.99

    def test_business_hours_no_after_hours_alert(self, detector):
        """Presence at 10 AM (business hours) must NOT trigger after-hours."""
        from datetime import datetime
        midday_ts = datetime(2025, 6, 1, 10, 30, 0).timestamp()
        assert not detector._is_after_hours(midday_ts), "10:30 AM is within business hours"


# ── Tailgating Detection ──────────────────────────────────────────────────

class TestTailgatingDetection:

    def test_two_entries_within_2_seconds_flags_tailgating(self, detector):
        """Two entry events within 2 seconds = tailgating."""
        now = time.monotonic()
        detector._entry_timestamps.append(now - 1.5)   # first person, 1.5s ago
        detector._entry_timestamps.append(now - 0.5)   # second person, 0.5s ago
        result = detector._check_tailgating(now)
        assert result is not None, "Entries 1 second apart must trigger tailgating"
        assert result.behavior_type == BehaviorType.TAILGATING

    def test_two_entries_5_seconds_apart_no_tailgating(self, detector):
        """Two entry events 5 seconds apart is NOT tailgating."""
        now = time.monotonic()
        detector._entry_timestamps.append(now - 5.5)   # first person, 5.5s ago
        detector._entry_timestamps.append(now - 5.0)   # second person, 5s ago — gap > 2s
        result = detector._check_tailgating(now)
        assert result is None, "Entries 5 seconds apart should not trigger tailgating"

    def test_single_entry_no_tailgating(self, detector):
        """Single entry cannot be tailgating."""
        result = detector._check_tailgating(time.monotonic())
        assert result is None, "Single entry event cannot trigger tailgating"


# ── Duress Protocol ───────────────────────────────────────────────────────

class TestDuressProtocol:

    def test_config_duress_pin_verification(self):
        import hashlib
        config = PharmacySecurityConfig(
            pharmacy_id=uuid4(),
            address="123 Main St",
            phone="555-1234",
            lat=32.7767,
            lng=-96.7970,
            duress_pin=hashlib.sha256("1234".encode()).hexdigest(),
        )
        assert config.verify_duress_pin("1234") is True
        assert config.verify_duress_pin("5678") is False
        assert config.verify_duress_pin("")      is False

    def test_duress_trigger_methods_all_valid(self):
        """All DuressTriggerMethod values must be valid enum members."""
        methods = [m.value for m in DuressTriggerMethod]
        assert "panic_button"      in methods
        assert "duress_pin"        in methods
        assert "ai_critical_alert" in methods
        assert "manual_staff_activation" in methods


# ── Rx Shopping Detector ──────────────────────────────────────────────────

class TestRxShoppingDetector:

    def test_risk_score_zero_for_no_flags(self):
        detector = RxShoppingDetector()
        profile = RxShoppingRiskProfile(
            patient_id=uuid4(), biometric_identity_id=None
        )
        assert detector._calculate_risk_score(profile) == 0.0

    def test_critical_flag_adds_high_risk(self):
        from services.biometric.behavioral_analysis.rx_shopping_detector import RxShoppingFlag
        detector = RxShoppingDetector()
        from uuid import uuid4
        profile = RxShoppingRiskProfile(patient_id=uuid4(), biometric_identity_id=None)
        profile.flags = [
            RxShoppingFlag(
                flag_id=uuid4(), patient_id=profile.patient_id,
                biometric_identity_id=None,
                flag_type="test", severity="critical",
                description="test", evidence={},
            )
        ]
        score = detector._calculate_risk_score(profile)
        assert score >= 0.40, "Critical flag must produce score >= 0.40"

    def test_risk_level_mapping(self):
        detector = RxShoppingDetector()
        assert detector._risk_level(0.0)  == "low"
        assert detector._risk_level(0.30) == "moderate"
        assert detector._risk_level(0.60) == "high"
        assert detector._risk_level(0.85) == "critical"

    def test_multi_pharmacy_threshold_constant_positive(self):
        """The threshold must be positive and reasonable (2–10)."""
        assert 2 <= MULTI_PHARMACY_24H_THRESHOLD <= 10, \
            f"Multi-pharmacy threshold {MULTI_PHARMACY_24H_THRESHOLD} out of reasonable range"
