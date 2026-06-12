from __future__ import annotations

import pytest

from services.ai.physician_message.builder import build_baseline
from services.ai.physician_message.schema import FORMATS, MessageInput


def _input() -> MessageInput:
    return MessageInput(
        prescriber_name="Nguyen",
        patient_context="72-year-old patient with chronic kidney disease",
        medication_issue="Concurrent ibuprofen use with apixaban may increase bleeding risk",
        clinical_rationale="The patient reports daily NSAID use while anticoagulated",
        recommendation_or_question="Would you consider an alternative pain plan or advise on NSAID avoidance?",
        urgency="urgent",
        supporting_data=["apixaban active on profile", "ibuprofen purchased this week"],
        pharmacist_name="A. Pharmacist",
    )


@pytest.mark.parametrize("fmt", FORMATS)
def test_each_format_contains_required_pharmacist_supplied_content(fmt: str):
    content = build_baseline(_input(), fmt)

    assert "Concurrent ibuprofen use with apixaban" in content.body
    assert "alternative pain plan" in content.body
    assert "Urgency: urgent" in content.body
    assert "ibuprofen purchased this week" in content.body
    assert content.language == "en"
    assert content.format == fmt


def test_sbar_has_required_sections():
    content = build_baseline(_input(), "sbar")

    assert set(content.sections) == {"situation", "background", "assessment", "recommendation"}
    assert "Situation:" in content.body
    assert "Background:" in content.body
    assert "Assessment:" in content.body
    assert "Recommendation:" in content.body


def test_letter_has_greeting_and_signoff():
    content = build_baseline(_input(), "letter")

    assert content.body.startswith("Dear Dr. Nguyen,")
    assert "Respectfully," in content.body
    assert "A. Pharmacist" in content.body

