# دارونامه Crawler + Full NFI Extraction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Insurer دارونامه acquisition engine (probe → harvest → staged run → diff → approve) with a GUI section, plus the NFI parser extracting every field on the detail page (producer, country, monograph) into the catalog.

**Architecture:** A strategy-pipeline acquisition layer (`coverage_harvest.py`, modeled on `nfi_harvest_service.py`) feeds the existing smart extractor (`infer_columns → link_rows → build_coverage`), persisting staged `coverage_runs` that an admin approves in a new `CoverageAdmin` dashboard section. A global `harvest_lock` serializes NFI + coverage jobs (shared Iran system proxy). The NFI parser gains a brands-table extractor (کشور lives in a flag `<img title=…>`), and new catalog columns + a `monograph` JSONB keep everything.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic (Postgres 127.0.0.1:5433), stdlib urllib fetching, pandas `read_table` parsing, React 19 + Vite + Tailwind + react-query (RTL/fa-IR), pytest (`python3 -m pytest` — pytest lives in the miniforge python3, NOT `.venv`).

**Spec:** `docs/superpowers/specs/2026-07-07-darunameh-crawler-design.md`

**Conventions used throughout:**
- Backend server runs on :8001; dev DB `postgresql+asyncpg://pharmpilot:pharmpilot_dev@127.0.0.1:5433/pharmpilot`.
- Migrations live in `data/migrations/versions/`; current head file is `0017_drug_price_proposals.py`. `tests/unit/test_alembic_schema_parity.py` enforces migration↔ORM parity — it is the primary migration test.
- Commit after every green step. All commits end with `Co-Authored-By:` trailer per repo rule (the graphify hook rebuilds the graph on commit automatically).
- Frontend `npx tsc -b` has pre-existing baseline errors in `DashboardShell`/`useAIProvider`/`MedReconciliationPage` — the bar is "no NEW errors", not zero.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `tests/fixtures/nfi/nfi_detail_17248.html` | Create (move from repo root) | Real NFI detail page fixture (Victoza) |
| `services/core/drug_catalog/nfi.py` | Modify | Parser: brands table, country, new labels |
| `tests/unit/test_nfi_full_extraction.py` | Create | Parser extraction tests |
| `services/core/drug_catalog/schema.py` | Modify | CatalogRecord: 5 new fields |
| `services/core/drug_catalog/importer.py` | Modify | monograph assembly, sticky-None upsert, clamps |
| `tests/unit/test_catalog_importer_enrichment.py` | Create | Importer mapping tests |
| `shared/models/drug_catalog.py` | Modify | 5 new columns |
| `shared/models/coverage.py` | Create | CoverageSource, CoverageRun |
| `shared/models/__init__.py` | Modify | register coverage models |
| `data/migrations/versions/0018_catalog_enrichment_coverage.py` | Create | columns + tables + seeds |
| `scripts/harvest_nfi.py` | Modify | delete parser copy, import service one |
| `services/core/drug_catalog/harvest_lock.py` | Create | global lock (owner-tagged) |
| `tests/unit/test_harvest_lock.py` | Create | lock tests |
| `services/core/drug_catalog/nfi_harvest_service.py` | Modify | acquire/release lock, expose holder |
| `services/core/drug_catalog/coverage_harvest.py` | Create | sniff, strategies, diff, probe, run service, apply |
| `tests/unit/test_coverage_harvest.py` | Create | pure-logic tests |
| `services/platform/routers/pricing.py` | Modify | coverage sources/runs endpoints |
| `frontend/workstation/src/lib/api.ts` | Modify | coverageApi |
| `frontend/workstation/src/dashboards/CoverageAdmin.tsx` | Create | GUI section |
| `frontend/workstation/src/dashboards/DrugCatalogAdmin.tsx` | Modify | remove coverage-upload card |
| `frontend/workstation/src/DashboardShell.tsx` | Modify | register section |

---

### Task 1: NFI parser — full-page extraction (TDD)

**Files:**
- Create: `tests/fixtures/nfi/nfi_detail_17248.html` (moved), `tests/unit/test_nfi_full_extraction.py`
- Modify: `services/core/drug_catalog/nfi.py`
- Delete: `ویکتوزا.html` (duplicate of the fixture, repo root)

- [ ] **Step 1.1: Move the fixture into the repo, delete the duplicate**

```bash
mkdir -p tests/fixtures/nfi
mv nfi_detail_17248.html tests/fixtures/nfi/nfi_detail_17248.html
rm "ویکتوزا.html"
```

`.gitignore` has `nfi_detail_*.html` (unscoped — matches at any depth), which would silently ignore the tracked fixture. Add a negation so the fixture is trackable. Edit `.gitignore`, immediately after the `nfi_detail_*.html` line, add:

```gitignore
!tests/fixtures/nfi/*.html
```

Verify: `git check-ignore tests/fixtures/nfi/nfi_detail_17248.html` prints nothing (no longer ignored).

- [ ] **Step 1.2: Write the failing tests**

Create `tests/unit/test_nfi_full_extraction.py`:

```python
"""NFI detail-page parser must extract EVERYTHING the page offers — including
the brands/similar-products table, where کشور (country) is a flag <img> whose
title attribute carries the country name and whose gif filename carries the
ISO code (e.g. CountriesFlag/DK.gif title="دانمارک")."""
from pathlib import Path

from services.core.drug_catalog.nfi import parse_detail

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "nfi" / "nfi_detail_17248.html"


def _parsed():
    return parse_detail(FIXTURE.read_text(encoding="utf-8"), page_id=17248)


def test_existing_fields_still_extracted():
    out = _parsed()
    assert out["irc"]
    assert out["brand_name"].upper().startswith("VICTOZA")
    assert out["generic_name"] == "liraglutide"
    assert out["nfi_id"] == 17248


def test_new_scalar_fields():
    out = _parsed()
    assert out["brand_owner"]                       # صاحب برند — own key now
    assert out["license_owner"]                     # صاحب پروانه
    assert out["license_valid_until"]               # تاریخ اعتبار پروانه (Jalali string)
    assert "LIRAGLUTIDE" in out["composition"].upper()   # raw ترکیبات text kept
    # NOTE: فارماکوکینتیک/مکانیسم اثر are empty on THIS real page (NFI left them
    # blank for Victoza), so we don't assert their presence here — the label
    # mapping is exercised with populated data by Task 2's importer test. We only
    # assert the label is *wired* (present in _PAIR_LABELS), checked below.


def test_pharmacokinetics_label_is_mapped():
    from services.core.drug_catalog.nfi import _PAIR_LABELS
    assert _PAIR_LABELS.get("فارماکوکینتیک") == "pharmacokinetics"


def test_brands_table_extracted_with_country():
    out = _parsed()
    brands = out["brands"]
    assert isinstance(brands, list) and len(brands) >= 2   # page says 18 similar
    first = brands[0]
    assert set(first) == {"name", "trade_owner", "country", "country_code",
                          "licensee", "status", "nfi_id"}
    assert any(b["country"] == "دانمارک" for b in brands)
    assert any(b["country_code"] == "DK" for b in brands)


def test_top_level_country_comes_from_own_row():
    out = _parsed()
    # the row linking to /NFI/Detail/17248 is this product's own registration
    assert out["country"] == "دانمارک"


def test_country_falls_back_to_first_row_without_page_id():
    html = FIXTURE.read_text(encoding="utf-8")
    out = parse_detail(html, page_id=None)
    assert out["country"]                           # still populated
```

- [ ] **Step 1.3: Run to verify failure**

Run: `python3 -m pytest tests/unit/test_nfi_full_extraction.py -q`
Expected: FAIL — `KeyError: 'brand_owner'` / `'brands'` (existing-fields test passes).

- [ ] **Step 1.4: Implement in `services/core/drug_catalog/nfi.py`**

Add two labels to `_PAIR_LABELS` (after the `"ترکیبات"` line):

```python
    "تاریخ اعتبار پروانه": "license_valid_until",
    "فارماکوکینتیک": "pharmacokinetics",
```

Add the brands-table extractor after `_digits()`:

```python
_TABLE_RE = re.compile(r"<table[^>]*>(.*?)</table>", re.S | re.I)
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_TITLE_ATTR_RE = re.compile(r'title="([^"]+)"')
_FLAG_ISO_RE = re.compile(r"CountriesFlag/([A-Za-z]{2})\.", re.I)
_DETAIL_LINK_RE = re.compile(r"/NFI/Detail/(\d+)")


def _parse_brands(html: str) -> list[dict]:
    """The محصولات مشابه table (نام دارو/صاحب نام تجاری/کشور/صاحب امتیاز/وضعیت):
    every registered brand of this generic, incl. this product's own row.
    کشور renders as a flag <img>; the name is its title attr, ISO its filename."""
    for tbl in _TABLE_RE.findall(html):
        if "کشور" not in tbl:
            continue
        rows: list[dict] = []
        for tr in _TR_RE.findall(tbl):
            tds = _TD_RE.findall(tr)
            if len(tds) < 6:
                continue                      # header row / malformed
            title = _TITLE_ATTR_RE.search(tds[3])
            iso = _FLAG_ISO_RE.search(tds[3])
            link = _DETAIL_LINK_RE.search(tds[1])
            status_title = _TITLE_ATTR_RE.search(tds[5])
            rows.append({
                "name": _clean(tds[1]) or None,
                "trade_owner": _clean(tds[2]) or None,
                "country": (title.group(1) if title else _clean(tds[3])) or None,
                "country_code": iso.group(1).upper() if iso else None,
                "licensee": _clean(tds[4]) or None,
                "status": _clean(tds[5]) or (status_title.group(1) if status_title else None),
                "nfi_id": int(link.group(1)) if link else None,
            })
        if rows:
            return rows
    return []
```

In `parse_detail`, replace the manufacturer/license_owner block with:

```python
    manu = raw.get("manufacturer") or raw.get("brand_owner") or raw.get("license_owner")
    if manu:
        out["manufacturer"] = manu
    for k in ("license_owner", "brand_owner", "license_valid_until", "composition"):
        if raw.get(k):
            out[k] = raw[k]
```

Extend the clinical loop's tuple with `"pharmacokinetics"`:

```python
    for k in ("indications", "interactions_text", "warnings", "side_effects",
              "advice", "mechanism", "pharmacokinetics"):
```

And before `out["category"] = "drug"`:

```python
    brands = _parse_brands(html)
    if brands:
        out["brands"] = brands
        own = next((b for b in brands if page_id is not None and b["nfi_id"] == page_id),
                   brands[0])
        if own.get("country"):
            out["country"] = own["country"]
```

- [ ] **Step 1.5: Run to verify pass**

Run: `python3 -m pytest tests/unit/test_nfi_full_extraction.py -q`
Expected: 6 passed.

- [ ] **Step 1.6: Commit**

```bash
git add tests/fixtures/nfi/ tests/unit/test_nfi_full_extraction.py services/core/drug_catalog/nfi.py
git commit -m "feat(nfi): extract producer country, brands table, and every page field"
```

---

### Task 2: CatalogRecord + importer mapping (TDD, pure)

**Files:**
- Modify: `services/core/drug_catalog/schema.py` (CatalogRecord), `services/core/drug_catalog/importer.py`
- Create: `tests/unit/test_catalog_importer_enrichment.py`

- [ ] **Step 2.1: Write the failing tests**

Create `tests/unit/test_catalog_importer_enrichment.py`:

