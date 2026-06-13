from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from services.ai.physician_message.builder import build_baseline
from services.ai.physician_message.narrator import render
from services.ai.physician_message.schema import MessageInput
from services.ai.physician_message.validator import validate


def _input() -> MessageInput:
    return MessageInput(
        medication_issue="Lisinopril may be contributing to persistent cough",
        clinical_rationale="Cough started after lisinopril initiation",
        recommendation_or_question="Would you consider evaluating lisinopril as a possible cause?",
        urgency="routine",
        supporting_data=["dry cough for three weeks"],
    )


def _payload(**overrides):
    baseline = build_baseline(_input(), "sbar")
    sections = dict(baseline.sections)
    sections.update(overrides)
    return {"language": "es", "sections": sections}


def test_validator_rejects_foreign_drug():
    baseline = build_baseline(_input(), "sbar")
    payload = _payload(assessment="Losartan is a better explanation. Urgency: routine.")

    clean, ok = validate(json.dumps(payload), _input(), baseline)

    assert clean is None
    assert ok is False


def test_validator_rejects_added_numeric_dose():
    baseline = build_baseline(_input(), "sbar")
    payload = _payload(recommendation="Would you consider lisinopril 20mg? Urgency: routine.")

    clean, ok = validate(payload, _input(), baseline)

    assert clean is None
    assert ok is False


def test_validator_rejects_urgency_change():
    baseline = build_baseline(_input(), "sbar")
    payload = _payload(situation="Urgency: emergent. I am writing to flag the cough concern.")

    clean, ok = validate(payload, _input(), baseline)

    assert clean is None
    assert ok is False


def test_validator_rejects_dropped_recommendation():
    baseline = build_baseline(_input(), "sbar")
    payload = _payload(recommendation="")

    clean, ok = validate(payload, _input(), baseline)

    assert clean is None
    assert ok is False


def test_validator_accepts_faithful_translation_shaped_payload():
    baseline = build_baseline(_input(), "sbar")
    payload = _payload(
        situation="Urgency: routine. Escribo para señalar una preocupación sobre lisinopril y tos.",
        background="Contexto del paciente no suministrado. Datos: tos seca por tres semanas.",
        assessment="La tos empezó después de iniciar lisinopril.",
        recommendation="¿Consideraría evaluar lisinopril como posible causa?",
    )

    clean, ok = validate(payload, _input(), baseline)

    assert ok is True
    assert clean is not None
    assert "Escribo" in clean.body


@pytest.mark.asyncio
async def test_degraded_or_empty_llm_returns_english_baseline(monkeypatch):
    async def degraded_generate(*_args, **_kwargs):
        return SimpleNamespace(text="", provider="none", degraded=True)

    monkeypatch.setattr("services.ai.physician_message.narrator.local_llm.generate", degraded_generate)
    baseline = build_baseline(_input(), "sbar")

    content, meta = await render(baseline, _input(), "sbar", "es")

    assert content.body == baseline.body
    assert content.language == "en"
    assert meta.llm_used is False
    assert meta.degraded is True

