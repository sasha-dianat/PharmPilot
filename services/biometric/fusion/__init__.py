"""Multi-modal biometric fusion — face, gait, iris, voice → one decision.

Design position, because it differs from the usual weighted-sum recipe:

**Exclusion, not dilution.** The standard approach gives every stream a weight
and sums. That silently lets a modality operating near chance drag a strong one,
and the damage is invisible in the fused number. Here a reading that fails its
quality floor, or that has no measured impostor calibration, is dropped with a
stated reason and contributes exactly zero. Published multimodal fusion gains
assume each modality is above its own floor; below it, fusion is not an
improvement.

**Grouping before combining.** Readings are grouped by the identity they name,
fused within a group, and the groups compared. Summing scores across modalities
without grouping hides disagreement: face saying A and iris saying B produces a
confident A under naive summation, and a review here.

**Combination in log-lambda space, not probability space.** The obvious
implementation fuses calibrated probabilities. It does not work here: at
identification operating points the probability saturates at exactly 1.0 while
the underlying scores still differ by many orders of magnitude, so two agreeing
modalities become indistinguishable from one. Fusion therefore works on
log(lambda) — the log expected number of gallery impostors scoring this high —
which stays finite and ordered arbitrarily far into the tail. Independent
evidence multiplies tail probabilities, so it adds in log space.

The independence assumption is real and load-bearing: two views of the same face
are ONE modality, not two, and presenting them as two would overstate confidence.

**Competing candidates reduce confidence, not just margin.** The winner's
confidence is a posterior across all candidate identities plus a null hypothesis
pinned at lambda = 1 (chance). And when admitted modalities name different
people at all, confidence is capped at CONFLICT_CEILING — conflicting biometric
evidence means the system does not know, whichever stream scored loudest.

The extractors for gait and iris are NOT implemented here. This module defines
the contract they feed. On present evidence gait will rarely clear its floor in
this deployment — see docs/design/IDENTIFICATION_PRECISION.md.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from uuid import UUID

from services.biometric.identity_resolution.thresholds import (
    ImpostorStats, confidence_from_similarity, log_expected_false_matches)

# Decision bands
AUTO = "auto"
REVIEW = "review"
NO_MATCH = "no_match"

# A fused decision needs corroboration: one modality is a proposal, never a
# decision. This mirrors the platform invariant that a biometric score alone
# cannot exceed IAL1.
MIN_MODALITIES_FOR_AUTO = 2
AUTO_CONFIDENCE = 0.99
AUTO_MIN_MARGIN = 0.10

# When admitted modalities name DIFFERENT people, the honest confidence is low
# whichever stream shouted loudest. Conflicting biometric evidence means the
# system does not know: a mis-enrolled template, a spoof, or a gallery error.
# Confidence is capped here rather than letting the strongest stream win.
CONFLICT_CEILING = 0.5


@dataclass(frozen=True)
class ModalityFloor:
    """Admission criteria for one modality.

    `reliability` is a prior on how much a *good* sample from this modality is
    worth relative to the others; `min_quality` is how good the sample has to be
    before it is allowed to speak at all.
    """

    min_quality: float
    reliability: float
    note: str = ""


# Floors are evidence-led, not aesthetic. Iris is the most accurate modality in
# the literature but needs the most cooperation; gait is the weakest and its
# behaviour under a full-body garment is unmeasured by anyone, so it must clear
# the highest quality bar before it is admitted at all.
FUSION_FLOORS: dict[str, ModalityFloor] = {
    "iris":  ModalityFloor(min_quality=0.55, reliability=1.00,
                           note="most accurate; requires close cooperative capture"),
    "face":  ModalityFloor(min_quality=0.35, reliability=0.85,
                           note="primary; degrades under mask/veil occlusion"),
    "voice": ModalityFloor(min_quality=0.45, reliability=0.60,
                           note="close-talk only; far-field EER ~15% is below any floor"),
    "gait":  ModalityFloor(min_quality=0.75, reliability=0.25,
                           note="CASIA-B CL rank-1 86.7% on 74 lab subjects; "
                                "no published evaluation under chador"),
}


@dataclass(frozen=True)
class ModalityReading:
    """One modality's opinion about one identity."""

    modality: str
    identity_id: UUID | None
    similarity: float                 # raw, modality-native
    quality: float                    # 0-1, measured sample quality
    stats: ImpostorStats | None       # None ⇒ uncalibrated ⇒ excluded
    gallery_size: int = 1


@dataclass(frozen=True)
class Contribution:
    modality: str
    similarity: float
    calibrated: float                 # P(no impostor scores this high); saturates
    quality: float
    weight: float
    log_lambda: float = 0.0           # the non-saturating view, used for fusion


@dataclass(frozen=True)
class Exclusion:
    modality: str
    reason: str


@dataclass
class FusedIdentity:
    identity_id: UUID | None
    confidence: float                 # unified, 0-1
    decision: str                     # auto | review | no_match
    margin: float                     # to the next distinct identity
    contributions: list[Contribution] = field(default_factory=list)
    excluded: list[Exclusion] = field(default_factory=list)
    runner_up_id: UUID | None = None
    explanation: str = ""


