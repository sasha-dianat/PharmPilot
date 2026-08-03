"""The Exception Register — giving a finding a life beyond the report that made it.

`reconciliation` recomputes twelve checks on demand and returns what is wrong
right now. That is the correct shape for a check and the wrong shape for
operations: a finding with no identity cannot be assigned, dispositioned,
deduplicated or measured, so "is this new or yesterday's?" has no answer and
time-to-resolution cannot be computed. The drug-catalog side of this codebase
solved the same problem with `issue_dispositions`; this is its inventory
counterpart.

Three ideas carry the module.

**Fingerprint.** A stable hash over (check, entity) that lets a later run
recognise the same problem rather than mint a twin. It deliberately excludes
quantities and timestamps: a lot that was 60 units expired yesterday and 60
units expired today is one problem seen twice, not two problems.

**Granularity.** Some checks are about a thing you can act on — quarantine
*this* lot — and deserve one exception each. Others are systemic: 46 legacy
fills with no movement is one situation, and splitting it into 46 alerts buries
the two expired lots that actually need a pharmacist this morning. Granularity
is therefore a property of the check, not a global rule.

**Score.** Severity alone ranks a 2-unit controlled-substance loss below 46
demo rows. Ranking multiplies what it costs, whom it can hurt, and how sure we
are — and returns the arithmetic, because a priority order nobody can
interrogate is one nobody will trust.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

# ── lifecycle ─────────────────────────────────────────────────────────────
OPEN = "open"
ASSIGNED = "assigned"
ACCEPTED = "accepted"        # a human ruled it tolerable; stays visible, stops nagging
SUPPRESSED = "suppressed"    # known and deliberately hidden until it changes
RESOLVED = "resolved"        # no longer fires

STATUSES = (OPEN, ASSIGNED, ACCEPTED, SUPPRESSED, RESOLVED)
# Statuses a re-run may reopen from. An ACCEPTED or SUPPRESSED finding that
# fires again is not news — that was the point of ruling on it.
REOPENABLE = (RESOLVED,)
ACTIVE = (OPEN, ASSIGNED, ACCEPTED, SUPPRESSED)


class ExceptionError(ValueError):
    """An illegal transition or a malformed finding."""


# ── granularity ───────────────────────────────────────────────────────────
ENTITY = "entity"        # one exception per affected row
AGGREGATE = "aggregate"  # one exception for the whole check

# Which field of a sample identifies the actionable thing, per check.
# A check absent from this map is aggregate by default: if we cannot name the
# thing to act on, splitting the finding produces alerts nobody can action.
CHECK_ENTITY: dict[str, tuple[str, tuple[str, ...]]] = {
    "expired_on_hand":          ("lot", ("lot_number", "irc", "ndc11")),
    "negative_stock":           ("lot", ("lot_id", "lot_number", "irc", "ndc11")),
    "aggregate_drift":          ("item", ("irc", "ndc11")),
    "over_reserved":            ("item", ("irc", "ndc11")),
    "duplicate_lot":            ("lot", ("lot_number", "irc_or_ndc")),
    "unit_conversion_suspect":  ("item", ("irc", "ndc11")),
    "unbound_from_formulary":   ("item", ("ndc11",)),
    "dispense_shortfall":       ("fill", ("fill_id",)),
    "suspicious_adjustment":    ("actor_item", ("actor", "irc", "ndc11", "irc_or_ndc")),
}
# Systemic by nature: one situation, however many rows it touches.
AGGREGATE_CHECKS = {"fill_without_movement", "untraceable_fill", "ledger_chain"}


def granularity(check: str) -> str:
    if check in AGGREGATE_CHECKS:
        return AGGREGATE
    return ENTITY if check in CHECK_ENTITY else AGGREGATE


def entity_of(check: str, sample: dict) -> tuple[str, str]:
    """→ (entity_type, entity_key) for one affected row."""
    etype, fields = CHECK_ENTITY.get(check, ("check", ()))
    parts = [str(sample[f]) for f in fields if sample.get(f) not in (None, "")]
    if not parts:
        return ("check", check)
    return (etype, "|".join(parts))


def fingerprint(check: str, entity_type: str, entity_key: str) -> str:
    """Stable across runs; independent of quantity, timestamp and row order."""
    raw = f"{check}\x1f{entity_type}\x1f{entity_key}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


# ── impact ────────────────────────────────────────────────────────────────
SEVERITY_URGENCY = {"critical": 1.0, "high": 0.65, "medium": 0.35, "info": 0.1}

# Checks whose subject can reach a patient. Separated from severity because the
# two are genuinely different questions: a duplicate lot row is a data defect,
# an expired sellable lot is a person swallowing something.
SAFETY_WEIGHT = {
    "expired_on_hand": 1.0,          # dispensable out-of-date medicine
    "untraceable_fill": 0.8,         # a recall cannot find these patients
    "dispense_shortfall": 0.5,       # the shelf disagrees; a patient may go short
    "fill_without_movement": 0.5,
    "negative_stock": 0.4,
    "over_reserved": 0.4,            # stock promised to a patient that is not there
    "suspicious_adjustment": 0.6,    # controlled-substance diversion signal
    "unbound_from_formulary": 0.2,
    "aggregate_drift": 0.2,
    "ledger_chain": 0.7,
}

# A currency figure spans orders of magnitude, so it is compressed rather than
# scaled linearly — otherwise one large write-off permanently outranks every
# safety finding on the board.
FINANCIAL_REFERENCE = 50_000_000.0     # ~the value at which financial impact saturates

W_URGENCY, W_FINANCIAL, W_SAFETY = 0.45, 0.20, 0.35


@dataclass(frozen=True)
class Impact:
    score: float                      # 0-100
    urgency: float
    financial: float
    safety: float
    confidence: float
    age_multiplier: float
    explanation: str

    def as_dict(self) -> dict:
        return {"score": round(self.score, 1), "urgency": round(self.urgency, 3),
                "financial": round(self.financial, 3), "safety": round(self.safety, 3),
                "confidence": round(self.confidence, 3),
                "age_multiplier": round(self.age_multiplier, 3),
                "explanation": self.explanation}


def score(*, check: str, severity: str, financial_impact: float = 0.0,
          confidence: float = 1.0, occurrences: int = 1,
          age_days: float = 0.0, is_controlled: bool = False) -> Impact:
    """Rank a finding by what it costs, whom it can hurt, and how sure we are.

    Confidence multiplies rather than adds: a model-derived finding we are half
    sure about should rank below a certain one of the same size, not merely
    slightly lower. Deterministic checks pass confidence 1.0 and are unaffected.

    Age escalates, capped. An unresolved critical finding should climb the board
    rather than sink beneath newer noise, but it must not eventually outrank a
    fresh emergency, so the multiplier saturates at 1.5.
    """
    if severity not in SEVERITY_URGENCY:
        raise ExceptionError(f"unknown severity {severity!r}")
    if not 0.0 <= confidence <= 1.0:
        raise ExceptionError("confidence must be within [0, 1]")

    urgency = SEVERITY_URGENCY[severity]
    financial = (math.log10(1 + max(0.0, financial_impact)) /
                 math.log10(1 + FINANCIAL_REFERENCE)) if financial_impact > 0 else 0.0
    financial = min(1.0, financial)
    safety = SAFETY_WEIGHT.get(check, 0.1)
    if is_controlled:
        # A controlled substance raises the floor regardless of quantity: the
        # signal is the discrepancy existing at all, not its size.
        safety = max(safety, 0.9)

    base = W_URGENCY * urgency + W_FINANCIAL * financial + W_SAFETY * safety
    age_mult = min(1.5, 1.0 + 0.05 * math.log1p(max(0.0, age_days)) *
                   max(1.0, math.log1p(occurrences)))
    total = 100.0 * base * confidence * age_mult

    bits = [f"urgency {urgency:.2f}×{W_URGENCY}",
            f"safety {safety:.2f}×{W_SAFETY}"]
    if financial > 0:
        bits.append(f"financial {financial:.2f}×{W_FINANCIAL}")
    if confidence < 1.0:
        bits.append(f"confidence ×{confidence:.2f}")
    if age_mult > 1.0:
        bits.append(f"unresolved {age_days:.0f}d ×{age_mult:.2f}")
    if is_controlled:
        bits.append("controlled substance raises the safety floor to 0.90")

    return Impact(score=min(100.0, total), urgency=urgency, financial=financial,
                  safety=safety, confidence=confidence, age_multiplier=age_mult,
                  explanation=" · ".join(bits))


# ── turning a report into register entries ────────────────────────────────

@dataclass
class Candidate:
    """One prospective register entry derived from a live check."""
    fingerprint: str
    check: str
    severity: str
    entity_type: str
    entity_key: str
    title_fa: str
    detail: str
    remediation: str
    rows: list[dict] = field(default_factory=list)
    financial_impact: float = 0.0
    confidence: float = 1.0
    is_controlled: bool = False

    @property
    def row_count(self) -> int:
        return len(self.rows)


def _financial_of(rows: list[dict]) -> float:
    total = 0.0
    for r in rows:
        for key in ("value", "financial_impact", "extended_value"):
            v = r.get(key)
            if v is not None:
                try:
                    total += abs(float(v))
                except (TypeError, ValueError):
                    pass
                break
    return total


def _controlled_in(rows: list[dict]) -> bool:
    return any(bool(r.get("controlled") or r.get("is_controlled")) for r in rows)


def candidates_from(findings: list) -> list[Candidate]:
    """Split a reconciliation report into register candidates.

    `findings` are `reconciliation.Finding` objects (or their `as_dict()`).
    Only firing checks produce candidates; a check with zero rows is the absence
    of a problem, not a problem with zero rows.

    Note this consumes the finding's FULL row list, not the ten-sample preview
    the API returns — the register is where an operator goes to see all 46.
    """
    out: list[Candidate] = []
    for f in findings:
        d = f if isinstance(f, dict) else f.as_dict()
        check = d["check"]
        rows = list(getattr(f, "samples", None) or d.get("samples") or [])
        count = d.get("count", len(rows))
        if not count:
            continue

        if granularity(check) == AGGREGATE:
            out.append(Candidate(
                fingerprint=fingerprint(check, "check", check), check=check,
                severity=d["severity"], entity_type="check", entity_key=check,
                title_fa=d["title_fa"], detail=d["detail"],
                remediation=d.get("remediation", ""), rows=rows,
                financial_impact=_financial_of(rows),
                is_controlled=_controlled_in(rows)))
            continue

        grouped: dict[tuple[str, str], list[dict]] = {}
        for row in rows:
            grouped.setdefault(entity_of(check, row), []).append(row)
        for (etype, ekey), erows in grouped.items():
            out.append(Candidate(
                fingerprint=fingerprint(check, etype, ekey), check=check,
                severity=d["severity"], entity_type=etype, entity_key=ekey,
                title_fa=d["title_fa"], detail=d["detail"],
                remediation=d.get("remediation", ""), rows=erows,
                financial_impact=_financial_of(erows),
                is_controlled=_controlled_in(erows)))
    return out


@dataclass
class Reconciliation:
    """What a run changed in the register."""
    opened: list[Candidate] = field(default_factory=list)
    recurred: list[tuple[Candidate, dict]] = field(default_factory=list)
    unchanged: list[tuple[Candidate, dict]] = field(default_factory=list)
    resolved: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        return {"opened": len(self.opened), "recurred": len(self.recurred),
                "unchanged": len(self.unchanged), "resolved": len(self.resolved)}


def diff(candidates: list[Candidate], existing: list[dict]) -> Reconciliation:
    """Compare a fresh report against the register's current state.

    Idempotent by construction: running twice with no underlying change opens
    nothing and resolves nothing, because the fingerprint of an unchanged
    problem is unchanged. That property is what makes the run safe to schedule.

    A finding that stops firing is resolved automatically — the operator should
    not have to close what the pharmacy already fixed. Anything ruled
    ACCEPTED or SUPPRESSED stays as it is: re-raising it would undo the ruling.
    """
    by_fp = {e["fingerprint"]: e for e in existing if e.get("status") in ACTIVE}
    seen: set[str] = set()
    out = Reconciliation()

    for c in candidates:
        seen.add(c.fingerprint)
        prior = by_fp.get(c.fingerprint)
        if prior is None:
            out.opened.append(c)
        elif prior.get("status") in (ACCEPTED, SUPPRESSED):
            out.unchanged.append((c, prior))
        else:
            out.recurred.append((c, prior))

    for fp, e in by_fp.items():
        if fp not in seen and e.get("status") not in (SUPPRESSED,):
            # Suppressed findings are deliberately hidden, not deliberately
            # closed; leaving them alone keeps the suppression meaningful.
            out.resolved.append(e)
    return out


# ── transitions ───────────────────────────────────────────────────────────
_ALLOWED: dict[str, tuple[str, ...]] = {
    OPEN:       (ASSIGNED, ACCEPTED, SUPPRESSED, RESOLVED),
    ASSIGNED:   (OPEN, ACCEPTED, SUPPRESSED, RESOLVED),
    ACCEPTED:   (OPEN, ASSIGNED, RESOLVED),
    SUPPRESSED: (OPEN, ASSIGNED, RESOLVED),
    RESOLVED:   (OPEN,),          # only a re-run may reopen
}


def check_transition(current: str, target: str, *, reason: str | None = None) -> None:
    """Raise unless this status change is legal and justified."""
    if current not in STATUSES:
        raise ExceptionError(f"unknown status {current!r}")
    if target not in STATUSES:
        raise ExceptionError(f"unknown status {target!r}")
    if target not in _ALLOWED[current]:
        raise ExceptionError(f"cannot move an exception from {current} to {target}")
    if target in (ACCEPTED, SUPPRESSED) and not (reason or "").strip():
        # Ruling a real finding tolerable is a decision, and a decision without
        # a recorded reason is indistinguishable later from someone clearing
        # their queue.
        raise ExceptionError(f"a reason is required to mark an exception {target}")


def age_days(first_seen: datetime, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    if first_seen.tzinfo is None:
        first_seen = first_seen.replace(tzinfo=timezone.utc)
    return max(0.0, (now - first_seen).total_seconds() / 86400.0)
