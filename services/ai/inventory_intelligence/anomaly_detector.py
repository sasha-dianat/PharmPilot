"""
Local Inventory Anomaly Detection Engine — Phase 22-A
=======================================================
100% offline — uses only scikit-learn, numpy, scipy. No external API calls.

Three complementary detectors:

  IsolationForestDetector
    Multi-feature unsupervised anomaly detection per NDC.
    Features: quantity_on_hand, dispense_rate_7d, days_supply,
              velocity_change, hour_of_day_pattern, is_controlled
    Catches: sudden unexplained stock drops, zero-dispense accumulation,
             unusual dispense bursts, off-hours activity

  CUSUMMonitor
    Cumulative Sum control chart for sequential dispensing data.
    Triggers an alert when the running sum deviates beyond ±threshold.
    Catches: gradual diversion (slow creep that isolation forest misses),
             sustained demand spikes/dips post-formulary change

  DiversionScreener
    Rule-enhanced ML screener for controlled substances (C-I through C-V).
    Combines CUSUM, Isolation Forest, and pharmacy-specific rules:
      - Perpetual inventory discrepancy > 2% over rolling 7 days
      - Repeated small fill dispenses (structuring to avoid ARCOS threshold)
      - Night-shift dispense pattern anomaly
      - Staff-specific dispense rate deviation

All detectors are designed to train on historical data stored in the local
PostgreSQL database — no cloud model hosting, no model download required.
Models are serialized to disk with joblib for persistence across restarts.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from uuid import UUID

import numpy as np

log = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────
MODEL_DIR = Path(os.environ.get(
    "PHARMPILOT_MODEL_DIR",
    str(Path.home() / ".pharmpilot" / "models"),
))
MODEL_DIR.mkdir(parents=True, exist_ok=True)

ISOLATION_FOREST_CONTAMINATION = 0.05   # Expect ~5% anomalies in training data
CUSUM_SLACK          = 0.5              # Allowable slack in standard deviations
CUSUM_THRESHOLD      = 4.0             # Alert when cumulative sum exceeds 4σ
DIVERSION_DISC_PCT   = 0.02            # Flag if discrepancy > 2% of theoretical
MIN_TRAINING_SAMPLES = 30              # Minimum records to train a model


# ── Data Structures ───────────────────────────────────────────────────────────

@dataclass
class StockAnomalyAlert:
    ndc11:          str
    pharmacy_id:    str
    alert_type:     str          # isolation_forest | cusum_high | cusum_low | diversion | perpetual_discrepancy
    severity:       str          # low | moderate | high | critical
    confidence:     float        # 0–1
    description:    str
    metric_name:    str
    metric_value:   float
    expected_range: tuple[float, float]
    detected_at:    datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    requires_count: bool = False  # Flag for required physical count
    controlled_substance: bool = False


@dataclass
class CUSUMState:
    """Running state for a CUSUM monitor on one NDC time series."""
    ndc11:      str
    s_pos:      float = 0.0    # Upper cumulative sum
    s_neg:      float = 0.0    # Lower cumulative sum
    mean:       float = 0.0    # Reference mean (from calibration period)
    std:        float = 1.0    # Reference std
    n_samples:  int   = 0
    alert_high: bool  = False
    alert_low:  bool  = False
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── Isolation Forest Detector ──────────────────────────────────────────────────

class IsolationForestDetector:
    """
    Per-NDC anomaly scoring using scikit-learn IsolationForest.

    Feature vector (8 dimensions):
      0: qty_on_hand          — absolute stock level
      1: days_supply          — qty_on_hand / avg_daily_demand
      2: velocity_7d          — units dispensed last 7 days
      3: velocity_change_pct  — (velocity_7d - velocity_prior7d) / velocity_prior7d
      4: dispense_std_ratio   — today's dispense / rolling std (how unusual is today)
      5: hour_entropy         — entropy of dispense times (uniform = normal, skewed = suspicious)
      6: staff_count          — distinct staff dispensing this NDC last 7 days
      7: is_controlled        — 0 or 1 (higher sensitivity for controlled substances)
    """

    def __init__(self, pharmacy_id: str):
        self.pharmacy_id = pharmacy_id
        self._models: dict[str, object] = {}     # ndc11 → fitted IsolationForest
        self._global_model = None                # Fallback model trained on all NDCs
        self._scaler = None

    def _model_path(self, ndc11: str) -> Path:
        slug = hashlib.md5(f"{self.pharmacy_id}_{ndc11}".encode()).hexdigest()[:12]
        return MODEL_DIR / f"iso_forest_{slug}.joblib"

    def _global_model_path(self) -> Path:
        slug = hashlib.md5(self.pharmacy_id.encode()).hexdigest()[:12]
        return MODEL_DIR / f"iso_forest_global_{slug}.joblib"

    def _build_feature_vector(self, record: dict) -> np.ndarray:
        """
        Build the 8-dim feature vector from a stock snapshot record.

        record keys:
          qty_on_hand, avg_daily_demand, velocity_7d, velocity_prior7d,
          dispense_today, dispense_std, hour_entropy, staff_count, is_controlled
        """
        qty        = float(record.get("qty_on_hand", 0))
        demand     = max(float(record.get("avg_daily_demand", 1.0)), 0.01)
        days_sup   = qty / demand
        vel_7d     = float(record.get("velocity_7d", demand * 7))
        vel_p7d    = max(float(record.get("velocity_prior7d", vel_7d)), 0.01)
        vel_chg    = (vel_7d - vel_p7d) / vel_p7d
        disp_today = float(record.get("dispense_today", demand))
        disp_std   = max(float(record.get("dispense_std", demand * 0.3)), 0.01)
        std_ratio  = disp_today / disp_std
        hour_ent   = float(record.get("hour_entropy", 2.0))
        staff_cnt  = float(record.get("staff_count", 2))
        controlled = float(record.get("is_controlled", 0))

        return np.array([
            qty,
            days_sup,
            vel_7d,
            vel_chg,
            std_ratio,
            hour_ent,
            staff_cnt,
            controlled,
        ], dtype=np.float32)

    def train(self, records: list[dict], ndc11: Optional[str] = None) -> bool:
        """
        Train or retrain the Isolation Forest on historical stock records.
        If ndc11 is provided, trains an NDC-specific model.
        Otherwise trains the global fallback model.
        """
        if len(records) < MIN_TRAINING_SAMPLES:
            log.debug("Insufficient samples for IsolationForest training (ndc=%s, n=%d)", ndc11, len(records))
            return False

        try:
            from sklearn.ensemble import IsolationForest
            from sklearn.preprocessing import StandardScaler
            import joblib

            X = np.array([self._build_feature_vector(r) for r in records])
            scaler = StandardScaler()
            X_scaled = scaler.fit_transform(X)

            model = IsolationForest(
                n_estimators=200,
                contamination=ISOLATION_FOREST_CONTAMINATION,
                max_features=0.8,
                bootstrap=True,
                random_state=42,
                n_jobs=-1,
            )
            model.fit(X_scaled)

            path = self._model_path(ndc11) if ndc11 else self._global_model_path()
            joblib.dump({"model": model, "scaler": scaler}, str(path))

            if ndc11:
                self._models[ndc11] = {"model": model, "scaler": scaler}
                log.info("IsolationForest trained for NDC %s (n=%d samples)", ndc11, len(records))
            else:
                self._global_model = {"model": model, "scaler": scaler}
                log.info("Global IsolationForest trained (n=%d samples)", len(records))
            return True

        except Exception as exc:
            log.error("IsolationForest training failed: %s", exc)
            return False

    def _load_model(self, ndc11: str) -> Optional[dict]:
        """Load model from disk if not in memory."""
        if ndc11 in self._models:
            return self._models[ndc11]
        path = self._model_path(ndc11)
        if path.exists():
            try:
                import joblib
                bundle = joblib.load(str(path))
                self._models[ndc11] = bundle
                return bundle
            except Exception as exc:
                log.warning("Failed to load model for %s: %s", ndc11, exc)
        # Try global fallback
        if self._global_model is None:
            gpath = self._global_model_path()
            if gpath.exists():
                try:
                    import joblib
                    self._global_model = joblib.load(str(gpath))
                except Exception:
                    pass
        return self._global_model

    def score(self, record: dict) -> tuple[float, bool]:
        """
        Score a single stock record.
        Returns (anomaly_score, is_anomaly).
        anomaly_score: higher = more anomalous (0.0–1.0 scale, inverted from sklearn).
        """
        ndc11  = record.get("ndc11", "")
        bundle = self._load_model(ndc11)

        if bundle is None:
            # No model available — use statistical fallback
            return self._statistical_fallback(record)

        try:
            X = self._build_feature_vector(record).reshape(1, -1)
            X_scaled = bundle["scaler"].transform(X)
            raw_score = bundle["model"].decision_function(X_scaled)[0]
            # sklearn: negative = anomaly, positive = normal
            # Convert to 0–1 scale where 1 = most anomalous
            normalized = float(np.clip(1 - (raw_score + 0.5), 0, 1))
            prediction = bundle["model"].predict(X_scaled)[0]
            is_anomaly = prediction == -1
            return normalized, is_anomaly
        except Exception as exc:
            log.warning("IsolationForest scoring failed: %s", exc)
            return self._statistical_fallback(record)

    def _statistical_fallback(self, record: dict) -> tuple[float, bool]:
        """
        Simple z-score fallback when no trained model is available.
        Flags if stock is < 10% or > 200% of expected (demand × lead_time).
        """
        qty    = float(record.get("qty_on_hand", 0))
        demand = max(float(record.get("avg_daily_demand", 1.0)), 0.01)
        vel_7d = float(record.get("velocity_7d", demand * 7))
        expected_7d = demand * 7
        if expected_7d == 0:
            return 0.0, False
        velocity_ratio = vel_7d / expected_7d
        # Anomalous if velocity is < 20% or > 300% of expected
        if velocity_ratio < 0.2 or velocity_ratio > 3.0:
            score = min(1.0, abs(velocity_ratio - 1.0) / 3.0)
            return score, score > 0.5
        return 0.1, False

    def detect(self, snapshot: dict) -> Optional[StockAnomalyAlert]:
        """
        Run detection on a single NDC stock snapshot.
        Returns an alert if anomalous, None otherwise.
        """
        score, is_anomaly = self.score(snapshot)
        if not is_anomaly:
            return None

        ndc11 = snapshot.get("ndc11", "unknown")
        qty   = float(snapshot.get("qty_on_hand", 0))
        dem   = max(float(snapshot.get("avg_daily_demand", 1.0)), 0.01)

        severity = "moderate" if score < 0.75 else "high" if score < 0.90 else "critical"
        is_controlled = bool(snapshot.get("is_controlled", False))
        if is_controlled:
            severity = "high" if severity == "moderate" else "critical"

        return StockAnomalyAlert(
            ndc11=ndc11,
            pharmacy_id=self.pharmacy_id,
            alert_type="isolation_forest",
            severity=severity,
            confidence=score,
            description=(
                f"Anomalous stock pattern detected for {ndc11}. "
                f"Current: {qty:.0f} units ({qty/dem:.1f}d supply). "
                f"Anomaly score: {score:.2f}. Physical count recommended."
            ),
            metric_name="anomaly_score",
            metric_value=score,
            expected_range=(0.0, 0.5),
            requires_count=severity in ("high", "critical"),
            controlled_substance=is_controlled,
        )


# ── CUSUM Monitor ──────────────────────────────────────────────────────────────

class CUSUMMonitor:
    """
    Cumulative Sum (CUSUM) control chart for sequential dispense time-series.

    Used alongside Isolation Forest to catch slow gradual diversion that
    anomaly detection misses (one unit at a time over weeks).

    Algorithm: Tabular CUSUM (Lucas 1982)
      s_pos_t = max(0, s_pos_{t-1} + (x_t - μ)/σ - k)
      s_neg_t = max(0, s_neg_{t-1} - (x_t - μ)/σ - k)
      Alert when s_pos or s_neg > h
    where k = CUSUM_SLACK (slack parameter), h = CUSUM_THRESHOLD
    """

    def __init__(self):
        self._states: dict[str, CUSUMState] = {}

    def calibrate(self, ndc11: str, history: list[float]) -> CUSUMState:
        """
        Calibrate CUSUM parameters from historical dispense data.
        Call this on normal (pre-anomaly) data to establish μ and σ.
        """
        arr = np.array(history, dtype=float)
        state = CUSUMState(
            ndc11=ndc11,
            mean=float(np.median(arr)),       # Use median — more robust to outliers
            std=max(float(np.std(arr)), 0.1),  # Prevent division by zero
            n_samples=len(arr),
        )
        self._states[ndc11] = state
        return state

    def update(self, ndc11: str, value: float) -> Optional[StockAnomalyAlert]:
        """
        Update the CUSUM with a new observation.
        Returns an alert if the control limit is exceeded, None otherwise.
        """
        if ndc11 not in self._states:
            # Auto-calibrate with this single value (no history)
            self._states[ndc11] = CUSUMState(ndc11=ndc11, mean=value, std=max(value * 0.3, 0.1))

        state = self._states[ndc11]
        z = (value - state.mean) / state.std   # Standardized observation

        # Update upper and lower cumulative sums
        state.s_pos = max(0.0, state.s_pos + z - CUSUM_SLACK)
        state.s_neg = max(0.0, state.s_neg - z - CUSUM_SLACK)
        state.n_samples += 1
        state.last_updated = datetime.now(timezone.utc)

        # Check for threshold breach
        if state.s_pos > CUSUM_THRESHOLD and not state.alert_high:
            state.alert_high = True
            return StockAnomalyAlert(
                ndc11=ndc11,
                pharmacy_id="",
                alert_type="cusum_high",
                severity="high",
                confidence=min(1.0, state.s_pos / CUSUM_THRESHOLD),
                description=(
                    f"CUSUM upper control limit exceeded for {ndc11}. "
                    f"Sustained above-average dispense rate detected. "
                    f"Cumulative sum: {state.s_pos:.2f}σ (limit: {CUSUM_THRESHOLD}σ). "
                    f"May indicate demand spike or inventory counting error."
                ),
                metric_name="cusum_s_pos",
                metric_value=state.s_pos,
                expected_range=(0.0, CUSUM_THRESHOLD),
            )

        if state.s_neg > CUSUM_THRESHOLD and not state.alert_low:
            state.alert_low = True
            return StockAnomalyAlert(
                ndc11=ndc11,
                pharmacy_id="",
                alert_type="cusum_low",
                severity="high",
                confidence=min(1.0, state.s_neg / CUSUM_THRESHOLD),
                description=(
                    f"CUSUM lower control limit exceeded for {ndc11}. "
                    f"Sustained below-average dispense rate — possible diversion or counting error. "
                    f"Cumulative sum: {state.s_neg:.2f}σ (limit: {CUSUM_THRESHOLD}σ)."
                ),
                metric_name="cusum_s_neg",
                metric_value=state.s_neg,
                expected_range=(0.0, CUSUM_THRESHOLD),
                requires_count=True,
            )

        # Auto-reset after 2× threshold (new regime established)
        if state.s_pos > CUSUM_THRESHOLD * 2:
            state.s_pos = 0.0
            state.alert_high = False
        if state.s_neg > CUSUM_THRESHOLD * 2:
            state.s_neg = 0.0
            state.alert_low = False

        return None

    def reset(self, ndc11: str) -> None:
        """Reset a CUSUM after a pharmacist acknowledges and investigates."""
        if ndc11 in self._states:
            self._states[ndc11].s_pos = 0.0
            self._states[ndc11].s_neg = 0.0
            self._states[ndc11].alert_high = False
            self._states[ndc11].alert_low = False

    def get_state(self, ndc11: str) -> Optional[CUSUMState]:
        return self._states.get(ndc11)

    def all_states_dict(self) -> list[dict]:
        return [
            {
                "ndc11":       s.ndc11,
                "s_pos":       round(s.s_pos, 3),
                "s_neg":       round(s.s_neg, 3),
                "mean":        round(s.mean, 3),
                "std":         round(s.std, 3),
                "alert_high":  s.alert_high,
                "alert_low":   s.alert_low,
                "n_samples":   s.n_samples,
                "last_updated": s.last_updated.isoformat(),
            }
            for s in self._states.values()
        ]


# ── Diversion Screener ─────────────────────────────────────────────────────────

class DiversionScreener:
    """
    Enhanced screener for controlled substance diversion.

    Combines:
      1. Perpetual inventory discrepancy (theoretical vs physical)
      2. Isolation Forest anomaly score
      3. CUSUM sustained deviation
      4. Structuring pattern (many small fills to avoid reporting thresholds)
      5. Off-hours dispense pattern
      6. Staff-specific deviation

    Returns a DiversionRiskScore 0–100 and a recommended action.
    """

    SCHEDULE_WEIGHTS = {
        "CII":  2.0,   # Highest scrutiny
        "CIII": 1.6,
        "CIV":  1.4,
        "CV":   1.2,
        "CI":   1.0,   # Not commonly dispensed
    }

    def __init__(self, pharmacy_id: str):
        self.pharmacy_id    = pharmacy_id
        self._iso_detector  = IsolationForestDetector(pharmacy_id)
        self._cusum_monitor = CUSUMMonitor()

    def score(
        self,
        ndc11: str,
        dea_schedule: Optional[str],
        snapshot: dict,
        dispense_history: list[dict],
    ) -> dict:
        """
        Compute diversion risk score for one controlled substance.

        snapshot: current stock state dict (for IsolationForest)
        dispense_history: list of {date, qty, staff_id, hour} records

        Returns dict with score 0–100, flags, and recommended action.
        """
        weight = self.SCHEDULE_WEIGHTS.get(dea_schedule or "", 1.0)
        flags: list[str] = []
        component_scores: dict[str, float] = {}

        # ── 1. Perpetual Inventory Discrepancy ────────────────────────────────
        theoretical = float(snapshot.get("theoretical_qty", 0))
        physical    = float(snapshot.get("qty_on_hand", 0))
        if theoretical > 0:
            discrepancy_pct = abs(theoretical - physical) / theoretical
            component_scores["perpetual_discrepancy"] = min(1.0, discrepancy_pct / DIVERSION_DISC_PCT)
            if discrepancy_pct > DIVERSION_DISC_PCT:
                flags.append(f"perpetual_discrepancy_{discrepancy_pct:.1%}")
        else:
            component_scores["perpetual_discrepancy"] = 0.0

        # ── 2. Isolation Forest Score ─────────────────────────────────────────
        iso_score, is_anomaly = self._iso_detector.score(snapshot)
        component_scores["isolation_forest"] = iso_score
        if is_anomaly:
            flags.append("isolation_forest_anomaly")

        # ── 3. CUSUM Sustained Deviation ────────────────────────────────────
        if dispense_history:
            daily_qtys = [float(r.get("qty", 0)) for r in dispense_history]
            state = self._cusum_monitor.calibrate(ndc11, daily_qtys[:max(1, len(daily_qtys) - 7)])
            for qty in daily_qtys[-7:]:
                alert = self._cusum_monitor.update(ndc11, qty)
                if alert:
                    flags.append(f"cusum_{alert.alert_type}")
            cs = self._cusum_monitor.get_state(ndc11)
            cusum_score = min(1.0, max(cs.s_pos, cs.s_neg) / CUSUM_THRESHOLD) if cs else 0.0
            component_scores["cusum"] = cusum_score
        else:
            component_scores["cusum"] = 0.0

        # ── 4. Structuring Pattern ───────────────────────────────────────────
        # Many fills just below ARCOS reporting threshold (e.g., lots of 99-unit fills)
        if dispense_history:
            qtys = [float(r.get("qty", 0)) for r in dispense_history]
            # CII ARCOS threshold is typically 100 units for Schedule II opioids
            near_threshold = sum(1 for q in qtys if 90 <= q <= 99) / max(len(qtys), 1)
            structuring_score = min(1.0, near_threshold * 5)
            component_scores["structuring"] = structuring_score
            if structuring_score > 0.3:
                flags.append("structuring_pattern")
        else:
            component_scores["structuring"] = 0.0

        # ── 5. Off-Hours Dispense ────────────────────────────────────────────
        if dispense_history:
            off_hours = sum(
                1 for r in dispense_history
                if int(r.get("hour", 12)) < 7 or int(r.get("hour", 12)) > 22
            )
            off_hours_ratio = off_hours / max(len(dispense_history), 1)
            component_scores["off_hours"] = min(1.0, off_hours_ratio * 3)
            if off_hours_ratio > 0.1:
                flags.append(f"off_hours_dispense_{off_hours_ratio:.0%}")
        else:
            component_scores["off_hours"] = 0.0

        # ── 6. Staff Concentration ────────────────────────────────────────────
        if dispense_history:
            staff_counts: dict = {}
            for r in dispense_history:
                sid = r.get("staff_id", "unknown")
                staff_counts[sid] = staff_counts.get(sid, 0) + float(r.get("qty", 0))
            total_qty = sum(staff_counts.values())
            if total_qty > 0:
                max_staff_pct = max(staff_counts.values()) / total_qty
                # Flag if one staff member dispensed > 70% of a controlled substance
                staff_conc_score = min(1.0, max(0.0, max_staff_pct - 0.5) * 2)
                component_scores["staff_concentration"] = staff_conc_score
                if max_staff_pct > 0.70:
                    flags.append(f"staff_concentration_{max_staff_pct:.0%}")
            else:
                component_scores["staff_concentration"] = 0.0
        else:
            component_scores["staff_concentration"] = 0.0

        # ── Aggregate Score ────────────────────────────────────────────────────
        # Weighted average of components, then multiply by DEA schedule weight
        weights = {
            "perpetual_discrepancy": 0.30,
            "isolation_forest":      0.25,
            "cusum":                 0.20,
            "structuring":           0.10,
            "off_hours":             0.10,
            "staff_concentration":   0.05,
        }
        base_score = sum(
            component_scores.get(k, 0) * w for k, w in weights.items()
        )
        final_score = min(100, base_score * weight * 100)

        # Recommended action
        if final_score >= 75:
            action = "immediate_physical_count_and_incident_report"
            severity = "critical"
        elif final_score >= 50:
            action = "physical_count_within_24h"
            severity = "high"
        elif final_score >= 25:
            action = "enhanced_monitoring_flag"
            severity = "moderate"
        else:
            action = "continue_monitoring"
            severity = "low"

        return {
            "ndc11":               ndc11,
            "dea_schedule":        dea_schedule,
            "diversion_risk_score": round(final_score, 1),
            "severity":            severity,
            "recommended_action":  action,
            "flags":               flags,
            "component_scores":    {k: round(v, 3) for k, v in component_scores.items()},
            "evaluated_at":        datetime.now(timezone.utc).isoformat(),
        }


# ── Batch Screener ────────────────────────────────────────────────────────────

class InventoryAnomalyEngine:
    """
    Orchestrates all anomaly detectors for a pharmacy's full inventory.
    Called nightly by the Celery beat task and on-demand via API.
    """

    def __init__(self, pharmacy_id: str):
        self.pharmacy_id    = pharmacy_id
        self._iso_detector  = IsolationForestDetector(pharmacy_id)
        self._cusum_monitor = CUSUMMonitor()
        self._diversion     = DiversionScreener(pharmacy_id)

    def train_from_history(self, all_snapshots: list[dict]) -> dict:
        """
        Train all models from historical stock snapshots.
        Call monthly or after adding significant new inventory.
        """
        results = {"global_model": False, "ndc_models": 0}

        # Train global model
        if len(all_snapshots) >= MIN_TRAINING_SAMPLES:
            results["global_model"] = self._iso_detector.train(all_snapshots)

        # Train per-NDC models for high-volume drugs
        from collections import defaultdict
        by_ndc: dict = defaultdict(list)
        for s in all_snapshots:
            by_ndc[s.get("ndc11", "")].append(s)

        for ndc11, records in by_ndc.items():
            if len(records) >= MIN_TRAINING_SAMPLES:
                if self._iso_detector.train(records, ndc11=ndc11):
                    results["ndc_models"] += 1

        log.info(
            "IsolationForest training complete — global=%s, per_ndc=%d",
            results["global_model"], results["ndc_models"]
        )
        return results

    def screen_snapshot(self, snapshots: list[dict]) -> list[StockAnomalyAlert]:
        """
        Run all detectors across a list of current stock snapshots.
        Returns list of anomaly alerts, sorted by severity.
        """
        alerts: list[StockAnomalyAlert] = []

        severity_order = {"critical": 0, "high": 1, "moderate": 2, "low": 3}

        for snap in snapshots:
            # Isolation Forest
            alert = self._iso_detector.detect(snap)
            if alert:
                alert.pharmacy_id = self.pharmacy_id
                alerts.append(alert)

            # CUSUM on daily dispense qty
            daily_qty = float(snap.get("velocity_7d", 0)) / 7
            cusum_alert = self._cusum_monitor.update(snap.get("ndc11", ""), daily_qty)
            if cusum_alert:
                cusum_alert.pharmacy_id = self.pharmacy_id
                cusum_alert.ndc11 = snap.get("ndc11", "")
                alerts.append(cusum_alert)

        alerts.sort(key=lambda a: (severity_order.get(a.severity, 9), -a.confidence))
        return alerts

    def screen_controlled_substances(
        self,
        controlled_snapshots: list[dict],
    ) -> list[dict]:
        """
        Run diversion screener across all controlled substance snapshots.
        Each snapshot must include dea_schedule and dispense_history.
        """
        results = []
        for snap in controlled_snapshots:
            result = self._diversion.score(
                ndc11=snap.get("ndc11", ""),
                dea_schedule=snap.get("dea_schedule"),
                snapshot=snap,
                dispense_history=snap.get("dispense_history", []),
            )
            results.append(result)

        # Sort by risk score descending
        results.sort(key=lambda r: r["diversion_risk_score"], reverse=True)
        return results

    def get_cusum_dashboard(self) -> list[dict]:
        """Return current CUSUM state for all monitored NDCs — for dashboard display."""
        return self._cusum_monitor.all_states_dict()