def _admit(r: ModalityReading) -> Exclusion | None:
    """Why this reading may not speak, or None if it may."""
    floor = FUSION_FLOORS.get(r.modality)
    if floor is None:
        return Exclusion(r.modality, f"unknown modality '{r.modality}' — no declared floor")
    if r.identity_id is None:
        return Exclusion(r.modality, "reading names no identity")
    if r.stats is None:
        return Exclusion(
            r.modality,
            "no impostor calibration for this modality, so its score cannot be "
            "converted to a probability — excluded rather than guessed")
    if r.quality < floor.min_quality:
        return Exclusion(
            r.modality,
            f"sample quality {r.quality:.2f} is below the {floor.min_quality:.2f} "
            f"floor for {r.modality} — excluded rather than down-weighted, so a "
            f"stream near chance cannot drag a strong one")
    return None


def fuse(readings: list[ModalityReading]) -> FusedIdentity:
    """Combine modality readings into one identity decision."""
    admitted: list[ModalityReading] = []
    excluded: list[Exclusion] = []
    for r in readings:
        why = _admit(r)
        (excluded.append(why) if why else admitted.append(r))

    if not admitted:
        return FusedIdentity(
            identity_id=None, confidence=0.0, decision=NO_MATCH, margin=0.0,
            excluded=excluded,
            explanation=("No modality cleared its quality floor or had a "
                         "calibration; nothing to fuse."))

    # Group by the identity each reading names, so disagreement is visible
    # rather than averaged away.
    groups: dict[UUID, list[ModalityReading]] = {}
    for r in admitted:
        assert r.identity_id is not None      # guaranteed by _admit
        groups.setdefault(r.identity_id, []).append(r)

    # Score each identity in log-lambda space. lambda is the expected number of
    # gallery impostors scoring this high; log-space is required because the
    # probability form saturates at exactly 1.0 long before two genuinely
    # different scores stop differing.
    scored: list[tuple[UUID, float, list[Contribution]]] = []
    for identity, group in groups.items():
        log_lambda = 0.0
        contribs: list[Contribution] = []
        for r in group:
            assert r.stats is not None and r.identity_id is not None
            floor = FUSION_FLOORS[r.modality]
            weight = r.quality * floor.reliability
            # Independent evidence multiplies tail probabilities, i.e. adds in
            # log space. Weight scales how much each stream is allowed to say.
            log_lambda += weight * log_expected_false_matches(
                r.stats, r.similarity, max(1, r.gallery_size))
            contribs.append(Contribution(
                modality=r.modality, similarity=r.similarity,
                calibrated=confidence_from_similarity(
                    r.stats, r.similarity, max(1, r.gallery_size)),
                quality=r.quality, weight=weight,
                log_lambda=log_expected_false_matches(
                    r.stats, r.similarity, max(1, r.gallery_size))))
        contribs.sort(key=lambda c: -c.weight)
        scored.append((identity, log_lambda, contribs))

    # Strongest evidence = most negative log-lambda.
    scored.sort(key=lambda t: t[1])
    best_id, best_log_lambda, best_contribs = scored[0]
    runner = scored[1] if len(scored) > 1 else None

    # Posterior across candidate identities plus a null hypothesis pinned at
    # lambda = 1 (one expected false match — i.e. chance). This is what makes a
    # competing candidate reduce the winner's confidence rather than only its
    # margin.
    strengths = [-t[1] for t in scored]          # higher = stronger
    null_strength = 0.0
    m = max(strengths + [null_strength])
    denom = math.exp(null_strength - m) + sum(math.exp(x - m) for x in strengths)
    best_conf = math.exp(strengths[0] - m) / denom

    margin = (best_conf - math.exp(strengths[1] - m) / denom) if runner else 1.0

    n_mod = len({c.modality for c in best_contribs})
    disagreement = len(scored) > 1

    if disagreement:
        # Conflicting evidence caps confidence regardless of relative strength.
        best_conf = min(best_conf, CONFLICT_CEILING)

    if (not disagreement and best_conf >= AUTO_CONFIDENCE
            and n_mod >= MIN_MODALITIES_FOR_AUTO and margin >= AUTO_MIN_MARGIN):
        decision = AUTO
        why = (f"{n_mod} modalities agree on this identity "
               f"({', '.join(sorted(c.modality for c in best_contribs))}); "
               f"fused log-lambda {best_log_lambda:.1f} means far fewer than one "
               f"enrolled person would score this high across all of them by "
               f"chance.")
    else:
        decision = REVIEW
        reasons = []
        if disagreement:
            reasons.append(
                f"modalities disagree — they name {len(scored)} different "
                f"identities, so the fused confidence is capped at "
                f"{CONFLICT_CEILING}")
        if n_mod < MIN_MODALITIES_FOR_AUTO:
            reasons.append(
                f"only one modality ({best_contribs[0].modality}) contributed — a "
                f"single biometric proposes a candidate, it does not establish "
                f"identity")
        if best_conf < AUTO_CONFIDENCE and not disagreement:
            reasons.append(
                f"fused confidence {best_conf:.4f} is below the "
                f"{AUTO_CONFIDENCE} auto-accept bar")
        why = "Referred for review — " + "; ".join(reasons) + "."

    if excluded:
        why += (" Excluded: "
                + "; ".join(f"{e.modality} ({e.reason})" for e in excluded) + ".")

    return FusedIdentity(
        identity_id=best_id, confidence=best_conf, decision=decision,
        margin=margin, contributions=best_contribs, excluded=excluded,
        runner_up_id=runner[0] if runner else None, explanation=why)
