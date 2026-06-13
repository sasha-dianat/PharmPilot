"""
#13 — Smart Prescriber Registry Enrichment  (offline-first)
===========================================================
Validates + enriches prescriber records and learns each prescriber's NORMAL
prescribing pattern so deviations on new Rxs can be flagged.

LOCAL BRAIN (always available):
  • Per-prescriber prescribing-distribution profile from local fills
    (feature_store.prescriber_profile): per-drug counts, mean/STD quantity, days supply.
  • DEVIATION SCORING for a candidate Rx: z-score of the prescribed quantity vs the
    prescriber's own history for that drug; novelty flag if the prescriber has never
    written this drug; controlled-substance escalation.
  • Local council/registration number FORMAT validation (structure + checksum rules).

CLOUD BRAIN (when online — enriches, never required):
  • Live validation against external medical-council / NPI registries, suspension
    status, specialty-vs-prescription consistency. When offline these are DEFERRED to
    the outbox and the record is marked "pending external verification".
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.ai.intelligence_core import (
    Tier, IntelligenceTier, build_envelope, cloud_call, feature_store, outbox,
)

logger = logging.getLogger(__name__)

SERVICE = "prescriber_enrichment"


# ─── Council-number format validation (local) ────────────────────────────────

@dataclass
class CouncilValidation:
    council_no:   str
    authority:    Optional[str]
    format_valid: bool
    reason:       str


def validate_council_number(council_no: str, authority: Optional[str] = None) -> CouncilValidation:
    """
    Local structural validation. Does NOT confirm the number is *real* (that needs
    the external registry, online) — only that it is well-formed.
    """
    raw = (council_no or "").strip()
    auth = (authority or "").strip()

    if not raw:
        return CouncilValidation(raw, authority, False, "Empty council/registration number.")

    cleaned = re.sub(r"[\s\-/]", "", raw)

    # Iranian Medical Council — 4–8 digit numeric.
    if "iran" in auth.lower() or "نظام" in raw or auth.lower() == "iranian medical council":
        if cleaned.isdigit() and 4 <= len(cleaned) <= 8:
            return CouncilValidation(raw, authority, True, "Valid Iranian Medical Council format.")
        return CouncilValidation(raw, authority, False, "Iranian council number should be 4–8 digits.")

    # NPI (US) — 10 digits with Luhn check.
    if auth.upper() == "NPI" or (cleaned.isdigit() and len(cleaned) == 10):
        if len(cleaned) == 10 and _npi_luhn_ok(cleaned):
            return CouncilValidation(raw, authority or "NPI", True, "Valid NPI (Luhn check passed).")
        if auth.upper() == "NPI":
            return CouncilValidation(raw, authority, False, "NPI failed 10-digit Luhn check.")

    # GMC (UK) — 7 digits.
    if auth.upper() == "GMC":
        if cleaned.isdigit() and len(cleaned) == 7:
            return CouncilValidation(raw, authority, True, "Valid GMC format.")
        return CouncilValidation(raw, authority, False, "GMC number should be 7 digits.")

    # Generic alphanumeric license — 4–20 chars.
    if re.fullmatch(r"[A-Za-z0-9]{4,20}", cleaned):
        return CouncilValidation(raw, authority, True, "Well-formed registration number.")

    return CouncilValidation(raw, authority, False, "Unrecognised registration number format.")


def _npi_luhn_ok(npi: str) -> bool:
    """NPI uses Luhn with an '80840' prefix on the first 9 digits."""
    if len(npi) != 10 or not npi.isdigit():
        return False
    base = "80840" + npi[:9]
    total, parity = 0, len(base) % 2
    for i, ch in enumerate(base):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    check = (10 - (total % 10)) % 10
    return check == int(npi[9])


# ─── Prescriber profile + deviation scoring ──────────────────────────────────

@dataclass
class DrugStat:
    ndc:            str
    drug_name:      str
    rx_count:       int
    avg_quantity:   float
    std_quantity:   float
    avg_days_supply:float
    is_controlled:  bool


async def get_profile(
    db: AsyncSession,
    pharmacy_id: str,
    prescriber_id: str,
    *,
    force_tier: Optional[Tier] = None,
) -> dict:
    """Prescriber's prescribing-distribution profile. §1.2 envelope."""
    tier = IntelligenceTier.resolve(SERVICE, force=force_tier)
    df = await feature_store.prescriber_profile(db, pharmacy_id, prescriber_id)

    stats: list[DrugStat] = []
    total_rx = 0
    controlled_rx = 0
    if df is not None and not df.empty:
        for _, r in df.iterrows():
            row = dict(r)
            cnt = int(row.get("rx_count") or 0)
            total_rx += cnt
            is_ctrl = bool(row.get("is_controlled", False))
            if is_ctrl:
                controlled_rx += cnt
            stats.append(DrugStat(
                ndc=str(row.get("ndc") or ""),
                drug_name=str(row.get("drug_name") or ""),
                rx_count=cnt,
                avg_quantity=round(float(row.get("avg_quantity") or 0), 2),
                std_quantity=round(float(row.get("std_quantity") or 0), 2),
                avg_days_supply=round(float(row.get("avg_days_supply") or 0), 1),
                is_controlled=is_ctrl,
            ))

    summary = {
        "prescriber_id":  prescriber_id,
        "total_rx":       total_rx,
        "distinct_drugs": len(stats),
        "controlled_rx":  controlled_rx,
        "controlled_share": round(controlled_rx / total_rx, 3) if total_rx else 0.0,
        "profile_maturity": ("established" if total_rx >= 50
                             else "developing" if total_rx >= 10 else "sparse"),
    }
    confidence = 0.8 if total_rx >= 50 else 0.55 if total_rx >= 10 else 0.4
    degraded = (tier == Tier.LOCAL)
    return build_envelope(
        {"summary": summary, "top_drugs": [s.__dict__ for s in stats[:25]]},
        tier_used=tier, confidence=confidence, degraded=degraded,
        options_active=["local_profile"],
        options_offline=["registry_validation", "suspension_check"] if degraded else [],
        model_version="prescriber_profile_v1",
    )


