#!/usr/bin/env python
"""Phase 2 of the pilot: the workflow and audit oracle against the real machine.

    python scripts/verify_workflow_audit.py

Drives the actual `RxStateMachine` through a real prescription lifecycle in
`pharmpilot_test`, then verifies the audit chain it produced against an oracle
that re-derives both the legal transition graph and the SHA-256 digest from the
specification.

Two kinds of result, deliberately separated:

  **check** — a property that must hold. A failure here is a regression and the
  script exits non-zero.

  **note** — a finding this run *demonstrates* about the current design. These
  are already recorded in the ledger and referred to their owners; printing them
  as failures would make this script permanently red and therefore worthless as a
  gate, which is how a suite stops being read.

Three of the five original notes are now checks. Migration 0053 gave
`rx_state_events` the columns that make its chain verifiable by someone other
than the writer — `hashed_at`, `previous_hash`, `sequence_number`,
`digest_version` — and widened the digest to span `reason`, `event_metadata`,
`triggered_by_type` and the event's position. What used to be demonstrated as
broken is now asserted as working, against the same live database and by the
same oracle.

Two notes remain, both out of this work's scope and owned elsewhere: the
unreachable `transferred_out` status, and `transition()` taking no pharmacy.

Rows written before 0053 were NOT rehashed — doing so would have re-blessed any
row that had already been altered. They stay `digest_version = 1` and verify
under the original six-field digest; the oracle dispatches per row.

Everything lands under a pharmacy created for this run.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text                                       # noqa: E402
from sqlalchemy.ext.asyncio import (async_sessionmaker,           # noqa: E402
                                    create_async_engine)

from services.core.pharmacy_workflow.state_machine import (        # noqa: E402
    InvalidTransitionError, RxStateMachine)
from shared.models.prescription import RxStatus                    # noqa: E402
from tests.simulation.domains.workflow import (                    # noqa: E402
    WorkflowDomain, event_digest)

from verify_demand_shape import person                             # noqa: E402
from verify_receiving import tenant, url                           # noqa: E402

# rx_number carries a GLOBAL unique constraint (prescriptions_rx_number_key),
# not a per-pharmacy one, so a fixed literal collides with the previous run.
RUN = uuid.uuid4().hex[:6].upper()

HAPPY = [RxStatus.PENDING_DUR, RxStatus.PENDING_VERIFICATION,
         RxStatus.VERIFICATION_IN_PROGRESS, RxStatus.PENDING_ADJUDICATION,
         RxStatus.READY_TO_FILL, RxStatus.FILLING, RxStatus.FILLED,
         RxStatus.DISPENSED]

# Every instant the state machine hashes. `transition()` calls
# `datetime.now(timezone.utc)` exactly once, immediately before computing the
# digest, so recording that call recovers the value the hash was built from —
# which is the only way to tell "the chain is broken" apart from "the chain was
# never verifiable". The two look identical from the table.
CAPTURED: list[datetime] = []


def _install_clock_probe() -> None:
    from services.core.pharmacy_workflow import state_machine as SM

    real = SM.datetime

    class Probe(real):                                    # type: ignore[misc,valid-type]
        @classmethod
        def now(cls, tz=None):
            v = real.now(tz)
            CAPTURED.append(v)
            return v

    SM.datetime = Probe


async def a_prescription(db, *, pid, patient, prescriber, rx_number,
                         controlled=False) -> uuid.UUID:
    rx = uuid.uuid4()
    await db.execute(text("""
        INSERT INTO prescriptions
          (id, pharmacy_id, patient_id, prescriber_id, rx_number, ndc,
           drug_name, sig_text, quantity_prescribed, days_supply,
           written_date, source, status, refills_authorized,
           refills_remaining, is_controlled, created_at, updated_at, is_deleted)
        VALUES (:i,:ph,:pa,:pr,:rn,'00093721410','audit drug','1 daily',30,30,
                CURRENT_DATE,'escript','intake',1,1,:ctl,now(),now(),false)"""),
        {"i": rx, "ph": pid, "pa": patient, "pr": prescriber, "rn": rx_number,
         "ctl": controlled})
    await db.commit()
    return rx


async def events(db, rx_id) -> list[dict]:
    """The chain as a verifier reads it back — nothing the writer kept in memory.

    Ordered by `sequence_number`, the key the writer now assigns, rather than by
    `created_at`. Postgres now() is transaction-start time, so `created_at` ties
    across every row of one transaction and cannot order them. NULLS LAST covers
    a version-1 row the 0053 backfill did not reach.
    """
    rows = (await db.execute(text(
        "SELECT prescription_id, from_status, to_status, triggered_by_id, "
        "       triggered_by_type, reason, metadata AS event_metadata, "
        "       created_at, hashed_at, previous_hash, sequence_number, "
        "       digest_version, event_hash "
        "  FROM rx_state_events WHERE prescription_id = :r "
        " ORDER BY sequence_number ASC NULLS LAST, created_at ASC, id ASC"),
        {"r": rx_id})).mappings().all()
    return [dict(r) for r in rows]


async def main() -> int:
    _install_clock_probe()
    engine = create_async_engine(url(), echo=False)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    fails: list[str] = []
    notes: list[str] = []

    def check(label: str, cond: bool, detail: str = "") -> None:
        print(f"  {'ok  ' if cond else 'FAIL'}  {label}"
              + (f"  — {detail}" if detail and not cond else ""))
        if not cond:
            fails.append(label)

    def note(label: str, detail: str) -> None:
        print(f"  NOTE  {label}\n        {detail}")
        notes.append(label)

    async with sm() as db:
        pid = await tenant(db)
        patient, prescriber = await person(db, pid)
        staff_id = uuid.uuid4()
        print(f"\ntenant {pid}\n")

        w = WorkflowDomain()
        RX1_LABEL = f"AUD-001-{RUN}"

        # ── the specification gate ────────────────────────────────────────
        print("the specification gate")
        drift = w.graph_drift()
        check("the oracle's lifecycle and the implementation's agree",
              not drift, "; ".join(drift[:5]))
        unreachable = w.unreachable_states()
        if unreachable:
            note("a declared state no run can reach",
                 f"{unreachable} — a terminal status with no incoming edge. "
                 f"Either transfer-out is a real operation missing its edge, or "
                 f"the status is dead vocabulary; the graph cannot say which.")

        # ── a real lifecycle through the real machine ─────────────────────
        print("\na prescription driven through the real state machine")
        rx_id = await a_prescription(db, pid=pid, patient=patient,
                                     prescriber=prescriber, rx_number=f"AUD-001-{RUN}")
        machine = RxStateMachine(db)
        for to in HAPPY:
            await machine.transition(rx_id, to, triggered_by_id=staff_id,
                                     reason=f"verify → {to.value}",
                                     check_epcs=False)
            await db.commit()          # one transaction per transition
        stored = await events(db, rx_id)
        check("every documented step was recorded", len(stored) == len(HAPPY),
              f"{len(stored)} events for {len(HAPPY)} transitions")
        # `captured` holds the Python instants the machine actually hashed — see
        # the probe below, which is what turns a suspicion into a proof.
        captured = CAPTURED[-len(stored):] if len(CAPTURED) >= len(stored) else []

        status = (await db.execute(text(
            "SELECT status FROM prescriptions WHERE id = :r"), {"r": rx_id})).scalar()
        check("the prescription reached dispensed",
              status == RxStatus.DISPENSED.value, str(status))

        # ── the chain, recomputed independently ───────────────────────────
        # This section used to be the finding. Before migration 0053 every one
        # of these links failed when recomputed from the table and every one
        # verified against a Python instant the writer had discarded — the chain
        # was unverifiable by anyone who was not the process that wrote it.
        print("\nthe audit chain, recomputed from the specification")
        found = w.chain_findings(RX1_LABEL, stored)
        breaks = [f for f in found if "broken at this link" in f]
        unverifiable = [f for f in found if "unverifiable rather than" in f]
        relinked = [f for f in found if "re-linked" in f]
        ordering = [f for f in found if "share created_at" in f
                    or "not in order" in f or "not unique" in f]

        check("the chain verifies from the database alone, with nothing the "
              "writer kept in memory", not breaks and not unverifiable,
              "; ".join((breaks + unverifiable)[:2]))
        check("every event stores the digest version it was written under",
              all(e["digest_version"] for e in stored),
              str([e["digest_version"] for e in stored]))
        check("every event stores the instant that was hashed",
              all(e["hashed_at"] is not None for e in stored))
        check("every event after the first stores its predecessor's hash",
              all(e["previous_hash"] is not None for e in stored[1:])
              and stored[0]["previous_hash"] is None)
        check("the stored links match the rows they point at", not relinked,
              "; ".join(relinked[:1]))
        check("the order a verifier reads is the order the writer used",
              not ordering, "; ".join(ordering[:1]))
        check("positions are 1..n with no gaps",
              [e["sequence_number"] for e in stored] == list(range(1, len(stored) + 1)),
              str([e["sequence_number"] for e in stored]))

        # The clock probe survives the fix, now checking the opposite claim.
        # `hashed_at` must be the instant the machine actually hashed, not
        # merely *an* instant: a column written from a second `now()` call would
        # look right in the table and still fail every recomputation.
        check("hashed_at is the instant the machine hashed, not a second "
              "reading of the clock",
              bool(captured) and all(e["hashed_at"] == t
                                     for e, t in zip(stored, captured)),
              f"{sum(1 for e, t in zip(stored, captured) if e['hashed_at'] != t)}"
              f" of {len(stored)} differ")

        # And the drift that used to break everything is still there — it is
        # simply no longer load-bearing, because nothing hashes created_at.
        drifts = [abs((e["created_at"] - e["hashed_at"]).total_seconds())
                  for e in stored if e["hashed_at"]]
        if drifts:
            print(f"        (application and database clocks still differ by a "
                  f"median {sorted(drifts)[len(drifts)//2]:.6f}s — harmless now "
                  f"that created_at is not what gets hashed)")

        # ── tamper detection: what the chain catches ──────────────────────
        # Measured against `stored` — the rows as they sit in the database.
        # Before 0053 this had to run against a replayed sequence, because
        # every stored link was already broken and a tamper test that cannot
        # fail is worse than none.
        print("\nwhat the chain catches")
        for label, field, value in [
            ("a rewritten status", "to_status", RxStatus.CANCELLED.value),
            ("a rewritten actor", "triggered_by_id", uuid.uuid4()),
            ("a rewritten actor type", "triggered_by_type", "ai"),
            ("a rewritten position", "sequence_number", 99),
        ]:
            tampered = [dict(e) for e in stored]
            tampered[3][field] = value
            check(f"{label} is detected",
                  any("broken at this link" in f
                      for f in w.chain_findings(RX1_LABEL, tampered)))

        # ── the finding this work closed ──────────────────────────────────
        # Under version 1 this exact edit left every link verifying. It is done
        # against the live database rather than in memory, because that is how
        # it was demonstrated to be broken.
        print("\nan edited justification, against the live database")
        target = stored[2]
        original_reason = target["reason"]
        await db.execute(text(
            "UPDATE rx_state_events SET reason = :new WHERE prescription_id = :r "
            "  AND to_status = :t"),
            {"new": "rewritten after the fact", "r": rx_id,
             "t": target["to_status"]})
        await db.commit()
        after = await events(db, rx_id)
        edited = [e for e in after if e["reason"] == "rewritten after the fact"]
        check("the reason really was changed in the database", bool(edited))
        after_breaks = [f for f in w.chain_findings(RX1_LABEL, after)
                        if "broken at this link" in f]
        check("and the chain now breaks on it", bool(after_breaks),
              f"reason on the {target['to_status']} event went from "
              f"{original_reason!r} to 'rewritten after the fact' and every link "
              f"still verified — the digest has stopped covering reason")
        check("exactly the edited link breaks, and the damage does not spread",
              len(after_breaks) == 1, f"{len(after_breaks)} break(s)")
        check("the version-2 digest leaves no audited field unprotected",
              w.hash_coverage_gap(version=2) == [],
              str(w.hash_coverage_gap(version=2)))

        # Repair the row so the ordering and tenancy sections below read a
        # coherent chain rather than inheriting this deliberate break.
        await db.execute(text(
            "UPDATE rx_state_events SET reason = :old WHERE prescription_id = :r "
            "  AND to_status = :t"),
            {"old": original_reason, "r": rx_id, "t": target["to_status"]})
        await db.commit()
        check("and the chain verifies again once the edit is reverted",
              not [f for f in w.chain_findings(RX1_LABEL, await events(db, rx_id))
                   if "broken at this link" in f])

        # ── a status that moved without the machine ───────────────────────
        print("\na status that moved without passing through the machine")
        rx2 = await a_prescription(db, pid=pid, patient=patient,
                                   prescriber=prescriber, rx_number=f"AUD-002-{RUN}")
        m2 = RxStateMachine(db)
        await m2.transition(rx2, RxStatus.PENDING_DUR, triggered_by_id=staff_id,
                            check_epcs=False)
        await db.commit()
        # Exactly what pos.py and AutoPAAgent do: write the column directly.
        await db.execute(text(
            "UPDATE prescriptions SET status = :s WHERE id = :r"),
            {"s": RxStatus.DISPENSED.value, "r": rx2})
        await db.commit()

        class Ctx:
            pass
        ctx = Ctx()
        ctx.db, ctx.pharmacy_id = db, pid
        db_findings = await w._check_against_db(ctx)
        check("a direct status write is detected by the oracle",
              any(f"AUD-002-{RUN}" in f and "without passing through the state machine" in f
                  for f in db_findings), str(db_findings[:2]))
        check("and the prescription driven properly is not flagged",
              not any(RX1_LABEL in f for f in db_findings), str(db_findings[:2]))

        # ── two transitions in one transaction ────────────────────────────
        print("\ntwo transitions in one transaction")
        rx3 = await a_prescription(db, pid=pid, patient=patient,
                                   prescriber=prescriber, rx_number=f"AUD-003-{RUN}")
        m3 = RxStateMachine(db)
        await m3.transition(rx3, RxStatus.PENDING_DUR, triggered_by_id=staff_id,
                            check_epcs=False)
        await m3.transition(rx3, RxStatus.PENDING_VERIFICATION,
                            triggered_by_id=staff_id, check_epcs=False)
        await db.commit()               # both rows in ONE transaction
        three = await events(db, rx3)
        stamps = {e["created_at"].isoformat() for e in three}
        seqs = [e["sequence_number"] for e in three]

        # The ambiguity is still in `created_at` and always will be — Postgres
        # now() is transaction-start time. What changed is that nothing depends
        # on it any more. Asserting the tie still exists keeps this honest: if
        # it ever stopped, the checks below would be passing for the wrong
        # reason and would no longer be testing anything.
        check("two events in one transaction still share created_at exactly",
              len(three) > 1 and len(stamps) == 1,
              f"{len(stamps)} distinct stamps across {len(three)} events")
        check("but they carry distinct, ordered positions",
              seqs == sorted(seqs) and len(set(seqs)) == len(seqs), str(seqs))
        check("and the second chains to the first by stored hash, not by clock",
              three[1]["previous_hash"] == three[0]["event_hash"])
        check("so the chain written in one transaction still verifies",
              not [f for f in w.chain_findings(f"AUD-003-{RUN}", three)
                   if "broken at this link" in f])

        # The constraint is what makes the position trustworthy under
        # concurrency: without it two transitions racing on one prescription
        # both read max(sequence_number) and both write the same successor.
        dup = False
        try:
            await db.execute(text(
                "INSERT INTO rx_state_events (id, prescription_id, from_status, "
                "  to_status, triggered_by_type, event_hash, sequence_number, "
                "  digest_version, hashed_at, created_at, updated_at, is_deleted) "
                "VALUES (gen_random_uuid(), :r, 'pending_dur', "
                "  'pending_verification', 'staff', :h, :s, 2, now(), now(), "
                "  now(), false)"),
                {"r": rx3, "h": "0" * 64, "s": seqs[-1]})
            await db.commit()
        except Exception:
            await db.rollback()
            dup = True
        check("a second event cannot claim a position already taken", dup,
              "the unique constraint on (prescription_id, sequence_number) is "
              "missing — two concurrent transitions can fork the chain")

        # ── the graph refuses what the specification refuses ──────────────
        print("\nthe machine refuses an illegal step")
        rx4 = await a_prescription(db, pid=pid, patient=patient,
                                   prescriber=prescriber, rx_number=f"AUD-004-{RUN}")
        refused = False
        try:
            await RxStateMachine(db).transition(
                rx4, RxStatus.DISPENSED, triggered_by_id=staff_id,
                check_epcs=False)
        except InvalidTransitionError:
            refused = True
        await db.rollback()
        check("intake straight to dispensed is refused", refused)
        check("and the oracle agrees it is illegal",
              not w.legal("intake", "dispensed"))

        # ── tenancy ───────────────────────────────────────────────────────
        print("\ntenancy on the command itself")
        other = await tenant(db)
        o_patient, o_prescriber = await person(db, other)
        rx5 = await a_prescription(db, pid=other, patient=o_patient,
                                   prescriber=o_prescriber, rx_number=f"AUD-005-{RUN}")
        await RxStateMachine(db).transition(
            rx5, RxStatus.PENDING_DUR, triggered_by_id=staff_id, check_epcs=False)
        await db.commit()
        moved = (await db.execute(text(
            "SELECT status FROM prescriptions WHERE id = :r"), {"r": rx5})).scalar()
        if moved == RxStatus.PENDING_DUR.value:
            note("transition() takes no pharmacy and checks none",
                 f"a prescription belonging to pharmacy {other} was advanced by "
                 f"a caller holding no claim to it. The command loads the Rx by "
                 f"id alone — already recorded as a Critical gap; the oracle now "
                 f"demonstrates it rather than citing it.")

    await engine.dispose()
    print(f"\n{len(notes)} finding(s) demonstrated, {len(fails)} check(s) failed")
    print(f"{'PASS' if not fails else 'FAIL: ' + ', '.join(fails)}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
