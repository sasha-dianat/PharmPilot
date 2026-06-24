import pytest
from services.ai.clinical_decision_support.physician_letter.placeholders import (
    ALLOWED_TOKENS, substitute,
)


def test_allowed_tokens_set():
    assert "{{PATIENT_NAME}}" in ALLOWED_TOKENS
    assert "{{COUNCIL_ID}}" in ALLOWED_TOKENS


def test_substitute_replaces_present_tokens():
    out = substitute("Dr {{PHYSICIAN_NAME}} / {{COUNCIL_ID}}",
                     {"{{PHYSICIAN_NAME}}": "Who", "{{COUNCIL_ID}}": "NP-7"})
    assert out == "Dr Who / NP-7"
    assert "{{" not in out


def test_substitute_raises_on_unfilled_token():
    with pytest.raises(ValueError):
        substitute("Hi {{PATIENT_NAME}}", {})   # token present, no value → leftover {{


def test_substitute_rejects_unknown_token():
    with pytest.raises(ValueError):
        substitute("{{NOT_A_TOKEN}}", {"{{NOT_A_TOKEN}}": "x"})
