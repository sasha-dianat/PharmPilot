"""Unit tests — Adherence prediction engine."""
import pytest
from datetime import date, timedelta
from services.ai.adherence_engine.predictor import (
    AdherencePredictor, AdherenceFeatureExtractor,
    AdherenceRisk, RISK_THRESHOLDS,
)


@pytest.fixture
def predictor():
    return AdherencePredictor()


@pytest.fixture
def extractor():
    return AdherenceFeatureExtractor()


class TestFeatureExtraction:

    def test_empty_history_returns_defaults(self, extractor):
        features = extractor.extract([], {"age": 50})
        assert features["n_fills"] == 0
        assert 0.0 <= features["historical_pdc"] <= 1.0

    def test_perfect_adherence_history(self, extractor):
        """Patient who always refills on time should have low avg_days_late and high PDC."""
        today = date.today()
        history = []
        for i in range(12):
            history.append({
                "fill_date": (today - timedelta(days=30 * (12 - i))).isoformat(),
                "days_supply": 30,
                "drug_name": "metformin",
                "copay": 10.0,
                "days_late": 0,
            })
        features = extractor.extract(history, {"age": 55, "n_medications": 5})
        assert features["n_fills"] == 12
        assert features["avg_days_late"] == 0.0

    def test_chronically_late_patient(self, extractor):
        today = date.today()
        history = [
            {"fill_date": (today - timedelta(days=35 * (6 - i))).isoformat(),
             "days_supply": 30, "days_late": 5, "copay": 15.0}
            for i in range(6)
        ]
        features = extractor.extract(history, {"age": 60})
        assert features["avg_days_late"] > 0, "Chronically late patient should have positive avg_days_late"


class TestRiskScoring:

    def test_perfect_adherent_is_low_risk(self, predictor):
        today = date.today()
        history = [
            {"fill_date": (today - timedelta(days=30 * (12 - i))).isoformat(),
             "days_supply": 30, "days_late": 0, "copay": 5.0}
            for i in range(12)
        ]
        score = predictor.predict(history, {"age": 50, "n_medications": 3, "distance_km": 2.0})
        assert score < 0.40, f"Perfect adherent should be low risk, got {score:.3f}"
        assert predictor.score_to_risk_level(score) == AdherenceRisk.LOW

    def test_high_gap_patient_is_high_risk(self, predictor):
        today = date.today()
        # Patient with multiple 30+ day gaps
        history = [
            {"fill_date": (today - timedelta(days=365)).isoformat(), "days_supply": 30, "days_late": 45, "copay": 60.0},
            {"fill_date": (today - timedelta(days=280)).isoformat(), "days_supply": 30, "days_late": 30, "copay": 60.0},
            {"fill_date": (today - timedelta(days=180)).isoformat(), "days_supply": 30, "days_late": 60, "copay": 60.0},
        ]
        score = predictor.predict(history, {"age": 70, "n_medications": 12, "distance_km": 20.0})
        risk = predictor.score_to_risk_level(score)
        assert risk in (AdherenceRisk.HIGH, AdherenceRisk.CRITICAL), \
            f"High-gap patient should be HIGH or CRITICAL risk, got {risk} (score={score:.3f})"

    def test_score_bounded_0_to_1(self, predictor):
        for _ in range(10):
            score = predictor.predict([], {"age": 50})
            assert 0.0 <= score <= 1.0, f"Score must be in [0,1], got {score}"

    def test_med_sync_reduces_risk(self, predictor):
        """Med sync enrollment is a protective factor that should reduce risk score."""
        base_history = [
            {"fill_date": "2024-01-01", "days_supply": 30, "days_late": 10, "copay": 25.0},
            {"fill_date": "2024-02-10", "days_supply": 30, "days_late": 10, "copay": 25.0},
        ]
        score_no_sync  = predictor.predict(base_history, {"age": 60, "n_medications": 8, "med_sync": False})
        score_with_sync = predictor.predict(base_history, {"age": 60, "n_medications": 8, "med_sync": True})
        assert score_with_sync < score_no_sync, "Med sync enrollment must reduce adherence risk score"


class TestRiskThresholds:

    def test_thresholds_are_contiguous(self):
        """Risk tiers must cover the entire [0, 1] range without gaps."""
        all_values = sorted(
            [(lo, hi) for lo, hi in RISK_THRESHOLDS.values()],
            key=lambda x: x[0]
        )
        # Check no gap between consecutive tiers
        for i in range(len(all_values) - 1):
            assert abs(all_values[i][1] - all_values[i+1][0]) < 0.02, \
                f"Gap between risk tiers at {all_values[i][1]}"

    def test_all_scores_map_to_a_risk_level(self):
        predictor = AdherencePredictor()
        for score in [0.0, 0.20, 0.41, 0.65, 0.66, 0.80, 0.81, 0.99]:
            level = predictor.score_to_risk_level(score)
            assert level in AdherenceRisk.__members__.values(), \
                f"Score {score} did not map to a valid risk level"
