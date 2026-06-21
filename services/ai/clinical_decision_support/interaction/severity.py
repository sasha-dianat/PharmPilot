from __future__ import annotations

from enum import Enum


class InteractionSeverity(str, Enum):
    CONTRAINDICATED = "Contraindicated"
    MAJOR = "Major"
    MODERATE = "Moderate"
    MINOR = "Minor"

    @property
    def rank(self) -> int:
        return {"Minor": 0, "Moderate": 1, "Major": 2, "Contraindicated": 3}[self.value]


_ORDER = [
    InteractionSeverity.MINOR,
    InteractionSeverity.MODERATE,
    InteractionSeverity.MAJOR,
    InteractionSeverity.CONTRAINDICATED,
]

_LEGACY = {
    "CRITICAL": InteractionSeverity.MAJOR,
    "HIGH": InteractionSeverity.MAJOR,
    "MODERATE": InteractionSeverity.MODERATE,
    "LOW": InteractionSeverity.MINOR,
    "INFO": InteractionSeverity.MINOR,
}


def normalize_severity(raw: str, *, contraindicated: bool = False) -> InteractionSeverity:
    if contraindicated:
        return InteractionSeverity.CONTRAINDICATED
    key = (raw or "").strip()
    try:
        return InteractionSeverity(key.title())        # already canonical ("Major")
    except ValueError:
        return _LEGACY.get(key.upper(), InteractionSeverity.MINOR)


def step(sev: InteractionSeverity, delta: int) -> InteractionSeverity:
    idx = max(0, min(len(_ORDER) - 1, _ORDER.index(sev) + delta))
    return _ORDER[idx]


# inhibitor/inducer strength × victim fm-bin → base severity (spec §2)
_MATRIX = {
    ("strong", "high"): InteractionSeverity.MAJOR,
    ("strong", "low"): InteractionSeverity.MODERATE,
    ("moderate", "high"): InteractionSeverity.MODERATE,
    ("moderate", "low"): InteractionSeverity.MINOR,
    ("weak", "high"): InteractionSeverity.MINOR,
    ("weak", "low"): InteractionSeverity.MINOR,
}


def magnitude_to_severity(strength: str, fm_bin: str) -> InteractionSeverity:
    return _MATRIX.get((strength, fm_bin), InteractionSeverity.MINOR)
