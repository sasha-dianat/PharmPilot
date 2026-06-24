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


from services.ai.clinical_decision_support.physician_letter.templates import (
    deterministic_template, LETTER_VERSION,
)
from services.ai.clinical_decision_support.physician_letter.content import ClinicalContent
from services.ai.clinical_decision_support.physician_letter.placeholders import substitute

_C = ClinicalContent(warning="A contraindicated interaction was found.",
                     mechanism="MAOI + serotonergic agent.", drugs=["warfarin", "phenelzine"])
_VALUES = {
    "{{PATIENT_NAME}}": "Ali Karimi", "{{PATIENT_NATIONAL_ID}}": "1234567890",
    "{{PHYSICIAN_NAME}}": "Dr Who", "{{COUNCIL_ID}}": "NP-77",
    "{{PHARMACIST_NAME}}": "Pat Pharm", "{{PHARMACIST_LICENSE}}": "LIC-9",
    "{{PHARMACY_NAME}}": "Central Pharmacy", "{{DATE}}": "2026-06-24",
}


def test_persian_template_inlines_content_and_substitutes():
    tpl = deterministic_template(_C, "fa")
    assert "warfarin" in tpl and "{{PATIENT_NAME}}" in tpl   # content inlined, identifiers as tokens
    letter = substitute(tpl, _VALUES)
    assert "Ali Karimi" in letter and "NP-77" in letter and "{{" not in letter


def test_english_template_available():
    assert "{{PHYSICIAN_NAME}}" in deterministic_template(_C, "en")


def test_unknown_language_falls_back_to_persian():
    assert deterministic_template(_C, "de") == deterministic_template(_C, "fa")


def test_letter_version_present():
    assert LETTER_VERSION


from services.ai.clinical_decision_support.physician_letter.validate import is_safe_template


def test_validate_accepts_clean_placeholder_template():
    assert is_safe_template("Dear {{PHYSICIAN_NAME}} ({{COUNCIL_ID}}), warning text.")


def test_validate_rejects_unknown_token():
    assert not is_safe_template("Dear {{DOCTOR}}")


def test_validate_rejects_stray_identifier():
    # model invented a national-id-like number instead of using the placeholder
    assert not is_safe_template("Patient national id 1234567890 has a problem.")


def test_validate_rejects_email_leak():
    assert not is_safe_template("contact dr@example.com")
