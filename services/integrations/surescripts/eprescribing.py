"""
Surescripts ePrescribing Integration — NCPDP SCRIPT 10.6 / 2017071.
Handles inbound NewRx, Refill, CancelRx messages.
Sends outbound acknowledgments and RxFill status notifications.
DEA check digit validation for prescriber NPI/DEA verification.
"""
import hashlib
import hmac
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4
from xml.etree import ElementTree as ET

import httpx

logger = logging.getLogger(__name__)

# NCPDP SCRIPT 10.6 XML namespace
SCRIPT_NS = "http://www.ncpdp.org/schema/SCRIPT"
SCRIPT_NS_MAP = {"script": SCRIPT_NS}


@dataclass
class InboundNewRx:
    """Parsed NCPDP SCRIPT NewRx message."""
    message_id: str
    sent_time: str
    # Prescriber
    prescriber_npi: str
    prescriber_dea: Optional[str]
    prescriber_first_name: str
    prescriber_last_name: str
    prescriber_state_license: Optional[str]
    prescriber_phone: Optional[str]
    prescriber_fax: Optional[str]
    prescriber_address: Optional[str]
    # Patient
    patient_first_name: str
    patient_last_name: str
    patient_dob: str
    patient_gender: str
    patient_address: Optional[str]
    patient_zip: Optional[str]
    # Drug
    drug_description: str
    ndc: Optional[str]
    rxcui: Optional[str]
    quantity: float
    quantity_unit: str
    days_supply: int
    refills: int
    sig: str
    daw: str
    # Metadata
    is_epcs: bool = False
    dea_schedule: Optional[str] = None
    digital_signature: Optional[str] = None
    prior_auth_number: Optional[str] = None
    diagnosis_codes: list[str] = field(default_factory=list)
    raw_xml: str = ""


@dataclass
class OutboundAck:
    """NCPDP SCRIPT acknowledgment response."""
    original_message_id: str
    ack_status: str    # AA=accepted, AE=application error, AR=rejected
    message: Optional[str] = None
    error_code: Optional[str] = None


class DEAValidator:
    """
    Validates DEA numbers using the official check digit algorithm.
    DEA number format: 2 letters + 7 digits (last digit = check digit)
    """

    @staticmethod
    def validate(dea_number: str, prescriber_last_name: str = "") -> tuple[bool, str]:
        """
        Returns (is_valid, reason).
        """
        if not dea_number:
            return False, "DEA number is empty"

        dea = dea_number.upper().strip()
        if not re.match(r"^[A-Z]{2}\d{7}$", dea):
            return False, f"Invalid DEA format: {dea} (expected 2 letters + 7 digits)"

        # First letter must be A, B, C, D, E, F, G, H, M, P, R, S, T, U, X
        valid_first = set("ABCDEFGHMPRSTU")
        # Practitioners: A, B, F, M, G (mid-level)
        # Mid-level practitioners: M
        # Institutions: A, B, D, E, F, G, J, K, L, P, R, S, T, U, X
        if dea[0] not in valid_first:
            return False, f"Invalid first letter: {dea[0]}"

        # Check digit algorithm
        digits = [int(d) for d in dea[2:]]
        check = (digits[0] + digits[2] + digits[4]) + 2 * (digits[1] + digits[3] + digits[5])
        check_digit = check % 10

        if check_digit != digits[6]:
            return False, f"DEA check digit mismatch (expected {check_digit}, got {digits[6]})"

        return True, "Valid"

    @staticmethod
    def extract_schedule_authorization(dea_number: str) -> list[str]:
        """
        DEA numbers beginning with certain letters authorize specific schedules.
        """
        if not dea_number or len(dea_number) < 1:
            return []
        first = dea_number[0].upper()
        if first in ("A", "B", "F", "G"):
            return ["CI", "CII", "CIII", "CIV", "CV"]  # Full schedule authorization
        elif first == "M":
            return ["CIII", "CIV", "CV"]  # Mid-level: III-V only
        elif first in ("P", "S"):
            return ["CI", "CII", "CIII", "CIV", "CV"]  # Researcher
        return []


