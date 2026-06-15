"""Shelf-verification vision logic — §1.2 envelope with graceful degradation.

Phase 1 ships the envelope + degradation contract only; real YOLOv8 / PaddleOCR /
MobileNet inference is Phase 3 (GPU). With no model available the result is
ADVISORY (count_verdict='warn') and demands manual confirmation — it never
silently 'passes', matching the platform's other local-AI surfaces.
"""
from __future__ import annotations

import os

# Set when Phase-3 weights are present (a real inference backend would replace this).
_MODEL_AVAILABLE = bool(os.environ.get("SHELF_VISION_MODEL"))


def _envelope(result: dict, *, degraded: bool, confidence: float) -> dict:
    return {
        "result": result,
        "tier_used": "local",
        "confidence": confidence,
        "degraded": degraded,
        "options_offline": [] if not degraded else ["yolo_count", "label_ocr", "form_classifier"],
    }


def _degraded_result(advisory: str) -> dict:
    return {
        "counted_items": None,
        "count_confidence": 0.0,
        "count_delta": None,
        "count_verdict": "warn",  # advisory → staff must confirm; never auto-pass when degraded
        "drug_name_ocr": None,
        "drug_name_match": None,
        "drug_form_detected": None,
        "drug_form_match": None,
        "bounding_boxes": [],
        "advisory_notes": [advisory],
    }


def shelf_verify(*, image_base64: str | None, staged_ndc: str, staged_lot: str,
                 staged_quantity: int, expected_drug_name: str, expected_drug_form: str) -> dict:
    """Verify a posed photo of items against the staged transfer. §1.2 envelope.

    Phase 1: no model (or no image) → degraded advisory requiring manual confirmation.
    Phase 3 will run the real CV stack here and populate counts/OCR/form/boxes.
    """
    if not _MODEL_AVAILABLE or not image_base64:
        return _envelope(
            _degraded_result(
                "Vision model unavailable — manual confirmation required "
                "(Phase 3 enables automatic count, OCR and form check)."
            ),
            degraded=True,
            confidence=0.0,
        )
    # Phase 3: YOLOv8 count + PaddleOCR label + MobileNet form classifier populate a real
    # result and a non-degraded envelope. Until weights ship, presence-without-inference is
    # still treated as degraded so nothing is silently accepted.
    return _envelope(
        _degraded_result("Vision inference not yet wired — manual confirmation required."),
        degraded=True,
        confidence=0.0,
    )
