"""
Config-driven clinical-knowledge ingestion (FDA Structured Product Labels).

Pulls per-drug SPL sections (drug/food interactions, contraindications, warnings
& precautions, pharmacokinetics — fully customizable via config/kb_ingest.json)
from openFDA/DailyMed, chunks them, tags each chunk by clinical DOMAIN, embeds
with the same model the RAG query path uses (neuml/PubMedBERT, 768-dim) and
upserts into the Qdrant collection the Second Brain reads.

Designed to be run by the owner with NO code changes — edit the JSON config and
run `python scripts/ingest_spl.py`. Idempotent: chunk IDs are deterministic
(uuid5 of drug+domain+index), so re-running refreshes rather than duplicates.

All sources are public-domain U.S. government works (FDA SPLs) — no licensing
restriction, unlike commercial drug-info databases.
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("kb.spl_ingest")

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = _REPO_ROOT / "config" / "kb_ingest.json"
_ID_NAMESPACE = uuid.UUID("a7c1f0de-1234-4abc-9def-0123456789ab")  # stable → deterministic point IDs


# ── Config ───────────────────────────────────────────────────────────────────

def load_config(path: str | os.PathLike | None = None) -> dict:
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    with open(p, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    cfg.setdefault("qdrant_url", "http://localhost:6334")
    cfg.setdefault("collection", "pharmpilot_clinical_knowledge")
    cfg.setdefault("embedding_model", "neuml/pubmedbert-base-embeddings")
    cfg.setdefault("embedding_dim", 768)
    cfg.setdefault("openfda_label_url", "https://api.fda.gov/drug/label.json")
    cfg.setdefault("request_timeout_s", 20)
    cfg.setdefault("chunk", {"size": 1200, "overlap": 200})
    cfg.setdefault("sections", [])
    cfg.setdefault("scope", {"mode": "top", "drugs": [], "limit": None})
    cfg.setdefault("top_drugs", [])
    cfg.setdefault("include_curated_ddi", False)
    return cfg


# ── Scope resolution ─────────────────────────────────────────────────────────

def resolve_scope(cfg: dict, db_url: str | None = None) -> list[str]:
    """Resolve the list of drug generic-names to ingest, per cfg['scope'].

    modes: 'top' → cfg['top_drugs']; 'list' → cfg['scope']['drugs'];
           'formulary' → distinct dispensed/stocked drugs from the DB.
    """
    scope = cfg.get("scope", {})
    mode = (scope.get("mode") or "top").lower()
    limit = scope.get("limit")

    if mode == "list":
        drugs = list(scope.get("drugs") or [])
    elif mode == "formulary":
        drugs = _formulary_from_db(db_url or os.environ.get("DATABASE_URL", ""))
    else:  # top
        drugs = list(cfg.get("top_drugs") or [])

    # de-dupe (case-insensitive) preserving order
    seen, out = set(), []
    for d in drugs:
        k = d.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(d.strip())
    return out[:limit] if limit else out


def _formulary_from_db(db_url: str) -> list[str]:
    """Distinct generic names for drugs the pharmacy stocks or has dispensed."""
    if not db_url:
        log.warning("scope=formulary but no DATABASE_URL — empty scope")
        return []
    import psycopg2  # optional dep; only needed for formulary mode
    sync = db_url.replace("+asyncpg", "")
    names: list[str] = []
    with psycopg2.connect(sync) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT lower(dp.generic_name)
            FROM drug_products dp
            WHERE dp.ndc11 IN (
                SELECT ndc11 FROM stock_levels WHERE quantity_on_hand > 0
                UNION SELECT ndc FROM prescriptions
            ) AND dp.generic_name IS NOT NULL
            """
        )
        names = [r[0] for r in cur.fetchall()]
    log.info("formulary scope: %d distinct drugs from DB", len(names))
    return names


# ── Fetch + chunk ────────────────────────────────────────────────────────────

def _clean(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html or "")).strip()


