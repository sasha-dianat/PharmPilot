"""
ZPL II Renderer — Phase 23
===========================
Converts LabelData into ZPL II commands for Zebra thermal printers.

Tested against: LP2844, GK420d, ZD420, ZD620, ZD888
Label size    : 4" × 2" (812 × 406 dots at 203 DPI) — most common Rx label
Font          : ZPL built-in A (proportional) and B (condensed monospace)
Barcode       : Code 128 for Rx# (scan-at-pickup), QR for structured data

Quick-start — send to printer via socket::

    renderer = ZPLRenderer()
    zpl = renderer.render(label_data)

    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect(("192.168.1.50", 9100))  # printer IP + port
        s.sendall(zpl.encode("ascii", errors="replace"))
"""
from __future__ import annotations

from .label_generator import LabelData, AUXILIARY_LABELS


class ZPLRenderer:
    """
    Renders LabelData to ZPL II string.

    Label layout (4"×2", 203 DPI):
    ┌──────────────────────────────────────────────────────┐
    │ PHARMACY NAME              Rx# 0001234    Date       │
    │ Address · Phone                                      │
    ├──────────────────────────────────────────────────────┤
    │ PATIENT LAST, First   DOB: MM/YYYY                   │
    ├──────────────────────────────────────────────────────┤
    │ DRUG NAME  Strength     Qty: 30  Days: 30  Rfls: 2  │
    │ Directions (SIG text — bold, largest font)           │
    ├──────────────────────────────────────────────────────┤
    │ Prescriber: Dr. Name  NPI: xxxxxxxxxx                │
    │ [CONTROLLED WARNING if applicable]                   │
    │ |||||||||||||| Code128 Barcode |||||||||||||         │
    │ Rx#0001234                                           │
    └──────────────────────────────────────────────────────┘
    """

    # Print width / label length in dots (203 DPI, 4"×2")
    PRINT_WIDTH  = 812
    LABEL_LENGTH = 406

    def render(self, label: LabelData) -> str:
        lines: list[str] = []

        def zpl(*parts: str) -> None:
            lines.append("".join(parts))

        def text(x: int, y: int, font_size: int, value: str, bold: bool = False) -> None:
            font = "^A0N" if not bold else "^A0B"
            zpl(f"^FO{x},{y}{font},{font_size},{font_size}^FD{_safe(value)}^FS")

        def thin_line(y: int) -> None:
            zpl(f"^FO10,{y}^GB{self.PRINT_WIDTH - 20},{y + 1},1^FS")

        def thick_line(y: int) -> None:
            zpl(f"^FO10,{y}^GB{self.PRINT_WIDTH - 20},{y + 2},2^FS")

        # --- Start label ---
        zpl("^XA")
        zpl(f"^PW{self.PRINT_WIDTH}")
        zpl(f"^LL{self.LABEL_LENGTH}")
        zpl("^LH0,0")

        # ── Row 1: Pharmacy header ──
        text(10, 6, 28, label.pharmacy_name, bold=False)
        rx_header = f"Rx# {label.rx_number}"
        text(530, 6, 22, rx_header)
        text(530, 28, 20, f"Date: {label.fill_date}")

        addr = f"{label.pharmacy_address}  {label.pharmacy_city_state_zip}  Tel: {label.pharmacy_phone}"
        text(10, 38, 18, addr)
        if label.pharmacy_npi:
            text(10, 56, 16, f"NPI: {label.pharmacy_npi}")

        thick_line(76)

        # ── Row 2: Patient ──
        patient_line = f"{label.patient_name_display}   DOB: {label.patient_dob_masked}"
        text(10, 82, 24, patient_line, bold=False)

        thin_line(108)

        # ── Row 3: Drug ──
        drug_line = label.drug_name.upper()
        if label.drug_strength:
            drug_line += f" {label.drug_strength}"
        text(10, 114, 26, drug_line, bold=False)
        detail = f"Qty: {int(label.quantity)}   Days: {label.days_supply}   Refills left: {label.refills_remaining}"
        text(10, 144, 18, detail)

        # SIG — largest, most prominent
        sig = label.sig_text or "Take as directed by prescriber"
        # Wrap long SIG into two lines
        if len(sig) > 55:
            sig1, sig2 = sig[:55], sig[55:110]
        else:
            sig1, sig2 = sig, ""
        text(10, 166, 22, sig1)
        if sig2:
            text(10, 190, 22, sig2)

        thick_line(214)

        # ── Row 4: Prescriber ──
        text(10, 220, 18, f"Prescriber: {label.prescriber_name}  NPI: {label.prescriber_npi}")
        text(10, 238, 16, f"Fill #{label.fill_number} of {label.refills_remaining + label.fill_number}")

        # ── Controlled substance banner ──
        if label.is_controlled:
            zpl(f"^FO10,256^GB{self.PRINT_WIDTH - 20},18,18^FS")
            zpl(f"^FO15,258^FR^A0N,14,14^FD")
            sched = label.dea_schedule or "CII"
            zpl(f"CAUTION: FEDERAL LAW PROHIBITS TRANSFER  ·  {sched} CONTROLLED SUBSTANCE")
            zpl("^FS")
            barcode_y = 278
        else:
            barcode_y = 258

        # ── Auxiliary labels (text strip) ──
        if label.auxiliary_labels:
            aux_objects = label.aux_label_objects
            aux_texts   = "  ".join(f"[{a.icon} {a.text}]" for a in aux_objects[:3])
            text(10, barcode_y, 15, aux_texts)
            barcode_y += 18

        # ── Code 128 barcode (Rx number) ──
        zpl(f"^FO10,{barcode_y}")
        zpl(f"^BCN,40,N,N,N")                 # Code 128, height=40, no HRI below
        zpl(f"^FD{label.rx_number}^FS")

        # Human-readable Rx# under barcode
        text(10, barcode_y + 44, 16, label.rx_number)

        # ── QR code (structured: rxnum|ndc|patient_last|qty) ──
        qr_payload = f"{label.rx_number}|{label.ndc11}|{label.patient_last}|{int(label.quantity)}"
        zpl(f"^FO700,{barcode_y}^BQN,2,2^FDQA,{_safe(qr_payload)}^FS")

        # ── End label ──
        zpl("^XZ")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Auxiliary: render auxiliary warning labels as separate small slips
    # ------------------------------------------------------------------

    def render_auxiliary_slips(self, label: LabelData) -> list[str]:
        """
        Returns one ZPL string per auxiliary warning label.
        Sized for 1"×0.5" (203×101 dots) warning stickers.
        """
        slips = []
        for code in label.auxiliary_labels:
            aux = AUXILIARY_LABELS.get(code)
            if not aux:
                continue
            lines = [
                "^XA",
                "^PW203",
                "^LL101",
                f"^FO10,10^A0N,20,20^FD{_safe(aux.icon)} {_safe(aux.text)}^FS",
                "^XZ",
            ]
            slips.append("\n".join(lines))
        return slips


def _safe(value: str, max_len: int = 120) -> str:
    """Strip ZPL control characters and truncate."""
    return str(value or "").replace("^", "").replace("~", "")[:max_len]
