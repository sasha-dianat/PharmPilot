"""
Unit tests — DEA number validation.
A DEA number with an invalid check digit must be caught before
a controlled substance Rx is dispensed.
"""
import pytest
from services.integrations.surescripts.eprescribing import DEAValidator


class TestDEACheckDigit:

    VALID_DEA_NUMBERS = [
        "AB1234563",   # Standard format, valid check digit
        "BC2345672",
        "MJ1234563",   # Mid-level practitioner prefix
        "FP9876547",
    ]

    INVALID_DEA_NUMBERS = [
        "AB1234560",  # Wrong check digit (should be 3)
        "AB1234561",
        "AB1234562",
        "AB1234564",
        "AB1234565",
        "AB1234566",
        "AB1234567",
        "AB1234568",
        "AB1234569",
    ]

    @pytest.mark.parametrize("dea", VALID_DEA_NUMBERS)
    def test_valid_dea_accepted(self, dea):
        valid, msg = DEAValidator.validate(dea)
        assert valid, f"Valid DEA {dea} was rejected: {msg}"

    @pytest.mark.parametrize("dea", INVALID_DEA_NUMBERS)
    def test_invalid_check_digit_rejected(self, dea):
        valid, msg = DEAValidator.validate(dea)
        assert not valid, f"Invalid DEA {dea} was accepted (should be rejected)"

    def test_empty_string_rejected(self):
        valid, msg = DEAValidator.validate("")
        assert not valid

    def test_wrong_format_rejected(self):
        valid, msg = DEAValidator.validate("1234567890")  # No letter prefix
        assert not valid

    def test_too_short_rejected(self):
        valid, msg = DEAValidator.validate("AB123")
        assert not valid

    def test_schedule_authorization_for_full_practitioner(self):
        schedules = DEAValidator.extract_schedule_authorization("AB1234563")
        assert "CII" in schedules
        assert "CIII" in schedules
        assert "CIV" in schedules
        assert "CV" in schedules

    def test_mid_level_limited_to_schedule_iii_v(self):
        schedules = DEAValidator.extract_schedule_authorization("MB1234563")
        assert "CII" not in schedules, "Mid-level practitioners cannot prescribe Schedule II"
        assert "CIII" in schedules

    def test_missing_dea_returns_empty_schedules(self):
        schedules = DEAValidator.extract_schedule_authorization("")
        assert schedules == []
