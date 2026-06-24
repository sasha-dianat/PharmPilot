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


from services.ai.clinical_decision_support.physician_letter.content import (
    build_clinical_content, ClinicalContent,
)
from services.ai.intelligence_core.phi_scrub import scrub_identifiers


def test_build_content_is_deidentified():
    findings = [{
        "participants": [{"name": "warfarin"}, {"name": "phenelzine"}],
        "mechanism": "MAOI with serotonergic agent: hypertensive crisis.",
        "mechanism_basis": "serotonergic + MAOI",
        "severity": "Contraindicated",
    }]
    c = build_clinical_content(findings)
    assert isinstance(c, ClinicalContent)
    assert "warfarin" in c.drugs and "phenelzine" in c.drugs
    assert "hypertensive" in c.mechanism
    blob = f"{c.warning} {c.mechanism} {' '.join(c.drugs)}"
    _, hits = scrub_identifiers(blob)
    assert hits == []   # no identifiers in clinical content
