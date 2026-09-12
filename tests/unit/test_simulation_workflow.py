"""The workflow and audit oracle — what may happen, and what the chain proves.

Two properties carry this domain and both are pinned here: a transition graph
re-derived from the specification rather than imported from the implementation,
and a hash chain recomputed from its documented definition so that the audit
findings are demonstrated rather than asserted.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tests.simulation.domains.workflow import (
    AUDITED_BUT_UNHASHED, CANCELLED, DISPENSED, FILLED, FILLING, HASHED_FIELDS,
    INTAKE, LEGAL, ON_HOLD, PENDING_ADJUDICATION, PENDING_DUR,
    PENDING_VERIFICATION, READY_TO_FILL, TERMINAL, TRANSFERRED_OUT,
    VERIFICATION_IN_PROGRESS, WILL_CALL, WorkflowDomain, event_digest,
    event_digest_v2)
from tests.simulation.spine.clock import SimClock
from tests.simulation.spine.domain import Domain
from tests.simulation.spine.facts import Fact
from tests.simulation.spine.harness import Harness

START = date(2026, 1, 1)
T0 = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)

HAPPY_PATH = [
    (None, INTAKE), (INTAKE, PENDING_DUR), (PENDING_DUR, PENDING_VERIFICATION),
    (PENDING_VERIFICATION, VERIFICATION_IN_PROGRESS),
    (VERIFICATION_IN_PROGRESS, PENDING_ADJUDICATION),
    (PENDING_ADJUDICATION, READY_TO_FILL), (READY_TO_FILL, FILLING),
    (FILLING, FILLED), (FILLED, DISPENSED),
]


def step(w, rx, frm, to, actor="alice", seq=1, at=T0, **kw):
    w.observe(Fact(seq=seq, at=at, day=0, kind="transitioned", subject=rx,
                   actor=actor, payload={"rx": rx, "from": frm, "to": to, **kw}))


def walk(w, rx="RX1", path=HAPPY_PATH, actor="alice"):
    for i, (frm, to) in enumerate(path):
        step(w, rx, frm, to, actor=actor, seq=i + 1,
             at=T0 + timedelta(minutes=i))


# ── the legal graph ───────────────────────────────────────────────────────
def test_the_documented_happy_path_is_legal_end_to_end():
    w = WorkflowDomain()
    walk(w)
    assert w._findings == []
    assert w.violations() == []
    assert w.history["RX1"].status == DISPENSED


def test_a_transition_the_specification_does_not_allow_is_reported():
    """Intake straight to dispensed skips DUR, verification and adjudication."""
    w = WorkflowDomain()
    step(w, "RX1", None, INTAKE)
    step(w, "RX1", INTAKE, DISPENSED, seq=2)
    assert any("is not a legal transition" in f for f in w._findings)


def test_a_prescription_cannot_begin_anywhere_but_intake():
    w = WorkflowDomain()
    step(w, "RX1", None, READY_TO_FILL)
    assert any("not a legal transition" in f for f in w._findings)


def test_nothing_leaves_a_terminal_state():
    for terminal in sorted(TERMINAL):
        assert LEGAL.get(terminal) == set(), terminal


def test_moving_out_of_a_terminal_state_is_reported():
    w = WorkflowDomain()
    step(w, "RX1", DISPENSED, FILLING)
    assert any("moved out of terminal state" in f for f in w._findings)


def test_transferred_out_is_declared_but_unreachable():
    """A terminal state with no incoming edge. Either transfer-out is a real
    pharmacy operation missing its edge, or the status is dead vocabulary — and
    the graph cannot tell you which, only that one of them is true."""
    assert TRANSFERRED_OUT in LEGAL
    assert TRANSFERRED_OUT in WorkflowDomain().unreachable_states()


def test_every_other_state_is_reachable_from_intake():
    assert WorkflowDomain().unreachable_states() == [TRANSFERRED_OUT]


def test_a_hold_can_always_be_left():
    """A state you can enter and not leave strands the prescription."""
    assert LEGAL[ON_HOLD]
    for state, nxt in LEGAL.items():
        if state not in TERMINAL:
            assert nxt, f"{state} is a non-terminal dead end"


# ── the specification gate ────────────────────────────────────────────────
def test_the_specification_and_the_implementation_agree_today():
    """If this fails the lifecycle moved. That is a different conversation from
    the application taking an illegal step."""
    drift = WorkflowDomain().graph_drift()
    assert drift == [], f"graph drift: {drift}"


def test_graph_drift_never_leaks_into_check():
    w = WorkflowDomain()
    assert w._findings == [] and w.violations() == []


# ── the chain ─────────────────────────────────────────────────────────────
def test_a_clean_chain_verifies():
    w = WorkflowDomain()
    walk(w)
    hashes = w.chain("RX1")
    stored = [
        {"prescription_id": "RX1", "from_status": s.frm, "to_status": s.to,
         "triggered_by_id": s.actor, "created_at": s.at, "event_hash": h}
        for s, h in zip(w.history["RX1"].steps, hashes)]
    assert w.chain_findings("RX1", stored) == []


def test_a_tampered_status_breaks_the_chain():
    w = WorkflowDomain()
    walk(w)
    hashes = w.chain("RX1")
    stored = [
        {"prescription_id": "RX1", "from_status": s.frm, "to_status": s.to,
         "triggered_by_id": s.actor, "created_at": s.at, "event_hash": h}
        for s, h in zip(w.history["RX1"].steps, hashes)]
    stored[4]["to_status"] = CANCELLED          # rewrite history
    found = w.chain_findings("RX1", stored)
    assert any("the chain is broken at this link" in f for f in found)


def test_replacing_a_stored_hash_breaks_exactly_two_links_and_stops():
    """A verifier that reports every later event as broken buries the one place
    the tampering happened; one that reports too few hides the blast radius.

    Replacing a stored hash breaks exactly two: the tampered event, and its
    successor, whose own digest embedded the original value. From there the
    verifier resynchronises to what is stored and the rest re-verifies. The
    two-link signature is itself diagnostic — it distinguishes a rewritten hash
    from an edited field, which breaks only one."""
    w = WorkflowDomain()
    walk(w)
    hashes = w.chain("RX1")
    stored = [
        {"prescription_id": "RX1", "from_status": s.frm, "to_status": s.to,
         "triggered_by_id": s.actor, "created_at": s.at, "event_hash": h}
        for s, h in zip(w.history["RX1"].steps, hashes)]
    stored[3]["event_hash"] = "0" * 64
    breaks = [f for f in w.chain_findings("RX1", stored) if "broken at this link" in f]
    assert len(breaks) == 2
    assert "event 3" in breaks[0] and "event 4" in breaks[1]


def test_editing_a_field_breaks_only_its_own_link():
    """The contrast with the test above: the successor's digest still embeds the
    untouched stored hash, so the damage does not spread."""
    w = WorkflowDomain()
    walk(w)
    hashes = w.chain("RX1")
    stored = [
        {"prescription_id": "RX1", "from_status": s.frm, "to_status": s.to,
         "triggered_by_id": s.actor, "created_at": s.at, "event_hash": h}
        for s, h in zip(w.history["RX1"].steps, hashes)]
    stored[3]["triggered_by_id"] = "mallory"
    breaks = [f for f in w.chain_findings("RX1", stored) if "broken at this link" in f]
    assert len(breaks) == 1 and "event 3" in breaks[0]


def test_a_changed_actor_breaks_the_chain():
    w = WorkflowDomain()
    walk(w)
    hashes = w.chain("RX1")
    stored = [
        {"prescription_id": "RX1", "from_status": s.frm, "to_status": s.to,
         "triggered_by_id": s.actor, "created_at": s.at, "event_hash": h}
        for s, h in zip(w.history["RX1"].steps, hashes)]
    stored[2]["triggered_by_id"] = "mallory"
    assert any("broken at this link" in f for f in w.chain_findings("RX1", stored))


# ── the two audit findings ────────────────────────────────────────────────
def test_the_digest_does_not_cover_the_fields_an_auditor_reads():
    """reason, metadata and actor type are what a regulator reads when asking
    why an override happened. The chain is tamper-evident about the shape of a
    transition and silent about its stated justification."""
    gap = WorkflowDomain().hash_coverage_gap()
    assert set(gap) == set(AUDITED_BUT_UNHASHED)
    for f in gap:
        assert f not in HASHED_FIELDS


def test_editing_a_reason_leaves_every_hash_valid():
    """Demonstrated on two records that genuinely differ — hashing one record
    twice would prove nothing."""
    w = WorkflowDomain()
    before = {"prescription_id": "RX1", "from_status": PENDING_DUR,
              "to_status": PENDING_VERIFICATION, "triggered_by_id": "alice",
              "created_at": T0, "previous_hash": None,
              "reason": "DUR override: prescriber consulted",
              "triggered_by_type": "staff"}
    after = dict(before, reason="routine", triggered_by_type="system")
    intact, changed = w.unprotected_edit(before, after)
    assert intact is True
    assert changed == ["reason", "triggered_by_type"]


def test_editing_a_status_does_not_go_unnoticed():
    """The complement: the chain is not worthless, and saying so precisely is
    the difference between a finding and a scare."""
    w = WorkflowDomain()
    before = {"prescription_id": "RX1", "from_status": PENDING_DUR,
              "to_status": PENDING_VERIFICATION, "triggered_by_id": "alice",
              "created_at": T0, "previous_hash": None}
    after = dict(before, to_status=CANCELLED)
    intact, changed = w.unprotected_edit(before, after)
    assert intact is False and changed == ["to_status"]


def test_events_sharing_a_timestamp_make_the_predecessor_arbitrary():
    """created_at is server_default=func.now(), and Postgres now() is
    transaction-start time — so two transitions in one transaction carry
    byte-identical timestamps while the writer picks its predecessor with
    ORDER BY created_at DESC LIMIT 1."""
    w = WorkflowDomain()
    stored = [
        {"prescription_id": "RX1", "from_status": None, "to_status": INTAKE,
         "triggered_by_id": "alice", "created_at": T0, "event_hash": "x"},
        {"prescription_id": "RX1", "from_status": INTAKE,
         "to_status": PENDING_DUR, "triggered_by_id": "alice",
         "created_at": T0, "event_hash": "y"},
    ]
    found = w.chain_findings("RX1", stored)
    assert any("share created_at" in f and "arbitrary" in f for f in found)


def test_distinct_timestamps_raise_no_ordering_complaint():
    w = WorkflowDomain()
    walk(w)
    hashes = w.chain("RX1")
    stored = [
        {"prescription_id": "RX1", "from_status": s.frm, "to_status": s.to,
         "triggered_by_id": s.actor, "created_at": s.at, "event_hash": h}
        for s, h in zip(w.history["RX1"].steps, hashes)]
    assert not any("share created_at" in f for f in w.chain_findings("RX1", stored))


# ── version 2: the chain a verifier can actually check ────────────────────
# Migration 0053. Everything above this line still describes version-1 rows,
# which were never rehashed and are still in the table.
def v2_rows(w, rx="RX1", *, triggered_by_type="staff", event_metadata=None):
    """The stored records a version-2 chain produces, as a verifier reads them."""
    hashes = w.chain_v2(rx, triggered_by_type=triggered_by_type,
                        event_metadata=event_metadata)
    out, prev = [], None
    for i, (s, h) in enumerate(zip(w.history[rx].steps, hashes), start=1):
        out.append({"prescription_id": rx, "sequence_number": i,
                    "from_status": s.frm, "to_status": s.to,
                    "triggered_by_id": s.actor,
                    "triggered_by_type": triggered_by_type,
                    "reason": s.reason, "event_metadata": event_metadata,
                    "hashed_at": s.at, "created_at": s.at,
                    "previous_hash": prev, "event_hash": h,
                    "digest_version": 2})
        prev = h
    return out


def test_version_2_covers_every_field_an_auditor_reads():
    """The whole point of widening the digest. Computed from the field lists,
    so narrowing it again fails here rather than in a regulator's office."""
    assert WorkflowDomain().hash_coverage_gap(version=2) == []


