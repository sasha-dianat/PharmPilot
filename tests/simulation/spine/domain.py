"""What a domain must provide to be checked alongside the others.

The existing inventory simulation proved one architecture — a seeded world, an
oracle written from the specification rather than the code, invariants that
compare the two, and escalating phases. This is that architecture with the
inventory assumptions removed, so pricing, adjudication, payment, workflow and
the shelf can each be added as a citizen rather than a fork.

A domain does three things and no more:

  **observe(fact)** — update its own ground truth from a description of what
  happened. This is where an oracle stays independent: it is told the event, not
  the application's answer, and works out the consequence with its own rules.

  **check(ctx)** — compare its ground truth against the database and return
  failure strings. A list, never an exception: one domain disagreeing must not
  stop the others from being checked, because the interesting runs are the ones
  where three things are wrong at once.

  **violations()** — the oracle policing itself. If this ever returns anything
  the *simulation* is at fault, not the application, and saying so is what stops
  a broken oracle being read as a broken platform.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .facts import Fact


@runtime_checkable
class Domain(Protocol):
    """One area of the platform, with its own ground truth."""

    name: str

    def observe(self, fact: Fact) -> None:
        """Fold this event into the domain's own account of the world."""

    async def check(self, ctx) -> list[str]:
        """Disagreements between that account and the database. Empty is good."""

    def violations(self) -> list[str]:
        """Rules the domain's own model broke. Non-empty means the oracle is wrong."""