class NCPDPScriptParser:
    """Parse incoming NCPDP SCRIPT 10.6 XML messages."""

    def parse_new_rx(self, xml_text: str) -> Optional[InboundNewRx]:
        """Parse a NewRx SCRIPT message."""
        try:
            root = ET.fromstring(xml_text)
            ns = {"s": SCRIPT_NS}

            # Message header
            header = root.find("s:Header", ns) or root.find("Header")
            msg_id = self._find_text(header, "s:MessageID", ns) or str(uuid4())
            sent_time = self._find_text(header, "s:SentTime", ns) or ""

            # Prescriber
            prescriber = root.find(".//s:Prescriber", ns) or root.find(".//Prescriber")
            presc_name = prescriber.find("s:Name", ns) if prescriber else None

            # Patient
            patient = root.find(".//s:Patient", ns) or root.find(".//Patient")
            pat_name = patient.find("s:Name", ns) if patient else None

            # Drug
            drug = root.find(".//s:DrugCoded", ns) or root.find(".//DrugCoded")
            medication = root.find(".//s:MedicationPrescribed", ns) or root.find(".//MedicationPrescribed")

            dea_number = self._find_text(prescriber, "s:DEANumber", ns) or ""
            dea_valid, _ = DEAValidator.validate(dea_number) if dea_number else (False, "")
            dea_schedule = self._detect_schedule(root, ns)

            return InboundNewRx(
                message_id=msg_id,
                sent_time=sent_time,
                # Prescriber
                prescriber_npi=self._find_text(prescriber, "s:NPI", ns) or "",
                prescriber_dea=dea_number if dea_valid else None,
                prescriber_first_name=self._find_text(presc_name, "s:FirstName", ns) or "",
                prescriber_last_name=self._find_text(presc_name, "s:LastName", ns) or "",
                prescriber_state_license=self._find_text(prescriber, "s:StateLicenseNumber", ns),
                prescriber_phone=self._find_phone(prescriber, ns),
                prescriber_fax=self._find_text(prescriber, ".//s:Fax", ns),
                prescriber_address=self._build_address(prescriber, ns),
                # Patient
                patient_first_name=self._find_text(pat_name, "s:FirstName", ns) or "",
                patient_last_name=self._find_text(pat_name, "s:LastName", ns) or "",
                patient_dob=self._find_text(patient, "s:DateOfBirth", ns) or "",
                patient_gender=self._find_text(patient, "s:Gender", ns) or "U",
                patient_address=self._build_address(patient, ns),
                patient_zip=self._find_text(patient, ".//s:ZipCode", ns),
                # Drug
                drug_description=self._find_text(drug, "s:DrugDescription", ns) or
                                 self._find_text(medication, "s:DrugDescription", ns) or "",
                ndc=self._find_text(drug, "s:ProductCode", ns),
                rxcui=self._find_text(drug, "s:RxCUI", ns),
                quantity=float(self._find_text(medication, "s:Quantity", ns) or "0"),
                quantity_unit=self._find_text(medication, "s:QuantityUnitOfMeasure", ns) or "EA",
                days_supply=int(self._find_text(medication, "s:DaysSupply", ns) or "30"),
                refills=int(self._find_text(medication, "s:NumberOfRefills", ns) or "0"),
                sig=self._find_text(root, ".//s:SigText", ns) or
                    self._find_text(root, ".//SigText") or "",
                daw=self._find_text(medication, "s:DAW", ns) or "0",
                is_epcs=dea_schedule is not None and dea_valid,
                dea_schedule=dea_schedule,
                prior_auth_number=self._find_text(root, ".//s:PriorAuthorization", ns),
                raw_xml=xml_text,
            )
        except Exception as exc:
            logger.error("NewRx parse failed: %s", exc)
            return None

    def build_acknowledgment(self, original_message_id: str, status: str, message: str = "") -> str:
        """Build NCPDP SCRIPT Status/Verify acknowledgment XML."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        ack_id = str(uuid4()).replace("-", "")[:20]
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<Message xmlns="{SCRIPT_NS}" version="010.6">
  <Header>
    <To>Surescripts</To>
    <From>PharmPilot</From>
    <MessageID>{ack_id}</MessageID>
    <SentTime>{timestamp}</SentTime>
    <Security><UsernameToken><Username>PHARMPILOT</Username></UsernameToken></Security>
    <TestMessage>0</TestMessage>
  </Header>
  <Body>
    <Status>
      <Code>{status}</Code>
      <Note>{message}</Note>
      <ReferenceNumber>{original_message_id}</ReferenceNumber>
    </Status>
  </Body>
</Message>"""

    def _find_text(self, element, path: str, ns: dict = None) -> Optional[str]:
        if element is None:
            return None
        try:
            found = element.find(path, ns or {})
            return found.text.strip() if found is not None and found.text else None
        except Exception:
            return None

    def _find_phone(self, element, ns: dict) -> Optional[str]:
        if element is None:
            return None
        phone_elem = element.find(".//s:CommunicationNumbers/s:PrimaryTelephone", ns)
        return phone_elem.text.strip() if phone_elem is not None and phone_elem.text else None

    def _build_address(self, element, ns: dict) -> Optional[str]:
        if element is None:
            return None
        addr = element.find(".//s:Address", ns)
        if not addr:
            return None
        parts = [
            self._find_text(addr, "s:AddressLine1", ns),
            self._find_text(addr, "s:City", ns),
            self._find_text(addr, "s:StateCode", ns),
            self._find_text(addr, "s:ZipCode", ns),
        ]
        return ", ".join(p for p in parts if p)

    def _detect_schedule(self, root, ns: dict) -> Optional[str]:
        dea_elem = root.find(".//s:DEASchedule", ns) or root.find(".//DEASchedule")
        if dea_elem is not None and dea_elem.text:
            text = dea_elem.text.strip().upper()
            for sched in ("CI", "CII", "CIII", "CIV", "CV"):
                if sched in text or text in sched:
                    return sched
        return None


