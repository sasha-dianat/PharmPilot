"""
Prescription OCR Engine
========================
Reads scanned images of physical prescriptions and extracts structured data:

  • Patient name (for relationship-tree enrichment)
  • Medications: drug name, strength, dosage form, SIG, quantity, refills
  • Prescriber: name, title/suffix, medical council / license number
  • Rx date, Rx number

Design principles
-----------------
• OCR backend: EasyOCR (GPU-optional) → pytesseract → ValueError if both absent
• Image pre-processing: same CLAHE + sharpen + upscale pipeline as package_verification
• All regex patterns are multilingual — Latin, Persian/Farsi labels both handled
• Extraction is probabilistic; each field carries a `confidence` (0–1)
• Never raises exceptions in `extract()`; missing fields are None + confidence=0

Usage
-----
    ocr = PrescriptionOCR()
    result = ocr.extract(image_bgr_list)
    # result.patient_name, result.medications, result.prescriber, …
"""
from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import Optional, List

import numpy as np

logger = logging.getLogger(__name__)

# ─── Dataclasses ──────────────────────────────────────────────────────────────

@dataclass
class TranscribedMedication:
    drug_name:     str
    strength:      Optional[str]  = None
    dosage_form:   Optional[str]  = None
    sig:           Optional[str]  = None        # Directions / Σ
    quantity:      Optional[str]  = None
    refills:       Optional[int]  = None
    confidence:    float           = 0.0


@dataclass
class TranscribedPrescriber:
    full_name:           str
    suffix:              Optional[str]  = None  # MD, DO, MBBS, etc.
    medical_council_no:  Optional[str]  = None  # License / council registration
    council_authority:   Optional[str]  = None  # "Iranian Medical Council", "GMC", "NPI", etc.
    raw_text:            Optional[str]  = None
    confidence:          float           = 0.0


@dataclass
class TranscribedRx:
    # Patient
    patient_name:        Optional[str]  = None
    patient_name_conf:   float           = 0.0
    patient_dob:         Optional[str]  = None

    # Prescriber
    prescriber:          Optional[TranscribedPrescriber] = None

    # Medications (one physical Rx may contain several)
    medications:         List[TranscribedMedication] = field(default_factory=list)

    # Rx metadata
    rx_date:             Optional[str]  = None
    rx_number:           Optional[str]  = None

    # Raw OCR text (full, for re-processing or audit)
    raw_text:            str             = ""
    overall_confidence:  float           = 0.0


# ─── Regex catalogue ──────────────────────────────────────────────────────────

# ── Patient name ──────────────────────────────────────────────────────────────
_PATIENT_LABEL = re.compile(
    r"(?:"
    r"patient[\s:]*name|patient|name|for|بیمار|نام\s+بیمار|نام"
    r")\s*[:\-]?\s*"
    r"([A-Za-zآ-یءأإؤئ][A-Za-zآ-یءأإؤئ\s]{3,50})",
    re.IGNORECASE,
)
# Standalone proper-name line: two+ capitalised words, no numbers
_STANDALONE_NAME = re.compile(
    r"^(?:Mr\.|Mrs\.|Ms\.|Dr\.|آقای|خانم)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)$"
    r"|^([A-Z][a-z]{1,20}(?:\s+[A-Z][a-z]{1,20}){1,3})$"
)

# ── Medications ───────────────────────────────────────────────────────────────
_RX_ITEM_LABEL = re.compile(
    r"(?:Rx\s*\d*|Drug|Medication|Med\.|℞|دارو|دارویی)[:\-]?\s*([^\n]{4,80})",
    re.IGNORECASE,
)
_STRENGTH = re.compile(
    r"(\d+(?:\.\d+)?)\s*(mg|mcg|µg|g|iu|unit|مگ|میلی\s*گرم|گرم)",
    re.IGNORECASE,
)
_DOSAGE_FORM = re.compile(
    r"\b(tablet|tab|capsule|cap|syrup|injection|inj|solution|sol|cream|ointment|drop|patch|"
    r"inhaler|powder|suspension|قرص|کپسول|شربت|آمپول|قطره|پماد|پودر|اسپری)\b",
    re.IGNORECASE,
)
_SIG_LABEL = re.compile(
    r"(?:sig|directions?|take|use|Σ|دستور\s*مصرف|دستورالعمل)[:\-]?\s*([^\n]{5,120})",
    re.IGNORECASE,
)
_QUANTITY = re.compile(
    r"(?:qty|quantity|disp(?:ense)?|#|تعداد)[:\s]*(\d+(?:\.\d+)?(?:\s*(?:tabs?|caps?|ml|g|units?))?)",
    re.IGNORECASE,
)
_REFILLS = re.compile(
    r"(?:refills?|ref\.?)[:\s]*(\d+)",
    re.IGNORECASE,
)

