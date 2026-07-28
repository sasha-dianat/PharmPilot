"""The enrolled-face gallery: exact search, durable mapping, real deletion.

Replaces a bare `faiss.IndexFlatIP` plus an in-process dict. Three things were
wrong with that arrangement and all three are addressed here.

**The mapping did not survive a restart.** `faiss.write_index` persisted the
vectors, but the FAISS-position → identity-UUID dict was only ever populated by
`enroll()` in the same process. Reload the index anywhere else and every match
resolved to `None` — a silent failure, the worst kind.

**Deletion was impossible.** Positional ids in a flat index cannot be removed,
so un-enrolling a former patient or honouring an erasure request had no
implementation path at all.

**Only the top-1 neighbour was retrieved,** which makes the margin test —
"is the best candidate meaningfully better than the runner-up?" — structurally
impossible to compute. `search` here returns the best hit *per identity*, which
is the form the margin test actually needs: a person enrolled with three
photographs must not appear to be their own runner-up.

Search is exact (brute-force inner product over L2-normalised vectors), not
approximate. That is a deliberate choice, not a shortcut: identification runs at
per-comparison false-match rates around 1e-7, where an ANN index's recall loss
costs true matches precisely at the tight end of the score distribution. At
pharmacy gallery sizes a 512-dimension dot product against 50,000 rows is ~25M
multiply-adds — a few milliseconds in NumPy, and it needs no native dependency.
`faiss` remains a reasonable optimisation if the gallery ever reaches millions.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from uuid import UUID

import numpy as np


@dataclass(frozen=True)
class GalleryHit:
    identity_id: UUID
    similarity: float          # raw cosine in [-1, 1]
    vector_id: int             # which enrolled template matched


class VectorGallery:
    """L2-normalised embeddings keyed by identity, with many templates per person."""

    def __init__(self, dim: int = 512):
        self.dim = dim
        self._vectors = np.zeros((0, dim), dtype=np.float32)
        self._vector_ids: list[int] = []
        self._owners: list[UUID] = []
        self._next_id = 1

    # ── state ────────────────────────────────────────────────────────────
    @property
    def size(self) -> int:
        """Number of enrolled templates."""
        return len(self._owners)

    @property
    def identity_count(self) -> int:
        """Number of distinct people. This is the N in the FPIR ≈ N·f budget —
        not `size`, since one person may hold several templates."""
        return len(set(self._owners))

    # ── mutation ─────────────────────────────────────────────────────────
    def add(self, identity_id: UUID, embedding: np.ndarray,
            vector_id: int | None = None) -> int:
        """Enrol one template. Returns the vector id to persist alongside the
        identity row (`biometric_identities.faiss_index_id`)."""
        v = self._normalise(embedding)
        if vector_id is None:
            vector_id = self._next_id
        self._next_id = max(self._next_id, vector_id) + 1
        self._vectors = np.vstack([self._vectors, v.reshape(1, -1)])
        self._vector_ids.append(int(vector_id))
        self._owners.append(identity_id)
        return int(vector_id)

    def remove(self, identity_id: UUID) -> int:
        """Withdraw every template belonging to one identity. Returns how many."""
        keep = [i for i, o in enumerate(self._owners) if o != identity_id]
        removed = self.size - len(keep)
        if removed:
            self._vectors = self._vectors[keep] if keep else np.zeros(
                (0, self.dim), dtype=np.float32)
            self._vector_ids = [self._vector_ids[i] for i in keep]
            self._owners = [self._owners[i] for i in keep]
        return removed

    def rebuild(self, rows: Iterable[tuple[UUID, int, np.ndarray]]) -> int:
        """Reconstruct from the database — the authoritative source of the
        mapping. Used on cold start when no sidecar file is present."""
        self._vectors = np.zeros((0, self.dim), dtype=np.float32)
        self._vector_ids, self._owners, self._next_id = [], [], 1
        vecs = []
        for identity_id, vector_id, emb in rows:
            vecs.append(self._normalise(emb))
            self._vector_ids.append(int(vector_id))
            self._owners.append(identity_id)
            self._next_id = max(self._next_id, int(vector_id) + 1)
        if vecs:
            self._vectors = np.vstack(vecs).astype(np.float32)
        return self.size

    # ── query ────────────────────────────────────────────────────────────
    def search(self, embedding: np.ndarray, k: int = 10) -> list[GalleryHit]:
        """Best hit per identity, strongest first, at most `k` identities.

        Collapsing to one row per person is what makes the caller's margin test
        meaningful — otherwise a well-enrolled person is their own runner-up and
        the margin is always ~0.
        """
        if self.size == 0 or k <= 0:
            return []
        q = self._normalise(embedding)
        sims = self._vectors @ q                       # cosine, vectors are unit
        best: dict[UUID, tuple[float, int]] = {}
        for i, owner in enumerate(self._owners):
            s = float(sims[i])
            prev = best.get(owner)
            if prev is None or s > prev[0]:
                best[owner] = (s, self._vector_ids[i])
        ordered = sorted(best.items(), key=lambda kv: -kv[1][0])[:k]
        return [GalleryHit(identity_id=o, similarity=s, vector_id=vid)
                for o, (s, vid) in ordered]

    # ── persistence ──────────────────────────────────────────────────────
    def save(self, path: str | Path) -> None:
        """Vectors AND mapping, written atomically. Persisting one without the
        other is the defect this replaces."""
        p = Path(path).with_suffix(".npz")
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".npz.tmp")
        # Write through a handle: np.savez appends ".npz" to a *path* that lacks
        # it, which would silently place the file somewhere other than `tmp`.
        with open(tmp, "wb") as fh:
            np.savez(
                fh,
                vectors=self._vectors,
                vector_ids=np.asarray(self._vector_ids, dtype=np.int64),
                owners=np.asarray([str(o) for o in self._owners], dtype=object),
                meta=np.asarray([json.dumps({"dim": self.dim,
                                             "next_id": self._next_id})],
                                dtype=object),
            )
        os.replace(tmp, p)

    def load(self, path: str | Path) -> int:
        p = Path(path).with_suffix(".npz")
        if not p.exists():
            return 0
        with np.load(p, allow_pickle=True) as z:
            self._vectors = z["vectors"].astype(np.float32)
            self._vector_ids = [int(i) for i in z["vector_ids"]]
            self._owners = [UUID(str(s)) for s in z["owners"]]
            meta = json.loads(str(z["meta"][0]))
        self.dim = int(meta.get("dim", self.dim))
        self._next_id = int(meta.get("next_id", self.size + 1))
        return self.size

    # ── internals ────────────────────────────────────────────────────────
    def _normalise(self, embedding: np.ndarray) -> np.ndarray:
        v = np.asarray(embedding, dtype=np.float32).reshape(-1)
        if v.shape[0] != self.dim:
            raise ValueError(f"expected dim {self.dim}, got {v.shape[0]}")
        n = float(np.linalg.norm(v))
        if n == 0.0:
            raise ValueError("cannot enrol or query a zero vector")
        # Defensive: the extractor already normalises, but an un-normalised
        # vector would silently turn cosine into an unbounded inner product and
        # every threshold in the system would become meaningless.
        return (v / n).astype(np.float32)
