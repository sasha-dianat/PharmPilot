"""Recommendations — advice with a name on it, and the scoreboard it earns.

Every advisory component here produces suggestions and, until now, forgot them.
That makes an advisory system unfalsifiable: a detector firing forty times a
week and accepted twice looks identical, from the outside, to one firing forty
times and accepted forty. This module gives each suggestion an identity, a
decision, and a score.

Three ideas carry it.

**Identity, not novelty.** A fingerprint is the hash of what the advice is
*about* — kind, subject, and the shape of the proposal — excluding quantities
and timestamps. A nightly job must recognise yesterday's recommendation rather
than raise it again; otherwise the acceptance rate measures how often the job
ran. This mirrors `exceptions.fingerprint()`, deliberately: the Exception
Register learned the same lesson and the two should behave alike.

**Agreement is not correctness.** `accepted` says a pharmacist agreed.
`outcome` says what happened. Reporting acceptance as accuracy is the standard
way an advisory system flatters itself — staff accept what is easy to accept,
and a recommendation to do nothing is the easiest of all.

**A rejection is the most valuable row in the table.** It is a labelled
negative produced by an expert at the moment they had the full context, and it
is the one thing that cannot be reconstructed afterwards. The decision note is
required for a rejection for that reason and no other.

Pure functions over already-fetched rows; `now` is always injected.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

KINDS = ("reorder", "write_off", "cycle_count", "formulary_binding",
         "anomaly", "expiry_risk", "demand_refresh")
STATUSES = ("open", "accepted", "rejected", "superseded", "expired")
TERMINAL = ("accepted", "rejected", "superseded", "expired")

# How long a recommendation stands before it stops describing the shelf. A
# reorder computed three weeks ago is about stock levels that have moved.
TTL_DAYS = {
    "reorder": 7, "expiry_risk": 30, "anomaly": 14, "cycle_count": 14,
    "formulary_binding": 90, "write_off": 30, "demand_refresh": 30,
}
DEFAULT_TTL_DAYS = 14

# Below this many decided recommendations, an acceptance rate is a fraction of a
# small number and reads as precision it has not earned.
MIN_DECIDED_FOR_RATE = 10


class RecommendationError(ValueError):
    """A recommendation that cannot be made or decided as asked."""


@dataclass(frozen=True)
class Proposal:
    kind: str
    subject: str                 # ndc11, lot id, or whatever the kind is about
    proposal: dict
    explanation: str
    produced_by: str
    features: dict = field(default_factory=dict)
    confidence: float | None = None
    severity: str | None = None
    model_version: str | None = None

    def as_dict(self) -> dict:
        return {"kind": self.kind, "subject": self.subject,
                "proposal": self.proposal, "explanation": self.explanation,
                "produced_by": self.produced_by, "features": self.features,
                "confidence": self.confidence, "severity": self.severity,
                "model_version": self.model_version,
                "fingerprint": fingerprint(self)}


# Keys excluded from the fingerprint: they change every run without changing
# what the advice is about, and including them would make every night's
# recommendation a new one.
VOLATILE_KEYS = {"quantity", "recommended_order_qty", "units", "days_of_stock",
                 "on_hand", "as_of", "generated_at", "order_by_date",
                 "est_cost", "metric_value", "detected_at", "confidence"}


def fingerprint(p: Proposal) -> str:
    """Stable identity for a piece of advice.

    Quantities are excluded on purpose. "Reorder metformin" is the same
    recommendation whether today's arithmetic says 340 units or 360; treating
    the number as part of the identity would reopen it every night and make the
    acceptance rate a measure of the scheduler.
    """
    if p.kind not in KINDS:
        raise RecommendationError(f"unknown recommendation kind {p.kind!r}")
    shape = {k: v for k, v in sorted(p.proposal.items())
             if k not in VOLATILE_KEYS}
    payload = json.dumps(
        {"kind": p.kind, "subject": p.subject, "shape": shape},
        sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def ttl_days(kind: str) -> int:
    return TTL_DAYS.get(kind, DEFAULT_TTL_DAYS)


def expires_at(kind: str, *, created_at: datetime) -> datetime:
    at = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
    return at + timedelta(days=ttl_days(kind))


def decide(row: dict, *, status: str, note: str | None,
           decided_by, now: datetime | None = None) -> dict:
    """Record a human's decision on a recommendation.

    A rejection must carry a reason. That is not bureaucracy: the reason is the
    labelled negative, produced by an expert at the moment they had the full
    context, and it is the only part of this that cannot be recovered later. An
    acceptance needs no note — the proposal already says what was agreed to.
    """
    if status not in ("accepted", "rejected"):
        raise RecommendationError(
            f"a person can accept or reject; {status!r} is not a decision")
    if row.get("status") != "open":
        raise RecommendationError(
            f"recommendation is already {row.get('status')!r} — deciding it "
            f"again would overwrite the original label")
    if decided_by is None:
        raise RecommendationError("a decision must name who made it")
    if status == "rejected" and not (note or "").strip():
        raise RecommendationError(
            "a rejection must say why — that reason is the training signal, "
            "and it cannot be reconstructed afterwards")

    return {"id": row.get("id"), "status": status,
            "decision_note": (note or "").strip() or None,
            "decided_by_id": decided_by,
            "decided_at": now or datetime.now(timezone.utc)}


# How long a decision suppresses the same advice from being raised again. A
# recommendation dismissed on Monday reappearing on Tuesday is the classic way
# an alert queue teaches people to ignore it — and it re-inflates the very
# denominator the fingerprint exists to protect.
COOLDOWN_DAYS = {"reorder": 3, "anomaly": 14, "cycle_count": 30,
                 "write_off": 14, "expiry_risk": 30, "formulary_binding": 180,
                 "demand_refresh": 14}
DEFAULT_COOLDOWN_DAYS = 14


def cooldown_days(kind: str) -> int:
    return COOLDOWN_DAYS.get(kind, DEFAULT_COOLDOWN_DAYS)


def suppressed(rows: list[dict], *, now: datetime | None = None) -> set[str]:
    """Fingerprints that were decided recently enough not to raise again.

    A rejection suppresses for the same window as an acceptance, deliberately.
    Both are a human saying "I have dealt with this"; re-asking either of them
    tomorrow is the same discourtesy and the same measurement error.
    """
    stamp = now or datetime.now(timezone.utc)
    out: set[str] = set()
    for r in rows:
        if r.get("status") not in ("accepted", "rejected"):
            continue
        decided = r.get("decided_at")
        fp = r.get("fingerprint")
        if decided is None or not fp:
            continue
        if decided.tzinfo is None:
            decided = decided.replace(tzinfo=timezone.utc)
        if decided + timedelta(days=cooldown_days(str(r.get("kind")))) > stamp:
            out.add(str(fp))
    return out


def supersede(rows: list[dict], keep_fingerprints: set[str]) -> list[dict]:
    """Open recommendations no longer produced by the current run.

    Closed as `superseded`, not `rejected`. Nobody disagreed with them; the
    conditions moved. Counting them as rejections would libel the model that
    made them and quietly depress every precision figure.
    """
    return [{"id": r.get("id"), "status": "superseded"}
            for r in rows
            if r.get("status") == "open"
            and r.get("fingerprint") not in keep_fingerprints]


def expired(rows: list[dict], *, now: datetime | None = None) -> list[dict]:
    """Open recommendations that have stopped describing the shelf."""
    stamp = now or datetime.now(timezone.utc)
    out = []
    for r in rows:
        if r.get("status") != "open":
            continue
        created = r.get("created_at")
        if created is None:
            continue
        if expires_at(str(r.get("kind")), created_at=created) <= stamp:
            out.append({"id": r.get("id"), "status": "expired"})
    return out


@dataclass(frozen=True)
class Score:
    kind: str
    produced: int
    decided: int
    accepted: int
    rejected: int
    open_now: int
    superseded: int
    acceptance_rate: float | None
    verdict: str
    explanation: str

    def as_dict(self) -> dict:
        return {"kind": self.kind, "produced": self.produced,
                "decided": self.decided, "accepted": self.accepted,
                "rejected": self.rejected, "open": self.open_now,
                "superseded": self.superseded,
                "acceptance_rate": self.acceptance_rate,
                "verdict": self.verdict, "explanation": self.explanation}


def scoreboard(rows: list[dict]) -> list[Score]:
    """Per-kind acceptance, and an explicit verdict on whether it is trusted.

    `superseded` and `expired` are excluded from the denominator. They are not
    disagreements — one is the world moving on, the other is nobody looking. A
    denominator that counts them measures staffing levels, not model quality.

    The verdicts are deliberately blunt:
      unmeasured — too few decisions to say anything
      ignored    — produced a lot, decided almost none. The most important
                   state to name, because it looks like a working detector on
                   every dashboard that counts alerts rather than outcomes.
      noisy      — decided and mostly rejected: the threshold is wrong
      trusted    — decided and mostly accepted
      mixed      — everything else
    """
    by_kind: dict[str, list[dict]] = {}
    for r in rows:
        by_kind.setdefault(str(r.get("kind")), []).append(r)

    out: list[Score] = []
    for kind, group in sorted(by_kind.items()):
        acc = sum(1 for r in group if r.get("status") == "accepted")
        rej = sum(1 for r in group if r.get("status") == "rejected")
        opn = sum(1 for r in group if r.get("status") == "open")
        sup = sum(1 for r in group if r.get("status") in ("superseded", "expired"))
        decided = acc + rej
        rate = (acc / decided) if decided else None

        if decided < MIN_DECIDED_FOR_RATE:
            if len(group) >= 3 * MIN_DECIDED_FOR_RATE and decided == 0:
                verdict = "ignored"
                why = (f"{len(group)} produced and none decided — staff are not "
                       f"acting on this at all, which an alert count would show "
                       f"as a working detector.")
            else:
                verdict = "unmeasured"
                why = (f"only {decided} decision(s); an acceptance rate here "
                       f"would be a fraction of a small number.")
        elif rate is not None and rate < 0.3:
            verdict = "noisy"
            why = (f"{acc} of {decided} accepted — the threshold is producing "
                   f"more work than value.")
        elif rate is not None and rate >= 0.7:
            verdict = "trusted"
            why = f"{acc} of {decided} accepted."
        else:
            verdict = "mixed"
            why = f"{acc} of {decided} accepted."

        out.append(Score(kind=kind, produced=len(group), decided=decided,
                         accepted=acc, rejected=rej, open_now=opn,
                         superseded=sup,
                         acceptance_rate=round(rate, 3) if rate is not None else None,
                         verdict=verdict, explanation=why))
    return out


def rejection_reasons(rows: list[dict], *, kind: str | None = None) -> list[dict]:
    """The notes behind rejections, most recent first.

    The training set. Reading these is how a threshold gets set by evidence
    rather than by whoever last complained about the noise.
    """
    out = [{"id": r.get("id"), "kind": r.get("kind"), "ndc11": r.get("ndc11"),
            "note": r.get("decision_note"), "decided_at": r.get("decided_at"),
            "confidence": r.get("confidence")}
           for r in rows
           if r.get("status") == "rejected"
           and (kind is None or r.get("kind") == kind)]
    out.sort(key=lambda r: (r["decided_at"] is None, r["decided_at"]), reverse=True)
    return out
