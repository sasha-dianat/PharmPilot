"""
Unit tests — Rx Copilot Tier2-A (#15 pilot-phase auto-advance, undo, alerts)
============================================================================
These guard the clinical-safety invariants agreed for the pilot:

  • Only `adjudication` may ever be auto-EXECUTED; `dur`/`verification` are
    pure decision aids and must NEVER trigger a state transition.
  • Every transition STEP_TRANSITIONS/UNDO_PATHS performs must be a LEGAL
    adjacency in the live RxStateMachine.TRANSITIONS graph — a typo here
    would either silently no-op (InvalidTransitionError swallowed) or, far
    worse, desync the audited "ok" response from what the database actually
    did. This is exactly the class of bug caught during design review (a
    drafted ON_HOLD -> PENDING_ADJUDICATION undo leg turned out to be illegal).
  • Controlled substances must always surface an explicit alert.
  • staff_id coercion must never silently fabricate an identity.
"""
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from services.ai.intelligence_services import rx_copilot
from services.ai.intelligence_services.rx_copilot import (
    AUTO_EXECUTABLE_STEPS, STEP_TRANSITIONS, UNDO_PATHS,
    StepRecommendation, _coerce_uuid, _controlled_substance_alert,
    auto_advance, undo_auto_advance,
)
from services.core.pharmacy_workflow.state_machine import (
    TRANSITIONS, RxStatus, InvalidTransitionError,
)


# ─── Adjacency-legality of every transition the copilot can perform ───────────

class TestTransitionLegality:
    """
    Every (from, to) pair the copilot might execute — for auto-advance OR undo —
    must be a real edge in RxStateMachine.TRANSITIONS. If it isn't,
    RxStateMachine.transition() raises InvalidTransitionError and the copilot
    must handle that gracefully (covered below) — but ideally these tables
    never attempt an illegal hop in the first place.
    """

    def test_only_adjudication_is_auto_executable(self):
        # Locked per pharmacist guidance: DUR and verification stay manual.
        assert AUTO_EXECUTABLE_STEPS == frozenset({"adjudication"})

    def test_step_transitions_cover_every_auto_executable_step(self):
        for step in AUTO_EXECUTABLE_STEPS:
            assert step in STEP_TRANSITIONS, f"{step} is auto-executable but has no STEP_TRANSITIONS entry"

    def test_step_transitions_are_legal_adjacencies(self):
        for step, (from_status, to_status) in STEP_TRANSITIONS.items():
            assert to_status in TRANSITIONS.get(from_status, []), (
                f"STEP_TRANSITIONS[{step!r}] = {from_status.value} -> {to_status.value} "
                f"is not a legal transition; allowed: "
                f"{[s.value for s in TRANSITIONS.get(from_status, [])]}"
            )

    def test_undo_paths_cover_every_auto_executable_step(self):
        for step in AUTO_EXECUTABLE_STEPS:
            assert step in UNDO_PATHS, f"{step} is auto-executable but has no UNDO_PATHS entry"

    def test_undo_paths_are_legal_adjacency_chains(self):
        """
        Every leg of every undo path must be a real edge — AND each leg's
        destination must equal the next leg's origin (a connected chain).
        """
        for step, legs in UNDO_PATHS.items():
            assert legs, f"UNDO_PATHS[{step!r}] is empty"
            for i, (leg_from, leg_to) in enumerate(legs):
                assert leg_to in TRANSITIONS.get(leg_from, []), (
                    f"UNDO_PATHS[{step!r}] leg {i}: {leg_from.value} -> {leg_to.value} "
                    f"is not a legal transition; allowed: "
                    f"{[s.value for s in TRANSITIONS.get(leg_from, [])]}"
                )
                if i + 1 < len(legs):
                    next_from, _ = legs[i + 1]
                    assert leg_to == next_from, (
                        f"UNDO_PATHS[{step!r}] is disconnected: leg {i} ends at "
                        f"{leg_to.value} but leg {i + 1} starts at {next_from.value}"
                    )

    def test_undo_path_does_not_claim_to_reach_pre_advance_status(self):
        """
        Document/lock the design decision: undo intentionally does NOT try to
        rewind back to the exact pre-auto-advance status (no legal direct edge
        exists from READY_TO_FILL/ON_HOLD back to PENDING_ADJUDICATION). It
        lands the Rx back in the human review queue instead.
        """
        legs = UNDO_PATHS["adjudication"]
        from_status, _ = STEP_TRANSITIONS["adjudication"]
        final_status = legs[-1][1]
        assert final_status != from_status
        assert final_status == RxStatus.PENDING_VERIFICATION
        # And lock in *why*: there genuinely is no legal direct edge back.
        assert RxStatus.PENDING_ADJUDICATION not in TRANSITIONS[RxStatus.ON_HOLD]
        assert RxStatus.PENDING_ADJUDICATION not in TRANSITIONS[RxStatus.READY_TO_FILL]