```python
"""build_records must map the enriched NFI parser output (country, owners,
monograph) and the upsert must never wipe existing enrichment/coverage with
NULLs when a source doesn't supply them (Excel imports carry no monograph)."""
from services.core.drug_catalog.importer import build_records, upsert_values

ROW = {
    "irc": "1234567890123456", "name_fa": "ویکتوزا", "generic_name": "liraglutide",
    "dosage_form": "INJECTION", "strength": "6 mg/1mL", "announced_price": "2500000",
    "manufacturer": "Novo Nordisk", "country": "دانمارک",
    "license_owner": "نوو نوردیسک پارس", "brand_owner": "Novo Nordisk",
    "license_valid_until": "1405/04/16",
    "composition": "LIRAGLUTIDE 6 mg/1mL",
    "indications": "دیابت نوع ۲", "mechanism": "آگونیست GLP-1",
    "pharmacokinetics": "نیمه‌عمر ۱۳ ساعت", "warnings": "پانکراتیت",
    "side_effects": "تهوع", "interactions_text": "انسولین", "advice": "تزریق روزانه",
    "brands": [{"name": "ویکتوزا", "trade_owner": "Novo Nordisk", "country": "دانمارک",
                "country_code": "DK", "licensee": "x", "status": None, "nfi_id": 17248}],
}


def test_build_records_maps_enrichment_fields():
    rec = build_records([ROW])[0]
    assert rec.country == "دانمارک"
    assert rec.license_owner == "نوو نوردیسک پارس"
    assert rec.brand_owner == "Novo Nordisk"
    assert rec.license_valid_until == "1405/04/16"
    mono = rec.monograph
    assert mono["composition"] == "LIRAGLUTIDE 6 mg/1mL"
    assert mono["pharmacokinetics"] == "نیمه‌عمر ۱۳ ساعت"
    assert mono["brands"][0]["country_code"] == "DK"
    for k in ("indications", "mechanism", "warnings", "side_effects",
              "interactions_text", "advice"):
        assert mono[k]


def test_build_records_without_enrichment_gives_none_monograph():
    bare = {"irc": "111", "name_fa": "x", "generic_name": "y"}
    rec = build_records([bare])[0]
    assert rec.monograph is None and rec.country is None


def test_upsert_values_excludes_sticky_none_fields_from_update():
    rec = build_records([{"irc": "111", "name_fa": "x", "generic_name": "y"}])[0]
    values, update_cols = upsert_values(rec, source="excel-import")
    # a source that carries no coverage/monograph/country must not NULL them out
    for sticky in ("coverage", "monograph", "country", "license_owner",
                   "brand_owner", "license_valid_until"):
        assert sticky not in update_cols
    assert "name_fa" in update_cols and "irc" not in update_cols


def test_upsert_values_includes_sticky_fields_when_present():
    rec = build_records([ROW])[0]
    values, update_cols = upsert_values(rec, source="nfi-harvest")
    assert update_cols["country"] == "دانمارک"
    assert update_cols["monograph"]["composition"]
```

- [ ] **Step 2.2: Run to verify failure**

Run: `python3 -m pytest tests/unit/test_catalog_importer_enrichment.py -q`
Expected: FAIL — `ImportError: cannot import name 'upsert_values'`.

- [ ] **Step 2.3: Implement**

`services/core/drug_catalog/schema.py` — append to the `CatalogRecord` dataclass fields (after `coverage`):

```python
    country: str | None = None                # کشور تولیدکننده (from the NFI brands table)
    license_owner: str | None = None          # صاحب پروانه
    brand_owner: str | None = None            # صاحب برند
    license_valid_until: str | None = None    # تاریخ اعتبار پروانه (Jalali, as printed)
    # everything else the NFI page offers: clinical sections + composition + brands
    monograph: dict | None = None
```

`services/core/drug_catalog/importer.py`:

Add aliases (append to `_ALIASES`):

```python
    "country": ("country", "کشور", "کشور_تولیدکننده"),
    "license_owner": ("license_owner",),
    "brand_owner": ("brand_owner",),
    "license_valid_until": ("license_valid_until",),
```

(Note: `صاحب_پروانه` stays a *manufacturer* alias for Excel imports; the parser
emits distinct `license_owner`/`manufacturer` keys so the harvest path is exact.)

Add monograph assembly + new fields in `build_records` (inside the loop, before `out.append`):

```python
        mono_keys = ("indications", "mechanism", "pharmacokinetics", "warnings",
                     "side_effects", "interactions_text", "advice", "composition", "brands")
        mono = {k: row[k] for k in mono_keys if row.get(k)}
```

and extend the `CatalogRecord(...)` call:

```python
            country=(str(_pick(row, "country")).strip() if _pick(row, "country") else None),
            license_owner=(str(_pick(row, "license_owner")).strip() if _pick(row, "license_owner") else None),
            brand_owner=(str(_pick(row, "brand_owner")).strip() if _pick(row, "brand_owner") else None),
            license_valid_until=(str(_pick(row, "license_valid_until")).strip() if _pick(row, "license_valid_until") else None),
            monograph=(mono or None),
```

Extend `_COL_LIMITS`:

```python
_COL_LIMITS = {"irc": 32, "gtin": 20, "name_fa": 300, "generic_name": 200,
               "ingredient_key": 300, "dosage_form": 80, "strength": 80,
               "brand_name": 200, "manufacturer": 200, "atc": 16, "source": 40,
               "country": 80, "license_owner": 200, "brand_owner": 200,
               "license_valid_until": 20}
```

Refactor `upsert_catalog` to expose a pure, testable helper. Fields that a
source may simply not know about must not be nulled on conflict-update:

```python
# Nullable enrichment a source may not carry — omit from the UPDATE when None so
# e.g. an Excel price import can't wipe NFI monographs or insurer coverage.
_STICKY_FIELDS = ("coverage", "monograph", "country", "license_owner",
                  "brand_owner", "license_valid_until")


def upsert_values(r: CatalogRecord, *, source: str) -> tuple[dict, dict]:
    """(insert values, on-conflict update columns) for one record."""
    values = dict(
        irc=r.irc, name_fa=r.name_fa, generic_name=r.generic_name,
        ingredient_key=r.ingredient_key, dosage_form=r.dosage_form, strength=r.strength,
        brand_name=r.brand_name, manufacturer=r.manufacturer, atc=r.atc,
        package_count=r.package_count, gtin=r.gtin, is_generic=r.is_generic,
        category=r.category.value,
        announced_price=(int(r.announced_price) if r.announced_price is not None else None),
        last_invoice_price=(int(r.last_invoice_price) if r.last_invoice_price is not None else None),
        coverage=r.coverage, source=source,
        country=r.country, license_owner=r.license_owner, brand_owner=r.brand_owner,
        license_valid_until=r.license_valid_until, monograph=r.monograph,
    )
    values = {k: _clamp(k, v) for k, v in values.items()}
    update_cols = {k: v for k, v in values.items()
                   if k != "irc" and not (k in _STICKY_FIELDS and v is None)}
    return values, update_cols


async def upsert_catalog(session, records: Iterable[CatalogRecord], *, source: str = "nfi") -> int:
    """Upsert CatalogRecords into drug_catalog by IRC, computing ingredient_key.
    Returns the number of rows written."""
    from sqlalchemy.dialects.postgresql import insert
    from shared.models.drug_catalog import DrugCatalogItem

    n = 0
    for r in records:
        values, update_cols = upsert_values(r, source=source)
        stmt = insert(DrugCatalogItem).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=["irc"], set_=update_cols)
        await session.execute(stmt)
        n += 1
    await session.commit()
    return n
```

Also update `repo.py:_to_record` to carry the new columns through:

```python
        package_count=row.package_count, gtin=row.gtin, coverage=row.coverage,
        country=row.country, license_owner=row.license_owner,
        brand_owner=row.brand_owner, license_valid_until=row.license_valid_until,
        monograph=row.monograph,
```

