"""MistralResearcher — turn a messy Iranian drug name into a validated,
provenance-carrying enrichment SUGGESTION via Mistral's web-search agent.

Design constraints (deterministic-first invariant):
  * Output is ALWAYS status='suggested' — it never enters the linker/ingest
    paths until an owner approves it.
  * The actual network call is isolated behind an injectable `search_fn` so the
    whole pipeline (prompt → JSON extraction → validation → persistence) is
    unit-testable offline, mirroring the project's injected-fetcher pattern.
  * No PHI ever reaches Mistral — only reference drug NAMES are sent.
"""
from __future__ import annotations

import json
import os
import re
from typing import Callable, Optional

from services.core.drug_catalog.enrichment import enrich_key, validate_suggestion

# The model is told: use web search, answer for the ONE drug named, and return a
# single JSON object — no prose. The worked example anchors the hardest real case
# (a brand-salted Persian name whose true identity is only findable by search).
RESEARCH_SYSTEM = (
    "تو یک داروشناس متخصص بازار داروی ایران هستی. برای نام دارویی که کاربر می‌دهد "
    "با جستجوی وب، هویت واقعی فرآورده را پیدا کن: نام ژنریک (ماده مؤثره)، نام تجاری/برند، "
    "شرکت تولیدکننده، کشور سازنده، شکل دارویی و قدرت(ها).\n"
    "قواعد سخت‌گیرانه:\n"
    "۱) فقط برای همان یک قلم پاسخ بده.\n"
    "۲) خروجی «فقط» یک شیء JSON معتبر باشد؛ هیچ متن توضیحی، Markdown یا code fence اضافه نکن.\n"
    "۳) هر مقداری که با اطمینان پیدا نکردی را null بگذار (حدس نزن).\n"
    "۴) confidence یک عدد بین 0 و 1 بر اساس قطعیت منابع.\n"
    "۵) sources فهرست URLهای واقعی مورد استفاده باشد.\n"
    "کلیدهای JSON دقیقاً: generic, brand, manufacturer, country, dosage_form, "
    "strengths (لیست), confidence (عدد), sources (لیست), notes.\n"
    "مثال — ورودی «ویتامین آ-تداژل» ⇒ "
    '{"generic":"vitamin a","brand":"A-Tedagel","manufacturer":"Tehran Daru",'
    '"country":"Iran","dosage_form":"softgel","strengths":["25000 IU","50000 IU"],'
    '"confidence":0.95,"sources":["https://www.darooyab.ir/..."],'
    '"notes":"مکمل ویتامین A، سافت‌ژل، تهران دارو"}'
)


def build_prompt(raw_name: str) -> str:
    return (
        f"نام فرآورده (همان‌طور که در فهرست بیمه/دارونامه آمده): «{raw_name}»\n"
        "این نام ممکن است غلط املایی، ترکیب برند+ژنریک، یا ناقص باشد. "
        "هویت واقعی آن را با جستجوی وب پیدا کن و طبق قالب JSON پاسخ بده."
    )


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def extract_json(text: str) -> dict:
    """Best-effort recovery of the single JSON object from a model reply.
    Tolerates ```json fences and leading/trailing prose. Returns {} on failure."""
    if not text:
        return {}
    s = text.strip()
    s = _FENCE_RE.sub("", s).strip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else {}
    except (json.JSONDecodeError, ValueError):
        pass
    # Fall back to the first balanced {...} span.
    start = s.find("{")
    if start == -1:
        return {}
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(s[start:i + 1])
                    return obj if isinstance(obj, dict) else {}
                except (json.JSONDecodeError, ValueError):
                    return {}
    return {}


# search_fn(prompt, system) -> raw model text. Injected for tests; the default
# hits Mistral's web-search agent.
SearchFn = Callable[[str, str], str]


