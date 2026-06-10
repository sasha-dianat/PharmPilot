"""
Package Verification Router — Phase 24
========================================
REST endpoints for medication packaging visual verification.

POST /api/v1/package-verification/extract-from-package
     Run OCR on package photos → extract drug name, NDC, strength, lot,
     expiry, manufacturer automatically. Staff verifies, not types.

POST /api/v1/package-verification/po-lookup
     Look up open Purchase Orders matching a scanned NDC for cross-reference.

POST /api/v1/package-verification/enroll
     Enroll a product from base64 photos taken during receiving.

POST /api/v1/package-verification/verify
     Verify that a dispensed item visually matches its expected NDC.

POST /api/v1/package-verification/identify
     Identify an unknown package (best match across enrolled catalogue).

GET  /api/v1/package-verification/products
     List all enrolled products.

GET  /api/v1/package-verification/products/{ndc11}
     Get details of one enrolled product.

DELETE /api/v1/package-verification/products/{ndc11}
     Remove a product from the enrollment index.

GET  /api/v1/package-verification/status
     Engine status (backend type, dim, enrolled count, cache state).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.database import get_db
from services.platform.auth import get_current_user
from services.ai.package_verification import (
    PackageEnrollmentEngine,
    PackageVerificationEngine,
)
from services.ai.package_verification.feature_extractor import PackageFeatureExtractor
from services.ai.package_verification.ocr_extractor import PackageOCRExtractor

logger = logging.getLogger(__name__)

router = APIRouter()

# Module-level singletons (lazily initialized on first request)
_enrollment:   Optional[PackageEnrollmentEngine]   = None
_verification: Optional[PackageVerificationEngine] = None
_ocr:          Optional[PackageOCRExtractor]        = None


def _get_engines() -> tuple[PackageEnrollmentEngine, PackageVerificationEngine]:
    global _enrollment, _verification
    if _enrollment is None:
        _enrollment   = PackageEnrollmentEngine()
        _verification = PackageVerificationEngine(_enrollment)
    return _enrollment, _verification


def _get_ocr() -> PackageOCRExtractor:
    global _ocr
    if _ocr is None:
        _ocr = PackageOCRExtractor()
    return _ocr


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class ExtractRequest(BaseModel):
    photos_b64:      list[str]   # base64-encoded images (JPEG/PNG) of the package
    use_all_frames:  bool = True  # OCR all frames for maximum coverage


class POLookupRequest(BaseModel):
    ndc11:       str
    pharmacy_id: Optional[str] = None


class EnrollRequest(BaseModel):
    ndc11:        str
    drug_name:    str
    manufacturer: str = ""
    dosage_form:  str = "other"
    lot_number:   Optional[str] = None
    expiry_date:  Optional[str] = None   # YYYY-MM-DD
    photos_b64:   list[str]              # original photos (for visual embedding)
    enrolled_by:  str
    notes:        Optional[str] = None
    replace:      bool = False
    po_id:        Optional[str] = None   # linked purchase order


class VerifyRequest(BaseModel):
    frame_b64:    str
    expected_ndc: str
    top_k:        int = 3


class IdentifyRequest(BaseModel):
    frame_b64: str
    top_k:     int = 5


# ---------------------------------------------------------------------------
# OCR extraction endpoint
# ---------------------------------------------------------------------------

@router.post("/extract-from-package")
async def extract_from_package(
    body: ExtractRequest,
    _user=Depends(get_current_user),
):
    """
    Run OCR on package photos and extract drug information automatically.

    Returns pre-filled drug details for the enrollment form.
    Staff verifies/corrects the data — no manual entry required.

    Fields extracted:
      - NDC-11 (normalised)
      - Drug name / brand name
      - Strength (e.g., "500 mg")
      - Dosage form (tablet, capsule, sachet, blister, etc.)
      - Manufacturer / labeler
      - Lot / batch number
      - Expiry date (YYYY-MM-DD)
      - Storage conditions
    """
    ocr = _get_ocr()
    enrollment, _ = _get_engines()
    extractor: PackageFeatureExtractor = enrollment.extractor

    if not body.photos_b64:
        raise HTTPException(status_code=422, detail="At least 1 photo is required")

    # Decode all images
    images = []
    for i, b64 in enumerate(body.photos_b64):
        img = extractor.decode_base64_frame(b64)
        if img is not None:
            images.append(img)
        else:
            logger.warning("Frame %d could not be decoded for OCR", i + 1)

    if not images:
        raise HTTPException(status_code=422, detail="No photos could be decoded")

    info = ocr.extract(images, use_all_frames=body.use_all_frames)

    return {
        "extracted": {
            "ndc11":         info.ndc11,
            "ndc_raw":       info.ndc_raw,
            "drug_name":     info.drug_name,
            "brand_name":    info.brand_name,
            "generic_name":  info.generic_name,
            "strength":      info.strength,
            "dosage_form":   info.dosage_form,
            "manufacturer":  info.manufacturer,
            "lot_number":    info.lot_number,
            "expiry_date":   info.expiry_date,
            "package_qty":   info.package_qty,
            "storage_notes": info.storage_notes,
        },
        "ocr_backend":   info.ocr_backend,
        "confidence":    info.confidence,
        "warnings":      info.warnings,
        "frames_processed": len(images),
    }


# ---------------------------------------------------------------------------
# Purchase Order cross-reference endpoint
# ---------------------------------------------------------------------------

@router.post("/po-lookup")
async def lookup_purchase_order(
    body: POLookupRequest,
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    """
    Find open Purchase Orders that include the scanned NDC.

    Used during receiving to cross-reference what was ordered vs what arrived.
    Returns PO details including wholesaler, ordered/received qty, unit cost,
    expected delivery date, and PO number (printed on distributor invoice).
    """
    ndc_clean = body.ndc11.replace("-", "").replace(" ", "")[:11]

    try:
        result = await db.execute(
            text("""
                SELECT
                    po.id            AS po_id,
                    po.po_number,
                    po.wholesaler,
                    po.status        AS po_status,
                    po.ordered_at,
                    po.expected_delivery,
                    po.received_at,
                    po.total_cost,
                    pol.ndc11,
                    pol.quantity_ordered,
                    pol.quantity_received,
                    pol.unit_cost,
                    pol.wholesaler_item_number,
                    pol.status       AS line_status,
                    dp.generic_name,
                    dp.brand_name,
                    dp.strength,
                    dp.dosage_form,
                    dp.labeler_name
                FROM purchase_order_lines pol
                JOIN purchase_orders po  ON po.id  = pol.order_id
                LEFT JOIN drug_products dp ON dp.id = pol.drug_product_id
                WHERE pol.ndc11 = :ndc
                  AND po.status IN ('submitted', 'acknowledged', 'partial', 'draft')
                ORDER BY po.ordered_at DESC NULLS LAST
                LIMIT 5
            """),
            {"ndc": ndc_clean},
        )
        rows = result.mappings().all()
    except Exception:
        # DB not available (e.g., preview mode) — return empty
        rows = []

    if not rows:
        return {
            "ndc11":    ndc_clean,
            "found":    False,
            "message":  "No open purchase orders found for this NDC. "
                        "The item may not have been ordered yet, or all POs are complete.",
            "orders":   [],
        }

    orders = []
    for row in rows:
        orders.append({
            "po_id":                str(row["po_id"]),
            "po_number":            row["po_number"],
            "wholesaler":           row["wholesaler"],
            "po_status":            row["po_status"],
            "line_status":          row["line_status"],
            "ordered_at":           str(row["ordered_at"]) if row["ordered_at"] else None,
            "expected_delivery":    str(row["expected_delivery"]) if row["expected_delivery"] else None,
            "received_at":          str(row["received_at"]) if row["received_at"] else None,
            "ndc11":                row["ndc11"],
            "quantity_ordered":     float(row["quantity_ordered"] or 0),
            "quantity_received":    float(row["quantity_received"] or 0),
            "quantity_outstanding": float(row["quantity_ordered"] or 0) - float(row["quantity_received"] or 0),
            "unit_cost":            float(row["unit_cost"]) if row["unit_cost"] else None,
            "wholesaler_item_no":   row["wholesaler_item_number"],
            "drug_from_catalog": {
                "generic_name": row["generic_name"],
                "brand_name":   row["brand_name"],
                "strength":     row["strength"],
                "dosage_form":  row["dosage_form"],
                "manufacturer": row["labeler_name"],
            },
        })

    return {
        "ndc11":  ndc_clean,
        "found":  True,
        "count":  len(orders),
        "orders": orders,
    }


# ---------------------------------------------------------------------------
# Enrollment endpoint (now includes optional lot/expiry/po_id)
# ---------------------------------------------------------------------------

@router.post("/enroll", status_code=status.HTTP_201_CREATED)
async def enroll_product(body: EnrollRequest, _user=Depends(get_current_user)):
    """
    Enroll a medication packaging appearance.

    Workflow:
      1. Call /extract-from-package to get OCR-extracted drug details
      2. Call /po-lookup to cross-reference with purchase order
      3. Staff reviews/corrects extracted data in the workstation form
      4. Call this endpoint with the verified data + original photos

    The photos are used to build the visual reference embedding (ML model).
    The structured drug data (drug_name, ndc11, etc.) is from OCR + staff verification.
    """
    enrollment, verification = _get_engines()
    extractor: PackageFeatureExtractor = enrollment.extractor

    if len(body.photos_b64) < 1:
        raise HTTPException(status_code=422, detail="At least 1 photo is required")

    images = []
    for i, b64 in enumerate(body.photos_b64):
        img = extractor.decode_base64_frame(b64)
        if img is None:
            raise HTTPException(status_code=422, detail=f"Photo {i + 1} could not be decoded")
        images.append(img)

    # Build notes: include lot/expiry if captured, for traceability
    notes_parts = []
    if body.notes:
        notes_parts.append(body.notes)
    if body.lot_number:
        notes_parts.append(f"Lot: {body.lot_number}")
    if body.expiry_date:
        notes_parts.append(f"Exp: {body.expiry_date}")
    if body.po_id:
        notes_parts.append(f"PO: {body.po_id}")

    try:
        product = enrollment.enroll(
            ndc11        = body.ndc11,
            drug_name    = body.drug_name,
            images_bgr   = images,
            enrolled_by  = body.enrolled_by,
            manufacturer = body.manufacturer,
            dosage_form  = body.dosage_form,
            notes        = " | ".join(notes_parts) or None,
            replace      = body.replace,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    verification.invalidate_cache()

    return {
        "success":      True,
        "ndc11":        product.ndc11,
        "drug_name":    product.drug_name,
        "photo_count":  product.photo_count,
        "enrolled_by":  product.enrolled_by,
        "enrolled_at":  product.enrolled_at,
        "dosage_form":  product.dosage_form,
        "lot_number":   body.lot_number,
        "expiry_date":  body.expiry_date,
        "linked_po":    body.po_id,
        "backend":      enrollment.extractor.backend,
        "embed_dim":    enrollment.extractor.dim,
    }


# ---------------------------------------------------------------------------
# Verify endpoint
# ---------------------------------------------------------------------------

@router.post("/verify")
async def verify_item(body: VerifyRequest, _user=Depends(get_current_user)):
    """
    Verify that a dispensed item visually matches its expected NDC.
    Returns status: pass | warn | fail | unenrolled | error
    """
    enrollment, verification = _get_engines()
    frame = enrollment.extractor.decode_base64_frame(body.frame_b64)
    if frame is None:
        raise HTTPException(status_code=422, detail="Frame could not be decoded")

    result = verification.verify_for_rx(
        frame_bgr    = frame,
        expected_ndc = body.expected_ndc,
        top_k        = body.top_k,
    )

    return {
        "status":          result.status,
        "similarity":      result.similarity,
        "confidence_pct":  result.confidence_pct,
        "expected_ndc":    result.expected_ndc,
        "expected_name":   result.expected_name,
        "message":         result.message,
        "verified_at":     result.verified_at,
        "latency_ms":      result.latency_ms,
        "is_pass":         result.is_pass,
        "needs_alert":     result.needs_alert,
        "top_candidates": [
            {"ndc11": c.ndc11, "drug_name": c.drug_name,
             "similarity": c.similarity, "is_accepted": c.is_accepted}
            for c in result.top_candidates
        ],
    }


# ---------------------------------------------------------------------------
# Identify endpoint
# ---------------------------------------------------------------------------

@router.post("/identify")
async def identify_unknown(body: IdentifyRequest, _user=Depends(get_current_user)):
    """Identify an unknown package by visual appearance."""
    enrollment, verification = _get_engines()
    frame = enrollment.extractor.decode_base64_frame(body.frame_b64)
    if frame is None:
        raise HTTPException(status_code=422, detail="Frame could not be decoded")

    result = verification.identify_unknown(frame_bgr=frame, top_k=body.top_k)
    return {
        "status":           result.status,
        "best_match_ndc":   result.expected_ndc,
        "best_match_name":  result.expected_name,
        "confidence_pct":   result.confidence_pct,
        "message":          result.message,
        "identified_at":    result.verified_at,
        "latency_ms":       result.latency_ms,
        "candidates": [
            {"ndc11": c.ndc11, "drug_name": c.drug_name,
             "similarity": c.similarity, "is_accepted": c.is_accepted}
            for c in result.top_candidates
        ],
    }


# ---------------------------------------------------------------------------
# Product catalogue endpoints
# ---------------------------------------------------------------------------

@router.get("/products")
async def list_products(_user=Depends(get_current_user)):
    enrollment, _ = _get_engines()
    products = enrollment.list_products()
    return {
        "total": len(products),
        "products": [
            {
                "ndc11":        p.ndc11,
                "drug_name":    p.drug_name,
                "manufacturer": p.manufacturer,
                "dosage_form":  p.dosage_form,
                "photo_count":  p.photo_count,
                "enrolled_by":  p.enrolled_by,
                "enrolled_at":  p.enrolled_at,
                "last_updated": p.last_updated,
                "notes":        p.notes,
            }
            for p in products
        ],
    }


@router.get("/products/{ndc11}")
async def get_product(ndc11: str, _user=Depends(get_current_user)):
    enrollment, _ = _get_engines()
    p = enrollment.get_product(ndc11)
    if not p:
        raise HTTPException(status_code=404, detail=f"NDC {ndc11} not enrolled")
    return {
        "ndc11":        p.ndc11,
        "drug_name":    p.drug_name,
        "manufacturer": p.manufacturer,
        "dosage_form":  p.dosage_form,
        "photo_count":  p.photo_count,
        "enrolled_by":  p.enrolled_by,
        "enrolled_at":  p.enrolled_at,
        "last_updated": p.last_updated,
        "notes":        p.notes,
    }


@router.delete("/products/{ndc11}")
async def delete_product(ndc11: str, staff_id: str, _user=Depends(get_current_user)):
    enrollment, verification = _get_engines()
    deleted = enrollment.delete_product(ndc11, staff_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"NDC {ndc11} not found")
    verification.invalidate_cache()
    return {"success": True, "ndc11": ndc11, "deleted_by": staff_id}


@router.get("/status")
async def engine_status(_user=Depends(get_current_user)):
    enrollment, _ = _get_engines()
    ocr = _get_ocr()
    products = enrollment.list_products()
    counts: dict[str, int] = {}
    for p in products:
        counts[p.dosage_form] = counts.get(p.dosage_form, 0) + 1
    return {
        "backend":               enrollment.extractor.backend,
        "embed_dim":             enrollment.extractor.dim,
        "enrolled_count":        len(products),
        "accept_threshold":      0.80,
        "warn_threshold":        0.60,
        "ocr_backend":           ocr.backend,
        "dosage_form_breakdown": counts,
    }