# ─── _coerce_uuid ──────────────────────────────────────────────────────────────

class TestCoerceUuid:
    def test_passes_through_uuid(self):
        u = uuid4()
        assert _coerce_uuid(u) == u

    def test_parses_uuid_string(self):
        u = uuid4()
        assert _coerce_uuid(str(u)) == u

    def test_returns_none_for_garbage_string(self):
        assert _coerce_uuid("staff") is None
        assert _coerce_uuid("not-a-uuid") is None

    def test_returns_none_for_other_types(self):
        assert _coerce_uuid(None) is None
        assert _coerce_uuid(12345) is None


# ─── _controlled_substance_alert ───────────────────────────────────────────────

class TestControlledSubstanceAlert:
    def test_none_for_non_controlled(self):
        assert _controlled_substance_alert({"is_controlled": False}) is None
        assert _controlled_substance_alert({}) is None

    def test_alert_for_controlled_with_schedule(self):
        alert = _controlled_substance_alert({"is_controlled": True, "dea_schedule": "II"})
        assert alert["level"] == "warning"
        assert alert["code"] == "controlled_substance"
        assert alert["dea_schedule"] == "II"
        assert "Schedule II" in alert["message"]

    def test_alert_for_controlled_without_schedule(self):
        alert = _controlled_substance_alert({"is_controlled": True, "dea_schedule": None})
        assert alert is not None
        assert alert["dea_schedule"] is None
        assert "Controlled substance" in alert["message"]


# ─── auto_advance: manual-only steps must never transition anything ──────────

def _rx(status="pending_adjudication", is_controlled=False, dea_schedule=None, rx_id=None):
    return {
        "rx_id": rx_id or str(uuid4()), "rx_number": "RX-1", "ndc": "00000-0000-00",
        "drug_name": "Test Drug", "patient_id": str(uuid4()), "status": status,
        "is_controlled": is_controlled, "dea_schedule": dea_schedule,
    }


def _rec(step, confidence, can_auto):
    return StepRecommendation(
        step=step, confidence=confidence, can_auto=can_auto,
        recommendation="auto_clear" if can_auto else "review",
        rationale="test", signals=["test_signal"],
    )


@pytest.mark.parametrize("step", ["dur", "verification"])
async def test_auto_advance_manual_steps_never_transition(step):
    """
    Even if confidence is sky-high, dur/verification must come back as
    'manual_step_decision_aid_only' and RxStateMachine.transition must NOT
    be called — these steps are decision aids only, per pharmacist guidance.
    """
    db = AsyncMock()
    rx = _rx(status="pending_dur" if step == "dur" else "verification_in_progress")

    with patch.object(rx_copilot, "_load_rx", AsyncMock(return_value=rx)), \
         patch.object(rx_copilot, "_dur_confidence", AsyncMock(return_value=_rec("dur", 0.99, False))), \
         patch.object(rx_copilot, "_adjudication_confidence", AsyncMock(return_value=_rec("adjudication", 0.99, False))), \
         patch.object(rx_copilot, "_verification_confidence", AsyncMock(return_value=_rec("verification", 0.99, False))), \
         patch.object(rx_copilot, "RxStateMachine") as MockSM:
        result = await auto_advance(db, rx["rx_id"], step, staff_id=uuid4())

    assert result["ok"] is False
    assert result["reason"] == "manual_step_decision_aid_only"
    assert result["step"] == step
    MockSM.assert_not_called()
    db.commit.assert_not_called()


async def test_auto_advance_unknown_step_rejected():
    db = AsyncMock()
    with patch.object(rx_copilot, "_load_rx", AsyncMock(return_value=_rx())):
        result = await auto_advance(db, "rx-1", "dispense", staff_id=uuid4())
    assert result == {"ok": False, "reason": "unknown_step"}


async def test_auto_advance_rx_not_found():
    db = AsyncMock()
    with patch.object(rx_copilot, "_load_rx", AsyncMock(return_value=None)):
        result = await auto_advance(db, "rx-1", "adjudication", staff_id=uuid4())
    assert result == {"ok": False, "reason": "rx_not_found"}


# ─── auto_advance: adjudication (the only auto-executable step) ──────────────