def test_version_1_rows_still_report_the_old_gap():
    """The fix did not rewrite history, so the finding remains true of the rows
    written under the old rule and must keep being reported for them."""
    gap = WorkflowDomain().hash_coverage_gap(version=1)
    assert set(gap) == set(AUDITED_BUT_UNHASHED)


def test_a_clean_version_2_chain_verifies():
    w = WorkflowDomain()
    walk(w)
    assert w.chain_findings("RX1", v2_rows(w)) == []


def test_editing_a_reason_now_breaks_the_chain():
    """The finding this work exists to close. Under version 1 this same pair of
    records left every link verifying — see the test of that name above."""
    w = WorkflowDomain()
    before = {"prescription_id": "RX1", "sequence_number": 1,
              "from_status": PENDING_DUR, "to_status": PENDING_VERIFICATION,
              "triggered_by_id": "alice", "triggered_by_type": "staff",
              "reason": "DUR override: prescriber consulted",
              "event_metadata": None, "hashed_at": T0, "previous_hash": None}
    after = dict(before, reason="routine")
    intact, changed = w.unprotected_edit(before, after, version=2)
    assert intact is False
    assert changed == ["reason"]


def test_the_same_edit_is_still_invisible_to_version_1():
    """Stated as a test rather than a comment: a version-1 row is not retro-
    actively protected by a migration, and anyone reading old rows needs to
    know that the guarantee they get is the old one."""
    w = WorkflowDomain()
    before = {"prescription_id": "RX1", "from_status": PENDING_DUR,
              "to_status": PENDING_VERIFICATION, "triggered_by_id": "alice",
              "created_at": T0, "previous_hash": None, "reason": "override"}
    after = dict(before, reason="routine")
    intact, _ = w.unprotected_edit(before, after, version=1)
    assert intact is True


