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
    """The chain as a verifier would have to read it back."""
    rows = (await db.execute(text(
        "SELECT prescription_id, from_status, to_status, triggered_by_id, "
        "       created_at, event_hash, reason "
        "  FROM rx_state_events WHERE prescription_id = :r "
        " ORDER BY created_at ASC, id ASC"), {"r": rx_id})).mappings().all()
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
        print("\nthe audit chain, recomputed from the specification")
        found = w.chain_findings(RX1_LABEL, stored)
        breaks = [f for f in found if "broken at this link" in f]
        ordering = [f for f in found if "share created_at" in f]
        check("one transaction per transition leaves the order unambiguous",
              not ordering, "; ".join(ordering[:1]))

        # Does it fail because the rows were tampered with, or because the
        # digest was never recomputable? From the table alone those are
        # indistinguishable, so replay with the instants the machine hashed.
        replay = [dict(e, created_at=t) for e, t in zip(stored, captured)]
        replay_breaks = [f for f in w.chain_findings(RX1_LABEL, replay)
                         if "broken at this link" in f]
        check("the chain verifies against the instants the machine hashed",
              captured and not replay_breaks,
              f"{len(replay_breaks)} break(s) even on replay")

        if breaks and not replay_breaks:
            drifts = [abs((e["created_at"] - t).total_seconds())
                      for e, t in zip(stored, captured)]
            note("the hashed timestamp is never stored, so no one can verify "
                 "the chain from the database",
                 f"all {len(breaks)} links fail when recomputed from "
                 f"created_at and all {len(stored)} verify against the Python "
                 f"instant the machine hashed (median drift "
                 f"{sorted(drifts)[len(drifts)//2]:.6f}s). `transition()` hashes "
                 f"`datetime.now(timezone.utc)` while `created_at` is "
                 f"`server_default=func.now()` — a different clock, and the "
                 f"hashed value is written to no column. rx_state_events also "
                 f"stores no previous_hash, so both inputs a verifier needs are "
                 f"absent: the tamper evidence is unfalsifiable in practice.")
        else:
            check("the chain the machine wrote verifies end to end", not breaks,
                  "; ".join(breaks[:2]))

        # ── tamper detection: what the chain DOES catch ───────────────────
        # Measured against `replay`, the only sequence that verifies at all. Run
        # against `stored` these would "pass" because every link is already
        # broken — a tamper test that cannot fail is worse than none.
        print("\nwhat the chain catches")
        tampered = [dict(e) for e in replay]
        tampered[3]["to_status"] = RxStatus.CANCELLED.value
        check("a rewritten status is detected",
              any("broken at this link" in f
                  for f in w.chain_findings(RX1_LABEL, tampered)))

        tampered = [dict(e) for e in replay]
        tampered[2]["triggered_by_id"] = uuid.uuid4()
        check("a rewritten actor is detected",
              any("broken at this link" in f
                  for f in w.chain_findings(RX1_LABEL, tampered)))

        # ── and what it does not ──────────────────────────────────────────
        print("\nwhat the chain does not catch")
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
        after_replay = [dict(e, created_at=t) for e, t in zip(after, captured)]
        after_breaks = [f for f in w.chain_findings(RX1_LABEL, after_replay)
                        if "broken at this link" in f]
        if edited and not after_breaks:
            note("an edited reason leaves the chain intact",
                 f"reason on the {target['to_status']} event was changed from "
                 f"{original_reason!r} to 'rewritten after the fact' and every "
                 f"link still verifies. The digest does NOT span "
                 f"{', '.join(w.hash_coverage_gap())} — precisely the fields a "
                 f"regulator reads when asking why an override happened.")
        else:
            check("an edited reason leaves the chain intact (expected finding)",
                  False, f"{len(after_breaks)} break(s) — the digest may now "
                         f"cover reason; update the oracle")

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
        if len(three) > 1 and len(stamps) == 1:
            note("two events in one transaction share created_at exactly",
                 f"{len(three)} events, {len(stamps)} distinct timestamp. "
                 f"created_at is server_default=func.now() and Postgres now() is "
                 f"transaction-start time, while the writer selects its "
                 f"predecessor with ORDER BY created_at DESC LIMIT 1 — so which "
                 f"event the second one chains to is decided by the planner. "
                 f"rx_state_events also stores no previous_hash column, so a "
                 f"verifier cannot recover the order the writer used.")
        else:
            check("two events in one transaction share created_at (expected)",
                  False, f"{len(stamps)} distinct stamps — the design changed")

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