async def test_auto_advance_adjudication_below_threshold_does_not_transition():
    db = AsyncMock()
    rx = _rx(status="pending_adjudication")
    with patch.object(rx_copilot, "_load_rx", AsyncMock(return_value=rx)), \
         patch.object(rx_copilot, "_adjudication_confidence", AsyncMock(return_value=_rec("adjudication", 0.5, False))), \
         patch.object(rx_copilot, "RxStateMachine") as MockSM:
        result = await auto_advance(db, rx["rx_id"], "adjudication", staff_id=uuid4())
    assert result["ok"] is False
    assert result["reason"] == "below_threshold"
    MockSM.assert_not_called()


async def test_auto_advance_adjudication_wrong_current_state_refused():
    """
    If the Rx has drifted out of PENDING_ADJUDICATION since evaluation (e.g.
    another workflow already moved it), we must not blindly attempt the
    transition — we check first and return a clear, specific reason.
    """
    db = AsyncMock()
    rx = _rx(status="filling")  # nowhere near pending_adjudication
    with patch.object(rx_copilot, "_load_rx", AsyncMock(return_value=rx)), \
         patch.object(rx_copilot, "_adjudication_confidence", AsyncMock(return_value=_rec("adjudication", 0.99, True))), \
         patch.object(rx_copilot, "RxStateMachine") as MockSM:
        result = await auto_advance(db, rx["rx_id"], "adjudication", staff_id=uuid4())
    assert result["ok"] is False
    assert result["reason"] == "rx_not_in_expected_state"
    assert result["expected_status"] == "pending_adjudication"
    assert result["actual_status"] == "filling"
    MockSM.assert_not_called()


async def test_auto_advance_adjudication_success_executes_legal_transition_and_audits():
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    rx = _rx(status="pending_adjudication")
    staff_id = uuid4()

    mock_sm_instance = MagicMock()
    mock_sm_instance.transition = AsyncMock()
    MockSM = MagicMock(return_value=mock_sm_instance)

    with patch.object(rx_copilot, "_load_rx", AsyncMock(return_value=rx)), \
         patch.object(rx_copilot, "_adjudication_confidence", AsyncMock(return_value=_rec("adjudication", 0.97, True))), \
         patch.object(rx_copilot, "RxStateMachine", MockSM):
        result = await auto_advance(db, rx["rx_id"], "adjudication", staff_id=staff_id)

    assert result["ok"] is True
    assert result["from_status"] == "pending_adjudication"
    assert result["to_status"] == "ready_to_fill"
    assert result["simulated"] is True
    assert result["pilot_phase"] is True
    assert "action_id" in result

    # Verify the EXACT (legal) transition was requested.
    mock_sm_instance.transition.assert_awaited_once()
    _, kwargs = mock_sm_instance.transition.call_args
    assert kwargs["to_status"] == RxStatus.READY_TO_FILL
    assert kwargs["triggered_by_id"] == staff_id
    assert kwargs["triggered_by_type"] == "ai"
    assert kwargs["metadata"]["simulated"] is True
    assert kwargs["metadata"]["pilot_phase"] is True
    assert "SIMULATED" in kwargs["reason"]

    # Audit row must have been inserted and the whole thing committed once.
    assert db.execute.await_count == 1
    db.commit.assert_awaited_once()


async def test_auto_advance_transition_refused_is_handled_gracefully():
    """
    If RxStateMachine itself refuses (e.g. InvalidTransitionError because the
    Rx moved between our state-check and the call), we must surface a clean
    'transition_refused' result — never let the exception propagate, and never
    insert a misleading 'ok: True' audit row.
    """
    db = AsyncMock()
    rx = _rx(status="pending_adjudication")

    mock_sm_instance = MagicMock()
    mock_sm_instance.transition = AsyncMock(side_effect=InvalidTransitionError("nope"))
    MockSM = MagicMock(return_value=mock_sm_instance)

    with patch.object(rx_copilot, "_load_rx", AsyncMock(return_value=rx)), \
         patch.object(rx_copilot, "_adjudication_confidence", AsyncMock(return_value=_rec("adjudication", 0.97, True))), \
         patch.object(rx_copilot, "RxStateMachine", MockSM):
        result = await auto_advance(db, rx["rx_id"], "adjudication", staff_id=uuid4())

    assert result["ok"] is False
    assert result["reason"] == "transition_refused"
    db.commit.assert_not_called()


# ─── undo_auto_advance ────────────────────────────────────────────────────────

def _action_row(step="adjudication", rx_id="rx-1", reverted=False):
    return {"id": "action-1", "rx_id": rx_id, "step": step, "reverted": reverted}


def _db_returning(action_row):
    """Stub db.execute so the first call (action lookup) returns `action_row`."""
    db = AsyncMock()
    select_result = MagicMock()
    select_result.mappings.return_value.first.return_value = action_row
    update_result = MagicMock()

    async def _execute(query, params=None):
        q = str(query).lower()
        if "select" in q and "rx_copilot_actions" in q:
            return select_result
        return update_result

    db.execute = AsyncMock(side_effect=_execute)
    db.commit = AsyncMock()
    return db


