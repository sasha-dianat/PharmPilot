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
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("YOUCOM_API_KEY", raising=False)


def _interaction(steps):
    return {"status": "completed", "steps": steps}


_RESULTS = [{"title": "VITAMIN A-TEDAGEL 25000IU", "url": "https://found.example/a",
             "snippet": "softgel 25000 IU"},
            {"title": "b", "url": "https://found.example/b", "snippet": "s2"}]


def _searcher(q, **kw):
    return list(_RESULTS)


def _model_output(text, urls=()):
    return {"type": "model_output", "content": [{
        "type": "text", "text": text,
        "annotations": [{"type": "url_citation", "url": u} for u in urls],
    }]}


# ── auth: the API decides, never a prefix heuristic ──────────────────────────

def test_aq_prefixed_key_is_accepted_and_sent_via_header(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "  AQ.Ab8RN6LfakeKEYn92A  ")   # + whitespace
    seen = {}

    def fake_post(url, body, headers=None):
        seen.update(url=url, body=body, headers=headers or {})
        return _interaction([_model_output('{"generic":"x"}')])

    gemini_search_fn("نام: «X»", "s", _post=fake_post, _search=_searcher)
    assert seen["headers"]["x-goog-api-key"] == "AQ.Ab8RN6LfakeKEYn92A"  # trimmed only
    assert "Authorization" not in seen["headers"]          # never a Bearer token
    assert "key=" not in seen["url"]                       # never in the URL
    assert seen["url"].endswith("/v1beta/interactions")