def test_editing_metadata_breaks_the_chain_under_version_2():
    w = WorkflowDomain()
    walk(w)
    clean = v2_rows(w, event_metadata={"override_code": "A1"})
    tampered = v2_rows(w, event_metadata={"override_code": "A1"})
    tampered[3]["event_metadata"] = {"override_code": "B2"}
    assert w.chain_findings("RX1", clean) == []
    assert any("broken at this link" in f
               for f in w.chain_findings("RX1", tampered))


def test_editing_the_actor_type_breaks_the_chain_under_version_2():
    """A transition relabelled from 'ai' to 'staff' changes who is accountable
    for it, and version 1 did not notice."""
    w = WorkflowDomain()
    walk(w)
    tampered = v2_rows(w)
    tampered[2]["triggered_by_type"] = "ai"
    assert any("broken at this link" in f
               for f in w.chain_findings("RX1", tampered))


def test_metadata_hashes_the_same_whatever_order_the_keys_arrive_in():
    """event_metadata round-trips through JSONB, which preserves neither key
    order nor formatting. A digest sensitive to either would break the chain on
    a read rather than on an edit — a false alarm, which is the expensive kind."""
    from tests.simulation.domains.workflow import canonical_metadata, event_digest_v2

    a = {"b": 2, "a": 1, "nested": {"y": 2, "x": 1}}
    b = {"a": 1, "nested": {"x": 1, "y": 2}, "b": 2}
    assert canonical_metadata(a) == canonical_metadata(b)
    args = ("RX1", 1, PENDING_DUR, PENDING_VERIFICATION, "alice", "staff", None)
    assert (event_digest_v2(*args, a, T0, None)
            == event_digest_v2(*args, b, T0, None))


