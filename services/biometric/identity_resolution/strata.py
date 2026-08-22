"""Calibration per occlusion stratum.

A single pooled ImpostorStats averages veiled faces in with unoccluded ones. The
resulting threshold is too LOW for the veiled subset, so the FPIR guarantee
silently fails for exactly the group it most affects — and in this deployment
that group is the majority of female customers.

THE HONEST FAILURE MATTERS MOST HERE. A stratum with too few enrolled pairs is
reported as NOT usable, and `to_impostor_stats` returns None for it. It does not
borrow the pooled figure. Borrowing is how a threshold measured on unoccluded
faces ends up applied to chador captures, which is the exact defect stratified
calibration exists to prevent.
"""
from __future__ import annotations

from . import repository as R
from . import vector_store as V

# Templates enrolled before the stratum was recorded. Still usable as the
# pooled calibration; simply not stratified.
POOLED = "all"


def _stratum_of(context) -> str:
    if not isinstance(context, dict):
        return POOLED
    return str(context.get("occlusion_stratum") or POOLED)


def split_by_stratum(index: V.ModalityIndex) -> dict[str, V.ModalityIndex]:
    """One sub-index per stratum present in the gallery."""
    n = len(index)
    # Populated by ModalityIndex.load. Defensive default so an index built by
    # older code degrades to the pooled bucket rather than raising.
    contexts = getattr(index, "_contexts", None) or [{}] * n

    buckets: dict[str, list[int]] = {}
    for i in range(n):
        ctx = contexts[i] if i < len(contexts) else {}
        buckets.setdefault(_stratum_of(ctx), []).append(i)

    out: dict[str, V.ModalityIndex] = {}
    for stratum, rows in buckets.items():
        sub = V.ModalityIndex(modality=index.modality, dim=index.dim)
        sub._vectors = index._vectors[rows]
        sub._identities = [index._identities[i] for i in rows]
        sub._templates = [index._templates[i] for i in rows]
        sub._quality = [index._quality[i] for i in rows]
        sub._contexts = [contexts[i] if i < len(contexts) else {} for i in rows]
        out[stratum] = sub
    return out


def measure_all_strata(index: V.ModalityIndex,
                       min_pairs: int = 1000) -> dict[str, R.Calibration]:
    """Measure each stratum independently. No stratum inherits another's."""
    return {s: R.measure_impostor_stats(sub, min_pairs=min_pairs)
            for s, sub in split_by_stratum(index).items()}


def to_impostor_stats(cal: R.Calibration, stratum: str,
                      sample_floor: int = 1000):
    """`ImpostorStats` for a usable calibration, or None.

    None is the correct answer for an unusable stratum: the fusion engine
    excludes an uncalibrated stream, and returning a placeholder would licence a
    vote on a distribution nobody measured.
    """
    from .thresholds import ImpostorStats

    if not cal.usable or cal.pairs < sample_floor or cal.impostor_std <= 0:
        return None
    try:
        return ImpostorStats(
            mean=float(cal.impostor_mean), std=float(cal.impostor_std),
            sample_size=int(cal.pairs), model=cal.modality,
            population=stratum)
    except ValueError:
        # The sample-size floor inside ImpostorStats is authoritative; a
        # calibration that fails it must not become a licence to vote.
        return None
