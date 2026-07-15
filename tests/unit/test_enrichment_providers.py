"""Provider backends — Gemini Interactions API parsing/auth + the factory.
Fully offline: the HTTP post is injected, no key or network required."""
import json

import pytest

import services.ai.provider_registry.registry as _reg
from services.ai.enrichment.providers import (
    DEFAULT_GEMINI_MODEL, PROVIDER_PACE, SEARCH_FNS, _gemini_output,
    _gemini_text, gemini_ping, gemini_search_fn, make_researcher)
from services.ai.enrichment.providers import test_provider as verify_provider  # avoid pytest collection


@pytest.fixture(autouse=True)
def _isolated_keys(monkeypatch, tmp_path):
    # Keep the developer's real ~/.pharmpilot keys out of these tests: point the
    # registry at an absent settings file (fresh singleton) and clear the env.
    monkeypatch.setattr(_reg, "_AI_SETTINGS_PATH", tmp_path / "absent.json")
    monkeypatch.setattr(_reg, "_registry", None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_RESEARCH_MODEL", raising=False)


def _interaction(steps):
    return {"status": "completed", "steps": steps}


def _model_output(text, urls=()):
    return {"type": "model_output", "content": [{
        "type": "text", "text": text,
        "annotations": [{"type": "url_citation", "url": u} for u in urls],
    }]}


# ── auth: the API decides, never a prefix heuristic ──────────────────────────

def test_aq_prefixed_key_is_accepted_and_sent_via_header(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "  AQ.Ab8RN6LfakeKEYn92A  ")   # + whitespace
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    seen = {}

    def fake_post(url, body, headers=None):
        seen.update(url=url, body=body, headers=headers or {})
        return _interaction([_model_output('{"generic":"x"}')])

    gemini_search_fn("p", "s", _post=fake_post)
    assert seen["headers"]["x-goog-api-key"] == "AQ.Ab8RN6LfakeKEYn92A"  # trimmed only
    assert "Authorization" not in seen["headers"]          # never a Bearer token
    assert "key=" not in seen["url"]                       # never in the URL
    assert seen["url"].endswith("/v1beta/interactions")


def test_aiza_key_also_accepted_no_prefix_requirement(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyFakeKey123")
    out = gemini_search_fn("p", "s", _post=lambda u, b, headers=None:
                           _interaction([_model_output('{"generic":"y"}')]))
    assert json.loads(out)["generic"] == "y"


def test_empty_key_rejected(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "   ")
    with pytest.raises(RuntimeError, match="AI Hub"):
        gemini_search_fn("p", "s", _post=lambda u, b, headers=None: {})


def test_google_api_key_fallback(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "AQ.fallbackKey")
    seen = {}
    def fake_post(url, body, headers=None):
        seen["key"] = (headers or {}).get("x-goog-api-key")
        return _interaction([_model_output("hi")])
    gemini_search_fn("p", "s", _post=fake_post)
    assert seen["key"] == "AQ.fallbackKey"


# ── request shape ────────────────────────────────────────────────────────────

def test_default_model_and_google_search_tool(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AQ.k")
    monkeypatch.delenv("GEMINI_RESEARCH_MODEL", raising=False)
    seen = {}
    def fake_post(url, body, headers=None):
        seen["body"] = body
        return _interaction([_model_output("t")])
    gemini_search_fn("the-prompt", "the-system", _post=fake_post)
    assert seen["body"]["model"] == DEFAULT_GEMINI_MODEL == "gemini-2.5-flash-lite"
    assert seen["body"]["tools"] == [{"type": "google_search"}]   # real search, not roleplay
    assert "the-system" in seen["body"]["input"] and "the-prompt" in seen["body"]["input"]


def test_model_env_override(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AQ.k")
    monkeypatch.setenv("GEMINI_RESEARCH_MODEL", "gemini-2.5-flash")
    seen = {}
    def fake_post(url, body, headers=None):
        seen["m"] = body["model"]
        return _interaction([_model_output("t")])
    gemini_search_fn("p", "s", _post=fake_post)
    assert seen["m"] == "gemini-2.5-flash"


def test_ping_sends_no_tools(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AQ.k")
    seen = {}
    def fake_post(url, body, headers=None):
        seen["body"] = body
        return _interaction([_model_output("OK")])
    out = gemini_ping(_post=fake_post)
    assert out["ok"] and out["reply"] == "OK"
    assert "tools" not in seen["body"]


# ── response parsing ─────────────────────────────────────────────────────────

def test_text_only_from_model_output_thoughts_hidden():
    payload = _interaction([
        {"type": "thought", "content": [{"type": "text", "text": "SECRET reasoning"}]},
        {"type": "google_search_call", "query": "q"},
        {"type": "google_search_result", "content": []},
        _model_output("visible answer"),
    ])
    text, urls = _gemini_output(payload)
    assert text == "visible answer"
    assert "SECRET" not in text and urls == []


def test_citations_extracted_and_deduped():
    payload = _interaction([
        _model_output('{"generic":"x"}', urls=["https://a.example", "https://a.example",
                                               "https://b.example"]),
    ])
    text, urls = _gemini_output(payload)
    assert urls == ["https://a.example", "https://b.example"]
    # and grafted into empty sources by _gemini_text
    assert json.loads(_gemini_text(payload))["sources"] == urls


def test_model_supplied_sources_kept():
    payload = _interaction([
        _model_output('{"generic":"x","sources":["https://model.example"]}',
                      urls=["https://cite.example"])])
    assert json.loads(_gemini_text(payload))["sources"] == ["https://model.example"]


def test_list_wrapped_payload_and_missing_output():
    text, _ = _gemini_output([_interaction([_model_output("wrapped")])])
    assert text == "wrapped"
    with pytest.raises(RuntimeError, match="model_output"):
        _gemini_output(_interaction([{"type": "thought", "content": []}]))


def test_unparseable_text_passes_through():
    payload = _interaction([_model_output("not json", urls=["https://a.example"])])
    assert _gemini_text(payload) == "not json"   # researcher reports the parse failure


# ── error categories ─────────────────────────────────────────────────────────

def test_429_maps_to_quota_message_and_is_rate_limited():
    from services.ai.enrichment.providers import _http_post_json  # noqa: F401
    from services.ai.enrichment.service import _is_rate_limited
    import urllib.error, io
    from unittest import mock
    err = urllib.error.HTTPError("u", 429, "Too Many", {}, io.BytesIO(b'{"error":{}}'))
    with mock.patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(RuntimeError) as ei:
            _http_post_json("https://x.example", {}, headers={"x-goog-api-key": "AQ.k"})
    msg = str(ei.value)
    assert "Status 429" in msg and "سهمیه" in msg
    assert "AQ.k" not in msg                       # no secret in errors
    assert _is_rate_limited([msg])                 # batch backoff sees it


def test_401_is_reported_as_auth_failure_not_oauth_verdict():
    import urllib.error, io
    from unittest import mock
    from services.ai.enrichment.providers import _http_post_json
    err = urllib.error.HTTPError("u", 401, "Unauthorized", {},
                                 io.BytesIO(b'{"error":{"status":"UNAUTHENTICATED"}}'))
    with mock.patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(RuntimeError) as ei:
            _http_post_json("https://x.example", {}, headers={"x-goog-api-key": "AQ.k"})
    msg = str(ei.value)
    assert "Status 401" in msg
    assert "OAuth" not in msg and "AIza" not in msg   # no prefix-based verdicts


# ── factory ──────────────────────────────────────────────────────────────────

def test_factory_rejects_non_searching_provider():
    with pytest.raises(ValueError, match="groq"):
        make_researcher("groq")
    with pytest.raises(ValueError):
        verify_provider("groq")


def test_factory_builds_known_providers():
    for name in ("mistral", "gemini", "GEMINI"):
        assert make_researcher(name) is not None
    assert set(SEARCH_FNS) == {"mistral", "gemini"}
    assert PROVIDER_PACE["gemini"] >= 4.0      # free tier ~15 RPM
