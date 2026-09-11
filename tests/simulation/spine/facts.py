"""What happened, in words no single domain owns.

The reason the spine needs its own vocabulary: in a whole-pharmacy pilot one
business action fans out across domains. A dispense decrements stock, prices a
line, computes an insurer share and creates a payable. If each domain listened to
the application directly, four listeners would each re-derive "what just
happened" from four different queries and disagree about it.

So the driver performs the real application action, describes it once as a
`Fact`, and every oracle that cares consumes the same description. Domains stay
independent; the account of events does not fork.

A fact is a *statement about the world*, never an instruction. Nothing here tells
a domain what to conclude — `payload` carries the observation and the domain
applies its own rules to it, which is what keeps an oracle independent of the
code it checks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

# Deliberately not one enum per domain: a fact is named for the business event,
# not for the table it lands in. Kinds are added as scenarios emit them — a
# vocabulary listing events nothing produces is the same promise-with-no-code
# this project has removed twice.
STOCK = ("received", "dispensed", "written_off", "counted", "transferred",
         "reserved", "released", "placed_on_shelf", "taken_off_shelf")
WORKFLOW = ("prescription_entered", "verified", "handed_over", "transitioned")
MONEY = ("priced", "adjudicated", "paid")
KINDS = STOCK + WORKFLOW + MONEY


class UnknownFact(ValueError):
    """A fact whose kind no domain has been taught to read."""


@dataclass(frozen=True)
class Fact:
    seq: int
    at: datetime
    day: int
    kind: str
    subject: str                       # ndc11, lot, rx number — what it is about
    actor: str | None = None           # who did it, for separation-of-duties checks
    quantity: Decimal | None = None
    money: Decimal | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise UnknownFact(
                f"{self.kind!r} is not a known fact kind. Add it to `facts.KINDS` "
                f"when a scenario actually emits it, not before.")

    def as_dict(self) -> dict:
        return {"seq": self.seq, "at": self.at.isoformat(), "day": self.day,
                "kind": self.kind, "subject": self.subject, "actor": self.actor,
                "quantity": None if self.quantity is None else float(self.quantity),
                "money": None if self.money is None else float(self.money),
                "payload": dict(self.payload)}
