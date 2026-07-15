"""Web-search backends for drug research.

Each provider exposes search_fn(prompt, system) -> raw model text. Every one
MUST perform a real web search: a non-searching LLM would confabulate Iranian
manufacturers and strengths, and the whole approve-with-sources trust model
depends on the answer being grounded in fetchable pages.

Reachability notes (verified 2026-07-15 from this deployment):
  * mistral — reachable, but the web_search connector's rate window is
    undocumented and tier-dependent; it throttles hard (~5 items / 500s here).
  * gemini  — Google Search grounding; free tier ~1,500 req/day at 15 RPM.
  * groq    — NOT usable: standard models have no web search at all (only the
    compound models do), and api.groq.com returns 403 "Access denied. Please
    check your network settings." to this host's datacenter/VPN egress ASN.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from .researcher import DrugResearcher, SearchFn, extract_json

# Starting spacing between call starts, per provider's known rate posture.
# The batch pacer adapts from here on every 429 / success.
PROVIDER_PACE: dict[str, float] = {
    "mistral": 3.0,
    "gemini": 4.5,      # free tier is 15 RPM → 4s floor, +margin
}


def _resolve_key(env: str) -> str:
    """Read a provider key set via the AI Hub.

    Keys entered in the admin UI are persisted to ~/.pharmpilot/
    ai_provider_settings.json and pushed into os.environ by the provider
    registry's _load_settings() — but the registry is built LAZILY, so in a
    fresh backend process that nobody has hit an /ai-settings route on, the
    env var is still unset. Touching the registry here forces that load, so a
    batch works whether or not the AI Hub page happens to have been opened."""
    try:
        from services.ai.provider_registry.registry import get_ai_registry
        get_ai_registry()          # idempotent; populates os.environ on first build
    except Exception:              # registry problems must not mask the key error
        pass
    return os.environ.get(env, "")


# ── Mistral: conversations API + web_search connector ────────────────────────

def mistral_search_fn(prompt: str, system: str) -> str:
    """Call Mistral's conversations/web-search agent. Lazy-imports the SDK so the
    module loads (and tests run) without `mistralai` or a key present."""
    api_key = _resolve_key("MISTRAL_API_KEY")
    if not api_key:
        raise RuntimeError("کلید Mistral تنظیم نشده است — آن را در «AI Hub» وارد کنید")
    # SDK v2 moved the client to mistralai.client; v1 exported it top-level.
    try:
        from mistralai.client import Mistral   # lazy: optional dependency (v2+)
    except ImportError:
        from mistralai import Mistral          # v1 fallback

    model = os.environ.get("MISTRAL_RESEARCH_MODEL", "mistral-large-latest")
    with Mistral(api_key=api_key) as client:
        res = client.beta.conversations.start(
            model=model,
            instructions=system,
            inputs=prompt,
            tools=[{"type": "web_search"}],
            completion_args={"response_format": {"type": "json_object"}},
        )
    return _mistral_text(res)


def _mistral_text(res) -> str:
    """Pull assistant text out of the several shapes the SDK/API may return."""
    for attr in ("outputs", "entries"):
        seq = getattr(res, attr, None)
        if seq:
            parts = []
            for e in seq:
                c = getattr(e, "content", None)
                if isinstance(c, str):
                    parts.append(c)
                elif isinstance(c, list):
                    parts.extend(getattr(x, "text", "") or (x.get("text") if isinstance(x, dict) else "")
                                 for x in c)
            joined = "\n".join(p for p in parts if p)
            if joined.strip():
                return joined
    choices = getattr(res, "choices", None)
    if choices:
        msg = getattr(choices[0], "message", None)
        content = getattr(msg, "content", None) if msg else None
        if isinstance(content, str):
            return content
    return ""


# ── Gemini: Interactions API, strict free tier ───────────────────────────────
# Auth is the x-goog-api-key header with the key EXACTLY as stored (trimmed
# only). No prefix heuristics: AI Studio issues both AIza… and newer AQ.…
# Authorization/Auth API keys, so whether a key is valid is the API's call,
# not a local guess — a wrong guess here previously rejected a working key.
#
# Verified live against this project's key (2026-07-15):
#   gemini-3.1-flash-lite  → status=completed
#   gemini-2.5-flash(-lite) → 404 "no longer available to new users"
#     (a MODEL_UNAVAILABLE condition, NOT a key failure)
#
# Strict free tier: Google Search grounding is NOT available for
# gemini-3.1-flash-lite on the API free tier, so SEARCH is separated from
# EXTRACTION — a free external web search finds candidate pages, and Gemini
# only extracts/normalizes from those results. Never silently enable the paid
# google_search tool or a paid model.

DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
_GEMINI_INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"


def _gemini_key() -> str:
    """GEMINI_API_KEY first, else the AI-Hub-managed GOOGLE_API_KEY. Trim only."""
    key = (_resolve_key("GEMINI_API_KEY") or _resolve_key("GOOGLE_API_KEY")).strip()
    if not key:
        raise RuntimeError("کلید Gemini تنظیم نشده است — آن را در «AI Hub» (Google) وارد کنید")
    return key


def _gemini_model() -> str:
    return (os.environ.get("GEMINI_RESEARCH_MODEL") or "").strip() or DEFAULT_GEMINI_MODEL


# ── free external search (SearchProvider seam) ──────────────────────────────
# search(query) → [{title, url, snippet}]. web_search() picks the backend:
# Brave Search API (keyed, reliable, free 2k/month) when a BRAVE_API_KEY is
# set in the AI Hub, else keyless DuckDuckGo HTML — which serves bot
# challenges to this deployment's datacenter egress after a few requests, so
# it is strictly the fallback. Swapping backends never touches the Gemini
# integration.

_DDG_URL = "https://html.duckduckgo.com/html/"
_BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _http_get(url: str, timeout: int = 30, data: bytes | None = None,
              headers: dict | None = None) -> str:
    """GET (or POST when `data` is given). DDG's html endpoint serves a bot
    challenge to datacenter IPs on GET but real results on POST — verified
    live from this deployment's egress."""
    h = {"User-Agent": _BROWSER_UA}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # "Status <code>" marker preserved → the batch's 429 backoff sees it.
        raise RuntimeError(f"Status {e.code}. جستجوی وب ناموفق بود") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"خطای شبکه در جستجوی وب: {e.reason}") from None


