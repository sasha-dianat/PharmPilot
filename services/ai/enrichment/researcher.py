"""DrugResearcher — turn a messy Iranian drug name into a validated,
provenance-carrying enrichment SUGGESTION via a web-searching LLM.

Provider-agnostic: the actual network call is an injectable `search_fn`
(see providers.py for the Mistral / Gemini implementations), which keeps the
whole pipeline (prompt → JSON extraction → validation → persistence)
unit-testable offline and mirrors the project's injected-fetcher pattern.

Design constraints (deterministic-first invariant):
  * Output is ALWAYS status='suggested' — it never enters the linker/ingest
    paths until an owner approves it.
  * The provider MUST actually search the web. A non-searching LLM would
    invent Iranian manufacturers and strengths, which is exactly what the
    approve-with-sources trust model exists to prevent.
  * No PHI ever leaves this module — only reference drug NAMES are sent.
"""
from __future__ import annotations

import json
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
    "strengths (لیست), confidence (عدد), sources (لیست), notes, item_kind, variants.\n"
    "۶) item_kind: «drug» برای دارو، «supply» برای اقلام غیر دارویی مثل بطری/ظرف خالی "
    "داروسازی (مثلاً BOTTLE 240 CC)، «supplement» برای مکمل، «other» در غیر این‌ها.\n"
    "۷) variants: اگر فرآورده در چند «شکل دارویی»، چند «قدرت» یا چند «برند» عرضه می‌شود، "
    "برای هر ترکیبِ واقعاً موجود یک عضو بده: "
    '{"dosage_form":..., "strength":..., "pack_size":..., "brand_name":..., "manufacturer":...}. '
    "مثال: تولمتین کپسول 400 و 600 ⇒ دو عضو؛ سالبوتامول اسپری استنشاقی/شربت/قرص ⇒ سه عضو "
    "(نوع اسپری MDI یا DPI را اگر منبع گفته مشخص کن). ترکیب ناموجود نساز.\n"
    "۸) pack_size (وزن/حجم/تعداد بسته مثل 30 g یا 70 g یا 10 mL) یک بعد هویتی مستقل است: "
    "شکل و قدرت یکسان با بستهٔ متفاوت، دو فرآوردهٔ جدا با دو قیمت جدا هستند. "
    "مثال: مترونیدازول ژل ۰٫۷۵٪ ⇒ "
    '[{"dosage_form":"topical gel","strength":"0.75 %","pack_size":"30 g"},'
    '{"dosage_form":"vaginal gel","strength":"0.75 %","pack_size":"70 g"}].\n'
    "۹) حداکثر دقت در هر معیار — «injection» به‌تنهایی کافی نیست: نوع دقیق "
    "(solution آماده، concentrate for infusion، powder for reconstitution)، "
    "route (intravenous/intramuscular/subcutaneous/…)، container "
    "(vial/ampoule/prefilled syringe)، و هم strength کل و هم concentration را جدا بده. "
    "مثال: کربوپلاتین ⇒ "
    '{"dosage_form":"injection, solution, concentrate","route":"intravenous",'
    '"strength":"150 mg","concentration":"10 mg/mL","pack_size":"15 mL",'
    '"container":"vial"}. آنچه منبع نگفته را null بگذار، حدس نزن.\n'
    "۱۰) فرآوردهٔ ترکیبی (چند مادهٔ مؤثره) یک واریانت است، نه چند قدرت: strength را "
    "ترکیبی بده و components را جدا. مثال: آلومینیوم/منیزیم هیدروکساید+سایمتیکون قرص جویدنی ⇒ "
    '{"dosage_form":"chewable tablet","strength":"200 mg / 200 mg / 25 mg",'
    '"components":[{"name":"aluminium hydroxide","strength":"200 mg"},'
    '{"name":"magnesium hydroxide","strength":"200 mg"},'
    '{"name":"simethicone","strength":"25 mg"}]}.\n'
    "۱۲) فرآوردهٔ گیاهی ⇒ item_kind: «herbal» و هویت فارماکوگنوزی در واریانت: "
    "scientific_name (نام علمی لاتین)، plant_part (اندام گیاه)، extract_type "
    "(نوع عصاره: خشک/هیدروالکلی/اسانس/…)، marker (مادهٔ استانداردشده). "
    "مثال: لیورگل ⇒ "
    '{"scientific_name":"Silybum marianum","plant_part":"seed",'
    '"extract_type":"dry extract","marker":"silymarin 70 mg",'
    '"dosage_form":"tablet","category":"flavonolignan"}. '
    "چندجزئی گیاهی ⇒ components با نام علمی هر گیاه؛ category = کلاس فارماکوگنوزی "
    "(essential_oil، anthraquinone، mucilage، saponin، coumarin، alkaloid، …).\n"
    "۱۳) نمک دارویی جزو هویت است و در salt_form می‌آید — mechlorethamine "
    "HYDROCHLORIDE با بازِ آزاد یک فرآورده نیست. مثال: والکلر ⇒ "
    '{"salt_form":"hydrochloride","dosage_form":"topical gel",'
    '"concentration":"0.016 %","pack_size":"60 g","brand_name":"Valchlor"}. '
    "generic را بدون نمک بده و نمک را جدا.\n"
    "۱۴) تجهیزات و ملزومات پزشکی/بیمارستانی ⇒ item_kind: «device» و category از "
    "فرهنگ دسته‌ها (injection_infusion، wound_care، urology، respiratory، "
    "gloves_ppe، sutures، tubes_drains، diagnostics، …) + معیارهای اندازه: "
    "size (گیج G / فرنچ Fr / طول)، material (لاتکس/سیلیکون/PVC/استیل)، "
    "sterility (sterile/non-sterile). مثال: آنژیوکت ⇒ "
    '{"category":"injection_infusion","dosage_form":"IV cannula",'
    '"size":"20 G","material":"PTFE","sterility":"sterile"}.\n'
    "۱۵) اگر نامی «هیچ» شکل دارویی و قدرتی ندارد یا با powder/فله توصیف شده، "
    "به‌احتمال زیاد مادهٔ اولیهٔ داروسازی ترکیبی است ⇒ item_kind: «bulk» و در "
    "category نقش آن: active_ingredient | excipient | base/vehicle. "
    "مثال: SALICYLIC ACID (بدون شکل/قدرت) ⇒ "
    '{"item_kind":"bulk","category":"active_ingredient",'
    '"variants":[{"dosage_form":"powder"}]}. '
    "ولی «POWDER FOR SUSPENSION» فرآوردهٔ نهایی است، نه فله.\n"
    "۱۶) مشخصاتی که در «خود نام» آمده (شکل، راه مصرف، قدرت/غلظت) قید قطعی هستند: "
    "پاسخ باید «همان» فرآورده باشد. اگر منابع فقط شکل/قدرت دیگری را مستند می‌کنند "
    "(مثلاً برای TRIENTINE ... INJECTION 1 mg/1mL فقط کپسول ۲۵۰ پیدا شود)، "
    "هرگز دادهٔ فرآوردهٔ دیگر را جایگزین نکن — فیلدهای تأییدنشده را null بگذار، "
    "confidence را پایین بیاور و در notes بنویس که این شکل/قدرت در منابع یافت نشد.\n"
    "۱۷) قدرت فرآورده‌های مایع باید «لنگر حجمی» داشته باشد:\n"
    "   - شربت/سوسپانسیون/محلول خوراکی ⇒ بر ۵ میلی‌لیتر (mg/5 mL)، مگر عرف "
    "دوزبندی خاص فرآورده چیز دیگری باشد (مثل سوسپانسیون پریمیدون ⇒ همان عرف).\n"
    "   - قطرهٔ چشمی، قطرهٔ خوراکی و تزریقی ⇒ غلظت بر ۱ میلی‌لیتر (per mL).\n"
    "   - قلم و سرنگ آماده ⇒ «هر دو»: غلظت per mL در concentration و مقدار کل "
    "هر ظرف در strength.\n"
    "   - محلول تک‌دوز استنشاقی/نبولایزر ⇒ مقدار کل هر ظرف + حجم ظرف. مثال: "
    "IPRATROPIUM/SALBUTAMOL SOLUTION RESPIRATORY 200 ug/1 mg/1mL 2.5MILLILITER ⇒ "
    '{"dosage_form":"inhalation solution (unit-dose nebulizer)",'
    '"concentration":"200 µg/mL + 1 mg/mL","strength":"0.5 mg / 2.5 mg per 2.5 mL",'
    '"pack_size":"2.5 mL","components":[{"name":"ipratropium bromide","strength":"0.5 mg"},'
    '{"name":"salbutamol sulfate","strength":"2.5 mg"}]}.\n'
    "۱۱) برای قلم‌های تزریق (انسولین و غیره) نوع دقیق قلم جزو هویت فرآورده است و در "
    "container می‌آید: prefilled disposable pen، reusable pen، cartridge/Penfill، و نام "
    "سیستم قلم اگر دارد (SoloStar، FlexPen، KwikPen، …). هر نوع قلم/کارتریج یک واریانت جدا است. "
    "مثال: انسولین گلارژین ⇒ "
    '[{"dosage_form":"injection, solution","route":"subcutaneous","concentration":"100 IU/mL",'
    '"pack_size":"3 mL","container":"prefilled pen (SoloStar)"},'
    '{"dosage_form":"injection, solution","route":"subcutaneous","concentration":"100 IU/mL",'
    '"pack_size":"3 mL","container":"cartridge (Penfill)"}].\n'
    "مثال — ورودی «ویتامین آ-تداژل» ⇒ "
    '{"generic":"vitamin a","brand":"A-Tedagel","manufacturer":"Tehran Daru",'
    '"country":"Iran","dosage_form":"softgel","strengths":["25000 IU","50000 IU"],'
    '"confidence":0.95,"sources":["https://www.darooyab.ir/..."],'
    '"notes":"مکمل ویتامین A، سافت‌ژل، تهران دارو"}'
)


