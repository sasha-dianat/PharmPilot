"""The stock ledger as a spine citizen.

Deliberately a *wrapper*, not a rewrite. `tests/simulation/oracle.py` and
`invariants.py` have found real defects — a quantity ceiling that wedged a SKU,
reserved stock removable from under a promise, an unreachable bucket-release
branch, an ABC classifier that called the most valuable item C — and they have
survived thousands of adversarial events. Rewriting them to fit a new interface
would risk all of that to gain nothing.

So this adapts rather than replaces: the existing `Driver` keeps feeding the
existing `Oracle` exactly as it does today, and this presents that same oracle to
the harness so the money and workflow domains can be checked in the same pass.
The existing sweep must keep passing unchanged, and that is the acceptance test
for the whole spine.
"""
from __future__ import annotations

from tests.simulation import invariants as INV
from tests.simulation.oracle import Oracle
from tests.simulation.spine.facts import Fact


class InventoryDomain:
    """Stock, presented to the spine. Ground truth stays in the existing oracle."""

    name = "inventory"

    def __init__(self, oracle: Oracle, *, app_state, app_totals, app_valuation):
        self.oracle = oracle
        # How to ask the *database* what it thinks. Injected rather than imported
        # so the domain does not need to know whether it is being driven by the
        # existing Driver or by a whole-pharmacy scenario.
        self._app_state = app_state
        self._app_totals = app_totals
        self._app_valuation = app_valuation

    def observe(self, fact: Fact) -> None:
        """Stock facts are already recorded by the Driver as it acts.

        The existing driver writes to the oracle at the moment it performs each
        action, because it alone knows the lot the application chose. Observing
        them again here would double-count. This exists so the harness can fan a
        fact out to every domain without inventory needing to opt out — the
        money and workflow domains are the ones that will read `dispensed`.
        """
        return None

    async def check(self, ctx) -> list[str]:
        bad: list[str] = []
        bad += INV.lots_agree(await self._app_state(), self.oracle)
        bad += INV.sku_totals_agree(await self._app_totals(), self.oracle)
        bad += await INV.structural(ctx.db, ctx.pharmacy_id)
        bad += INV.valuation_agrees(await self._app_valuation(), self.oracle)
        return bad

    def violations(self) -> list[str]:
        return self.oracle.violations()
