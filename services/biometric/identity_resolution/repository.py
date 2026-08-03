"""Loading the vector store out of Postgres, and keeping it warm.

`vector_store.ModalityIndex` searches in memory; this is what fills it. The
requirement is that the owner can query the gallery *at any second*, so the
index cannot be rebuilt per request — a 50,000-template load from the database
on every query would dominate the search it exists to serve.

So the index is cached per (pharmacy, modality) and invalidated on write. The
cache is deliberately in-process and not distributed: correctness never depends
on it (a cold cache just reloads), and a stale index is impossible because every
enrolment and retirement clears it explicitly.

Calibration lives here too. `services.biometric.fusion` refuses to let an
uncalibrated modality vote, and `ImpostorStats` will not construct on fewer than
1000 mismatched pairs — the tail estimate below that is noise, and the tail is
the entire operating point. `measure_impostor_stats` derives those pairs from
the gallery itself, which is the only population that actually reflects this
pharmacy's people, cameras and lighting.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import vector_store as V

if TYPE_CHECKING:  # avoids a circular import at runtime
    from .thresholds import ImpostorStats

log = logging.getLogger(__name__)

# (pharmacy_id, modality) -> (index, loaded_at)
_CACHE: dict[tuple[str, str], tuple[V.ModalityIndex, datetime]] = {}


def invalidate(pharmacy_id, modality: str | None = None) -> int:
    """Drop cached indexes after a write. Returns how many were dropped."""
    keys = [k for k in _CACHE
            if k[0] == str(pharmacy_id) and (modality is None or k[1] == modality)]
    for k in keys:
        _CACHE.pop(k, None)
    return len(keys)


def cache_state() -> list[dict]:
    """What is warm right now — surfaced so the admin panel can show it rather
    than the operator guessing why one query was slower than the next."""
    return [{"pharmacy_id": k[0], "modality": k[1], "templates": len(idx),
             "identities": idx.identity_count, "loaded_at": ts.isoformat()}
            for k, (idx, ts) in sorted(_CACHE.items())]


async def load_index(db: AsyncSession, pharmacy_id, modality: str, *,
                     force: bool = False) -> V.ModalityIndex:
    """Return a searchable index for one modality, from cache when possible."""
    key = (str(pharmacy_id), modality)
    if not force and key in _CACHE:
        return _CACHE[key][0]

    dim = V.EXPECTED_DIM.get(modality)
    if dim is None:
        raise V.VectorStoreError(f"unknown modality {modality!r}")

    rows = (await db.execute(text("""
        SELECT id AS template_id, identity_id, embedding, dim, quality
        FROM biometric_templates
        WHERE pharmacy_id = :pid AND modality = :m
          AND retired_at IS NULL AND is_deleted = false
        ORDER BY created_at"""), {"pid": pharmacy_id, "m": modality})).mappings().all()

    loaded = []
    for r in rows:
        if int(r["dim"]) != dim:
            # A template stored at another dimension cannot be compared with
            # this modality's probes. Skip it loudly rather than crash the whole
            # gallery load for one bad row.
            log.warning("skipping %s template %s: dim %s != %s",
                        modality, r["template_id"], r["dim"], dim)
            continue
        loaded.append({"identity_id": r["identity_id"], "template_id": r["template_id"],
                       "embedding": V.decode_vector(r["embedding"], dim),
                       "quality": r["quality"]})

    idx = V.ModalityIndex(modality=modality, dim=dim)
    idx.load(loaded)
    _CACHE[key] = (idx, datetime.now(timezone.utc))
    return idx


async def enrol_template(db: AsyncSession, *, pharmacy_id, identity_id: UUID,
                         modality: str, embedding: np.ndarray, model_version: str,
                         quality: float = 1.0, enrolled_by_id=None,
                         capture_context: dict | None = None) -> UUID:
    """Add one template. Does not commit — the caller owns the transaction."""
    dim = V.EXPECTED_DIM.get(modality)
    if dim is None:
        raise V.VectorStoreError(f"unknown modality {modality!r}")
    vec = np.asarray(embedding, dtype=np.float32)
    if vec.shape != (dim,):
        raise V.VectorStoreError(
            f"{modality} embedding has dimension {vec.shape}, expected ({dim},)")
    if not 0.0 <= float(quality) <= 1.0:
        raise V.VectorStoreError("quality must be within [0, 1]")

    row = (await db.execute(text("""
        INSERT INTO biometric_templates
            (id, pharmacy_id, identity_id, modality, embedding, dim,
             model_version, quality, capture_context, enrolled_by_id,
             created_at, updated_at, is_deleted)
        VALUES (gen_random_uuid(), :pid, :iid, :m, :emb, :dim, :mv, :q,
                CAST(:ctx AS jsonb), :by, NOW(), NOW(), false)
        RETURNING id"""),
        {"pid": pharmacy_id, "iid": identity_id, "m": modality,
         "emb": V.encode_vector(vec), "dim": dim, "mv": model_version,
         "q": float(quality), "by": enrolled_by_id,
         "ctx": __import__("json").dumps(capture_context or {})})).scalar_one()
    invalidate(pharmacy_id, modality)
    return row


async def retire_template(db: AsyncSession, *, pharmacy_id, template_id: UUID,
                          reason: str) -> bool:
    """Retire, never delete. An identification made last year has to stay
    explainable from the templates that existed when it was made."""
    res = await db.execute(text("""
        UPDATE biometric_templates
           SET retired_at = NOW(), retired_reason = :why, updated_at = NOW()
         WHERE id = :tid AND pharmacy_id = :pid AND retired_at IS NULL
        RETURNING modality"""),
        {"tid": template_id, "pid": pharmacy_id, "why": reason[:240]})
    row = res.first()
    if row is None:
        return False
    invalidate(pharmacy_id, row[0])
    return True


# ── calibration ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Calibration:
    modality: str
    impostor_mean: float
    impostor_std: float
    pairs: int
    genuine_mean: float | None
    genuine_std: float | None
    genuine_pairs: int
    usable: bool
    reason: str = ""

    def as_dict(self) -> dict:
        return {"modality": self.modality,
                "impostor_mean": round(self.impostor_mean, 4),
                "impostor_std": round(self.impostor_std, 4),
                "pairs": self.pairs,
                "genuine_mean": round(self.genuine_mean, 4) if self.genuine_mean is not None else None,
                "genuine_std": round(self.genuine_std, 4) if self.genuine_std is not None else None,
                "genuine_pairs": self.genuine_pairs,
                "usable": self.usable, "reason": self.reason}


def measure_impostor_stats(index: V.ModalityIndex,
                           min_pairs: int = 1000) -> Calibration:
    """Score every cross-identity template pair in the gallery.

    This is the impostor distribution that matters: the same people, cameras and
    lighting the system will actually run against. A published figure from a
    benchmark does not transfer — veiling, masks and desk-mounted capture all
    move it.

    Same-identity pairs are collected too, but they are a weaker sample: two
    templates of one person enrolled minutes apart are more alike than the same
    person a year later, so the genuine mean here is optimistic and is reported
    as such rather than used to set an acceptance threshold.
    """
    n = len(index)
    if n < 2:
        return Calibration(index.modality, 0.0, 0.0, 0, None, None, 0, False,
                           "fewer than two templates enrolled")

    vecs = index._vectors                      # already L2-normalised on load
    ids = np.asarray([str(i) for i in index._identities])
    sims = vecs @ vecs.T
    iu = np.triu_indices(n, k=1)
    same = ids[iu[0]] == ids[iu[1]]
    scores = sims[iu]

    impostor = scores[~same]
    genuine = scores[same]
    pairs = int(impostor.size)

    if pairs < min_pairs:
        return Calibration(
            index.modality,
            float(impostor.mean()) if pairs else 0.0,
            float(impostor.std()) if pairs else 0.0,
            pairs,
            float(genuine.mean()) if genuine.size else None,
            float(genuine.std()) if genuine.size else None,
            int(genuine.size), False,
            f"{pairs} cross-identity pairs is too few to estimate the tail; "
            f"need {min_pairs}. Enrol more people or more templates each.")

    std = float(impostor.std())
    if std <= 0:
        return Calibration(index.modality, float(impostor.mean()), 0.0, pairs,
                           None, None, int(genuine.size), False,
                           "impostor scores have zero spread — the gallery is "
                           "probably duplicated templates of one person")
    return Calibration(index.modality, float(impostor.mean()), std, pairs,
                       float(genuine.mean()) if genuine.size else None,
                       float(genuine.std()) if genuine.size else None,
                       int(genuine.size), True)


async def load_impostor_stats(db: AsyncSession, pharmacy_id,
                              modality: str) -> "ImpostorStats | None":
    """The stored calibration for a modality, as `ImpostorStats`, or None.

    None is meaningful: `services.biometric.fusion` treats an uncalibrated
    modality as one that may not vote, which is the correct default.
    """
    from .thresholds import ImpostorStats

    r = (await db.execute(text("""
        SELECT impostor_mean, impostor_std, samples, model_version, measured_at
        FROM biometric_score_stats
        WHERE pharmacy_id = :pid AND modality = :m
        ORDER BY measured_at DESC LIMIT 1"""),
        {"pid": pharmacy_id, "m": modality})).mappings().first()
    if r is None:
        return None
    try:
        return ImpostorStats(
            mean=float(r["impostor_mean"]), std=float(r["impostor_std"]),
            sample_size=int(r["samples"]), model=r["model_version"],
            measured_at=r["measured_at"].isoformat() if r["measured_at"] else None)
    except ValueError as e:
        # A stored calibration that no longer satisfies the sample-size floor
        # must not silently become a licence to vote.
        log.warning("stored %s calibration rejected: %s", modality, e)
        return None