def brave_search(query: str, *, max_results: int = 6, _get=None) -> list[dict]:
    """Brave Search API → [{title, url, snippet}]. Key via X-Subscription-Token
    header (never in the URL). Free plan: 2,000 queries/month at 1 req/s —
    the batch pacer already spaces calls wider than that."""
    key = _resolve_key("BRAVE_API_KEY").strip()
    if not key:
        raise RuntimeError("کلید Brave تنظیم نشده است — آن را در «AI Hub» وارد کنید")
    from urllib.parse import urlencode
    url = f"{_BRAVE_URL}?{urlencode({'q': query, 'count': max_results})}"
    raw = (_get or _http_get)(url, headers={
        "X-Subscription-Token": key, "Accept": "application/json"})
    payload = json.loads(raw) if isinstance(raw, str) else raw
    results = []
    for r in ((payload.get("web") or {}).get("results") or [])[:max_results]:
        u, t = r.get("url"), (r.get("title") or "").strip()
        if u and t:
            results.append({"title": t, "url": u,
                            "snippet": (r.get("description") or "")[:300]})
    return results


def search_backend_name() -> str:
    return "brave" if _resolve_key("BRAVE_API_KEY").strip() else "duckduckgo"


def web_search(query: str, *, max_results: int = 6) -> list[dict]:
    """The active SearchProvider: Brave when its key is set, else DDG."""
    if search_backend_name() == "brave":
        return brave_search(query, max_results=max_results)
    return ddg_search(query, max_results=max_results)


def _ddg_unwrap(href: str) -> str:
    """DDG links results through //duckduckgo.com/l/?uddg=<encoded-url>."""
    if href.startswith("//"):
        href = "https:" + href
    if "uddg=" in href:
        from urllib.parse import parse_qs, urlparse
        target = (parse_qs(urlparse(href).query).get("uddg") or [""])[0]
        return target
    return href if href.startswith("http") else ""