def test_a_relinked_chain_is_detected():
    """previous_hash is stored now, so a verifier can compare the link the
    writer claims against the one the rows actually form. Version 1 stored no
    link at all, which is why this check could not exist."""
    w = WorkflowDomain()
    walk(w)
    rows = v2_rows(w)
    rows[4]["previous_hash"] = "0" * 64
    found = w.chain_findings("RX1", rows)
    assert any("the chain was re-linked" in f for f in found)


def test_sequence_numbers_are_reported_when_they_are_not_unique():
    """The unique constraint in 0053 makes this impossible to write, so the
    oracle must still catch it — a constraint dropped in a later migration is
    exactly the regression this is here for."""
    w = WorkflowDomain()
    walk(w)
    rows = v2_rows(w)
    rows[3]["sequence_number"] = rows[2]["sequence_number"]
    assert any("not unique" in f and "the chain forks" in f
               for f in w.chain_findings("RX1", rows))


def test_a_reordered_chain_is_reported():
    w = WorkflowDomain()
    walk(w)
    rows = v2_rows(w)
    rows[2], rows[5] = rows[5], rows[2]
    assert any("not in order as read" in f
               for f in w.chain_findings("RX1", rows))


def test_a_version_2_row_without_its_inputs_is_unverifiable_not_tampered():
    """The distinction the whole method rests on. A row that cannot be
    recomputed must not be reported as a broken link: 'this was never checkable'
    and 'someone changed this' call for completely different responses, and
    conflating them reports a design gap as an intrusion."""
    w = WorkflowDomain()
    walk(w)
    rows = v2_rows(w)
    rows[3]["hashed_at"] = None
    found = w.chain_findings("RX1", rows)
    assert any("unverifiable rather than verified" in f for f in found)
    assert not any("broken at this link" in f for f in found)