def _default_search_fn(prompt: str, system: str) -> str:
    """Call Mistral's conversations/web-search agent. Lazy-imports the SDK so the
    module loads (and tests run) without `mistralai` or a key present."""
    api_key = os.environ.get("MISTRAL_API_KEY", "")
    if not api_key:
        raise RuntimeError("MISTRAL_API_KEY تنظیم نشده است")
    from mistralai import Mistral  # lazy: optional dependency

    model = os.environ.get("MISTRAL_RESEARCH_MODEL", "mistral-large-latest")
    with Mistral(api_key=api_key) as client:
        res = client.beta.conversations.start(
            model=model,
            inputs=f"{system}\n\n{prompt}",
            tools=[{"type": "web_search"}],
            completion_args={"response_format": {"type": "json_object"}},
        )
    return _text_from_response(res)


def _text_from_response(res) -> str:
    """Pull assistant text out of the several shapes the SDK/API may return."""
    # Newer conversations API: res.outputs = [entry, ...] with .content
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
    # Chat-completions shape: res.choices[0].message.content
    choices = getattr(res, "choices", None)
    if choices:
        msg = getattr(choices[0], "message", None)
        content = getattr(msg, "content", None) if msg else None
        if isinstance(content, str):
            return content
    return ""


# Map the model's suggestion keys → DrugEnrichment columns.
_COL_MAP = {"generic": "generic_name", "brand": "brand_name",
            "manufacturer": "manufacturer", "country": "country",
            "dosage_form": "dosage_form", "strengths": "strengths",
            "notes": "notes"}


class MistralResearcher:
    """Stateless researcher: name → (validated suggestion dict | None, errors)."""

    def __init__(self, search_fn: Optional[SearchFn] = None):
        self._search = search_fn or _default_search_fn

    def research(self, raw_name: str) -> tuple[Optional[dict], list[str]]:
        """Returns (suggestion, errors). `suggestion` is a dict of DrugEnrichment
        column values (plus confidence/sources) ready for persistence, or None on
        failure. Never raises for a bad model reply — it reports via errors."""
        raw_name = (raw_name or "").strip()
        if not raw_name:
            return None, ["نام خالی است"]
        try:
            text = self._search(build_prompt(raw_name), RESEARCH_SYSTEM)
        except Exception as e:  # network/SDK/key failures are reported, not raised
            return None, [f"جستجو ناموفق بود: {type(e).__name__}: {e}"]
        raw = extract_json(text)
        if not raw:
            return None, ["پاسخ مدل قابل تجزیه به JSON نبود"]
        clean, errors = validate_suggestion(raw)
        if errors:
            return None, errors
        if not any(clean.get(f) for f in ("generic", "brand", "manufacturer",
                                          "dosage_form", "strengths")):
            return None, ["مدل هیچ فیلد مفیدی برنگرداند"]
        suggestion = {_COL_MAP[k]: v for k, v in clean.items() if k in _COL_MAP}
        suggestion["confidence"] = clean.get("confidence")
        suggestion["sources"] = clean.get("sources") or []
        return suggestion, []


async def save_suggestion(db, raw_name: str, suggestion: dict, *,
                          researched_by: str = "mistral") -> "object":
    """Upsert a researched suggestion by spelling-proof key as status='suggested'.

    NEVER downgrades an already-approved/rejected row: if a row for this key
    exists with a decided status, the new research is discarded (the owner's
    decision stands). Returns the DrugEnrichment row (or None if skipped)."""
    from sqlalchemy import select
    from shared.models.enrichment import DrugEnrichment

    key = enrich_key(raw_name)
    if not key:
        return None
    row = (await db.execute(
        select(DrugEnrichment).where(DrugEnrichment.key == key)
    )).scalar_one_or_none()
    if row is not None and row.status in ("approved", "rejected"):
        return None  # owner already decided — don't overwrite

    fields = dict(
        raw_name=raw_name,
        generic_name=suggestion.get("generic_name"),
        brand_name=suggestion.get("brand_name"),
        manufacturer=suggestion.get("manufacturer"),
        country=suggestion.get("country"),
        dosage_form=suggestion.get("dosage_form"),
        strengths=suggestion.get("strengths"),
        notes=suggestion.get("notes"),
        sources=suggestion.get("sources") or [],
        confidence=suggestion.get("confidence"),
        researched_by=researched_by,
        status="suggested",
    )
    if row is None:
        row = DrugEnrichment(key=key, **fields)
        db.add(row)
    else:
        for k, v in fields.items():
            setattr(row, k, v)
    await db.commit()
    return row
