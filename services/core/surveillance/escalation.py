"""What to do when identification does not settle.

The fusion engine can return NO_MATCH, and that used to be the end of it. A
supervising system does not stop at "unknown visitor": it mints a provisional
identity and asks the counter for the name and national code, which is how an
unknown person becomes a known one.

Two rules are encoded here rather than left to the UI:

ELICIT BEFORE COMPARING. The prompt never names a weak candidate. Showing
"possibly X" makes staff ask "are you X?", a leading question a polite person
answers yes to — which corrupts the verification being attempted. The hint is
returned separately and compared against the independently-obtained answer.

A HINT IS BELOW IAL1. Weak candidates are for post-hoc comparison only. They
never open a chart and are never stored as the observation's identity.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from services.biometric.fusion import AUTO, NO_MATCH

IDENTIFY_MANUALLY = "identify_manually"

# More than a few candidates turns the screen into a lineup and invites the
# staff member to pick a face rather than ask a question.
MAX_HINTS = 3

_ASK = "لطفاً نام و کد ملی مراجعه‌کننده را بپرسید"
_OCCLUDED = "چهره پوشیده است"


@dataclass
class StaffAction:
    prompt_fa: str
    needs_provisional: bool
    reason: str
    hint_identity_ids: list[UUID] = field(default_factory=list)


def escalate(fused, stratum=None, hint_ids: list[UUID] | None = None
             ) -> StaffAction | None:
    """The human step this result needs, or None if it needs none."""
    if fused.decision == AUTO:
        return None

    hints = list(hint_ids) if hint_ids else (
        [fused.identity_id] if fused.identity_id else [])
    hints = [h for h in hints if h is not None][:MAX_HINTS]

    survivors: tuple[str, ...] = ()
    if stratum is not None:
        from services.biometric.occlusion import SURVIVING_MODALITIES
        survivors = SURVIVING_MODALITIES.get(stratum.value, ())

    if fused.decision == NO_MATCH:
        prompt = _ASK
        if survivors:
            prompt = f"{_OCCLUDED} — {_ASK}"
        reason = ("no modality settled on an identity"
                  + (f"; tried {', '.join(survivors)}" if survivors else ""))
        return StaffAction(prompt_fa=prompt, needs_provisional=True,
                           reason=reason, hint_identity_ids=hints)

    # REVIEW: a candidate exists and is worth confirming, so no new provisional
    # record is needed — only a person.
    prompt = _ASK
    if survivors:
        prompt = f"{_OCCLUDED} — {_ASK}"
    reason = ("a candidate was found but not confirmed"
              + (f"; tried {', '.join(survivors)}" if survivors else ""))
    return StaffAction(prompt_fa=prompt, needs_provisional=False,
                       reason=reason, hint_identity_ids=hints)
