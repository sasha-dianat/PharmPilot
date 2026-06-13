"""
Trainable Reference Corpus — PART 2 / §1.5 of the Offline-First Doctrine
========================================================================
A pharmacist-growable knowledge base used by several services (label dosing
references, compounding compatibility charts, prescriber registry exports,
trajectory guideline snippets, DUR rationale phrasing).

Design:
  • Each reference document is chunked, embedded LOCALLY (PubMedBERT via
    local_llm.embed_batch) and stored so retrieval works fully offline.
  • Primary store = the existing knowledge_engine vector store (Qdrant) when it is
    reachable; this gives shared, scalable retrieval when the platform is online.
  • OFFLINE-SAFE FALLBACK = a local SQLite table at ~/.pharmpilot/reference_corpus/
    holding chunk text + embedding blobs; retrieval is brute-force numpy cosine.
    This guarantees RAG keeps working with Qdrant down / no internet.

Retrieval returns the top-k chunks; a caller (service) then either:
  • OFFLINE: uses the retrieved chunks verbatim / lightly templated, or
  • ONLINE : passes them to the cloud LLM for fluent synthesis.

Public surface:
  ReferenceCorpus(namespace)
    .ingest(doc_id, text, metadata)   -> chunk_count
    .retrieve(query, k, namespace)    -> list[RetrievedRef]
    .delete(doc_id)                   -> int
    .stats()                          -> dict
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import local_llm

logger = logging.getLogger(__name__)

CORPUS_DIR = Path(os.environ.get(
    "PHARMPILOT_REFERENCE_DIR",
    str(Path.home() / ".pharmpilot" / "reference_corpus"),
))
CORPUS_DIR.mkdir(parents=True, exist_ok=True)
_DB_PATH = CORPUS_DIR / "corpus.db"


@dataclass
class RetrievedRef:
    doc_id:     str
    namespace:  str
    chunk:      str
    score:      float
    metadata:   dict = field(default_factory=dict)


# ─── Chunking ─────────────────────────────────────────────────────────────────

def _chunk_text(text: str, target_words: int = 120, overlap: int = 25) -> list[str]:
    """Simple word-window chunker with overlap. Good enough for reference tables."""
    words = text.split()
    if len(words) <= target_words:
        return [text.strip()] if text.strip() else []
    chunks, i = [], 0
    while i < len(words):
        chunk = " ".join(words[i:i + target_words])
        if chunk.strip():
            chunks.append(chunk.strip())
        i += target_words - overlap
    return chunks


# ─── Embedding (de)serialization for SQLite blob storage ─────────────────────

def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


# ─── Local SQLite store (the offline fallback) ───────────────────────────────

class _LocalStore:
    def __init__(self):
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(_DB_PATH))
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS ref_chunks (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    namespace  TEXT NOT NULL,
                    doc_id     TEXT NOT NULL,
                    chunk      TEXT NOT NULL,
                    embedding  BLOB NOT NULL,
                    metadata   TEXT,
                    created_at TEXT
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_ref_ns ON ref_chunks(namespace)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_ref_doc ON ref_chunks(doc_id)")

    def add(self, namespace: str, doc_id: str, chunks: list[str],
            embeddings: list[list[float]], metadata: dict) -> int:
        now = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(metadata)
        with self._conn() as c:
            for chunk, emb in zip(chunks, embeddings):
                c.execute(
                    "INSERT INTO ref_chunks (namespace, doc_id, chunk, embedding, metadata, created_at)"
                    " VALUES (?,?,?,?,?,?)",
                    (namespace, doc_id, chunk, _pack(emb), meta_json, now),
                )
        return len(chunks)

    def search(self, namespace: str, query_vec: list[float], k: int) -> list[RetrievedRef]:
        rows = []
        with self._conn() as c:
            cur = c.execute(
                "SELECT doc_id, chunk, embedding, metadata FROM ref_chunks WHERE namespace = ?",
                (namespace,),
            )
            rows = cur.fetchall()
        scored = []
        for doc_id, chunk, emb_blob, meta_json in rows:
            score = local_llm.cosine(query_vec, _unpack(emb_blob))
            scored.append(RetrievedRef(
                doc_id=doc_id, namespace=namespace, chunk=chunk, score=score,
                metadata=json.loads(meta_json) if meta_json else {},
            ))
        scored.sort(key=lambda r: -r.score)
        return scored[:k]

    def delete(self, doc_id: str) -> int:
        with self._conn() as c:
            cur = c.execute("DELETE FROM ref_chunks WHERE doc_id = ?", (doc_id,))
            return cur.rowcount

    def stats(self, namespace: Optional[str] = None) -> dict:
        with self._conn() as c:
            if namespace:
                n = c.execute("SELECT COUNT(*) FROM ref_chunks WHERE namespace=?",
                              (namespace,)).fetchone()[0]
                d = c.execute("SELECT COUNT(DISTINCT doc_id) FROM ref_chunks WHERE namespace=?",
                              (namespace,)).fetchone()[0]
            else:
                n = c.execute("SELECT COUNT(*) FROM ref_chunks").fetchone()[0]
                d = c.execute("SELECT COUNT(DISTINCT doc_id) FROM ref_chunks").fetchone()[0]
        return {"chunks": n, "documents": d}


_local_store: Optional[_LocalStore] = None


def _get_local_store() -> _LocalStore:
    global _local_store
    if _local_store is None:
        _local_store = _LocalStore()
    return _local_store


# ─── Public API ───────────────────────────────────────────────────────────────

class ReferenceCorpus:
    """
    A namespaced trainable reference store. `namespace` separates corpora per
    service, e.g. "label_dosing", "compounding_compat", "prescriber_registry".

    Embeddings are always computed locally, so ingest + retrieve work offline.
    The local SQLite store is always written (durable offline copy); when the
    Qdrant vector store is reachable we ALSO mirror there for shared retrieval.
    """

    def __init__(self, namespace: str):
        self.namespace = namespace

    # ── ingest ──────────────────────────────────────────────────────────────

    def ingest(self, doc_id: str, text: str, metadata: Optional[dict] = None) -> int:
        metadata = metadata or {}
        chunks = _chunk_text(text)
        if not chunks:
            return 0

        embeddings = local_llm.embed_batch(chunks)

        # Always persist locally (offline durability).
        count = _get_local_store().add(self.namespace, doc_id, chunks, embeddings, metadata)

        # Best-effort mirror into the shared Qdrant store when reachable.
        self._mirror_to_qdrant(doc_id, chunks, embeddings, metadata)

        logger.info("[reference_corpus:%s] ingested %s (%d chunks)",
                    self.namespace, doc_id, count)
        return count

    def _mirror_to_qdrant(self, doc_id, chunks, embeddings, metadata) -> None:
        try:
            from services.ai.knowledge_engine.vector_store import ClinicalVectorStore
            store = ClinicalVectorStore(collection=f"ref_{self.namespace}")
            store.ensure_collection()
            for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                store.upsert_chunk(
                    chunk_id=f"{doc_id}::{i}",
                    content=chunk,
                    embedding=emb,
                    metadata={"doc_id": doc_id, "namespace": self.namespace, **metadata},
                )
        except Exception as exc:  # pragma: no cover — offline / no qdrant is expected
            logger.debug("[reference_corpus:%s] qdrant mirror skipped (%s)", self.namespace, exc)

    # ── retrieve ─────────────────────────────────────────────────────────────

    def retrieve(self, query: str, k: int = 5) -> list[RetrievedRef]:
        """
        Top-k reference chunks for a query. Tries shared Qdrant first when online,
        always falls back to the local brute-force store so this never fails offline.
        """
        query_vec = local_llm.embed(query)

        # Try Qdrant (shared, fast) — but only if it answers quickly.
        hits = self._retrieve_qdrant(query, k)
        if hits:
            return hits

        # Offline / Qdrant down → local numpy cosine.
        return _get_local_store().search(self.namespace, query_vec, k)

    def _retrieve_qdrant(self, query: str, k: int) -> list[RetrievedRef]:
        try:
            from services.ai.knowledge_engine.vector_store import ClinicalVectorStore
            store = ClinicalVectorStore(collection=f"ref_{self.namespace}")
            results = store.search(query, top_k=k)
            out = []
            for r in results:
                # RetrievedChunk shape varies; access defensively.
                content = getattr(r, "content", None) or getattr(r, "text", "")
                meta    = getattr(r, "metadata", {}) or {}
                score   = float(getattr(r, "score", 0.0))
                out.append(RetrievedRef(
                    doc_id=meta.get("doc_id", ""), namespace=self.namespace,
                    chunk=content, score=score, metadata=meta,
                ))
            return out
        except Exception as exc:  # pragma: no cover
            logger.debug("[reference_corpus:%s] qdrant retrieve skipped (%s)", self.namespace, exc)
            return []

    # ── management ───────────────────────────────────────────────────────────

    def delete(self, doc_id: str) -> int:
        removed = _get_local_store().delete(doc_id)
        try:
            from services.ai.knowledge_engine.vector_store import ClinicalVectorStore
            ClinicalVectorStore(collection=f"ref_{self.namespace}").delete_source(doc_id)
        except Exception:  # pragma: no cover
            pass
        return removed

    def stats(self) -> dict:
        return {"namespace": self.namespace, **_get_local_store().stats(self.namespace)}
