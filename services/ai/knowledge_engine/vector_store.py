"""
Qdrant vector store client for the clinical knowledge base.
Manages embedding storage, similarity search, and filtered retrieval.
Uses PubMedBERT embeddings for medical-domain accuracy.
"""
import logging
import uuid
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

QDRANT_COLLECTION = "pharmpilot_clinical_knowledge"
EMBEDDING_DIM = 768          # PubMedBERT / BioBERT dimension
MEDICAL_EMBEDDING_MODEL = "pritamdeka/PubMedBERT-mnli-snli-scinli-scitail-mednli-stsb"
# Alternative: "neuml/pubmedbert-base-embeddings" (lighter, faster)


@dataclass
class RetrievedChunk:
    chunk_id: str
    source_id: str
    content: str
    section_title: Optional[str]
    source_title: str
    source_type: str
    url: Optional[str]
    doi: Optional[str]
    evidence_grade: Optional[str]
    similarity_score: float
    drugs_mentioned: list[str] = field(default_factory=list)
    specialty_tags: list[str] = field(default_factory=list)


class ClinicalVectorStore:
    """
    Qdrant-backed vector store optimised for clinical knowledge retrieval.
    Uses PubMedBERT embeddings — significantly outperforms general-purpose
    embeddings on biomedical text similarity tasks.
    """

    def __init__(
        self,
        qdrant_url: str = "http://localhost:6333",
        api_key: Optional[str] = None,
        collection: str = QDRANT_COLLECTION,
    ):
        self.url = qdrant_url
        self.api_key = api_key
        self.collection = collection
        self._client = None
        self._embedder = None

    def _get_client(self):
        if self._client is None:
            from qdrant_client import QdrantClient
            self._client = QdrantClient(url=self.url, api_key=self.api_key)
        return self._client

    def _get_embedder(self):
        if self._embedder is None:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading PubMedBERT embedding model…")
            self._embedder = SentenceTransformer(MEDICAL_EMBEDDING_MODEL)
            logger.info("PubMedBERT loaded. Dimension: %d", EMBEDDING_DIM)
        return self._embedder

    def ensure_collection(self) -> None:
        """Create the Qdrant collection if it doesn't exist."""
        from qdrant_client.models import Distance, VectorParams
        client = self._get_client()
        existing = [c.name for c in client.get_collections().collections]
        if self.collection not in existing:
            client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
            )
            logger.info("Created Qdrant collection: %s", self.collection)

    def embed_text(self, text: str) -> list[float]:
        """Embed a text string using PubMedBERT."""
        embedder = self._get_embedder()
        vector = embedder.encode(text, normalize_embeddings=True)
        return vector.tolist()

    def embed_batch(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        """Batch embed multiple texts efficiently."""
        embedder = self._get_embedder()
        vectors = embedder.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=len(texts) > 100,
        )
        return [v.tolist() for v in vectors]

    def upsert_chunk(
        self,
        chunk_id: str,
        content: str,
        metadata: dict,
    ) -> None:
        """Insert or update a single knowledge chunk."""
        from qdrant_client.models import PointStruct
        client = self._get_client()
        vector = self.embed_text(content)
        client.upsert(
            collection_name=self.collection,
            points=[PointStruct(id=chunk_id, vector=vector, payload=metadata)],
        )

    def upsert_chunks_batch(
        self,
        chunks: list[dict],   # [{id, content, metadata}]
        batch_size: int = 64,
    ) -> int:
        """Batch upsert chunks — significantly faster than one-by-one."""
        from qdrant_client.models import PointStruct
        client = self._get_client()

        total = 0
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i: i + batch_size]
            texts = [c["content"] for c in batch]
            vectors = self.embed_batch(texts)

            points = [
                PointStruct(
                    id=c["id"],
                    vector=vectors[j],
                    payload=c["metadata"],
                )
                for j, c in enumerate(batch)
            ]
            client.upsert(collection_name=self.collection, points=points)
            total += len(batch)
            logger.debug("Upserted %d/%d chunks", total, len(chunks))

        return total

    def search(
        self,
        query: str,
        top_k: int = 8,
        score_threshold: float = 0.55,
        filter_specialty: Optional[list[str]] = None,
        filter_drugs: Optional[list[str]] = None,
        filter_source_types: Optional[list[str]] = None,
    ) -> list[RetrievedChunk]:
        """
        Semantic similarity search with optional metadata filters.
        Returns top_k most relevant chunks above score_threshold.
        """
        from qdrant_client.models import Filter, FieldCondition, MatchAny

        client = self._get_client()
        query_vector = self.embed_text(query)

        # Build filter conditions
        must_conditions = []
        if filter_specialty:
            must_conditions.append(
                FieldCondition(key="specialty_tags", match=MatchAny(any=filter_specialty))
            )
        if filter_drugs:
            must_conditions.append(
                FieldCondition(key="drugs_mentioned", match=MatchAny(any=[d.lower() for d in filter_drugs]))
            )
        if filter_source_types:
            must_conditions.append(
                FieldCondition(key="source_type", match=MatchAny(any=filter_source_types))
            )

        query_filter = Filter(must=must_conditions) if must_conditions else None

        # qdrant-client >= 1.12 removed Client.search in favor of query_points;
        # fall back to the legacy search() on older clients.
        if hasattr(client, "query_points"):
            results = client.query_points(
                collection_name=self.collection,
                query=query_vector,
                query_filter=query_filter,
                limit=top_k,
                score_threshold=score_threshold,
                with_payload=True,
            ).points
        else:
            results = client.search(
                collection_name=self.collection,
                query_vector=query_vector,
                query_filter=query_filter,
                limit=top_k,
                score_threshold=score_threshold,
                with_payload=True,
            )

        return [
            RetrievedChunk(
                chunk_id=str(r.id),
                source_id=r.payload.get("source_id", ""),
                content=r.payload.get("content", ""),
                section_title=r.payload.get("section_title"),
                source_title=r.payload.get("source_title", "Unknown Source"),
                source_type=r.payload.get("source_type", ""),
                url=r.payload.get("url"),
                doi=r.payload.get("doi"),
                evidence_grade=r.payload.get("evidence_grade"),
                similarity_score=r.score,
                drugs_mentioned=r.payload.get("drugs_mentioned", []),
                specialty_tags=r.payload.get("specialty_tags", []),
            )
            for r in results
        ]

    def delete_source(self, source_id: str) -> int:
        """Remove all chunks belonging to a source (e.g., outdated guideline version)."""
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        client = self._get_client()
        result = client.delete(
            collection_name=self.collection,
            points_selector=Filter(
                must=[FieldCondition(key="source_id", match=MatchValue(value=source_id))]
            ),
        )
        logger.info("Deleted source %s from vector store", source_id)
        return getattr(result, "deleted_count", 0)

    def get_stats(self) -> dict:
        """Return collection statistics (compatible with Qdrant 1.9.x and 1.11+)."""
        client = self._get_client()
        info = client.get_collection(self.collection)
        # Qdrant 1.9.x uses points_count; 1.11+ may use vectors_count
        total = (getattr(info, "vectors_count", None)
                 or getattr(info, "points_count", None)
                 or 0)
        indexed = (getattr(info, "indexed_vectors_count", None) or 0)
        return {
            "collection": self.collection,
            "total_vectors": total,
            "indexed_vectors": indexed,
            "embedding_model": MEDICAL_EMBEDDING_MODEL,
            "dimension": EMBEDDING_DIM,
        }