async def test_undo_action_not_found():
    db = _db_returning(None)
    result = await undo_auto_advance(db, "rx-1", "missing-action", staff_id=uuid4())
    assert result == {"ok": False, "reason": "action_not_found"}


async def test_undo_action_rx_mismatch():
    db = _db_returning(_action_row(rx_id="other-rx"))
    result = await undo_auto_advance(db, "rx-1", "action-1", staff_id=uuid4())
    assert result == {"ok": False, "reason": "action_rx_mismatch"}


async def test_undo_already_reverted():
    db = _db_returning(_action_row(reverted=True))
    result = await undo_auto_advance(db, "rx-1", "action-1", staff_id=uuid4())
    assert result == {"ok": False, "reason": "already_reverted"}


async def test_undo_unsupported_step():
    db = _db_returning(_action_row(step="dur"))  # dur was never auto-executable -> no undo path
    result = await undo_auto_advance(db, "rx-1", "action-1", staff_id=uuid4())
    assert result == {"ok": False, "reason": "undo_not_supported_for_step", "step": "dur"}


async def test_undo_rx_not_in_expected_state():
    db = _db_returning(_action_row())
    rx = _rx(status="dispensed", rx_id="rx-1")  # already dispensed — can't undo
    with patch.object(rx_copilot, "_load_rx", AsyncMock(return_value=rx)):
        result = await undo_auto_advance(db, "rx-1", "action-1", staff_id=uuid4())
    assert result["ok"] is False
    assert result["reason"] == "rx_not_in_expected_state"
    assert result["expected_status"] == "ready_to_fill"


async def test_undo_success_drives_every_leg_and_marks_reverted():
    db = _db_returning(_action_row())
    rx = _rx(status="ready_to_fill", rx_id="rx-1")
    staff_id = uuid4()

    mock_sm_instance = MagicMock()
    mock_sm_instance.transition = AsyncMock()
    MockSM = MagicMock(return_value=mock_sm_instance)

    with patch.object(rx_copilot, "_load_rx", AsyncMock(return_value=rx)), \
         patch.object(rx_copilot, "RxStateMachine", MockSM):
        result = await undo_auto_advance(db, "rx-1", "action-1", staff_id=staff_id)

    assert result["ok"] is True
    assert result["reverted"] is True
    assert result["legs"] == [["ready_to_fill", "on_hold"], ["on_hold", "pending_verification"]]
    assert result["final_status"] == "pending_verification"

    # Two legs => two transition calls, each individually audited.
    assert mock_sm_instance.transition.await_count == 2
    calls = mock_sm_instance.transition.call_args_list
    assert calls[0].kwargs["to_status"] == RxStatus.ON_HOLD
    assert calls[1].kwargs["to_status"] == RxStatus.PENDING_VERIFICATION
    for c in calls:
        assert c.kwargs["triggered_by_type"] == "staff"  # human undo decision, not "ai"
        assert c.kwargs["triggered_by_id"] == staff_id
        assert c.kwargs["metadata"]["undo_of_action_id"] == "action-1"

    db.commit.assert_awaited_once()


async def test_undo_partial_failure_preserves_completed_legs_and_does_not_mark_reverted():
    """
    If leg 2 fails, leg 1's transition must still be committed (the audit trail
    must reflect what actually happened to the Rx) — and the action must NOT be
    marked reverted (so it isn't silently treated as resolved).
    """
    db = _db_returning(_action_row())
    rx = _rx(status="ready_to_fill", rx_id="rx-1")

    mock_sm_instance = MagicMock()
    mock_sm_instance.transition = AsyncMock(
        side_effect=[None, InvalidTransitionError("blocked")]
    )
    MockSM = MagicMock(return_value=mock_sm_instance)

    with patch.object(rx_copilot, "_load_rx", AsyncMock(return_value=rx)), \
         patch.object(rx_copilot, "RxStateMachine", MockSM):
        result = await undo_auto_advance(db, "rx-1", "action-1", staff_id=uuid4())

    assert result["ok"] is False
    assert result["reason"] == "undo_partially_failed"
    assert result["completed_legs"] == [["ready_to_fill", "on_hold"]]
    db.commit.assert_awaited_once()  # the completed leg is persisted, not lost

    # The UPDATE ... reverted = TRUE must NOT have been issued.
    executed_queries = [str(c.args[0]).lower() for c in db.execute.call_args_list]
    assert not any("update" in q and "reverted" in q for q in executed_queries)
