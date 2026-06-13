from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.ai.counselling.engine import build_baseline
from services.ai.counselling.knowledge import resolve_facts
from services.ai.counselling.narrator import render
from services.ai.counselling.validator import validate


def _baseline(drug: str = "lisinopril"):
    normalized, facts = resolve_facts(drug)
    content = build_baseline(drug, "standard")
    assert normalized
    assert facts is not None
    assert content is not None
    return facts, content


def _payload(content):
    return {
        "what_for": content.what_for,
        "how_to_take": content.how_to_take,
        "what_to_avoid": content.what_to_avoid,
        "common_side_effects": content.common_side_effects,
        "serious_red_flags": content.serious_red_flags,
        "missed_dose": content.missed_dose,
        "adherence_tips": content.adherence_tips,
        "teach_back_questions": content.teach_back_questions,
    }


def test_validator_rejects_foreign_drug_name():
    facts, baseline = _baseline("lisinopril")
    payload = _payload(baseline)
    payload["what_for"] = "This is like losartan for blood pressure."

    clean, ok = validate(payload, facts, baseline)

    assert clean is None
    assert ok is False


def test_validator_rejects_dropped_serious_red_flag():
    facts, baseline = _baseline("warfarin")
    payload = _payload(baseline)
    payload["serious_red_flags"] = baseline.serious_red_flags[:-1]

    clean, ok = validate(payload, facts, baseline)

    assert clean is None
    assert ok is False


def test_validator_rejects_added_numeric_dose():
    facts, baseline = _baseline("atorvastatin")
    payload = _payload(baseline)
    payload["how_to_take"] = "Take 80 mg every day."

    clean, ok = validate(payload, facts, baseline)

    assert clean is None
    assert ok is False


def test_validator_rejects_stop_start_or_dose_directive():
    facts, baseline = _baseline("metoprolol")
    payload = _payload(baseline)
    payload["missed_dose"] = "If you miss it, double the dose next time."

    clean, ok = validate(payload, facts, baseline)

    assert clean is None
    assert ok is False


def test_validator_accepts_faithful_translation_shaped_payload():
    facts, baseline = _baseline("metformin")
    payload = {
        "what_for": "Ayuda a controlar el azucar en sangre.",
        "how_to_take": "Tomelo con comidas como fue recetado.",
        "what_to_avoid": ["Mucho alcohol", "Deshidratacion", "Omitir controles de rinon"],
        "common_side_effects": ["Diarrea", "Nausea", "Gas o malestar estomacal"],
        "serious_red_flags": ["Debilidad extrema", "Dificultad para respirar", "Dolor fuerte de estomago con vomitos"],
        "missed_dose": "Si olvida una dosis, tomela con comida cuando lo recuerde, salvo que este cerca de la siguiente.",
        "adherence_tips": ["Tomelo con comida", "Mantenga controles de laboratorio", "Consulte si los efectos estomacales continuan"],
        "teach_back_questions": ["Para que es?", "Como lo tomara?", "Que hara si olvida una dosis?", "Que sintomas requieren llamar?"],
    }

    clean, ok = validate(payload, facts, baseline)

    assert ok is True
    assert clean is not None
    assert clean.what_for.startswith("Ayuda")


@pytest.mark.asyncio
async def test_degraded_or_empty_llm_uses_english_baseline(monkeypatch):
    async def degraded_generate(*_args, **_kwargs):
        return SimpleNamespace(text="", provider="none", degraded=True)

    monkeypatch.setattr("services.ai.counselling.narrator.local_llm.generate", degraded_generate)
    facts, baseline = _baseline("sertraline")

    content, meta = await render(baseline, facts, "low_literacy", "es")

    assert content.language == "en"
    assert content.level == "standard"
    assert content.what_for == baseline.what_for
    assert meta.llm_used is False
    assert meta.degraded is True