def test_aiza_key_also_accepted_no_prefix_requirement(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyFakeKey123")
    out = gemini_search_fn("«y-drug»", "s", _search=_searcher,
                           _post=lambda u, b, headers=None:
                           _interaction([_model_output('{"generic":"y","sources":["https://found.example/a"]}')]))
    assert json.loads(out)["generic"] == "y"


def test_empty_key_rejected(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "   ")
    with pytest.raises(RuntimeError, match="AI Hub"):
        gemini_search_fn("«p»", "s", _search=_searcher,
                         _post=lambda u, b, headers=None: {})


def test_google_api_key_fallback(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "AQ.fallbackKey")
    seen = {}
    def fake_post(url, body, headers=None):
        seen["key"] = (headers or {}).get("x-goog-api-key")
        return _interaction([_model_output("hi")])
    gemini_search_fn("«p»", "s", _post=fake_post, _search=_searcher)
    assert seen["key"] == "AQ.fallbackKey"


# ── request shape: strict free tier ──────────────────────────────────────────

def test_default_model_no_paid_search_tool_results_embedded(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AQ.k")
    seen = {}
    def fake_post(url, body, headers=None):
        seen["body"] = body
        return _interaction([_model_output("t")])
    gemini_search_fn("نام فرآورده: «ویتامین آ-تداژل»", "the-system",
                     _post=fake_post, _search=_searcher)
    assert seen["body"]["model"] == DEFAULT_GEMINI_MODEL == "gemini-3.1-flash-lite"
    assert "tools" not in seen["body"]          # free tier: NO paid google_search grounding
    assert "the-system" in seen["body"]["input"]
    assert "https://found.example/a" in seen["body"]["input"]   # search results embedded


def test_model_env_override(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AQ.k")
    monkeypatch.setenv("GEMINI_RESEARCH_MODEL", "gemini-4-flash")
    seen = {}
    def fake_post(url, body, headers=None):
        seen["m"] = body["model"]
        return _interaction([_model_output("t")])
    gemini_search_fn("«p»", "s", _post=fake_post, _search=_searcher)
    assert seen["m"] == "gemini-4-flash"


def test_search_results_urls_become_fallback_sources(monkeypatch):
    # Model omitted sources → top REAL search URLs fill in (never invented links)
    monkeypatch.setenv("GEMINI_API_KEY", "AQ.k")
    out = gemini_search_fn("«x»", "s", _search=_searcher,
                           _post=lambda u, b, headers=None:
                           _interaction([_model_output('{"generic":"x"}')]))
    assert json.loads(out)["sources"] == ["https://found.example/a", "https://found.example/b"]


def test_empty_search_results_fail_fast(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AQ.k")
    with pytest.raises(RuntimeError, match="جستجوی وب"):
        gemini_search_fn("«x»", "s", _search=lambda q, **kw: [],
                         _post=lambda u, b, headers=None: {})


def test_ddg_parsing_and_uddg_unwrap():
    from services.ai.enrichment.providers import ddg_search
    page = '''
    <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdarooyab.ir%2Fangipars&amp;rut=x">ANGIPARS &amp; info</a>
    <a class="result__snippet" href="#">herbal <b>drug</b> for diabetic foot</a>
    <a rel="nofollow" class="result__a" href="https://direct.example/page">Direct result</a>
    <a class="result__snippet" href="#">second snippet</a>
    '''
    rs = ddg_search("q", _get=lambda u, **kw: page)
    assert rs[0]["url"] == "https://darooyab.ir/angipars"      # uddg unwrapped
    assert rs[0]["title"] == "ANGIPARS & info"                 # entities + tags cleaned
    assert rs[0]["snippet"] == "herbal drug for diabetic foot"
    assert rs[1]["url"] == "https://direct.example/page"


def test_brave_search_parses_and_authenticates_via_header(monkeypatch):
    from services.ai.enrichment.providers import brave_search
    monkeypatch.setenv("BRAVE_API_KEY", " brv-key-123 ")
    seen = {}

    def fake_get(url, headers=None, **kw):
        seen.update(url=url, headers=headers or {})
        return json.dumps({"web": {"results": [
            {"title": "ANGIPARS caps", "url": "https://darooyab.ir/a",
             "description": "herbal drug for diabetic foot ulcers"},
            {"title": "", "url": "https://skip.me"},          # untitled → skipped
            {"title": "dup ok", "url": "https://b.example", "description": "d2"},
        ]}})

    rs = brave_search("angipars دارو", _get=fake_get)
    assert seen["headers"]["X-Subscription-Token"] == "brv-key-123"   # trimmed, in header
    assert "brv-key-123" not in seen["url"]                           # never in the URL
    assert rs[0] == {"title": "ANGIPARS caps", "url": "https://darooyab.ir/a",
                     "snippet": "herbal drug for diabetic foot ulcers"}
    assert len(rs) == 2


def test_brave_requires_key(monkeypatch):
    from services.ai.enrichment.providers import brave_search
    with pytest.raises(RuntimeError, match="AI Hub"):
        brave_search("q", _get=lambda u, **kw: "{}")


def test_web_search_seam_priority_youcom_brave_ddg(monkeypatch):
    from services.ai.enrichment import providers as ep
    assert ep.search_backend_name() == "duckduckgo"      # no key → free fallback
    monkeypatch.setenv("BRAVE_API_KEY", "brv")
    assert ep.search_backend_name() == "brave"
    monkeypatch.setenv("YOUCOM_API_KEY", "ydc-x")        # keyed You.com outranks brave
    assert ep.search_backend_name() == "youcom"


def test_youcom_search_parses_and_authenticates_via_header(monkeypatch):
    from services.ai.enrichment.providers import youcom_search
    monkeypatch.setenv("YOUCOM_API_KEY", " ydc-key-1 ")
    seen = {}

    def fake_get(url, headers=None, **kw):
        seen.update(url=url, headers=headers or {})
        # live-verified shape: results.web[] with description + snippets[]
        return json.dumps({"results": {"web": [
            {"url": "https://darooyab.ir/a", "title": "ANGIPARS caps",
             "description": "short", "snippets": ["a much longer, richer snippet text"]},
            {"url": "https://no-title.example", "title": ""},
            {"url": "https://b.example", "title": "b", "description": "d2", "snippets": []},
        ]}})

    rs = youcom_search("angipars دارو", _get=fake_get)
    assert seen["headers"]["X-API-KEY"] == "ydc-key-1"          # trimmed, in header
    assert "ydc-key-1" not in seen["url"]                       # never in the URL
    assert "query=" in seen["url"] and seen["url"].startswith("https://ydc-index.io/v1/search")
    assert rs[0]["snippet"] == "a much longer, richer snippet text"   # richest text wins
    assert [r["url"] for r in rs] == ["https://darooyab.ir/a", "https://b.example"]


def test_youcom_requires_key():
    from services.ai.enrichment.providers import youcom_search
    with pytest.raises(RuntimeError, match="AI Hub"):
        youcom_search("q", _get=lambda u, **kw: "{}")


def test_youcom_older_hits_shape_fallback(monkeypatch):
    from services.ai.enrichment.providers import youcom_search
    monkeypatch.setenv("YOUCOM_API_KEY", "ydc-k")
    rs = youcom_search("q", _get=lambda u, **kw: json.dumps(
        {"hits": [{"url": "https://h.example", "title": "hit", "description": "d"}]}))
    assert rs == [{"title": "hit", "url": "https://h.example", "snippet": "d"}]


def test_rank_results_trusted_domain_and_similarity_first():
    from services.ai.enrichment.providers import rank_results
    rs = [{"title": "random shop", "url": "https://shop.example/x"},
          {"title": "ANGIPARS capsule", "url": "https://www.darooyab.ir/angipars"},
          {"title": "ANGIPARS", "url": "https://blog.example/angipars"}]
    ranked = rank_results(rs, "ANGIPARS")
    assert ranked[0]["url"].startswith("https://www.darooyab.ir")   # trusted + similar
    assert ranked[-1]["title"] == "random shop"                     # nothing dropped
    assert len(ranked) == 3


def test_query_enriched_with_approved_generic(monkeypatch, tmp_path):
    import json as _json
    from services.core.drug_catalog import enrichment as enr
    from services.core.drug_catalog.enrichment import enrich_key
    art = tmp_path / "ref.json"
    art.write_text(_json.dumps({"entries": [
        {"key": enrich_key("تداژل-ایکس"), "generic_name": "vitamin a"}]}), encoding="utf-8")
    monkeypatch.setattr(enr, "REFERENCE_PATH", art)
    import services.ai.enrichment.providers as ep
    monkeypatch.setattr(ep, "_ref_cache", {})
    monkeypatch.setenv("GEMINI_API_KEY", "AQ.k")
    seen = {}
    def searcher(q, **kw):
        seen["q"] = q
        return list(_RESULTS)
    ep.gemini_search_fn("نام: «تداژل-ایکس»", "s", _search=searcher,
                        _post=lambda u, b, headers=None:
                        _interaction([_model_output('{"generic":"vitamin a"}')]))
    assert "vitamin a" in seen["q"] and "دارو" in seen["q"]


def test_latin_retry_when_first_query_empty(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AQ.k")
    import services.ai.enrichment.providers as ep
    monkeypatch.setattr(ep, "_ref_cache", {})
    calls = []
    def searcher(q, **kw):
        calls.append(q)
        return [] if len(calls) == 1 else list(_RESULTS)
    ep.gemini_search_fn("نام: «ETHACRIDINE  LANTATE»", "s", _search=searcher,
                        _post=lambda u, b, headers=None:
                        _interaction([_model_output('{"generic":"x"}')]))
    assert len(calls) == 2
    assert calls[1] == "ETHACRIDINE LANTATE drug"     # bare latin retry


def test_404_retired_model_is_model_unavailable_not_key_failure():
    import io
    import urllib.error
    from unittest import mock
    from services.ai.enrichment.providers import _http_post_json
    body = b'{"error":{"code":404,"message":"This model models/gemini-2.5-flash-lite is no longer available to new users."}}'
    err = urllib.error.HTTPError("u", 404, "Not Found", {}, io.BytesIO(body))
    with mock.patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(RuntimeError) as ei:
            _http_post_json("https://x.example", {}, headers={"x-goog-api-key": "AQ.k"})
    msg = str(ei.value)
    assert "MODEL_UNAVAILABLE" in msg
    assert "کلید" not in msg and "INVALID_API_KEY" not in msg   # not blamed on the key


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
