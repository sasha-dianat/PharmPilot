"""Provider backends — Gemini response parsing/grounding + the factory.
Fully offline: the HTTP post is injected, no key or network required."""
import json

import pytest

from services.ai.enrichment.providers import (
    PROVIDER_PACE, SEARCH_FNS, _gemini_text, gemini_search_fn, make_researcher)


def _payload(text, uris=()):
    cand = {"content": {"parts": [{"text": text}]}}
    if uris:
        cand["groundingMetadata"] = {
            "groundingChunks": [{"web": {"uri": u}} for u in uris]}
    return {"candidates": [cand]}


def test_gemini_text_joins_parts():
    p = {"candidates": [{"content": {"parts": [{"text": "a"}, {"text": "b"}]}}]}
    assert _gemini_text(p) == "a\nb"


def test_gemini_text_raises_when_no_candidates():
    with pytest.raises(RuntimeError):
        _gemini_text({"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}})


def test_gemini_grafts_grounding_uris_into_empty_sources():
    # Grounding URIs are pages Google actually fetched — stronger provenance
    # than URLs the model merely claims, so they fill an empty `sources`.
    reply = '{"generic":"vitamin a","dosage_form":"softgel"}'
    out = _gemini_text(_payload(reply, ["https://a.example", "https://b.example"]))
    assert json.loads(out)["sources"] == ["https://a.example", "https://b.example"]


def test_gemini_keeps_model_supplied_sources():
    reply = '{"generic":"x","sources":["https://model-said.example"]}'
    out = _gemini_text(_payload(reply, ["https://grounding.example"]))
    assert json.loads(out)["sources"] == ["https://model-said.example"]


def test_gemini_unparseable_text_passes_through_untouched():
    out = _gemini_text(_payload("not json at all", ["https://a.example"]))
    assert out == "not json at all"      # researcher reports the parse failure


def test_gemini_search_fn_end_to_end_with_injected_post(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "AIzaFAKE")
    seen = {}

    def fake_post(url, body):
        seen["url"] = url
        seen["body"] = body
        return _payload('{"generic":"vitamin a","confidence":0.9}',
                        ["https://src.example"])

    txt = gemini_search_fn("prompt-x", "system-y", _post=fake_post)
    assert json.loads(txt)["sources"] == ["https://src.example"]
    # web search must actually be requested, else the model would confabulate
    assert seen["body"]["tools"] == [{"google_search": {}}]
    assert seen["body"]["systemInstruction"]["parts"][0]["text"] == "system-y"
    assert "AIzaFAKE" in seen["url"]


def test_gemini_rejects_oauth_style_key(monkeypatch):
    # The AQ.* token in ai_provider_settings is an OAuth token, not a Gemini key;
    # fail with an actionable message instead of a bare 401.
    monkeypatch.setenv("GOOGLE_API_KEY", "AQ.Ab8RN6JfakeOAuthToken")
    with pytest.raises(RuntimeError, match="aistudio"):
        gemini_search_fn("p", "s", _post=lambda u, b: {})


def test_gemini_missing_key_raises(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        gemini_search_fn("p", "s", _post=lambda u, b: {})


def test_factory_rejects_non_searching_provider():
    # groq has no web search on standard models — must not silently degrade
    with pytest.raises(ValueError, match="groq"):
        make_researcher("groq")
    with pytest.raises(ValueError):
        make_researcher("")


def test_factory_builds_known_providers():
    for name in ("mistral", "gemini", "GEMINI"):
        assert make_researcher(name) is not None
    assert set(SEARCH_FNS) == {"mistral", "gemini"}
    assert PROVIDER_PACE["gemini"] >= 4.0      # free tier is 15 RPM
