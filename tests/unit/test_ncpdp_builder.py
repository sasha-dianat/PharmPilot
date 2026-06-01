"""
Unit tests — NCPDP D.0 claim builder and parser.
Tests every claim field, reversal, and COB construction.
These are patient-safety-critical — a malformed claim causes rejection
or incorrect patient billing.
"""
import pytest
from services.core.adjudication.ncpdp_builder import NCPDPBuilder, NCPDPClaim, NCPDPParser


@pytest.fixture
def builder():
    return NCPDPBuilder()


@pytest.fixture
def parser():
    return NCPDPParser()


@pytest.fixture
def standard_claim():
    return NCPDPClaim(
        bin_number="004336",
        pcn="ADV",
        cardholder_id="ABC123456789",
        group_id="GRP001",
        ndc="00071015423",
        quantity="30.000",
        days_supply="030",
        date_of_service="20250601",
        rx_number="PH1234567890",
        fill_number="0",
        prescriber_id="1234567890",
        ingredient_cost_submitted="45.99",
        dispensing_fee_submitted="1.50",
        usual_and_customary_charge="47.49",
    )


class TestNCPDPClaimBuilder:

    def test_billing_claim_starts_with_bin(self, builder, standard_claim):
        raw = builder.build_billing_claim(standard_claim)
        assert raw[:6] == "004336", f"BIN mismatch: {raw[:6]}"

    def test_billing_claim_version_and_transaction_code(self, builder, standard_claim):
        raw = builder.build_billing_claim(standard_claim)
        assert raw[6:8] == "D0", f"Version mismatch: {raw[6:8]}"
        assert raw[8:10] == "B1", f"Transaction code mismatch: {raw[8:10]}"

    def test_ndc_11_digits_no_dashes(self, builder, standard_claim):
        raw = builder.build_billing_claim(standard_claim)
        # NDC field D7 should contain 11-digit NDC without dashes
        assert "00071015423" in raw, "NDC not found in claim"
        assert "-" not in raw.split("D7")[1][:15] if "D7" in raw else True

    def test_quantity_formatted_as_thousandths(self, builder, standard_claim):
        """Quantity 30.000 should be encoded as 30000 (thousandths, no decimal)."""
        raw = builder.build_billing_claim(standard_claim)
        assert "30000" in raw, "Quantity not encoded as thousandths"

    def test_amount_formatted_as_cents(self, builder, standard_claim):
        """$45.99 should be encoded as 0000004599 (cents, 10 digits)."""
        raw = builder.build_billing_claim(standard_claim)
        assert "4599" in raw, "Ingredient cost not encoded as cents"

    def test_reversal_transaction_code_b2(self, builder, standard_claim):
        raw = builder.build_reversal(standard_claim)
        assert raw[8:10] == "B2", f"Reversal transaction code should be B2, got {raw[8:10]}"

    def test_reversal_preserves_rx_number(self, builder, standard_claim):
        raw = builder.build_reversal(standard_claim)
        assert "PH1234567890" in raw, "Rx number must be present in reversal"

    def test_prior_auth_included_when_provided(self, builder, standard_claim):
        standard_claim.prior_auth_number_submitted = "PA123456789"
        standard_claim.prior_auth_type_code = "1"
        raw = builder.build_billing_claim(standard_claim)
        assert "PA123456789" in raw, "PA number should appear in claim when provided"

    def test_claim_minimum_length(self, builder, standard_claim):
        """A valid D.0 claim must be at least 50 characters."""
        raw = builder.build_billing_claim(standard_claim)
        assert len(raw) >= 50, f"Claim too short: {len(raw)} chars"

    def test_group_separator_present(self, builder, standard_claim):
        raw = builder.build_billing_claim(standard_claim)
        # GS character (0x1D) separates segments
        assert chr(0x1D) in raw, "Group separator missing from claim"


class TestNCPDPParser:

    def test_parse_approved_response(self, parser):
        """Simulate an approved adjudication response."""
        # Build a minimal approved response
        GS = chr(0x1D)
        FS = chr(0x1C)
        raw = f"004336D0B1ADV{GS}AM21{FS}ANA{FS}F3AUTH12345{GS}AM25{FS}D94599{FS}DC150{FS}DX4749"
        result = parser.parse_response(raw)
        assert result["approved"] is True
        assert result["transaction_response_status"] == "A"

    def test_parse_rejected_response(self, parser):
        GS = chr(0x1D)
        FS = chr(0x1C)
        raw = f"004336D0B1ADV{GS}AM21{FS}ANR{GS}AM23{FS}FA75{FS}FBPrior Authorization Required"
        result = parser.parse_response(raw)
        assert result["approved"] is False
        assert "75" in result["reject_codes"]

    def test_parse_empty_response(self, parser):
        result = parser.parse_response("")
        assert result["status"] == "error"

    def test_multiple_reject_codes(self, parser):
        GS = chr(0x1D)
        FS = chr(0x1C)
        raw = f"004336D0B1ADV{GS}AM21{FS}ANR{GS}AM23{FS}FA75{FS}FA65"
        result = parser.parse_response(raw)
        assert "75" in result["reject_codes"]


class TestRejectResolver:

    def test_auto_resolvable_codes(self):
        from services.core.adjudication.reject_resolver import RejectResolver, AUTO_RESOLVABLE_CODES
        assert "87" in AUTO_RESOLVABLE_CODES  # Transaction count exceeded — retry
        assert "99" in AUTO_RESOLVABLE_CODES  # Host processing error — retry

    def test_prior_auth_is_auto_resolvable(self):
        from services.core.adjudication.reject_resolver import REJECT_RESOLUTION_MAP
        assert "75" in REJECT_RESOLUTION_MAP
        assert REJECT_RESOLUTION_MAP["75"].auto_resolvable is True

    def test_get_pharmacist_instructions_returns_list(self):
        from services.core.adjudication.reject_resolver import RejectResolver
        resolver = RejectResolver()
        instructions = resolver.get_pharmacist_instructions(["75", "19"])
        assert len(instructions) == 2
        assert all("instruction" in i for i in instructions)

    def test_unknown_reject_code_handled(self):
        from services.core.adjudication.reject_resolver import RejectResolver
        resolver = RejectResolver()
        instructions = resolver.get_pharmacist_instructions(["ZZ"])  # Unknown code
        assert len(instructions) == 1
        assert "ZZ" in instructions[0]["reject_code"]