def extract_sections_from_label(label: dict, cfg: dict) -> list[dict]:
    """Pull the configured clinical sections out of one openFDA label record.

    Works on any openFDA drug/label result — whether fetched live from the API
    or read from the openFDA bulk-download JSON (same schema). Returns
    [{domain, header, text}].
    """
    out_sections = []
    for sec in cfg["sections"]:
        parts = []
        for field in sec["fields"]:
            vals = label.get(field) or []
            if isinstance(vals, list):
                for v in vals:
                    c = _clean(v)
                    if len(c) > 50:
                        parts.append(c)
        if parts:
            out_sections.append({
                "domain": sec["domain"],
                "header": sec.get("header", sec["domain"].upper()),
                "text": "\n".join(parts),
            })
    return out_sections


def generic_of(label: dict) -> Optional[str]:
    """Canonical dedup key for a label: its first generic (active) name, lowered."""
    openfda = label.get("openfda", {}) or {}
    for key in ("generic_name", "substance_name"):
        vals = openfda.get(key) or []
        if vals:
            return str(vals[0]).strip().lower()
    return None


def brand_of(label: dict, fallback: str = "") -> str:
    openfda = label.get("openfda", {}) or {}
    return (openfda.get("brand_name") or [fallback])[0] or fallback


def fetch_label_sections(client, drug: str, cfg: dict) -> Optional[dict]:
    """Return {'drug','brand','sections':[{domain,header,text}]} or None (API mode)."""
    params = {"search": f'openfda.generic_name:"{drug}"', "limit": 1}
    try:
        resp = client.get(cfg["openfda_label_url"], params=params,
                          timeout=cfg["request_timeout_s"])
        if resp.status_code != 200:
            return None
        results = resp.json().get("results", [])
        if not results:
            return None
    except Exception as e:  # noqa: BLE001
        log.debug("fetch failed for %s: %s", drug, e)
        return None

    label = results[0]
    out_sections = extract_sections_from_label(label, cfg)
    if not out_sections:
        return None
    return {"drug": drug, "brand": brand_of(label, drug), "sections": out_sections}


def chunk_text(text: str, size: int = 1200, overlap: int = 200) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks, cur, cur_len = [], [], 0
    for sent in sentences:
        if cur_len + len(sent) > size and cur:
            ch = " ".join(cur).strip()
            if len(ch) > 80:
                chunks.append(ch)
            tail = ch[-overlap:]
            cur, cur_len = [tail], len(tail)
        cur.append(sent)
        cur_len += len(sent)
    if cur:
        ch = " ".join(cur).strip()
        if len(ch) > 80:
            chunks.append(ch)
    return chunks


# ── Ingest ───────────────────────────────────────────────────────────────────

def _point_id(drug: str, domain: str, idx: int) -> str:
    return str(uuid.uuid5(_ID_NAMESPACE, f"spl:{drug.lower()}:{domain}:{idx}"))