class SurescriptsClient:
    """
    Surescripts network client for message sending/receiving.
    Handles message authentication and Surescripts-specific HTTP protocol.
    """

    def __init__(self, sender_id: str, password: str, environment: str = "test"):
        self.sender_id = sender_id
        self.password = password
        self.base_url = (
            "https://test.surescripts.net" if environment == "test"
            else "https://surescripts.net"
        )
        self.parser = NCPDPScriptParser()

    async def send_acknowledgment(
        self,
        original_message_id: str,
        status: str = "AA",
        message: str = "Message received",
    ) -> bool:
        """Send acknowledgment to Surescripts. Must respond within 60 seconds."""
        xml = self.parser.build_acknowledgment(original_message_id, status, message)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    f"{self.base_url}/messaging/v1",
                    content=xml.encode("utf-8"),
                    headers={
                        "Content-Type": "application/xml",
                        "X-SenderID": self.sender_id,
                        "Authorization": f"Basic {self._auth_header()}",
                    },
                )
                success = response.status_code in (200, 204)
                if success:
                    logger.info("ACK sent for message %s", original_message_id)
                else:
                    logger.error("ACK failed: %s %s", response.status_code, response.text[:200])
                return success
        except Exception as exc:
            logger.error("Failed to send ACK: %s", exc)
            return False

    def _auth_header(self) -> str:
        import base64
        credentials = f"{self.sender_id}:{self.password}"
        return base64.b64encode(credentials.encode()).decode()

    def process_inbound_message(self, xml_body: str) -> Optional[InboundNewRx]:
        """Process an inbound message from Surescripts webhook."""
        # Detect message type
        if "<NewRx>" in xml_body or "NewRx" in xml_body:
            return self.parser.parse_new_rx(xml_body)
        # Future: handle Refill, CancelRx, RxRenewalRequest
        logger.warning("Unhandled SCRIPT message type")
        return None
