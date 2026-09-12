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
what made the audit findings below demonstrable rather than asserted — and what
now demonstrates that they are closed.

**Two digest versions, because history was not rewritten.** Migration 0053
widened the digest, which changes every hash it computes. Rehashing the rows
already in the table would have re-blessed as valid any row that had been
altered, so rows keep the rule they were written under: `digest_version = 1` for
anything written before 0053, `2` for everything since. This oracle verifies
each row under its own version and says so when it cannot verify one at all.

**What version 1 could not do, and why the distinction matters.** A version-1
row stores neither the instant that was hashed (the writer hashed
`datetime.now(timezone.utc)`; the row took `created_at` from
`server_default=func.now()`, a different clock) nor the predecessor's hash. Both
digest inputs were discarded after use. Such a row is *unverifiable by design*,
which is not the same finding as *tampered with*, and from the table alone the
two are indistinguishable — a verifier that conflated them would report a design
gap as an intrusion. `chain_findings()` keeps them apart.

**Version 2 stores what it consumed.** `hashed_at`, `previous_hash` and
`sequence_number` are columns now, so the chain is recomputable by someone other
than the writer. `sequence_number` also replaces `created_at` as the ordering
key: Postgres `now()` is transaction-start time, so two transitions committed
together carried byte-identical timestamps and "the previous event" was whichever
row the planner returned.

**Version 2 spans what an auditor reads.** `reason`, `event_metadata` and
`triggered_by_type` are inside the digest, along with the event's position. Under
version 1 they were not, and an edited DUR override justification left every link
verifying — demonstrated against a live database, not cited.
`hash_coverage_gap()` still reports the version-1 gap, because version-1 rows
still exist and that is still true of them.

None of this ever meant the chain was worthless. Version 1 detects a changed
status, a changed actor id or a deleted event. It does not detect an edited
reason, and an audit trail that advertises tamper evidence should be precise
about which fields it covers — under either version.
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

# The fields each digest version spans. Kept as data so the coverage gap below
# is a computed fact rather than a claim in a comment.
LEGACY_DIGEST_VERSION = 1
DIGEST_VERSION = 2

HASHED_FIELDS = ("prescription_id", "from_status", "to_status",
                 "triggered_by_id", "timestamp", "previous_hash")
# What version 1 left outside the digest. Still true of version-1 rows, which is
# why this survives the fix rather than being deleted with it.
AUDITED_BUT_UNHASHED = ("reason", "event_metadata", "triggered_by_type")

HASHED_FIELDS_V2 = ("version", "prescription_id", "sequence_number",
                    "from_status", "to_status", "triggered_by_id",
                    "triggered_by_type", "reason", "event_metadata",
                    "timestamp", "previous_hash")


