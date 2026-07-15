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
import urllib.error
import urllib.request

from .researcher import DrugResearcher, SearchFn, extract_json

# Starting spacing between call starts, per provider's known rate posture.
# The batch pacer adapts from here on every 429 / success.
PROVIDER_PACE: dict[str, float] = {
    "mistral": 3.0,
    "gemini": 4.5,      # free tier is 15 RPM → 4s floor, +margin
}


# ── Mistral: conversations API + web_search connector ────────────────────────

def mistral_search_fn(prompt: str, system: str) -> str:
    """Call Mistral's conversations/web-search agent. Lazy-imports the SDK so the
    module loads (and tests run) without `mistralai` or a key present."""
    api_key = os.environ.get("MISTRAL_API_KEY", "")
    if not api_key:
        raise RuntimeError("MISTRAL_API_KEY تنظیم نشده است")
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


# ── Gemini: generateContent + google_search grounding ───────────────────────

_GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
               "{model}:generateContent?key={key}")


def gemini_search_fn(prompt: str, system: str, *, _post=None) -> str:
    """Call Gemini with Google Search grounding. `_post` is injectable for tests.

    Grounding and JSON response_mime_type are mutually exclusive, so JSON is
    requested via the prompt and recovered by the researcher's tolerant
    extract_json. Grounding source URIs are merged into the suggestion's
    `sources` — those are pages Google actually retrieved, so they are stronger
    provenance than URLs the model merely claims."""
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY تنظیم نشده است")
    if api_key.startswith("AQ."):
        raise RuntimeError(
            "کلید Google از نوع OAuth (AQ.…) است، نه کلید Gemini API. "
            "یک کلید ...AIza از aistudio.google.com/apikey بگیرید.")
    model = os.environ.get("GEMINI_RESEARCH_MODEL", "gemini-2.0-flash")
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.0},
    }
    payload = (_post or _http_post_json)(
        _GEMINI_URL.format(model=model, key=api_key), body)
    return _gemini_text(payload)


def _http_post_json(url: str, body: dict, timeout: int = 90) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        # Surface the status so the batch's 429 detection can see it.
        raise RuntimeError(f"Status {e.code}. Body: {detail}") from None


def _gemini_text(payload: dict) -> str:
    """Join the candidate's text parts, then graft grounding URIs into sources."""
    cands = (payload or {}).get("candidates") or []
    if not cands:
        fb = (payload or {}).get("promptFeedback") or {}
        raise RuntimeError(f"پاسخی برنگشت (promptFeedback={fb})")
    cand = cands[0]
    parts = ((cand.get("content") or {}).get("parts")) or []
    text = "\n".join(p.get("text", "") for p in parts if p.get("text")).strip()

    uris: list[str] = []
    for chunk in ((cand.get("groundingMetadata") or {}).get("groundingChunks") or []):
        uri = (chunk.get("web") or {}).get("uri")
        if uri and uri not in uris:
            uris.append(uri)
    if not uris:
        return text
    obj = extract_json(text)
    if not obj:
        return text
    if not obj.get("sources"):
        obj["sources"] = uris[:5]
    return json.dumps(obj, ensure_ascii=False)


# ── factory ─────────────────────────────────────────────────────────────────

SEARCH_FNS: dict[str, SearchFn] = {
    "mistral": mistral_search_fn,
    "gemini": gemini_search_fn,
}


def make_researcher(provider: str = "mistral") -> DrugResearcher:
    """Build a researcher for a web-search-capable provider. Unknown providers
    raise rather than silently degrading to a non-searching backend."""
    fn = SEARCH_FNS.get((provider or "").lower())
    if fn is None:
        raise ValueError(
            f"ارائه‌دهنده پشتیبانی‌نشده: {provider!r} — "
            f"گزینه‌ها: {', '.join(sorted(SEARCH_FNS))}")
    return DrugResearcher(fn)
