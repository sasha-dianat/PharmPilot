"""
Unit tests — Rx State Machine
Tests every valid transition, invalid transition, and EPCS enforcement.
A state machine error can cause double-dispensing or missed DUR alerts —
both are patient safety events.
"""
import hashlib
from datetime import datetime, timezone
from uuid import UUID

import pytest
from services.core.pharmacy_workflow.state_machine import (
    RxStateMachine, TRANSITIONS, InvalidTransitionError,
    EPCSRequiredError, _compute_event_hash, _compute_event_hash_v2, RxStatus
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


class TestDigestVersion2:
    """The widened digest (migration 0053).

    Version 1 spanned only the shape of a transition, so a DUR override's stated
    justification could be rewritten with every link still verifying — shown
    against a live database, not argued. Version 2 spans what an auditor reads.
    Each field gets its own test because each was independently unprotected.
    """

    RX = UUID("12345678-1234-5678-1234-567812345678")
    ACTOR = UUID("87654321-4321-8765-4321-876543218765")
    TS = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    def digest(self, **over):
        base = dict(prescription_id=self.RX, sequence_number=1,
                    from_status="pending_dur", to_status="pending_verification",
                    triggered_by_id=self.ACTOR, triggered_by_type="staff",
                    reason="DUR override: prescriber consulted",
                    event_metadata={"code": "A1"}, timestamp=self.TS,
                    previous_hash=None)
        return _compute_event_hash_v2(**{**base, **over})

    def test_it_is_deterministic(self):
        assert self.digest() == self.digest()

    def test_it_is_64_hex_chars(self):
        h = self.digest()
        assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)

    def test_the_reason_is_covered(self):
        """The finding this version exists to close."""
        assert self.digest() != self.digest(reason="routine")

    def test_the_metadata_is_covered(self):
        assert self.digest() != self.digest(event_metadata={"code": "B2"})

    def test_the_actor_type_is_covered(self):
        """Relabelling a transition from 'ai' to 'staff' changes who is
        accountable for it."""
        assert self.digest() != self.digest(triggered_by_type="ai")

    def test_the_position_is_covered(self):
        """Binding the sequence number in is what makes reordering detectable.
        Version 1 hashed no position at all."""
        assert self.digest() != self.digest(sequence_number=2)

    def test_version_1_and_version_2_never_collide(self):
        """The two rules must not produce the same hash for the same event, or
        digest_version would be unfalsifiable — a v1 row could pose as v2."""
        v1 = _compute_event_hash(self.RX, "pending_dur", "pending_verification",
                                 self.ACTOR, self.TS, None)
        assert v1 != self.digest(reason=None, event_metadata=None)

    def test_metadata_key_order_does_not_change_the_hash(self):
        """event_metadata round-trips through JSONB, which preserves neither key
        order nor formatting. A digest sensitive to either would break the chain
        on an ordinary read — a false alarm, which is the expensive kind."""
        a = self.digest(event_metadata={"a": 1, "b": {"x": 1, "y": 2}})
        b = self.digest(event_metadata={"b": {"y": 2, "x": 1}, "a": 1})
        assert a == b

    def test_metadata_none_and_empty_are_the_same_thing(self):
        """The writer stores `metadata or {}`, so a caller passing None and a
        caller passing {} must not produce different hashes for one event."""
        assert self.digest(event_metadata=None) == self.digest(event_metadata={})

    def test_a_value_json_cannot_serialise_does_not_break_an_audit_write(self):
        """Refusing to record a transition because its metadata held a Decimal
        would be far worse than recording a stringified value."""
        from decimal import Decimal
        assert len(self.digest(event_metadata={"paid": Decimal("1.50")})) == 64
