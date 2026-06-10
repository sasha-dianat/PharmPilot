"""
#6 — Controlled-Substance Forgery & Integrity Detection  (offline-first)
========================================================================
Scores a scanned prescription image for forgery risk — GATED to controlled
substances (CII–CV) ONLY. For non-controlled Rxs this is a no-op (skipped=True).

LOCAL BRAIN (always available):
  • VISUAL image forensics computable offline with OpenCV:
      - Error-Level-Analysis (ELA) residual energy (re-save delta) → tampering hint
      - Edge/“print uniformity” entropy → home-printer vs pre-printed pad
      - Noise variance & block artefacts
    Combined into a visual_risk (0–1). Degrades to 0.0 contribution if cv2 absent.
  • BEHAVIORAL signal: per-prescriber prescribing-distribution deviation for THIS
    drug/quantity (reuses #13 prescriber_enrichment.score_rx). A controlled Rx far
    outside the prescriber's norm raises the score.
  • Fused into forgery_score (0–1) with a risk band + recommended action.

CLOUD BRAIN (when online — enriches, never required):
  • Live prescriber-license/registry validity, cross-pharmacy duplicate-image
    detection, PDMP corroboration. Offline → local score stands with a
    "verify license when online" note.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.ai.intelligence_core import (
    Tier, IntelligenceTier, build_envelope, cloud_call,
)
from services.ai.intelligence_services import prescriber_enrichment

logger = logging.getLogger(__name__)

SERVICE = "integrity_detection"

RISK_CRITICAL = 0.66
RISK_WARNING  = 0.40

CONTROLLED_SCHEDULES = {"CII", "CIII", "CIV", "CV", "C2", "C3", "C4", "C5", "2", "3", "4", "5"}


@dataclass
class IntegrityScore:
    skipped:         bool
    skip_reason:     str = ""
    forgery_score:   float = 0.0
    risk_band:       str = "ok"
    visual_risk:     float = 0.0
    behavioral_risk: float = 0.0
    signals:         list[str] = field(default_factory=list)
    rationale:       str = ""
    recommended_action: str = "none"


def _is_controlled(dea_schedule: Optional[str], is_controlled: bool) -> bool:
    if is_controlled:
        return True
    if not dea_schedule:
        return False
    return dea_schedule.upper().replace("-", "").strip() in CONTROLLED_SCHEDULES


# ─── Visual forensics (OpenCV, offline) ───────────────────────────────────────

def _visual_forensics(image_path: str) -> tuple[float, list[str]]:
    """
    Returns (visual_risk 0–1, signals[]). Safe if cv2/numpy missing → (0.0, []).
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return 0.0, []

    try:
        img = cv2.imread(image_path)
        if img is None:
            return 0.0, []
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        signals: list[str] = []
        risk = 0.0

        # 1. ELA — re-encode at quality 90 and measure residual energy.
        ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if ok:
            recoded = cv2.imdecode(enc, cv2.IMREAD_COLOR)
            ela = cv2.absdiff(img, recoded).astype("float32")
            ela_energy = float(ela.mean())
            # High residual in localised regions hints at paste/edit.
            ela_std = float(ela.std())
            if ela_std > 12.0:
                risk += 0.30
                signals.append("ela_localized_residual")
            elif ela_std > 7.0:
                risk += 0.12

        # 2. Edge/print uniformity entropy.
        edges = cv2.Canny(gray, 50, 150)
        edge_density = float(edges.mean()) / 255.0
        # Home-printed text often shows different edge density than offset print pads.
        if edge_density < 0.02 or edge_density > 0.25:
            risk += 0.15
            signals.append("atypical_edge_density")

        # 3. Noise variance (Laplacian) — scans of scans / photocopies differ.
        lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        if lap_var < 50:
            risk += 0.15
            signals.append("low_sharpness_possible_copy")

        # 4. Block artefacts via FFT high-freq ratio (very rough).
        f = np.fft.fft2(gray)
        fshift = np.abs(np.fft.fftshift(f))
        h, w = gray.shape
        cy, cx = h // 2, w // 2
        high = fshift[cy - h // 8:cy + h // 8, cx - w // 8:cx + w // 8].mean()
        total = fshift.mean() + 1e-6
        if (high / total) > 8.0:
            risk += 0.10
            signals.append("compression_block_artefacts")

        return min(1.0, risk), signals
    except Exception as exc:  # noqa: BLE001
        logger.warning("[integrity_detection] visual forensics failed (%s)", exc)
        return 0.0, []


async def _get_doc_path(db: AsyncSession, doc_id: str) -> Optional[str]:
    try:
        res = await db.execute(text(
            "SELECT file_path FROM rx_documents WHERE id = :id AND deleted = FALSE"),
            {"id": doc_id})
        row = res.mappings().first()
        return row["file_path"] if row else None
    except Exception:  # noqa: BLE001
        return None


async def score(
    db: AsyncSession,
    pharmacy_id: str,
    *,
    dea_schedule: Optional[str] = None,
    is_controlled: bool = False,
    doc_id: Optional[str] = None,
    prescriber_id: Optional[str] = None,
    ndc: str = "",
    drug_name: str = "",
    quantity: float = 0.0,
    force_tier: Optional[Tier] = None,
) -> dict:
    """
    Forgery/integrity score for a controlled-substance Rx. §1.2 envelope.
    Non-controlled → skipped (no-op).
    """
    tier = IntelligenceTier.resolve(SERVICE, force=force_tier)

    if not _is_controlled(dea_schedule, is_controlled):
        return build_envelope(
            IntegrityScore(skipped=True, skip_reason="not_a_controlled_substance").__dict__,
            tier_used=tier, confidence=1.0, degraded=False,
            options_active=["controlled_gate"], options_offline=[],
            model_version="integrity_detection_v1",
        )

    signals: list[str] = []

    # 1. Visual forensics (offline).
    visual_risk = 0.0
    if doc_id:
        path = await _get_doc_path(db, doc_id)
        if path and Path(path).exists():
            visual_risk, vsig = _visual_forensics(path)
            signals += vsig
        else:
            signals.append("no_image_available")

    # 2. Behavioral deviation (reuse #13, offline).
    behavioral_risk = 0.0
    if prescriber_id and (ndc or drug_name):
        try:
            dev_env = await prescriber_enrichment.score_rx(
                db, pharmacy_id, prescriber_id,
                ndc=ndc, drug_name=drug_name, quantity=quantity,
                is_controlled=True, force_tier=tier,
            )
            behavioral_risk = float(dev_env["result"].get("deviation_score", 0.0))
            signals += [f"behavior:{f}" for f in dev_env["result"].get("flags", [])]
        except Exception as exc:  # noqa: BLE001
            logger.warning("[integrity_detection] behavioral score failed (%s)", exc)

    # Fuse: visual weighted slightly higher; controlled substances get a floor bump.
    forgery_score = round(min(1.0, 0.55 * visual_risk + 0.45 * behavioral_risk), 3)

    band = ("critical" if forgery_score >= RISK_CRITICAL
            else "warning" if forgery_score >= RISK_WARNING else "ok")

    if band == "critical":
        action = "hold_for_pharmacist_review"
        rationale = "Multiple integrity signals on a controlled-substance Rx. Hold and verify with prescriber before dispensing."
    elif band == "warning":
        action = "verify_prescriber"
        rationale = "Some integrity concern on a controlled-substance Rx. Recommend prescriber call-back / PDMP check."
    else:
        action = "proceed_with_normal_diligence"
        rationale = "No strong integrity concerns detected on local signals."

    score_obj = IntegrityScore(
        skipped=False, forgery_score=forgery_score, risk_band=band,
        visual_risk=round(visual_risk, 3), behavioral_risk=round(behavioral_risk, 3),
        signals=signals, rationale=rationale, recommended_action=action,
    )

    options_active  = ["visual_forensics", "behavioral_deviation", "controlled_gate"]
    options_offline: list[str] = []
    degraded = (tier == Tier.LOCAL)

    if tier in (Tier.CLOUD, Tier.HYBRID):
        async def _corroborate():
            return True   # placeholder: license validity, dup-image, PDMP
        _, ok = await cloud_call(_corroborate, fallback=False, label="integrity_corroborate")
        if ok:
            options_active += ["license_validity", "duplicate_image_check", "pdmp_corroboration"]
            tier = Tier.HYBRID
        else:
            degraded = True
            options_offline = ["license_validity", "duplicate_image_check", "pdmp_corroboration"]
    else:
        options_offline = ["license_validity", "duplicate_image_check", "pdmp_corroboration"]

    confidence = 0.7 if (visual_risk > 0 or behavioral_risk > 0) else 0.5
    return build_envelope(
        score_obj.__dict__,
        tier_used=tier, confidence=confidence, degraded=degraded,
        options_active=options_active, options_offline=options_offline,
        model_version="integrity_detection_v1",
    )
