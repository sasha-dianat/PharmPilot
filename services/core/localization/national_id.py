"""
Iranian National Code (کد ملی) validation and normalization.
=============================================================
The کد ملی is a 10-digit number that uniquely identifies every Iranian citizen
and is the PRIMARY patient identifier in the default (Iranian) configuration.

Validation rules:
  1. Exactly 10 digits (after Persian→ASCII normalization, zero-padded).
  2. Not all identical digits (e.g. 0000000000, 1111111111 are invalid).
  3. Checksum: for the first 9 digits d0..d8,
        s = Σ d[i] * (10 - i)   for i in 0..8
        r = s mod 11
        check = r            if r < 2
              = 11 - r        otherwise
     The 10th digit d9 must equal `check`.

Also provides:
  - American SSN handling for the optional US configuration.
  - A unified `validate_primary_id` that dispatches on the active identity system.
"""
from __future__ import annotations

import re
from typing import Optional

from services.core.localization.digits import extract_digits


# ── Iranian national code (کد ملی) ────────────────────────────────────────────

def normalize_national_code(raw: str) -> Optional[str]:
    """
    Normalize a raw national code string to a 10-digit ASCII string.
    Handles Persian digits, separators, and missing leading zeros.
    Returns None if it cannot be coerced to 10 digits.
    """
    digits = extract_digits(raw or "")
    if not digits:
        return None
    # Codes from some provinces are stored without leading zeros — left-pad.
    if len(digits) < 10:
        digits = digits.zfill(10)
    if len(digits) != 10:
        return None
    return digits


def validate_national_code(raw: str) -> bool:
    """Validate an Iranian national code (کد ملی) including checksum."""
    code = normalize_national_code(raw)
    if code is None:
        return False
    # Reject all-identical-digit codes (these pass checksum but are invalid)
    if len(set(code)) == 1:
        return False
    check = int(code[9])
    s = sum(int(code[i]) * (10 - i) for i in range(9))
    r = s % 11
    expected = r if r < 2 else 11 - r
    return check == expected


def national_code_checksum(first9: str) -> int:
    """Compute the valid 10th check digit for the first 9 digits."""
    s = sum(int(first9[i]) * (10 - i) for i in range(9))
    r = s % 11
    return r if r < 2 else 11 - r


# ── American SSN (optional US configuration) ──────────────────────────────────

def normalize_ssn(raw: str) -> Optional[str]:
    digits = extract_digits(raw or "")
    return digits if len(digits) == 9 else None


def validate_ssn(raw: str) -> bool:
    """Basic structural SSN validation (area/group/serial not all-zero)."""
    ssn = normalize_ssn(raw)
    if ssn is None:
        return False
    area, group, serial = ssn[:3], ssn[3:5], ssn[5:]
    if area in ("000", "666") or area[0] == "9":
        return False
    if group == "00" or serial == "0000":
        return False
    return True


def ssn_last4(raw: str) -> Optional[str]:
    ssn = normalize_ssn(raw)
    return ssn[-4:] if ssn else None


# ── Unified dispatch on active identity system ────────────────────────────────

def validate_primary_id(raw: str, identity_system: str = "iranian") -> bool:
    """Validate the primary patient identifier for the active configuration."""
    if identity_system == "iranian":
        return validate_national_code(raw)
    elif identity_system == "american":
        return validate_ssn(raw)
    return False


def normalize_primary_id(raw: str, identity_system: str = "iranian") -> Optional[str]:
    """Normalize the primary identifier; returns canonical storable form."""
    if identity_system == "iranian":
        return normalize_national_code(raw)
    elif identity_system == "american":
        # Store only last-4 for SSN (privacy); full SSN never persisted.
        return ssn_last4(raw)
    return None


# Iranian national-code pattern for extraction from free text (10 consecutive digits)
NATIONAL_CODE_PATTERN = re.compile(r"\b(\d{10})\b")
