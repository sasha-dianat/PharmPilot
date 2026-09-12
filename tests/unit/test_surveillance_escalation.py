"""When identification fails, the system asks a human — it does not shrug.

fuse() used to end a failed identification with NO_MATCH and no next step. A
supervising system mints a provisional identity and tells the counter to obtain
the name and national code, so the unknown visitor becomes a known one.
"""
from __future__ import annotations

from uuid import uuid4

from services.biometric.fusion import (
    AUTO, NO_MATCH, REVIEW, Contribution, Exclusion, FusedIdentity)
from services.biometric.occlusion import OcclusionStratum
from services.core.surveillance.escalation import (
    IDENTIFY_MANUALLY, StaffAction, escalate)


def fused(decision, identity=None, confidence=0.0, contribs=(), excluded=()):
    return FusedIdentity(
        identity_id=identity, confidence=confidence, decision=decision,
        margin=0.0, contributions=list(contribs), excluded=list(excluded),
        explanation="")


def test_a_confident_identification_needs_no_escalation():
    assert escalate(fused(AUTO, identity=uuid4(), confidence=0.995)) is None


def test_no_match_escalates_to_a_staff_request():
    action = escalate(fused(NO_MATCH))
    assert isinstance(action, StaffAction)
    assert action.needs_provisional is True
    assert "کد ملی" in action.prompt_fa          # asks for the national code
    assert "نام" in action.prompt_fa             # and the name


def test_the_prompt_never_names_a_weak_candidate():
    """Showing a low-confidence name makes staff ask a leading question that a
    polite person answers yes to, corrupting the verification. Elicit first,
    compare afterwards."""
    maybe = uuid4()
    action = escalate(fused(NO_MATCH, identity=maybe, confidence=0.34))
    assert str(maybe) not in action.prompt_fa
    assert maybe in action.hint_identity_ids      # kept, for later comparison


def test_review_escalates_but_does_not_demand_a_provisional_identity():
    """A review already has a candidate worth confirming; it does not need a new
    provisional record, only a human."""
    action = escalate(fused(REVIEW, identity=uuid4(), confidence=0.5))
    assert action is not None
    assert action.needs_provisional is False


def test_an_occluded_failure_says_so_and_names_what_was_tried():
    """The counter deserves to know the face was covered — it changes how they
    ask, and it is the difference between a system fault and a physical one."""
    action = escalate(
        fused(NO_MATCH, excluded=[Exclusion("face", "quality below floor")]),
        stratum=OcclusionStratum.SCARF_MASK)
    assert "پوشیده" in action.prompt_fa or "پوشش" in action.prompt_fa
    assert "voice" in action.reason and "gait" in action.reason


def test_escalation_decision_value_is_the_stored_vocabulary():
    assert IDENTIFY_MANUALLY == "identify_manually"


def test_hints_are_capped_so_the_ui_cannot_become_a_lineup():
    ids = [uuid4() for _ in range(9)]
    action = escalate(fused(NO_MATCH, identity=ids[0]), hint_ids=ids)
    assert len(action.hint_identity_ids) <= 3