def ddg_search(query: str, *, max_results: int = 6, _get=None) -> list[dict]:
    """Free web search → [{title, url, snippet}]. `_get` injectable for tests."""
    import html as _html
    from urllib.parse import urlencode
    page = (_get or _http_get)(_DDG_URL, data=urlencode({"q": query}).encode())
    results: list[dict] = []
    for m in re.finditer(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            page, re.S):
        url = _ddg_unwrap(_html.unescape(m.group(1)))
        title = _html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
        if url and title and not any(r["url"] == url for r in results):
            results.append({"title": title, "url": url})
        if len(results) >= max_results:
            break
    snippets = [_html.unescape(re.sub(r"<[^>]+>", "", s)).strip()
                for s in re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', page, re.S)]
    for r, s in zip(results, snippets):
        r["snippet"] = s[:300]
    return results


def _name_from_prompt(prompt: str) -> str:
    """build_prompt quotes the raw drug name as «name» — recover it."""
    m = re.search(r"«(.+?)»", prompt, re.S)
    return m.group(1).strip() if m else prompt.strip().splitlines()[0][:120]


def gemini_search_fn(prompt: str, system: str, *, _post=None, _search=None) -> str:
    """Free-tier pipeline: free external search → Gemini 3.1 Flash-Lite
    EXTRACTION over those results (no google_search tool — not available on
    the free tier for this model, and paid grounding must never turn on
    silently). `_post`/`_search` are injectable for tests.

    Provenance: the model is told to cite only URLs from the supplied result
    list; if it returns none, the top search URLs are used — either way,
    sources are pages a real search surfaced, never invented links."""
    raw_name = _name_from_prompt(prompt)
    results = (_search or web_search)(f"{raw_name} دارو")
    if not results:
        raise RuntimeError(f"جستجوی وب نتیجه‌ای برای «{raw_name}» نداشت")
    listing = json.dumps(results, ensure_ascii=False, indent=1)
    input_text = (
        f"{system}\n\n{prompt}\n\n"
        "نتایج جستجوی وب در ادامه آمده است. فقط بر اساس همین نتایج استخراج کن؛ "
        "به دانش درونی تکیه نکن و مقدار تأییدنشده را null بگذار:\n"
        f"{listing}\n\n"
        "در sources فقط URLهایی از همین فهرست را بگذار که واقعاً استفاده کردی."
    )
    payload = (_post or _http_post_json)(
        _GEMINI_INTERACTIONS_URL,
        {"model": _gemini_model(), "input": input_text},
        headers={"x-goog-api-key": _gemini_key()})
    text, cited = _gemini_output(payload)
    obj = extract_json(text)
    if obj and not obj.get("sources"):
        obj["sources"] = cited or [r["url"] for r in results[:3]]
        return json.dumps(obj, ensure_ascii=False)
    return text


def gemini_ping(*, _post=None, _search=None) -> dict:
    """Real connection test: a minimal no-tools interaction, plus a probe of
    the active search backend (both legs of the pipeline must work). The API —
    not a key-prefix heuristic — decides whether the credential works.
    → {ok, model, reply, search_backend, search_results} or raises."""
    model = _gemini_model()
    payload = (_post or _http_post_json)(
        _GEMINI_INTERACTIONS_URL,
        {"model": model, "input": "Reply with exactly: OK"},
        headers={"x-goog-api-key": _gemini_key()})
    text, _ = _gemini_output(payload)
    out = {"ok": True, "provider": "gemini", "model": model, "reply": text[:60],
           "search_backend": search_backend_name()}
    try:
        out["search_results"] = len((_search or web_search)("aspirin 100 mg دارو"))
    except Exception as e:
        out["ok"] = False
        out["search_error"] = str(e)[:160]
    return out


_API_HINTS = {
    400: "درخواست نامعتبر بود",
    401: "احراز هویت ناموفق — Google این کلید را نپذیرفت",
    403: "دسترسی مجاز نیست (پروژه/منطقه/مجوز)",
    404: "مدل یا نقطه اتصال یافت نشد",
    429: "(FREE_TIER_QUOTA_EXHAUSTED) سهمیه رایگان یا محدودیت نرخ Gemini پر شده است — بعداً دوباره تلاش کنید",
}


def _http_post_json(url: str, body: dict, headers: dict | None = None,
                    timeout: int = 90) -> dict:
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        hint = _API_HINTS.get(e.code, "")
        if e.code == 404 and "no longer available" in detail:
            # Retired model (e.g. gemini-2.5-*) — a MODEL_UNAVAILABLE condition,
            # emphatically NOT a key failure: do not retry, do not blame auth.
            hint = ("(MODEL_UNAVAILABLE) این مدل برای این پروژه در دسترس نیست — "
                    f"مدل پیش‌فرض {DEFAULT_GEMINI_MODEL} است یا GEMINI_RESEARCH_MODEL را تنظیم کنید")
        # "Status <code>" prefix is load-bearing: the batch's 429 detection
        # keys off it. Keys travel in headers, never URLs, so `detail` and the
        # message are key-free by construction.
        raise RuntimeError(f"Status {e.code}. {hint} Body: {detail}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"خطای شبکه (نه احراز هویت): {e.reason}") from None


def _gemini_output(payload) -> tuple[str, list[str]]:
    """(user-visible text, citation URLs) from an Interactions response.

    Text comes ONLY from model_output steps — thought / google_search_call /
    google_search_result steps are internal and never surfaced. Citations are
    url_citation annotations, deduped by URL."""
    if isinstance(payload, list):          # the API wraps some payloads in a list
        payload = payload[0] if payload else {}
    payload = payload or {}
    texts: list[str] = []
    urls: list[str] = []
    for step in payload.get("steps") or []:
        if not isinstance(step, dict) or step.get("type") != "model_output":
            continue
        for block in step.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            if isinstance(block.get("text"), str):
                texts.append(block["text"])
            for ann in block.get("annotations") or []:
                if (isinstance(ann, dict) and ann.get("type") == "url_citation"
                        and isinstance(ann.get("url"), str) and ann["url"] not in urls):
                    urls.append(ann["url"])
    # Convenience top-level fields some responses carry.
    if not texts:
        for k in ("output_text", "output", "text"):
            v = payload.get(k)
            if isinstance(v, str) and v.strip():
                texts.append(v)
                break
    if not texts:
        raise RuntimeError(f"پاسخ بدون model_output (status={payload.get('status')!r})")
    return "\n".join(texts).strip(), urls


def _gemini_text(payload) -> str:
    """Extract the reply and graft citation URLs into an empty `sources`."""
    text, urls = _gemini_output(payload)
    if not urls:
        return text
    obj = extract_json(text)
    if not obj:
        return text
    if not obj.get("sources"):
        obj["sources"] = urls[:5]
    return json.dumps(obj, ensure_ascii=False)


def mistral_ping() -> dict:
    """Real connection test: a 1-token chat completion (no web_search burned)."""
    api_key = _resolve_key("MISTRAL_API_KEY")
    if not api_key:
        raise RuntimeError("کلید Mistral تنظیم نشده است — آن را در «AI Hub» وارد کنید")
    try:
        from mistralai.client import Mistral
    except ImportError:
        from mistralai import Mistral
    with Mistral(api_key=api_key) as client:
        res = client.chat.complete(
            model=os.environ.get("MISTRAL_RESEARCH_MODEL", "mistral-large-latest"),
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
            max_tokens=4)
    reply = (res.choices[0].message.content or "") if getattr(res, "choices", None) else ""
    return {"ok": True, "provider": "mistral", "reply": str(reply)[:60]}


# ── factory ─────────────────────────────────────────────────────────────────

SEARCH_FNS: dict[str, SearchFn] = {
    "mistral": mistral_search_fn,
    "gemini": gemini_search_fn,
}

PROVIDER_TESTS = {
    "mistral": mistral_ping,
    "gemini": gemini_ping,
}


def test_provider(provider: str) -> dict:
    """Live connection test for a provider — a real API request decides,
    never a key-prefix heuristic. Raises with the categorized failure."""
    fn = PROVIDER_TESTS.get((provider or "").lower())
    if fn is None:
        raise ValueError(f"ارائه‌دهنده پشتیبانی‌نشده: {provider!r}")
    return fn()


def make_researcher(provider: str = "mistral") -> DrugResearcher:
    """Build a researcher for a web-search-capable provider. Unknown providers
    raise rather than silently degrading to a non-searching backend."""
    fn = SEARCH_FNS.get((provider or "").lower())
    if fn is None:
        raise ValueError(
            f"ارائه‌دهنده پشتیبانی‌نشده: {provider!r} — "
            f"گزینه‌ها: {', '.join(sorted(SEARCH_FNS))}")
    return DrugResearcher(fn)
