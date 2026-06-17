"""
Load a pre-embedded points file (produced on a GPU / in Colab) into the local
Qdrant — making the corpus available OFFLINE without re-embedding here.

The file is gzipped JSON-Lines, one object per line:
    {"id": "<uuid>", "vector": [..768 floats..], "payload": {...}}

This is the version-independent transfer: the heavy embedding happened on a fast
box; locally we just upsert the vectors (fast). Same Qdrant collection the
Second Brain queries, so retrieval works offline immediately after.

Usage:
    python scripts/load_points.py pharmpilot_kb_points.jsonl.gz
    QDRANT_URL=http://localhost:6334 python scripts/load_points.py points.jsonl.gz --recreate
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

DEFAULT_COLLECTION = "pharmpilot_clinical_knowledge"
DEFAULT_DIM = 768


def _read_points(path: str):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def main() -> None:
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams, PointStruct

    ap = argparse.ArgumentParser(description="Load pre-embedded points into local Qdrant")
    ap.add_argument("points_file", help="path to pharmpilot_kb_points.jsonl(.gz)")
    ap.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6334"))
    ap.add_argument("--collection", default=DEFAULT_COLLECTION)
    ap.add_argument("--dim", type=int, default=DEFAULT_DIM)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--recreate", action="store_true",
                    help="drop & recreate the collection first (fresh import)")
    args = ap.parse_args()

    client = QdrantClient(url=args.qdrant_url)
    names = [c.name for c in client.get_collections().collections]
    if args.recreate and args.collection in names:
        client.delete_collection(args.collection)
        names.remove(args.collection)
    if args.collection not in names:
        client.create_collection(
            collection_name=args.collection,
            vectors_config=VectorParams(size=args.dim, distance=Distance.COSINE))
        print(f"created collection {args.collection} (dim={args.dim}, cosine)")

    buf, total = [], 0
    for rec in _read_points(args.points_file):
        buf.append(PointStruct(id=rec["id"], vector=rec["vector"], payload=rec.get("payload", {})))
        if len(buf) >= args.batch:
            client.upsert(collection_name=args.collection, points=buf)
            total += len(buf); buf = []
            if total % (args.batch * 10) == 0:
                print(f"  upserted {total} points…")
    if buf:
        client.upsert(collection_name=args.collection, points=buf)
        total += len(buf)

    count = client.count(args.collection).count
    print(f"✓ loaded {total} points → collection now holds {count} vectors. RAG is offline-ready.")


if __name__ == "__main__":
    main()
