from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


MODEL_VERSION = "physmsg-v1"

FORMATS = ("sbar", "soap", "concise", "letter")
LANGUAGES = ("en", "fr", "fa", "ar", "es")
URGENCIES = ("routine", "urgent", "emergent")

MessageFormat = Literal["sbar", "soap", "concise", "letter"]
MessageLanguage = Literal["en", "fr", "fa", "ar", "es"]
MessageUrgency = Literal["routine", "urgent", "emergent"]

PHARMACIST_VERIFICATION_NOTICE = (
    "Advisory physician communication support only. A licensed pharmacist must verify, edit, "
    "and send any message; the system does not transmit it."
)


@dataclass(frozen=True)
class MessageInput:
    medication_issue: str
    recommendation_or_question: str
    urgency: str = "routine"
    prescriber_name: str | None = None
    patient_context: str | None = None
    clinical_rationale: str | None = None
    supporting_data: list[str] = field(default_factory=list)
    pharmacist_name: str | None = None


@dataclass
class MessageContent:
    format: str
    language: str
    subject: str
    body: str
    urgency: str
    sections: dict[str, str]


@dataclass(frozen=True)
class LLMMeta:
    llm_used: bool
    provider: str
    degraded: bool
    note: str | None = None

