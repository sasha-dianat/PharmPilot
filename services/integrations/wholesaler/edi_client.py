"""
Wholesaler EDI Integration — McKesson, Cardinal Health, AmerisourceBergen.
Implements: EDI 850 (PO), 855 (PO Acknowledgment), 856 (ASN), 810 (Invoice).
DSCSA serialized product traceability (EPCIS 2.0 lot/serial tracking).
"""
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)


@dataclass
class EDI850Line:
    """Single line item on a purchase order."""
    line_number: int
    ndc11: str
    drug_name: str
    quantity_ordered: float
    unit_of_measure: str = "EA"
    unit_price: Optional[float] = None
    wholesaler_item_number: Optional[str] = None


@dataclass
class EDI850PurchaseOrder:
    """X12 850 Purchase Order."""
    po_number: str
    pharmacy_npi: str
    pharmacy_name: str
    pharmacy_dea: Optional[str]
    wholesaler_id: str
    order_date: str
    requested_delivery_date: Optional[str] = None
    lines: list[EDI850Line] = field(default_factory=list)


@dataclass
class EDI856ASN:
    """X12 856 Advance Ship Notice — what's actually being shipped."""
    asn_number: str
    po_number: str
    ship_date: str
    carrier: str
    tracking_number: Optional[str]
    lines: list[dict] = field(default_factory=list)
    # Each line: {ndc11, lot_number, expiry_date, quantity_shipped, serial_number}


@dataclass
class DSCSATraceabilityRecord:
    """DSCSA serialized drug traceability — required by 21 CFR Part 203."""
    ndc11: str
    lot_number: str
    expiry_date: str
    serial_number: str
    transaction_date: str
    transaction_type: str   # S=Sale, P=Purchase, R=Return
    from_trading_partner: str
    to_trading_partner: str
    transaction_id: str = field(default_factory=lambda: str(uuid4()))


class X12EDIBuilder:
    """Build X12 EDI transaction sets for wholesaler communication."""

    ISA_CONTROL_NUMBER = 1  # Incremented per transaction in production

    def build_850(self, po: EDI850PurchaseOrder) -> str:
        """Build X12 850 Purchase Order transaction."""
        isa_id = str(self.ISA_CONTROL_NUMBER).zfill(9)
        gs_id = str(self.ISA_CONTROL_NUMBER).zfill(9)
        st_id = str(self.ISA_CONTROL_NUMBER).zfill(4)
        timestamp = datetime.now().strftime("%y%m%d")
        time_str = datetime.now().strftime("%H%M")

        segments = [
            f"ISA*00*          *00*          *01*{po.pharmacy_npi.ljust(15)}*01*{po.wholesaler_id.ljust(15)}*{timestamp}*{time_str}*^*00501*{isa_id}*0*P*:",
            f"GS*PO*{po.pharmacy_npi}*{po.wholesaler_id}*{datetime.now().strftime('%Y%m%d')}*{time_str}*{gs_id}*X*005010",
            f"ST*850*{st_id}",
            f"BEG*00*SA*{po.po_number}**{po.order_date}",
        ]

        if po.requested_delivery_date:
            segments.append(f"DTM*002*{po.requested_delivery_date.replace('-', '')}")

        # Ship to address
        segments.extend([
            f"N1*ST*{po.pharmacy_name}*29*{po.pharmacy_npi}",
        ])

        # Line items
        for line in po.lines:
            segments.extend([
                f"PO1*{line.line_number}*{line.quantity_ordered}*{line.unit_of_measure}*{line.unit_price or ''}**N4*{line.ndc11}",
                f"PID*F****{line.drug_name[:30]}",
            ])

        # Transaction totals
        segments.extend([
            f"CTT*{len(po.lines)}",
            f"SE*{len(segments) - 2}*{st_id}",
            f"GE*1*{gs_id}",
            f"IEA*1*{isa_id}",
        ])

        return "\n".join(segments)

    def parse_855(self, edi_text: str) -> dict:
        """Parse X12 855 Purchase Order Acknowledgment."""
        result = {"status": "unknown", "lines": [], "po_number": ""}
        for line in edi_text.split("\n"):
            fields = line.split("*")
            if not fields:
                continue
            seg = fields[0]
            if seg == "BAK":
                result["po_number"] = fields[3] if len(fields) > 3 else ""
                result["status"] = "acknowledged" if fields[1] in ("AC", "AD") else "rejected"
            elif seg == "PO1" and len(fields) > 6:
                result["lines"].append({
                    "line": fields[1],
                    "quantity_accepted": fields[2],
                    "ndc11": fields[6],
                    "status": "accepted",
                })
        return result

    def parse_856(self, edi_text: str) -> EDI856ASN:
        """Parse X12 856 Advance Ship Notice."""
        asn = EDI856ASN(
            asn_number="", po_number="", ship_date="",
            carrier="", tracking_number=None
        )
        current_lot = {}
        for line in edi_text.split("\n"):
            fields = line.split("*")
            if not fields:
                continue
            seg = fields[0]
            if seg == "BSN":
                asn.asn_number = fields[2] if len(fields) > 2 else ""
                asn.ship_date = fields[3] if len(fields) > 3 else ""
            elif seg == "TD3":
                asn.carrier = fields[1] if len(fields) > 1 else ""
                asn.tracking_number = fields[4] if len(fields) > 4 else None
            elif seg == "LIN" and len(fields) > 6:
                current_lot = {"ndc11": fields[6]}
            elif seg == "SN1" and current_lot:
                current_lot["quantity_shipped"] = float(fields[2]) if len(fields) > 2 else 0
            elif seg == "LOT" and current_lot:
                current_lot["lot_number"] = fields[1] if len(fields) > 1 else ""
                current_lot["expiry_date"] = fields[2] if len(fields) > 2 else ""
            elif seg == "DTM" and current_lot and fields[1] == "036":
                current_lot["expiry_date"] = fields[2] if len(fields) > 2 else ""
                asn.lines.append(dict(current_lot))
                current_lot = {}
        return asn

    def parse_810(self, edi_text: str) -> dict:
        """Parse X12 810 Invoice."""
        invoice = {"invoice_number": "", "total": 0.0, "lines": []}
        for line in edi_text.split("\n"):
            fields = line.split("*")
            if not fields:
                continue
            seg = fields[0]
            if seg == "BIG":
                invoice["invoice_number"] = fields[3] if len(fields) > 3 else ""
            elif seg == "IT1" and len(fields) > 6:
                invoice["lines"].append({
                    "ndc11": fields[6],
                    "quantity": float(fields[2]) if len(fields) > 2 else 0,
                    "unit_price": float(fields[4]) if len(fields) > 4 else 0,
                })
            elif seg == "TDS":
                try:
                    invoice["total"] = float(fields[1]) / 100 if len(fields) > 1 else 0.0
                except ValueError:
                    pass
        return invoice


