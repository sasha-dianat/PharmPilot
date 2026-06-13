"""
Rx Label Engine — Phase 23
===========================
Generates dispensing labels in three output modes:

  thermal_pdf   — HTML/CSS renderable in browser for PDF/print
  thermal_zpl   — ZPL II string for Zebra LP2844 / GK420d / ZD420 printers
  handwritten   — Reference worksheet only; pharmacist handwrites the label
                  manually. No print command is issued. The system records
                  that the label was produced by hand.

Components:
  LabelGenerator       — Assembles LabelData from Rx + patient + pharmacy records
  ZPLRenderer          — Converts LabelData to ZPL II thermal commands
  AuxiliaryLabelLibrary — Color-coded warning label catalog
"""
from .label_generator import LabelGenerator, LabelData, AuxiliaryLabel, PrintMode
from .zpl_renderer import ZPLRenderer

__all__ = [
    "LabelGenerator",
    "LabelData",
    "AuxiliaryLabel",
    "PrintMode",
    "ZPLRenderer",
]
