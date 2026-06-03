"""
Persian / Arabic digit normalization.
======================================
Iranian prescriptions, insurance portals, and speech-to-text output frequently
contain Persian (۰-۹) or Arabic-Indic (٠-٩) digits. Every numeric extraction
(national code, phone, dates, dosages) must normalize to ASCII before parsing.
"""

# Persian (Farsi) digits ۰۱۲۳۴۵۶۷۸۹
_PERSIAN = "۰۱۲۳۴۵۶۷۸۹"
# Arabic-Indic digits ٠١٢٣٤٥٦٧٨٩
_ARABIC = "٠١٢٣٤٥٦٧٨٩"
_ASCII = "0123456789"

_TO_ASCII = {ord(p): a for p, a in zip(_PERSIAN, _ASCII)}
_TO_ASCII.update({ord(a): d for a, d in zip(_ARABIC, _ASCII)})
# Persian thousands separator + Arabic decimal/comma → normalize
_TO_ASCII.update({ord("٫"): ".", ord("٬"): ",", ord("،"): ","})

_TO_PERSIAN = {ord(a): p for a, p in zip(_ASCII, _PERSIAN)}


def normalize_digits(text: str) -> str:
    """Convert any Persian/Arabic digits in a string to ASCII 0-9."""
    if not text:
        return text
    return text.translate(_TO_ASCII)


def to_persian_digits(value) -> str:
    """Convert ASCII digits in a value to Persian digits (for display)."""
    return str(value).translate(_TO_PERSIAN)


def extract_digits(text: str) -> str:
    """Return only the ASCII digits from a (possibly Persian) string."""
    normalized = normalize_digits(text or "")
    return "".join(ch for ch in normalized if ch.isdigit())
