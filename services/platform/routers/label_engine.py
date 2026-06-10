"""
Label Engine Router — Phase 23
================================
Endpoints for Rx label generation, ZPL printing, and handwritten-mode audit.

POST /api/v1/labels/{rx_id}/generate    — resolve label data from Rx record
GET  /api/v1/labels/{rx_id}/zpl         — return ZPL II string
POST /api/v1/labels/{rx_id}/print       — send ZPL to configured Zebra printer IP
POST /api/v1/labels/{rx_id}/handwritten — record that pharmacist hand-wrote the label
GET  /api/v1/labels/auxiliary           — list all available auxiliary warning labels
"""
from __future__ import annotations

import asyncio
import logging
import socket
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.database import get_db
from services.platform.auth import get_current_user
from services.ai.label_engine import LabelGenerator, LabelData, PrintMode, ZPLRenderer
from services.ai.label_engine.label_generator import AUXILIARY_LABELS

logger = logging.getLogger(__name__)

router = APIRouter()
_gen      = LabelGenerator()
_renderer = ZPLRenderer()


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class LabelGenerateRequest(BaseModel):
    print_mode:       PrintMode = PrintMode.THERMAL_PDF
    extra_aux_codes:  list[str] = []
    handwritten_by:   Optional[str] = None


class PrintRequest(BaseModel):
    printer_ip:   str          # e.g. "192.168.1.50"
    printer_port: int = 9100
    include_aux:  bool = True  # also send auxiliary warning slips


