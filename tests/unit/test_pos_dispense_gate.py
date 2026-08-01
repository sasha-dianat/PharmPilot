"""Collecting payment must never be able to dispense a prescription.

`POST /pos/collect-payment` used to run this, as raw SQL, with the comment
"best-effort":

    UPDATE prescriptions SET status = 'dispensed'
    WHERE id = :rx_id AND status NOT IN ('cancelled', 'voided')

Every one of the fifteen other statuses qualified — including **DUR_HOLD**, the
status whose entire purpose is to stop a dispense on a dangerous interaction.
It bypassed RxStateMachine, the SHA-256 hash-chained RxStateEvent trail, and the
EPCS check for controlled substances. Taking a customer's money dispensed the
medicine.

The state machine's own docstring says "All transitions go through this class —
no direct status updates permitted." These tests hold the POS endpoint to that.

Payment and dispensing are separate events: paying for a prescription that is
still being filled is ordinary pharmacy practice, so payment is always recorded.
What must not happen is the status moving anywhere the state machine forbids.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.core.pharmacy_workflow.state_machine import TRANSITIONS
from shared.models.prescription import RxStatus

POS = Path("services/platform/routers/pos.py")


# ── the contract, expressed against the state machine itself ─────────────

def test_dispensed_is_reachable_from_only_two_states():
    """Anchors the tests below: if the workflow ever legitimately adds another
    predecessor, this fails and the POS gate is re-examined deliberately."""
    sources = {s for s, allowed in TRANSITIONS.items() if RxStatus.DISPENSED in allowed}
    assert sources == {RxStatus.FILLED, RxStatus.WILL_CALL}


@pytest.mark.parametrize("blocked", [
    RxStatus.INTAKE,
    RxStatus.PENDING_DUR,
    RxStatus.DUR_HOLD,                    # the one that matters most
    RxStatus.PENDING_VERIFICATION,
    RxStatus.VERIFICATION_IN_PROGRESS,
    RxStatus.PENDING_ADJUDICATION,
    RxStatus.ADJUDICATION_REJECTED,
    RxStatus.PENDING_PA,
    RxStatus.READY_TO_FILL,
    RxStatus.FILLING,
    RxStatus.ON_HOLD,
    RxStatus.RETURNED_TO_STOCK,
])
def test_these_states_may_never_reach_dispensed(blocked):
    assert RxStatus.DISPENSED not in TRANSITIONS.get(blocked, []), (
        f"{blocked.value} must not transition straight to dispensed")


# ── the endpoint must not write status itself ────────────────────────────

def _collect_payment_body() -> str:
    src = POS.read_text(encoding="utf-8")
    start = src.index("async def collect_payment")
    nxt = src.find("\n@router.", start)
    return src[start: nxt if nxt != -1 else len(src)]


def test_collect_payment_does_not_update_status_by_raw_sql():
    body = _collect_payment_body()
    offenders = re.findall(r"UPDATE\s+prescriptions[\s\S]{0,400}?status\s*=", body,
                           flags=re.IGNORECASE)
    assert not offenders, (
        "collect_payment writes prescriptions.status directly. Status changes "
        "must go through RxStateMachine.transition() so the transition is "
        "validated, hash-chained into RxStateEvent, and EPCS-checked.")


def test_collect_payment_routes_through_the_state_machine():
    body = _collect_payment_body()
    assert "RxStateMachine" in body and "transition" in body, (
        "collect_payment must use RxStateMachine.transition() to move an Rx to "
        "DISPENSED")


def test_collect_payment_only_attempts_dispense_from_a_legal_state():
    """The endpoint must consult the state machine's own table rather than
    hard-coding a status list that can drift away from it."""
    body = _collect_payment_body()
    assert ("TRANSITIONS" in body or "DISPENSABLE" in body), (
        "the dispensable predecessor states must be derived from the state "
        "machine, not restated as a literal in the POS router")


def test_payment_is_still_recorded_regardless_of_dispensability():
    """Paying for an Rx that is still being filled is ordinary practice. The
    safety property is 'do not dispense', not 'do not take money'."""
    body = _collect_payment_body()
    insert_at = body.find("INSERT INTO payment_events")
    assert insert_at != -1, "payment must still be recorded"
    # the payment insert must not sit behind a dispensability check
    guard = re.search(r"if\s+.*dispensable.*:", body[:insert_at], flags=re.IGNORECASE)
    assert guard is None, (
        "the payment_events insert must not be conditional on the Rx being "
        "dispensable — record the money, withhold the medicine")


def test_response_reports_whether_the_rx_was_dispensed():
    """Silence here is how a blocked dispense becomes a handed-over bag: staff
    see a successful payment and assume the workflow completed."""
    src = POS.read_text(encoding="utf-8")
    model = src[src.index("class CollectPaymentResponse"):]
    model = model[:model.index("\nclass ")]
    assert "dispensed" in model, (
        "CollectPaymentResponse must state whether the Rx was dispensed")
    assert "rx_status" in model, (
        "CollectPaymentResponse must return the resulting Rx status")


def test_blocked_dispense_carries_a_reason():
    body = _collect_payment_body()
    assert "dispense_blocked_reason" in body, (
        "a blocked dispense must explain itself so the counter can act on it")


# ── functional: drive the handler and watch what SQL it emits ────────────

class _Result:
    def __init__(self, row): self._row = row
    def mappings(self): return self
    def first(self): return self._row


class StubDb:
    """Records every statement so the test can assert what was written."""
    def __init__(self, rx_row):
        self.rx_row = rx_row
        self.sql: list[str] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, stmt, params=None):
        text_ = str(stmt)
        self.sql.append(" ".join(text_.split()))
        if "SELECT p.rx_number" in text_:
            return _Result(self.rx_row)
        return _Result(None)

    async def commit(self): self.commits += 1
    async def rollback(self): self.rollbacks += 1


def _request(rx_id="11111111-1111-1111-1111-111111111111"):
    from services.platform.routers import pos
    return pos.CollectPaymentRequest(
        rx_id=rx_id, tender_type="cash", amount_tendered=10.0,
        collected_by="someone-who-typed-their-own-name")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["dur_hold", "pending_dur", "filling", "on_hold"])
async def test_paying_does_not_dispense_a_prescription_that_is_not_ready(status):
    """The core regression. Money changes hands; the medicine does not."""
    from services.platform.routers import pos
    db = StubDb({"rx_number": "RX-1", "status": status, "patient_pay": 10.0})

    resp = await pos.collect_payment(
        body=_request(), db=db, _current={"staff_id": str(__import__("uuid").uuid4())})

    assert resp.dispensed is False
    assert resp.rx_status == status
    assert resp.dispense_blocked_reason and status in resp.dispense_blocked_reason
    # the payment IS recorded …
    assert any("INSERT INTO payment_events" in q for q in db.sql)
    # … and nothing ever writes the status
    assert not any("UPDATE prescriptions" in q for q in db.sql), db.sql


@pytest.mark.asyncio
async def test_dur_hold_specifically_is_never_dispensed():
    """Called out on its own because this is the status whose entire purpose is
    to stop a dispense on a dangerous interaction."""
    from services.platform.routers import pos
    db = StubDb({"rx_number": "RX-2", "status": "dur_hold", "patient_pay": 0.0})

    resp = await pos.collect_payment(
        body=_request(), db=db, _current={"staff_id": str(__import__("uuid").uuid4())})

    assert resp.dispensed is False
    assert "dur_hold" in resp.rx_status
    assert not any("UPDATE prescriptions" in q for q in db.sql)
