"""PHI egress scrubber — the single choke point before any prompt leaves for an LLM.

PharmPilot's rule: **no direct patient identifier may reach any LLM, even a BAA
provider.** The model only needs de-identified clinical facts (age, conditions,
meds, labs, allergies, drug/dose). This module redacts patterned identifiers
(phone, national ID, email, long digit runs, ``label: value`` PII) from a prompt
and returns the scrubbed text plus the list of categories it hit — never the
value. It is wired into ``local_llm.generate`` so every generation path is
covered by construction.

Design intent: in normal operation this should NOT fire — the codebase already
keeps identifiers out of prompts. A non-empty ``hits`` list is a tripwire that a
caller is leaking; fix the source, don't rely on the scrub.
"""
from __future__ import annotations

import re

# ``label: value`` PII — redact the value, keep the label. Bare "name" is
# deliberately excluded (would eat "drug name: warfarin"); only qualified name
# labels match.
_LABELED = re.compile(
    r"(?im)(?<![A-Za-z])"
    r"(patient\s+name|full\s+name|first\s+name|last\s+name|father'?s?\s*name|"
    r"phone(?:\s*number)?|telephone|tel|mobile|cell|"
    r"national\s*id|nid|mrn|medical\s+record(?:\s+number)?|"
    r"address|ssn|dob|date\s+of\s+birth|e-?mail)"
    r"\s*[:=]\s*(.+)$"
)

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Iranian mobile, with or without +98/0 prefix and space/dash separators.
_PHONE = re.compile(r"(?:\+?98[\s-]?|0)9(?:[\s-]?\d){9}")
_NATIONAL_ID = re.compile(r"(?<!\d)\d{10}(?!\d)")   # کد ملی is exactly 10 digits
_LONG_DIGITS = re.compile(r"(?<!\d)\d{7,}(?!\d)")    # MRN / other long ID runs


def _label_category(label: str) -> str:
    low = re.sub(r"\s+", " ", label.strip().lower())
    if "name" in low:
        return "name"
    if low in {"phone", "phone number", "telephone", "tel", "mobile", "cell"}:
        return "phone"
    if low in {"national id", "nationalid", "nid"}:
        return "national_id"
    if "mrn" in low or "medical record" in low:
        return "mrn"
    if low in {"dob", "date of birth"}:
        return "dob"
    if "mail" in low:
        return "email"
    if low == "ssn":
        return "ssn"
    if low == "address":
        return "address"
    return "identifier"


def scrub_identifiers(text: str | None) -> tuple[str, list[str]]:
    """Redact direct identifiers from ``text``.

    Returns ``(scrubbed_text, hit_categories)``. ``hit_categories`` lists the
    kinds of identifier found (e.g. ``["phone", "name"]``), never the values, so
    it is safe to log.
    """
    if not text:
        return "", []

    hits: list[str] = []

    def _add(cat: str) -> None:
        if cat not in hits:
            hits.append(cat)

    def _labeled_repl(m: re.Match[str]) -> str:
        _add(_label_category(m.group(1)))
        return f"{m.group(1)}: [REDACTED]"

    out = _LABELED.sub(_labeled_repl, text)

    out, n = _EMAIL.subn("[EMAIL]", out)
    if n:
        _add("email")
    out, n = _PHONE.subn("[PHONE]", out)
    if n:
        _add("phone")
    out, n = _NATIONAL_ID.subn("[ID]", out)
    if n:
        _add("national_id")
    out, n = _LONG_DIGITS.subn("[NUM]", out)
    if n:
        _add("id_number")

    return out, hits
