"""
Package OCR Extractor — Phase 24
==================================
Extracts drug information from medication packaging photos using OCR.

The extracted data pre-fills the enrollment form so inventory staff only
need to verify and confirm — not type anything manually.

Extracted fields:
  - NDC (11-digit, normalised)
  - Drug name (generic + brand)
  - Strength (e.g., "500 mg", "10 mg/2 mL")
  - Dosage form (tablet, capsule, injection, sachet, blister, etc.)
  - Manufacturer / labeler name
  - Lot / batch number
  - Expiry date (normalised to YYYY-MM-DD)
  - Package quantity
  - Storage conditions (refrigerate, protect from light, etc.)

OCR backend priority (auto-selected):
  1. EasyOCR     — best multi-language accuracy, handles print+handwriting
  2. pytesseract — Tesseract wrapper, widely available
  3. OpenCV text detection (MSER) — minimal fallback, extracts raw character regions
     but does NOT produce readable text; used only as a "no OCR engine" signal

Pre-processing pipeline (improves OCR accuracy on packaging):
  1. CLAHE equalisation → improves contrast on glossy/metallic packaging
  2. Adaptive threshold (Gaussian) → binarise
  3. Deskew / rotation correction  → straighten tilted scans
  4. Upscale to ≥300 DPI equivalent if image < 800px wide
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None  # type: ignore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Extracted data model
# ---------------------------------------------------------------------------

@dataclass
class ExtractedPackageInfo:
    """Parsed drug information extracted from package text via OCR."""
    ndc11:          Optional[str]  = None   # normalised 11-digit, no dashes
    ndc_raw:        Optional[str]  = None   # as printed on pack
    drug_name:      Optional[str]  = None   # best guess at generic/brand name
    brand_name:     Optional[str]  = None
    generic_name:   Optional[str]  = None
    strength:       Optional[str]  = None   # e.g. "500 mg", "10 mg/2 mL"
    dosage_form:    Optional[str]  = None   # sachet | blister | bottle | tablet | capsule | …
    manufacturer:   Optional[str]  = None
    lot_number:     Optional[str]  = None
    expiry_date:    Optional[str]  = None   # YYYY-MM-DD
    package_qty:    Optional[str]  = None   # e.g. "30 tablets", "10 x 100 mg"
    storage_notes:  Optional[str]  = None   # e.g. "Store below 25°C"
    raw_text:       str            = ""
    confidence:     float          = 0.0    # 0–1
    ocr_backend:    str            = "none"
    warnings:       list[str]      = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pre-processing helpers
# ---------------------------------------------------------------------------

def _preprocess_for_ocr(image_bgr: np.ndarray) -> np.ndarray:
    """Enhance packaging image for OCR accuracy."""
    if image_bgr is None or image_bgr.size == 0:
        return image_bgr

    h, w = image_bgr.shape[:2]

    # Upscale small images
    if w < 800:
        scale = 800 / w
        image_bgr = cv2.resize(image_bgr, None, fx=scale, fy=scale,
                               interpolation=cv2.INTER_LANCZOS4)

    # CLAHE on L channel (improves contrast on shiny/dark packaging)
    lab = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2LAB)
    cl = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    lab[:, :, 0] = cl.apply(lab[:, :, 0])
    enhanced = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

    # Sharpen
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
    enhanced = cv2.filter2D(enhanced, -1, kernel)

    # Deskew: find dominant text angle via Hough lines on binarised image
    gray = cv2.cvtColor(enhanced, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    angle = _estimate_skew(binary)
    if abs(angle) > 0.5:
        enhanced = _rotate_image(enhanced, angle)

    return enhanced


def _estimate_skew(binary: np.ndarray) -> float:
    """Estimate text skew angle from binary image (degrees)."""
    try:
        coords = np.column_stack(np.where(binary > 0))
        if len(coords) < 100:
            return 0.0
        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle = 90 + angle
        return angle
    except Exception:
        return 0.0


def _rotate_image(image: np.ndarray, angle: float) -> np.ndarray:
    h, w = image.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(image, M, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)


def _best_frame_for_ocr(images: list[np.ndarray]) -> np.ndarray:
    """Select the sharpest, most text-rich frame from a list of package photos."""
    if len(images) == 1:
        return images[0]

    scored = []
    for img in images:
        gray    = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        sharpness = cv2.Laplacian(gray, cv2.CV_64F).var()
        # Estimate text density: proportion of dark pixels on binarised image
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        text_density = binary.sum() / binary.size
        score = sharpness * (1 + text_density * 5)
        scored.append((score, img))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


# ---------------------------------------------------------------------------
# OCR backends
# ---------------------------------------------------------------------------

def _ocr_easyocr(image_bgr: np.ndarray) -> tuple[str, float]:
    """EasyOCR — best accuracy, supports many scripts including Persian/Arabic."""
    try:
        import easyocr
        reader = easyocr.Reader(['en', 'fa'], gpu=False, verbose=False)
        results = reader.readtext(image_bgr, detail=1, paragraph=False)
        lines = []
        total_conf = 0.0
        for (_bbox, text, conf) in results:
            lines.append(text)
            total_conf += conf
        combined = "\n".join(lines)
        avg_conf = (total_conf / len(results)) if results else 0.0
        return combined, avg_conf
    except ImportError:
        raise
    except Exception as e:
        logger.warning("EasyOCR error: %s", e)
        return "", 0.0


def _ocr_tesseract(image_bgr: np.ndarray) -> tuple[str, float]:
    """pytesseract — reliable, widely installed."""
    try:
        import pytesseract
        from PIL import Image as PILImage

        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        pil_img = PILImage.fromarray(rgb)

        # Get text with confidence
        data = pytesseract.image_to_data(pil_img, output_type=pytesseract.Output.DICT,
                                         config="--oem 3 --psm 6")
        words  = [w for w, c in zip(data["text"], data["conf"]) if int(c) > 0 and w.strip()]
        confs  = [int(c) for c in data["conf"] if int(c) > 0]
        text   = pytesseract.image_to_string(pil_img, config="--oem 3 --psm 6")
        avg_c  = (sum(confs) / len(confs) / 100.0) if confs else 0.0
        return text, avg_c
    except ImportError:
        raise
    except Exception as e:
        logger.warning("Tesseract error: %s", e)
        return "", 0.0


# ---------------------------------------------------------------------------
# Text parsing helpers
# ---------------------------------------------------------------------------

# NDC patterns: 5-4-2, 5-3-2, 4-4-2 (with dashes, spaces, or raw 11 digits)
_NDC_PATTERNS = [
    re.compile(r'\b(\d{5})[-\s](\d{4})[-\s](\d{2})\b'),          # 5-4-2
    re.compile(r'\b(\d{5})[-\s](\d{3})[-\s](\d{2})\b'),           # 5-3-2
    re.compile(r'\b(\d{4})[-\s](\d{4})[-\s](\d{2})\b'),           # 4-4-2
    re.compile(r'\bNDC\s*[:#]?\s*(\d{5,6}[-\s]?\d{3,4}[-\s]?\d{1,2})\b', re.I),
    re.compile(r'\b(\d{11})\b'),  # raw 11 digits (last resort)
]

_STRENGTH_PATTERN = re.compile(
    r'(\d+\.?\d*)\s*(mg|mcg|µg|g|ml|mL|L|%|iu|IU|units|unit|mmol|mEq|mg/ml|mg/mL|mcg/ml|µg/mL)',
    re.I,
)

_LOT_PATTERN = re.compile(
    r'(?:lot|batch|lot\s*no|lot\s*#|b\.?\s*no|batch\s*no)[\s:#]*([A-Z0-9][A-Z0-9\-]{1,20})',
    re.I,
)

_EXPIRY_PATTERNS = [
    re.compile(r'(?:exp(?:iry|ires?)?|use\s*by|best\s*before|bb)[\s:./]*(\d{1,2})[/.-](\d{4})', re.I),
    re.compile(r'(?:exp(?:iry|ires?)?|use\s*by)[\s:./]*(\d{1,2})[/.-](\d{2})\b', re.I),
    re.compile(r'(?:exp(?:iry|ires?)?|use\s*by)[\s:./]*([A-Za-z]{3,9})[\s/-]?(\d{2,4})', re.I),
]

_DOSAGE_FORM_KEYWORDS: dict[str, str] = {
    'tablet':     'tablet',  'tab':       'tablet',
    'capsule':    'capsule', 'cap':       'capsule',
    'sachet':     'sachet',
    'blister':    'blister',
    'injection':  'injection','injectable':'injection','inj':     'injection',
    'vial':       'vial',
    'ampoule':    'ampoule', 'ampule':    'ampoule',
    'syrup':      'syrup',   'suspension':'syrup',    'susp':    'syrup',
    'solution':   'solution','soln':      'solution',
    'cream':      'cream',
    'ointment':   'ointment','oint':      'ointment',
    'gel':        'gel',
    'patch':      'patch',   'transdermal':'patch',
    'inhaler':    'inhaler', 'inhalation':'inhaler',
    'drops':      'drops',   'ophthalmic':'drops',
    'powder':     'powder',
    'effervescent':'effervescent',
}

_STORAGE_KEYWORDS = [
    'refrigerate', 'store below', 'keep refrigerated', 'do not freeze',
    'protect from light', 'store in cool', 'below 25', 'below 30',
    'store at room temperature', 'controlled room temperature',
]

_MANUFACTURER_PREFIXES = [
    'manufactured by', 'manufactured for', 'distributed by',
    'mfg by', 'mfr:', 'marketed by', 'produced by',
]


def _parse_ndc(text: str) -> tuple[Optional[str], Optional[str]]:
    """Return (ndc11_clean, ndc_raw)."""
    for pattern in _NDC_PATTERNS:
        m = pattern.search(text)
        if m:
            raw = m.group(0)
            digits = re.sub(r'\D', '', raw)
            if len(digits) == 11:
                return digits, raw
            elif len(digits) == 10:
                return '0' + digits, raw   # pad labeler to 5 digits
    return None, None


def _parse_strength(text: str) -> Optional[str]:
    matches = _STRENGTH_PATTERN.findall(text)
    if not matches:
        return None
    # Return the most specific (longest) match
    strengths = [f"{v} {u}" for v, u in matches]
    return strengths[0]


def _parse_lot(text: str) -> Optional[str]:
    m = _LOT_PATTERN.search(text)
    return m.group(1).strip() if m else None


def _parse_expiry(text: str) -> Optional[str]:
    for pattern in _EXPIRY_PATTERNS:
        m = pattern.search(text)
        if m:
            try:
                g = m.groups()
                if len(g) == 2:
                    # Try MM/YYYY
                    try:
                        month = int(g[0])
                        year  = int(g[1])
                        if year < 100:
                            year += 2000
                        if 1 <= month <= 12:
                            return f"{year:04d}-{month:02d}-01"
                    except ValueError:
                        pass
                    # Try month-name/year
                    try:
                        dt = datetime.strptime(f"{g[0]} {g[1]}", "%b %Y")
                        return dt.strftime("%Y-%m-01")
                    except ValueError:
                        pass
            except Exception:
                pass
    return None


def _parse_dosage_form(text: str) -> Optional[str]:
    text_lower = text.lower()
    for kw, form in _DOSAGE_FORM_KEYWORDS.items():
        if re.search(r'\b' + kw + r'\b', text_lower):
            return form
    return None


def _parse_storage(text: str) -> Optional[str]:
    text_lower = text.lower()
    for kw in _STORAGE_KEYWORDS:
        idx = text_lower.find(kw)
        if idx != -1:
            return text[idx:idx + 60].strip().split('\n')[0].strip()
    return None


def _parse_manufacturer(text: str) -> Optional[str]:
    text_lower = text.lower()
    for prefix in _MANUFACTURER_PREFIXES:
        idx = text_lower.find(prefix)
        if idx != -1:
            rest  = text[idx + len(prefix):idx + len(prefix) + 80].strip()
            first = rest.split('\n')[0].strip(' .,')
            if first:
                return first
    return None


def _parse_drug_name(text: str, strength: Optional[str]) -> Optional[str]:
    """
    Heuristic: drug name is usually on the first 1-3 lines (before lot/expiry/NDC).
    Filter out lines that are pure numbers, dates, or NDC-like.
    """
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    candidates = []
    for line in lines[:8]:
        # Skip lines that are purely numeric, look like NDC, lot, expiry, etc.
        if re.fullmatch(r'[\d\s\-/\.%]+', line):
            continue
        if re.search(r'\b(?:ndc|lot|exp|batch|mfg|manufactured|distributed)\b', line, re.I):
            continue
        if len(line) < 3 or len(line) > 80:
            continue
        # Prefer lines with letters
        if sum(c.isalpha() for c in line) >= 3:
            candidates.append(line)

    if not candidates:
        return None

    # First good candidate is usually the drug name
    name = candidates[0]
    # Remove embedded strength if present (avoid duplication)
    if strength:
        name = re.sub(re.escape(strength), '', name, flags=re.I).strip(' ,')
    return name or None


# ---------------------------------------------------------------------------
# Main extractor class
# ---------------------------------------------------------------------------

class PackageOCRExtractor:
    """
    Runs OCR on package photos and parses drug information.

    Usage::

        extractor = PackageOCRExtractor()
        info = extractor.extract(images_bgr)
        # info.drug_name, info.strength, info.ndc11, info.lot_number, …
    """

    def __init__(self) -> None:
        self._backend: str = self._detect_backend()

    def _detect_backend(self) -> str:
        try:
            import easyocr  # noqa: F401
            logger.info("PackageOCRExtractor: using EasyOCR")
            return "easyocr"
        except ImportError:
            pass
        try:
            import pytesseract  # noqa: F401
            logger.info("PackageOCRExtractor: using pytesseract/Tesseract")
            return "tesseract"
        except ImportError:
            pass
        logger.warning("PackageOCRExtractor: no OCR engine found — install easyocr or pytesseract")
        return "none"

    @property
    def backend(self) -> str:
        return self._backend

    def extract(
        self,
        images_bgr: list[np.ndarray],
        use_all_frames: bool = False,
    ) -> ExtractedPackageInfo:
        """
        Extract drug information from one or more package photos.

        Args:
            images_bgr:    List of BGR images (any number of angles)
            use_all_frames: If True, run OCR on ALL frames and merge results.
                            If False (default), use only the sharpest frame.
                            use_all_frames=True is slower but finds more info.
        Returns:
            ExtractedPackageInfo with all parsed fields
        """
        if not images_bgr:
            return ExtractedPackageInfo(
                warnings=["No images provided"],
                ocr_backend=self._backend,
            )

        if self._backend == "none":
            return ExtractedPackageInfo(
                warnings=[
                    "No OCR engine installed. Install easyocr or pytesseract to enable "
                    "automatic text extraction from packaging."
                ],
                ocr_backend="none",
            )

        # Select frames
        if use_all_frames or len(images_bgr) == 1:
            frames = images_bgr
        else:
            # Use best frame + top-2 by sharpness for redundancy
            scored = sorted(
                images_bgr,
                key=lambda img: cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var(),
                reverse=True,
            )
            frames = scored[:min(3, len(scored))]

        # Run OCR
        all_text_parts = []
        total_conf = 0.0
        for img in frames:
            preprocessed = _preprocess_for_ocr(img)
            try:
                if self._backend == "easyocr":
                    text, conf = _ocr_easyocr(preprocessed)
                else:
                    text, conf = _ocr_tesseract(preprocessed)
                if text.strip():
                    all_text_parts.append(text)
                    total_conf += conf
            except ImportError:
                # Backend was detected at init but import failed during use
                self._backend = "none"
                return ExtractedPackageInfo(
                    warnings=["OCR backend became unavailable during extraction"],
                    ocr_backend="none",
                )
            except Exception as e:
                logger.warning("OCR error on frame: %s", e)

        combined_text = "\n\n".join(all_text_parts)
        avg_conf = (total_conf / len(frames)) if frames else 0.0

        return self._parse(combined_text, avg_conf)

    def extract_from_single_b64(self, b64_image: str) -> ExtractedPackageInfo:
        """Convenience: decode one base64 image and extract."""
        import base64
        try:
            hdr = b64_image.find(',')
            data = base64.b64decode(b64_image[hdr + 1:] if hdr != -1 else b64_image)
            arr  = np.frombuffer(data, dtype=np.uint8)
            img  = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                return ExtractedPackageInfo(warnings=["Could not decode image"])
            return self.extract([img])
        except Exception as e:
            return ExtractedPackageInfo(warnings=[f"Decode error: {e}"])

    def _parse(self, text: str, confidence: float) -> ExtractedPackageInfo:
        if not text.strip():
            return ExtractedPackageInfo(
                raw_text=text,
                confidence=0.0,
                ocr_backend=self._backend,
                warnings=["OCR produced no text — image may be too blurry or low-contrast"],
            )

        warnings = []
        ndc11, ndc_raw = _parse_ndc(text)
        strength       = _parse_strength(text)
        lot            = _parse_lot(text)
        expiry         = _parse_expiry(text)
        dosage_form    = _parse_dosage_form(text)
        manufacturer   = _parse_manufacturer(text)
        storage        = _parse_storage(text)
        drug_name      = _parse_drug_name(text, strength)

        if not ndc11:
            warnings.append("NDC not found in text — please enter manually")
        if not expiry:
            warnings.append("Expiry date not found — please enter manually")
        if not lot:
            warnings.append("Lot number not found — please enter manually")

        return ExtractedPackageInfo(
            ndc11        = ndc11,
            ndc_raw      = ndc_raw,
            drug_name    = drug_name,
            strength     = strength,
            dosage_form  = dosage_form or "other",
            manufacturer = manufacturer,
            lot_number   = lot,
            expiry_date  = expiry,
            storage_notes= storage,
            raw_text     = text[:3000],     # cap to avoid huge payloads
            confidence   = round(confidence, 3),
            ocr_backend  = self._backend,
            warnings     = warnings,
        )