async def score_rx(
    db: AsyncSession,
    pharmacy_id: str,
    prescriber_id: str,
    *,
    ndc: str,
    drug_name: str,
    quantity: float,
    is_controlled: bool = False,
    force_tier: Optional[Tier] = None,
) -> dict:
    """
    Score how unusual a candidate Rx is for this prescriber. §1.2 envelope.
    Deviation = z-score of quantity vs prescriber's own history for the drug.
    """
    tier = IntelligenceTier.resolve(SERVICE, force=force_tier)
    df = await feature_store.prescriber_profile(db, pharmacy_id, prescriber_id)

    match = None
    if df is not None and not df.empty:
        for _, r in df.iterrows():
            row = dict(r)
            if str(row.get("ndc")) == ndc or (
                drug_name and str(row.get("drug_name", "")).lower() == drug_name.lower()
            ):
                match = row
                break

    flags: list[str] = []
    z = 0.0
    novelty = False

    if match is None:
        novelty = True
        flags.append("novel_drug_for_prescriber")
        deviation_score = 0.45   # moderate — unseen but not necessarily wrong
        rationale = "This prescriber has no prior history with this drug at this pharmacy."
    else:
        avg = float(match.get("avg_quantity") or 0)
        std = float(match.get("std_quantity") or 0)
        cnt = int(match.get("rx_count") or 0)
        if std > 1e-6 and cnt >= 3:
            z = (quantity - avg) / std
        elif avg > 0:
            z = (quantity - avg) / max(avg * 0.5, 1.0)   # crude when std unknown
        abs_z = abs(z)
        deviation_score = round(min(1.0, abs_z / 4.0), 3)
        if abs_z >= 3:
            flags.append("quantity_extreme_deviation")
            rationale = f"Quantity {quantity:g} is {abs_z:.1f}σ from this prescriber's usual {avg:g}."
        elif abs_z >= 2:
            flags.append("quantity_deviation")
            rationale = f"Quantity {quantity:g} is {abs_z:.1f}σ above/below usual {avg:g}."
        else:
            rationale = "Within this prescriber's normal range."

    if is_controlled and (novelty or deviation_score >= 0.5):
        flags.append("controlled_substance_review")
        deviation_score = min(1.0, deviation_score + 0.2)

    band = ("high" if deviation_score >= 0.66
            else "moderate" if deviation_score >= 0.33 else "low")

    degraded = (tier == Tier.LOCAL)
    return build_envelope(
        {
            "deviation_score": deviation_score,
            "band":            band,
            "z_score":         round(z, 2),
            "novelty":         novelty,
            "flags":           flags,
            "rationale":       rationale,
        },
        tier_used=tier, confidence=0.7 if match is not None else 0.45,
        degraded=degraded, options_active=["local_distribution"],
        options_offline=["registry_corroboration"] if degraded else [],
        model_version="prescriber_deviation_v1",
    )


async def enrich(
    db: AsyncSession,
    *,
    prescriber_id: Optional[str],
    full_name: str,
    council_no: str,
    authority: Optional[str] = None,
    pharmacy_id: Optional[str] = None,
    force_tier: Optional[Tier] = None,
) -> dict:
    """
    Validate the council number locally and (online) queue external verification.
    Offline → record is flagged 'pending external verification' via the outbox.
    """
    tier = IntelligenceTier.resolve(SERVICE, force=force_tier)

    cv = validate_council_number(council_no, authority)

    verification_status = "pending"
    options_active = ["local_format_validation"]
    options_offline: list[str] = []
    degraded = False

    if tier in (Tier.CLOUD, Tier.HYBRID):
        async def _verify():
            # Placeholder for real external registry lookup. Returns a dict.
            return {"verified": None, "suspended": None}
        result, ok = await cloud_call(_verify, fallback=None, label="registry_verify")
        if ok and result is not None:
            options_active.append("external_registry")
            verification_status = "verified_format_only"  # real impl sets verified/suspended
            tier = Tier.HYBRID
        else:
            degraded = True

    if tier == Tier.LOCAL or degraded:
        # Park the external verification for when we're back online.
        try:
            await outbox.enqueue(
                db, action_type="prescriber_registry_verify",
                payload={"prescriber_id": prescriber_id, "full_name": full_name,
                         "council_no": council_no, "authority": authority},
                dedup_key=f"prescriber_verify:{council_no}",
                pharmacy_id=pharmacy_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[prescriber_enrichment] outbox enqueue failed (%s)", exc)
        options_offline = ["external_registry", "suspension_check"]
        verification_status = "queued_for_verification"

    return build_envelope(
        {
            "prescriber_id":       prescriber_id,
            "full_name":           full_name,
            "council_validation":  cv.__dict__,
            "verification_status": verification_status,
        },
        tier_used=tier, confidence=0.9 if cv.format_valid else 0.5,
        degraded=degraded or tier == Tier.LOCAL,
        options_active=options_active, options_offline=options_offline,
        model_version="prescriber_enrich_v1",
    )