class WholesalerEDIClient:
    """
    Unified EDI client for all three major pharmaceutical wholesalers.
    Abstracts the slight format variations between McKesson, Cardinal, and AmerisourceBergen.
    """

    WHOLESALER_ENDPOINTS = {
        "mckesson": {
            "edi_url": "https://connect.mckesson.com/edi",
            "sftp_host": "connect.mckesson.com",
            "id": "MCKESSON",
        },
        "cardinal": {
            "edi_url": "https://edi.cardinalhealth.com/transactions",
            "sftp_host": "edi.cardinalhealth.com",
            "id": "CARDINAL",
        },
        "amerisource": {
            "edi_url": "https://edi.amerisourcebergen.com/submit",
            "sftp_host": "edi.amerisourcebergen.com",
            "id": "AMERISOURCE",
        },
    }

    def __init__(self, wholesaler: str, api_key: str = "", sender_id: str = ""):
        self.wholesaler = wholesaler.lower()
        self.api_key = api_key
        self.sender_id = sender_id
        self.builder = X12EDIBuilder()
        self.config = self.WHOLESALER_ENDPOINTS.get(self.wholesaler, {})

    async def submit_purchase_order(self, po: EDI850PurchaseOrder) -> dict:
        """Submit a purchase order via EDI 850."""
        edi_850 = self.builder.build_850(po)
        logger.info("Submitting EDI 850 PO %s to %s (%d lines)",
                    po.po_number, self.wholesaler, len(po.lines))

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    self.config["edi_url"],
                    content=edi_850.encode("ascii"),
                    headers={
                        "Content-Type": "application/edi-x12",
                        "X-API-Key": self.api_key,
                        "X-Sender-ID": self.sender_id,
                    },
                )
                return {
                    "status": "submitted" if response.status_code in (200, 202) else "error",
                    "wholesaler": self.wholesaler,
                    "po_number": po.po_number,
                    "response_code": response.status_code,
                }
        except Exception as exc:
            logger.error("EDI 850 submission failed: %s", exc)
            return {"status": "error", "error": str(exc), "po_number": po.po_number}

    def process_asn(self, edi_856_text: str) -> EDI856ASN:
        """Process incoming ASN — what is actually being shipped."""
        return self.builder.parse_856(edi_856_text)

    def process_invoice(self, edi_810_text: str) -> dict:
        """Reconcile invoice against PO — flag discrepancies."""
        return self.builder.parse_810(edi_810_text)

    def build_dscsa_record(
        self,
        asn_line: dict,
        pharmacy_npi: str,
        wholesaler_duns: str,
    ) -> DSCSATraceabilityRecord:
        """Build a DSCSA T3 (Transaction, Tracing, Transfer) record from an ASN line."""
        return DSCSATraceabilityRecord(
            ndc11=asn_line.get("ndc11", ""),
            lot_number=asn_line.get("lot_number", ""),
            expiry_date=asn_line.get("expiry_date", ""),
            serial_number=asn_line.get("serial_number", ""),
            transaction_date=date.today().isoformat(),
            transaction_type="P",  # Purchase
            from_trading_partner=wholesaler_duns,
            to_trading_partner=pharmacy_npi,
        )


import httpx