def test_sequence_numbers_start_at_one_to_match_the_writer():
    w = WorkflowDomain()
    walk(w)
    assert [r["sequence_number"] for r in v2_rows(w)] == list(range(1, 10))


def test_a_mixed_chain_verifies_each_row_under_its_own_version():
    """What the table actually looks like after 0053: old rows under the old
    rule, new rows under the new one, one continuous chain across the cut-over."""
    w = WorkflowDomain()
    walk(w)
    steps = w.history["RX1"].steps
    rows, prev = [], None
    for i, s in enumerate(steps, start=1):
        if i <= 4:                                    # written before 0053
            h = event_digest(s.rx, s.frm, s.to, s.actor, s.at, prev)
            rows.append({"prescription_id": "RX1", "from_status": s.frm,
                         "to_status": s.to, "triggered_by_id": s.actor,
                         "created_at": s.at, "event_hash": h,
                         "sequence_number": i, "digest_version": 1})
        else:                                          # written after
            from tests.simulation.domains.workflow import event_digest_v2
            h = event_digest_v2(s.rx, i, s.frm, s.to, s.actor, "staff",
                                s.reason, None, s.at, prev)
            rows.append({"prescription_id": "RX1", "sequence_number": i,
                         "from_status": s.frm, "to_status": s.to,
                         "triggered_by_id": s.actor, "triggered_by_type": "staff",
                         "reason": s.reason, "event_metadata": None,
                         "hashed_at": s.at, "created_at": s.at,
                         "previous_hash": prev, "event_hash": h,
                         "digest_version": 2})
        prev = h
    assert w.chain_findings("RX1", rows) == []


