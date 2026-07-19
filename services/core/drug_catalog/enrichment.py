"""هوش‌یار دارو — the canonical drug-enrichment reference.

Web research (Mistral/Claude) produces SUGGESTED rows; only owner-APPROVED rows
are ever loaded into the linker/ingest paths, keeping ingestion deterministic.
Approved rows also export to data/reference/drug_enrichments.json — the
version-controlled canonical artifact that re-seeds any environment.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from services.ai.clinical_decision_support.normalizer import normalize
from .schema import canonical_ingredient

REFERENCE_PATH = Path("data/reference/drug_enrichments.json")

SUGGESTION_FIELDS = ("generic", "brand", "manufacturer", "country",
                     "dosage_form", "strengths", "pack_size",
                     "confidence", "sources", "notes",
                     "item_kind", "variants")

# bulk: compounding raw ingredient (مادهٔ اولیه) — an API/excipient sold as
# powder/bulk with no finished dosage form or strength, used as the first
# ingredient of compounded drugs. Distinct from finished products AND from
# supply (containers).
ITEM_KINDS = ("drug", "herbal", "device", "bulk", "supply", "supplement", "other")

# pack_size (۳۰ g / ۷۰ g / ۱۰ mL / ۱۰۰ عددی) is an identity dimension of its
# own: identical form+strength in different pack sizes are different priced
# products (metronidazole 0.75% gel 30 g ≠ 70 g).
# Injectables (and sprays/drops) need more than a bare form to identify a
# product: route (IV/IM/SC…), concentration (10 mg/mL) as distinct from total
# strength (150 mg), and container (vial/ampoule/prefilled syringe) are all
# identity dimensions — carboplatin "injection" alone is ambiguous between a
# concentrate-for-infusion vial and a ready solution.
# components: [{name, strength}] — combination products (Al/Mg hydroxide +
# simethicone 200/200/25 mg) carry per-active SERVING amounts, which are one
# product, never separate strength variants.
# Pharmacognosy identity for herbal products (item_kind='herbal'):
# scientific_name (Latin binomial), plant_part (root/leaf/flower/…),
# extract_type (dry/hydroalcoholic/essential oil/…), marker (standardized
# constituent, e.g. "silymarin 70 mg").
# salt_form: the salt IS identity (mechlorethamine HYDROCHLORIDE ≠ free base —
# different solubility, form, and product). category: structured class — for
# herbals the pharmacognosy class (flavonolignan/essential_oil/…), for devices
# the dictionary category (data/reference/device_categories.json). Devices add
# size (gauge/Fr/length), material, sterility.
_VARIANT_KEYS = ("dosage_form", "route", "strength", "concentration",
                 "pack_size", "container", "components", "salt_form", "category",
                 "scientific_name", "plant_part", "extract_type", "marker",
                 "size", "material", "sterility",
                 "brand_name", "manufacturer", "notes")

# Fields persisted in the committed canonical JSON artifact (id/status/timestamps
# are environment-specific and intentionally excluded — all exported rows are approved).
_REFERENCE_FIELDS = ("key", "raw_name", "irc", "generic_name", "brand_name",
                     "manufacturer", "country", "dosage_form", "strengths",
                     "variants", "item_kind",
                     "notes", "sources", "researched_by", "confidence")


def enrich_key(name) -> str:
    """Spelling-proof identity for a drug name: Arabic yeh/kaf → Persian,
    ZWNJ/dashes → space, whitespace folded, salt-stripped via the clinical
    normalizer, lay-name canonicalized. Same drug ⇒ same key, forever."""
    if not name:
        return ""
    s = str(name).replace("ي", "ی").replace("ك", "ک").replace("‌", " ")
    s = s.replace("-", " ").replace("–", " ")
    s = " ".join(s.split()).strip().lower()
    n = normalize(s) or s
    return canonical_ingredient(n) or n


def validate_suggestion(d: dict) -> tuple[dict, list[str]]:
    """Keep only known fields with sane shapes. → (clean, errors)."""
    d = d or {}
    errors: list[str] = []
    clean: dict = {}
    for f in SUGGESTION_FIELDS:
        v = d.get(f)
        if v in (None, "", [], {}):
            continue
        if f == "confidence":
            try:
                v = float(v)
            except (TypeError, ValueError):
                errors.append("confidence must be a number")
                continue
            if not 0.0 <= v <= 1.0:
                errors.append("confidence out of [0,1]")
                continue
        elif f in ("strengths", "sources"):
            if not isinstance(v, list):
                errors.append(f"{f} must be a list")
                continue
            v = [str(x).strip() for x in v if str(x).strip()]
        elif f == "variants":
            if not isinstance(v, list):
                errors.append("variants must be a list")
                continue
            cleaned = []
            for e in v:
                if not isinstance(e, dict):
                    continue
                cv = {}
                for k in _VARIANT_KEYS:
                    if k == "components":
                        comps = e.get(k)
                        cv[k] = [{"name": str(c.get("name", "")).strip() or None,
                                  "strength": str(c.get("strength", "")).strip() or None}
                                 for c in comps if isinstance(c, dict)] \
                            if isinstance(comps, list) else None
                    else:
                        cv[k] = (str(e[k]).strip() or None) if e.get(k) is not None else None
                if any(cv.values()):
                    cleaned.append(cv)
            v = cleaned
        elif f == "item_kind":
            v = str(v).strip().lower()
            if v not in ITEM_KINDS:
                continue                       # unknown kind → default handled later
        elif f in ("dosage_form", "brand", "pack_size"):
            # A product sold as several forms/brands legitimately answers with a
            # list — PRESERVE it for variant fan-out (the researcher stores the
            # first value in the scalar column and expands the rest).
            if isinstance(v, (list, tuple)):
                v = [str(x).strip() for x in v if str(x).strip()]
                if not v:
                    continue
                if len(v) == 1:
                    v = v[0]
            else:
                v = str(v).strip()
        else:
            if isinstance(v, (list, tuple)):
                v = next((str(x).strip() for x in v if str(x).strip()), "")
                if not v:
                    continue
            v = str(v).strip()
        clean[f] = v
    return clean, errors


_SUPPLY_RE = None  # compiled lazily below


def infer_item_kind(raw_name: str, clean: dict) -> str:
    """Deterministic classification guard on top of the model's item_kind.
    Empty bottles/containers for compounding (e.g. «BOTTLE 240 CC») must never
    be treated as drugs, even if the model says otherwise."""
    import re
    global _SUPPLY_RE
    if _SUPPLY_RE is None:
        _SUPPLY_RE = re.compile(
            r"(bottle|container|jar|بطری|ظرف|شیشه)\s*.{0,15}?\d+\s*(cc|ml|میلی)", re.I)
    if _SUPPLY_RE.search(str(raw_name)) and not clean.get("generic"):
        return "supply"
    # Compounding first-ingredient: explicitly bulk/فله, or a bare powder with
    # NO dosage form and NO strength — but never a finished powder product
    # («POWDER FOR SUSPENSION», sachets), which names its form.
    low = str(raw_name).lower()
    if re.search(r"\b(bulk|فله)\b", low):
        return "bulk"
    if (("powder" in low or "پودر" in low)
            and not clean.get("dosage_form") and not clean.get("strengths")
            and not re.search(r"for\s+(oral\s+)?(suspension|injection|solution)"
                              r"|sachet|ساشه|شربت", low)):
        return "bulk"
    kind = clean.get("item_kind")
    return kind if kind in ITEM_KINDS else "drug"


# Attributes a formulary row STATES in its own name are ground truth and must
# survive into the variant even when the model's web sources omit them (capreomycin
# «INTRAMUSCULAR» → route; barium «SACHET» → container; simethicone «30 mL» → pack).
_ROUTE_WORDS = {
    "intramuscular": "intramuscular", "im": "intramuscular",
    "intravenous": "intravenous", "iv": "intravenous",
    "subcutaneous": "subcutaneous", "sc": "subcutaneous", "sq": "subcutaneous",
    "oral": "oral", "ophthalmic": "ophthalmic", "otic": "otic",
    "nasal": "nasal", "rectal": "rectal", "vaginal": "vaginal",
    "topical": "topical", "inhalation": "inhalation", "respiratory": "inhalation",
    "sublingual": "sublingual", "intrathecal": "intrathecal",
}
_CONTAINER_WORDS = {
    "sachet": "sachet", "ساشه": "sachet", "vial": "vial", "ampoule": "ampoule",
    "ampule": "ampoule", "amp": "ampoule", "prefilled": "prefilled syringe",
    "cartridge": "cartridge", "penfill": "cartridge (Penfill)", "pen": "pen",
    "dropper": "dropper bottle", "tube": "tube", "bottle": "bottle",
}
_VOL_RE = None  # lazy


def extract_row_attributes(raw_name: str) -> dict:
    """Pull route / container / pack_size / concentration that the FORMULARY ROW
    already states, deterministically — no model, no network. These are facts the
    source printed; research must not lose them."""
    import re
    global _VOL_RE
    if _VOL_RE is None:
        _VOL_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|mل|میلی\s*لیتر|cc|g|گرم|mg|mcg|µg|ug)\b", re.I)
    low = str(raw_name or "").lower()
    words = set(re.findall(r"[a-zµ]+", low))
    out: dict = {}
    for w, route in _ROUTE_WORDS.items():
        if w in words:
            out["route"] = route
            break
    for w, cont in _CONTAINER_WORDS.items():
        if w in low:
            out["container"] = cont
            break
    # concentration «40 mg/1mL», then total pack volume «30 mL» / weight «135 g»
    conc = re.search(r"\d+(?:\.\d+)?\s*(?:mg|mcg|µg|ug|g|iu)\s*/\s*\d*\s*ml", low)
    if conc:
        out["concentration"] = conc.group(0).replace(" ", "").replace("/", " / ")
    vols = [m.group(0).strip() for m in _VOL_RE.finditer(low)
            if m.group(2).lower() in ("ml", "cc", "g", "گرم", "میلی لیتر")]
    if vols:
        out["pack_size"] = vols[-1].replace("cc", "mL").replace("ml", "mL")
    return out


def apply_row_attributes(raw_name: str, variants: list[dict]) -> list[dict]:
    """Backfill row-stated route/container/pack_size/concentration into variants
    the model left blank. Never overwrites a value the model DID provide."""
    attrs = extract_row_attributes(raw_name)
    if not attrs or not variants:
        return variants
    for v in variants:
        for k in ("route", "container", "pack_size", "concentration"):
            if attrs.get(k) and not v.get(k):
                v[k] = attrs[k]
    return variants


def classify_ingredient_groups(row_names: list[str]) -> dict:
    """Formulary-level disambiguation pre-pass. Gathers rows by canonical active
    ingredient, then classifies each within its group using ONLY what each name
    states (form/strength/route/pack). The payoff is confident BULK detection:
    a row with no form AND no strength AND no route, sitting in a group whose
    OTHER members DO carry those, is a raw ingredient (its finished siblings
    prove it) — far surer than judging that row alone.

    → {ingredient_key: {members: [{name, form, route, strengths, attrs, is_bulk}],
                        forms: [...], has_finished: bool}}"""
    import re
    from .schema import canonical_ingredient

    def _form_tok(name):
        for tok in re.findall(r"[a-z]+", str(name).lower()):
            if tok in _FORM_HINTS:
                return _FORM_HINTS[tok]
        return None

    groups: dict[str, dict] = {}
    for name in row_names:
        canon = canonical_ingredient(normalize(str(name)) or str(name)) or str(name).lower()
        attrs = extract_row_attributes(name)
        form = _form_tok(name)
        strengths = re.findall(r"\d+(?:\.\d+)?\s*(?:mg|mcg|µg|g|iu|%)", str(name).lower())
        g = groups.setdefault(canon, {"members": [], "forms": set()})
        g["members"].append({"name": name, "form": form, "route": attrs.get("route"),
                             "strengths": strengths, "attrs": attrs, "is_bulk": False})
        if form:
            g["forms"].add(form)
    for g in groups.values():
        multi = len(g["members"]) > 1
        for m in g["members"]:
            bare = not m["form"] and not m["strengths"] and not m["route"]
            # bulk only when siblings prove finished forms exist (or single bare row
            # that is itself formless — still a bulk candidate, lower certainty)
            m["is_bulk"] = bare and (bool(g["forms"]) or not multi)
        g["forms"] = sorted(g["forms"])
        g["has_finished"] = bool(g["forms"])
    return groups


# Minimal form-word map for grouping (avoids importing coverage_import cycle).
_FORM_HINTS = {
    "tablet": "tablet", "tab": "tablet", "capsule": "capsule", "cap": "capsule",
    "syrup": "syrup", "suspension": "suspension", "solution": "solution",
    "injection": "injection", "powder": "powder", "gel": "gel", "cream": "cream",
    "ointment": "ointment", "drops": "drops", "drop": "drops", "spray": "spray",
    "inhaler": "inhaler", "patch": "patch", "suppository": "suppository",
    "sachet": "sachet", "lotion": "lotion", "shampoo": "shampoo",
}


def expand_variants(clean: dict) -> list[dict]:
    """Deterministic fan-out into registrable variants (never trusts the LLM to
    enumerate combinations):
      * model-provided variants[] pass through cleaned and deduped;
      * ONE dosage form × N strengths → N variants (tolmetin 400/600 → 2);
      * N dosage forms → one variant per form (salbutamol spray/syrup/tablet →
        3); strengths stay parent-level because cross-attributing them to the
        wrong form would fabricate products."""
    out: list[dict] = []

    def add(form, strength, brand, pack=None, components=None):
        v = {"dosage_form": form, "strength": strength, "pack_size": pack,
             "components": components, "brand_name": brand,
             "manufacturer": clean.get("manufacturer") or None, "notes": None}
        if any(v.values()) and v not in out:
            out.append(v)

    provided = clean.get("variants") or []
    if provided:
        for v in provided:
            v = {**{k: None for k in _VARIANT_KEYS}, **v}
            if v not in out:
                out.append(v)
        return out

    forms = clean.get("dosage_form")
    forms = forms if isinstance(forms, list) else ([forms] if forms else [])
    brands = clean.get("brand")
    brands = brands if isinstance(brands, list) else [brands or None]
    strengths = clean.get("strengths") or []
    packs = clean.get("pack_size")
    packs = packs if isinstance(packs, list) else ([packs] if packs else [])

    # Combination guard: N actives («al / mg / simethicone») with N strengths
    # means per-active SERVING amounts of ONE product — a composite strength
    # with components, never N fabricated variants.
    actives = [a.strip() for a in str(clean.get("generic") or "").split("/")
               if a.strip()]
    if len(actives) > 1 and strengths and len(strengths) == len(actives) \
            and len(forms) <= 1:
        form = forms[0] if forms else None
        comp = [{"name": a, "strength": s} for a, s in zip(actives, strengths)]
        composite = " / ".join(strengths)
        for brand in brands:
            for p in (packs or [None]):
                add(form, composite, brand, p, components=comp)
        return out

    for brand in brands:
        if len(forms) <= 1:
            form = forms[0] if forms else None
            base = [(form, s) for s in strengths] if strengths else \
                   ([(form, None)] if (form or brand or packs) else [])
            for form_, s in base:
                # pack sizes fan out only against a SINGLE form (identity is
                # unambiguous); multi-form pack attribution must come from the
                # model's variants[] to avoid fabricating combinations.
                if len(packs) >= 1 and len(forms) <= 1:
                    for p in packs:
                        add(form_, s, brand, p)
                else:
                    add(form_, s, brand)
        else:
            for form in forms:
                add(form, None, brand)
    return out


# ---------------------------------------------------------------------------
# Reference plumbing — load approved rows, export/import the canonical artifact,
# and build the deterministic research worklist. Only status='approved' rows
# ever leave this module into the ingest/link paths.
# ---------------------------------------------------------------------------

async def load_approved(db) -> dict[str, dict]:
    """key → applyable detail dict for every approved enrichment row.

    Shape per key: {generic_name, brand_name, manufacturer, country,
    dosage_form, strengths, irc}. This is exactly what the linker/ingest seams
    consume; nothing else is exposed."""
    from sqlalchemy import select
    from shared.models.enrichment import DrugEnrichment

    rows = (await db.execute(
        select(DrugEnrichment).where(DrugEnrichment.status == "approved")
    )).scalars().all()
    return {
        r.key: {
            "generic_name": r.generic_name,
            "brand_name": r.brand_name,
            "manufacturer": r.manufacturer,
            "country": r.country,
            "dosage_form": r.dosage_form,
            "strengths": r.strengths,
            "variants": r.variants,
            "irc": r.irc,
        }
        # supply items (empty bottles/containers) must never steer drug matching
        for r in rows if r.key and (getattr(r, "item_kind", "drug") or "drug") == "drug"
    }


async def export_reference(db, path=REFERENCE_PATH) -> int:
    """Write every approved row (sorted by key) to the committed JSON artifact.

    Format: {version: 1, exported_at: iso, entries: [row-dicts]}. Returns the
    number of exported entries."""
    from sqlalchemy import select
    from shared.models.enrichment import DrugEnrichment

    rows = (await db.execute(
        select(DrugEnrichment)
        .where(DrugEnrichment.status == "approved")
        .order_by(DrugEnrichment.key)
    )).scalars().all()
    entries = [{f: getattr(r, f) for f in _REFERENCE_FIELDS} for r in rows]
    payload = {
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "entries": entries,
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(entries)


async def import_reference(db, path=REFERENCE_PATH) -> int:
    """Upsert the committed artifact's entries by key as status='approved'.

    Existing rows are updated in place (researched_by preserved when the entry
    omits it); missing rows are inserted. Returns the number upserted."""
    from sqlalchemy import select
    from shared.models.enrichment import DrugEnrichment

    path = Path(path)
    if not path.exists():
        return 0
    payload = json.loads(path.read_text(encoding="utf-8")) or {}
    entries = payload.get("entries") or []
    count = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key") or enrich_key(entry.get("raw_name") or "")
        if not key:
            continue
        row = (await db.execute(
            select(DrugEnrichment).where(DrugEnrichment.key == key)
        )).scalar_one_or_none()
        researched_by = (entry.get("researched_by")
                         or (row.researched_by if row is not None else None)
                         or "manual")
        fields = dict(
            raw_name=entry.get("raw_name") or key,
            irc=entry.get("irc"),
            generic_name=entry.get("generic_name"),
            brand_name=entry.get("brand_name"),
            manufacturer=entry.get("manufacturer"),
            country=entry.get("country"),
            dosage_form=entry.get("dosage_form"),
            strengths=entry.get("strengths"),
            variants=entry.get("variants"),
            item_kind=entry.get("item_kind") or "drug",
            notes=entry.get("notes"),
            sources=entry.get("sources"),
            researched_by=researched_by,
            confidence=entry.get("confidence"),
            status="approved",
        )
        if row is None:
            db.add(DrugEnrichment(key=key, **fields))
        else:
            for k, v in fields.items():
                setattr(row, k, v)
        count += 1
    await db.commit()
    return count


async def list_enrichments(db, status: str | None = "suggested",
                           limit: int = 500) -> list[dict]:
    """Return enrichment rows (newest first) as JSON dicts for the review GUI.
    status=None returns all statuses."""
    from sqlalchemy import select
    from shared.models.enrichment import DrugEnrichment

    q = select(DrugEnrichment).order_by(DrugEnrichment.updated_at.desc()).limit(min(limit, 2000))
    if status:
        q = q.where(DrugEnrichment.status == status)
    rows = (await db.execute(q)).scalars().all()
    from .match_intel import load_model, score_suggestion
    model = load_model()
    return [{
        "fs_score": score_suggestion(r.raw_name, {
            "generic_name": r.generic_name, "brand_name": r.brand_name,
            "dosage_form": r.dosage_form, "strengths": r.strengths,
            "variants": r.variants}, model),
        "id": str(r.id), "key": r.key, "raw_name": r.raw_name, "irc": r.irc,
        "generic_name": r.generic_name, "brand_name": r.brand_name,
        "manufacturer": r.manufacturer, "country": r.country,
        "dosage_form": r.dosage_form, "strengths": r.strengths,
        "variants": r.variants, "item_kind": getattr(r, "item_kind", "drug"),
        "notes": r.notes,
        "sources": r.sources, "researched_by": r.researched_by,
        "confidence": r.confidence, "status": r.status,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    } for r in rows]


async def decide_enrichments(db, ids: list, *, approve: bool,
                             staff_id=None) -> dict:
    """Approve or reject enrichment rows by id. Approved rows immediately become
    eligible to self-apply at the next harvest/ingest. Returns {updated, status}."""
    from datetime import datetime, timezone
    from sqlalchemy import select
    from shared.models.enrichment import DrugEnrichment

    new_status = "approved" if approve else "rejected"
    rows = (await db.execute(
        select(DrugEnrichment).where(DrugEnrichment.id.in_(list(ids)))
    )).scalars().all()
    now = datetime.now(timezone.utc)
    for r in rows:
        r.status = new_status
        r.decided_by = staff_id
        r.decided_at = now
    await db.commit()
    return {"updated": len(rows), "status": new_status}


async def mark_bulk(db, names: list[str], *, staff_id=None) -> dict:
    """Owner confirms formulary rows as compounding raw ingredients. Upserts an
    APPROVED enrichment (item_kind='bulk') per spelling-proof key, so load_approved
    keeps them OUT of drug matching. Never downgrades an existing drug row that
    already holds real detail — only bare/absent rows become bulk.
    → {marked, skipped}."""
    from datetime import datetime, timezone
    from sqlalchemy import select
    from shared.models.enrichment import DrugEnrichment

    marked = skipped = 0
    now = datetime.now(timezone.utc)
    for name in names or []:
        key = enrich_key(name)
        if not key:
            skipped += 1
            continue
        row = (await db.execute(
            select(DrugEnrichment).where(DrugEnrichment.key == key))).scalar_one_or_none()
        if row is not None and row.item_kind == "drug" and (row.variants or row.strengths):
            skipped += 1        # a real finished-drug row — don't reclassify
            continue
        if row is None:
            row = DrugEnrichment(key=key, raw_name=str(name), researched_by="manual")
            db.add(row)
        row.item_kind = "bulk"
        row.status = "approved"
        row.decided_by = staff_id
        row.decided_at = now
        marked += 1
    await db.commit()
    return {"marked": marked, "skipped": skipped}


async def build_worklist(db, min_confidence: float = 0.7) -> list[dict]:
    """Deterministic research worklist, deduped by spelling-proof key.

    Sources (in priority order): every insurer's latest non-failed CoverageRun's
    unmatched[] rows ('unmatched'), its review[] items below min_confidence
    ('low_confidence'), and catalog products referenced in any run's staged
    coverage that still lack dosage_form or strength ('missing_details').
    Keys already present in drug_enrichments (ANY status) are excluded, so an
    already-researched drug is never re-queued.

    → [{key, raw_name, reason, insurer}]."""
    from sqlalchemy import select
    from shared.models.coverage import CoverageRun
    from shared.models.drug_catalog import DrugCatalogItem
    from shared.models.enrichment import DrugEnrichment

    existing = set((await db.execute(select(DrugEnrichment.key))).scalars().all())
    items: dict[str, dict] = {}
    all_names: list[str] = []          # every row name seen, for family grouping

    def add(raw_name, reason, insurer):
        if not raw_name:
            return
        all_names.append(str(raw_name))
        key = enrich_key(raw_name)
        if not key or key in existing or key in items:
            return
        items[key] = {"key": key, "raw_name": str(raw_name),
                      "reason": reason, "insurer": insurer}

    insurers = (await db.execute(
        select(CoverageRun.insurer).distinct())).scalars().all()
    staged_map: dict[str, str] = {}   # irc → first insurer that staged it
    for insurer in insurers:
        run = (await db.execute(
            select(CoverageRun)
            .where(CoverageRun.insurer == insurer,
                   CoverageRun.status.in_(("parsed", "approved")))
            .order_by(CoverageRun.started_at.desc())
            .limit(1)
        )).scalar_one_or_none()
        if run is None:
            continue
        for u in (run.unmatched or []):
            row = u.get("row") if isinstance(u, dict) else None
            name = row.get("drug_name") if isinstance(row, dict) else None
            add(name, "unmatched", insurer)
        for r in (run.review or []):
            if not isinstance(r, dict):
                continue
            conf = r.get("confidence")
            if conf is None or conf >= min_confidence:
                continue
            row = r.get("row") if isinstance(r.get("row"), dict) else {}
            add(row.get("drug_name") or r.get("name"), "low_confidence", insurer)
        if isinstance(run.staged, dict):
            for irc in run.staged:
                staged_map.setdefault(irc, insurer)

    if staged_map:
        catalog = (await db.execute(
            select(DrugCatalogItem)
            .where(DrugCatalogItem.irc.in_(list(staged_map)))
        )).scalars().all()
        for c in catalog:
            form_ok = bool(c.dosage_form and str(c.dosage_form).strip())
            strength_ok = bool(c.strength and str(c.strength).strip())
            if not form_ok or not strength_ok:
                add(c.name_fa, "missing_details", staged_map.get(c.irc))

    # Family annotation: attach the dosage forms this ingredient appears in
    # across the formulary, so ONE research pass can be told to cover the whole
    # family (metformin → tablet + XR tablet + oral solution) instead of one form.
    from .schema import canonical_ingredient
    groups = classify_ingredient_groups(all_names)
    canon_forms = {ck: g["forms"] for ck, g in groups.items() if g["forms"]}
    for it in items.values():
        ck = canonical_ingredient(normalize(it["raw_name"]) or it["raw_name"]) \
            or it["raw_name"].lower()
        forms = canon_forms.get(ck)
        if forms and len(forms) > 1:
            it["forms"] = forms

    return list(items.values())