# ── Prescriber ────────────────────────────────────────────────────────────────
_PRESCRIBER_LABEL = re.compile(
    r"(?:prescriber|physician|doctor|dr\.|attending|پزشک|نام\s*پزشک)[:\-]?\s*"
    r"([A-Za-zآ-یءأإؤئ][A-Za-zآ-یءأإؤئ\s\-\.]{4,60})",
    re.IGNORECASE,
)
_PRESCRIBER_SUFFIX = re.compile(
    r"\b(MD|M\.D\.|DO|D\.O\.|MBBS|MBChB|DDS|PharmD|NP|PA|دکتر|Dr\.?)\b",
    re.IGNORECASE,
)
# Prescriber name + suffix on the same line (common at bottom of Rx)
_PRESCRIBER_WITH_SUFFIX = re.compile(
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\s*,?\s*(MD|M\.D\.|DO|D\.O\.|MBBS|MBChB|DDS|PharmD|NP|PA)(?:\b|$)",
)

# ── Medical council / license number ─────────────────────────────────────────
#
# International patterns:
#   Iran:    شماره نظام پزشکی / نظام پزشکی  followed by 5-8 digit number
#   Generic: License No. / Lic# / Reg. No. / Council No. / Board Reg.
#   NPI (US): 10-digit number after NPI
#   GMC (UK): 7-digit number after GMC
#   MCSA (SA): council number
_COUNCIL_PATTERNS: List[tuple[str, re.Pattern]] = [
    ("Iranian Medical Council",   re.compile(r"(?:نظام\s*پزشکی|شماره\s*نظام)[:\s]*(\d{4,8})",         re.IGNORECASE)),
    ("License",                   re.compile(r"lic(?:ense)?\.?\s*(?:no\.?|#|number)?[:\s]*([A-Z0-9\-/]{4,20})", re.IGNORECASE)),
    ("Registration",              re.compile(r"reg(?:istration)?\.?\s*(?:no\.?|#)?[:\s]*([A-Z0-9\-/]{4,20})", re.IGNORECASE)),
    ("Medical Council",           re.compile(r"(?:medical\s+council|council\s+no\.?|board\s+reg\.?)[:\s#]*([A-Z0-9\-]{4,20})", re.IGNORECASE)),
    ("NPI",                       re.compile(r"\bNPI[:\s#]*(\d{10})\b",                                re.IGNORECASE)),
    ("GMC",                       re.compile(r"\bGMC[:\s#]*(\d{7})\b",                                 re.IGNORECASE)),
    ("Stamp Number",              re.compile(r"(?:stamp|seal|مهر)[:\s#]*([A-Z0-9\-]{3,15})",           re.IGNORECASE)),
]

# ── Rx metadata ───────────────────────────────────────────────────────────────
_RX_DATE = re.compile(
    r"(?:date|تاریخ)[:\s]*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}"
    r"|\d{4}[\/\-]\d{2}[\/\-]\d{2}"
    r"|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s*\d{2,4})",
    re.IGNORECASE,
)
_RX_NUMBER = re.compile(
    r"(?:rx\s*#?|prescription\s*(?:no\.?|#|number)|نسخه\s*شماره)[:\s]*([A-Z0-9\-]{3,20})",
    re.IGNORECASE,
)

# ─── Image pre-processing ─────────────────────────────────────────────────────

def _preprocess(image_bgr: np.ndarray) -> np.ndarray:
    """Enhance image for OCR: CLAHE + sharpen + upscale if small."""
    try:
        import cv2

        # Upscale if small
        h, w = image_bgr.shape[:2]
        if max(h, w) < 1800:
            scale = 1800 / max(h, w)
            image_bgr = cv2.resize(image_bgr, None, fx=scale, fy=scale,
                                   interpolation=cv2.INTER_CUBIC)

        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)

        # CLAHE for contrast
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray  = clahe.apply(gray)

        # Sharpen
        kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
        gray   = cv2.filter2D(gray, -1, kernel)

        # Deskew
        coords = np.column_stack(np.where(gray < 200))
        if len(coords) > 200:
            angle  = cv2.minAreaRect(coords)[-1]
            angle  = -(90 + angle) if angle < -45 else -angle
            if abs(angle) < 15:
                M     = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
                gray  = cv2.warpAffine(gray, M, (w, h),
                                       flags=cv2.INTER_CUBIC,
                                       borderMode=cv2.BORDER_REPLICATE)

        # Threshold for crisp text
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)

    except Exception:
        return image_bgr


