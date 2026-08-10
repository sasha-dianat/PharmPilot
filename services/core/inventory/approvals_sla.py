"""The clock on a maker-checker approval, and the escalation when it runs out.

A write-off waits for a second signature. Nothing recorded when that signature
was due, and nothing noticed when it never came. Maker-checker without a
deadline is a queue that stalls silently, and the silence is the problem: the
movement never applies, so the stock stays on the books looking sellable, the
requester assumes it is in hand, and the discrepancy ages until a physical count
finds it months later.

Deadlines follow the value at risk rather than a single global timeout:

  * A controlled substance is a same-day matter. Unaccounted controlled stock is
    a regulatory exposure and a diversion signal, and a diversion signal that
    waits three days has stopped being a signal.
  * Expiry and recall removals are time-bound by the reason they exist — stock
    that must come off the shelf is still on it until the write-off applies.
  * Everything else gets the ordinary working-days window.

Escalation raises who is told, never what happens. Nothing here approves,
rejects or applies anything: an approval that expired unanswered is still
pending, because the alternative — auto-approving on a timer — would defeat the
entire point of requiring a second person.

Pure functions; `now` is always injected.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# Hours allowed before a pending approval is overdue.
SLA_HOURS = {
    "controlled": 8,          # any controlled substance, whatever the movement
    "EXPIRY_REMOVAL": 24,
    "RECALL_REMOVAL": 8,      # the stock is unsafe and still on the shelf
    "COUNT_LOSS": 48,
    "COUNT_GAIN": 48,
    "WASTE": 48,
    "SUPPLIER_CREDIT": 72,    # money, not safety — a supplier claim can wait
    "FIELD_EDIT": 48,
}
DEFAULT_SLA_HOURS = 48

# How far past the deadline each level of escalation is reached.
ESCALATION_STEPS = ((1, 1.0), (2, 2.0), (3, 4.0))
MAX_ESCALATION = 3

LEVEL_AUDIENCE = {
    0: "the assigned approver",
    1: "the pharmacist in charge",
    2: "the pharmacy owner",
    3: "the owner, flagged as an unresolved control failure",
}


@dataclass(frozen=True)
class Deadline:
    due_at: datetime
    hours: int
    reason: str

    def as_dict(self) -> dict:
        return {"due_at": self.due_at.isoformat(), "hours": self.hours,
                "reason": self.reason}


@dataclass(frozen=True)
class Overdue:
    approval_id: str
    movement_type: str
    is_controlled: bool
    due_at: datetime
    hours_late: float
    level: int
    audience: str
    explanation: str

    def as_dict(self) -> dict:
        return {"approval_id": self.approval_id,
                "movement_type": self.movement_type,
                "is_controlled": self.is_controlled,
                "due_at": self.due_at.isoformat(),
                "hours_late": round(self.hours_late, 1),
                "level": self.level, "audience": self.audience,
                "explanation": self.explanation}


def sla_hours(movement_type: str | None, *, is_controlled: bool = False) -> tuple[int, str]:
    """How long this approval may wait, and why that long."""
    if is_controlled:
        return SLA_HOURS["controlled"], (
            "controlled substance — unaccounted controlled stock is a diversion "
            "signal, and one that waits is not a signal")
    key = str(movement_type or "").upper()
    if key in SLA_HOURS:
        why = {
            "EXPIRY_REMOVAL": "expired stock is on the shelf until this applies",
            "RECALL_REMOVAL": "recalled stock is on the shelf until this applies",
            "SUPPLIER_CREDIT": "a supplier claim is money, not safety",
        }.get(key, "standard write-off window")
        return SLA_HOURS[key], why
    return DEFAULT_SLA_HOURS, "standard approval window"


def deadline(movement_type: str | None, *, requested_at: datetime,
             is_controlled: bool = False) -> Deadline:
    hours, why = sla_hours(movement_type, is_controlled=is_controlled)
    at = requested_at if requested_at.tzinfo else requested_at.replace(
        tzinfo=timezone.utc)
    return Deadline(due_at=at + timedelta(hours=hours), hours=hours, reason=why)


def level_for(hours_late: float, sla: int) -> int:
    """Escalation level from how far past the deadline this is.

    Measured in multiples of the item's own SLA, not absolute hours: eight hours
    late on a controlled substance is a full window overdue, and eight hours late
    on a supplier credit is barely anything.
    """
    if hours_late <= 0 or sla <= 0:
        return 0
    ratio = hours_late / sla
    level = 0
    for lvl, threshold in ESCALATION_STEPS:
        if ratio >= threshold:
            level = lvl
    return min(level, MAX_ESCALATION)


def overdue(rows: list[dict], *, now: datetime | None = None) -> list[Overdue]:
    """Pending approvals past their deadline, with who should now be told.

    Rows without a `due_at` are treated as due from `created_at`, so approvals
    raised before the clock existed are not silently exempt from it.
    """
    stamp = now or datetime.now(timezone.utc)
    out: list[Overdue] = []
    for r in rows:
        if r.get("status") != "pending":
            continue
        controlled = bool(r.get("is_controlled"))
        mtype = r.get("movement_type")
        hours, _ = sla_hours(mtype, is_controlled=controlled)

        due = r.get("due_at")
        if due is None:
            created = r.get("created_at")
            if created is None:
                continue
            due = deadline(mtype, requested_at=created,
                           is_controlled=controlled).due_at
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due > stamp:
            continue

        late = (stamp - due).total_seconds() / 3600.0
        lvl = level_for(late, hours)
        out.append(Overdue(
            approval_id=str(r.get("id")), movement_type=str(mtype),
            is_controlled=controlled, due_at=due, hours_late=late, level=lvl,
            audience=LEVEL_AUDIENCE.get(lvl, LEVEL_AUDIENCE[MAX_ESCALATION]),
            explanation=(
                f"{round(late, 1)}h past a {hours}h window — still pending, and "
                f"the stock it covers is still on the books."),
        ))
    out.sort(key=lambda o: (-o.level, -o.hours_late))
    return out


def needs_escalating(rows: list[dict], *, now: datetime | None = None) -> list[Overdue]:
    """Overdue approvals whose level has risen since anyone was last told.

    Without this, a nightly sweep re-announces the same twelve approvals every
    night and the alert stops being read — which is the same failure as never
    alerting, arrived at differently.
    """
    current = {str(r.get("id")): int(r.get("escalation_level") or 0) for r in rows}
    return [o for o in overdue(rows, now=now) if o.level > current.get(o.approval_id, 0)]
