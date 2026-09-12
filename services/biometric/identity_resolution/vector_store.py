"""The biometric vector store: many templates per person, more than one modality.

What this replaces. `BiometricIdentity` carries a single `face_embedding_enc`
and a single `gait_signature_enc` — one template per modality per person. That
is the binding limit on accuracy, because the cheapest reliable way to improve
recognition is to enrol the same person several times (different day, distance,
lighting, head angle) and keep the best match across templates. With one slot,
every new capture must overwrite the last, so enrolment can never improve and a
single bad capture degrades the person permanently.

Here a person has as many templates as you care to enrol, per modality, each
carrying its own quality and provenance.

Search is exact, not approximate, and that is deliberate — the reasoning in
`gallery.py` applies unchanged: identification runs at per-comparison false-match
rates around 1e-7, where an ANN index's recall loss lands precisely on the true
matches at the tight end of the distribution. A 512-float dot product against
50,000 rows is ~25M multiply-adds, single-digit milliseconds in NumPy. pgvector
is unavailable on this database and Qdrant is not running; neither is needed at
pharmacy scale, and the interface here leaves room for either later.

`search` returns the best hit **per identity**, not the best hits overall. A
person enrolled from five photographs must not occupy the top five results and
appear to be their own runner-up — that would make the margin test (is the best
candidate meaningfully better than the second-best *person*?) impossible.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field

from uuid import UUID

import numpy as np

FACE = "face"
GAIT = "gait"
MODALITIES = (FACE, GAIT)

# Dimensions are fixed per modality so a mismatched vector is caught at
# enrolment rather than silently producing meaningless similarities.
EXPECTED_DIM = {FACE: 512, GAIT: 64}


class VectorStoreError(ValueError):
    """An enrolment or query that cannot be honoured."""


@dataclass(frozen=True)
class Hit:
    identity_id: UUID
    similarity: float          # raw cosine in [-1, 1]
    template_id: UUID
    modality: str
    quality: float


@dataclass
class ModalityIndex:
    """Exact inner-product index over L2-normalised vectors for one modality."""
    modality: str
    dim: int
    _vectors: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), np.float32))
    _identities: list[UUID] = field(default_factory=list)
    _templates: list[UUID] = field(default_factory=list)
    _quality: list[float] = field(default_factory=list)
    # Retained so calibration can be stratified. Without it the occlusion
    # stratum recorded at enrolment is lost on load, and one pooled threshold
    # gets applied to every kind of capture — including the veiled ones it is
    # least appropriate for.
    _contexts: list[dict] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self._identities)

    @property
    def identity_count(self) -> int:
        return len(set(self._identities))

    def load(self, rows: list[dict]) -> None:
        """Rebuild from durable storage. Rows: {identity_id, template_id,
        embedding, quality}."""
        vecs, ids, tids, quals, ctxs = [], [], [], [], []
        for r in rows:
            v = np.asarray(r["embedding"], dtype=np.float32)
            if v.shape != (self.dim,):
                raise VectorStoreError(
                    f"{self.modality} template {r['template_id']} has dimension "
                    f"{v.shape}, expected ({self.dim},)")
            n = float(np.linalg.norm(v))
            if n < 1e-9:
                raise VectorStoreError(f"degenerate template {r['template_id']}")
            vecs.append(v / n)
            ids.append(r["identity_id"])
            tids.append(r["template_id"])
            quals.append(float(r.get("quality") or 1.0))
            ctxs.append(dict(r.get("capture_context") or {}))
        self._vectors = (np.vstack(vecs).astype(np.float32) if vecs
                         else np.zeros((0, self.dim), np.float32))
        self._identities, self._templates, self._quality = ids, tids, quals
        self._contexts = ctxs

    def search(self, probe: np.ndarray, top_k: int = 5) -> list[Hit]:
        """Best hit per identity, descending. Empty when nothing is enrolled."""
        if len(self) == 0:
            return []
        p = np.asarray(probe, dtype=np.float32)
        if p.shape != (self.dim,):
            raise VectorStoreError(
                f"probe dimension {p.shape} != ({self.dim},) for {self.modality}")
        n = float(np.linalg.norm(p))
        if n < 1e-9:
            raise VectorStoreError("degenerate probe")
        sims = self._vectors @ (p / n)

        best: dict[UUID, tuple[float, int]] = {}
        for i, s in enumerate(sims):
            ident = self._identities[i]
            if ident not in best or s > best[ident][0]:
                best[ident] = (float(s), i)
        ranked = sorted(best.items(), key=lambda kv: -kv[1][0])[:top_k]
        return [Hit(identity_id=ident, similarity=s, template_id=self._templates[i],
                    modality=self.modality, quality=self._quality[i])
                for ident, (s, i) in ranked]


# ── serialisation ─────────────────────────────────────────────────────────
# float32 little-endian, base64 for transport. Not encryption: the template
# column is protected at rest by the database, and this is only a stable,
# lossless encoding that survives a JSON round trip.

def encode_vector(v: np.ndarray) -> bytes:
    arr = np.asarray(v, dtype="<f4")
    if not np.all(np.isfinite(arr)):
        raise VectorStoreError("vector contains non-finite values")
    return arr.tobytes()


def decode_vector(raw: bytes | str, dim: int) -> np.ndarray:
    if isinstance(raw, str):
        raw = base64.b64decode(raw)
    arr = np.frombuffer(raw, dtype="<f4")
    if arr.shape != (dim,):
        raise VectorStoreError(f"decoded dimension {arr.shape} != ({dim},)")
    return arr.astype(np.float32)


# ── handing off to the fusion engine ──────────────────────────────────────
# This module deliberately does NOT decide what a match means. `services.
# biometric.fusion` already owns that: it admits or excludes a modality against
# an evidence-led quality floor, fuses in log-lambda space, and returns a
# decision with its reasoning. An earlier draft of this file grew its own
# two-modality fusion; it was removed rather than shipped, because two engines
# disagreeing about the same identity is worse than either engine alone.
#
# Storage produces readings. Fusion judges them.

def to_readings(hits_by_modality: dict[str, list["Hit"]],
                stats_by_modality: dict[str, object],
                quality_by_modality: dict[str, float] | None = None,
                gallery_size: int = 1,
                gallery_size_by_modality: dict[str, int] | None = None) -> list:
    """Convert store hits into `fusion.ModalityReading`s.

    `stats_by_modality` holds the measured `ImpostorStats` per modality; a
    modality without one is passed through with `stats=None`, which the fusion
    engine excludes by design — an uncalibrated stream must not vote.

    `gallery_size_by_modality` matters more than it looks: FPIR ~= N * FMR is a
    PER-MODALITY relationship, and the face and gait galleries hold different
    numbers of people. A single shared N computes at least one modality's
    confidence against the wrong denominator, understating risk for the larger
    gallery. The shared `gallery_size` remains the fallback.
    """
    from services.biometric.fusion import ModalityReading

    quality_by_modality = quality_by_modality or {}
    sizes = gallery_size_by_modality or {}
    out = []
    for modality, hits in hits_by_modality.items():
        n = int(sizes.get(modality, gallery_size))
        for h in hits:
            out.append(ModalityReading(
                modality=modality, identity_id=h.identity_id,
                similarity=h.similarity,
                quality=float(quality_by_modality.get(modality, h.quality)),
                stats=stats_by_modality.get(modality),
                gallery_size=n))
    return out
