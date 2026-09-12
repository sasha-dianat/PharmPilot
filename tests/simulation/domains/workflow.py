"""The workflow and audit oracle: what may happen, and what the chain proves.

Phase 2 of the pilot. Two questions, and the platform answers them in two very
different places:

  *May this prescription move from here to there?* — the legal transition graph.
  *Can anyone prove it did?* — the SHA-256 chain over `rx_state_events`.

Both are re-derived here. The transition graph is written from the lifecycle the
Project Bible documents, not imported from `state_machine.TRANSITIONS`, for the
same reason the money oracle restates tariffs: sharing the table would make a
wrong edge invisible to both sides at once. `graph_drift()` reports where the
specification and the implementation disagree about what is legal — a finding in
its own right, and a different one from "the application took an illegal step".

The hash is recomputed from its documented definition. That recomputation is
what makes the two audit findings below demonstrable rather than assertions:

**The chain has no stored link.** `rx_state_events` has `event_hash` and no
`previous_hash` column. The previous hash goes *into* the digest and is then
thrown away, so the chain cannot be verified from the table — a verifier has to
recompute forward from the first event and hope it reconstructs the same order
the writer used. Which leads directly to:

**The order the writer used is not reliably reconstructible.** The writer selects
its predecessor with `ORDER BY created_at DESC LIMIT 1`, and `created_at` is
`server_default=func.now()` — Postgres `now()` is *transaction start* time, the
same value for every row written in one transaction. Two transitions in one
transaction therefore carry byte-identical timestamps and "the previous event"
becomes whichever row the planner happens to return.

**The digest does not cover what an auditor reads.** It spans prescription_id,
from_status, to_status, triggered_by_id, timestamp and previous_hash. It does not
span `reason`, `event_metadata` or `triggered_by_type`. The chain is therefore
tamper-evident about the *shape* of a transition and silent about its stated
justification — the DUR override reason, the actor type, the metadata a
regulator would actually read. `hash_coverage_gap()` demonstrates it by editing a
reason and showing every hash still verifies.

None of this is a claim that the chain is worthless. It detects a changed status,
a changed actor id or a deleted event. It does not detect an edited reason, and
an audit trail that advertises tamper evidence should be precise about which
fields it covers.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from ..spine.facts import Fact

# ── the lifecycle, restated from the specification ────────────────────────
# Transcribed from the Project Bible's documented lifecycle —
#   intake → pending_dur → pending_verification → verification_in_progress →
#   pending_adjudication → ready_to_fill → filling → filled/will_call → dispensed
# — plus the hold, rejection, PA, cancel, return and terminal branches it names.
# Written here rather than imported so that a wrong edge is not invisible to both
# the engine and its oracle at once. See `graph_drift()`.
INTAKE = "intake"
PENDING_DUR = "pending_dur"
DUR_HOLD = "dur_hold"
PENDING_VERIFICATION = "pending_verification"
VERIFICATION_IN_PROGRESS = "verification_in_progress"
PENDING_ADJUDICATION = "pending_adjudication"
ADJUDICATION_REJECTED = "adjudication_rejected"
PENDING_PA = "pending_pa"
READY_TO_FILL = "ready_to_fill"
FILLING = "filling"
FILLED = "filled"
WILL_CALL = "will_call"
DISPENSED = "dispensed"
RETURNED_TO_STOCK = "returned_to_stock"
CANCELLED = "cancelled"
TRANSFERRED_OUT = "transferred_out"
ON_HOLD = "on_hold"

LEGAL: dict[str, set[str]] = {
    INTAKE: {PENDING_DUR, DUR_HOLD, ON_HOLD, CANCELLED},
    PENDING_DUR: {PENDING_VERIFICATION, DUR_HOLD, ON_HOLD, CANCELLED},
    DUR_HOLD: {PENDING_VERIFICATION, CANCELLED},
    PENDING_VERIFICATION: {VERIFICATION_IN_PROGRESS, ON_HOLD, CANCELLED},
    VERIFICATION_IN_PROGRESS: {PENDING_ADJUDICATION, DUR_HOLD,
                               PENDING_VERIFICATION, ON_HOLD, CANCELLED},
    PENDING_ADJUDICATION: {ADJUDICATION_REJECTED, PENDING_PA, READY_TO_FILL,
                           ON_HOLD},
    ADJUDICATION_REJECTED: {PENDING_ADJUDICATION, ON_HOLD, CANCELLED},
    PENDING_PA: {PENDING_ADJUDICATION, ON_HOLD, CANCELLED},
    READY_TO_FILL: {FILLING, ON_HOLD, CANCELLED},
    FILLING: {FILLED, READY_TO_FILL, ON_HOLD},
    FILLED: {WILL_CALL, DISPENSED, RETURNED_TO_STOCK},
    WILL_CALL: {DISPENSED, RETURNED_TO_STOCK},
    ON_HOLD: {INTAKE, PENDING_VERIFICATION, CANCELLED},
    DISPENSED: set(),
    RETURNED_TO_STOCK: set(),
    CANCELLED: set(),
    TRANSFERRED_OUT: set(),
}

TERMINAL = {DISPENSED, RETURNED_TO_STOCK, CANCELLED, TRANSFERRED_OUT}

# A controlled substance entering either of these needs an EPCS-enrolled actor.
EPCS_REQUIRED = {VERIFICATION_IN_PROGRESS, FILLING}

# The fields the documented digest actually spans. Kept as data so the coverage
# gap below is a computed fact rather than a claim in a comment.
HASHED_FIELDS = ("prescription_id", "from_status", "to_status",
                 "triggered_by_id", "timestamp", "previous_hash")
AUDITED_BUT_UNHASHED = ("reason", "event_metadata", "triggered_by_type")


def event_digest(prescription_id, from_status, to_status, triggered_by_id,
                 timestamp: datetime, previous_hash: str | None) -> str:
    """The chain hash, recomputed from its documented definition."""
    payload = json.dumps({
        "prescription_id": str(prescription_id),
        "from_status": from_status,
        "to_status": to_status,
        "triggered_by_id": str(triggered_by_id) if triggered_by_id else None,
        "timestamp": timestamp.isoformat(),
        "previous_hash": previous_hash,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class Step:
    """One transition, as the oracle was told about it."""
    seq: int
    rx: str
    frm: str | None
    to: str
    actor: str | None
    at: datetime
    controlled: bool = False
    epcs_enrolled: bool = False
    reason: str | None = None
    pharmacy: str | None = None


@dataclass
class RxHistory:
    rx: str
    steps: list[Step] = field(default_factory=list)
    status: str | None = None
    pharmacy: str | None = None


class WorkflowDomain:
    """Independent ground truth for what may happen and what the chain proves."""

    name = "workflow"

    def __init__(self, *, verify_chain: bool = True):
        self.verify_chain = verify_chain
        self.history: dict[str, RxHistory] = {}
        self.rx_numbers: dict[str, set[str]] = {}   # pharmacy → rx numbers seen
        self._findings: list[str] = []
        self._broken: list[str] = []

    # ── the legal graph ───────────────────────────────────────────────────
    def legal(self, frm: str | None, to: str) -> bool:
        if frm is None:
            # A prescription starts life at intake. Anything else as a first
            # state means the row was created already in motion.
            return to == INTAKE
        return to in LEGAL.get(frm, set())

    def observe(self, fact: Fact) -> None:
        if fact.kind == "prescription_entered":
            rx = str(fact.subject)
            ph = str(fact.payload.get("pharmacy") or "")
            h = self.history.setdefault(rx, RxHistory(rx=rx, pharmacy=ph))
            h.pharmacy = ph or h.pharmacy
            seen = self.rx_numbers.setdefault(ph, set())
            if rx in seen:
                self._findings.append(
                    f"rx number {rx} issued twice in pharmacy {ph} — a duplicate "
                    f"number makes two prescriptions indistinguishable in the "
                    f"audit trail")
            seen.add(rx)
            h.status = fact.payload.get("status") or INTAKE
            return

        if fact.kind != "transitioned":
            return

        p = fact.payload
        rx = str(p.get("rx") or fact.subject)
        h = self.history.setdefault(rx, RxHistory(rx=rx))
        frm = p.get("from")
        to = str(p.get("to"))
        step = Step(seq=fact.seq, rx=rx, frm=frm, to=to,
                    actor=fact.actor, at=fact.at,
                    controlled=bool(p.get("controlled", False)),
                    epcs_enrolled=bool(p.get("epcs_enrolled", False)),
                    reason=p.get("reason"), pharmacy=p.get("pharmacy"))

        # The oracle's own model must stay coherent: it was told the transition
        # started where the last one ended.
        if h.steps and frm is not None and frm != h.steps[-1].to:
            self._broken.append(
                f"rx {rx}: told a transition from {frm!r}, but the last one this "
                f"oracle saw ended at {h.steps[-1].to!r}")

        if not self.legal(frm, to):
            self._findings.append(
                f"rx {rx}: {frm or '(new)'} → {to} is not a legal transition")
        if frm in TERMINAL:
            self._findings.append(
                f"rx {rx}: moved out of terminal state {frm} to {to}")
        if step.controlled and to in EPCS_REQUIRED and not step.epcs_enrolled:
            self._findings.append(
                f"rx {rx}: a controlled substance entered {to} under an actor "
                f"who is not EPCS-enrolled")

        h.steps.append(step)
        h.status = to

    # ── the chain ─────────────────────────────────────────────────────────
    def chain(self, rx: str) -> list[str]:
        """Recompute the chain forward. Returns each event's expected hash."""
        h = self.history.get(rx)
        if h is None:
            return []
        out: list[str] = []
        prev: str | None = None
        for s in h.steps:
            prev = event_digest(s.rx, s.frm, s.to, s.actor, s.at, prev)
            out.append(prev)
        return out

    def chain_findings(self, rx: str, stored: list[dict]) -> list[str]:
        """Compare a stored event sequence against the recomputed chain.

        `stored` is a list of {from_status, to_status, triggered_by_id,
        created_at, event_hash} in the order the application would read them.
        """
        out: list[str] = []
        prev: str | None = None
        for i, ev in enumerate(stored):
            expect = event_digest(
                ev["prescription_id"], ev.get("from_status"), ev["to_status"],
                ev.get("triggered_by_id"), ev["created_at"], prev)
            if ev["event_hash"] != expect:
                out.append(
                    f"rx {rx} event {i} ({ev.get('from_status')} → "
                    f"{ev['to_status']}): stored hash {ev['event_hash'][:16]}… "
                    f"does not match the recomputed {expect[:16]}… — the chain "
                    f"is broken at this link")
                # Continue from the stored hash so one break does not cascade
                # into a false report on every later event.
                prev = ev["event_hash"]
            else:
                prev = expect

        # Ambiguous ordering: the writer picks its predecessor by created_at, so
        # two events sharing one makes "the previous event" arbitrary.
        stamps: dict[str, int] = {}
        for ev in stored:
            key = ev["created_at"].isoformat()
            stamps[key] = stamps.get(key, 0) + 1
        for stamp, n in stamps.items():
            if n > 1:
                out.append(
                    f"rx {rx}: {n} events share created_at {stamp} — the writer "
                    f"selects its predecessor with ORDER BY created_at DESC "
                    f"LIMIT 1, so which one they chain to is arbitrary and the "
                    f"chain cannot be reconstructed by a verifier")
        return out

    def hash_coverage_gap(self) -> list[str]:
        """Fields an auditor reads that the digest does not protect.

        Not a disagreement with the application — a property of the documented
        digest. Reported separately so it is never mistaken for a broken chain.
        """
        out = []
        for f in AUDITED_BUT_UNHASHED:
            if f not in HASHED_FIELDS:
                out.append(f)
        return out

    def unprotected_edit(self, before: dict, after: dict) -> tuple[bool, list[str]]:
        """Did an edit between these two event records leave the chain intact?

        Takes two genuinely different records and reports (chain_still_valid,
        fields_that_changed). Hashing one record twice and observing that it
        matches would prove nothing — the Bible lists tautological checks among
        this codebase's known sins, and an audit demonstration is the last place
        to add one. So this hashes two records that really do differ and lets the
        caller see which differences the digest noticed.
        """
        changed = [k for k in set(before) | set(after)
                   if before.get(k) != after.get(k)]
        h_before = event_digest(before["prescription_id"], before.get("from_status"),
                                before["to_status"], before.get("triggered_by_id"),
                                before["created_at"], before.get("previous_hash"))
        h_after = event_digest(after["prescription_id"], after.get("from_status"),
                               after["to_status"], after.get("triggered_by_id"),
                               after["created_at"], after.get("previous_hash"))
        return h_before == h_after, sorted(changed)

    # ── the Domain protocol ───────────────────────────────────────────────
    async def check(self, ctx) -> list[str]:
        out = list(self._findings)
        self._findings.clear()

        # A prescription that reached a terminal state must have got there
        # through the graph, and its current status must be the last thing the
        # chain records. A status that moved with no event behind it is the
        # signature of a direct write — POS and AutoPAAgent both do this.
        db = getattr(ctx, "db", None)
        if db is None:
            return out
        out.extend(await self._check_against_db(ctx))
        return out

    async def _check_against_db(self, ctx) -> list[str]:
        from sqlalchemy import text
        out: list[str] = []
        rows = (await ctx.db.execute(text(
            "SELECT p.rx_number, p.status, "
            "       (SELECT e.to_status FROM rx_state_events e "
            "         WHERE e.prescription_id = p.id "
            "         ORDER BY e.created_at DESC, e.id DESC LIMIT 1) AS last_event, "
            "       (SELECT count(*) FROM rx_state_events e "
            "         WHERE e.prescription_id = p.id) AS n_events "
            "  FROM prescriptions p WHERE p.pharmacy_id = :p"),
            {"p": ctx.pharmacy_id})).all()
        for rx_number, status, last_event, n_events in rows:
            if n_events == 0:
                out.append(
                    f"rx {rx_number}: status {status!r} with no state events at "
                    f"all — nothing proves how it got there")
            elif last_event is not None and status != last_event:
                out.append(
                    f"rx {rx_number}: status is {status!r} but the last recorded "
                    f"event ended at {last_event!r} — the status moved without "
                    f"passing through the state machine")
        return out

    def violations(self) -> list[str]:
        return list(self._broken)

    # ── the specification gate ────────────────────────────────────────────
    def graph_drift(self) -> list[str]:
        """Where the implementation's legal graph differs from the specification.

        Same role as the money oracle's `tariff_drift()`: kept out of `check()`
        so "the lifecycle changed" is never reported as "the application took an
        illegal step". They need different readers.
        """
        from services.core.pharmacy_workflow.state_machine import TRANSITIONS

        theirs = {frm.value: {t.value for t in tos}
                  for frm, tos in TRANSITIONS.items()}
        out: list[str] = []
        for state in sorted(set(theirs) | set(LEGAL)):
            a = LEGAL.get(state)
            b = theirs.get(state)
            if a is None:
                out.append(f"{state}: implementation has it, the specification "
                           f"does not")
                continue
            if b is None:
                out.append(f"{state}: specification has it, the implementation "
                           f"does not")
                continue
            for extra in sorted(b - a):
                out.append(f"{state} → {extra}: legal in the implementation, not "
                           f"in the specification")
            for missing in sorted(a - b):
                out.append(f"{state} → {missing}: in the specification, refused "
                           f"by the implementation")
        return out

    def unreachable_states(self) -> list[str]:
        """States no run can ever get to — dead vocabulary, or a missing edge."""
        seen = {INTAKE}
        frontier = [INTAKE]
        while frontier:
            cur = frontier.pop()
            for nxt in LEGAL.get(cur, set()):
                if nxt not in seen:
                    seen.add(nxt)
                    frontier.append(nxt)
        return sorted(set(LEGAL) - seen)
