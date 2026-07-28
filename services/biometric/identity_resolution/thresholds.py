"""Where an identification threshold actually comes from.

The constants this replaces (`MATCH_THRESHOLD_HIGH = 0.95`, `PROBABLE = 0.80`,
`POSSIBLE = 0.50`) read like probabilities but were compared against raw cosine
similarity between L2-normalised ArcFace embeddings. Those are different
quantities on different scales, and the confusion propagated: the patient
resolver went on to treat the cosine as `biometric_confidence >= 0.80`, i.e. as
80% certainty.

Two facts drive everything here.

**A verification threshold is not an identification threshold.** Comparing one
probe against one enrolled template at false-match rate `f` is a different risk
from comparing it against N of them. With N roughly independent comparisons,

    FPIR ≈ 1 − (1 − f)^N ≈ N · f

so a per-comparison `f` of 1e-5 — a respectable verification operating point —
yields ≈0.2 false identifications per query against a 20,000-person gallery.
The threshold must therefore be a function of gallery size, which is what
`threshold_for_gallery` is.

**The impostor distribution must be measured, not assumed.** Converting a
target `f` into a cosine threshold needs the distribution of impostor scores for
*this* model on *this* population and *these* cameras. Published figures do not
transfer: veiling, masks, and desk-mounted capture all shift it. So
`ImpostorStats` is a required input, and the engine refuses to auto-accept
without one. That is deliberate — it makes the original mistake unrepeatable,
because there is no longer a default number to accidentally trust.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import NormalDist

# Ceiling on the z-score we will ask for. Beyond ~8σ the Gaussian tail model is
# pure extrapolation and the honest answer is "your gallery is too large for
# single-factor face identification at this FPIR", not a bigger number.
_MAX_Z = 8.0

_NORM = NormalDist()


@dataclass(frozen=True)
class ImpostorStats:
    """Measured distribution of similarity between DIFFERENT people.

    Produced by scoring a large sample of known-mismatched pairs drawn from the
    real gallery and the real capture points — not from a public benchmark.
    """

    mean: float
    std: float
    sample_size: int
    measured_at: str | None = None
    model: str | None = None
    population: str | None = None      # e.g. "counter-1, hijab subset"

    def __post_init__(self) -> None:
        if self.std <= 0:
            raise ValueError("impostor std must be > 0")
        if self.sample_size < 1000:
            # Below this the tail estimate is noise, and the tail is the whole
            # point — we are operating at f ~ 1e-7.
            raise ValueError(
                f"impostor sample_size={self.sample_size} is too small to "
                "estimate a tail; need >= 1000 mismatched pairs")
        if not -1.0 <= self.mean <= 1.0:
            raise ValueError("impostor mean must be a cosine in [-1, 1]")


def required_fmr(gallery_size: int, target_fpir: float) -> float:
    """Per-comparison false-match rate needed to hold FPIR over N comparisons."""
    if target_fpir <= 0 or target_fpir >= 1:
        raise ValueError("target_fpir must be in (0, 1)")
    return target_fpir / max(1, gallery_size)


def threshold_for_gallery(stats: ImpostorStats, gallery_size: int,
                          target_fpir: float = 1e-3) -> float:
    """Cosine threshold holding system-level FPIR over a gallery of `gallery_size`.

    τ = μ + σ · z(1 − f) where f = target_fpir / N.
    """
    f = required_fmr(gallery_size, target_fpir)
    z = min(_MAX_Z, _NORM.inv_cdf(1.0 - f))
    return stats.mean + stats.std * z


def expected_false_matches(stats: ImpostorStats, similarity: float,
                           gallery_size: int) -> float:
    """λ — how many impostors in this gallery are expected to score >= `similarity`.

    This is the number that makes a match interpretable to a human reviewer:
    "about 0.001 of the enrolled population would score this high by chance".
    """
    z = (similarity - stats.mean) / stats.std
    tail = 1.0 - _NORM.cdf(z)
    return max(0.0, gallery_size * tail)


def confidence_from_similarity(stats: ImpostorStats, similarity: float,
                               gallery_size: int) -> float:
    """P(no impostor in the gallery would score this high), via a Poisson tail.

    Distinct from `similarity` on purpose, and reported alongside it rather than
    instead of it. At the auto-accept threshold this evaluates to
    1 − target_fpir, so the two views stay consistent.
    """
    lam = expected_false_matches(stats, similarity, gallery_size)
    if lam > 700:                      # exp underflow guard
        return 0.0
    import math
    return float(math.exp(-lam))


def calibrate(impostor_scores) -> ImpostorStats:
    """Fit `ImpostorStats` from measured mismatched-pair similarities."""
    import statistics as st
    scores = [float(s) for s in impostor_scores]
    if len(scores) < 1000:
        raise ValueError(
            f"need >= 1000 mismatched pairs to calibrate, got {len(scores)}")
    return ImpostorStats(mean=st.fmean(scores), std=st.stdev(scores),
                         sample_size=len(scores))
