"""
Pharmacovigilance Engine — Phase 22-D
=======================================
Local, offline adverse event signal detection on your own dispensing + clinical data.
No external API required. No cloud model hosting.

Components:
  SignalDetector    — PRR/ROR disproportionality analysis on your own patient events
  CUSUMSignal       — Change-point detection for emerging safety signals
  ADRCaseRegistry   — Internal adverse drug reaction case management workflow
"""
from .signal_detector import SignalDetector, ADRSignal, CUSUMSignalMonitor, ADRCaseRegistry

__all__ = [
    "SignalDetector",
    "ADRSignal",
    "CUSUMSignalMonitor",
    "ADRCaseRegistry",
]
