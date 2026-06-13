from __future__ import annotations

import pytest

from services.ai.counselling.engine import build_baseline


@pytest.mark.parametrize("drug", ["amoxicillin", "lisinopril", "warfarin", "metformin", "tramadol"])
def test_baseline_content_has_required_sections(drug):
    content = build_baseline(drug, "standard")

    assert content is not None
    assert content.what_for
    assert content.how_to_take
    assert content.what_to_avoid
    assert content.common_side_effects
    assert content.serious_red_flags
    assert content.missed_dose
    assert content.adherence_tips
    assert content.teach_back_questions
    assert len(content.serious_red_flags) >= 1
    assert content.language == "en"


def test_class_fallback_for_unlisted_statin():
    content = build_baseline("rosuvastatin", "standard")

    assert content is not None
    assert "cholesterol" in content.what_for.lower()
    assert any("muscle" in item.lower() for item in content.serious_red_flags)


def test_unknown_drug_returns_none():
    assert build_baseline("mysterymed") is None