# ── gates ─────────────────────────────────────────────────────────────────
def test_a_controlled_substance_needs_an_epcs_enrolled_actor():
    w = WorkflowDomain()
    step(w, "RX1", PENDING_VERIFICATION, VERIFICATION_IN_PROGRESS,
         controlled=True, epcs_enrolled=False)
    assert any("not EPCS-enrolled" in f for f in w._findings)


def test_an_enrolled_actor_clears_the_gate():
    w = WorkflowDomain()
    step(w, "RX1", PENDING_VERIFICATION, VERIFICATION_IN_PROGRESS,
         controlled=True, epcs_enrolled=True)
    assert w._findings == []


def test_an_uncontrolled_drug_needs_no_enrolment():
    w = WorkflowDomain()
    step(w, "RX1", READY_TO_FILL, FILLING, controlled=False)
    assert w._findings == []


# ── rx numbers ────────────────────────────────────────────────────────────
def test_a_duplicate_rx_number_in_one_pharmacy_is_reported():
    """generate_rx_number counts today's prescriptions and adds one, with no
    serialisation, so two concurrent intakes produce the same number."""
    w = WorkflowDomain()
    for _ in range(2):
        w.observe(Fact(seq=1, at=T0, day=0, kind="prescription_entered",
                       subject="PH120260101000001", payload={"pharmacy": "A"}))
    assert any("issued twice" in f for f in w._findings)


def test_the_same_number_in_two_pharmacies_is_fine():
    w = WorkflowDomain()
    for ph in ("A", "B"):
        w.observe(Fact(seq=1, at=T0, day=0, kind="prescription_entered",
                       subject="PH120260101000001", payload={"pharmacy": ph}))
    assert w._findings == []


# ── the oracle policing itself ────────────────────────────────────────────
def test_an_incoherent_account_is_the_oracles_fault_not_the_platforms():
    w = WorkflowDomain()
    step(w, "RX1", None, INTAKE)
    step(w, "RX1", READY_TO_FILL, FILLING, seq=2)   # skipped the middle
    assert any("the last one this oracle saw ended at" in v
               for v in w.violations())


# ── it is a citizen of the spine ──────────────────────────────────────────
def test_workflow_is_a_domain():
    assert isinstance(WorkflowDomain(), Domain)
    assert WorkflowDomain().name == "workflow"


@pytest.mark.asyncio
async def test_the_harness_carries_workflow_with_the_others():
    from tests.simulation.domains.money import MoneyDomain

    w, m = WorkflowDomain(), MoneyDomain()
    h = Harness(db=None, start=START, domains=[w, m])
    for i, (frm, to) in enumerate(HAPPY_PATH):
        h.fact("transitioned", subject="RX1", actor="alice",
               payload={"rx": "RX1", "from": frm, "to": to})
    h.fact("priced", subject="RX1", payload={
        "rx": "RX1", "plan": "tamin", "setting": "outpatient",
        "technical_fee": "0",
        "lines": [{"irc": "A", "quantity": "1", "consumer_price": "100000",
                   "reference_price": None}]})
    h.fact("dispensed", subject="N1", quantity=Decimal("1"),
           payload={"rx": "RX1"})
    assert await h.check("a whole prescription") == []
    assert h.report.domains == ["workflow", "money"]


@pytest.mark.asyncio
async def test_an_illegal_step_and_an_unpriced_dispense_are_both_reported():
    """The run that matters is the one where two things are wrong at once."""
    from tests.simulation.domains.money import MoneyDomain

    w, m = WorkflowDomain(), MoneyDomain()
    h = Harness(db=None, start=START, domains=[w, m])
    h.fact("transitioned", subject="RX1", actor="alice",
           payload={"rx": "RX1", "from": INTAKE, "to": DISPENSED})
    h.fact("dispensed", subject="N1", quantity=Decimal("3"),
           payload={"rx": "RX1"})
    found = await h.check("two faults at once")
    assert {f.domain for f in found} == {"workflow", "money"}
    assert h.report.by_domain() == {"workflow": 1, "money": 1}
