"""DrugResearcher — offline via injected search_fn (no network, no SDK)."""
from services.ai.enrichment.researcher import (
    DrugResearcher, build_prompt, extract_json)


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
    r = DrugResearcher(search_fn=lambda p, s: reply)
    sug, errors = r.research("ویتامین آ-تداژل")
    assert errors == []
    assert sug["generic_name"] == "vitamin a"      # generic → generic_name
    assert sug["brand_name"] == "A-Tedagel"        # brand → brand_name
    assert sug["manufacturer"] == "Tehran Daru"
    assert sug["strengths"] == ["25000 IU", "50000 IU"]
    assert sug["confidence"] == 0.95 and sug["sources"] == ["https://x"]


def test_research_rejects_empty_and_unparseable():
    r = DrugResearcher(search_fn=lambda p, s: "not json")
    sug, errors = r.research("something")
    assert sug is None and errors

    sug2, errors2 = DrugResearcher(search_fn=lambda p, s: "{}").research("x")
    assert sug2 is None and errors2

    sug3, errors3 = DrugResearcher(search_fn=lambda p, s: "").research("")
    assert sug3 is None and errors3


def test_research_reports_search_failure_without_raising():
    def boom(p, s):
        raise RuntimeError("network down")
    sug, errors = DrugResearcher(search_fn=boom).research("x")
    assert sug is None
    assert any("network down" in e for e in errors)


def test_row_consistency_guard_caps_conflicting_research():
    # The trientine case: row says INJECTION 1 mg/1mL; web only documents the
    # capsule 250 mg → research must come back flagged and low-confidence.
    reply = ('{"generic":"trientine","brand":"Syprine","dosage_form":"capsule",'
             '"strengths":["250 mg"],"confidence":0.9,"sources":["https://x"]}')
    sug, errors = DrugResearcher(search_fn=lambda p, s: reply).research(
        "TRIENTINE DIHYDROCHLORIDE INJECTION INTRAMUSCULAR 1 mg/1mL")
    assert errors == [] and sug is not None
    assert sug["confidence"] <= 0.3                     # capped, was 0.9
    assert "مغایر" in (sug["notes"] or "")              # visible warning
    # consistent research passes untouched
    ok = ('{"generic":"trientine","dosage_form":"injection",'
          '"strengths":["1 mg/1 mL"],"confidence":0.9,"sources":["https://x"]}')
    sug2, _ = DrugResearcher(search_fn=lambda p, s: ok).research(
        "TRIENTINE DIHYDROCHLORIDE INJECTION INTRAMUSCULAR 1 mg/1mL")
    assert sug2 is not None and sug2["confidence"] == 0.9
    assert "مغایر" not in (sug2.get("notes") or "")


def test_build_prompt_family_hint():
    from services.ai.enrichment.researcher import build_prompt
    p = build_prompt("METFORMIN", hint_forms=["tablet", "solution"])
    assert "tablet" in p and "solution" in p and "خانواده" in p
    assert "خانواده" not in build_prompt("METFORMIN")     # no hint → no family clause


def test_research_passes_family_hint_to_search():
    seen = {}
    def fake(prompt, system):
        seen["prompt"] = prompt
        return '{"generic":"metformin","dosage_form":"tablet","confidence":0.9,"sources":["https://x"]}'
    DrugResearcher(search_fn=fake).research("METFORMIN", hint_forms=["tablet", "injection"])
    assert "injection" in seen["prompt"]                  # family reached the model


def test_research_rejects_all_null_fields():
    reply = '{"generic":null,"brand":null,"strengths":[],"confidence":0.1,"sources":[]}'
    sug, errors = DrugResearcher(search_fn=lambda p, s: reply).research("x")
    assert sug is None and errors