def ingest(cfg: dict, *, db_url: str | None = None,
           progress: Callable[[str], None] | None = None) -> dict:
    """Run the full ingest. Returns a summary dict."""
    import httpx
    from sentence_transformers import SentenceTransformer
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue

    def say(m: str) -> None:
        log.info(m)
        if progress:
            progress(m)

    drugs = resolve_scope(cfg, db_url=db_url)
    say(f"scope resolved: {len(drugs)} drugs ({cfg['scope'].get('mode')})")
    if not drugs:
        return {"drugs": 0, "vectors": 0, "skipped": [], "note": "empty scope"}

    say(f"loading embedder {cfg['embedding_model']} …")
    embedder = SentenceTransformer(cfg["embedding_model"])

    client_q = QdrantClient(url=cfg["qdrant_url"])
    coll = cfg["collection"]
    existing = [c.name for c in client_q.get_collections().collections]
    if coll not in existing:
        client_q.create_collection(
            collection_name=coll,
            vectors_config=VectorParams(size=cfg["embedding_dim"], distance=Distance.COSINE),
        )
        say(f"created collection {coll}")

    size = cfg["chunk"]["size"]
    overlap = cfg["chunk"]["overlap"]
    total_vec, done, skipped = 0, 0, []

    with httpx.Client() as http:
        for n, drug in enumerate(drugs, 1):
            label = fetch_label_sections(http, drug, cfg)
            if not label:
                skipped.append(drug)
                say(f"[{n}/{len(drugs)}] {drug}: no SPL found — skipped")
                continue
            # idempotent: clear prior chunks for this drug before re-insert
            try:
                client_q.delete(collection_name=coll, points_selector=Filter(
                    must=[FieldCondition(key="drug", match=MatchValue(value=drug.lower())),
                          FieldCondition(key="source_type", match=MatchValue(value="fda_spl"))]))
            except Exception:  # noqa: BLE001
                pass

            drug_vec = 0
            for sec in label["sections"]:
                chunks = chunk_text(sec["text"], size, overlap)
                for i in range(0, len(chunks), 16):
                    batch = chunks[i:i + 16]
                    vecs = embedder.encode(batch, normalize_embeddings=True, show_progress_bar=False)
                    points = []
                    for j, (ch, v) in enumerate(zip(batch, vecs)):
                        idx = i + j
                        points.append(PointStruct(
                            id=_point_id(drug, sec["domain"], idx),
                            vector=v.tolist(),
                            payload={
                                "content": ch,
                                "drug": drug.lower(),
                                "domain": sec["domain"],
                                "source_title": f"{label['brand']} — {sec['header']}",
                                "source_id": f"spl_{drug.lower()}_{sec['domain']}",
                                "source_type": "fda_spl",
                                "url": "https://dailymed.nlm.nih.gov/",
                                "chunk_index": idx,
                            }))
                    client_q.upsert(collection_name=coll, points=points)
                    drug_vec += len(points)
            total_vec += drug_vec
            done += 1
            say(f"[{n}/{len(drugs)}] {drug} ({label['brand']}): {drug_vec} chunks → {len(label['sections'])} sections")

    if cfg.get("include_curated_ddi"):
        try:
            ddi_vec = _ingest_curated_ddi(cfg, embedder, client_q, say)
            total_vec += ddi_vec
        except Exception as e:  # noqa: BLE001
            say(f"curated DDI skipped: {e}")

    summary = {"drugs_ingested": done, "vectors": total_vec,
               "skipped": skipped, "collection": coll}
    say(f"DONE: {done} drugs, {total_vec} vectors, {len(skipped)} skipped")
    return summary


# ── Bulk ingest (openFDA / DailyMed bulk download) ─────────────────────────────

def _iter_bulk_records(bulk_dir: str):
    """Yield openFDA label records from a dir of bulk files (.json or .json.zip).

    The openFDA 'drug label' bulk download is the same data DailyMed publishes,
    pre-parsed into JSON ({"results": [...]}) — so no HL7 XML parsing is needed.
    Download from https://api.fda.gov/download.json (drug → label partitions).
    """
    import glob
    import json as _json
    import zipfile

    files = sorted(glob.glob(os.path.join(bulk_dir, "*.json")) +
                   glob.glob(os.path.join(bulk_dir, "*.json.zip")) +
                   glob.glob(os.path.join(bulk_dir, "*.zip")))
    for fp in files:
        if fp.endswith(".zip"):
            with zipfile.ZipFile(fp) as zf:
                for inner in zf.namelist():
                    if inner.endswith(".json"):
                        with zf.open(inner) as fh:
                            data = _json.loads(fh.read().decode("utf-8", "replace"))
                            yield from (data.get("results") or [])
        else:
            with open(fp, "r", encoding="utf-8") as fh:
                data = _json.load(fh)
                yield from (data.get("results") or [])


