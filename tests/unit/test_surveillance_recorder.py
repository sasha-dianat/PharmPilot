"""Turning a fused identity and an RF fix into one durable observation.

The table has existed since migration 0032 and nothing wrote to it. These tests
pin the shape, and specifically the two invariants the schema enforces so that a
caller gets a clear Python error rather than an IntegrityError from Postgres.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from services.biometric.fusion import (
    Contribution, Exclusion, FusedIdentity)
from services.biometric.occlusion import OcclusionStratum
from services.core.rf_mapping import PositionFix
from services.core.surveillance.escalation import IDENTIFY_MANUALLY
from services.core.surveillance.recorder import build_observation

PHARMACY = uuid4()
NOW = datetime(2026, 8, 16, 9, 30, tzinfo=timezone.utc)


def a_fused(identity=None, decision="auto", confidence=0.995):
    return FusedIdentity(
        identity_id=identity or uuid4(), confidence=confidence,
        decision=decision, margin=0.4,
        contributions=[Contribution(modality="face", similarity=0.72,
                                    calibrated=0.999, quality=0.9, weight=0.77,
                                    log_lambda=-38.2)],
        excluded=[Exclusion(modality="gait", reason="quality 0.30 below floor")],
        explanation="2 modalities agree")


def test_fused_result_is_persisted_with_its_working():
    obs = build_observation(
        pharmacy_id=PHARMACY, site="pharmacy", action_type="entry",
        observed_at=NOW, fused=a_fused(), stratum=OcclusionStratum.MASK)

    assert obs["fusion_decision"] == "auto"
    assert obs["modalities_used"] == ["face"]
    # the exclusions travel with the row: an observation that cannot explain
    # what it discarded is not reviewable months later
    assert obs["fusion_detail"]["excluded"][0]["modality"] == "gait"
    assert obs["fusion_detail"]["occlusion_stratum"] == "mask"


def test_rf_fix_is_persisted_with_its_uncertainty():
    fix = PositionFix(x=8.0, y=6.0, uncertainty_m=2.5, method="fingerprint",
                      ap_count=4)
    obs = build_observation(
        pharmacy_id=PHARMACY, site="depot", action_type="movement",
        observed_at=NOW, fix=fix, rf_device_ref="dev-77")

    assert (obs["rf_x"], obs["rf_y"]) == (8.0, 6.0)
    assert obs["rf_uncertainty_m"] == 2.5
    assert obs["rf_method"] == "fingerprint"
    assert obs["rf_device_ref"] == "dev-77"


def test_observation_may_carry_neither_side():
    obs = build_observation(pharmacy_id=PHARMACY, site="depot",
                            action_type="exit", observed_at=NOW)
    assert obs["fusion_decision"] is None
    assert obs["rf_x"] is None


def test_site_must_be_one_the_schema_accepts():
    """ck_surv_obs_site allows only pharmacy|depot. Fail in Python with a clear
    message rather than as an IntegrityError from the driver."""
    with pytest.raises(ValueError, match="site"):
        build_observation(pharmacy_id=PHARMACY, site="warehouse",
                          action_type="entry", observed_at=NOW)


def test_a_coordinate_without_uncertainty_is_refused():
    """Mirrors ck_surv_obs_rf_uncertainty_required. A coordinate without its
    error bar invites false precision on a heatmap."""
    bad = PositionFix(x=1.0, y=2.0, uncertainty_m=0.0, method="trilateration",
                      ap_count=3)
    with pytest.raises(ValueError, match="uncertainty"):
        build_observation(pharmacy_id=PHARMACY, site="depot",
                          action_type="movement", observed_at=NOW, fix=bad)


def test_review_decision_is_recorded_as_review_not_promoted():
    obs = build_observation(
        pharmacy_id=PHARMACY, site="pharmacy", action_type="entry",
        observed_at=NOW, fused=a_fused(decision="review", confidence=0.5))
    assert obs["fusion_decision"] == "review"
    assert obs["biometric_identity_id"] is not None   # candidate is kept


def test_an_identify_manually_hint_is_never_stored_as_the_identity():
    """The escalation ladder's rule, enforced at the point of persistence: a
    hint is below IAL1. It exists to be compared against an answer the staff
    member obtains independently — storing it as the observation's identity
    would promote a guess into the record."""
    hint = uuid4()
    obs = build_observation(
        pharmacy_id=PHARMACY, site="pharmacy", action_type="entry",
        observed_at=NOW,
        fused=a_fused(identity=hint, decision=IDENTIFY_MANUALLY,
                      confidence=0.31))

    assert obs["fusion_decision"] == "identify_manually"
    assert obs["biometric_identity_id"] is None
    # but it is not thrown away — it travels in the working, clearly labelled
    assert obs["fusion_detail"]["hint_identity_id"] == str(hint)


def test_no_match_also_stores_no_identity():
    obs = build_observation(
        pharmacy_id=PHARMACY, site="pharmacy", action_type="entry",
        observed_at=NOW,
        fused=a_fused(identity=uuid4(), decision="no_match", confidence=0.0))
    assert obs["biometric_identity_id"] is None


def test_an_unknown_decision_is_refused():
    with pytest.raises(ValueError, match="decision"):
        build_observation(pharmacy_id=PHARMACY, site="pharmacy",
                          action_type="entry", observed_at=NOW,
                          fused=a_fused(decision="definitely"))