(This compiles only after Task 3 adds the model columns — run repo-touching
tests after Task 3; the pure tests in this task don't touch `repo.py`.)

- [ ] **Step 2.4: Run to verify pass**

Run: `python3 -m pytest tests/unit/test_catalog_importer_enrichment.py tests/unit/test_drug_catalog.py tests/unit/test_coverage_import.py -q`
Expected: all pass (existing suites unaffected).

- [ ] **Step 2.5: Commit**

```bash
git add services/core/drug_catalog/schema.py services/core/drug_catalog/importer.py services/core/drug_catalog/repo.py tests/unit/test_catalog_importer_enrichment.py
git commit -m "feat(catalog): enrichment fields + monograph; sticky-None upsert stops coverage wipe"
```

---

### Task 3: Models + migration 0018 (catalog columns, coverage tables, seeds)

**Files:**
- Modify: `shared/models/drug_catalog.py`, `shared/models/__init__.py`
- Create: `shared/models/coverage.py`, `data/migrations/versions/0018_catalog_enrichment_coverage.py`

- [ ] **Step 3.1: Add columns to `shared/models/drug_catalog.py`** (after the `coverage` column):

```python
    # NFI enrichment (full detail-page extraction)
    country: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    license_owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    brand_owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    license_valid_until: Mapped[str | None] = mapped_column(String(20), nullable=True)  # Jalali as printed
    # everything else the NFI page offers: clinical sections + composition + brands table
    monograph: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
```

- [ ] **Step 3.2: Create `shared/models/coverage.py`**

```python
"""دارونامه acquisition — per-insurer source configs and staged harvest runs.

A CoverageSource is a repeatable pointer at wherever an insurer publishes its
formulary (URL + strategy + parse settings). A CoverageRun is one harvest's
staged output: parsed coverage, diff vs the live catalog, and the review queue.
Nothing touches drug_catalog.coverage until an admin approves the run.
"""
from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import TimestampedBase


class CoverageSource(TimestampedBase):
    __tablename__ = "coverage_sources"

    insurer: Mapped[str] = mapped_column(String(40), index=True, nullable=False)   # "tamin" | "salamat" | ...
    name: Mapped[str] = mapped_column(String(120), nullable=False)                  # display, e.g. "دارونامه تأمین اجتماعی"
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # "auto" | "file_url" | "html_table" | "paginated_html" | "json_api"
    strategy: Mapped[str] = mapped_column(String(20), default="auto", nullable=False)
    # {page_param, page_start, max_pages, table_index, record_path, delay_sec,
    #  proxy, encoding, column_overrides: {header: role|"ignore"}}
    settings: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    check_interval_days: Mapped[int] = mapped_column(Integer, default=7, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_status: Mapped[str | None] = mapped_column(String(20), nullable=True)


class CoverageRun(TimestampedBase):
    __tablename__ = "coverage_runs"

    source_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("coverage_sources.id"), index=True, nullable=False)
    insurer: Mapped[str] = mapped_column(String(40), nullable=False)   # denormalized for display
    # "running" | "parsed" | "approved" | "rejected" | "failed"
    status: Mapped[str] = mapped_column(String(20), default="running", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stats: Mapped[dict | None] = mapped_column(JSONB, nullable=True)      # {rows, applied, review, unmatched, skipped, columns}
    diff: Mapped[dict | None] = mapped_column(JSONB, nullable=True)       # {added, changed, removed, samples}
    staged: Mapped[dict | None] = mapped_column(JSONB, nullable=True)     # irc → {insurer: entry}
    review: Mapped[list | None] = mapped_column(JSONB, nullable=True)     # [{id, row, irc, name, confidence, entry}]
    unmatched: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # capped sample
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 3.3: Register in `shared/models/__init__.py`** — add alongside the other model imports (match the file's existing import style exactly; it eagerly imports every module):

```python
from . import coverage  # noqa: F401
```

- [ ] **Step 3.4: Create the migration**

First read the head's revision id: `grep -E "^(revision|down_revision)" data/migrations/versions/0017_drug_price_proposals.py` — use its `revision` value as `down_revision` below (shown here as `"0017_drug_price_proposals"`; substitute the actual string). Also confirm the id-column idiom used by sibling tables: `grep -B1 -A2 'uuid_generate\|gen_random' data/migrations/versions/0016_drug_catalog.py | head -8` — the live `drug_catalog.id` carries `server_default=uuid_generate_v4()`, and new tables must match or the parity test flags them.

Create `data/migrations/versions/0018_catalog_enrichment_coverage.py`:

```python
"""catalog enrichment columns + coverage sources/runs (+ seeded insurer sources)"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0018_catalog_enrichment_coverage"
down_revision = "0017_drug_price_proposals"   # ← actual revision string of 0017
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("drug_catalog", sa.Column("country", sa.String(80), nullable=True))
    op.add_column("drug_catalog", sa.Column("license_owner", sa.String(200), nullable=True))
    op.add_column("drug_catalog", sa.Column("brand_owner", sa.String(200), nullable=True))
    op.add_column("drug_catalog", sa.Column("license_valid_until", sa.String(20), nullable=True))
    op.add_column("drug_catalog", sa.Column("monograph", JSONB, nullable=True))
    op.create_index("ix_drug_catalog_country", "drug_catalog", ["country"])

    op.create_table(
        "coverage_sources",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("insurer", sa.String(40), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("url", sa.String(500), nullable=True),
        sa.Column("strategy", sa.String(20), nullable=False),
        sa.Column("settings", JSONB, nullable=True),
        sa.Column("check_interval_days", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_status", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_coverage_sources_insurer", "coverage_sources", ["insurer"])

    op.create_table(
        "coverage_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("source_id", UUID(as_uuid=True), sa.ForeignKey("coverage_sources.id"), nullable=False),
        sa.Column("insurer", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stats", JSONB, nullable=True),
        sa.Column("diff", JSONB, nullable=True),
        sa.Column("staged", JSONB, nullable=True),
        sa.Column("review", JSONB, nullable=True),
        sa.Column("unmatched", JSONB, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("applied_by", UUID(as_uuid=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_coverage_runs_source_id", "coverage_runs", ["source_id"])

    # Seed one source per major insurer. URLs are best-known ROOTS, unverifiable
    # without the Iran proxy — the admin probes and corrects them in the GUI.
    op.execute(sa.text("""
        INSERT INTO coverage_sources (id, insurer, name, url, strategy, settings,
                                      check_interval_days, enabled, created_at, updated_at)
        VALUES
        (gen_random_uuid(), 'tamin', 'دارونامه تأمین اجتماعی',
         'https://darman.tamin.ir', 'auto', '{}', 7, true, now(), now()),
        (gen_random_uuid(), 'salamat', 'دارونامه بیمه سلامت',
         'https://ihio.gov.ir', 'auto', '{}', 7, true, now(), now()),
        (gen_random_uuid(), 'armed_forces', 'دارونامه نیروهای مسلح',
         'https://esata.ir', 'auto', '{}', 7, true, now(), now())
    """))


def downgrade() -> None:
    op.drop_index("ix_coverage_runs_source_id", table_name="coverage_runs")
    op.drop_table("coverage_runs")
    op.drop_index("ix_coverage_sources_insurer", table_name="coverage_sources")
    op.drop_table("coverage_sources")
    op.drop_index("ix_drug_catalog_country", table_name="drug_catalog")
    for col in ("monograph", "license_valid_until", "brand_owner", "license_owner", "country"):
        op.drop_column("drug_catalog", col)
```

If `gen_random_uuid()` errors on this Postgres (pre-13 without pgcrypto), check how earlier migrations generate UUIDs (`grep -rl uuid_generate data/migrations/versions | head -1`) — the DB default is `uuid_generate_v4()` per the `drug_catalog` table default, so substitute `uuid_generate_v4()`.

- [ ] **Step 3.5: Run the parity test (the migration's real test), then upgrade the dev DB**

```bash
python3 -m pytest tests/unit/test_alembic_schema_parity.py -q
DATABASE_URL="postgresql+asyncpg://pharmpilot:pharmpilot_dev@127.0.0.1:5433/pharmpilot" python3 -m alembic upgrade head
PGPASSWORD=pharmpilot_dev psql -h 127.0.0.1 -p 5433 -U pharmpilot -d pharmpilot -c "SELECT insurer, name, strategy FROM coverage_sources;"
```

Expected: parity passes (single head, ORM==chain); upgrade applies; 3 seeded rows print. If alembic reads its URL differently, check `data/migrations/env.py` for the env var it expects.

- [ ] **Step 3.6: Full catalog test sweep**

Run: `python3 -m pytest tests/unit/test_catalog_importer_enrichment.py tests/unit/test_drug_catalog.py tests/unit/test_coverage_import.py tests/unit/test_alembic_schema_parity.py -q`
Expected: all pass.

- [ ] **Step 3.7: Commit**

```bash
git add shared/models/drug_catalog.py shared/models/coverage.py shared/models/__init__.py data/migrations/versions/0018_catalog_enrichment_coverage.py
git commit -m "feat(db): catalog enrichment columns + coverage_sources/coverage_runs (seeded)"
```

---

### Task 4: Deduplicate the NFI parser (script imports the service)

**Files:**
- Modify: `scripts/harvest_nfi.py`

- [ ] **Step 4.1: Write the failing test** — append to `tests/unit/test_nfi_full_extraction.py`:

```python
def test_script_uses_the_service_parser():
    import sys
    sys.path.insert(0, ".")
    import importlib
    script = importlib.import_module("scripts.harvest_nfi")
    from services.core.drug_catalog import nfi
    assert script.parse_detail is nfi.parse_detail
```

Run: `python3 -m pytest tests/unit/test_nfi_full_extraction.py::test_script_uses_the_service_parser -q`
Expected: FAIL (script has its own copy).

- [ ] **Step 4.2: Implement**

In `scripts/harvest_nfi.py`: delete the local `parse_detail()` (L256-…), `_clean()` (L246), `_digits()` (L252) definitions and any regex constants used only by them; at the top of the file (after existing imports) add:

```python
import sys
sys.path.insert(0, ".")
from services.core.drug_catalog.nfi import parse_detail, _clean, _digits  # noqa: E402
```

Keep the script's own fetching (`_get`, opener) as-is — only the parsing dedupes. If `probe()`/`crawl()` referenced the deleted regexes, they now come via `parse_detail` only; run the next step to confirm nothing else breaks.

- [ ] **Step 4.3: Verify**

```bash
python3 -m pytest tests/unit/test_nfi_full_extraction.py -q
./.venv/bin/python -c "import sys; sys.path.insert(0,'.'); import scripts.harvest_nfi as s; print('OK', s.parse_detail.__module__)"
```

Expected: 7 passed; `OK services.core.drug_catalog.nfi`.

- [ ] **Step 4.4: Commit**

```bash
git add scripts/harvest_nfi.py tests/unit/test_nfi_full_extraction.py
git commit -m "refactor(nfi): single parse_detail — script imports the service parser"
```

---

### Task 5: Global harvest lock (TDD) + NFI service integration

**Files:**
- Create: `services/core/drug_catalog/harvest_lock.py`, `tests/unit/test_harvest_lock.py`
- Modify: `services/core/drug_catalog/nfi_harvest_service.py`

- [ ] **Step 5.1: Write the failing tests**

Create `tests/unit/test_harvest_lock.py`:

```python
"""One crawl at a time across NFI + دارونامه jobs — the Iran proxy is a system
proxy, so parallel harvests are both impolite and broken."""
import pytest

from services.core.drug_catalog import harvest_lock


@pytest.fixture(autouse=True)
def _clean_lock():
    harvest_lock.force_release()
    yield
    harvest_lock.force_release()


def test_acquire_release_roundtrip():
    assert harvest_lock.holder() is None
    assert harvest_lock.acquire("nfi") is True
    assert harvest_lock.holder() == "nfi"
    harvest_lock.release("nfi")
    assert harvest_lock.holder() is None


def test_second_acquire_blocked_and_reports_holder():
    assert harvest_lock.acquire("nfi")
    assert harvest_lock.acquire("coverage:tamin") is False
    assert harvest_lock.holder() == "nfi"


def test_release_by_non_holder_is_ignored():
    assert harvest_lock.acquire("coverage:tamin")
    harvest_lock.release("nfi")
    assert harvest_lock.holder() == "coverage:tamin"


def test_nfi_service_reports_lock_holder_in_status():
    from services.core.drug_catalog import nfi_harvest_service as svc
    assert harvest_lock.acquire("coverage:tamin")
    snap = svc.status()
    assert snap["lock_holder"] == "coverage:tamin"


def test_nfi_start_refuses_while_lock_held():
    from services.core.drug_catalog import nfi_harvest_service as svc
    assert harvest_lock.acquire("coverage:tamin")
    with pytest.raises(RuntimeError):
        svc.start(1, 2)
```

Run: `python3 -m pytest tests/unit/test_harvest_lock.py -q`
Expected: FAIL — `ModuleNotFoundError: ... harvest_lock`.

- [ ] **Step 5.2: Create `services/core/drug_catalog/harvest_lock.py`**

```python
"""Global harvest mutex — NFI crawl and دارونامه harvests share the Iran system
proxy, so only ONE may run at a time. Owner-tagged so GUIs can say who holds it."""
from __future__ import annotations

import threading

_guard = threading.Lock()
_holder: str | None = None


def acquire(owner: str) -> bool:
    global _holder
    with _guard:
        if _holder is not None:
            return False
        _holder = owner
        return True


def release(owner: str) -> None:
    global _holder
    with _guard:
        if _holder == owner:
            _holder = None


def holder() -> str | None:
    return _holder


def force_release() -> None:
    """Test/emergency use only."""
    global _holder
    with _guard:
        _holder = None
```

- [ ] **Step 5.3: Integrate into `nfi_harvest_service.py`**

Add import: `from . import harvest_lock`

In `start()`, before creating state:

```python
    if _STATE.running:
        raise RuntimeError("A harvest is already running.")
    if not harvest_lock.acquire("nfi"):
        raise RuntimeError(f"قفل برداشت در اختیار دیگری است: {harvest_lock.holder()}")
```

In `_run()`'s `finally:` block add `harvest_lock.release("nfi")` (before setting `running = False`).

In `status()`:

```python
def status() -> dict:
    d = _STATE.snapshot()
    d["lock_holder"] = harvest_lock.holder()
    return d
```

- [ ] **Step 5.4: Run to verify pass**

Run: `python3 -m pytest tests/unit/test_harvest_lock.py -q`
Expected: 5 passed.

- [ ] **Step 5.5: Commit**

```bash
git add services/core/drug_catalog/harvest_lock.py services/core/drug_catalog/nfi_harvest_service.py tests/unit/test_harvest_lock.py
git commit -m "feat(harvest): global owner-tagged harvest lock; NFI service honors it"
```

---

### Task 6: Acquisition pure logic — sniffer, strategies, overrides, diff (TDD)

**Files:**
- Create: `services/core/drug_catalog/coverage_harvest.py` (pure parts), `tests/unit/test_coverage_harvest.py`

- [ ] **Step 6.1: Write the failing tests**

Create `tests/unit/test_coverage_harvest.py`:

```python
"""دارونامه acquisition engine — pure parts: strategy sniffing, paginated
fetching (injected fetcher, no network), column-override precedence, and the
staged-vs-current coverage diff."""
import json

import pytest

from services.core.drug_catalog.coverage_harvest import (
    sniff_strategy, fetch_rows, resolve_roles, compute_diff,
)

CSV = "کد فرآورده,نام دارو,درصد تعهد\n123,استامینوفن,70\n".encode("utf-8")
HTML_TABLE = "<html><body><table><tr><th>نام دارو</th><th>درصد تعهد</th></tr><tr><td>استامینوفن</td><td>70</td></tr></table></body></html>".encode("utf-8")
JSON_BODY = json.dumps({"data": {"items": [{"name": "استامینوفن", "share": 70}]}}).encode("utf-8")
XLSX_MAGIC = b"PK\x03\x04" + b"\x00" * 32


# ── sniffer ───────────────────────────────────────────────────────────────────
def test_sniff_xlsx_magic():
    assert sniff_strategy(XLSX_MAGIC, "application/octet-stream", "https://x/y") == "file_url"

def test_sniff_json():
    assert sniff_strategy(JSON_BODY, "application/json", "https://x/api") == "json_api"

def test_sniff_html_table():
    assert sniff_strategy(HTML_TABLE, "text/html", "https://x/list") == "html_table"

def test_sniff_html_table_with_page_placeholder_is_paginated():
    assert sniff_strategy(HTML_TABLE, "text/html", "https://x/list?page={page}") == "paginated_html"

def test_sniff_csv_falls_to_file_url():
    assert sniff_strategy(CSV, "text/csv", "https://x/d.csv") == "file_url"


# ── strategies via injected fetcher ──────────────────────────────────────────
def _fake_fetcher(pages: dict[str, bytes], content_type="text/html"):
    def fetch(url: str):
        body = pages.get(url)
        return (200, body, content_type) if body is not None else (404, b"", content_type)
    return fetch


def test_file_url_strategy_parses_csv_rows():
    fetch = _fake_fetcher({"https://x/d.csv": CSV}, "text/csv")
    rows, pages = fetch_rows("https://x/d.csv", "file_url", {}, fetch)
    assert pages == 1 and len(rows) == 1
    assert rows[0]["نام_دارو"] == "استامینوفن"      # read_table normalizes headers


def test_paginated_html_stops_on_empty_page():
    p1 = HTML_TABLE
    p2 = HTML_TABLE.replace("استامینوفن".encode(), "متفورمین".encode())
    empty = "<html><body><table><tr><th>نام دارو</th></tr></table></body></html>".encode()
    fetch = _fake_fetcher({"https://x/l?page=1": p1, "https://x/l?page=2": p2,
                           "https://x/l?page=3": empty})
    rows, pages = fetch_rows("https://x/l?page={page}", "paginated_html",
                             {"page_start": 1, "max_pages": 10, "delay_sec": 0}, fetch)
    assert pages == 3 and len(rows) == 2


def test_paginated_html_stops_on_duplicate_page():
    fetch = _fake_fetcher({f"https://x/l?page={n}": HTML_TABLE for n in (1, 2, 3, 4)})
    rows, pages = fetch_rows("https://x/l?page={page}", "paginated_html",
                             {"page_start": 1, "max_pages": 10, "delay_sec": 0}, fetch)
    assert len(rows) == 1                            # page 2 duplicates page 1 → stop


def test_paginated_html_respects_max_pages():
    def infinite(url):
        n = int(url.rsplit("=", 1)[1])
        body = HTML_TABLE.replace(b"70", str(n).encode())
        return 200, body, "text/html"
    rows, pages = fetch_rows("https://x/l?page={page}", "paginated_html",
                             {"page_start": 1, "max_pages": 3, "delay_sec": 0}, infinite)
    assert pages == 3


def test_json_api_descends_record_path():
    fetch = _fake_fetcher({"https://x/api?page=1": JSON_BODY,
                           "https://x/api?page=2": json.dumps({"data": {"items": []}}).encode()},
                          "application/json")
    rows, pages = fetch_rows("https://x/api?page={page}", "json_api",
                             {"record_path": "data.items", "page_start": 1,
                              "max_pages": 5, "delay_sec": 0}, fetch)
    assert rows == [{"name": "استامینوفن", "share": 70}]


# ── column overrides ─────────────────────────────────────────────────────────
def test_infer_columns_survives_read_table_underscored_headers():
    """read_table normalizes 'درصد تعهد' → 'درصد_تعهد'. The exact-alias pass must
    still beat the 'تعهد' substring (covered) — requires _norm_header to fold
    underscores back to spaces. Regression companion to commit cfaf3c7."""
    from services.core.drug_catalog.coverage_import import infer_columns
    rows = [{"نام_دارو": "METFORMIN 500MG TAB", "درصد_تعهد": "70",
             "قیمت_مورد_تعهد": "8000", "تعهد_بیمه": "دارد"}]
    roles = infer_columns(rows)
    assert roles["درصد_تعهد"] == "share_pct"
    assert roles["تعهد_بیمه"] == "covered"
    assert roles["قیمت_مورد_تعهد"] == "reference_price"


def test_resolve_roles_overrides_beat_inference_and_ignore_drops():
    rows = [{"c1": "ACETAMINOPHEN 500MG TAB", "c2": "70", "c3": "junk"}]
    roles = resolve_roles(rows, {"c2": "share_pct", "c3": "ignore"})
    assert roles["c1"] == "drug_name"        # inferred
    assert roles["c2"] == "share_pct"        # override wins
    assert "c3" not in roles                 # ignored


# ── diff ─────────────────────────────────────────────────────────────────────
def _staged(irc, **entry):
    return {irc: {"tamin": {"covered": True, **entry}}}


def test_diff_added_changed_removed():
    staged = {**_staged("111", share_pct=70), **_staged("222", share_pct=90)}
    current = {"222": {"covered": True, "share_pct": 70},          # changed 70→90
               "333": {"covered": True, "share_pct": 50}}          # removed
    d = compute_diff(staged, current, insurer="tamin")
    assert d["added"] == 1 and d["changed"] == 1 and d["removed"] == 1
    ch = d["samples"]["changed"][0]
    assert ch["irc"] == "222" and ch["fields"][0] == {"field": "share_pct", "old": 70, "new": 90}
    assert d["samples"]["removed"] == ["333"]


def test_diff_ignores_match_metadata_fields():
    staged = {"111": {"tamin": {"covered": True, "share_pct": 70,
                                "match_confidence": 0.9, "match_method": "irc"}}}
    current = {"111": {"covered": True, "share_pct": 70,
                       "match_confidence": 0.6, "match_method": "ingredient"}}
    d = compute_diff(staged, current, insurer="tamin")
    assert d["changed"] == 0                 # only covered/share_pct/reference_price/ceiling count


def test_diff_samples_capped_at_50():
    staged = {str(i): {"tamin": {"covered": True}} for i in range(80)}
    d = compute_diff(staged, {}, insurer="tamin")
    assert d["added"] == 80 and len(d["samples"]["added"]) == 50
```

Run: `python3 -m pytest tests/unit/test_coverage_harvest.py -q`
Expected: FAIL — module doesn't exist.

- [ ] **Step 6.2a: Fix `_norm_header` in `services/core/drug_catalog/coverage_import.py`** — fold underscores too, so headers that already passed through `read_table` normalization still exact-match the alias table:

```python
def _norm_header(h: str) -> str:
    return re.sub(r"\s+", " ", str(h).replace("‌", " ").replace("_", " ").strip().lower())
```

- [ ] **Step 6.2b: Create `services/core/drug_catalog/coverage_harvest.py` (pure parts)**

```python
"""دارونامه acquisition engine — probe a configured insurer source, harvest it
with a pluggable strategy, run the smart coverage extractor, and stage the
result as a CoverageRun for human approval. Repeatable weekly: each run diffs
against the catalog's live coverage so the admin sees exactly what changed.

Deterministic, no LLM. Network I/O is isolated behind a fetcher callable so
every strategy is testable offline. Shares the global harvest_lock with the
NFI crawler (system-wide Iran proxy ⇒ one crawl at a time).
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
import urllib.request
from dataclasses import asdict, dataclass

from .coverage_import import build_coverage, infer_columns, link_rows, normalize_rows
from .excel_import import read_table
from .nfi import UA, make_opener
from . import harvest_lock

# entry fields that constitute a real coverage change (match metadata excluded)
_DIFF_FIELDS = ("covered", "share_pct", "reference_price", "ceiling")
_SAMPLE_CAP = 50

STRATEGIES = ("auto", "file_url", "html_table", "paginated_html", "json_api")


# ── fetching ─────────────────────────────────────────────────────────────────
def make_fetcher(proxy: str | None = None, timeout: int = 30):
    """fetch(url) -> (status, body_bytes, content_type). Proxy falls back to the
    server's HTTPS_PROXY (the Iran system proxy)."""
    opener = make_opener(proxy or os.getenv("HTTPS_PROXY"))

    def fetch(url: str) -> tuple[int, bytes, str]:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Accept": "*/*", "Accept-Language": "fa,en;q=0.8"})
        try:
            with opener.open(req, timeout=timeout) as resp:
                return resp.status, resp.read(), resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            return e.code, b"", ""
        except Exception:
            return 0, b"", ""
    return fetch


# ── strategy sniffing ────────────────────────────────────────────────────────
def sniff_strategy(body: bytes, content_type: str, url: str) -> str:
    ct = (content_type or "").lower()
    if body[:4] == b"PK\x03\x04" or "spreadsheet" in ct or "excel" in ct:
        return "file_url"
    stripped = body.lstrip()[:1]
    if stripped in (b"{", b"[") or "json" in ct:
        return "json_api"
    text = body[:200_000].decode("utf-8", errors="replace").lower()
    if "<table" in text:
        return "paginated_html" if "{page}" in url else "html_table"
    return "file_url"                       # CSV & anything table-file-like


# ── row extraction per strategy ──────────────────────────────────────────────
def _rows_from_bytes(body: bytes, content_type: str, url: str) -> list[dict]:
    """Write to a temp file with the right suffix and reuse read_table."""
    ct = (content_type or "").lower()
    if body[:4] == b"PK\x03\x04" or "spreadsheet" in ct:
        suffix = ".xlsx"
    elif "<table" in body[:200_000].decode("utf-8", errors="replace").lower():
        suffix = ".html"
    else:
        m = re.search(r"\.(xlsx|xlsm|xls|csv|tsv|html?|txt)(?:$|[?#])", url.lower())
        suffix = f".{m.group(1)}" if m else ".csv"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(body)
        tmp.close()
        return read_table(tmp.name)
    finally:
        os.unlink(tmp.name)


def _descend(obj, path: str):
    for part in [p for p in (path or "").split(".") if p]:
        if not isinstance(obj, dict) or part not in obj:
            return []
        obj = obj[part]
    return obj if isinstance(obj, list) else []


def _page_url(url_template: str, settings: dict, page: int) -> str:
    if "{page}" in url_template:
        return url_template.replace("{page}", str(page))
    sep = "&" if "?" in url_template else "?"
    return f"{url_template}{sep}{settings.get('page_param', 'page')}={page}"


def fetch_rows(url: str, strategy: str, settings: dict, fetch) -> tuple[list[dict], int]:
    """Run a strategy → (raw rows, pages fetched). Raises RuntimeError on a
    transport-dead first page so the caller can fail the run with a clear cause."""
    settings = settings or {}
    if strategy in ("file_url", "html_table"):
        status, body, ct = fetch(url)
        if status != 200 or not body:
            raise RuntimeError(f"HTTP {status} از مقصد — پروکسی ایران در دسترس نیست یا آدرس اشتباه است")
        rows = _rows_from_bytes(body, ct, url)
        if strategy == "html_table":
            idx = int(settings.get("table_index", 0) or 0)
            _ = idx  # read_table returns the largest table; index reserved for future selector work
        return rows, 1

    # paginated strategies
    page = int(settings.get("page_start", 1) or 1)
    max_pages = int(settings.get("max_pages", 500) or 500)
    delay = float(settings.get("delay_sec", 0.5) or 0)
    all_rows: list[dict] = []
    prev_fingerprint = None
    pages = 0
    while pages < max_pages:
        status, body, ct = fetch(_page_url(url, settings, page))
        pages += 1
        if status != 200 or not body:
            if pages == 1:
                raise RuntimeError(f"HTTP {status} از مقصد — پروکسی ایران در دسترس نیست یا آدرس اشتباه است")
            break
        if strategy == "json_api":
            try:
                doc = json.loads(body)
            except json.JSONDecodeError:
                break
            rows = _descend(doc, settings.get("record_path", ""))
        else:
            rows = _rows_from_bytes(body, ct, url)
        if not rows:
            break
        fingerprint = json.dumps(rows[:3], sort_keys=True, ensure_ascii=False, default=str)
        if fingerprint == prev_fingerprint:
            break                            # site ignores the page param → stop
        prev_fingerprint = fingerprint
        all_rows.extend(rows)
        page += 1
        if delay:
            time.sleep(delay)
    return all_rows, pages


# ── column-role resolution (saved overrides beat inference) ──────────────────
def resolve_roles(rows: list[dict], overrides: dict[str, str] | None) -> dict[str, str]:
    roles = infer_columns(rows)
    for col, role in (overrides or {}).items():
        if role == "ignore":
            roles.pop(col, None)
        elif rows and col in rows[0]:
            for existing_col, r in list(roles.items()):
                if r == role and existing_col != col:
                    del roles[existing_col]     # override claims the role uniquely
            roles[col] = role
    return roles


# ── diff (staged vs live coverage for one insurer) ───────────────────────────
def compute_diff(staged: dict, current: dict[str, dict], *, insurer: str) -> dict:
    """staged: irc → {insurer: entry}; current: irc → entry (live coverage[insurer])."""
    added, changed, removed = [], [], []
    for irc, per_insurer in staged.items():
        new = per_insurer.get(insurer) or {}
        old = current.get(irc)
        if old is None:
            added.append(irc)
            continue
        fields = [{"field": f, "old": old.get(f), "new": new.get(f)}
                  for f in _DIFF_FIELDS if old.get(f) != new.get(f)]
        if fields:
            changed.append({"irc": irc, "fields": fields})
    for irc in current:
        if irc not in staged:
            removed.append(irc)
    return {
        "added": len(added), "changed": len(changed), "removed": len(removed),
        "samples": {"added": added[:_SAMPLE_CAP],
                    "changed": changed[:_SAMPLE_CAP],
                    "removed": removed[:_SAMPLE_CAP]},
    }
```

- [ ] **Step 6.3: Run to verify pass**

Run: `python3 -m pytest tests/unit/test_coverage_harvest.py tests/unit/test_coverage_import.py -q`
Expected: 15 + 8 passed (the underscored-header regression included; existing coverage_import suite unaffected by the `_norm_header` change).

- [ ] **Step 6.4: Commit**

```bash
git add services/core/drug_catalog/coverage_harvest.py services/core/drug_catalog/coverage_import.py tests/unit/test_coverage_harvest.py
git commit -m "feat(coverage): acquisition strategies, sniffer, role overrides, diff; underscored-header fix"
```

---

### Task 7: Harvest run service — probe, background run, apply/reject

**Files:**
- Modify: `services/core/drug_catalog/coverage_harvest.py` (append service layer), `tests/unit/test_coverage_harvest.py` (append)

- [ ] **Step 7.1: Write the failing tests** — append to `tests/unit/test_coverage_harvest.py`:

```python
# ── service-layer pure pieces ────────────────────────────────────────────────
from services.core.drug_catalog import harvest_lock
from services.core.drug_catalog.coverage_harvest import (
    probe_payload, stage_run_payload, CoverageHarvestState,
)


def test_probe_payload_detects_and_samples():
    fetch = _fake_fetcher({"https://x/l": HTML_TABLE})
    p = probe_payload("https://x/l", settings={}, fetch=fetch)
    assert p["detected_strategy"] == "html_table"
    assert p["sample_rows"] and p["inferred_columns"]
    assert p["row_count_sampled"] == 1


def test_probe_payload_unreachable_raises():
    fetch = _fake_fetcher({})
    with pytest.raises(RuntimeError):
        probe_payload("https://x/nope", settings={}, fetch=fetch)


def test_stage_run_payload_builds_stats_review_ids_and_diff():
    from services.core.drug_catalog.schema import CatalogRecord
    from decimal import Decimal
    catalog = [CatalogRecord(irc="123", name_fa="استامینوفن", generic_name="acetaminophen",
                             dosage_form="TABLET", strength="500 mg",
                             announced_price=Decimal("8000"))]
    rows = [{"کد فرآورده": "123", "نام دارو": "استامینوفن", "درصد تعهد": "70"}]
    payload = stage_run_payload(rows, catalog, insurer="tamin",
                                overrides=None, current={},
                                min_confidence=0.75)
    assert payload["stats"]["rows"] == 1 and payload["stats"]["applied"] == 1
    assert payload["staged"]["123"]["tamin"]["share_pct"] == 70
    assert payload["diff"]["added"] == 1
    assert all("id" in item for item in payload["review"])


def test_harvest_state_snapshot_has_lock_holder():
    harvest_lock.force_release()
    st = CoverageHarvestState(running=True, insurer="tamin", phase="fetching")
    snap = st.snapshot()
    assert snap["insurer"] == "tamin" and "lock_holder" in snap
```

Run: `python3 -m pytest tests/unit/test_coverage_harvest.py -q`
Expected: new tests FAIL (ImportError).

- [ ] **Step 7.2: Append the service layer to `coverage_harvest.py`**

```python
# ── probe (synchronous, one fetch, persists nothing) ─────────────────────────
def probe_payload(url: str, *, settings: dict, fetch) -> dict:
    status, body, ct = fetch(url)
    if status != 200 or not body:
        raise RuntimeError(f"HTTP {status} از مقصد — پروکسی ایران در دسترس نیست یا آدرس اشتباه است")
    strategy = sniff_strategy(body, ct, url)
    if strategy == "json_api":
        rows = _descend(json.loads(body), (settings or {}).get("record_path", ""))[:20]
    else:
        rows = _rows_from_bytes(body, ct, url)[:20]
    return {
        "detected_strategy": strategy,
        "proposed_settings": {"page_start": 1, "max_pages": 500, "delay_sec": 0.5},
        "sample_rows": rows[:20],
        "row_count_sampled": len(rows),
        "inferred_columns": resolve_roles(rows, (settings or {}).get("column_overrides")),
    }


# ── staging (pure: rows + catalog + current coverage → run payload) ──────────
def stage_run_payload(rows: list[dict], catalog: list, *, insurer: str,
                      overrides: dict | None, current: dict[str, dict],
                      min_confidence: float = 0.75) -> dict:
    roles = resolve_roles(rows, overrides)
    if "drug_name" not in roles.values() and "irc" not in roles.values():
        raise RuntimeError(
            f"ستون نام دارو یا IRC شناسایی نشد — ستون‌ها: {list(rows[0].keys())[:12] if rows else []}")
    normalized = normalize_rows(rows, roles)
    links = link_rows(normalized, catalog)
    cov = build_coverage(links, insurer=insurer, min_confidence=min_confidence,
                         catalog=catalog)
    review = [{"id": i, **item, "accepted": False} for i, item in enumerate(cov.review)]
    return {
        "stats": {**cov.stats, "columns": roles},
        "staged": cov.applied,
        "review": review,
        "unmatched": cov.unmatched[:200],
        "diff": compute_diff(cov.applied, current, insurer=insurer),
    }


# ── background run state (mirrors nfi_harvest_service) ───────────────────────
@dataclass
class CoverageHarvestState:
    running: bool = False
    source_id: str = ""
    insurer: str = ""
    phase: str = ""              # fetching | linking | diffing | saving | done | failed
    pages: int = 0
    rows: int = 0
    run_id: str | None = None
    error: str | None = None
    started_at: float | None = None
    finished_at: float | None = None

    def snapshot(self) -> dict:
        d = asdict(self)
        d["lock_holder"] = harvest_lock.holder()
        d["elapsed_sec"] = round((self.finished_at or time.time()) - self.started_at, 1) if self.started_at else 0.0
        return d


_STATE = CoverageHarvestState()


def status() -> dict:
    return _STATE.snapshot()


async def _current_coverage(db, insurer: str) -> dict[str, dict]:
    from sqlalchemy import select
    from shared.models.drug_catalog import DrugCatalogItem
    rows = (await db.execute(
        select(DrugCatalogItem.irc, DrugCatalogItem.coverage)
        .where(DrugCatalogItem.coverage.isnot(None)))).all()
    out = {}
    for irc, cov in rows:
        if isinstance(cov, dict) and insurer in cov:
            out[irc] = cov[insurer]
    return out


async def _run(source_id) -> None:
    from datetime import datetime, timezone
    from sqlalchemy import select
    from services.platform.database import AsyncSessionLocal
    from shared.models.coverage import CoverageRun, CoverageSource
    from . import repo

    owner = f"coverage:{_STATE.insurer}"
    run_id = None
    try:
        async with AsyncSessionLocal() as db:
            src = (await db.execute(select(CoverageSource)
                                    .where(CoverageSource.id == source_id))).scalar_one()
            run = CoverageRun(source_id=src.id, insurer=src.insurer, status="running",
                              started_at=datetime.now(timezone.utc))
            db.add(run)
            await db.commit()
            await db.refresh(run)
            run_id = run.id
            _STATE.run_id = str(run_id)

            settings = src.settings or {}
            fetch = make_fetcher(settings.get("proxy"))
            strategy = src.strategy
            if strategy == "auto":
                s, body, ct = fetch(src.url)
                if s != 200 or not body:
                    raise RuntimeError(f"HTTP {s} از مقصد — پروکسی ایران در دسترس نیست یا آدرس اشتباه است")
                strategy = sniff_strategy(body, ct, src.url)

            _STATE.phase = "fetching"
            import asyncio
            rows, pages = await asyncio.to_thread(fetch_rows, src.url, strategy, settings, fetch)
            _STATE.pages, _STATE.rows = pages, len(rows)

            _STATE.phase = "linking"
            catalog = await repo.fetch_all(db)
            current = await _current_coverage(db, src.insurer)
            _STATE.phase = "diffing"
            payload = await asyncio.to_thread(
                stage_run_payload, rows, catalog,
                insurer=src.insurer, overrides=settings.get("column_overrides"),
                current=current)

            _STATE.phase = "saving"
            run.status = "parsed"
            run.finished_at = datetime.now(timezone.utc)
            run.stats, run.staged = payload["stats"], payload["staged"]
            run.review, run.unmatched, run.diff = payload["review"], payload["unmatched"], payload["diff"]
            src.last_run_at, src.last_run_status = run.finished_at, "parsed"
            await db.commit()
            _STATE.phase = "done"
    except Exception as e:
        _STATE.error = f"{type(e).__name__}: {e}"
        _STATE.phase = "failed"
        try:
            async with AsyncSessionLocal() as db:
                from shared.models.coverage import CoverageRun as CR, CoverageSource as CS
                from sqlalchemy import select as _sel
                if run_id is not None:
                    r = (await db.execute(_sel(CR).where(CR.id == run_id))).scalar_one_or_none()
                    if r:
                        r.status, r.error = "failed", _STATE.error
                s = (await db.execute(_sel(CS).where(CS.id == source_id))).scalar_one_or_none()
                if s:
                    from datetime import datetime as _dt, timezone as _tz
                    s.last_run_at, s.last_run_status = _dt.now(_tz.utc), "failed"
                await db.commit()
        except Exception:
            pass
    finally:
        harvest_lock.release(owner)
        _STATE.running = False
        _STATE.finished_at = time.time()


def start_harvest(source_id, insurer: str) -> dict:
    """Kick off a background دارونامه harvest. Raises RuntimeError if the global
    harvest lock is held (NFI crawl or another coverage job)."""
    global _STATE
    import asyncio
    if _STATE.running:
        raise RuntimeError("یک برداشت پوشش در حال اجراست.")
    owner = f"coverage:{insurer}"
    if not harvest_lock.acquire(owner):
        raise RuntimeError(f"قفل برداشت در اختیار دیگری است: {harvest_lock.holder()}")
    _STATE = CoverageHarvestState(running=True, source_id=str(source_id),
                                  insurer=insurer, phase="starting",
                                  started_at=time.time())
    asyncio.create_task(_run(source_id))
    return _STATE.snapshot()


# ── approve / reject ─────────────────────────────────────────────────────────
async def apply_run(db, run_id, *, remove_missing: bool = False,
                    accepted_review_ids: list[int] | None = None,
                    staff_id=None) -> dict:
    from datetime import datetime, timezone
    from sqlalchemy import select
    from shared.models.coverage import CoverageRun
    from shared.models.drug_catalog import DrugCatalogItem
    from .coverage_import import apply_coverage

    run = (await db.execute(select(CoverageRun).where(CoverageRun.id == run_id))).scalar_one()
    if run.status != "parsed":
        raise RuntimeError(f"فقط اجرای parsed قابل اعمال است (وضعیت فعلی: {run.status})")

    staged = dict(run.staged or {})
    accepted = set(accepted_review_ids or [])
    review_applied = 0
    for item in (run.review or []):
        if item["id"] in accepted:
            # spread the accepted entry across the matched product's ingredient group
            target = (await db.execute(select(DrugCatalogItem).where(
                DrugCatalogItem.irc == item["irc"]))).scalar_one_or_none()
            if not target:
                continue
            group = (await db.execute(select(DrugCatalogItem.irc).where(
                DrugCatalogItem.ingredient_key == target.ingredient_key))).scalars().all()
            for irc in group or [item["irc"]]:
                staged.setdefault(irc, {})[run.insurer] = item["entry"]
            review_applied += 1

    updated = await apply_coverage(db, staged)

    removed_cleared = 0
    if remove_missing:
        for irc in (run.diff or {}).get("samples", {}).get("removed", []):
            row = (await db.execute(select(DrugCatalogItem).where(
                DrugCatalogItem.irc == irc))).scalar_one_or_none()
            if row and isinstance(row.coverage, dict) and run.insurer in row.coverage:
                cov = dict(row.coverage)
                cov.pop(run.insurer)
                row.coverage = cov or None
                removed_cleared += 1

    run.status = "approved"
    run.applied_by = staff_id
    run.applied_at = datetime.now(timezone.utc)
    await db.commit()
    return {"products_updated": updated, "review_applied": review_applied,
            "removed_cleared": removed_cleared}


async def reject_run(db, run_id) -> None:
    from sqlalchemy import select
    from shared.models.coverage import CoverageRun
    run = (await db.execute(select(CoverageRun).where(CoverageRun.id == run_id))).scalar_one()
    if run.status != "parsed":
        raise RuntimeError(f"فقط اجرای parsed قابل رد است (وضعیت فعلی: {run.status})")
    run.status = "rejected"
    await db.commit()
```

Note on `remove_missing`: it operates on the diff's `removed` **samples** (capped at 50). If a weekly list legitimately drops >50 items, the cap under-removes — acceptable v1 behavior; the diff panel shows the true `removed` count so the admin sees the discrepancy. (Documented in the GUI copy in Task 9.)

- [ ] **Step 7.3: Run to verify pass**

Run: `python3 -m pytest tests/unit/test_coverage_harvest.py -q`
Expected: 19 passed.

- [ ] **Step 7.4: Commit**

```bash
git add services/core/drug_catalog/coverage_harvest.py tests/unit/test_coverage_harvest.py
git commit -m "feat(coverage): probe, staged background runs, approve/reject with review spread"
```

---

### Task 8: API endpoints

**Files:**
- Modify: `services/platform/routers/pricing.py`

- [ ] **Step 8.1: Append the endpoints** (after the existing `/coverage/import` endpoint; imports at top of the block, local to keep the module's lazy-import style):

```python
# ── دارونامه coverage sources & staged runs ───────────────────────────────────
class CoverageSourceIn(BaseModel):
    insurer: str
    name: str
    url: str | None = None
    strategy: str = "auto"
    settings: dict | None = None
    check_interval_days: int = 7
    enabled: bool = True


class ApproveRunRequest(BaseModel):
    remove_missing: bool = False
    accepted_review_ids: list[int] = []


def _source_json(s, lock_holder: str | None) -> dict:
    from datetime import datetime, timezone
    due = bool(s.enabled and (
        s.last_run_at is None or
        (datetime.now(timezone.utc) - s.last_run_at).days >= s.check_interval_days))
    return {"id": str(s.id), "insurer": s.insurer, "name": s.name, "url": s.url,
            "strategy": s.strategy, "settings": s.settings or {},
            "check_interval_days": s.check_interval_days, "enabled": s.enabled,
            "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None,
            "last_run_status": s.last_run_status, "due": due,
            "lock_holder": lock_holder}


@router.get("/coverage/sources")
async def list_coverage_sources(staff: Staff = Depends(require_permission("inventory:read")),
                                db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    from shared.models.coverage import CoverageSource
    from services.core.drug_catalog import harvest_lock
    rows = (await db.execute(select(CoverageSource)
                             .order_by(CoverageSource.created_at))).scalars().all()
    return {"sources": [_source_json(s, harvest_lock.holder()) for s in rows]}


@router.post("/coverage/sources")
async def create_coverage_source(body: CoverageSourceIn,
                                 staff: Staff = Depends(require_permission("inventory:write")),
                                 db: AsyncSession = Depends(get_db)):
    from shared.models.coverage import CoverageSource
    from services.core.drug_catalog import harvest_lock
    s = CoverageSource(**body.model_dump())
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return _source_json(s, harvest_lock.holder())


@router.put("/coverage/sources/{source_id}")
async def update_coverage_source(source_id: UUID, body: CoverageSourceIn,
                                 staff: Staff = Depends(require_permission("inventory:write")),
                                 db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    from shared.models.coverage import CoverageSource
    from services.core.drug_catalog import harvest_lock
    s = (await db.execute(select(CoverageSource)
                          .where(CoverageSource.id == source_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Source not found")
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    await db.commit()
    await db.refresh(s)
    return _source_json(s, harvest_lock.holder())


@router.delete("/coverage/sources/{source_id}")
async def delete_coverage_source(source_id: UUID,
                                 staff: Staff = Depends(require_permission("inventory:write")),
                                 db: AsyncSession = Depends(get_db)):
    from sqlalchemy import delete as sa_delete, select
    from shared.models.coverage import CoverageRun, CoverageSource
    s = (await db.execute(select(CoverageSource)
                          .where(CoverageSource.id == source_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Source not found")
    await db.execute(sa_delete(CoverageRun).where(CoverageRun.source_id == source_id))
    await db.delete(s)
    await db.commit()
    return {"deleted": True}


@router.post("/coverage/sources/{source_id}/probe")
async def probe_coverage_source(source_id: UUID,
                                staff: Staff = Depends(require_permission("inventory:write")),
                                db: AsyncSession = Depends(get_db)):
    import asyncio
    from sqlalchemy import select
    from shared.models.coverage import CoverageSource
    from services.core.drug_catalog import coverage_harvest as ch
    s = (await db.execute(select(CoverageSource)
                          .where(CoverageSource.id == source_id))).scalar_one_or_none()
    if not s or not s.url:
        raise HTTPException(status_code=404, detail="Source (or its URL) not found")
    fetch = ch.make_fetcher((s.settings or {}).get("proxy"))
    try:
        return await asyncio.to_thread(ch.probe_payload, s.url,
                                       settings=s.settings or {}, fetch=fetch)
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/coverage/sources/{source_id}/harvest")
async def start_coverage_harvest(source_id: UUID,
                                 staff: Staff = Depends(require_permission("inventory:write")),
                                 db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    from shared.models.coverage import CoverageSource
    from services.core.drug_catalog import coverage_harvest as ch
    s = (await db.execute(select(CoverageSource)
                          .where(CoverageSource.id == source_id))).scalar_one_or_none()
    if not s or not s.url:
        raise HTTPException(status_code=404, detail="Source (or its URL) not found")
    try:
        return ch.start_harvest(s.id, s.insurer)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/coverage/harvest/status")
async def coverage_harvest_status(staff: Staff = Depends(require_permission("inventory:read"))):
    from services.core.drug_catalog import coverage_harvest as ch
    return ch.status()


@router.get("/coverage/runs")
async def list_coverage_runs(source_id: UUID | None = None, limit: int = 20,
                             staff: Staff = Depends(require_permission("inventory:read")),
                             db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    from shared.models.coverage import CoverageRun
    q = select(CoverageRun).order_by(CoverageRun.started_at.desc()).limit(min(limit, 100))
    if source_id:
        q = q.where(CoverageRun.source_id == source_id)
    rows = (await db.execute(q)).scalars().all()
    return {"runs": [{"id": str(r.id), "source_id": str(r.source_id), "insurer": r.insurer,
                      "status": r.status,
                      "started_at": r.started_at.isoformat() if r.started_at else None,
                      "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                      "stats": r.stats, "diff_counts": {k: (r.diff or {}).get(k)
                                                        for k in ("added", "changed", "removed")},
                      "error": r.error} for r in rows]}


@router.get("/coverage/runs/{run_id}")
async def get_coverage_run(run_id: UUID,
                           staff: Staff = Depends(require_permission("inventory:read")),
                           db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    from shared.models.coverage import CoverageRun
    r = (await db.execute(select(CoverageRun)
                          .where(CoverageRun.id == run_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"id": str(r.id), "source_id": str(r.source_id), "insurer": r.insurer,
            "status": r.status, "stats": r.stats, "diff": r.diff,
            "review": r.review, "unmatched": r.unmatched, "error": r.error,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None}


@router.post("/coverage/runs/{run_id}/approve")
async def approve_coverage_run(run_id: UUID, body: ApproveRunRequest,
                               staff: Staff = Depends(require_permission("inventory:write")),
                               db: AsyncSession = Depends(get_db)):
    from services.core.drug_catalog import coverage_harvest as ch
    try:
        return await ch.apply_run(db, run_id, remove_missing=body.remove_missing,
                                  accepted_review_ids=body.accepted_review_ids,
                                  staff_id=staff.id)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/coverage/runs/{run_id}/reject")
async def reject_coverage_run(run_id: UUID,
                              staff: Staff = Depends(require_permission("inventory:write")),
                              db: AsyncSession = Depends(get_db)):
    from services.core.drug_catalog import coverage_harvest as ch
    try:
        await ch.reject_run(db, run_id)
        return {"rejected": True}
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
```

- [ ] **Step 8.2: Verify live against the running backend** (server on :8001 auto-reloads; restart it if not running):

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8001/api/v1/auth/login -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"PharmPilot2024!"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
curl -s http://127.0.0.1:8001/api/v1/pricing/coverage/sources -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | head -30
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8001/api/v1/pricing/coverage/harvest/status -H "Authorization: Bearer $TOKEN"
```

Expected: 3 seeded sources with `due: true`, `lock_holder: null`; status returns 200.

- [ ] **Step 8.3: Run the full backend test sweep**

Run: `python3 -m pytest tests/unit -q 2>&1 | tail -5`
Expected: no new failures vs the pre-task baseline.

- [ ] **Step 8.4: Commit**

```bash
git add services/platform/routers/pricing.py
git commit -m "feat(api): coverage source CRUD, probe, staged harvest runs, approve/reject"
```

---

### Task 9: GUI — CoverageAdmin section + card move

**Files:**
- Modify: `frontend/workstation/src/lib/api.ts`, `frontend/workstation/src/DashboardShell.tsx`, `frontend/workstation/src/dashboards/DrugCatalogAdmin.tsx`
- Create: `frontend/workstation/src/dashboards/CoverageAdmin.tsx`

- [ ] **Step 9.1: Extend `pricingApi` in `api.ts`** (inside the existing `pricingApi` object, after `nfiStop`):

```typescript
  // دارونامه coverage sources & staged runs
  coverageSources: () => apiClient.get('/pricing/coverage/sources'),
  coverageSourceSave: (id: string | null, body: Record<string, unknown>) =>
    id ? apiClient.put(`/pricing/coverage/sources/${id}`, body)
       : apiClient.post('/pricing/coverage/sources', body),
  coverageSourceDelete: (id: string) => apiClient.delete(`/pricing/coverage/sources/${id}`),
  coverageProbe: (id: string) => apiClient.post(`/pricing/coverage/sources/${id}/probe`, {}),
  coverageHarvest: (id: string) => apiClient.post(`/pricing/coverage/sources/${id}/harvest`, {}),
  coverageHarvestStatus: () => apiClient.get('/pricing/coverage/harvest/status'),
  coverageRuns: (sourceId?: string) =>
    apiClient.get('/pricing/coverage/runs', { params: sourceId ? { source_id: sourceId } : {} }),
  coverageRun: (id: string) => apiClient.get(`/pricing/coverage/runs/${id}`),
  coverageApprove: (id: string, body: { remove_missing: boolean; accepted_review_ids: number[] }) =>
    apiClient.post(`/pricing/coverage/runs/${id}/approve`, body),
  coverageReject: (id: string) => apiClient.post(`/pricing/coverage/runs/${id}/reject`, {}),
```

- [ ] **Step 9.2: Create `frontend/workstation/src/dashboards/CoverageAdmin.tsx`**

```tsx
/**
 * CoverageAdmin — دارونامه sources, harvest & review. Per-insurer source cards
 * (probe → detect format → save column overrides), background harvest behind the
 * global proxy lock, staged runs with a diff vs live coverage, and preview→اعمال.
 * The one-shot upload card (instant apply) also lives here, moved from DrugCatalogAdmin.
 */
import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { pricingApi } from '../lib/api'

const fa = (n: number | null | undefined) =>
  n == null ? '—' : new Intl.NumberFormat('fa-IR').format(n)

interface Source {
  id: string; insurer: string; name: string; url: string | null; strategy: string
  settings: Record<string, unknown>; check_interval_days: number; enabled: boolean
  last_run_at: string | null; last_run_status: string | null; due: boolean
  lock_holder: string | null
}
interface RunSummary {
  id: string; source_id: string; insurer: string; status: string
  started_at: string | null; finished_at: string | null
  stats: Record<string, number> | null
  diff_counts: { added: number | null; changed: number | null; removed: number | null }
  error: string | null
}

const STRATEGIES = [
  ['auto', 'تشخیص خودکار'], ['file_url', 'فایل مستقیم (Excel/CSV)'],
  ['html_table', 'جدول HTML'], ['paginated_html', 'HTML صفحه‌بندی‌شده'],
  ['json_api', 'JSON API'],
] as const
const ROLES = ['irc', 'gtin', 'drug_name', 'covered', 'share_pct',
               'reference_price', 'ceiling', 'inpatient', 'ignore'] as const

export default function CoverageAdmin() {
  const qc = useQueryClient()
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const [probe, setProbe] = useState<{ sourceId: string; data: any } | null>(null)
  const [openRun, setOpenRun] = useState<string | null>(null)

  const { data: sources } = useQuery<{ sources: Source[] }>({
    queryKey: ['coverage-sources'],
    queryFn: () => pricingApi.coverageSources().then(r => r.data),
    refetchInterval: 15_000,
  })
  const { data: hs } = useQuery<{ running: boolean; phase: string; pages: number;
                                  rows: number; insurer: string; error: string | null;
                                  run_id: string | null; lock_holder: string | null }>({
    queryKey: ['coverage-harvest-status'],
    queryFn: () => pricingApi.coverageHarvestStatus().then(r => r.data),
    refetchInterval: q => (q.state.data?.running ? 2_000 : 15_000),
  })
  const { data: runs } = useQuery<{ runs: RunSummary[] }>({
    queryKey: ['coverage-runs'],
    queryFn: () => pricingApi.coverageRuns().then(r => r.data),
    refetchInterval: hs?.running ? 5_000 : 30_000,
  })

  const err = (e: unknown, fallback: string) =>
    setMsg({ kind: 'err', text: (e as any)?.response?.data?.detail || fallback })

  const doProbe = async (s: Source) => {
    setMsg(null)
    try {
      const { data } = await pricingApi.coverageProbe(s.id)
      setProbe({ sourceId: s.id, data })
    } catch (e) { err(e, 'تشخیص ناموفق بود.') }
  }
  const doHarvest = async (s: Source) => {
    setMsg(null)
    try {
      await pricingApi.coverageHarvest(s.id)
      qc.invalidateQueries({ queryKey: ['coverage-harvest-status'] })
    } catch (e) { err(e, 'شروع برداشت ناموفق بود.') }
  }
  const saveSource = async (s: Source, patch: Record<string, unknown>) => {
    setMsg(null)
    try {
      await pricingApi.coverageSourceSave(s.id, {
        insurer: s.insurer, name: s.name, url: s.url, strategy: s.strategy,
        settings: s.settings, check_interval_days: s.check_interval_days,
        enabled: s.enabled, ...patch,
      })
      qc.invalidateQueries({ queryKey: ['coverage-sources'] })
      setMsg({ kind: 'ok', text: 'ذخیره شد.' })
    } catch (e) { err(e, 'ذخیره ناموفق بود.') }
  }

  const locked = !!(hs?.lock_holder || sources?.sources?.[0]?.lock_holder)

  return (
    <div className="p-4 space-y-4 text-slate-100" dir="rtl">
      <h2 className="text-lg font-bold">پوشش بیمه — دارونامه بیمه‌گرها</h2>
      {msg && <p className={`text-sm ${msg.kind === 'ok' ? 'text-emerald-400' : 'text-red-400'}`}>{msg.text}</p>}

      {/* live harvest strip */}
      {hs?.running && (
        <div className="bg-indigo-500/10 border border-indigo-500/40 rounded-lg p-3 text-[12px] font-mono flex flex-wrap gap-x-5">
          <span className="text-indigo-300">در حال برداشت: {hs.insurer}</span>
          <span>مرحله: {hs.phase}</span><span>صفحات: {fa(hs.pages)}</span>
          <span>ردیف‌ها: {fa(hs.rows)}</span>
          {hs.error && <span className="text-red-400">{hs.error}</span>}
        </div>
      )}

      {/* source cards */}
      <div className="grid md:grid-cols-2 gap-3">
        {(sources?.sources || []).map(s => (
          <SourceCard key={s.id} s={s} locked={locked} lockHolder={hs?.lock_holder ?? s.lock_holder}
                      onProbe={() => doProbe(s)} onHarvest={() => doHarvest(s)}
                      onSave={patch => saveSource(s, patch)} />
        ))}
      </div>

      {/* probe result */}
      {probe && (
        <ProbePanel data={probe.data} onClose={() => setProbe(null)}
          onSaveOverrides={(ov) => {
            const s = sources?.sources.find(x => x.id === probe.sourceId)
            if (s) saveSource(s, { settings: { ...s.settings, column_overrides: ov },
                                   strategy: probe.data.detected_strategy })
            setProbe(null)
          }} />
      )}

      {/* runs + preview */}
      <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 space-y-2">
        <p className="font-semibold text-sm">اجراهای برداشت</p>
        {(runs?.runs || []).length === 0 && <p className="text-[12px] text-slate-500">هنوز اجرایی ثبت نشده.</p>}
        {(runs?.runs || []).map(r => (
          <div key={r.id} className="flex flex-wrap items-center gap-3 text-[12px] border-b border-slate-700/60 pb-1.5">
            <span className="font-mono">{r.insurer}</span>
            <StatusChip status={r.status} />
            <span className="text-slate-500">{r.finished_at ? new Date(r.finished_at).toLocaleString('fa-IR') : '…'}</span>
            {r.stats && <span>ردیف: {fa(r.stats.rows)} · اعمال‌پذیر: {fa(r.stats.applied)} · بازبینی: {fa(r.stats.review)}</span>}
            <span className="text-slate-400">＋{fa(r.diff_counts.added)} / ✎{fa(r.diff_counts.changed)} / −{fa(r.diff_counts.removed)}</span>
            {r.error && <span className="text-red-400 truncate max-w-[24rem]">{r.error}</span>}
            {r.status === 'parsed' &&
              <button onClick={() => setOpenRun(openRun === r.id ? null : r.id)}
                className="px-2 py-0.5 bg-slate-700 hover:bg-slate-600 rounded">پیش‌نمایش</button>}
          </div>
        ))}
        {openRun && <RunPreview runId={openRun} onDone={() => { setOpenRun(null)
          qc.invalidateQueries({ queryKey: ['coverage-runs'] }) }} onError={err} />}
      </div>

      <UploadCard onMsg={setMsg} />
    </div>
  )
}

function StatusChip({ status }: { status: string }) {
  const tone: Record<string, string> = {
    parsed: 'bg-amber-500/15 text-amber-300', approved: 'bg-emerald-500/15 text-emerald-300',
    rejected: 'bg-slate-600/40 text-slate-400', failed: 'bg-red-500/15 text-red-300',
    running: 'bg-indigo-500/15 text-indigo-300',
  }
  return <span className={`text-[11px] px-2 py-0.5 rounded-full ${tone[status] || 'bg-slate-700'}`}>{status}</span>
}

function SourceCard({ s, locked, lockHolder, onProbe, onHarvest, onSave }: {
  s: Source; locked: boolean; lockHolder: string | null
  onProbe: () => void; onHarvest: () => void; onSave: (patch: Record<string, unknown>) => void
}) {
  const [url, setUrl] = useState(s.url || '')
  const [strategy, setStrategy] = useState(s.strategy)
  const [interval, setIntervalDays] = useState(s.check_interval_days)
  return (
    <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-3 space-y-2">
      <div className="flex items-center gap-2">
        <span className="font-semibold text-sm">{s.name}</span>
        {s.due && <span className="text-[11px] px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-300">به‌روزرسانی لازم</span>}
        {s.last_run_status && <StatusChip status={s.last_run_status} />}
      </div>
      <input value={url} onChange={e => setUrl(e.target.value)} dir="ltr" placeholder="https://…"
        className="w-full bg-slate-900 border border-slate-600 rounded px-2 py-1 text-[12px] font-mono" />
      <div className="flex flex-wrap items-center gap-2 text-[12px]">
        <select value={strategy} onChange={e => setStrategy(e.target.value)}
          className="bg-slate-900 border border-slate-600 rounded px-2 py-1">
          {STRATEGIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <label className="flex items-center gap-1 text-slate-400">هر
          <input type="number" value={interval} onChange={e => setIntervalDays(+e.target.value)}
            className="w-14 bg-slate-900 border border-slate-600 rounded px-1 py-0.5" /> روز</label>
        <button onClick={() => onSave({ url, strategy, check_interval_days: interval })}
          className="px-3 py-1 bg-slate-700 hover:bg-slate-600 rounded">ذخیره</button>
        <button onClick={onProbe} className="px-3 py-1 bg-indigo-600 hover:bg-indigo-500 rounded">تشخیص</button>
        <button onClick={onHarvest} disabled={locked}
          title={locked ? `قفل برداشت: ${lockHolder}` : undefined}
          className="px-3 py-1 bg-emerald-600 hover:bg-emerald-500 rounded disabled:opacity-40">برداشت</button>
      </div>
      {s.last_run_at && <p className="text-[11px] text-slate-500">
        آخرین اجرا: {new Date(s.last_run_at).toLocaleString('fa-IR')}</p>}
    </div>
  )
}

function ProbePanel({ data, onClose, onSaveOverrides }: {
  data: any; onClose: () => void; onSaveOverrides: (ov: Record<string, string>) => void
}) {
  const cols: string[] = data.sample_rows?.[0] ? Object.keys(data.sample_rows[0]) : []
  const [roles, setRoles] = useState<Record<string, string>>(
    () => ({ ...(data.inferred_columns || {}) }))
  return (
    <div className="bg-slate-800/70 border border-indigo-500/40 rounded-lg p-4 space-y-2">
      <div className="flex items-center gap-3">
        <p className="font-semibold text-sm">نتیجه تشخیص</p>
        <span className="text-[11px] px-2 py-0.5 rounded-full bg-indigo-500/15 text-indigo-300">{data.detected_strategy}</span>
        <button onClick={onClose} className="mr-auto text-slate-400 hover:text-slate-200">بستن ✕</button>
      </div>
      <div className="overflow-x-auto">
        <table className="text-[11px] font-mono">
          <thead><tr>{cols.map(c => (
            <th key={c} className="px-2 py-1 text-right border-b border-slate-600">
              <div className="text-slate-300">{c}</div>
              <select value={roles[c] || 'ignore'}
                onChange={e => setRoles({ ...roles, [c]: e.target.value })}
                className="bg-slate-900 border border-slate-600 rounded px-1 mt-0.5">
                {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
              </select>
            </th>))}</tr></thead>
          <tbody>{(data.sample_rows || []).slice(0, 8).map((row: any, i: number) => (
            <tr key={i}>{cols.map(c => <td key={c} className="px-2 py-0.5 text-slate-400 max-w-[12rem] truncate">{String(row[c] ?? '')}</td>)}</tr>
          ))}</tbody>
        </table>
      </div>
      <button onClick={() => onSaveOverrides(roles)}
        className="px-3 py-1 bg-emerald-600 hover:bg-emerald-500 rounded text-sm">ذخیره تنظیمات ستون‌ها</button>
    </div>
  )
}

function RunPreview({ runId, onDone, onError }: {
  runId: string; onDone: () => void; onError: (e: unknown, f: string) => void
}) {
  const [removeMissing, setRemoveMissing] = useState(false)
  const [accepted, setAccepted] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const { data: run } = useQuery<any>({
    queryKey: ['coverage-run', runId],
    queryFn: () => pricingApi.coverageRun(runId).then(r => r.data),
  })
  if (!run) return <p className="text-[12px] text-slate-500">در حال بارگذاری…</p>
  const decide = async (approve: boolean) => {
    setBusy(true)
    try {
      if (approve) await pricingApi.coverageApprove(runId,
        { remove_missing: removeMissing, accepted_review_ids: [...accepted] })
      else await pricingApi.coverageReject(runId)
      onDone()
    } catch (e) { onError(e, 'تصمیم اعمال نشد.') } finally { setBusy(false) }
  }
  const d = run.diff || { added: 0, changed: 0, removed: 0, samples: {} }
  return (
    <div className="border border-amber-500/40 rounded-lg p-3 space-y-2 text-[12px]">
      <div className="flex flex-wrap gap-4 font-mono">
        <span>ردیف‌ها: {fa(run.stats?.rows)}</span>
        <span className="text-emerald-300">اعمال‌پذیر: {fa(run.stats?.applied)}</span>
        <span className="text-amber-300">بازبینی: {fa(run.stats?.review)}</span>
        <span className="text-red-300">نامنطبق: {fa(run.stats?.unmatched)}</span>
        <span>＋جدید: {fa(d.added)} · ✎تغییر: {fa(d.changed)} · −حذف‌شده از فهرست: {fa(d.removed)}</span>
      </div>
      {(d.samples?.changed || []).length > 0 && (
        <div className="max-h-40 overflow-y-auto space-y-0.5">
          {(d.samples.changed).map((c: any) => (
            <div key={c.irc} className="font-mono text-slate-400">
              {c.irc}: {c.fields.map((f: any) => `${f.field} ${f.old ?? '—'}→${f.new ?? '—'}`).join(' · ')}
            </div>))}
        </div>)}
      {(run.review || []).length > 0 && (
        <div className="max-h-40 overflow-y-auto space-y-0.5">
          <p className="font-semibold">موارد نیازمند بازبینی — تأیید هر مورد آن را همراه اجرا اعمال می‌کند:</p>
          {run.review.map((item: any) => (
            <label key={item.id} className="flex items-center gap-2 font-mono text-slate-400">
              <input type="checkbox" checked={accepted.has(item.id)}
                onChange={e => { const s = new Set(accepted); e.target.checked ? s.add(item.id) : s.delete(item.id); setAccepted(s) }} />
              {item.name} ← «{String(item.row?.drug_name ?? '')}» (اطمینان {item.confidence})
            </label>))}
        </div>)}
      <label className="flex items-center gap-2 text-amber-300">
        <input type="checkbox" checked={removeMissing} onChange={e => setRemoveMissing(e.target.checked)} />
        حذف پوشش اقلامی که در فهرست جدید نیستند (حداکثر ۵۰ مورد نمونه‌گیری‌شده — با احتیاط)
      </label>
      <div className="flex gap-2">
        <button onClick={() => decide(true)} disabled={busy}
          className="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded disabled:opacity-50">اعمال</button>
        <button onClick={() => decide(false)} disabled={busy}
          className="px-4 py-1.5 bg-red-600 hover:bg-red-500 rounded disabled:opacity-50">رد</button>
      </div>
    </div>
  )
}

function UploadCard({ onMsg }: { onMsg: (m: { kind: 'ok' | 'err'; text: string }) => void }) {
  const qc = useQueryClient()
  const covRef = useRef<HTMLInputElement>(null)
  const [covInsurer, setCovInsurer] = useState('tamin')
  const [busy, setBusy] = useState(false)
  const importCoverage = async (file: File) => {
    setBusy(true)
    try {
      const { data } = await pricingApi.importCoverage(file, covInsurer)
      onMsg({ kind: 'ok', text: `پوشش بیمه برای ${fa(data.stats.products_updated)} قلم اعمال شد (${fa(data.stats.review)} بازبینی، ${fa(data.stats.unmatched)} نامنطبق).` })
      qc.invalidateQueries({ queryKey: ['coverage-runs'] })
    } catch (e: unknown) {
      onMsg({ kind: 'err', text: (e as any)?.response?.data?.detail || 'بارگذاری دارونامه ناموفق بود.' })
    } finally { setBusy(false); if (covRef.current) covRef.current.value = '' }
  }
  return (
    <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 space-y-2">
      <p className="font-semibold text-sm">بارگذاری دستی دارونامه (اعمال فوری)</p>
      <p className="text-[11px] text-slate-500">فایل Excel/CSV یا صفحه HTML ذخیره‌شده — موارد کم‌اطمینان اعمال نمی‌شوند.</p>
      <div className="flex items-center gap-3 text-sm">
        <select value={covInsurer} onChange={e => setCovInsurer(e.target.value)} disabled={busy}
          className="bg-slate-900 border border-slate-600 rounded px-2 py-1">
          <option value="tamin">تأمین اجتماعی</option>
          <option value="salamat">بیمه سلامت</option>
          <option value="armed_forces">نیروهای مسلح</option>
        </select>
        <input ref={covRef} type="file" accept=".xlsx,.xls,.csv,.tsv,.html,.htm" className="text-sm text-slate-300"
          onChange={e => { const f = e.target.files?.[0]; if (f) importCoverage(f) }} disabled={busy} />
      </div>
    </div>
  )
}
```

- [ ] **Step 9.3: Register the section in `DashboardShell.tsx`**

Add import (after the `DrugCatalogAdmin` import):

```tsx
import CoverageAdmin          from './dashboards/CoverageAdmin'
```

Append to `SECTIONS` (after the `drug-catalog` entry):

```tsx
  { id:'coverage', key:'b', label:'Insurance Coverage', icon:ShieldCheck, description:'دارونامه sources, harvest & review', shortcut:'Alt+B' },
```

Append to `SECTION_COMPONENTS`:

```tsx
  coverage: CoverageAdmin,
```

- [ ] **Step 9.4: Remove the coverage card from `DrugCatalogAdmin.tsx`**

Delete: the `covRef`, `covInsurer`, `covResult` state lines; the `importCoverage` function; the entire `{/* ── Insurance coverage import … ── */}` JSX block (the `بارگذاری دارونامه بیمه (تعهدات)` card and its `covResult` rendering).

- [ ] **Step 9.5: Verify in the browser (preview tools)**

1. Ensure dev server (`preview_start` name `workstation`) and backend :8001 are up.
2. Log in (admin / PharmPilot2024!) → 📊 Dashboards → **Insurance Coverage** (Alt+B).
3. Confirm via `preview_snapshot`: 3 seeded source cards render, each with a "به‌روزرسانی لازم" badge and تشخیص/برداشت buttons; the manual-upload card is present; Drug Catalog section no longer shows the coverage card.
4. Click برداشت on a seed source — expect a red message (unreachable without proxy) rather than a crash; `coverage_runs` list stays sane.
5. `preview_console_logs` (level=error): no new errors. `preview_screenshot` for the record.
6. `cd frontend/workstation && npx tsc -b 2>&1 | tail -20` — no NEW errors vs baseline (known baseline: DashboardShell/useAIProvider/MedReconciliationPage).

- [ ] **Step 9.6: Commit**

```bash
git add frontend/workstation/src/lib/api.ts frontend/workstation/src/DashboardShell.tsx frontend/workstation/src/dashboards/CoverageAdmin.tsx frontend/workstation/src/dashboards/DrugCatalogAdmin.tsx
git commit -m "feat(gui): Insurance Coverage section — sources, probe, staged runs, diff review"
```

---

### Task 10: End-to-end staging rehearsal + full verification

**Files:** none new — exercises the whole pipeline with a local fixture served over HTTP.

- [ ] **Step 10.1: Serve a synthetic دارونامه locally and run a REAL harvest through the API**

```bash
mkdir -p /tmp/darunameh && cat > /tmp/darunameh/tamin.csv <<'EOF'
کد فرآورده,نام دارو,درصد تعهد,قیمت مورد تعهد,تعهد بیمه
2939581622742553,استامینوفن کدئین,70,45000,بله
,الندرونیک اسید 10 mg قرص,90,120000,بله
EOF
(cd /tmp/darunameh && python3 -m http.server 8099 &) && sleep 1
TOKEN=$(curl -s -X POST http://127.0.0.1:8001/api/v1/auth/login -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"PharmPilot2024!"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
SRC=$(curl -s http://127.0.0.1:8001/api/v1/pricing/coverage/sources -H "Authorization: Bearer $TOKEN" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['sources'][0]['id'])")
# point the tamin seed at the local file, probe, then harvest
curl -s -X PUT http://127.0.0.1:8001/api/v1/pricing/coverage/sources/$SRC \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"insurer":"tamin","name":"دارونامه تأمین اجتماعی","url":"http://127.0.0.1:8099/tamin.csv","strategy":"auto","settings":{},"check_interval_days":7,"enabled":true}' > /dev/null
curl -s -X POST http://127.0.0.1:8001/api/v1/pricing/coverage/sources/$SRC/probe -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | head -20
curl -s -X POST http://127.0.0.1:8001/api/v1/pricing/coverage/sources/$SRC/harvest -H "Authorization: Bearer $TOKEN"
sleep 5 && curl -s http://127.0.0.1:8001/api/v1/pricing/coverage/runs -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | head -30
```

Expected: probe detects `file_url` with correct column roles (`درصد تعهد → share_pct` — the bug fixed in cfaf3c7); harvest produces a `parsed` run with stats and a diff.

- [ ] **Step 10.2: Approve the run via API and verify coverage landed**

```bash
RUN=$(curl -s http://127.0.0.1:8001/api/v1/pricing/coverage/runs -H "Authorization: Bearer $TOKEN" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['runs'][0]['id'])")
curl -s -X POST http://127.0.0.1:8001/api/v1/pricing/coverage/runs/$RUN/approve \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"remove_missing": false, "accepted_review_ids": []}' | python3 -m json.tool
PGPASSWORD=pharmpilot_dev psql -h 127.0.0.1 -p 5433 -U pharmpilot -d pharmpilot -t \
  -c "SELECT coverage->'tamin' FROM drug_catalog WHERE irc='2939581622742553';"
# state machine: approving an already-approved run must 409
curl -s -o /dev/null -w "re-approve -> %{http_code}\n" -X POST \
  http://127.0.0.1:8001/api/v1/pricing/coverage/runs/$RUN/approve \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"remove_missing": false, "accepted_review_ids": []}'
kill %1  # stop the http.server
```

Expected: `products_updated` ≥ 1; the psql query prints an entry with `"share_pct": 70` — and the whole ingredient group got it (spread); `re-approve -> 409`.

- [ ] **Step 10.3: Full suite + lock cross-check + graph refresh**

```bash
python3 -m pytest tests/unit -q 2>&1 | tail -3
graphify update .
```

Expected: no new failures vs pre-plan baseline; graph rebuilt.

- [ ] **Step 10.4: Commit anything outstanding, update the roadmap**

In `docs/ROADMAP.md` Phase 1, change the دارونامه line to:

```markdown
- 🟡 Load per-insurer دارونامه (تأمین / سلامت / نیروهای مسلح) — crawler + staged-review GUI built; needs real source URLs behind the Iran proxy
```

```bash
git add docs/ROADMAP.md
git commit -m "docs(roadmap): دارونامه crawler + coverage GUI built; awaiting real sources"
```

---

## Post-plan (next proxy session — operational, not code)

1. Turn on the Iran system proxy.
2. NFI panel → re-crawl ids 1–60000 (backfills country/monograph for all 39k + finishes the range). Ingest is idempotent.
3. Coverage panel → probe each insurer seed URL, correct it to the real دارونامه page, save overrides, harvest, review diff, اعمال.
4. Receipt-level validation of a real Rx (roadmap Phase 1 exit).
