"""
Unit tests for the intake precompute service (council + triage caching).
Tests run without a real DB by stubbing the AsyncSession.
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from services.core.pharmacy_workflow.intake_precompute import IntakePrecomputeService


def _make_db(rx_data: dict | None = None, dur_alerts: list | None = None,
             conditions: list | None = None):
    """Build a minimal async-session stub returning controlled fixture data."""
    db = AsyncMock()

    def _empty():
        r = MagicMock()
        r.mappings.return_value.first.return_value = None
        r.mappings.return_value.all.return_value = []
        r.scalar.return_value = None
        return r

    async def _execute(query, params=None):
        q = str(query).lower()
        # Main Rx row — identified by selecting quantity_prescribed (only _load_rx does that)
        if "quantity_prescribed" in q:
            if rx_data:
                r = MagicMock()
                r.mappings.return_value.first.return_value = rx_data  # plain dict supports []
                return r
            return _empty()
        # DUR alerts — each row accessed via dict keys in _load_dur_alerts mappings
        elif "dur_alerts" in q:
            r = MagicMock()
            r.mappings.return_value.all.return_value = list(dur_alerts or [])  # list of dicts
            return r
        # Clinical notes / conditions
        elif "clinical_notes" in q:
            rows = [MagicMock(note_type="condition", content=c) for c in (conditions or [])]
            r = MagicMock(); r.mappings.return_value.all.return_value = rows; return r
        # Everything else → empty
        return _empty()

    db.execute = AsyncMock(side_effect=_execute)
    return db


def _std_rx():
    pid = str(uuid4())
    return {
        "id": str(uuid4()),
        "patient_id": pid,
        "drug_name": "Amlodipine",
        "drug_strength": "5mg",
        "sig_text": "Take 1 tablet daily",
        "quantity_prescribed": 30,
        "days_supply": 30,
        "refills_authorized": 5,
        "refills_remaining": 5,
        "dea_schedule": None,
        "is_controlled": False,
        "ndc": "00071015523",
    }


@pytest.fixture
def svc_and_db():
    rx = _std_rx()
    db = _make_db(rx_data=rx)
    svc = IntakePrecomputeService(db)
    return svc, db, rx


def test_precompute_completes_for_clean_rx(svc_and_db):
    svc, db, rx = svc_and_db
    result = asyncio.run(svc.run(rx["id"]))
    assert result["status"] == "ready"
    assert result["lane"] in ("green", "amber", "red")
    assert "council" in result


def test_precompute_produces_valid_council_cache(svc_and_db):
    svc, db, rx = svc_and_db
    result = asyncio.run(svc.run(rx["id"]))
    council = result["council"]
    # Required keys always present
    for key in ("generated_at", "blockers", "cautions", "hereditary",
                "total_findings", "has_blockers", "all_findings"):
        assert key in council


def test_precompute_green_lane_for_stable_chronic(svc_and_db):
    """A known patient's non-controlled refill with no DUR/council alerts → GREEN."""
    svc, db, _ = svc_and_db
    rx = _std_rx()
    rx["refills_remaining"] = 3     # not first fill
    rx["refills_authorized"] = 5
    db.execute = AsyncMock(side_effect=_make_db(rx_data=rx).execute.side_effect)

    # Patch triage to simulate conditions: refill + drug on profile.
    with patch("services.core.pharmacy_workflow.intake_precompute.ReviewTriageEngine") as mock_eng:
        from services.core.pharmacy_workflow.triage import TriageResult, ReviewLane
        mock_eng.return_value.classify.return_value = TriageResult(
            lane=ReviewLane.GREEN, reasons=["stable chronic"], hard_gates=[],
            one_tap_confirm=True, requires_visual_verification=True,
            suggested_priority=0.2,
        )
        result = asyncio.run(IntakePrecomputeService(db).run(rx["id"]))
    assert result["lane"] == "green"


def test_precompute_red_for_controlled_substance():
    rx = _std_rx()
    rx["drug_name"] = "Oxycodone"
    rx["is_controlled"] = True
    rx["dea_schedule"] = "CII"
    db = _make_db(rx_data=rx)
    result = asyncio.run(IntakePrecomputeService(db).run(rx["id"]))
    assert result["lane"] == "red"


def test_precompute_red_for_unresolved_hardstop():
    rx = _std_rx()
    db = _make_db(rx_data=rx, dur_alerts=[
        {"severity": "critical", "is_hard_stop": True, "was_overridden": False}
    ])
    result = asyncio.run(IntakePrecomputeService(db).run(rx["id"]))
    assert result["lane"] == "red"


def test_precompute_fails_gracefully_on_missing_rx():
    db = _make_db(rx_data=None)   # no Rx row
    result = asyncio.run(IntakePrecomputeService(db).run(uuid4()))
    assert result["status"] == "failed"
    assert "rx_not_found" in result["reason"]


def test_precompute_g6pd_relative_triggers_caution():
    """
    If the patient context carries a family member with فاویسم and an oxidant
    drug is prescribed, the council's hereditary specialist must fire a caution
    which lands in council_cache['hereditary'] or council_cache['cautions'].
    """
    rx = _std_rx()
    rx["drug_name"] = "Nitrofurantoin"

    db = _make_db(rx_data=rx)

    # Stub family profile load to return a G6PD-positive relative.
    original_execute = db.execute.side_effect
    async def patched_execute(query, params=None):
        q = str(query).lower()
        if "person_links" in q or "connected_component" in q.replace("_", " "):
            r = MagicMock()
            r.mappings.return_value.all.return_value = []
            return r
        return await original_execute(query, params)
    db.execute = AsyncMock(side_effect=patched_execute)

    # Override _build_patient_context to include a G6PD family member.
    with patch.object(
        IntakePrecomputeService,
        "_build_patient_context",
        return_value={
            "patient_id": rx["patient_id"],
            "active_medications": [],
            "diagnoses": [],
            "inherited_conditions": [],
            "allergies": [],
            "labs": {},
            "pharmacogenomics": None,
        },
    ), patch.object(
        IntakePrecomputeService,
        "_load_family_profiles_for_council",
        create=True,
        return_value=[{"relationship": "brother", "diagnoses": ["فاویسم"],
                       "inherited_conditions": []}],
    ):
        # The council specialist will use its own _load_family_profiles — patch at DB level.
        async def council_family_stub(*args, **kwargs):
            return [{"relationship": "brother", "diagnoses": ["فاویسم"],
                     "inherited_conditions": []}]
        with patch(
            "services.ai.clinical_brain.council.specialist_council.SpecialistCouncil"
            "._load_family_profiles",
            new=council_family_stub,
        ):
            result = asyncio.run(IntakePrecomputeService(db).run(rx["id"]))

    assert result["status"] == "ready"
    council = result["council"]
    all_f = council.get("all_findings", [])
    # Either cautions or hereditary list should contain the G6PD finding.
    combined = council.get("cautions", []) + council.get("hereditary", []) + all_f
    g6pd_hit = any("g6pd" in str(f).lower() or "favism" in str(f).lower()
                   or "oxidant" in str(f).lower() for f in combined)
    assert g6pd_hit, f"G6PD finding not in council output: {council}"
