"""The spine: one clock, one account of events, many domains checked together.

The single design decision here is fan-out. In a whole-pharmacy run a business
action touches several domains at once — a dispense moves stock, empties a shelf,
prices a line and creates a payable — and the failures worth finding are the ones
*between* domains: stock says the units left, money says nothing was owed.

So the driver performs the real action, describes it once, and every domain that
cares observes the same description. Checking then runs every domain against the
database in one pass, and reports all their findings rather than stopping at the
first, because a run that halts on finding one has hidden the other three.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .clock import SimClock
from .domain import Domain
from .facts import Fact


@dataclass
class Finding:
    day: int
    domain: str
    scenario: str
    detail: str

    def __str__(self) -> str:
        return f"[day {self.day}] {self.domain}/{self.scenario}: {self.detail}"


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    facts: int = 0
    checks: int = 0
    days: int = 0
    domains: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings

    def by_domain(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.findings:
            out[f.domain] = out.get(f.domain, 0) + 1
        return out

    def summary(self) -> str:
        if self.ok:
            return (f"{self.facts} facts over {self.days} simulated day(s), "
                    f"{self.checks} check round(s) across "
                    f"{len(self.domains)} domain(s) — no invariant violated.")
        return (f"{self.facts} facts over {self.days} simulated day(s): "
                f"{len(self.findings)} finding(s) — {self.by_domain()}")


class Harness:
    """Holds the clock, the domains and the account of what happened."""

    def __init__(self, db, *, start: date, pharmacy_id=None,
                 domains: list[Domain] | None = None):
        self.db = db
        self.pharmacy_id = pharmacy_id
        self.clock = SimClock(start=start)
        self.domains: list[Domain] = list(domains or [])
        self.report = Report(domains=[d.name for d in (domains or [])])
        self._seq = 0

    def register(self, domain: Domain) -> None:
        self.domains.append(domain)
        self.report.domains.append(domain.name)

    def fact(self, kind: str, subject: str, **kw) -> Fact:
        """Describe what just happened, and tell every domain that cares."""
        self._seq += 1
        f = Fact(seq=self._seq, at=self.clock.now(), day=self.clock.day,
                 kind=kind, subject=subject, **kw)
        for d in self.domains:
            d.observe(f)
        self.report.facts += 1
        return f

    def advance(self, days: int = 1) -> date:
        self.report.days += days
        return self.clock.advance(days)

    async def check(self, scenario: str = "") -> list[Finding]:
        """Every domain against the database, in one pass, reporting all of them."""
        self.report.checks += 1
        found: list[Finding] = []
        for d in self.domains:
            # The oracle policing itself first: a domain whose own model is
            # broken cannot be trusted to judge the application, and reporting
            # its disagreements as platform defects would be worse than useless.
            for v in d.violations():
                found.append(Finding(self.clock.day, d.name,
                                     "oracle self-check", v))
            try:
                for detail in await d.check(self):
                    found.append(Finding(self.clock.day, d.name, scenario, detail))
            except Exception as exc:                       # noqa: BLE001
                # One domain failing to check must not stop the others. The
                # interesting runs are the ones where three things are wrong.
                found.append(Finding(self.clock.day, d.name, scenario,
                                     f"check raised {type(exc).__name__}: {exc}"))
        self.report.findings.extend(found)
        return found
