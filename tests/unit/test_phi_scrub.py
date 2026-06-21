"""Tests for the LLM-egress PHI scrubber.

The scrubber is the single choke point that guarantees no direct patient
identifier leaves the process inside an LLM prompt — even for BAA providers.
It redacts patterned identifiers (phone, national ID, email, long digit runs,
`label: value` PII) and reports which categories it hit, never the value.
"""
from __future__ import annotations

from services.ai.intelligence_core.phi_scrub import scrub_identifiers


def test_iranian_phone_is_redacted():
    out, hits = scrub_identifiers("Patient on warfarin, call 09301234567 to confirm.")
    assert "09301234567" not in out
    assert "warfarin" in out  # clinical content preserved
    assert "phone" in hits


def test_intl_phone_is_redacted():
    out, hits = scrub_identifiers("reach at +98 930 123 4567 today")
    assert "9301234567" not in out.replace(" ", "")
    assert "phone" in hits


def test_ten_digit_national_id_is_redacted():
    out, hits = scrub_identifiers("کد ملی 1234567890 for the chart")
    assert "1234567890" not in out
    assert "national_id" in hits


def test_email_is_redacted():
    out, hits = scrub_identifiers("contact ali.karimi@example.com about refill")
    assert "ali.karimi@example.com" not in out
    assert "email" in hits


def test_labeled_name_value_is_redacted():
    out, hits = scrub_identifiers("Patient name: Ali Karimi\nAge: 28\nDrug: warfarin")
    assert "Ali Karimi" not in out
    assert "28" in out and "warfarin" in out  # clinical facts survive
    assert "name" in hits


def test_long_digit_run_is_redacted():
    out, hits = scrub_identifiers("MRN 8842317 prescribed metformin")
    assert "8842317" not in out
    assert "metformin" in out


def test_clinical_text_without_identifiers_is_untouched():
    text = "Age 64, eGFR 38, on metformin 500 mg BID, NKDA. Consider dose review."
    out, hits = scrub_identifiers(text)
    assert out == text
    assert hits == []


def test_short_clinical_numbers_are_preserved():
    # doses, labs, ages must NOT be treated as identifiers
    text = "warfarin 5 mg, INR 2.4, age 72, eGFR 45"
    out, _ = scrub_identifiers(text)
    assert "5 mg" in out and "2.4" in out and "72" in out and "45" in out


def test_none_and_empty_are_safe():
    assert scrub_identifiers("") == ("", [])
    assert scrub_identifiers(None) == ("", [])


def test_generate_scrubs_prompt_before_provider_call(monkeypatch):
    """local_llm.generate must scrub identifiers before the prompt leaves."""
    import asyncio
    import types

    from services.ai.intelligence_core import local_llm
    from services.ai.provider_registry import registry as reg_mod

    captured: dict[str, str] = {}

    class _Resp:
        content, provider, model = "ok", "ollama_local", "test"

    class _FakeRegistry:
        async def call(self, *, prompt, system_prompt=None, **kw):
            captured["prompt"] = prompt
            captured["system"] = system_prompt or ""
            return _Resp()

    monkeypatch.setattr(reg_mod, "get_ai_registry", lambda: _FakeRegistry())
    monkeypatch.setattr(local_llm, "network_up", lambda: False)  # force local path

    asyncio.run(local_llm.generate(
        "Patient on warfarin, call 09301234567",
        system="Patient name: Ali Karimi",
        prefer_local=True,
    ))

    assert "09301234567" not in captured["prompt"]
    assert "Ali Karimi" not in captured["system"]
    assert "warfarin" in captured["prompt"]  # clinical content survives
