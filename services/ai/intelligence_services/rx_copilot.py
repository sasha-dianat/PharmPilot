"""
#15 — End-to-End Rx Workflow Intelligent Automation  (the Rx Copilot)
=====================================================================
Sits over the existing Rx state machine. For each Rx it computes a per-step
AUTOMATION-CONFIDENCE and, where the pharmacist has authorised it, auto-advances
steps — with a full, reversible audit trail. AI proposes; the pharmacist remains
the accountable signer. Nothing auto-dispenses.

LOCAL BRAIN (always available — fuses sibling services' local signals):
  • DUR step       → local clinical history confidence ("seen safe N times")
  • Adjudication   → LOCAL claim-rejection probability (heuristic/optional model)
  • Verification   → package-visual + prescriber-deviation signals
  Each step auto-clears only when local confidence ≥ a pharmacist-set threshold.
  Offline, automation is MORE valuable (it removes clicks when connectivity flaps).

CLOUD BRAIN (when online):
  • Cloud LLM second-opinion on borderline DUR; live adjudication submission.
  Offline → claim submission is parked in the outbox; everything else still
  auto-advances locally.

Thresholds are per-step and pharmacist-configurable; defaults are conservative.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.ai.intelligence_core import (
    Tier, IntelligenceTier, build_envelope,
)
from services.core.pharmacy_workflow.state_machine import (
    RxStateMachine, InvalidTransitionError, EPCSRequiredError,
)
from shared.models.prescription import RxStatus

logger = logging.getLogger(__name__)

SERVICE = "rx_copilot"

# Conservative default auto-clear thresholds (pharmacist can raise/lower).
DEFAULT_THRESHOLDS = {
    "dur":           0.95,
    "adjudication":  0.95,
    "verification":  0.90,
}

# ─── Pilot-phase auto-execution scope ──────────────────────────────────────────
#
# Per pharmacist guidance gathered for #15 Tier-2:
#   • DUR clearance must stay MANUAL (controlled substances flagged for the
#     pharmacist so they're never neglected).
#   • Verification must stay MANUAL (never auto-advance — mirrors DUR).
#   • Adjudication MAY be auto-advanced in the pilot ONLY as a SIMULATED outcome
#     (no live PBM/insurance connector exists yet); production must replace this
#     with a real submission gated on a confirmed clean-claim response before
#     the transition fires.
#
# `dur` and `verification` remain pure decision aids: evaluate() and
# auto_advance() both still compute and surface their confidence/recommendation
# so the UI can show "AI suggests clearing this", but auto_advance() will NEVER
# execute a state transition for a step outside AUTO_EXECUTABLE_STEPS — the
# pharmacist must act with their own credentials via POST /{rx_id}/transition.
AUTO_EXECUTABLE_STEPS = frozenset({"adjudication"})

# Maps an auto-executable step to the (from, to) RxStatus transition it performs.
# SIMULATED / PILOT PHASE — see AUTO_EXECUTABLE_STEPS docstring above.
STEP_TRANSITIONS: dict[str, tuple[RxStatus, RxStatus]] = {
    "adjudication": (RxStatus.PENDING_ADJUDICATION, RxStatus.READY_TO_FILL),
}

# Coarse, multi-leg COMPENSATING reversal path for each auto-executable step
# (Option B, per pharmacist's choice of "filtering/display logic" over deletion —
# this is the undo mechanism for that broader design).
#
# RxStateMachine.TRANSITIONS has NO direct READY_TO_FILL -> PENDING_ADJUDICATION
# (nor ON_HOLD -> PENDING_ADJUDICATION) adjacency — verified against the live
# adjacency dict. That is intentional: "skipping back" to adjudication without a
# fresh review would re-create exactly the risk an undo is meant to fix. Adding
# such a direct edge would itself be a clinical/compliance decision out of scope
# for this pilot.
#
# So "undo" here does NOT try to surgically rewind to the pre-auto-advance status.
# Instead it lands the Rx at PENDING_VERIFICATION — back in the pharmacist review
# queue — via the only fully adjacency-legal compensating route:
#     READY_TO_FILL -> ON_HOLD -> PENDING_VERIFICATION
# From there the normal verify -> adjudicate flow naturally re-derives a fresh
# (and this time pharmacist-reviewed) adjudication outcome. Each leg is its own
# independently-audited, hash-chained RxStateEvent — nothing is hidden.
#
# TODO(production): once compliance signs off on a documented "reversal" event
# type / adjacency, consider a more direct path; for the pilot, "back to full
# human review" is the safer and more defensible behavior.
UNDO_PATHS: dict[str, tuple[tuple[RxStatus, RxStatus], ...]] = {
    "adjudication": (
        (RxStatus.READY_TO_FILL, RxStatus.ON_HOLD),
        (RxStatus.ON_HOLD, RxStatus.PENDING_VERIFICATION),
    ),
}


@dataclass
class StepRecommendation:
    step:          str
    confidence:    float
    can_auto:      bool
    recommendation: str        # auto_clear | review | hold
    rationale:     str
    signals:       list[str] = field(default_factory=list)


# ─── Per-step local confidence estimators ─────────────────────────────────────

async def _dur_confidence(db: AsyncSession, rx: dict) -> StepRecommendation:
    """
    Confidence that the DUR step is safe to auto-clear: high when this exact
    drug+patient pairing has a clean history and no open critical alerts.
    """
    signals: list[str] = []
    conf = 0.5
    ndc = rx.get("ndc")
    patient_id = rx.get("patient_id")
    is_controlled = bool(rx.get("is_controlled", False))

    # How often this patient has safely received this drug before.
    try:
        res = await db.execute(text("""
            SELECT COUNT(*) AS n
            FROM   prescriptions
            WHERE  patient_id = :pid AND ndc = :ndc
              AND  status IN ('dispensed', 'picked_up')
        """), {"pid": patient_id, "ndc": ndc})
        prior = int(res.scalar() or 0)
    except Exception:  # noqa: BLE001
        prior = 0

    if prior >= 3:
        conf += 0.4; signals.append(f"stable_history_{prior}_fills")
    elif prior >= 1:
        conf += 0.2; signals.append(f"prior_fills_{prior}")

    # Open override / unresolved alert → cannot auto-clear.
    try:
        res = await db.execute(text("""
            SELECT COUNT(*) AS n FROM dur_override_events WHERE rx_id = :rx_id
        """), {"rx_id": rx.get("rx_id")})
        overrides = int(res.scalar() or 0)
    except Exception:  # noqa: BLE001
        overrides = 0
    if overrides > 0:
        conf = min(conf, 0.4); signals.append("has_override_documented")

    if is_controlled:
        conf = min(conf, 0.6); signals.append("controlled_substance_manual_review")

    conf = round(min(1.0, conf), 3)
    return StepRecommendation(
        step="dur", confidence=conf, can_auto=False,
        recommendation="", rationale="", signals=signals,
    )


async def _adjudication_confidence(db: AsyncSession, rx: dict) -> StepRecommendation:
    """
    LOCAL claim-clean probability: based on this drug+plan's historical rejection
    rate. (A trained classifier can replace this heuristic later.)
    """
    signals: list[str] = []
    ndc = rx.get("ndc")
    try:
        res = await db.execute(text("""
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN status ILIKE '%reject%'
                       OR (reject_codes IS NOT NULL AND reject_codes::text NOT IN ('[]','null',''))
                  THEN 1 ELSE 0 END) AS rejected
            FROM claim_transactions
            WHERE ndc = :ndc
              AND created_at >= now() - interval '180 days'
        """), {"ndc": ndc})
        row = res.mappings().first() or {}
        total = int(row.get("total") or 0)
        rejected = int(row.get("rejected") or 0)
    except Exception:  # noqa: BLE001
        total, rejected = 0, 0

    if total >= 5:
        clean_rate = 1.0 - (rejected / total)
        conf = round(0.5 + 0.5 * clean_rate, 3)
        signals.append(f"history_{total}_claims_{rejected}_rejected")
    else:
        conf = 0.6   # not enough history → moderate
        signals.append("sparse_claim_history")

    return StepRecommendation(
        step="adjudication", confidence=conf, can_auto=False,
        recommendation="", rationale="", signals=signals,
    )


async def _verification_confidence(db: AsyncSession, rx: dict) -> StepRecommendation:
    """
    LOCAL verification confidence from available signals (package-visual match if
    present on the Rx, prescriber deviation low). Conservative default.
    """
    signals: list[str] = []
    conf = 0.6
    # If a package verification result is stored on the Rx, use it.
    pv = rx.get("package_verification_score")
    if pv is not None:
        try:
            pv = float(pv)
            conf = round(0.4 + 0.6 * pv, 3)
            signals.append(f"package_match_{pv:.2f}")
        except (TypeError, ValueError):
            pass
    if rx.get("is_controlled"):
        conf = min(conf, 0.7); signals.append("controlled_manual_check")
    return StepRecommendation(
        step="verification", confidence=conf, can_auto=False,
        recommendation="", rationale="", signals=signals,
    )


def _finalize(step: StepRecommendation, threshold: float) -> StepRecommendation:
    step.can_auto = step.confidence >= threshold
    if step.can_auto:
        step.recommendation = "auto_clear"
        step.rationale = (f"Confidence {step.confidence:.0%} ≥ threshold {threshold:.0%} — "
                          "safe to auto-advance (pharmacist may undo).")
    elif step.confidence >= threshold - 0.2:
        step.recommendation = "review"
        step.rationale = f"Confidence {step.confidence:.0%} below auto threshold — quick review advised."
    else:
        step.recommendation = "hold"
        step.rationale = f"Confidence {step.confidence:.0%} low — pharmacist judgement required."
    return step


async def _load_rx(db: AsyncSession, rx_id: str) -> Optional[dict]:
    try:
        res = await db.execute(text("""
            SELECT id AS rx_id, rx_number, ndc, drug_name, patient_id, status,
                   COALESCE(is_controlled,false) AS is_controlled, dea_schedule
            FROM prescriptions WHERE id = :id
        """), {"id": rx_id})
        row = res.mappings().first()
        return dict(row) if row else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("[rx_copilot] rx load failed (%s)", exc)
        return None


def _coerce_uuid(value) -> Optional[UUID]:
    """
    Best-effort coercion of a staff identifier to a UUID for the state machine /
    audit calls. Accepts a UUID, a UUID string, or returns None for anything else
    (e.g. a legacy non-UUID placeholder) — callers should treat None as "identity
    unknown" rather than guessing.
    """
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except (ValueError, AttributeError, TypeError):
            return None
    return None


def _controlled_substance_alert(rx: dict) -> Optional[dict]:
    """
    Surface an explicit, impossible-to-miss alert whenever the Rx involves a
    controlled substance — regardless of which step is being evaluated/advanced
    and regardless of the automation outcome.

    Per pharmacist guidance: "any controlled substances should be noted and
    flagged for the pharmacist to observe and be alerted not to be neglected."
    This is intentionally surfaced on EVERY copilot interaction with a controlled
    Rx (evaluate, auto_advance, undo) — never silently folded into a confidence
    number a pharmacist might skim past.
    """
    if not rx.get("is_controlled"):
        return None
    schedule = rx.get("dea_schedule")
    label = f"Schedule {schedule} controlled substance" if schedule else "Controlled substance"
    return {
        "level": "warning",
        "code": "controlled_substance",
        "dea_schedule": schedule,
        "message": (
            f"{label} — requires direct pharmacist attention. "
            "Automation must not be allowed to defer or obscure this review."
        ),
    }


async def evaluate(
    db: AsyncSession,
    rx_id: str,
    *,
    thresholds: Optional[dict] = None,
    force_tier: Optional[Tier] = None,
) -> dict:
    """Compute per-step automation confidence for an Rx. §1.2 envelope."""
    tier = IntelligenceTier.resolve(SERVICE, force=force_tier)
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}

    rx = await _load_rx(db, rx_id)
    if not rx:
        return build_envelope(
            {"steps": [], "overall": "rx_not_found"},
            tier_used=Tier.LOCAL, confidence=0.0, degraded=True,
            options_active=[], options_offline=["all"],
            model_version="rx_copilot_v1",
        )

    dur   = _finalize(await _dur_confidence(db, rx),          th["dur"])
    adj   = _finalize(await _adjudication_confidence(db, rx), th["adjudication"])
    ver   = _finalize(await _verification_confidence(db, rx), th["verification"])
    steps = [dur, adj, ver]

    auto_count = sum(1 for s in steps if s.can_auto)
    overall = ("mostly_automatable" if auto_count >= 2
               else "partial" if auto_count == 1 else "manual")

    options_active  = ["local_step_confidence"]
    options_offline = ["cloud_second_opinion", "live_adjudication"] if tier == Tier.LOCAL else []
    degraded = (tier == Tier.LOCAL)
    if tier in (Tier.CLOUD, Tier.HYBRID):
        options_active.append("cloud_second_opinion")

    return build_envelope(
        {
            "rx_id": rx_id, "rx_number": rx.get("rx_number"),
            "drug_name": rx.get("drug_name"),
            "is_controlled": rx.get("is_controlled"),
            "steps": [s.__dict__ for s in steps],
            "thresholds": th,
            "auto_clearable_steps": auto_count,
            "overall": overall,
            "auto_executable_steps": sorted(AUTO_EXECUTABLE_STEPS),
            "controlled_substance_alert": _controlled_substance_alert(rx),
        },
        tier_used=tier, confidence=round(sum(s.confidence for s in steps) / 3, 3),
        degraded=degraded, options_active=options_active, options_offline=options_offline,
        model_version="rx_copilot_v1",
    )


async def auto_advance(
    db: AsyncSession,
    rx_id: str,
    step: str,
    *,
    staff_id,
    thresholds: Optional[dict] = None,
) -> dict:
    """
    PILOT PHASE — only `adjudication` is auto-EXECUTABLE (see AUTO_EXECUTABLE_STEPS
    docstring). `dur` and `verification` are computed and returned as MANUAL
    DECISION AIDS ONLY — this function will never transition the Rx for them; the
    pharmacist must act with their own credentials via POST /{rx_id}/transition.

    When it does execute (adjudication only), the resulting RxStateEvent is
    explicitly marked `triggered_by_type="ai"` with `metadata={"simulated": True,
    "pilot_phase": True, ...}` and a human-readable `reason` — so the immutable
    audit trail itself documents that this was a SIMULATED outcome pending a real
    external adjudication connector, not a live insurance decision.

    A `controlled_substance_alert` is always included in the response when the Rx
    involves a controlled substance, regardless of step or outcome.
    """
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    rx = await _load_rx(db, rx_id)
    if not rx:
        return {"ok": False, "reason": "rx_not_found"}

    estimator = {"dur": _dur_confidence, "adjudication": _adjudication_confidence,
                 "verification": _verification_confidence}.get(step)
    if not estimator:
        return {"ok": False, "reason": "unknown_step"}

    rec = _finalize(await estimator(db, rx), th.get(step, 0.95))
    alert = _controlled_substance_alert(rx)

    if step not in AUTO_EXECUTABLE_STEPS:
        # Manual-only step: surface the AI's read, execute nothing.
        return {
            "ok": False, "reason": "manual_step_decision_aid_only",
            "step": step, "confidence": rec.confidence,
            "recommendation": rec.recommendation, "rationale": rec.rationale,
            "controlled_substance_alert": alert,
        }

    if not rec.can_auto:
        return {"ok": False, "reason": "below_threshold", "confidence": rec.confidence,
                "controlled_substance_alert": alert}

    from_status, to_status = STEP_TRANSITIONS[step]
    try:
        current_status = RxStatus(rx["status"])
    except ValueError:
        current_status = None
    if current_status != from_status:
        return {
            "ok": False, "reason": "rx_not_in_expected_state",
            "expected_status": from_status.value, "actual_status": rx.get("status"),
            "controlled_substance_alert": alert,
        }

    staff_uuid = _coerce_uuid(staff_id)
    threshold = th.get(step, 0.95)

    sm = RxStateMachine(db)
    try:
        await sm.transition(
            prescription_id=rx["rx_id"],
            to_status=to_status,
            triggered_by_id=staff_uuid,
            triggered_by_type="ai",
            reason=(
                f"rx_copilot auto-advance ({step}): confidence {rec.confidence:.0%} "
                f"≥ threshold {threshold:.0%}. SIMULATED outcome — PILOT PHASE "
                f"(no live adjudication connector yet; pharmacist may undo)."
            ),
            metadata={
                "service": SERVICE, "step": step,
                "confidence": rec.confidence, "threshold": threshold,
                "signals": rec.signals,
                "simulated": True, "pilot_phase": True,
            },
        )
    except (InvalidTransitionError, EPCSRequiredError, ValueError) as exc:
        logger.warning("[rx_copilot] auto_advance transition refused for rx %s: %s", rx_id, exc)
        return {"ok": False, "reason": "transition_refused", "detail": str(exc),
                "controlled_substance_alert": alert}

    # Audit the auto-action in the SAME transaction as the transition, so the
    # state change and its rx_copilot_actions audit row commit atomically and
    # the action is guaranteed undoable from the moment it's visible.
    import uuid
    from datetime import datetime, timezone
    action_id = str(uuid.uuid4())
    await db.execute(text("""
        INSERT INTO rx_copilot_actions
            (id, rx_id, step, confidence, staff_id, created_at)
        VALUES (:id, :rx, :step, :conf, :staff, :now)
    """), {"id": action_id, "rx": rx_id, "step": step,
           "conf": rec.confidence,
           "staff": str(staff_uuid) if staff_uuid else (str(staff_id) if staff_id else None),
           "now": datetime.now(timezone.utc)})
    await db.commit()
    return {
        "ok": True, "step": step, "confidence": rec.confidence,
        "from_status": from_status.value, "to_status": to_status.value,
        "action_id": action_id, "audited": True, "reversible": True,
        "simulated": True, "pilot_phase": True,
        "controlled_substance_alert": alert,
    }


async def undo_auto_advance(
    db: AsyncSession,
    rx_id: str,
    action_id: str,
    *,
    staff_id,
) -> dict:
    """
    Pharmacist-initiated reversal of a previously-executed pilot-phase
    auto-advance (Option B — coarse compensating reversal; see UNDO_PATHS for
    why this is NOT a surgical rewind, and why that's the safer choice).

    Validates the originating rx_copilot_actions row (exists, belongs to this
    Rx, not already reverted), drives the Rx through each adjacency-legal leg of
    UNDO_PATHS[step] — EACH leg becomes its own independently-audited,
    hash-chained RxStateEvent (triggered_by_type="staff", since this is a human
    pharmacist decision to undo, not an AI action) — and finally marks the
    original action `reverted = TRUE` so it cannot be replayed.

    If a leg fails partway through, whatever legs DID succeed are committed
    (never silently lost — the audit trail must reflect reality) and the action
    is left NOT reverted, with `completed_legs` reported so a human can see
    exactly how far the reversal got.
    """
    row_res = await db.execute(text("""
        SELECT id, rx_id, step, reverted FROM rx_copilot_actions WHERE id = :id
    """), {"id": action_id})
    row = row_res.mappings().first()
    if not row:
        return {"ok": False, "reason": "action_not_found"}
    if row["rx_id"] != rx_id:
        return {"ok": False, "reason": "action_rx_mismatch"}
    if row["reverted"]:
        return {"ok": False, "reason": "already_reverted"}

    step = row["step"]
    legs = UNDO_PATHS.get(step)
    if not legs:
        return {"ok": False, "reason": "undo_not_supported_for_step", "step": step}

    rx = await _load_rx(db, rx_id)
    if not rx:
        return {"ok": False, "reason": "rx_not_found"}

    expected_start = legs[0][0]
    try:
        current_status = RxStatus(rx["status"])
    except ValueError:
        current_status = None
    if current_status != expected_start:
        return {
            "ok": False, "reason": "rx_not_in_expected_state",
            "expected_status": expected_start.value, "actual_status": rx.get("status"),
        }

    staff_uuid = _coerce_uuid(staff_id)
    sm = RxStateMachine(db)
    completed_legs: list[list[str]] = []
    try:
        for leg_from, leg_to in legs:
            await sm.transition(
                prescription_id=rx_id,
                to_status=leg_to,
                triggered_by_id=staff_uuid,
                triggered_by_type="staff",
                reason=(
                    f"rx_copilot undo ({step}): pharmacist-initiated reversal of "
                    f"simulated auto-advance action {action_id} — compensating "
                    f"leg {leg_from.value} → {leg_to.value} (PILOT PHASE; "
                    f"coarse reversal routes back through full review, by design)."
                ),
                metadata={
                    "service": SERVICE, "undo_of_action_id": action_id,
                    "step": step, "leg": [leg_from.value, leg_to.value],
                    "pilot_phase": True,
                },
            )
            completed_legs.append([leg_from.value, leg_to.value])
    except (InvalidTransitionError, EPCSRequiredError, ValueError) as exc:
        logger.error(
            "[rx_copilot] undo_auto_advance partially failed for action %s (rx %s) "
            "after legs %s: %s", action_id, rx_id, completed_legs, exc,
        )
        await db.commit()  # persist whatever legs DID succeed — never hide audit reality
        return {
            "ok": False, "reason": "undo_partially_failed", "detail": str(exc),
            "completed_legs": completed_legs,
        }

    await db.execute(text("""
        UPDATE rx_copilot_actions SET reverted = TRUE WHERE id = :id
    """), {"id": action_id})
    await db.commit()
    return {
        "ok": True, "action_id": action_id, "step": step,
        "legs": completed_legs, "reverted": True,
        "final_status": legs[-1][1].value,
    }
