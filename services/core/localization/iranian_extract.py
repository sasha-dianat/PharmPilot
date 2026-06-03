"""
Iranian identity extraction from free text (transcript / OCR'd prescription).
=============================================================================
Pulls structured identity fields out of:
  1. Reception-booth conversation transcripts (Persian speech-to-text)
  2. Photographs of handwritten/printed prescriptions (OCR text)

Staff never type a name — everything here is automatic. The strongest signal is
the national code (کد ملی): a checksum-valid 10-digit number is near-unique and
is preferred over fuzzy name matching.

Handles Persian digits, Persian name honorifics (آقای/خانم), Jalali dates,
and Iranian mobile numbers (۰۹xxxxxxxxx / +۹۸).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from services.core.localization.digits import normalize_digits
from services.core.localization.jalali import jalali_str_to_date
from services.core.localization.national_id import (
    NATIONAL_CODE_PATTERN,
    validate_national_code,
)


@dataclass
class ExtractedIdentity:
    national_code: Optional[str] = None
    national_code_valid: bool = False
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    father_name: Optional[str] = None
    full_name_raw: Optional[str] = None
    dob: Optional[date] = None                 # Gregorian (canonical)
    dob_jalali: Optional[str] = None           # As written
    gender: Optional[str] = None               # M | F
    phone: Optional[str] = None
    # Per-field provenance + an overall confidence for the strongest signal.
    provenance: dict = field(default_factory=dict)
    confidence: float = 0.0

    @property
    def has_strong_key(self) -> bool:
        """True when we have a checksum-valid national code (near-unique)."""
        return self.national_code_valid


# ── Lexical resources ─────────────────────────────────────────────────────────

# Honorifics that precede a name in Persian
_HONORIFIC = r"(?:سرکار خانم|جناب آقای|نام و نام خانوادگی|نام بیمار|به نام|آقای|خانم|بیمار)"
# Persian LETTER class only — explicitly excludes punctuation (،؛؟:) and digits.
# Covers Arabic base letters + Persian-specific پ چ ژ ک گ ی + ZWNJ.
_FA = "ء-غف-يٰ-ٳٹپچژکگۀ-یە‌"
# Tokens that are field labels, never part of a name — stop capture here.
_NAME_STOPWORDS = {
    # field labels
    "کد", "ملی", "تاریخ", "تولد", "متولد", "فرزند", "نام", "پدر",
    "شماره", "تماس", "تلفن", "همراه", "بیمه", "نسخه", "پزشک", "است", "هست",
    # prepositions / particles / common verbs that follow a name in speech
    "به", "از", "با", "در", "را", "که", "و", "تا", "بر", "این", "آن",
    "مراجعه", "کرد", "آمد", "رفت", "گفت", "دارد", "داروخانه", "عزیز",
}

_NAME_AFTER_HONORIFIC = re.compile(
    rf"{_HONORIFIC}[:\s]+([{_FA}]+(?:\s+[{_FA}]+){{0,2}})"
)
_FATHER_NAME = re.compile(rf"(?:نام پدر|فرزند)[:\s]+([{_FA}]+)")
_DOB_LABELLED = re.compile(
    r"(?:تاریخ تولد|تولد|متولد|ت\.ت)[:\s]*([0-9۰-۹]{2,4}[/\-.][0-9۰-۹]{1,2}[/\-.][0-9۰-۹]{1,4})"
)
_DOB_BARE_JALALI = re.compile(
    r"\b(1[34][0-9]{2}[/\-.][0-1]?[0-9][/\-.][0-3]?[0-9])\b"
)
_IRAN_MOBILE = re.compile(r"(?:\+?98|0)?9\d{9}")
_GENDER_MALE = re.compile(r"\b(?:آقای|جناب|مرد|پسر|مذکر)\b")
_GENDER_FEMALE = re.compile(r"\b(?:خانم|سرکار|زن|دختر|مونث|مؤنث)\b")


class IranianIdentityExtractor:
    """Extracts an ExtractedIdentity from transcript or OCR text."""

    def extract(self, text: str, source: str = "transcript") -> ExtractedIdentity:
        if not text:
            return ExtractedIdentity()
        norm = normalize_digits(text)
        ident = ExtractedIdentity()

        self._extract_national_code(norm, ident, source)
        self._extract_name(text, ident, source)          # names need original Persian text
        self._extract_father_name(text, ident)
        self._extract_dob(norm, ident, source)
        self._extract_phone(norm, ident, source)
        self._extract_gender(text, ident)

        ident.confidence = self._score(ident)
        return ident

    # ── individual field extractors ──────────────────────────────────────────

    def _extract_national_code(self, norm: str, ident: ExtractedIdentity, source: str) -> None:
        # Prefer a checksum-valid 10-digit run; fall back to first 10-digit run.
        candidates = NATIONAL_CODE_PATTERN.findall(norm)
        valid = next((c for c in candidates if validate_national_code(c)), None)
        chosen = valid or (candidates[0] if candidates else None)
        if chosen:
            ident.national_code = chosen
            ident.national_code_valid = bool(valid)
            ident.provenance["national_code"] = {
                "source": source, "valid": bool(valid),
            }

    def _extract_name(self, text: str, ident: ExtractedIdentity, source: str) -> None:
        m = _NAME_AFTER_HONORIFIC.search(text)
        if m:
            full = re.sub(r"\s+", " ", m.group(1)).strip()
            # Trim at the first field-label stopword (e.g. "... محمدی کد ملی").
            tokens: list[str] = []
            for tok in full.split(" "):
                if tok in _NAME_STOPWORDS:
                    break
                tokens.append(tok)
            if not tokens:
                return
            full = " ".join(tokens)
            ident.full_name_raw = full
            parts = tokens
            if len(parts) == 1:
                ident.first_name = parts[0]
            else:
                # Persian convention: given name(s) first, family name last.
                ident.first_name = " ".join(parts[:-1])
                ident.last_name = parts[-1]
            ident.provenance["name"] = {"source": source, "raw": full}

    def _extract_father_name(self, text: str, ident: ExtractedIdentity) -> None:
        m = _FATHER_NAME.search(text)
        if m:
            ident.father_name = m.group(1).strip()

    def _extract_dob(self, norm: str, ident: ExtractedIdentity, source: str) -> None:
        m = _DOB_LABELLED.search(norm) or _DOB_BARE_JALALI.search(norm)
        if m:
            jalali_raw = m.group(1)
            ident.dob_jalali = jalali_raw
            ident.dob = jalali_str_to_date(jalali_raw)
            ident.provenance["dob"] = {"source": source, "jalali": jalali_raw}

    def _extract_phone(self, norm: str, ident: ExtractedIdentity, source: str) -> None:
        m = _IRAN_MOBILE.search(norm.replace(" ", ""))
        if m:
            digits = re.sub(r"\D", "", m.group())
            # Normalize to 09xxxxxxxxx
            if digits.startswith("98"):
                digits = "0" + digits[2:]
            elif not digits.startswith("0"):
                digits = "0" + digits
            if len(digits) == 11:
                ident.phone = digits
                ident.provenance["phone"] = {"source": source}

    def _extract_gender(self, text: str, ident: ExtractedIdentity) -> None:
        if _GENDER_FEMALE.search(text):
            ident.gender = "F"
        elif _GENDER_MALE.search(text):
            ident.gender = "M"

    def _score(self, ident: ExtractedIdentity) -> float:
        """Confidence of the strongest available identity signal."""
        if ident.national_code_valid:
            return 0.97
        if ident.national_code:                  # present but checksum-failed
            return 0.55
        score = 0.0
        if ident.last_name:
            score += 0.45
        if ident.first_name:
            score += 0.20
        if ident.dob:
            score += 0.20
        if ident.phone:
            score += 0.15
        return min(score, 0.85)