# ─── OCR backends ─────────────────────────────────────────────────────────────

def _ocr_easyocr(image_bgr: np.ndarray) -> str:
    import easyocr
    reader = easyocr.Reader(["en", "fa"], gpu=False, verbose=False)
    results = reader.readtext(image_bgr)
    return " \n ".join(r[1] for r in results if r[2] > 0.3)


def _ocr_tesseract(image_bgr: np.ndarray) -> str:
    import pytesseract
    import cv2
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    return pytesseract.image_to_string(gray, lang="eng+fas",
                                       config="--psm 4 --oem 3")


def _run_ocr(image_bgr: np.ndarray) -> tuple[str, str]:
    """Returns (text, backend_used). Tries EasyOCR first, falls back to tesseract."""
    for backend, fn in [("easyocr", _ocr_easyocr), ("tesseract", _ocr_tesseract)]:
        try:
            text = fn(image_bgr)
            if text.strip():
                return text, backend
        except ImportError:
            continue
        except Exception as e:
            logger.warning("[PrescriptionOCR] %s failed: %s", backend, e)
    return "", "none"


# ─── Field parsers ────────────────────────────────────────────────────────────

def _parse_patient_name(lines: List[str], full_text: str) -> tuple[Optional[str], float]:
    # 1. Labelled pattern (highest confidence)
    m = _PATIENT_LABEL.search(full_text)
    if m:
        name = m.group(1).strip().title()
        return name, 0.90

    # 2. Standalone proper-name line in top-third of document
    top_third = lines[:max(1, len(lines) // 3)]
    for line in top_third:
        stripped = line.strip()
        m = _STANDALONE_NAME.match(stripped)
        if m:
            name = (m.group(1) or m.group(2) or "").strip().title()
            if len(name.split()) >= 2 and len(name) < 50:
                return name, 0.70

    return None, 0.0


def _parse_medications(full_text: str) -> List[TranscribedMedication]:
    meds: List[TranscribedMedication] = []

    # Labelled Rx items
    for m in _RX_ITEM_LABEL.finditer(full_text):
        drug_line = m.group(1).strip()
        med       = _parse_single_med_line(drug_line, confidence=0.85)
        if med:
            meds.append(med)

    # If nothing labelled, try to pick up drug-name + strength combinations
    if not meds:
        for line in full_text.split("\n"):
            s = _STRENGTH.search(line)
            if s:
                drug_name = line[:s.start()].strip().rstrip(",;:")
                if drug_name and len(drug_name) > 2:
                    med = _parse_single_med_line(line, confidence=0.65)
                    if med:
                        meds.append(med)

    return meds[:10]   # cap at 10 items per Rx scan


def _parse_single_med_line(line: str, confidence: float) -> Optional[TranscribedMedication]:
    if not line.strip():
        return None

    strength_m = _STRENGTH.search(line)
    dosage_m   = _DOSAGE_FORM.search(line)

    # Drug name = text before the strength/form indicator
    split_at = min(
        strength_m.start() if strength_m else len(line),
        dosage_m.start()   if dosage_m   else len(line),
    )
    drug_name = line[:split_at].strip().rstrip(",;:")

    if not drug_name:
        drug_name = line.strip()

    strength   = (f"{strength_m.group(1)}{strength_m.group(2)}").strip() if strength_m else None
    dosage_form= dosage_m.group(1).lower()  if dosage_m   else None

    return TranscribedMedication(
        drug_name=drug_name,
        strength=strength,
        dosage_form=dosage_form,
        confidence=confidence,
    )


def _parse_prescriber(lines: List[str], full_text: str) -> Optional[TranscribedPrescriber]:
    # 1. Labelled pattern
    m = _PRESCRIBER_LABEL.search(full_text)
    if m:
        raw  = m.group(1).strip()
        name, suffix = _split_name_suffix(raw)
        council_no, authority = _find_council_number(full_text)
        return TranscribedPrescriber(
            full_name=name.title(), suffix=suffix,
            medical_council_no=council_no, council_authority=authority,
            raw_text=raw, confidence=0.90,
        )

    # 2. Name + suffix pattern (common on bottom of printed Rx)
    bottom = lines[max(0, len(lines) - len(lines) // 3):]
    for line in reversed(bottom):
        m = _PRESCRIBER_WITH_SUFFIX.search(line)
        if m:
            council_no, authority = _find_council_number(full_text)
            return TranscribedPrescriber(
                full_name=m.group(1).strip().title(),
                suffix=m.group(2),
                medical_council_no=council_no, council_authority=authority,
                raw_text=line.strip(), confidence=0.80,
            )

    # 3. Dr./Dr prefix
    for line in reversed(lines):
        sm = _PRESCRIBER_SUFFIX.search(line)
        if sm:
            raw  = line.strip()
            name, suffix = _split_name_suffix(raw)
            council_no, authority = _find_council_number(full_text)
            return TranscribedPrescriber(
                full_name=name.title(), suffix=suffix,
                medical_council_no=council_no, council_authority=authority,
                raw_text=raw, confidence=0.65,
            )

    # 4. Just extract the council number — prescriber name might be a stamp
    council_no, authority = _find_council_number(full_text)
    if council_no:
        return TranscribedPrescriber(
            full_name="", suffix=None,
            medical_council_no=council_no, council_authority=authority,
            confidence=0.40,
        )

    return None


def _split_name_suffix(text: str) -> tuple[str, Optional[str]]:
    m = _PRESCRIBER_SUFFIX.search(text)
    if m:
        suffix  = m.group(1)
        name    = (text[:m.start()] + text[m.end():]).strip(" ,;")
        return name, suffix
    return text.strip(), None


def _find_council_number(text: str) -> tuple[Optional[str], Optional[str]]:
    for authority, pat in _COUNCIL_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1).strip(), authority
    return None, None


def _parse_sig(full_text: str) -> Optional[str]:
    m = _SIG_LABEL.search(full_text)
    return m.group(1).strip() if m else None


def _parse_rx_date(full_text: str) -> Optional[str]:
    m = _RX_DATE.search(full_text)
    return m.group(1).strip() if m else None


def _parse_rx_number(full_text: str) -> Optional[str]:
    m = _RX_NUMBER.search(full_text)
    return m.group(1).strip() if m else None


# ─── Main engine class ────────────────────────────────────────────────────────

class PrescriptionOCR:
    """
    Extracts structured prescription data from one or more scanned images
    of a physical Rx (front, back, or multiple angle shots).

    Parameters
    ----------
    multi_image_strategy : "first" | "concat"
        "first"  — OCR only the first image (faster, good for single-page Rx)
        "concat" — OCR all images and concatenate text (slower, for multi-page/back-of-Rx)
    """

    def __init__(self, multi_image_strategy: str = "concat"):
        self.strategy = multi_image_strategy

    def extract(self, images_bgr: List[np.ndarray]) -> TranscribedRx:
        """
        Run OCR on the provided images and return a TranscribedRx.
        Never raises; returns an empty TranscribedRx on total failure.
        """
        if not images_bgr:
            return TranscribedRx()

        try:
            return self._extract(images_bgr)
        except Exception as e:
            logger.error("[PrescriptionOCR] Extraction failed: %s", e, exc_info=True)
            return TranscribedRx()

    def _extract(self, images_bgr: List[np.ndarray]) -> TranscribedRx:
        # OCR
        texts: List[str] = []
        backends: List[str] = []
        imgs_to_ocr = images_bgr if self.strategy == "concat" else images_bgr[:1]

        for img in imgs_to_ocr:
            processed    = _preprocess(img)
            text, backend= _run_ocr(processed)
            if text.strip():
                texts.append(text)
                backends.append(backend)

        full_text = "\n".join(texts)
        if not full_text.strip():
            return TranscribedRx(raw_text="")

        lines = [l for l in full_text.split("\n") if l.strip()]

        # Extract fields
        patient_name, patient_conf = _parse_patient_name(lines, full_text)
        prescriber                 = _parse_prescriber(lines, full_text)
        medications                = _parse_medications(full_text)
        sig                        = _parse_sig(full_text)
        rx_date                    = _parse_rx_date(full_text)
        rx_number                  = _parse_rx_number(full_text)

        # Attach sig to first medication if not already parsed
        if sig and medications and not medications[0].sig:
            medications[0].sig = sig

        # Overall confidence heuristic
        field_scores = [
            patient_conf,
            prescriber.confidence if prescriber else 0.0,
            max((m.confidence for m in medications), default=0.0),
        ]
        overall = sum(field_scores) / max(len(field_scores), 1)

        return TranscribedRx(
            patient_name=patient_name,
            patient_name_conf=patient_conf,
            prescriber=prescriber,
            medications=medications,
            rx_date=rx_date,
            rx_number=rx_number,
            raw_text=full_text,
            overall_confidence=round(overall, 2),
        )

    def extract_from_file(self, file_path: str) -> TranscribedRx:
        """Convenience: load image from file path and extract."""
        try:
            import cv2
            img = cv2.imread(file_path)
            if img is None:
                raise ValueError(f"Could not load image: {file_path}")
            return self.extract([img])
        except Exception as e:
            logger.error("[PrescriptionOCR] File load failed: %s", e)
            return TranscribedRx()
