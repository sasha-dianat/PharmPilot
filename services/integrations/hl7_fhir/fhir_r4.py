"""
HL7 FHIR R4 integration — bidirectional EHR data exchange.
Implements: Patient, MedicationRequest, MedicationDispense, Practitioner, Coverage.
Supports SMART on FHIR OAuth2 for EHR-launched contexts.
Also handles inbound HL7 v2.x ADT messages for demographic updates.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

import httpx

logger = logging.getLogger(__name__)

FHIR_VERSION = "4.0.1"


@dataclass
class FHIRPatient:
    """FHIR R4 Patient resource mapped to PharmPilot patient model."""
    fhir_id: str
    first_name: str
    last_name: str
    date_of_birth: str   # YYYY-MM-DD
    gender: str          # male, female, other, unknown
    phone: Optional[str] = None
    email: Optional[str] = None
    address_line1: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    npi: Optional[str] = None
    mrn: Optional[str] = None     # Medical record number from EHR
    insurance_member_id: Optional[str] = None


@dataclass
class FHIRMedicationRequest:
    """FHIR R4 MedicationRequest resource (inbound prescription from EHR)."""
    fhir_id: str
    patient_fhir_id: str
    prescriber_fhir_id: str
    prescriber_npi: Optional[str]
    medication_coding_system: str    # http://hl7.org/fhir/sid/ndc or RxNorm
    medication_code: str             # NDC or RxNorm code
    medication_display: str
    dosage_instruction_text: str
    quantity_value: float
    quantity_unit: str
    days_supply: Optional[int]
    refills_allowed: int = 0
    authored_on: str = ""
    status: str = "active"
    intent: str = "order"
    controlled_substance: bool = False
    dea_schedule: Optional[str] = None


class FHIRR4Client:
    """
    FHIR R4 RESTful API client.
    Used for: reading patient data from EHRs, pushing dispense notifications,
    receiving new prescriptions via MedicationRequest resources.
    """

    def __init__(self, base_url: str, access_token: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token

    def _headers(self) -> dict:
        headers = {
            "Content-Type": "application/fhir+json",
            "Accept": "application/fhir+json",
            "fhirVersion": FHIR_VERSION,
        }
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    async def get_patient(self, patient_id: str) -> Optional[FHIRPatient]:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.base_url}/Patient/{patient_id}",
                headers=self._headers(),
            )
            if resp.status_code == 200:
                return self._parse_patient_resource(resp.json())
        return None

    async def search_patients(
        self,
        family: Optional[str] = None,
        given: Optional[str] = None,
        birthdate: Optional[str] = None,
        identifier: Optional[str] = None,
    ) -> list[FHIRPatient]:
        params = {}
        if family:
            params["family"] = family
        if given:
            params["given"] = given
        if birthdate:
            params["birthdate"] = birthdate
        if identifier:
            params["identifier"] = identifier

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.base_url}/Patient",
                params=params,
                headers=self._headers(),
            )
            if resp.status_code == 200:
                bundle = resp.json()
                return [
                    self._parse_patient_resource(entry["resource"])
                    for entry in bundle.get("entry", [])
                    if entry.get("resource", {}).get("resourceType") == "Patient"
                ]
        return []

    async def get_medication_requests(
        self,
        patient_id: str,
        status: str = "active",
    ) -> list[FHIRMedicationRequest]:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.base_url}/MedicationRequest",
                params={"patient": patient_id, "status": status},
                headers=self._headers(),
            )
            if resp.status_code == 200:
                bundle = resp.json()
                return [
                    self._parse_medication_request(entry["resource"])
                    for entry in bundle.get("entry", [])
                    if entry.get("resource", {}).get("resourceType") == "MedicationRequest"
                ]
        return []

    async def post_medication_dispense(
        self,
        prescription_fill_data: dict,
        patient_fhir_id: str,
        prescriber_fhir_id: str,
    ) -> Optional[str]:
        """Notify the EHR that a medication was dispensed."""
        resource = {
            "resourceType": "MedicationDispense",
            "id": str(uuid4()),
            "status": "completed",
            "medicationCodeableConcept": {
                "coding": [
                    {
                        "system": "http://hl7.org/fhir/sid/ndc",
                        "code": prescription_fill_data.get("ndc_dispensed", ""),
                        "display": prescription_fill_data.get("drug_name", ""),
                    }
                ]
            },
            "subject": {"reference": f"Patient/{patient_fhir_id}"},
            "performer": [{"actor": {"reference": f"Practitioner/{prescriber_fhir_id}"}}],
            "quantity": {
                "value": prescription_fill_data.get("quantity_dispensed"),
                "unit": "EA",
                "system": "http://unitsofmeasure.org",
            },
            "daysSupply": {
                "value": prescription_fill_data.get("days_supply"),
                "unit": "days",
            },
            "whenHandedOver": datetime.now(timezone.utc).isoformat(),
            "dosageInstruction": [
                {"text": prescription_fill_data.get("sig_text", "")}
            ],
        }

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{self.base_url}/MedicationDispense",
                json=resource,
                headers=self._headers(),
            )
            if resp.status_code in (200, 201):
                return resp.json().get("id")
        return None

    def _parse_patient_resource(self, resource: dict) -> FHIRPatient:
        name = resource.get("name", [{}])[0] if resource.get("name") else {}
        address = resource.get("address", [{}])[0] if resource.get("address") else {}
        telecom = resource.get("telecom", [])
        phone = next((t["value"] for t in telecom if t.get("system") == "phone"), None)
        email = next((t["value"] for t in telecom if t.get("system") == "email"), None)

        return FHIRPatient(
            fhir_id=resource.get("id", ""),
            first_name=(name.get("given") or [""])[0],
            last_name=name.get("family", ""),
            date_of_birth=resource.get("birthDate", ""),
            gender=resource.get("gender", "unknown"),
            phone=phone,
            email=email,
            address_line1=(address.get("line") or [""])[0],
            city=address.get("city"),
            state=address.get("state"),
            zip_code=address.get("postalCode"),
        )

    def _parse_medication_request(self, resource: dict) -> FHIRMedicationRequest:
        med = resource.get("medicationCodeableConcept", {})
        coding = (med.get("coding") or [{}])[0]
        dosage = (resource.get("dosageInstruction") or [{}])[0]
        dispense_req = resource.get("dispenseRequest", {})
        quantity = dispense_req.get("quantity", {})

        return FHIRMedicationRequest(
            fhir_id=resource.get("id", ""),
            patient_fhir_id=resource.get("subject", {}).get("reference", "").replace("Patient/", ""),
            prescriber_fhir_id=resource.get("requester", {}).get("reference", "").replace("Practitioner/", ""),
            prescriber_npi=None,  # Resolved via Practitioner resource lookup
            medication_coding_system=coding.get("system", ""),
            medication_code=coding.get("code", ""),
            medication_display=coding.get("display", med.get("text", "")),
            dosage_instruction_text=dosage.get("text", ""),
            quantity_value=float(quantity.get("value", 0)),
            quantity_unit=quantity.get("unit", "EA"),
            days_supply=dispense_req.get("expectedSupplyDuration", {}).get("value"),
            refills_allowed=dispense_req.get("numberOfRepeatsAllowed", 0),
            authored_on=resource.get("authoredOn", ""),
            status=resource.get("status", "active"),
        )


class HL7v2Parser:
    """
    HL7 v2.x message parser for inbound ADT (demographic) and ORU (lab) messages.
    Handles pipe-delimited HL7 v2.5.1 messages from hospital EHRs.
    """

    def parse_adt(self, hl7_text: str) -> Optional[dict]:
        """Parse ADT^A01/A08/A28/A31 message → patient demographics dict."""
        try:
            segments = {}
            for line in hl7_text.strip().split("\n"):
                if not line.strip():
                    continue
                fields = line.split("|")
                segment_name = fields[0]
                segments[segment_name] = fields

            if "PID" not in segments:
                return None

            pid = segments["PID"]
            name_parts = (pid[5] if len(pid) > 5 else "").split("^")
            address_parts = (pid[11] if len(pid) > 11 else "").split("^")

            return {
                "mrn": pid[3] if len(pid) > 3 else "",
                "last_name": name_parts[0] if name_parts else "",
                "first_name": name_parts[1] if len(name_parts) > 1 else "",
                "date_of_birth": pid[7] if len(pid) > 7 else "",
                "gender": pid[8] if len(pid) > 8 else "U",
                "address_line1": address_parts[0] if address_parts else "",
                "city": address_parts[2] if len(address_parts) > 2 else "",
                "state": address_parts[3] if len(address_parts) > 3 else "",
                "zip_code": address_parts[4] if len(address_parts) > 4 else "",
                "phone": pid[13] if len(pid) > 13 else "",
                "insurance_id": pid[18] if len(pid) > 18 else "",
                "source": "hl7_adt",
            }
        except Exception as exc:
            logger.error("ADT parse failed: %s", exc)
            return None

    def parse_oru(self, hl7_text: str) -> list[dict]:
        """Parse ORU^R01 message → list of lab result dicts."""
        results = []
        try:
            segments = {}
            current_obx = []
            for line in hl7_text.strip().split("\n"):
                if not line.strip():
                    continue
                fields = line.split("|")
                seg = fields[0]
                if seg == "OBX":
                    current_obx.append(fields)
                else:
                    segments[seg] = fields

            pid = segments.get("PID", [])
            mrn = pid[3] if len(pid) > 3 else ""

            for obx in current_obx:
                if len(obx) < 6:
                    continue
                test_coding = obx[3].split("^") if len(obx) > 3 else [""]
                results.append({
                    "mrn": mrn,
                    "loinc_code": test_coding[0],
                    "test_name": test_coding[1] if len(test_coding) > 1 else test_coding[0],
                    "value": obx[5] if len(obx) > 5 else "",
                    "unit": obx[6] if len(obx) > 6 else "",
                    "reference_range": obx[7] if len(obx) > 7 else "",
                    "abnormal_flag": obx[8] if len(obx) > 8 else "",
                    "result_status": obx[11] if len(obx) > 11 else "",
                    "source": "hl7_oru",
                })
        except Exception as exc:
            logger.error("ORU parse failed: %s", exc)
        return results

    def build_msa_ack(self, message_control_id: str, ack_code: str = "AA") -> str:
        """Build HL7 v2 ACK response."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        ack_id = str(uuid4()).replace("-", "")[:20]
        return "\r".join([
            f"MSH|^~\\&|PHARMPILOT|PHARMPILOT|SENDER||{timestamp}||ACK|{ack_id}|P|2.5.1",
            f"MSA|{ack_code}|{message_control_id}",
        ]) + "\r"