def build_prompt(raw_name: str, hint_forms: list | None = None) -> str:
    p = (
        f"نام فرآورده (همان‌طور که در فهرست بیمه/دارونامه آمده): «{raw_name}»\n"
        "این نام ممکن است غلط املایی، ترکیب برند+ژنریک، یا ناقص باشد. "
        "هویت واقعی آن را با جستجوی وب پیدا کن و طبق قالب JSON پاسخ بده."
    )
    if hint_forms:
        p += ("\nاین مادهٔ مؤثره در فهرست به این شکل‌های دارویی دیده شده است: "
              f"{'، '.join(str(f) for f in hint_forms)} — "
              "برای هر شکل مرتبط یک واریانت جدا بده تا کل خانوادهٔ فرآورده پوشش داده شود "
              "(شکل ناموجود نساز).")
    return p


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


# search_fn(prompt, system) -> raw model text. Injected for tests; real
# implementations live in providers.py.
SearchFn = Callable[[str, str], str]


# Map the model's suggestion keys → DrugEnrichment columns.
_COL_MAP = {"generic": "generic_name", "brand": "brand_name",
            "manufacturer": "manufacturer", "country": "country",
            "dosage_form": "dosage_form", "strengths": "strengths",
            "notes": "notes"}


class DrugResearcher:
    """Stateless researcher: name → (validated suggestion dict | None, errors).
    `search_fn` decides the provider; there is no default, so a caller can never
    silently research against a non-searching backend."""

    def __init__(self, search_fn: SearchFn):
        self._search = search_fn

    def research(self, raw_name: str,
                 hint_forms: list | None = None) -> tuple[Optional[dict], list[str]]:
        """Returns (suggestion, errors). `suggestion` is a dict of DrugEnrichment
        column values (plus confidence/sources) ready for persistence, or None on
        failure. Never raises for a bad model reply — it reports via errors."""
        raw_name = (raw_name or "").strip()
        if not raw_name:
            return None, ["نام خالی است"]
        try:
            text = self._search(build_prompt(raw_name, hint_forms), RESEARCH_SYSTEM)
        except Exception as e:  # network/SDK/key failures are reported, not raised
            return None, [f"جستجو ناموفق بود: {type(e).__name__}: {e}"]
        raw = extract_json(text)
        if not raw:
            return None, ["پاسخ مدل قابل تجزیه به JSON نبود"]
        clean, errors = validate_suggestion(raw)
        if errors:
            return None, errors
        if not any(clean.get(f) for f in ("generic", "brand", "manufacturer",
                                          "dosage_form", "strengths", "item_kind")):
            return None, ["مدل هیچ فیلد مفیدی برنگرداند"]
        from services.core.drug_catalog.enrichment import (
            apply_row_attributes, expand_variants, infer_item_kind)
        suggestion = {_COL_MAP[k]: v for k, v in clean.items() if k in _COL_MAP}
        # dosage_form/brand may be lists (multi-form/-brand products): the scalar
        # column takes the first value; the full set lives in variants.
        for col in ("dosage_form", "brand_name"):
            if isinstance(suggestion.get(col), list):
                suggestion[col] = suggestion[col][0]
        suggestion["variants"] = apply_row_attributes(raw_name, expand_variants(clean))
        suggestion["item_kind"] = infer_item_kind(raw_name, clean)
        suggestion["confidence"] = clean.get("confidence")
        suggestion["sources"] = clean.get("sources") or []
        _check_row_consistency(raw_name, suggestion)
        return suggestion, []


