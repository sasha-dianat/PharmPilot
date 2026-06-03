"""
Iranian insurance / social-security integration — base contracts.
==================================================================
Iran's coverage is dominated by public organisations that all key on the
national code (کد ملی) rather than a card/member-id the way US PBMs do:

  - بیمه سلامت ایران       Iran Health Insurance Organization (Salamat)
  - سازمان تأمین اجتماعی   Social Security Organization (Tamin Ejtemaei)
  - بیمه نیروهای مسلح      Armed Forces Medical Services (Sazman-e Bimه Niروhaye Mosallah)
  - بیمه‌های تکمیلی        Supplementary private insurers (Dana, Alborz, Asia, ...)

Unlike openFDA, none of these expose a public REST API — production access
requires an organisational contract + credentials (سامانه استحقاق‌سنجی /
eligibility-inquiry web service, usually SOAP or a provincial gateway).

This module therefore defines a clean adapter interface plus a SANDBOX
implementation that returns deterministic mock data so the whole identity
and adjudication pipeline is testable end-to-end today. Swapping in a live
adapter is a credential + endpoint change, nothing structural.

PHI discipline: national code is sensitive. Never log it in full — adapters
mask to first-3 + last-2 when logging.
"""
from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional

from services.core.localization.national_id import (
    normalize_national_code,
    validate_national_code,
)

logger = logging.getLogger(__name__)


class InsuranceOrg(str, Enum):
    SALAMAT = "salamat"            # بیمه سلامت
    TAMIN = "tamin"               # تأمین اجتماعی
    ARMED_FORCES = "armed_forces" # نیروهای مسلح
    SUPPLEMENTARY = "supplementary"  # تکمیلی (private)


class EligibilityStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    SUSPENDED = "suspended"
    NOT_FOUND = "not_found"
    ERROR = "error"


def mask_national_code(code: Optional[str]) -> str:
    """Mask a national code for safe logging: 045****99."""
    if not code or len(code) < 5:
        return "***"
    return f"{code[:3]}****{code[-2:]}"


@dataclass
class InsuranceMember:
    """A person's coverage record under one Iranian insurer."""
    national_code: str
    org: InsuranceOrg
    status: EligibilityStatus
    # Demographics returned by the eligibility service (authoritative source of truth)
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    father_name: Optional[str] = None
    date_of_birth: Optional[date] = None        # Gregorian (converted from Jalali at the edge)
    date_of_birth_jalali: Optional[str] = None  # As returned by the portal
    gender: Optional[str] = None                 # M | F
    # Coverage
    policy_number: Optional[str] = None
    coverage_book_number: Optional[str] = None   # شماره دفترچه (legacy book) if any
    relationship_to_principal: Optional[str] = None  # principal | spouse | child | parent
    principal_national_code: Optional[str] = None    # head-of-family code if dependent
    province: Optional[str] = None
    valid_until: Optional[date] = None
    copay_percent: Optional[float] = None        # patient share %
    raw: dict = field(default_factory=dict)      # raw provider payload for audit


@dataclass
class FamilyCoverage:
    """All persons covered under the same principal — the basis for person-linking."""
    principal_national_code: str
    org: InsuranceOrg
    members: list[InsuranceMember] = field(default_factory=list)


class IranianInsuranceAdapter(abc.ABC):
    """Adapter contract for an Iranian insurance / social-security organisation."""

    org: InsuranceOrg

    @abc.abstractmethod
    async def check_eligibility(self, national_code: str) -> InsuranceMember:
        """استحقاق‌سنجی — look up a person's coverage by national code."""

    @abc.abstractmethod
    async def get_family_coverage(self, national_code: str) -> FamilyCoverage:
        """
        Return everyone covered under the same principal as `national_code`.
        This is the authoritative source for linking 2+ persons (spouse,
        children, dependent parents) without the pharmacist entering anything.
        """

    def _require_valid_code(self, national_code: str) -> str:
        code = normalize_national_code(national_code)
        if code is None or not validate_national_code(code):
            raise ValueError(f"Invalid national code {mask_national_code(code)}")
        return code
