# Drug Enrichment Intelligence (هوش‌یار دارو) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Model directive (owner):** bounded tasks are executed by **Opus 4.8** workers; the Claude seed pass (Task E7) is executed by Fable directly (needs in-session web search).

**Goal:** A per-drug web-research module (Mistral in-app) whose owner-approved output becomes a canonical, versioned, self-applying reference that fixes form/strength/manufacturer/country gaps in every future NFI harvest and coverage link — permanently.

**Architecture:** Deterministic core (`drug_enrichments` store + spelling-proof keys + worklist + application seams in linker/ingest + committed JSON artifact) with a suggestion-only research engine on top (Mistral Agents API `web_search`, background batch job mirroring the harvest pattern). Only `approved` rows have any effect.

**Tech Stack:** SQLAlchemy async + Alembic (head 0019 → 0020), FastAPI, httpx (already a dependency), React 19 + Tailwind RTL, pytest via miniforge `python3 -m pytest` (NOT `.venv`).

## Global Constraints

- Deterministic-first invariant: research output is `suggested`; ONLY `approved` rows load into any pipeline. No LLM call in ingest/link paths.
- NFI stays authoritative: gap-fill only fields that are missing/blank — never overwrite.
- `MISTRAL_API_KEY` from backend env only; never committed, never in memory files, never logged.
- No PHI anywhere in this module (reference drug names only).
- Backend :8001 = launchd KeepAlive (`kill` = restart onto new code). Admin login admin / PharmPilot2024!.
- Graphify before reading source to explore; read files directly only to modify/debug specific lines.
- Every commit: trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` (worker tasks) — E7 uses the Fable trailer.
- Banned: `any`-casts / ts-ignore / tsconfig loosening; bare `except: pass` around research parsing (record errors per item).

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `services/core/drug_catalog/enrichment.py` | Create | enrich_key, suggestion validation, load/export/import reference |
| `shared/models/enrichment.py` | Create | DrugEnrichment model |
| `shared/models/__init__.py` | Modify | register enrichment module |
| `data/migrations/versions/0020_drug_enrichments.py` | Create | table + indexes |
| `services/core/drug_catalog/coverage_import.py` | Modify | `link_rows(..., enrichments=None)` augmentation |
| `services/core/drug_catalog/importer.py` | Modify | `upsert_catalog(..., enrichments=None)` gap-fill |
| `services/ai/enrichment/__init__.py` | Create | package |
| `services/ai/enrichment/mistral_researcher.py` | Create | Mistral Agents API web_search researcher |
| `services/ai/enrichment/service.py` | Create | background batch job (state/start/status/stop) |
| `services/platform/routers/pricing.py` | Modify | `/pricing/enrichment/*` endpoints |
| `frontend/workstation/src/lib/api.ts` | Modify | enrichmentApi calls |
| `frontend/workstation/src/dashboards/CoverageAdmin.tsx` | Modify | «غنی‌سازی هوشمند» tab in the workbench |
| `tests/unit/test_enrichment.py` | Create | pure + mocked tests |
| `data/reference/drug_enrichments.json` | Create (E7) | the committed canonical artifact |

---

### Task E1: Store foundation — enrich_key, model, migration 0020, suggestion validation

**Files:**
- Create: `services/core/drug_catalog/enrichment.py`, `shared/models/enrichment.py`, `data/migrations/versions/0020_drug_enrichments.py`, `tests/unit/test_enrichment.py`
- Modify: `shared/models/__init__.py` (add `from . import enrichment  # noqa: F401` beside `coverage`)

**Interfaces:**
- Produces: `enrich_key(name: str) -> str`; `validate_suggestion(d: dict) -> tuple[dict, list[str]]`; model `DrugEnrichment` (`__tablename__ = "drug_enrichments"`); constant `SUGGESTION_FIELDS`.

- [ ] **Step 1: failing tests** — create `tests/unit/test_enrichment.py`:

```python
"""هوش‌یار دارو — spelling-proof keys, suggestion validation, reference plumbing."""
import pytest

from services.core.drug_catalog.enrichment import enrich_key, validate_suggestion


def test_enrich_key_folds_spelling_variants():
    # Arabic yeh/kaf, ZWNJ, case, salt words must all collapse to one key
    a = enrich_key("ویتامین آ-تداژل")
    b = enrich_key("ويتامين آ‌-تداژل")          # Arabic yeh + ZWNJ variant
    assert a == b and a
    assert enrich_key("Metformin HCL") == enrich_key("metformin hydrochloride")


def test_enrich_key_empty_is_empty():
    assert enrich_key("") == "" and enrich_key(None) == ""


def test_validate_suggestion_cleans_and_flags():
    d = {"generic": "vitamin a", "brand": "A-Tedagel", "manufacturer": "Tehran Daru",
         "country": "Iran", "dosage_form": "SOFTGEL",
         "strengths": ["25000 IU", "50000 IU"], "confidence": 0.9,
         "sources": ["https://tehrandarou.com/x"], "junk_field": 1}
    clean, errors = validate_suggestion(d)
    assert errors == []
    assert clean["dosage_form"] == "SOFTGEL" and len(clean["strengths"]) == 2
    assert "junk_field" not in clean


def test_validate_suggestion_rejects_bad_shapes():
    clean, errors = validate_suggestion({"confidence": "high", "sources": "not-a-list"})
    assert errors                                   # confidence not float, sources not list
    clean2, errors2 = validate_suggestion({"generic": "x", "confidence": 1.5, "sources": []})
    assert any("confidence" in e for e in errors2)  # out of [0,1]
```

- [ ] **Step 2:** `python3 -m pytest tests/unit/test_enrichment.py -q` → FAIL (module missing).

- [ ] **Step 3: implement `services/core/drug_catalog/enrichment.py`:**

```python
"""هوش‌یار دارو — the canonical drug-enrichment reference.

Web research (Mistral/Claude) produces SUGGESTED rows; only owner-APPROVED rows
are ever loaded into the linker/ingest paths, keeping ingestion deterministic.
Approved rows also export to data/reference/drug_enrichments.json — the
version-controlled canonical artifact that re-seeds any environment.
"""
from __future__ import annotations

import json
from pathlib import Path

from services.ai.clinical_decision_support.normalizer import normalize
from .schema import canonical_ingredient

REFERENCE_PATH = Path("data/reference/drug_enrichments.json")

SUGGESTION_FIELDS = ("generic", "brand", "manufacturer", "country",
                     "dosage_form", "strengths", "confidence", "sources", "notes")


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
        else:
            v = str(v).strip()
        clean[f] = v
    return clean, errors
```

- [ ] **Step 4: model `shared/models/enrichment.py`:**

```python
"""Drug enrichment reference — web-researched details, owner-approved before use."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Float, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import TimestampedBase


class DrugEnrichment(TimestampedBase):
    __tablename__ = "drug_enrichments"

    key: Mapped[str] = mapped_column(String(300), unique=True, index=True, nullable=False)
    raw_name: Mapped[str] = mapped_column(String(300), nullable=False)
    irc: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    generic_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    brand_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    country: Mapped[str | None] = mapped_column(String(80), nullable=True)
    dosage_form: Mapped[str | None] = mapped_column(String(80), nullable=True)
    strengths: Mapped[list | None] = mapped_column(JSONB, nullable=True)   # display strings
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    sources: Mapped[list | None] = mapped_column(JSONB, nullable=True)     # urls
    researched_by: Mapped[str] = mapped_column(String(20), nullable=False)  # mistral|claude|manual
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # suggested | approved | rejected
    status: Mapped[str] = mapped_column(String(20), default="suggested", index=True, nullable=False)
    decided_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

Register in `shared/models/__init__.py`: `from . import enrichment  # noqa: F401` (alphabetical, after `depot`/near `coverage`).

- [ ] **Step 5: migration `data/migrations/versions/0020_drug_enrichments.py`** (id idiom = `server_default=sa.text("uuid_generate_v4()")`, matching 0018):

```python
"""drug_enrichments — owner-approved web-researched drug details reference"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "drug_enrichments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("key", sa.String(300), nullable=False),
        sa.Column("raw_name", sa.String(300), nullable=False),
        sa.Column("irc", sa.String(32), nullable=True),
        sa.Column("generic_name", sa.String(200), nullable=True),
        sa.Column("brand_name", sa.String(200), nullable=True),
        sa.Column("manufacturer", sa.String(200), nullable=True),
        sa.Column("country", sa.String(80), nullable=True),
        sa.Column("dosage_form", sa.String(80), nullable=True),
        sa.Column("strengths", JSONB, nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("sources", JSONB, nullable=True),
        sa.Column("researched_by", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("decided_by", UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_drug_enrichments_key", "drug_enrichments", ["key"], unique=True)
    op.create_index("ix_drug_enrichments_irc", "drug_enrichments", ["irc"])
    op.create_index("ix_drug_enrichments_status", "drug_enrichments", ["status"])


def downgrade() -> None:
    op.drop_index("ix_drug_enrichments_status", table_name="drug_enrichments")
    op.drop_index("ix_drug_enrichments_irc", table_name="drug_enrichments")
    op.drop_index("ix_drug_enrichments_key", table_name="drug_enrichments")
    op.drop_table("drug_enrichments")
```

- [ ] **Step 6: verify + apply:**
`python3 -m pytest tests/unit/test_enrichment.py -q` → all pass.
`python3 -m pytest tests/unit/test_alembic_schema_parity.py -rx 2>&1 > /tmp/p.txt; grep -c drug_enrichments /tmp/p.txt` → `0` (zero drift).
`DATABASE_URL="postgresql+asyncpg://pharmpilot:pharmpilot_dev@127.0.0.1:5433/pharmpilot" python3 -m alembic -c alembic.ini upgrade head` → 0019→0020.
`python3 -c "import shared.models; from shared.models.enrichment import DrugEnrichment; print('OK')"`.

- [ ] **Step 7: commit** `feat(db): drug_enrichments reference store — spelling-proof keys + suggestion validation (migration 0020)`

---

### Task E2: Reference plumbing — load / export / import + worklist builder

**Files:**
- Modify: `services/core/drug_catalog/enrichment.py` (append), `tests/unit/test_enrichment.py` (append)

**Interfaces:**
- Consumes: `DrugEnrichment`, `enrich_key`.
- Produces (all in `enrichment.py`):
  - `async load_approved(db) -> dict[str, dict]` — key → `{generic_name, brand_name, manufacturer, country, dosage_form, strengths, irc}` for `status='approved'`.
  - `async export_reference(db, path=REFERENCE_PATH) -> int` — writes approved rows (sorted by key) as pretty JSON `{version: 1, exported_at: iso, entries: [...]}`; returns count.
  - `async import_reference(db, path=REFERENCE_PATH) -> int` — upserts entries by `key` with `status='approved'`, `researched_by` preserved; returns count.
  - `async build_worklist(db, min_confidence: float = 0.7) -> list[dict]` — dedup by key: `{key, raw_name, reason: 'unmatched'|'low_confidence'|'missing_details', insurer}` from every insurer's latest non-failed CoverageRun (`unmatched[]` rows' drug_name; `review[]` items with confidence < min_confidence) plus catalog products that appear in any run's staged coverage but have blank dosage_form or strength. EXCLUDES keys already present in drug_enrichments (any status).

- [ ] **Step 1: failing tests** — append (uses a REAL throwaway asyncpg DB only if `DATABASE_URL` reachable; otherwise these three tests `pytest.skip`). Exact code:

```python
import asyncio
import os

import pytest


def _db_or_skip():
    url = os.environ.get("DATABASE_URL",
        "postgresql+asyncpg://pharmpilot:pharmpilot_dev@127.0.0.1:5433/pharmpilot")
    try:
        from sqlalchemy.ext.asyncio import create_async_engine
        eng = create_async_engine(url)
        async def ping():
            async with eng.connect() as c:
                await c.close()
            await eng.dispose()
        asyncio.get_event_loop().run_until_complete(ping())
    except Exception:
        pytest.skip("dev DB unreachable")
    return url


def test_reference_roundtrip_and_load(tmp_path):
    url = _db_or_skip()
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from shared.models.enrichment import DrugEnrichment
    from services.core.drug_catalog.enrichment import (
        enrich_key, load_approved, export_reference, import_reference)

    async def run():
        eng = create_async_engine(url)
        S = async_sessionmaker(eng, expire_on_commit=False)
        key = enrich_key("__test_vitamin_a_tedagel__")
        async with S() as db:
            db.add(DrugEnrichment(key=key, raw_name="__test_vitamin_a_tedagel__",
                                  generic_name="vitamin a", brand_name="A-Tedagel",
                                  manufacturer="Tehran Daru", country="Iran",
                                  dosage_form="SOFTGEL", strengths=["25000 IU"],
                                  sources=["https://x"], researched_by="manual",
                                  confidence=0.9, status="approved"))
            await db.commit()
            ref = await load_approved(db)
            assert key in ref and ref[key]["manufacturer"] == "Tehran Daru"
            p = tmp_path / "ref.json"
            n = await export_reference(db, p)
            assert n >= 1 and p.exists()
            # delete, re-import, still approved
            obj = await db.get(DrugEnrichment, (await db.execute(
                __import__("sqlalchemy").select(DrugEnrichment).where(
                    DrugEnrichment.key == key))).scalar_one().id)
            await db.delete(obj); await db.commit()
            m = await import_reference(db, p)
            assert m >= 1
            ref2 = await load_approved(db)
            assert key in ref2
            # cleanup
            row = (await db.execute(__import__("sqlalchemy").select(DrugEnrichment)
                    .where(DrugEnrichment.key == key))).scalar_one()
            await db.delete(row); await db.commit()
        await eng.dispose()
    asyncio.get_event_loop().run_until_complete(run())
```

(Implementer: replace the awkward `__import__("sqlalchemy").select` with a top-of-function `from sqlalchemy import select` — the intent is the round-trip behavior, keep assertions identical.)

- [ ] **Step 2:** run → FAIL (functions missing). **Step 3:** implement the four functions in `enrichment.py` — `load_approved` selects `status=='approved'`; `export_reference` builds `{"version": 1, "exported_at": datetime.now(timezone.utc).isoformat(), "entries": [row-dicts sorted by key]}` and `path.parent.mkdir(parents=True, exist_ok=True)`; `import_reference` reads the file, `validate_suggestion`-style-cleans each entry, upserts by key (update existing row's fields + status='approved'); `build_worklist` queries CoverageRun per distinct insurer (`status IN ('parsed','approved') ORDER BY started_at DESC LIMIT 1` each) and drug_catalog for staged-referenced blanks, applying the exclusion via one `select(DrugEnrichment.key)`. **Step 4:** tests pass. **Step 5:** commit `feat(enrichment): reference load/export/import + deterministic worklist builder`.

---

### Task E3: Application seams — linker augmentation + ingest gap-fill (the "forever" wiring)

**Files:**
- Modify: `services/core/drug_catalog/coverage_import.py` (`link_rows` signature), `services/core/drug_catalog/importer.py` (`upsert_catalog`), `services/core/drug_catalog/coverage_harvest.py` (pass enrichments through `stage_run_payload`/`_run`), `tests/unit/test_enrichment.py` (append)

**Interfaces:**
- Consumes: `load_approved` shape (`key → {generic_name, dosage_form, strengths, ...}`), `enrich_key`.
- Produces: `link_rows(rows, catalog, enrichments: dict | None = None)`; `upsert_catalog(session, records, *, source="nfi", enrichments: dict | None = None)`; `stage_run_payload(..., enrichments=None)` and `_run` loads approved enrichments before linking.

- [ ] **Step 1: failing tests** — append:

```python
def test_linker_uses_enrichment_to_match_ambiguous_row():
    # «ویتامین آ-تداژل» carries no form/strength/generic — unmatchable today.
    # With an approved enrichment the row augments and links to the catalog softgel.
    from decimal import Decimal
    from services.core.drug_catalog.coverage_import import link_rows
    from services.core.drug_catalog.enrichment import enrich_key
    from services.core.drug_catalog.schema import CatalogRecord

    catalog = [CatalogRecord(irc="T1", name_fa="ویتامین آ ۲۵۰۰۰", generic_name="vitamin a",
                             dosage_form="SOFTGEL", strength="25000 IU",
                             announced_price=Decimal("50000"))]
    row = {"drug_name": "ویتامین آ-تداژل"}
    assert not link_rows([row], catalog)[0].matched          # baseline: no match
    enr = {enrich_key("ویتامین آ-تداژل"): {
        "generic_name": "vitamin a", "dosage_form": "SOFTGEL",
        "strengths": ["25000 IU", "50000 IU"], "brand_name": "A-Tedagel",
        "manufacturer": "Tehran Daru", "country": "Iran", "irc": None}}
    link = link_rows([row], catalog, enrichments=enr)[0]
    assert link.matched and link.record.irc == "T1"


def test_ingest_gapfill_never_overwrites_nfi_values():
    from services.core.drug_catalog.importer import build_records, upsert_values
    from services.core.drug_catalog.enrichment import enrich_key
    rec = build_records([{"irc": "1", "name_fa": "ویتامین آ-تداژل",
                          "generic_name": "vitamin a", "country": "ایران"}])[0]
    enr = {enrich_key("ویتامین آ-تداژل"): {
        "country": "France", "manufacturer": "Tehran Daru", "dosage_form": "SOFTGEL"}}
    from services.core.drug_catalog.importer import apply_enrichment_gaps
    filled = apply_enrichment_gaps(rec, enr)
    assert filled.country == "ایران"                 # NFI value KEPT
    assert filled.manufacturer == "Tehran Daru"      # gap filled
    assert filled.dosage_form == "SOFTGEL"           # gap filled
```

- [ ] **Step 2:** run → FAIL. **Step 3: implement:**
  - `coverage_import.link_rows(rows, catalog, enrichments=None)`: after computing `canon, strengths, form, fa = _row_signals(name)`, do:
    ```python
        if enrichments:
            e = enrichments.get(enrich_key(name))
            if e:
                if not canon and e.get("generic_name"):
                    canon = canonical_ingredient(normalize(e["generic_name"])) or canon
                if not strengths and e.get("strengths"):
                    for s_disp in e["strengths"]:
                        strengths |= set(re.findall(r"\d+(?:\.\d+)?", str(s_disp).translate(_DIGIT_FIX)))
                    row_mg = row_mg | _strength_mg(" ".join(map(str, e["strengths"])))
                if not form and e.get("dosage_form"):
                    for tok in re.findall(r"[A-Za-z]+", str(e["dosage_form"]).lower()):
                        if tok in _FORM_WORDS:
                            form = _FORM_WORDS[tok]; break
                if e.get("irc") and e["irc"] in by_irc:      # direct pin wins
                    out.append(LinkResult(row, by_irc[e["irc"]], 1.0, "enrichment"))
                    continue
    ```
    (import `enrich_key` lazily inside the function to avoid a module cycle:
    `from .enrichment import enrich_key`. "SOFTGEL"→ not in `_FORM_WORDS` — ADD
    `"softgel": "capsule", "سافت ژل": "capsule", "سافتژل": "capsule"` to `_FORM_WORDS`.)
  - `importer.apply_enrichment_gaps(rec: CatalogRecord, enrichments: dict) -> CatalogRecord`: look up by `enrich_key(rec.name_fa)` then `enrich_key(rec.generic_name)`; `dataclasses.replace(rec, **{f: e[src] ...})` only for fields currently falsy (country, manufacturer, brand_name, dosage_form, strength — strength from `e["strengths"][0]` only when rec.strength blank). `upsert_catalog(..., enrichments=None)` maps records through it when provided.
  - `coverage_harvest.stage_run_payload(..., enrichments=None)` → pass to `link_rows`; `_run` loads `await load_approved(db)` and passes it.
- [ ] **Step 4:** `python3 -m pytest tests/unit/test_enrichment.py tests/unit/test_coverage_import.py tests/unit/test_coverage_harvest.py -q` → ALL green (existing suites must not regress). **Step 5:** commit `feat(enrichment): linker augmentation + ingest gap-fill — approved reference self-applies`.

---

### Task E4: Research engine — Mistral researcher + batch service

**Files:**
- Create: `services/ai/enrichment/__init__.py` (empty), `services/ai/enrichment/mistral_researcher.py`, `services/ai/enrichment/service.py`
- Modify: `tests/unit/test_enrichment.py` (append)

**Interfaces:**
- Produces:
  - `mistral_researcher.research(drug_name: str, *, api_key: str, model: str = "mistral-medium-latest", timeout: float = 90.0) -> dict` — returns a `validate_suggestion`-clean dict (raises `ResearchError(str)` on transport/parse failure after one retry).
  - `service.start_batch(keys: list[str] | None = None) -> dict` (snapshot; RuntimeError if running or `MISTRAL_API_KEY` unset), `service.status() -> dict`, `service.request_stop() -> bool`. State fields: `running, total, done, failed, current, errors[≤50], started_at, finished_at`.

- [ ] **Step 1: failing tests** (mocked; no network):

```python
def test_mistral_researcher_parses_json_from_text(monkeypatch):
    from services.ai.enrichment import mistral_researcher as mr
    fake = {"generic": "vitamin a", "brand": "A-Tedagel", "manufacturer": "Tehran Daru",
            "country": "Iran", "dosage_form": "softgel",
            "strengths": ["25000 IU", "50000 IU"], "confidence": 0.85,
            "sources": ["https://tehrandarou.com/p/1"]}
    import json as _j
    monkeypatch.setattr(mr, "_call_mistral",
                        lambda name, api_key, model, timeout: "text before " + _j.dumps(fake) + " after")
    out = mr.research("ویتامین آ-تداژل", api_key="k")
    assert out["manufacturer"] == "Tehran Daru" and out["confidence"] == 0.85


def test_mistral_researcher_raises_on_garbage(monkeypatch):
    from services.ai.enrichment import mistral_researcher as mr
    monkeypatch.setattr(mr, "_call_mistral", lambda *a, **k: "no json here at all")
    with pytest.raises(mr.ResearchError):
        mr.research("x", api_key="k")


def test_batch_service_requires_key(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    from services.ai.enrichment import service as es
    with pytest.raises(RuntimeError):
        es.start_batch(["k1"])
```

- [ ] **Step 2:** FAIL. **Step 3: implement.**
  `mistral_researcher.py`: `_PROMPT` (Persian+English, includes the worked example: «ویتامین آ-تداژل» → generic vitamin a / brand A-Tedagel / manufacturer Tehran Daru / country Iran / softgel / 25000+50000 IU; instructs: search the web for the drug, cross-check at least 2 sources, answer ONLY minified JSON with keys `generic, brand, manufacturer, country, dosage_form, strengths, confidence, sources`). `_call_mistral(name, api_key, model, timeout) -> str` uses httpx: create agent once per process (`POST https://api.mistral.ai/v1/agents` `{model, name:"pharmpilot-drug-researcher", instructions:_PROMPT, tools:[{"type":"web_search"}]}` — cache agent id in a module global), then `POST /v1/conversations` `{agent_id, inputs: drug_name}` and concatenate every text chunk in the response entries. `research()` extracts the first balanced `{...}` block (`_extract_json`), `json.loads`, `validate_suggestion` — on parse errors retries `_call_mistral` ONCE, then raises `ResearchError`. `service.py`: mirrors `coverage_harvest`'s state/job pattern: `start_batch` (reads `os.environ["MISTRAL_API_KEY"]` → RuntimeError if unset/running; when `keys is None` calls `build_worklist`), background `asyncio.create_task(_run(...))` that for each worklist item calls `await asyncio.to_thread(research, raw_name, api_key=...)`, writes a `DrugEnrichment(status='suggested', researched_by='mistral', ...)` row (skip if key exists), sleeps `1.5s` between calls, records failures in `errors` without aborting.
- [ ] **Step 4:** tests pass; whole file `python3 -m pytest tests/unit/test_enrichment.py -q` green. **Step 5:** commit `feat(ai): Mistral web-search researcher + background enrichment batch`.

---

### Task E5: API endpoints

**Files:** Modify `services/platform/routers/pricing.py`, append router tests are NOT required (style: verify live like the other coverage endpoints).

**Interfaces:** all under `/pricing/enrichment/` (read=`inventory:read`, write=`inventory:write`), local-import style identical to `/pricing/inconsistencies`:
- `GET worklist` → `{count, items:[{key, raw_name, reason, insurer}]}` (from `build_worklist`)
- `POST run` body `{keys?: string[]}` → start_batch snapshot; 409 on RuntimeError
- `GET run/status` → `service.status()`
- `GET suggestions?status=suggested` → rows as dicts (id str, all fields)
- `POST decide` body `{ids: string[], approve: bool}` → set status approved/rejected + decided_by/at; returns counts
- `POST export` → `{path, count}` via `export_reference`
- `POST import` → `{count}` via `import_reference`

Verify live: restart backend (launchd kill), `curl` worklist (expect count>0 with the tamin/salamat runs present), suggestions empty, decide 404-free, export creates `data/reference/drug_enrichments.json` with `entries: []` initially. Commit `feat(api): enrichment worklist/run/suggestions/decide/export endpoints`.

---

### Task E6: GUI — workbench tab «غنی‌سازی هوشمند»

**Files:** Modify `frontend/workstation/src/lib/api.ts` (add `enrichmentApi`), `frontend/workstation/src/dashboards/CoverageAdmin.tsx`.

Requirements: inside `InconsistenciesPanel` add a third top-level tab «غنی‌سازی هوشمند» beside the two existing ones: (1) header row — worklist count chip, «شروع پژوهش (Mistral)» button (disabled while running; 409/no-key error via `apiErrorText`), live progress strip polling `run/status` every 2s while running (`done/total/failed`, current drug name); (2) suggestions table (comfortable: text-sm, tabular-nums): checkbox | نام دارو | ژنریک | برند | تولیدکننده | کشور | شکل | قدرت‌ها | اطمینان | منابع (domain-only link text) | researched_by; bulk «تأیید همه»/«لغو همه» + «تأیید موارد انتخابی» / «رد موارد انتخابی» calling `decide`; (3) footer: «برون‌سپاری مرجع (ذخیره فایل)» calling export and showing the returned path + count. tsc must stay at zero errors. Browser-verify (login → coverage → workbench → tab renders; empty states fine). Commit `feat(gui): enrichment tab — research batch, suggestion review, reference export`.

---

### Task E7 (FABLE, not delegated): Claude seed pass + artifact + close-out

1. Fable pulls `GET /pricing/enrichment/worklist`, picks ~10 representative items (incl. ویتامین آ-تداژل if present), researches each with in-session WebSearch/WebFetch (≥2 sources each), POSTs rows via a small script inserting `DrugEnrichment(status='suggested', researched_by='claude', sources=[urls])`.
2. Owner (or Fable with owner's standing approval for the seed) approves the good ones in the GUI → `POST export` → commit `data/reference/drug_enrichments.json` as the first canonical artifact.
3. Re-run one salamat/tamin staged harvest and record how many previously-unmatched rows now link (the seed's measurable effect).
4. Update `PROJECT_STATE.md` (milestone + invariant: "enrichment reference is suggestion→approval; approved artifact committed") and `docs/ROADMAP.md`.

---

## Verification gates (whole feature)

- `python3 -m pytest tests/unit/test_enrichment.py tests/unit/test_coverage_import.py tests/unit/test_coverage_harvest.py tests/unit/test_alembic_schema_parity.py -q` — green (parity xfail baseline unchanged, zero drift mentions of drug_enrichments).
- `cd frontend/workstation && npx tsc -b` — still ZERO errors.
- Live: worklist>0 → (with key) a 3-item `POST run {keys:[...]}` batch produces suggested rows with sources; decide→approve; export writes the artifact; a re-harvest shows fewer unmatched.
