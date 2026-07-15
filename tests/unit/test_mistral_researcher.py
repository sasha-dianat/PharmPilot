"""MistralResearcher — offline via injected search_fn (no network, no SDK)."""
from services.ai.enrichment.mistral_researcher import (
    MistralResearcher, build_prompt, extract_json)


def test_extract_json_tolerates_fences_and_prose():
    assert extract_json('```json\n{"generic":"x"}\n```') == {"generic": "x"}
    assert extract_json('here you go: {"a": 1} thanks') == {"a": 1}
    assert extract_json("no json here") == {}
    assert extract_json("") == {}
    # first balanced object wins even with a trailing partial
    assert extract_json('{"a": {"b": 2}} {oops') == {"a": {"b": 2}}


def test_build_prompt_includes_raw_name():
    assert "ویتامین آ-تداژل" in build_prompt("ویتامین آ-تداژل")


def test_research_happy_path_maps_columns():
    reply = ('{"generic":"vitamin a","brand":"A-Tedagel","manufacturer":"Tehran Daru",'
             '"country":"Iran","dosage_form":"softgel","strengths":["25000 IU","50000 IU"],'
             '"confidence":0.95,"sources":["https://x"],"notes":"n"}')
    r = MistralResearcher(search_fn=lambda p, s: reply)
    sug, errors = r.research("ویتامین آ-تداژل")
    assert errors == []
    assert sug["generic_name"] == "vitamin a"      # generic → generic_name
    assert sug["brand_name"] == "A-Tedagel"        # brand → brand_name
    assert sug["manufacturer"] == "Tehran Daru"
    assert sug["strengths"] == ["25000 IU", "50000 IU"]
    assert sug["confidence"] == 0.95 and sug["sources"] == ["https://x"]


def test_research_rejects_empty_and_unparseable():
    r = MistralResearcher(search_fn=lambda p, s: "not json")
    sug, errors = r.research("something")
    assert sug is None and errors

    sug2, errors2 = MistralResearcher(search_fn=lambda p, s: "{}").research("x")
    assert sug2 is None and errors2

    sug3, errors3 = MistralResearcher(search_fn=lambda p, s: "").research("")
    assert sug3 is None and errors3


def test_research_reports_search_failure_without_raising():
    def boom(p, s):
        raise RuntimeError("network down")
    sug, errors = MistralResearcher(search_fn=boom).research("x")
    assert sug is None
    assert any("network down" in e for e in errors)


def test_research_rejects_all_null_fields():
    reply = '{"generic":null,"brand":null,"strengths":[],"confidence":0.1,"sources":[]}'
    sug, errors = MistralResearcher(search_fn=lambda p, s: reply).research("x")
    assert sug is None and errors


def test_batch_backoff_retries_429_but_not_other_failures():
    # Real failure mode: Mistral web_search returns Status 429 for a burst, then
    # recovers. The batch must retry those; a non-429 failure must NOT be retried.
    import asyncio
    from services.ai.enrichment.service import _research_with_backoff

    calls = {"n": 0}
    ok = ('{"generic":"vitamin a","dosage_form":"softgel",'
          '"confidence":0.9,"sources":["https://x"]}')

    def flaky(p, s):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("API error occurred: Status 429. Body: web_search rate limit reached.")
        return ok

    r = MistralResearcher(search_fn=flaky)
    sug, errors = asyncio.get_event_loop().run_until_complete(
        _research_with_backoff(r, "x", backoffs=(0.01, 0.01, 0.01)))
    assert sug is not None and calls["n"] == 3      # two 429s retried, third wins

    calls["n"] = 0
    def hard_fail(p, s):
        calls["n"] += 1
        raise RuntimeError("invalid api key")
    sug2, errors2 = asyncio.get_event_loop().run_until_complete(
        _research_with_backoff(MistralResearcher(search_fn=hard_fail), "x",
                               backoffs=(0.01, 0.01)))
    assert sug2 is None and calls["n"] == 1          # not rate-limit → no retry
