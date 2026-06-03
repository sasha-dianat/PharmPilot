"""
Unit tests for the Iranian identity layer:
  - Jalali calendar conversion (historical reference dates + round-trip)
  - National code (کد ملی) checksum validation
  - Persian/Arabic digit normalization
  - Iranian identity extraction from transcript + Rx OCR
  - Insurance registry family aggregation + person-linking
"""
import asyncio
from datetime import date

import pytest

from services.core.localization.jalali import (
    gregorian_to_jalali, jalali_to_gregorian, date_to_jalali_str,
    jalali_str_to_date, age_from_dob,
)
from services.core.localization.national_id import (
    validate_national_code, normalize_national_code, national_code_checksum,
    validate_ssn,
)
from services.core.localization.digits import normalize_digits, to_persian_digits
from services.core.localization.iranian_extract import IranianIdentityExtractor


# ── Jalali calendar ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("greg,jalali", [
    ((1979, 2, 11), (1357, 11, 22)),   # Islamic Revolution — 22 Bahman 1357
    ((2024, 3, 20), (1403, 1, 1)),     # Nowruz 1403
    ((2000, 1, 1), (1378, 10, 11)),    # Y2K
])
def test_gregorian_to_jalali_reference_dates(greg, jalali):
    assert gregorian_to_jalali(*greg) == jalali


@pytest.mark.parametrize("greg", [(1979, 2, 11), (2024, 3, 20), (2000, 1, 1), (2026, 6, 3)])
def test_jalali_roundtrip(greg):
    j = gregorian_to_jalali(*greg)
    assert jalali_to_gregorian(*j) == greg


def test_jalali_string_helpers():
    d = date(1979, 2, 11)
    assert date_to_jalali_str(d) == "1357/11/22"
    assert jalali_str_to_date("۱۳۵۷/۱۱/۲۲") == d  # Persian digits accepted


def test_age_is_calendar_agnostic():
    assert age_from_dob(date(2000, 1, 1), today=date(2026, 6, 3)) == 26


# ── National code (کد ملی) ────────────────────────────────────────────────────

@pytest.mark.parametrize("code,valid", [
    ("0499370899", True),
    ("0012345679", True),
    ("۰۴۹۹۳۷۰۸۹۹", True),   # Persian digits
    ("1111111111", False),  # all-identical
    ("1234567890", False),  # bad checksum
    ("12345", False),       # too short with bad checksum after padding
])
def test_national_code_validation(code, valid):
    assert validate_national_code(code) is valid


def test_national_code_checksum_generation():
    first9 = "049937089"
    check = national_code_checksum(first9)
    assert validate_national_code(first9 + str(check))


def test_normalize_national_code_pads():
    assert normalize_national_code("۴۹۹۳۷۰۸۹۹") is not None  # 9 digits → padded to 10


# ── Digit normalization ───────────────────────────────────────────────────────

def test_digit_normalization():
    assert normalize_digits("۰۹۱۲۳۴۵۶۷۸۹") == "09123456789"
    assert normalize_digits("٠١٢٣") == "0123"  # Arabic-Indic
    assert to_persian_digits("1403") == "۱۴۰۳"


# ── Identity extraction ───────────────────────────────────────────────────────

def test_extract_from_transcript():
    ex = IranianIdentityExtractor()
    t = "سلام آقای رضا محمدی، کد ملی شما ۰۴۹۹۳۷۰۸۹۹ هست؟ تاریخ تولد ۱۳۶۵/۰۳/۱۵"
    r = ex.extract(t, "transcript")
    assert r.national_code == "0499370899"
    assert r.national_code_valid is True
    assert r.first_name == "رضا"
    assert r.last_name == "محمدی"
    assert r.gender == "M"
    assert r.dob == date(1986, 6, 5)
    assert r.confidence >= 0.95


def test_extract_from_prescription_ocr():
    ex = IranianIdentityExtractor()
    rx = "نام بیمار: فاطمه حسینی  فرزند: علی  کد ملی: ۲۲۹۸۷۶۵۴۳۱  متولد ۱۳۴۸/۱۱/۲۲"
    r = ex.extract(rx, "ocr")
    assert r.first_name == "فاطمه"
    assert r.last_name == "حسینی"
    assert r.father_name == "علی"
    assert r.national_code_valid is True


def test_extract_name_stops_at_field_labels():
    ex = IranianIdentityExtractor()
    r = ex.extract("خانم مریم رضایی به داروخانه مراجعه کرد", "transcript")
    assert r.first_name == "مریم"
    assert r.last_name == "رضایی"
    assert r.gender == "F"


# ── Insurance registry + person linking ───────────────────────────────────────

def test_insurance_family_aggregation_links_persons():
    from services.integrations.iranian_insurance.registry import IranianInsuranceRegistry
    from services.integrations.iranian_insurance.base import InsuranceOrg
    from services.integrations.iranian_insurance.adapters import (
        SalamatAdapter, TaminAdapter, ArmedForcesAdapter, SupplementaryAdapter)

    reg = IranianInsuranceRegistry({
        InsuranceOrg.SALAMAT: SalamatAdapter(),
        InsuranceOrg.TAMIN: TaminAdapter(),
        InsuranceOrg.ARMED_FORCES: ArmedForcesAdapter(),
        InsuranceOrg.SUPPLEMENTARY: SupplementaryAdapter(),
    })
    agg = asyncio.run(reg.aggregate_coverage("0499370899"))
    assert agg.primary is not None
    assert agg.primary.org == InsuranceOrg.SALAMAT
    # at least the principal is present; linked persons all have valid national codes
    assert len(agg.linked_persons) >= 1
    for m in agg.linked_persons:
        assert validate_national_code(m.national_code)
    # principal's own code excluded from linked_national_codes
    assert "0499370899" not in agg.linked_national_codes


def test_american_mode_uses_ssn():
    # SSN validation path for the optional US configuration
    assert validate_ssn("123456789") is True
    assert validate_ssn("000123456") is False  # area 000 invalid
