from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from services.ai.adr_detective.narrator import narrate
from services.ai.adr_detective.schema import ADRContext, ADRMedication, SuspectedCause
from services.ai.adr_detective.validator import validate
from services.ai.clinical_decision_support.normalizer import classes_of, normalize


def med(name: str) -> ADRMedication:
    normalized = normalize(name)
    return ADRMedication(name, normalized, classes_of(normalized))


def cause(causality: str = "possible") -> SuspectedCause:
    return SuspectedCause(
        drug="lisinopril",
        normalized_name="lisinopril",
        reaction="cough",
        causality=causality,
        seriousness="mild",
        reasoning=["deterministic wording"],
        suggested_pharmacist_action="Assess symptom timing and discuss concern with prescriber if needed.",
        urgency="low",
        evidence_sources=["FDA label: lisinopril"],
    )


def context() -> ADRContext:
    return ADRContext(complaint="dry cough", medications=[med("lisinopril")])


def test_validator_rejects_hallucinated_drug_in_payload_text():
    payload = {
        "causes": [
            {
                "drug": "lisinopril",
                "causality": "possible",
                "reasoning": ["Losartan is another likely explanation."],
                "alternative_explanations": [],
                "questions_to_ask": [],
            }
        ]
    }

    _clean, ok = validate(payload, [cause()], context())

    assert ok is False


def test_validator_rejects_causality_upgrade():
    payload = {
        "causes": [
            {
                "drug": "lisinopril",
                "causality": "probable",
                "reasoning": ["This is now probable."],
                "alternative_explanations": [],
                "questions_to_ask": [],
            }
        ]
    }

    _clean, ok = validate(payload, [cause("possible")], context())

    assert ok is False


def test_validator_rejects_stop_start_or_dose_directives():
    payload = {
        "causes": [
            {
                "drug": "lisinopril",
                "causality": "possible",
                "reasoning": ["Stop lisinopril today."],
                "alternative_explanations": [],
                "questions_to_ask": ["Should the dose be reduced?"],
            }
        ]
    }

    _clean, ok = validate(json.dumps(payload), [cause()], context())

    assert ok is False


@pytest.mark.asyncio
async def test_degraded_or_empty_llm_uses_deterministic_template(monkeypatch):
    async def degraded_generate(*_args, **_kwargs):
        return SimpleNamespace(text="", provider="none", degraded=True)

    monkeypatch.setattr("services.ai.adr_detective.narrator.generate", degraded_generate)

    causes, meta = await narrate([cause()], context())

    assert causes[0].reasoning == ["deterministic wording"]
    assert meta.llm_used is False
    assert meta.degraded is True
