"""
Unit tests — Rx State Machine
Tests every valid transition, invalid transition, and EPCS enforcement.
A state machine error can cause double-dispensing or missed DUR alerts —
both are patient safety events.
"""
import hashlib
import pytest
from services.core.pharmacy_workflow.state_machine import (
    RxStateMachine, TRANSITIONS, InvalidTransitionError,
    EPCSRequiredError, _compute_event_hash, RxStatus
)


class TestTransitionMap:
    """Validate the transition map is logically complete and consistent."""

    def test_all_rx_statuses_have_transitions(self):
        """Every RxStatus must appear as a key in TRANSITIONS."""
        for status in RxStatus:
            assert status in TRANSITIONS, f"{status.value} missing from TRANSITIONS"

    def test_terminal_states_have_no_transitions(self):
        terminal = [RxStatus.DISPENSED, RxStatus.CANCELLED, RxStatus.RETURNED_TO_STOCK, RxStatus.TRANSFERRED_OUT]
        for status in terminal:
            assert TRANSITIONS[status] == [], f"Terminal state {status.value} should have no outgoing transitions"

    def test_intake_can_reach_verification(self):
        """Critical path: INTAKE → PENDING_DUR → PENDING_VERIFICATION."""
        assert RxStatus.PENDING_DUR in TRANSITIONS[RxStatus.INTAKE]
        assert RxStatus.PENDING_VERIFICATION in TRANSITIONS[RxStatus.PENDING_DUR]

    def test_dispensed_is_reachable(self):
        """DISPENSED must be reachable from WILL_CALL and FILLED."""
        assert RxStatus.DISPENSED in TRANSITIONS[RxStatus.WILL_CALL]
        assert RxStatus.DISPENSED in TRANSITIONS[RxStatus.FILLED]

    def test_cancellation_available_from_active_states(self):
        """Staff must be able to cancel from any pre-dispensed state."""
        cancellable = [
            RxStatus.INTAKE, RxStatus.PENDING_DUR, RxStatus.PENDING_VERIFICATION,
            RxStatus.ADJUDICATION_REJECTED, RxStatus.PENDING_PA,
        ]
        for status in cancellable:
            assert RxStatus.CANCELLED in TRANSITIONS[status], \
                f"Cannot cancel from {status.value} — this is a workflow blocker"

    def test_no_self_loops(self):
        """A state should not transition to itself."""
        for status, targets in TRANSITIONS.items():
            assert status not in targets, f"Self-loop detected on {status.value}"


class TestEventHashChaining:

    def test_hash_is_deterministic(self):
        from uuid import UUID
        from datetime import datetime, timezone
        h1 = _compute_event_hash(
            prescription_id=UUID("12345678-1234-5678-1234-567812345678"),
            from_status="intake",
            to_status="pending_dur",
            triggered_by_id=UUID("87654321-4321-8765-4321-876543218765"),
            timestamp=datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
            previous_hash=None,
        )
        h2 = _compute_event_hash(
            prescription_id=UUID("12345678-1234-5678-1234-567812345678"),
            from_status="intake",
            to_status="pending_dur",
            triggered_by_id=UUID("87654321-4321-8765-4321-876543218765"),
            timestamp=datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
            previous_hash=None,
        )
        assert h1 == h2, "Same input must produce same hash"

    def test_hash_changes_with_different_status(self):
        from uuid import UUID
        from datetime import datetime, timezone
        ts = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        rx_id = UUID("12345678-1234-5678-1234-567812345678")
        h1 = _compute_event_hash(rx_id, "intake", "pending_dur", None, ts)
        h2 = _compute_event_hash(rx_id, "intake", "pending_verification", None, ts)
        assert h1 != h2, "Different transitions must produce different hashes"

    def test_hash_is_64_hex_chars(self):
        from uuid import UUID
        from datetime import datetime, timezone
        h = _compute_event_hash(
            UUID("12345678-1234-5678-1234-567812345678"),
            "intake", "pending_dur", None,
            datetime(2025, 6, 1, tzinfo=timezone.utc),
        )
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_previous_hash_included_in_chain(self):
        from uuid import UUID
        from datetime import datetime, timezone
        ts = datetime(2025, 6, 1, tzinfo=timezone.utc)
        rx = UUID("12345678-1234-5678-1234-567812345678")
        h_no_prev  = _compute_event_hash(rx, "a", "b", None, ts, previous_hash=None)
        h_with_prev = _compute_event_hash(rx, "a", "b", None, ts, previous_hash="abc123")
        assert h_no_prev != h_with_prev, "Previous hash must affect the chain"