def event_digest(prescription_id, from_status, to_status, triggered_by_id,
                 timestamp: datetime, previous_hash: str | None) -> str:
    """The version-1 chain hash, recomputed from its documented definition.

    Kept for rows written before migration 0053. They were never rehashed, so
    this is the only rule under which they verify.
    """
    payload = json.dumps({
        "prescription_id": str(prescription_id),
        "from_status": from_status,
        "to_status": to_status,
        "triggered_by_id": str(triggered_by_id) if triggered_by_id else None,
        "timestamp": timestamp.isoformat(),
        "previous_hash": previous_hash,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def canonical_metadata(metadata: dict | None) -> str:
    """`event_metadata` as one stable string.

    Re-derived here rather than imported for the same reason the rest of this
    module is: a shared helper would make a canonicalisation bug agree with
    itself on both sides. Sorted keys and no incidental whitespace, because the
    value round-trips through JSONB before a verifier reads it and Postgres
    preserves neither key order nor formatting.
    """
    return json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"),
                      default=str)


def event_digest_v2(prescription_id, sequence_number: int, from_status,
                    to_status, triggered_by_id, triggered_by_type: str,
                    reason: str | None, event_metadata: dict | None,
                    timestamp: datetime, previous_hash: str | None) -> str:
    """The version-2 chain hash: the transition's shape, position and stated
    justification, recomputed from the definition in migration 0053."""
    payload = json.dumps({
        "version": DIGEST_VERSION,
        "prescription_id": str(prescription_id),
        "sequence_number": sequence_number,
        "from_status": from_status,
        "to_status": to_status,
        "triggered_by_id": str(triggered_by_id) if triggered_by_id else None,
        "triggered_by_type": triggered_by_type,
        "reason": reason,
        "event_metadata": canonical_metadata(event_metadata),
        "timestamp": timestamp.isoformat(),
        "previous_hash": previous_hash,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def digest_for(ev: dict, *, previous_hash: str | None) -> str:
    """Recompute one stored event's hash under whichever version wrote it.

    `previous_hash` is passed in rather than read off `ev` so the caller can
    choose: the *stored* link (what the writer claims) or the *recomputed* one
    (what the chain actually produces). Verifying both, and reporting where they
    disagree, is what catches a re-linked chain.
    """
    if int(ev.get("digest_version") or LEGACY_DIGEST_VERSION) >= DIGEST_VERSION:
        return event_digest_v2(
            ev["prescription_id"], ev["sequence_number"], ev.get("from_status"),
            ev["to_status"], ev.get("triggered_by_id"),
            ev.get("triggered_by_type") or "staff", ev.get("reason"),
            ev.get("event_metadata"), ev["hashed_at"], previous_hash)
    return event_digest(
        ev["prescription_id"], ev.get("from_status"), ev["to_status"],
        ev.get("triggered_by_id"), ev["created_at"], previous_hash)


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
        """Recompute the version-1 chain forward. Each event's expected hash."""
        h = self.history.get(rx)
        if h is None:
            return []
        out: list[str] = []
        prev: str | None = None
        for s in h.steps:
            prev = event_digest(s.rx, s.frm, s.to, s.actor, s.at, prev)
            out.append(prev)
        return out

    def chain_v2(self, rx: str, *, triggered_by_type: str = "staff",
                 event_metadata: dict | None = None) -> list[str]:
        """The same, under the version-2 digest.

        Positions start at 1 to match the writer, which numbers a prescription's
        first event 1 rather than 0. An oracle that started at 0 would disagree
        with every real row while looking correct in isolation.
        """
        h = self.history.get(rx)
        if h is None:
            return []
        out: list[str] = []
        prev: str | None = None
        for i, s in enumerate(h.steps, start=1):
            prev = event_digest_v2(s.rx, i, s.frm, s.to, s.actor,
                                   triggered_by_type, s.reason, event_metadata,
                                   s.at, prev)
            out.append(prev)
        return out

    def chain_findings(self, rx: str, stored: list[dict]) -> list[str]:
        """Compare a stored event sequence against the recomputed chain.

        `stored` is a list of event records in the order the application would
        read them. A version-2 record carries digest_version, sequence_number,
        hashed_at, previous_hash, reason, event_metadata and triggered_by_type
        alongside the original fields; a version-1 record carries only the
        original six and is verified under the old rule.

        A record with no `digest_version` is treated as version 1, so a caller
        holding legacy rows needs to know nothing about versioning.
        """
        out: list[str] = []
        prev: str | None = None
        for i, ev in enumerate(stored):
            version = int(ev.get("digest_version") or LEGACY_DIGEST_VERSION)

            # A version-2 row missing an input the version-2 digest consumes
            # cannot be checked at all. Say that, rather than recomputing
            # against a guess and reporting the mismatch as tampering.
            if version >= DIGEST_VERSION:
                missing = [k for k in ("sequence_number", "hashed_at")
                           if ev.get(k) is None]
                if missing:
                    out.append(
                        f"rx {rx} event {i} ({ev.get('from_status')} → "
                        f"{ev['to_status']}): claims digest version {version} "
                        f"but stores no {', '.join(missing)} — the digest "
                        f"cannot be recomputed, so this row is unverifiable "
                        f"rather than verified")
                    prev = ev["event_hash"]
                    continue

            expect = digest_for(ev, previous_hash=prev)
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

        out.extend(self._link_findings(rx, stored))
        out.extend(self._order_findings(rx, stored))
        return out

    def _link_findings(self, rx: str, stored: list[dict]) -> list[str]:
        """Does each row's STORED predecessor match the row before it?

        Only version 2 can be asked this — version 1 stored no `previous_hash`,
        which is the whole reason its chain was never verifiable from the table.
        The recomputation above already folds the link into each digest, so a
        disagreement here means the stored link and the hashed link differ:
        someone re-pointed the chain and left the digests alone.
        """
        out: list[str] = []
        expected_prev: str | None = None
        for i, ev in enumerate(stored):
            if int(ev.get("digest_version") or LEGACY_DIGEST_VERSION) >= DIGEST_VERSION:
                if ev.get("previous_hash") != expected_prev:
                    out.append(
                        f"rx {rx} event {i}: stored previous_hash "
                        f"{str(ev.get('previous_hash'))[:16]}… is not the hash "
                        f"of the event before it "
                        f"({str(expected_prev)[:16]}…) — the chain was "
                        f"re-linked")
            expected_prev = ev["event_hash"]
        return out

    def _order_findings(self, rx: str, stored: list[dict]) -> list[str]:
        """Can a verifier recover the order the writer used?

        Version 2 records a position and is asked whether it is sane. Version 1
        recorded none, so the only thing that can be asked of it is whether
        `created_at` — the key its writer actually ordered by — distinguishes
        its rows, and transaction-start time frequently does not.
        """
        out: list[str] = []
        v2 = [e for e in stored
              if int(e.get("digest_version") or LEGACY_DIGEST_VERSION) >= DIGEST_VERSION]
        v1 = [e for e in stored if e not in v2]

        seqs = [e.get("sequence_number") for e in v2]
        if seqs:
            if len(set(seqs)) != len(seqs):
                out.append(
                    f"rx {rx}: sequence numbers {seqs} are not unique — two "
                    f"events claim one position and the chain forks")
            elif seqs != sorted(seqs):
                out.append(
                    f"rx {rx}: sequence numbers {seqs} are not in order as read "
                    f"— the read order is not the order the writer used")

        stamps: dict[str, int] = {}
        for ev in v1:
            key = ev["created_at"].isoformat()
            stamps[key] = stamps.get(key, 0) + 1
        for stamp, n in stamps.items():
            if n > 1:
                out.append(
                    f"rx {rx}: {n} version-1 events share created_at {stamp} — "
                    f"their writer selected its predecessor with ORDER BY "
                    f"created_at DESC LIMIT 1, so which one they chain to is "
                    f"arbitrary and that part of the chain cannot be "
                    f"reconstructed by a verifier")
        return out

    def hash_coverage_gap(self, version: int = LEGACY_DIGEST_VERSION) -> list[str]:
        """Fields an auditor reads that the given digest version does not protect.

        Not a disagreement with the application — a property of the documented
        digest. Reported separately so it is never mistaken for a broken chain.

        Version 1 leaves `reason`, `event_metadata` and `triggered_by_type`
        outside. Version 2 spans them and returns nothing, and this is computed
        from the field lists rather than hard-coded so that narrowing the digest
        again would show up here instead of in a comment.
        """
        covered = HASHED_FIELDS_V2 if version >= DIGEST_VERSION else HASHED_FIELDS
        return [f for f in AUDITED_BUT_UNHASHED if f not in covered]

    def unprotected_edit(self, before: dict, after: dict,
                         version: int = LEGACY_DIGEST_VERSION) -> tuple[bool, list[str]]:
        """Did an edit between these two event records leave the chain intact?

        Takes two genuinely different records and reports (chain_still_valid,
        fields_that_changed). Hashing one record twice and observing that it
        matches would prove nothing — the Bible lists tautological checks among
        this codebase's known sins, and an audit demonstration is the last place
        to add one. So this hashes two records that really do differ and lets the
        caller see which differences the digest noticed.

        Under version 2 an edited `reason` is no longer unprotected, so the same
        pair of records that returned True here now returns False. The method
        keeps its name because the question it asks is unchanged; only the
        answer moved.
        """
        changed = [k for k in set(before) | set(after)
                   if before.get(k) != after.get(k)]
        stamped = dict(digest_version=version)
        h_before = digest_for({**stamped, **before},
                              previous_hash=before.get("previous_hash"))
        h_after = digest_for({**stamped, **after},
                             previous_hash=after.get("previous_hash"))
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
            # sequence_number first: created_at is transaction-start time and
            # ties across every row of one transaction, so it cannot name the
            # last event. NULLS LAST keeps a legacy row the 0053 backfill missed
            # from sorting to the front under DESC and posing as the latest.
            "         ORDER BY e.sequence_number DESC NULLS LAST, "
            "                  e.created_at DESC, e.id DESC LIMIT 1) AS last_event, "
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