def test_pool_parallelizes_and_counts():
    # 9 items × 0.15s serial ≈ 1.35s; 3 workers must land well under that.
    import asyncio
    import time as t
    from services.ai.enrichment import service as es

    ok = ('{"generic":"x","dosage_form":"tab","confidence":0.9,'
          '"sources":["https://x"]}')

    def slow(p, s):
        t.sleep(0.15)
        return ok

    items = [{"raw_name": f"drug {i}"} for i in range(9)]
    saved: list[str] = []

    async def fake_save(name, sug):
        saved.append(name)
        return object()

    es._reset_state()
    start = t.time()
    asyncio.get_event_loop().run_until_complete(
        es._run_pool(items, DrugResearcher(search_fn=slow),
                     workers=3, throttle_sec=0, save=fake_save,
                     pacer=es._Pacer(0.0, 0.0)))
    elapsed = t.time() - start
    assert sorted(saved) == sorted(i["raw_name"] for i in items)
    assert es._STATE.saved == 9 and es._STATE.done == 9 and es._STATE.failed == 0
    assert elapsed < 1.0, f"pool did not parallelize (took {elapsed:.2f}s)"


def test_pool_save_error_fails_item_not_run():
    import asyncio
    from services.ai.enrichment import service as es

    ok = '{"generic":"x","dosage_form":"tab","confidence":0.9,"sources":["https://x"]}'
    items = [{"raw_name": "a"}, {"raw_name": "b"}]

    async def bad_save(name, sug):
        if name == "a":
            raise RuntimeError("db down")
        return object()

    es._reset_state()
    asyncio.get_event_loop().run_until_complete(
        es._run_pool(items, DrugResearcher(search_fn=lambda p, s: ok),
                     workers=2, throttle_sec=0, save=bad_save,
                     pacer=es._Pacer(0.0, 0.0)))
    assert es._STATE.done == 2 and es._STATE.saved == 1 and es._STATE.failed == 1


def test_stop_batch_cancels_pool_early():
    import asyncio
    import time as t
    from services.ai.enrichment import service as es

    ok = '{"generic":"x","dosage_form":"tab","confidence":0.9,"sources":["https://x"]}'
    def slow(p, s):
        t.sleep(0.1)
        return ok
    items = [{"raw_name": f"drug {i}"} for i in range(30)]
    saved = []
    async def fake_save(name, sug):
        saved.append(name)
        return object()

    async def run_and_cancel():
        es._reset_state()
        es._CANCEL.clear()
        pool = asyncio.ensure_future(
            es._run_pool(items, DrugResearcher(search_fn=slow),
                         workers=2, throttle_sec=0, save=fake_save,
                         pacer=es._Pacer(0.0, 0.0)))
        await asyncio.sleep(0.25)          # let ~2 waves finish
        es._CANCEL.set()                   # what stop_batch() does
        await pool

    asyncio.new_event_loop().run_until_complete(run_and_cancel())
    assert 0 < len(saved) < 30             # started, then stopped well short
    es._CANCEL.clear()


def test_pacer_widens_on_429_and_spaces_calls():
    import asyncio
    import time as t
    from services.ai.enrichment import service as es

    p = es._Pacer(min_interval=0.1, max_interval=2.0)
    # 429s double the spacing; successes narrow it back toward min.
    p.on_rate_limit(); p.on_rate_limit()
    assert p.interval > 0.5                      # 0.5*2 floor then doubled again
    widened = p.interval
    for _ in range(30):
        p.on_success()
    assert p.interval < widened and p.interval >= 0.1

    # acquire() enforces the spacing between call starts globally
    p2 = es._Pacer(min_interval=0.15, max_interval=1.0)
    async def two_acquires():
        start = t.time()
        await p2.acquire()
        await p2.acquire()
        return t.time() - start
    took = asyncio.get_event_loop().run_until_complete(two_acquires())
    assert took >= 0.14, f"second call start not spaced ({took:.3f}s)"


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

    r = DrugResearcher(search_fn=flaky)
    sug, errors = asyncio.get_event_loop().run_until_complete(
        _research_with_backoff(r, "x", backoffs=(0.01, 0.01, 0.01)))
    assert sug is not None and calls["n"] == 3      # two 429s retried, third wins

    calls["n"] = 0
    def hard_fail(p, s):
        calls["n"] += 1
        raise RuntimeError("invalid api key")
    sug2, errors2 = asyncio.get_event_loop().run_until_complete(
        _research_with_backoff(DrugResearcher(search_fn=hard_fail), "x",
                               backoffs=(0.01, 0.01)))
    assert sug2 is None and calls["n"] == 1          # not rate-limit → no retry