class HandwrittenAuditRequest(BaseModel):
    notes:        Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _load_rx_for_label(rx_id: str, pharmacy_id: str, db: AsyncSession) -> dict:
    """Load Rx + patient + pharmacy into flat dicts, scoped to the caller's pharmacy."""
    result = await db.execute(
        text("""
            SELECT
                rx.id, rx.rx_number, rx.fill_date,
                rx.drug_name, rx.drug_strength, rx.drug_form, rx.ndc,
                rx.quantity_prescribed, rx.days_supply, rx.refills_remaining,
                rx.sig_text, rx.is_controlled, rx.dea_schedule,
                p.first_name AS patient_first, p.last_name AS patient_last,
                p.date_of_birth,
                pr.first_name AS presc_first, pr.last_name AS presc_last,
                pr.npi AS presc_npi,
                ph.name AS ph_name, ph.address_line1, ph.city,
                ph.state, ph.zip_code, ph.phone, ph.npi AS ph_npi,
                pf.fill_number AS latest_fill_number
            FROM prescriptions rx
            JOIN patients p   ON p.id   = rx.patient_id
            LEFT JOIN prescribers pr ON pr.id = rx.prescriber_id
            LEFT JOIN pharmacies ph ON ph.id = rx.pharmacy_id
            LEFT JOIN LATERAL (
                SELECT fill_number FROM prescription_fills
                WHERE prescription_id = rx.id
                ORDER BY fill_number DESC
                LIMIT 1
            ) pf ON true
            WHERE rx.id = :rx_id AND rx.pharmacy_id = :pharmacy_id
        """),
        {"rx_id": rx_id, "pharmacy_id": pharmacy_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Prescription {rx_id} not found")

    rx = {
        "rx_number":          row["rx_number"],
        "fill_number":        row["latest_fill_number"] or 1,
        "fill_date":          str(row["fill_date"] or date.today()),
        "drug_name":          row["drug_name"] or "",
        "drug_strength":      row["drug_strength"] or "",
        "dosage_form":        row["drug_form"] or "",
        "ndc11":              row["ndc"] or "",
        "quantity_prescribed": float(row["quantity_prescribed"] or 0),
        "days_supply":        int(row["days_supply"] or 0),
        "refills_remaining":  int(row["refills_remaining"] or 0),
        "sig_text":           row["sig_text"] or "",
        "is_controlled":      bool(row["is_controlled"]),
        "dea_schedule":       row["dea_schedule"],
        "prescriber": {
            "first_name": row["presc_first"] or "",
            "last_name":  row["presc_last"] or "",
            "npi":        row["presc_npi"] or "",
        },
    }
    patient = {
        "first_name":    row["patient_first"] or "",
        "last_name":     row["patient_last"] or "",
        "date_of_birth": row["date_of_birth"] or "",
    }
    pharmacy = {
        "name":         row["ph_name"] or "PharmPilot Pharmacy",
        "address_line1":row["address_line1"] or "",
        "city":         row["city"] or "",
        "state":        row["state"] or "",
        "zip_code":     row["zip_code"] or "",
        "phone":        row["phone"] or "",
        "npi":          row["ph_npi"] or "",
    }
    return {"rx": rx, "patient": patient, "pharmacy": pharmacy}


def _label_to_dict(label: LabelData) -> dict:
    return {
        "rx_number":             label.rx_number,
        "fill_number":           label.fill_number,
        "fill_date":             label.fill_date,
        "drug_name":             label.drug_name,
        "drug_strength":         label.drug_strength,
        "dosage_form":           label.dosage_form,
        "ndc11":                 label.ndc11,
        "quantity":              label.quantity,
        "days_supply":           label.days_supply,
        "refills_remaining":     label.refills_remaining,
        "sig_text":              label.sig_text,
        "patient_name":          label.patient_name_display,
        "patient_dob_masked":    label.patient_dob_masked,
        "prescriber_name":       label.prescriber_name,
        "prescriber_npi":        label.prescriber_npi,
        "pharmacy_name":         label.pharmacy_name,
        "pharmacy_address":      label.pharmacy_address,
        "pharmacy_city_state_zip": label.pharmacy_city_state_zip,
        "pharmacy_phone":        label.pharmacy_phone,
        "pharmacy_npi":          label.pharmacy_npi,
        "is_controlled":         label.is_controlled,
        "dea_schedule":          label.dea_schedule,
        "print_mode":            label.print_mode.value,
        "handwritten_by":        label.handwritten_by,
        "auxiliary_labels": [
            {"code": a.code, "text": a.text, "color": a.color_hex, "icon": a.icon}
            for a in label.aux_label_objects
        ],
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

# NOTE: static routes (e.g. /auxiliary) must be registered before dynamic
# "/{rx_id}/..." routes so they can never be shadowed by path-parameter matching.
@router.get("/auxiliary")
async def list_auxiliary_labels(_user=Depends(get_current_user)):
    """Return the full catalog of available auxiliary warning label codes."""
    return {
        "total": len(AUXILIARY_LABELS),
        "labels": [
            {
                "code":      a.code,
                "text":      a.text,
                "color_hex": a.color_hex,
                "icon":      a.icon,
            }
            for a in AUXILIARY_LABELS.values()
        ],
    }


@router.post("/{rx_id}/generate")
async def generate_label(
    rx_id: str,
    body: LabelGenerateRequest,
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    """
    Assemble a label data object from Rx + patient + pharmacy records.
    Returns structured label data (not yet rendered to ZPL or PDF).
    """
    records = await _load_rx_for_label(rx_id, _user["pharmacy_id"], db)
    label = _gen.from_records(
        rx=records["rx"],
        patient=records["patient"],
        pharmacy=records["pharmacy"],
        print_mode=body.print_mode,
        handwritten_by=body.handwritten_by,
        extra_aux_codes=body.extra_aux_codes,
    )
    return {"label": _label_to_dict(label), "rx_id": rx_id}


@router.get("/{rx_id}/zpl")
async def get_zpl(
    rx_id: str,
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    """Return raw ZPL II string. Download and send to printer via socket port 9100."""
    records = await _load_rx_for_label(rx_id, _user["pharmacy_id"], db)
    label = _gen.from_records(
        rx=records["rx"],
        patient=records["patient"],
        pharmacy=records["pharmacy"],
        print_mode=PrintMode.THERMAL_ZPL,
    )
    zpl_string = _renderer.render(label)
    return {
        "rx_id": rx_id,
        "rx_number": label.rx_number,
        "zpl": zpl_string,
        "auxiliary_slips_zpl": _renderer.render_auxiliary_slips(label),
        "instructions": "Send ZPL to printer: socket.connect((printer_ip, 9100)); socket.send(zpl.encode())",
    }


@router.post("/{rx_id}/print")
async def send_to_printer(
    rx_id: str,
    body: PrintRequest,
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    """
    Render ZPL and send directly to Zebra thermal printer over TCP/9100.
    Raises 502 if printer is unreachable.
    """
    records = await _load_rx_for_label(rx_id, _user["pharmacy_id"], db)
    label = _gen.from_records(
        rx=records["rx"],
        patient=records["patient"],
        pharmacy=records["pharmacy"],
        print_mode=PrintMode.THERMAL_ZPL,
    )
    zpl_string = _renderer.render(label)
    aux_slips  = _renderer.render_auxiliary_slips(label) if body.include_aux else []

    def _send_socket():
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(5)
                s.connect((body.printer_ip, body.printer_port))
                # Main label
                s.sendall(zpl_string.encode("ascii", errors="replace"))
                # Auxiliary warning slips
                for slip in aux_slips:
                    s.sendall(slip.encode("ascii", errors="replace"))
        except OSError as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Printer unreachable at {body.printer_ip}:{body.printer_port} — {e}",
            )

    await asyncio.get_event_loop().run_in_executor(None, _send_socket)
    logger.info("Label sent to printer %s:%s for Rx %s", body.printer_ip, body.printer_port, label.rx_number)

    return {
        "success": True,
        "rx_id":   rx_id,
        "rx_number": label.rx_number,
        "printer": f"{body.printer_ip}:{body.printer_port}",
        "auxiliary_slips_sent": len(aux_slips),
    }


@router.post("/{rx_id}/handwritten")
async def record_handwritten(
    rx_id: str,
    body: HandwrittenAuditRequest,
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    """
    Record that the pharmacist hand-wrote the label for this Rx.
    No print job is created. Audit entry is stored.

    Use when: pharmacist elects to write the label manually rather than
    print (e.g., unusual format, thermal printer offline, patient request).
    """
    result = await db.execute(
        text("SELECT rx_number FROM prescriptions WHERE id = :id AND pharmacy_id = :pharmacy_id"),
        {"id": rx_id, "pharmacy_id": _user["pharmacy_id"]},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Prescription {rx_id} not found")

    staff_id = _user["staff_id"]

    # Log the handwritten audit event — stored as a label_event entry
    await db.execute(
        text("""
            INSERT INTO label_events (rx_id, event_type, staff_id, notes, created_at)
            VALUES (:rx_id, 'handwritten', :staff_id, :notes, NOW())
            ON CONFLICT DO NOTHING
        """),
        {"rx_id": rx_id, "staff_id": staff_id, "notes": body.notes or ""},
    )
    await db.commit()

    logger.info("Handwritten label recorded for Rx %s by staff %s", row["rx_number"], staff_id)
    return {
        "success":    True,
        "rx_id":      rx_id,
        "rx_number":  row["rx_number"],
        "recorded_by": staff_id,
        "message":    "Handwritten label recorded. No print job was created.",
    }