def ingest_bulk_dir(cfg: dict, bulk_dir: str, *, limit: int | None = None,
                    only_generics: set[str] | None = None,
                    product_types: set[str] | None = None,
                    progress: Callable[[str], None] | None = None) -> dict:
    """Ingest the openFDA/DailyMed bulk dataset, deduped by generic name.

    Keeps ONE representative label per distinct generic (the catalog is full of
    repackaged duplicates). `only_generics` optionally restricts to a formulary;
    `product_types` (default from cfg) keeps only clinically-relevant labels
    (e.g. HUMAN PRESCRIPTION DRUG) — filtering out sunscreens/homeopathics.
    Deterministic point IDs keyed by generic → idempotent + resumable.
    """
    if product_types is None and cfg.get("product_types"):
        product_types = {p.upper() for p in cfg["product_types"]}
    from sentence_transformers import SentenceTransformer
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams, PointStruct

    def say(m: str) -> None:
        log.info(m)
        if progress:
            progress(m)

    say(f"loading embedder {cfg['embedding_model']} …")
    embedder = SentenceTransformer(cfg["embedding_model"])
    client_q = QdrantClient(url=cfg["qdrant_url"])
    coll = cfg["collection"]
    if coll not in [c.name for c in client_q.get_collections().collections]:
        client_q.create_collection(
            collection_name=coll,
            vectors_config=VectorParams(size=cfg["embedding_dim"], distance=Distance.COSINE))
        say(f"created collection {coll}")

    size, overlap = cfg["chunk"]["size"], cfg["chunk"]["overlap"]
    seen: set[str] = set()
    total_vec = done = scanned = 0

    for label in _iter_bulk_records(bulk_dir):
        scanned += 1
        if product_types is not None:
            pt = ((label.get("openfda", {}) or {}).get("product_type") or [""])[0]
            if (pt or "").upper() not in product_types:
                continue
        gen = generic_of(label)
        if not gen or gen in seen:
            continue
        if only_generics is not None and gen not in only_generics:
            continue
        sections = extract_sections_from_label(label, cfg)
        if not sections:
            continue
        seen.add(gen)
        brand = brand_of(label, gen)
        drug_vec = 0
        for sec in sections:
            chunks = chunk_text(sec["text"], size, overlap)
            for i in range(0, len(chunks), 32):
                batch = chunks[i:i + 32]
                vecs = embedder.encode(batch, normalize_embeddings=True, show_progress_bar=False)
                pts = [PointStruct(
                    id=_point_id(gen, sec["domain"], i + j),
                    vector=v.tolist(),
                    payload={"content": ch, "drug": gen, "domain": sec["domain"],
                             "source_title": f"{brand} — {sec['header']}",
                             "source_id": f"spl_{gen}_{sec['domain']}",
                             "source_type": "fda_spl",
                             "url": "https://dailymed.nlm.nih.gov/", "chunk_index": i + j})
                    for j, (ch, v) in enumerate(zip(batch, vecs))]
                client_q.upsert(collection_name=coll, points=pts)
                drug_vec += len(pts)
        total_vec += drug_vec
        done += 1
        if done % 50 == 0:
            say(f"{done} drugs ingested ({total_vec} vectors), {scanned} records scanned…")
        if limit and done >= limit:
            break

    say(f"BULK DONE: {done} distinct drugs, {total_vec} vectors ({scanned} records scanned)")
    return {"drugs_ingested": done, "vectors": total_vec, "records_scanned": scanned, "collection": coll}


def _ingest_curated_ddi(cfg, embedder, client_q, say) -> int:
    """Best-effort: embed the project's curated high-severity DDI pairs so they
    are searchable in the RAG alongside SPL narrative."""
    import importlib.util
    from qdrant_client.models import PointStruct

    spec = importlib.util.spec_from_file_location(
        "seed_ddi", str(_REPO_ROOT / "scripts" / "seed_drug_interactions.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    pairs = getattr(mod, "CRITICAL_INTERACTIONS", [])
    if not pairs:
        return 0
    docs, ids = [], []
    for k, row in enumerate(pairs):
        a, b, sev, mech, desc = row[0], row[1], row[2], row[3], row[4]
        docs.append(f"DRUG INTERACTION: {a} + {b} — severity {sev}. "
                    f"Mechanism: {mech}. {desc}")
        ids.append(str(uuid.uuid5(_ID_NAMESPACE, f"ddi:{a}:{b}".lower())))
    vecs = embedder.encode(docs, normalize_embeddings=True, show_progress_bar=False)
    points = [PointStruct(id=ids[i], vector=vecs[i].tolist(), payload={
        "content": docs[i], "domain": "interaction",
        "source_title": "Curated high-severity drug interaction",
        "source_id": f"ddi_{i}", "source_type": "curated_ddi",
        "url": "", "chunk_index": i,
    }) for i in range(len(docs))]
    client_q.upsert(collection_name=cfg["collection"], points=points)
    say(f"curated DDI: {len(points)} pairs embedded")
    return len(points)