def _check_row_consistency(raw_name: str, suggestion: dict) -> None:
    """Deterministic guard behind prompt rule ۱۶: attributes stated in the
    row's OWN name (form, strength) are ground truth. Research contradicting
    them (trientine INJECTION 1 mg/1mL answered as capsule 250 mg) gets its
    confidence capped and a visible warning — the suspect-first queue then
    surfaces it. Mutates `suggestion` in place; never raises."""
    try:
        from services.core.drug_catalog.match_intel import (
            _DIGITS, _form_token, _strength_mg)
        row_form = _form_token(raw_name)
        row_mg = _strength_mg(str(raw_name).translate(_DIGITS))
        sug_forms = set()
        for f in [suggestion.get("dosage_form")] + \
                [v.get("dosage_form") for v in (suggestion.get("variants") or [])]:
            tok = _form_token(f)
            if tok:
                sug_forms.add(tok)
        strength_bits = list(suggestion.get("strengths") or [])
        for v in (suggestion.get("variants") or []):
            strength_bits += [v.get("strength"), v.get("concentration")]
        sug_mg = _strength_mg(" ".join(str(s) for s in strength_bits if s))
        conflicts = []
        if row_form and sug_forms and row_form not in sug_forms:
            conflicts.append("شکل دارویی با خودِ نام مغایر است")
        if row_mg and sug_mg and not (row_mg & sug_mg):
            conflicts.append("قدرت با خودِ نام مغایر است")
        if conflicts:
            cur = suggestion.get("confidence")
            suggestion["confidence"] = min(float(cur), 0.3) if cur is not None else 0.3
            suggestion["notes"] = (
                f"{suggestion.get('notes') or ''} ⚠ {'؛ '.join(conflicts)} — "
                "پژوهش با مشخصات صریح نام هم‌خوان نیست؛ احتمالاً منابع فقط "
                "شکل/قدرت دیگری را پوشش داده‌اند").strip()
    except Exception:
        pass   # the guard must never break research itself


async def save_suggestion(db, raw_name: str, suggestion: dict, *,
                          researched_by: str = "mistral") -> "object":   # noqa: D401
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
        variants=suggestion.get("variants") or None,
        item_kind=suggestion.get("item_kind") or "drug",
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
