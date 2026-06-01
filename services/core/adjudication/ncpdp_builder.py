"""
NCPDP D.0 claim transaction builder.
Constructs properly formatted NCPDP D.0 fixed-position records.
Handles B1 (billing), B2 (reversal), and COB secondary claims.
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class NCPDPClaim:
    """All fields needed to build a D.0 billing transaction."""
    # Routing
    bin_number: str           # 6 chars, field 401-BZ
    pcn: str                  # Up to 10 chars, field 402-D2
    # Transaction header
    transaction_code: str = "B1"  # B1=billing, B2=reversal
    version: str = "D0"
    # Group 1 — Insurance
    group_id: str = ""        # field 301-C1
    cardholder_id: str = ""   # field 302-C2
    person_code: str = "01"   # field 303-C3
    # Group 2 — Patient
    patient_dob: str = ""     # field 304-C4 CCYYMMDD
    patient_gender: str = "1" # 1=M, 2=F, 0=unknown
    patient_first_name: str = ""
    patient_last_name: str = ""
    # Group 3 — Insurance (additional)
    other_coverage_code: str = "0"  # field 308-C8: 0=not specified
    # Group 4 — Claim
    date_of_service: str = ""       # field 401-BZ CCYYMMDD
    rx_number: str = ""             # field 402-D2
    fill_number: str = "0"          # field 403-D3 0=new, 1-99=refill
    days_supply: str = "030"        # field 405-D5
    compound_code: str = "1"        # 1=not compound
    daw_code: str = "0"             # field 408-D8
    ndc: str = ""                   # field 407-D7 11-digit no dashes
    quantity: str = ""              # field 442-E7 7d2f (digits.2decimals)
    pharmacist_id: str = ""         # field 411-DB
    prescriber_id: str = ""         # field 411-DB (NPI)
    # Group 5 — Prescriber
    prescriber_id_qualifier: str = "01"  # 01=NPI
    # Group 6 — COB
    other_payer_amount_paid: str = ""    # For secondary claim
    other_payer_reject_count: str = ""
    # Group 7 — Workers Comp (omit for retail)
    # Group 8 — DUR / PPS
    reason_for_service_code: str = ""   # DUR reason
    professional_service_code: str = ""
    result_of_service_code: str = ""
    # Group 9 — Pricing
    ingredient_cost_submitted: str = ""  # field 409-D9 9d2f
    dispensing_fee_submitted: str = ""   # field 412-DC 9d2f
    usual_and_customary_charge: str = "" # field 426-DQ
    gross_amount_due: str = ""          # field 430-DU
    # Group 10 — COB (secondary)
    prior_auth_type_code: str = ""      # field 461-EU
    prior_auth_number_submitted: str = ""  # field 462-EV
    # Group 11 — Additional
    submission_clarification_code: str = ""  # field 420-DK


class NCPDPBuilder:
    """
    Builds NCPDP D.0 fixed-position claim strings.
    Uses proper field separators and group markers.
    """
    FIELD_SEPARATOR = chr(0x1C)   # FS — field separator
    GROUP_SEPARATOR = chr(0x1D)   # GS — group separator
    RECORD_SEPARATOR = chr(0x1E)  # RS — segment separator (not used in D0)
    SEGMENT_SEPARATOR = chr(0x1E)

    def build_billing_claim(self, claim: NCPDPClaim) -> str:
        """Build a complete D.0 B1 billing transaction."""
        parts = []

        # Transaction header (fixed, no separators)
        parts.append(self._header(claim))

        # Group 1 — Insurance
        parts.append(self._group1(claim))
        # Group 2 — Patient
        parts.append(self._group2(claim))
        # Group 4 — Claim (primary claim segment)
        parts.append(self._group4(claim))
        # Group 5 — Prescriber
        parts.append(self._group5(claim))
        # Group 7 — Pricing
        parts.append(self._group7(claim))

        # Optional groups
        if claim.prior_auth_number_submitted:
            parts.append(self._group_prior_auth(claim))

        if claim.other_payer_amount_paid:
            parts.append(self._group_cob(claim))

        return "".join(parts)

    def build_reversal(self, claim: NCPDPClaim) -> str:
        """Build a B2 reversal transaction."""
        modified = NCPDPClaim(**{
            **claim.__dict__,
            "transaction_code": "B2",
        })
        # Reversal only needs header + claim group
        return self._header(modified) + self._group1(modified) + self._group4_reversal(modified)

    def _header(self, claim: NCPDPClaim) -> str:
        # NCPDP D0 header: BIN(6) + Version(2) + TransactionCode(2) + ProcessorControlNumber(10)
        bin_ = claim.bin_number.ljust(6)[:6]
        version = claim.version[:2].ljust(2)
        tc = claim.transaction_code[:2].ljust(2)
        pcn = claim.pcn.ljust(10)[:10]
        return f"{bin_}{version}{tc}{pcn}"

    def _fs(self, field_id: str, value: str) -> str:
        """Format a field: field_id + value."""
        return f"{self.FIELD_SEPARATOR}{field_id}{value}"

    def _group1(self, claim: NCPDPClaim) -> str:
        """AM01 — Insurance segment."""
        fields = f"{self.GROUP_SEPARATOR}AM01"
        fields += self._fs("C2", claim.cardholder_id[:20])
        fields += self._fs("C1", claim.group_id[:15])
        fields += self._fs("C3", claim.person_code[:3])
        return fields

    def _group2(self, claim: NCPDPClaim) -> str:
        """AM02 — Patient segment."""
        fields = f"{self.GROUP_SEPARATOR}AM02"
        if claim.patient_dob:
            fields += self._fs("C4", claim.patient_dob[:8])
        if claim.patient_first_name:
            fields += self._fs("CC", claim.patient_first_name[:12])
        if claim.patient_last_name:
            fields += self._fs("CB", claim.patient_last_name[:15])
        return fields

    def _group4(self, claim: NCPDPClaim) -> str:
        """AM04 — Claim segment."""
        fields = f"{self.GROUP_SEPARATOR}AM04"
        fields += self._fs("D2", claim.rx_number.ljust(12)[:12])
        fields += self._fs("D3", claim.fill_number[:2])
        fields += self._fs("D7", claim.ndc[:11])      # NDC-11, no dashes
        fields += self._fs("E7", self._format_quantity(claim.quantity))
        fields += self._fs("D5", claim.days_supply.zfill(3)[:3])
        fields += self._fs("BZ", claim.date_of_service[:8])
        fields += self._fs("D8", claim.daw_code[:1])
        if claim.submission_clarification_code:
            fields += self._fs("DK", claim.submission_clarification_code[:2])
        return fields

    def _group4_reversal(self, claim: NCPDPClaim) -> str:
        """Minimal claim segment for B2 reversal."""
        fields = f"{self.GROUP_SEPARATOR}AM04"
        fields += self._fs("D2", claim.rx_number.ljust(12)[:12])
        fields += self._fs("D3", claim.fill_number[:2])
        fields += self._fs("BZ", claim.date_of_service[:8])
        return fields

    def _group5(self, claim: NCPDPClaim) -> str:
        """AM05 — Prescriber segment."""
        fields = f"{self.GROUP_SEPARATOR}AM05"
        fields += self._fs("DB", claim.prescriber_id_qualifier[:2])
        fields += self._fs("DR", claim.prescriber_id[:15])   # NPI
        return fields

    def _group7(self, claim: NCPDPClaim) -> str:
        """AM07 — Pricing segment."""
        fields = f"{self.GROUP_SEPARATOR}AM07"
        if claim.ingredient_cost_submitted:
            fields += self._fs("D9", self._format_amount(claim.ingredient_cost_submitted))
        if claim.dispensing_fee_submitted:
            fields += self._fs("DC", self._format_amount(claim.dispensing_fee_submitted))
        if claim.usual_and_customary_charge:
            fields += self._fs("DQ", self._format_amount(claim.usual_and_customary_charge))
        if claim.gross_amount_due:
            fields += self._fs("DU", self._format_amount(claim.gross_amount_due))
        return fields

    def _group_prior_auth(self, claim: NCPDPClaim) -> str:
        """AM09 — Prior authorization."""
        fields = f"{self.GROUP_SEPARATOR}AM09"
        fields += self._fs("EU", claim.prior_auth_type_code[:2])
        fields += self._fs("EV", claim.prior_auth_number_submitted[:20])
        return fields

    def _group_cob(self, claim: NCPDPClaim) -> str:
        """AM03 — COB/Other Payments."""
        fields = f"{self.GROUP_SEPARATOR}AM03"
        fields += self._fs("E8", claim.other_payer_amount_paid)
        return fields

    @staticmethod
    def _format_amount(amount: str) -> str:
        """Format as NCPDP amount: up to 9 digits + 2 decimal places, no decimal point."""
        try:
            val = float(amount)
            cents = int(round(val * 100))
            return str(cents).zfill(10)
        except (ValueError, TypeError):
            return "0" * 10

    @staticmethod
    def _format_quantity(quantity: str) -> str:
        """Format as NCPDP quantity: 7 digits + 3 decimal places, no decimal point."""
        try:
            val = float(quantity)
            thousandths = int(round(val * 1000))
            return str(thousandths).zfill(10)
        except (ValueError, TypeError):
            return "0" * 10


class NCPDPParser:
    """Parse NCPDP D.0 response transactions."""

    FIELD_SEPARATOR = chr(0x1C)
    GROUP_SEPARATOR = chr(0x1D)

    def parse_response(self, raw: str) -> dict:
        """Parse a PBM adjudication response."""
        if not raw:
            return {"status": "error", "message": "Empty response"}

        result = {
            "transaction_code": raw[6:8].strip() if len(raw) > 8 else "",
            "reject_codes": [],
            "reject_messages": [],
            "approved": False,
        }

        # Split into groups
        groups = raw.split(self.GROUP_SEPARATOR)
        for group in groups:
            if not group:
                continue
            if group.startswith("AM21"):  # Header response
                result.update(self._parse_header_response(group))
            elif group.startswith("AM22"):  # Message group
                result.update(self._parse_message_group(group))
            elif group.startswith("AM23"):  # Reject group
                result.update(self._parse_reject_group(group))
            elif group.startswith("AM25"):  # Pricing response
                result.update(self._parse_pricing_response(group))

        result["approved"] = result.get("transaction_response_status") == "A"
        return result

    def _parse_header_response(self, group: str) -> dict:
        fields = group.split(self.FIELD_SEPARATOR)
        parsed = {}
        for f in fields:
            if f.startswith("AN"):
                parsed["transaction_response_status"] = f[2:3]  # A=approved, R=rejected
            elif f.startswith("F3"):
                parsed["authorization_number"] = f[2:].strip()
        return parsed

    def _parse_reject_group(self, group: str) -> dict:
        fields = group.split(self.FIELD_SEPARATOR)
        reject_codes = []
        reject_messages = []
        for f in fields:
            if f.startswith("FA"):
                reject_codes.append(f[2:4].strip())
            elif f.startswith("FB"):
                reject_messages.append(f[2:].strip())
        return {"reject_codes": reject_codes, "reject_messages": reject_messages}

    def _parse_message_group(self, group: str) -> dict:
        fields = group.split(self.FIELD_SEPARATOR)
        for f in fields:
            if f.startswith("FQ"):
                return {"additional_message": f[2:].strip()}
        return {}

    def _parse_pricing_response(self, group: str) -> dict:
        fields = group.split(self.FIELD_SEPARATOR)
        pricing = {}
        field_map = {
            "D9": "ingredient_cost_paid",
            "DC": "dispensing_fee_paid",
            "DX": "total_amount_paid",
            "DY": "patient_pay_amount",
            "NB": "basis_of_reimbursement_determination",
        }
        for f in fields:
            if len(f) >= 2:
                key = f[:2]
                val = f[2:]
                if key in field_map:
                    try:
                        pricing[field_map[key]] = float(val) / 100
                    except ValueError:
                        pricing[field_map[key]] = val
        return pricing
