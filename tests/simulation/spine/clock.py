"""One clock the whole simulation reads, so a run can span months.

**What this cannot do, stated first.** It cannot freeze the application's clock.
`services/` reads the wall clock in 57 places in the inventory area alone, and
neither `freezegun` nor `time_machine` is installed. So this governs what the
harness *writes and asserts* — the dates on the rows it creates, the `as_of` it
passes, the day a scenario believes it is — while the application's own
`datetime.now()` still returns the real instant.

That is a real limit and it decides what a simulated year can prove. It can prove
everything computed from dated rows: demand shape, seasonality, lead time, expiry,
supplier history. It cannot prove behaviour that branches on the process's own
"now" — which is precisely the class of defect this project has already been
bitten by twice, once in the routers and once in a rotting test.

The seam that would close it already exists: `services/core/inventory/clock.py`
derives the pharmacy's own today from its timezone. If every day-boundary
decision read that, one injection point would make the whole platform
time-travellable. Today six call sites use it and seventeen bypass it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone


@dataclass
class SimClock:
    """The day a scenario believes it is, and how it moves."""
    start: date
    day: int = 0
    _stamps: int = field(default=0, repr=False)

    @property
    def today(self) -> date:
        return self.start + timedelta(days=self.day)

    def now(self) -> datetime:
        """A distinct instant on the current simulated day.

        Monotonic within a day: two events written in the same simulated day
        must not share a timestamp, or any ordering derived from `created_at`
        becomes arbitrary — which is the bug that made one shelf reconciliation
        return different answers on consecutive runs.
        """
        self._stamps += 1
        return datetime.combine(
            self.today, time(hour=8, tzinfo=timezone.utc)
        ) + timedelta(seconds=self._stamps)

    def advance(self, days: int = 1) -> date:
        if days < 0:
            raise ValueError("a simulation runs forwards")
        self.day += days
        self._stamps = 0
        return self.today

    def at(self, day: int) -> date:
        return self.start + timedelta(days=day)

    def days_since_start(self) -> int:
        return self.day
