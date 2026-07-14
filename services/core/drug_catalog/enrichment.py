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
                     "dosage_form", "strengths", "confidence", "sources", "notes")

# Fields persisted in the committed canonical JSON artifact (id/status/timestamps
# are environment-specific and intentionally excluded — all exported rows are approved).
_REFERENCE_FIELDS = ("key", "raw_name", "irc", "generic_name", "brand_name",
                     "manufacturer", "country", "dosage_form", "strengths",
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
        else:
            v = str(v).strip()
        clean[f] = v
    return clean, errors


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
            "irc": r.irc,
        }
        for r in rows if r.key
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
    return [{
        "id": str(r.id), "key": r.key, "raw_name": r.raw_name, "irc": r.irc,
        "generic_name": r.generic_name, "brand_name": r.brand_name,
        "manufacturer": r.manufacturer, "country": r.country,
        "dosage_form": r.dosage_form, "strengths": r.strengths, "notes": r.notes,
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

    def add(raw_name, reason, insurer):
        if not raw_name:
            return
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

    return list(items.values())
