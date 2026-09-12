"""Turn a fused identity and/or an RF fix into one durable observation row.

`surveillance_observations` records what a sensor REPORTED at a moment, with
the confidence and the reason behind it. It never asserts who someone is —
promotion to an identity decision happens elsewhere and needs corroboration this
table cannot supply.

Two things are enforced here rather than trusted to callers:

THE SCHEMA'S CHECK CONSTRAINTS ARE MIRRORED, so a caller gets a clear Python
error instead of an IntegrityError surfacing from the driver three frames away.

A HINT IS NEVER STORED AS THE IDENTITY. When the decision is `identify_manually`
or `no_match`, any candidate the fusion engine surfaced is a hint — it exists to
be compared against an answer the staff member obtains independently. Writing it
into `biometric_identity_id` would promote a guess into the record and let a
later query treat it as an identification. It travels in `fusion_detail`
instead, clearly labelled.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

VALID_SITES = ("pharmacy", "depot")
VALID_DECISIONS = ("auto", "review", "no_match", "identify_manually")

# Decisions whose candidate is a hint rather than an identification. Storing the
# candidate for these would promote a guess into the observation record.
HINT_ONLY_DECISIONS = ("no_match", "identify_manually")


def build_observation(*, pharmacy_id: UUID, site: str, action_type: str,
                      observed_at: datetime, fused=None, fix=None,
                      zone_id: str | None = None, camera_id: str | None = None,
                      rf_device_ref: str | None = None, stratum=None,
                      source: str = "edge",
                      known_zones: frozenset[str] | None = None) -> dict:
    """Kwargs for `SurveillanceObservation`. Validates the schema's constraints.

    `known_zones` is the set of registered zone codes for this pharmacy and
    site, from `services.core.vision.zones.load_zone_codes`. Passing None means
    the caller could not look the registry up; the zone then travels
    unvalidated and migration 0052's foreign key has the final say. Refusing
    every observation in that case would turn a registry outage into a capture
    outage, which is the worse failure.
    """
    if site not in VALID_SITES:
        raise ValueError(f"site must be one of {VALID_SITES}, got {site!r}")

    if zone_id is not None:
        # '' is not "unzoned". It would slip past any membership test guarded
        # on truthiness and then defeat every `zone_id IS NULL` branch
        # downstream, so it is refused regardless of whether zones are known.
        if zone_id != zone_id.strip() or not zone_id:
            raise ValueError(
                f"zone_id must be non-empty and unpadded, got {zone_id!r}")
        if known_zones is not None and zone_id not in known_zones:
            raise ValueError(
                f"zone {zone_id!r} is not registered for this pharmacy and "
                f"site; register it in vision_zone before sending observations")

    row: dict = {
        "pharmacy_id": pharmacy_id,
        "observed_at": observed_at,
        "site": site,
        "zone_id": zone_id,
        "action_type": action_type,
        "camera_id": camera_id,
        "source": source,
        "biometric_identity_id": None,
        "fusion_decision": None,
        "fusion_confidence": None,
        "fusion_margin": None,
        "fusion_detail": None,
        "modalities_used": None,
        "fusion_explanation": None,
        "rf_device_ref": rf_device_ref,
        "rf_x": None, "rf_y": None, "rf_uncertainty_m": None,
        "rf_method": None, "rf_ap_count": None,
    }

    if fused is not None:
        if fused.decision not in VALID_DECISIONS:
            raise ValueError(
                f"fusion decision must be one of {VALID_DECISIONS}, "
                f"got {fused.decision!r}")

        is_hint = fused.decision in HINT_ONLY_DECISIONS
        row.update({
            # A hint never becomes the observation's identity — see the module
            # docstring. It is preserved below, labelled as what it is.
            "biometric_identity_id": None if is_hint else fused.identity_id,
            "fusion_decision": fused.decision,
            "fusion_confidence": round(float(fused.confidence), 5),
            "fusion_margin": round(float(fused.margin), 5),
            "modalities_used": [c.modality for c in fused.contributions],
            "fusion_explanation": fused.explanation,
            # The working travels with the row. An observation that cannot say
            # what it discarded, and why, is not reviewable after the fact.
            "fusion_detail": {
                "occlusion_stratum": (stratum.value if stratum is not None
                                      else None),
                "hint_identity_id": (str(fused.identity_id)
                                     if is_hint and fused.identity_id else None),
                "runner_up_id": (str(fused.runner_up_id)
                                 if fused.runner_up_id else None),
                "contributions": [
                    {"modality": c.modality, "similarity": c.similarity,
                     "calibrated": c.calibrated, "quality": c.quality,
                     "weight": c.weight, "log_lambda": c.log_lambda}
                    for c in fused.contributions],
                "excluded": [{"modality": e.modality, "reason": e.reason}
                             for e in fused.excluded],
            },
        })

    if fix is not None:
        if not fix.uncertainty_m or fix.uncertainty_m <= 0:
            raise ValueError(
                "an RF coordinate requires a positive uncertainty_m; a "
                "coordinate without its error bar invites false precision")
        row.update({
            "rf_x": round(float(fix.x), 2),
            "rf_y": round(float(fix.y), 2),
            "rf_uncertainty_m": round(float(fix.uncertainty_m), 2),
            "rf_method": fix.method,
            "rf_ap_count": fix.ap_count,
        })

    return row


async def record(db, **kwargs) -> UUID:
    """Persist one observation. Returns its id."""
    from shared.models.surveillance_log import SurveillanceObservation

    obs = SurveillanceObservation(**build_observation(**kwargs))
    db.add(obs)
    await db.flush()
    return obs.id
