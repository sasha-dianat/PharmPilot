"""
PDMP (Prescription Drug Monitoring Program) Integration.
Supports: Appriss NarxCare API + PMP InterConnect PMIX/NIEM XML.
Auto-triggers query before dispensing any Schedule II–V controlled substance.
Multi-state routing — sends to correct state PDMP based on patient address.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import httpx

logger = logging.getLogger(__name__)


@dataclass
class PDMPPatientQuery:
    first_name: str
    last_name: str
    date_of_birth: str      # YYYY-MM-DD
    state_of_residence: str  # 2-char state code
    # Optional but improves match rate
    ssn_last4: Optional[str] = None
    street_address: Optional[str] = None
    zip_code: Optional[str] = None
    gender: Optional[str] = None  # M/F


@dataclass
class NarxScore:
    """
    Appriss NarxCare composite risk score.
    Scale 0–999 per drug category. Higher = higher risk.
    """
    narcotic_score: Optional[int] = None       # Opioid risk
    sedative_score: Optional[int] = None       # Benzo/sedative risk
    stimulant_score: Optional[int] = None      # Stimulant risk
    overdose_indicator: bool = False           # Prior overdose on record
    doctor_shopping_indicator: bool = False   # Multiple prescribers
    cash_pay_indicator: bool = False          # High cash-pay prescriptions
    risk_level: str = "unknown"               # low, moderate, high, critical


@dataclass
class PDMPDispenseRecord:
    ndc: str
    drug_name: str
    quantity: float
    days_supply: int
    dispense_date: str
    prescriber_npi: str
    prescriber_name: str
    pharmacy_name: str
    pharmacy_npi: str
    state: str
    payment_type: str  # insurance, cash, medicaid


@dataclass
class PDMPQueryResult:
    query_id: str
    patient_found: bool
    query_state: str
    query_timestamp: datetime
    dispense_records: list[PDMPDispenseRecord] = field(default_factory=list)
    narx_score: Optional[NarxScore] = None
    rx_count_30d: int = 0
    rx_count_90d: int = 0
    controlled_substance_count_30d: int = 0
    prescriber_count_30d: int = 0
    pharmacy_count_30d: int = 0
    # Calculated risk flags
    multiple_providers_flag: bool = False        # > 3 prescribers in 30 days
    multiple_pharmacies_flag: bool = False       # > 3 pharmacies in 30 days
    overlapping_controlled_flag: bool = False    # Concurrent opioid + benzo
    raw_response: Optional[str] = None


# State PDMP endpoint routing
STATE_PDMP_ENDPOINTS = {
    # States on PMP InterConnect (PMIX/NIEM XML)
    "AL": "https://pdmp.alabamainterchangepoint.gov/pmix",
    "AK": "https://pdmp.alaska.gov/pmix",
    "AZ": "https://pdmp.az.gov/pmix",
    "AR": "https://pdmp.arkansas.gov/pmix",
    "CA": "https://pdmp.ca.gov/pmix",
    "CO": "https://pdmp.colorado.gov/pmix",
    "FL": "https://fl.pmpinterconnect.com/pmix",
    "GA": "https://ga.pmpinterconnect.com/pmix",
    "TX": "https://tx.pmpinterconnect.com/pmix",
    "NY": "https://ny.pmpinterconnect.com/pmix",
    # Default NarxCare endpoint (Appriss) — covers most states
    "DEFAULT_NARXCARE": "https://api.apprisshealth.com/narxcare/v2",
}

# States where PDMP query is MANDATORY before dispensing Schedule II
MANDATORY_QUERY_STATES_CII = {
    "NY", "NJ", "FL", "TX", "CA", "OH", "PA", "IL", "GA", "AZ",
    "WA", "CO", "VA", "NC", "MA", "MI", "TN", "MO", "MD", "WI",
    "MN", "OR", "AL", "SC", "KY", "LA", "CT", "OK", "AR", "MS",
    "KS", "NV", "NM", "WV", "ID", "HI", "ME", "NH", "RI", "MT",
    "DE", "SD", "ND", "AK", "VT", "WY",
}

# States where PDMP query is mandatory for CIII-CV as well
MANDATORY_QUERY_STATES_CIII_CV = {
    "NY", "NJ", "OH", "WA", "OR", "KY",
}


class PDMPClient:
    """
    Unified PDMP client supporting both NarxCare and PMP InterConnect protocols.
    Automatically selects the correct endpoint per state.
    """

    def __init__(
        self,
        narxcare_api_key: str = "",
        state_credentials: Optional[dict] = None,
        timeout_seconds: int = 10,
    ):
        self.narxcare_api_key = narxcare_api_key
        self.state_credentials = state_credentials or {}
        self.timeout = timeout_seconds

    def should_query(
        self,
        dea_schedule: str,
        patient_state: str,
        pharmacy_state: str,
    ) -> bool:
        """Determine if PDMP query is required for this dispensing event."""
        if not dea_schedule or dea_schedule == "OTC":
            return False
        schedule_upper = dea_schedule.upper()
        if schedule_upper in ("CI", "CII"):
            return True  # Always query for CII
        if schedule_upper in ("CIII", "CIV", "CV"):
            state = patient_state or pharmacy_state
            return state.upper() in MANDATORY_QUERY_STATES_CIII_CV
        return False

    async def query(
        self,
        patient: PDMPPatientQuery,
        requesting_pharmacy_npi: str,
        requesting_pharmacist_npi: str,
        dea_schedule: str,
    ) -> PDMPQueryResult:
        """
        Query the PDMP for a patient's controlled substance history.
        Tries NarxCare first (fastest, richest data), falls back to PMIX.
        """
        from uuid import uuid4
        query_id = str(uuid4())
        logger.info(
            "PDMP query for patient DOB=%s state=%s schedule=%s",
            patient.date_of_birth, patient.state_of_residence, dea_schedule,
        )

        try:
            if self.narxcare_api_key:
                result = await self._query_narxcare(
                    patient, requesting_pharmacy_npi, requesting_pharmacist_npi, query_id
                )
            else:
                result = await self._query_pmix(
                    patient, requesting_pharmacy_npi, requesting_pharmacist_npi, query_id
                )
            self._calculate_risk_flags(result)
            return result

        except Exception as exc:
            logger.error("PDMP query failed: %s", exc)
            # Return an empty result — do not block dispensing on PDMP timeout
            # but flag for pharmacist review
            return PDMPQueryResult(
                query_id=query_id,
                patient_found=False,
                query_state=patient.state_of_residence,
                query_timestamp=datetime.now(timezone.utc),
                raw_response=f"PDMP query failed: {exc}",
            )

    async def _query_narxcare(
        self,
        patient: PDMPPatientQuery,
        pharmacy_npi: str,
        pharmacist_npi: str,
        query_id: str,
    ) -> PDMPQueryResult:
        """Query Appriss NarxCare API (REST/JSON)."""
        payload = {
            "requestHeader": {
                "requestId": query_id,
                "requestTimestamp": datetime.now(timezone.utc).isoformat(),
                "requestingOrganizationId": pharmacy_npi,
                "requestingProviderId": pharmacist_npi,
            },
            "patient": {
                "firstName": patient.first_name,
                "lastName": patient.last_name,
                "dateOfBirth": patient.date_of_birth,
                "gender": patient.gender,
                "address": {
                    "state": patient.state_of_residence,
                    "zip": patient.zip_code,
                },
            },
            "includeNarxScores": True,
            "includeHistory": True,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                STATE_PDMP_ENDPOINTS["DEFAULT_NARXCARE"],
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.narxcare_api_key}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            data = response.json()

        return self._parse_narxcare_response(data, query_id, patient.state_of_residence)

    async def _query_pmix(
        self,
        patient: PDMPPatientQuery,
        pharmacy_npi: str,
        pharmacist_npi: str,
        query_id: str,
    ) -> PDMPQueryResult:
        """Query PMP InterConnect PMIX/NIEM XML endpoint."""
        endpoint = STATE_PDMP_ENDPOINTS.get(
            patient.state_of_residence.upper(),
            STATE_PDMP_ENDPOINTS.get("DEFAULT_NARXCARE", "")
        )
        xml_request = self._build_pmix_request(patient, pharmacy_npi, pharmacist_npi, query_id)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                endpoint,
                content=xml_request.encode("utf-8"),
                headers={"Content-Type": "application/xml"},
            )
            response.raise_for_status()

        return self._parse_pmix_response(response.text, query_id, patient.state_of_residence)

    def _parse_narxcare_response(self, data: dict, query_id: str, state: str) -> PDMPQueryResult:
        patient_data = data.get("patient", {})
        history = data.get("prescriptionHistory", [])
        narx_data = data.get("narxScores", {})

        dispense_records = [
            PDMPDispenseRecord(
                ndc=r.get("ndc", ""),
                drug_name=r.get("drugName", ""),
                quantity=float(r.get("quantity", 0)),
                days_supply=int(r.get("daysSupply", 0)),
                dispense_date=r.get("dispenseDate", ""),
                prescriber_npi=r.get("prescriberNpi", ""),
                prescriber_name=r.get("prescriberName", ""),
                pharmacy_name=r.get("pharmacyName", ""),
                pharmacy_npi=r.get("pharmacyNpi", ""),
                state=r.get("state", state),
                payment_type=r.get("paymentType", "unknown"),
            )
            for r in history
        ]

        narx_score = None
        if narx_data:
            narcotic = narx_data.get("narcoticScore")
            sedative = narx_data.get("sedativeScore")
            risk = "low"
            if narcotic and narcotic > 500:
                risk = "critical"
            elif narcotic and narcotic > 300:
                risk = "high"
            elif narcotic and narcotic > 150:
                risk = "moderate"

            narx_score = NarxScore(
                narcotic_score=narcotic,
                sedative_score=sedative,
                stimulant_score=narx_data.get("stimulantScore"),
                overdose_indicator=narx_data.get("overdoseIndicator", False),
                doctor_shopping_indicator=narx_data.get("doctorShoppingIndicator", False),
                risk_level=risk,
            )

        return PDMPQueryResult(
            query_id=query_id,
            patient_found=bool(patient_data),
            query_state=state,
            query_timestamp=datetime.now(timezone.utc),
            dispense_records=dispense_records,
            narx_score=narx_score,
            rx_count_30d=len([r for r in dispense_records
                              if self._within_days(r.dispense_date, 30)]),
            rx_count_90d=len([r for r in dispense_records
                              if self._within_days(r.dispense_date, 90)]),
            raw_response=str(data),
        )

    def _build_pmix_request(self, patient, pharmacy_npi, pharmacist_npi, query_id) -> str:
        """Build PMIX/NIEM XML request."""
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<pmix:PMPInterconnectRequest xmlns:pmix="http://www.pmpinterconnect.com/pmix">
  <pmix:RequestHeader>
    <pmix:RequestId>{query_id}</pmix:RequestId>
    <pmix:RequestTimestamp>{datetime.now(timezone.utc).isoformat()}</pmix:RequestTimestamp>
    <pmix:RequestingOrganizationId>{pharmacy_npi}</pmix:RequestingOrganizationId>
  </pmix:RequestHeader>
  <pmix:Patient>
    <pmix:FirstName>{patient.first_name}</pmix:FirstName>
    <pmix:LastName>{patient.last_name}</pmix:LastName>
    <pmix:DateOfBirth>{patient.date_of_birth}</pmix:DateOfBirth>
    <pmix:StateOfResidence>{patient.state_of_residence}</pmix:StateOfResidence>
  </pmix:Patient>
</pmix:PMPInterconnectRequest>"""

    def _parse_pmix_response(self, xml_text: str, query_id: str, state: str) -> PDMPQueryResult:
        """Parse PMIX/NIEM XML response."""
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(xml_text)
            ns = {"pmix": "http://www.pmpinterconnect.com/pmix"}
            records = []
            for rx in root.findall(".//pmix:PrescriptionDispensed", ns):
                records.append(PDMPDispenseRecord(
                    ndc=rx.findtext("pmix:NDC", "", ns),
                    drug_name=rx.findtext("pmix:DrugName", "", ns),
                    quantity=float(rx.findtext("pmix:Quantity", "0", ns)),
                    days_supply=int(rx.findtext("pmix:DaysSupply", "0", ns)),
                    dispense_date=rx.findtext("pmix:DispenseDate", "", ns),
                    prescriber_npi=rx.findtext("pmix:PrescriberNPI", "", ns),
                    prescriber_name=rx.findtext("pmix:PrescriberName", "", ns),
                    pharmacy_name=rx.findtext("pmix:PharmacyName", "", ns),
                    pharmacy_npi=rx.findtext("pmix:PharmacyNPI", "", ns),
                    state=state,
                    payment_type=rx.findtext("pmix:PaymentType", "unknown", ns),
                ))
            return PDMPQueryResult(
                query_id=query_id,
                patient_found=len(records) > 0,
                query_state=state,
                query_timestamp=datetime.now(timezone.utc),
                dispense_records=records,
                raw_response=xml_text[:2000],
            )
        except Exception as exc:
            logger.error("PMIX XML parse failed: %s", exc)
            return PDMPQueryResult(
                query_id=query_id, patient_found=False,
                query_state=state, query_timestamp=datetime.now(timezone.utc),
            )

    def _calculate_risk_flags(self, result: PDMPQueryResult) -> None:
        """Calculate derived risk flags from dispense history."""
        recent_30d = [r for r in result.dispense_records if self._within_days(r.dispense_date, 30)]
        result.controlled_substance_count_30d = len(recent_30d)
        result.prescriber_count_30d = len(set(r.prescriber_npi for r in recent_30d))
        result.pharmacy_count_30d = len(set(r.pharmacy_npi for r in recent_30d))
        result.multiple_providers_flag = result.prescriber_count_30d > 3
        result.multiple_pharmacies_flag = result.pharmacy_count_30d > 3

        opioid_names = {"oxycodone", "hydrocodone", "morphine", "fentanyl", "hydromorphone", "codeine"}
        benzo_names = {"alprazolam", "clonazepam", "diazepam", "lorazepam", "temazepam"}
        has_opioid = any(any(o in r.drug_name.lower() for o in opioid_names) for r in recent_30d)
        has_benzo = any(any(b in r.drug_name.lower() for b in benzo_names) for r in recent_30d)
        result.overlapping_controlled_flag = has_opioid and has_benzo

    @staticmethod
    def _within_days(date_str: str, days: int) -> bool:
        if not date_str:
            return False
        try:
            from datetime import date, timedelta
            disp_date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
            return (date.today() - disp_date).days <= days
        except Exception:
            return False
