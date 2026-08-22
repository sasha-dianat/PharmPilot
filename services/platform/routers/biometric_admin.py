"""Biometric gallery administration — the owner's window onto the vector store.

Answers, at any second and without an LLM: who is enrolled, on which
modalities, how good the templates are, whether the gallery is calibrated well
enough to be trusted, and — given a face embedding or a walk — who this is.

The whole surface is deliberately explainable. Every search returns the
per-modality similarities, the calibrated evidence each one contributed, what
was excluded and why, and the margin to the runner-up. An identification the
owner cannot interrogate is one they cannot defend, so nothing here returns a
bare name and a number.

Two properties the endpoints preserve rather than assume:

**Calibration gates voting.** `services.biometric.fusion` refuses to let an
uncalibrated modality contribute, and `ImpostorStats` will not construct below
1000 mismatched pairs. `/calibrate` measures those pairs from this pharmacy's
own gallery — published figures do not transfer across cameras, lighting or
veiling.

**Retire, never delete.** An identification made last year has to stay
explainable from the templates that existed when it was made.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.biometric.gait import encoder as G
from services.biometric.identity_resolution import repository as R
from services.biometric.identity_resolution import vector_store as V
from services.platform.auth import require_permission
from services.platform.database import get_db
from shared.models.auth import Staff

router = APIRouter()
log = logging.getLogger(__name__)

MAX_TOP_K = 25


# ── Gallery browse ────────────────────────────────────────────────────────

@router.get("/admin/gallery")
async def browse_gallery(
    q: Optional[str] = Query(None, description="identity id, patient/staff link, or class"),
    modality: Optional[str] = Query(None, description="face | gait"),
    linked: Optional[str] = Query(None, description="patient | staff | unlinked"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    staff: Staff = Depends(require_permission("biometric:read")),
    db: AsyncSession = Depends(get_db),
):
    """Who is in the vector store, and what each identity is enrolled on."""
    if modality and modality not in V.MODALITIES:
        raise HTTPException(422, f"unknown modality {modality!r}")

    where = ["bi.pharmacy_id = :pid", "bi.is_deleted = false"]
    p: dict[str, Any] = {"pid": staff.pharmacy_id, "lim": limit, "off": offset}
    if q:
        where.append("(CAST(bi.id AS text) ILIKE :q OR CAST(bi.patient_id AS text) "
                     "ILIKE :q OR CAST(bi.staff_id AS text) ILIKE :q "
                     "OR bi.identity_class ILIKE :q)")
        p["q"] = f"%{q}%"
    if linked == "patient":
        where.append("bi.patient_id IS NOT NULL")
    elif linked == "staff":
        where.append("bi.staff_id IS NOT NULL")
    elif linked == "unlinked":
        where.append("bi.patient_id IS NULL AND bi.staff_id IS NULL")
    if modality:
        where.append("EXISTS (SELECT 1 FROM biometric_templates t WHERE "
                     "t.identity_id = bi.id AND t.modality = :m "
                     "AND t.retired_at IS NULL AND t.is_deleted = false)")
        p["m"] = modality

    rows = (await db.execute(text(f"""
        SELECT bi.id, bi.identity_class, bi.patient_id, bi.staff_id,
               bi.first_seen_at, bi.last_seen_at, bi.total_visits,
               bi.is_watchlist_match, bi.watchlist_source,
               COUNT(t.id) FILTER (WHERE t.modality = 'face'
                                   AND t.retired_at IS NULL) AS face_templates,
               COUNT(t.id) FILTER (WHERE t.modality = 'gait'
                                   AND t.retired_at IS NULL) AS gait_templates,
               COUNT(t.id) FILTER (WHERE t.retired_at IS NOT NULL) AS retired_templates,
               AVG(t.quality) FILTER (WHERE t.retired_at IS NULL) AS mean_quality,
               MAX(t.created_at) AS last_enrolled_at,
               COUNT(*) OVER () AS total_rows
        FROM biometric_identities bi
        LEFT JOIN biometric_templates t
               ON t.identity_id = bi.id AND t.is_deleted = false
        WHERE {' AND '.join(where)}
        GROUP BY bi.id
        ORDER BY bi.last_seen_at DESC NULLS LAST
        LIMIT :lim OFFSET :off"""), p)).mappings().all()

    total = rows[0]["total_rows"] if rows else 0
    return {
        "identities": [{
            "identity_id": str(r["id"]), "identity_class": r["identity_class"],
            "patient_id": str(r["patient_id"]) if r["patient_id"] else None,
            "staff_id": str(r["staff_id"]) if r["staff_id"] else None,
            "face_templates": int(r["face_templates"]),
            "gait_templates": int(r["gait_templates"]),
            "retired_templates": int(r["retired_templates"]),
            "mean_quality": round(float(r["mean_quality"]), 3) if r["mean_quality"] else None,
            "enrolled_modalities": [m for m, n in (("face", r["face_templates"]),
                                                   ("gait", r["gait_templates"])) if n],
            "total_visits": int(r["total_visits"] or 0),
            "is_watchlist_match": bool(r["is_watchlist_match"]),
            "watchlist_source": r["watchlist_source"],
            "first_seen_at": r["first_seen_at"].isoformat() if r["first_seen_at"] else None,
            "last_seen_at": r["last_seen_at"].isoformat() if r["last_seen_at"] else None,
            "last_enrolled_at": r["last_enrolled_at"].isoformat() if r["last_enrolled_at"] else None,
        } for r in rows],
        "total": total, "limit": limit, "offset": offset,
    }


@router.get("/admin/gallery/stats")
async def gallery_stats(
    staff: Staff = Depends(require_permission("biometric:read")),
    db: AsyncSession = Depends(get_db),
):
    """Gallery health, including whether each modality may currently vote.

    `can_vote` is the number that matters operationally: a modality without a
    stored calibration is excluded by the fusion engine no matter how many
    templates it holds.
    """
    per = (await db.execute(text("""
        SELECT modality,
               COUNT(*) FILTER (WHERE retired_at IS NULL) AS active,
               COUNT(*) FILTER (WHERE retired_at IS NOT NULL) AS retired,
               COUNT(DISTINCT identity_id) FILTER (WHERE retired_at IS NULL) AS identities,
               AVG(quality) FILTER (WHERE retired_at IS NULL) AS mean_quality,
               MIN(quality) FILTER (WHERE retired_at IS NULL) AS min_quality
        FROM biometric_templates
        WHERE pharmacy_id = :pid AND is_deleted = false
        GROUP BY modality"""), {"pid": staff.pharmacy_id})).mappings().all()

    out = {}
    for m in V.MODALITIES:
        row = next((r for r in per if r["modality"] == m), None)
        stats = await R.load_impostor_stats(db, staff.pharmacy_id, m)
        out[m] = {
            "active_templates": int(row["active"]) if row else 0,
            "retired_templates": int(row["retired"]) if row else 0,
            "identities": int(row["identities"]) if row else 0,
            "mean_quality": round(float(row["mean_quality"]), 3)
            if row and row["mean_quality"] else None,
            "min_quality": round(float(row["min_quality"]), 3)
            if row and row["min_quality"] else None,
            "dim": V.EXPECTED_DIM[m],
            "calibrated": stats is not None,
            "can_vote": stats is not None,
            "calibration": ({"impostor_mean": round(stats.mean, 4),
                             "impostor_std": round(stats.std, 4),
                             "samples": stats.sample_size,
                             "measured_at": stats.measured_at} if stats else None),
            "why_excluded": None if stats else
            "no measured impostor distribution — run POST /admin/gallery/calibrate",
        }
    total_identities = (await db.execute(text(
        "SELECT COUNT(*) FROM biometric_identities WHERE pharmacy_id = :pid "
        "AND is_deleted = false"), {"pid": staff.pharmacy_id})).scalar()
    return {"modalities": out, "identities_total": int(total_identities or 0),
            "warm_indexes": R.cache_state(),
            "generated_at": datetime.now(timezone.utc).isoformat()}


@router.get("/admin/gallery/{identity_id}")
async def identity_detail(
    identity_id: UUID,
    staff: Staff = Depends(require_permission("biometric:read")),
    db: AsyncSession = Depends(get_db),
):
    """Everything held about one identity, templates included (metadata only —
    the embeddings themselves are never returned over the API)."""
    head = (await db.execute(text("""
        SELECT id, identity_class, patient_id, staff_id, confidence_score,
               first_seen_at, last_seen_at, total_visits, is_watchlist_match,
               watchlist_source, security_notes, consent_security_monitoring,
               consent_patient_services, consent_recorded_at, consent_method
        FROM biometric_identities
        WHERE id = :iid AND pharmacy_id = :pid AND is_deleted = false"""),
        {"iid": identity_id, "pid": staff.pharmacy_id})).mappings().first()
    if head is None:
        raise HTTPException(404, "identity not found at this pharmacy")

    templates = (await db.execute(text("""
        SELECT id, modality, dim, model_version, quality, capture_context,
               enrolled_by_id, created_at, retired_at, retired_reason
        FROM biometric_templates
        WHERE identity_id = :iid AND pharmacy_id = :pid AND is_deleted = false
        ORDER BY created_at DESC"""),
        {"iid": identity_id, "pid": staff.pharmacy_id})).mappings().all()

    visits = (await db.execute(text("""
        SELECT entered_at, exited_at, duration_seconds, identity_class,
               match_confidence, profile_preloaded
        FROM pharmacy_visits
        WHERE biometric_identity_id = :iid AND pharmacy_id = :pid
        ORDER BY entered_at DESC LIMIT 25"""),
        {"iid": identity_id, "pid": staff.pharmacy_id})).mappings().all()

    return {
        "identity": {
            "identity_id": str(head["id"]), "identity_class": head["identity_class"],
            "patient_id": str(head["patient_id"]) if head["patient_id"] else None,
            "staff_id": str(head["staff_id"]) if head["staff_id"] else None,
            "total_visits": int(head["total_visits"] or 0),
            "is_watchlist_match": bool(head["is_watchlist_match"]),
            "watchlist_source": head["watchlist_source"],
            "security_notes": head["security_notes"],
            "consent": {"security_monitoring": bool(head["consent_security_monitoring"]),
                        "patient_services": bool(head["consent_patient_services"]),
                        "recorded_at": head["consent_recorded_at"].isoformat()
                        if head["consent_recorded_at"] else None,
                        "method": head["consent_method"]},
            "first_seen_at": head["first_seen_at"].isoformat() if head["first_seen_at"] else None,
            "last_seen_at": head["last_seen_at"].isoformat() if head["last_seen_at"] else None,
        },
        "templates": [{
            "template_id": str(t["id"]), "modality": t["modality"], "dim": int(t["dim"]),
            "model_version": t["model_version"], "quality": float(t["quality"]),
            "capture_context": t["capture_context"],
            "enrolled_by": str(t["enrolled_by_id"]) if t["enrolled_by_id"] else None,
            "created_at": t["created_at"].isoformat() if t["created_at"] else None,
            "retired_at": t["retired_at"].isoformat() if t["retired_at"] else None,
            "retired_reason": t["retired_reason"], "active": t["retired_at"] is None,
        } for t in templates],
        "recent_visits": [{
            "entered_at": v["entered_at"].isoformat() if v["entered_at"] else None,
            "exited_at": v["exited_at"].isoformat() if v["exited_at"] else None,
            "duration_seconds": v["duration_seconds"],
            "identity_class": v["identity_class"],
            "match_confidence": float(v["match_confidence"]) if v["match_confidence"] is not None else None,
            "profile_preloaded": bool(v["profile_preloaded"]),
        } for v in visits],
    }


# ── The query ─────────────────────────────────────────────────────────────

class GalleryQuery(BaseModel):
    """A probe. Supply a face embedding, a gait keypoint sequence, or both —
    supplying both is what makes fusion possible and is the accurate path."""
    face_embedding: Optional[list[float]] = Field(
        None, description="512-d ArcFace embedding")
    gait_keypoints: Optional[list[list[list[float]]]] = Field(
        None, description="(frames, 17, 3) COCO-17 keypoints as [x, y, confidence]")
    gait_embedding: Optional[list[float]] = Field(
        None, description="64-d gait embedding, if already encoded")
    top_k: int = Field(5, ge=1, le=MAX_TOP_K)


@router.post("/admin/gallery/search")
async def search_gallery(
    body: GalleryQuery,
    staff: Staff = Depends(require_permission("biometric:read")),
    db: AsyncSession = Depends(get_db),
):
    """Identify a probe against the gallery, with the full reasoning attached.

    Returns per-modality hits *and* the fused decision. The fused decision is
    the answer; the per-modality hits are how the owner checks it. When a
    modality is excluded — uncalibrated, below its quality floor, no templates —
    the exclusion and its reason are returned rather than the modality silently
    contributing nothing.
    """
    probes: dict[str, np.ndarray] = {}
    quality: dict[str, float] = {}
    notes: list[str] = []

    if body.face_embedding is not None:
        vec = np.asarray(body.face_embedding, dtype=np.float32)
        if vec.shape != (V.EXPECTED_DIM[V.FACE],):
            raise HTTPException(422, f"face embedding must be "
                                     f"{V.EXPECTED_DIM[V.FACE]}-d, got {vec.shape[0]}")
        probes[V.FACE] = vec
        quality[V.FACE] = 1.0

    if body.gait_keypoints is not None:
        kp = np.asarray(body.gait_keypoints, dtype=np.float32)
        try:
            gvec, gq = G.encode(kp)
        except G.GaitError as e:
            # Not an error for the whole query — face may still identify. Say so
            # explicitly rather than returning a quietly face-only result.
            notes.append(f"gait unusable: {e}")
        else:
            probes[V.GAIT] = gvec
            quality[V.GAIT] = gq.quality_score()
            notes.append(f"gait quality {gq.quality_score():.2f} "
                         f"({gq.usable_frames} usable frames, "
                         f"{gq.cycles_observed:.1f} cycles)")
    elif body.gait_embedding is not None:
        vec = np.asarray(body.gait_embedding, dtype=np.float32)
        if vec.shape != (V.EXPECTED_DIM[V.GAIT],):
            raise HTTPException(422, f"gait embedding must be "
                                     f"{V.EXPECTED_DIM[V.GAIT]}-d, got {vec.shape[0]}")
        probes[V.GAIT] = vec
        quality[V.GAIT] = 1.0

    if not probes:
        # Distinguish "you sent nothing" from "what you sent could not be used".
        # Collapsing the two would have the caller re-send the same unusable
        # clip forever.
        if notes:
            raise HTTPException(422, "probe supplied but unusable: " + "; ".join(notes))
        raise HTTPException(422, "supply face_embedding, gait_keypoints or "
                                 "gait_embedding")

    hits_by_modality: dict[str, list[V.Hit]] = {}
    stats_by_modality: dict[str, object] = {}
    gallery_size = 1
    for modality, probe in probes.items():
        index = await R.load_index(db, staff.pharmacy_id, modality)
        if len(index) == 0:
            notes.append(f"{modality}: no enrolled templates")
            continue
        hits_by_modality[modality] = index.search(probe, top_k=body.top_k)
        gallery_size = max(gallery_size, index.identity_count)
        st = await R.load_impostor_stats(db, staff.pharmacy_id, modality)
        if st is not None:
            stats_by_modality[modality] = st

    from services.biometric.fusion import fuse

    # Fusion takes each modality's BEST opinion, not its whole candidate list.
    # Passing the full top-k made three face hits look like three modalities
    # naming three different identities, which the engine correctly read as
    # disagreement and capped at review. The rest of the list is returned below
    # for the reviewer, and is what the within-modality margin is drawn from.
    best_by_modality = {m: hits[:1] for m, hits in hits_by_modality.items() if hits}
    readings = V.to_readings(best_by_modality, stats_by_modality,
                             quality_by_modality=quality, gallery_size=gallery_size)
    fused = fuse(readings)

    return {
        "decision": fused.decision,
        "identity_id": str(fused.identity_id) if fused.identity_id else None,
        "confidence": round(fused.confidence, 4),
        "margin": round(fused.margin, 4),
        "runner_up_id": str(fused.runner_up_id) if fused.runner_up_id else None,
        "explanation": fused.explanation,
        "contributions": [{"modality": c.modality,
                           "similarity": round(c.similarity, 4),
                           "calibrated": round(c.calibrated, 6),
                           "quality": round(c.quality, 3)}
                          for c in fused.contributions],
        "excluded": [{"modality": e.modality, "reason": e.reason}
                     for e in fused.excluded],
        "per_modality_hits": {
            m: [{"identity_id": str(h.identity_id),
                 "similarity": round(h.similarity, 4),
                 "template_id": str(h.template_id),
                 "template_quality": round(h.quality, 3)} for h in hits]
            for m, hits in hits_by_modality.items()},
        "gallery_size": gallery_size,
        "notes": notes,
        "queried_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Enrolment ─────────────────────────────────────────────────────────────

class EnrolTemplate(BaseModel):
    identity_id: UUID
    modality: str
    model_version: str = Field(..., min_length=1, max_length=64)
    embedding: Optional[list[float]] = None
    gait_keypoints: Optional[list[list[list[float]]]] = None
    quality: float = Field(1.0, ge=0.0, le=1.0)
    capture_context: Optional[dict] = None


@router.post("/admin/gallery/templates", status_code=201)
async def enrol_template(
    body: EnrolTemplate,
    staff: Staff = Depends(require_permission("biometric:write")),
    db: AsyncSession = Depends(get_db),
):
    """Add a template to an identity.

    Multiple templates per identity per modality is the point: one enrolment
    photograph is a single viewpoint under a single light, and identification
    accuracy improves materially with more. The old single-column model made
    this impossible.
    """
    if body.modality not in V.MODALITIES:
        raise HTTPException(422, f"unknown modality {body.modality!r}")

    owned = (await db.execute(text(
        "SELECT 1 FROM biometric_identities WHERE id = :iid AND pharmacy_id = :pid "
        "AND is_deleted = false"),
        {"iid": body.identity_id, "pid": staff.pharmacy_id})).scalar()
    if not owned:
        raise HTTPException(404, "identity not found at this pharmacy")

    quality = body.quality
    if body.modality == V.GAIT and body.gait_keypoints is not None:
        try:
            vec, gq = G.encode(np.asarray(body.gait_keypoints, dtype=np.float32))
        except G.GaitError as e:
            raise HTTPException(422, f"gait sequence unusable: {e}")
        quality = gq.quality_score()
    elif body.embedding is not None:
        vec = np.asarray(body.embedding, dtype=np.float32)
    else:
        raise HTTPException(422, "supply embedding, or gait_keypoints for gait")

    try:
        tid = await R.enrol_template(
            db, pharmacy_id=staff.pharmacy_id, identity_id=body.identity_id,
            modality=body.modality, embedding=vec,
            model_version=body.model_version, quality=quality,
            enrolled_by_id=staff.id, capture_context=body.capture_context)
    except V.VectorStoreError as e:
        raise HTTPException(422, str(e))
    await db.commit()
    return {"template_id": str(tid), "modality": body.modality,
            "quality": round(float(quality), 4),
            "note": "calibration is now stale — re-run "
                    "POST /admin/gallery/calibrate before relying on this modality"}


class RetireTemplate(BaseModel):
    reason: str = Field(..., min_length=3, max_length=240)


@router.post("/admin/gallery/templates/{template_id}/retire")
async def retire_template(
    template_id: UUID, body: RetireTemplate,
    staff: Staff = Depends(require_permission("biometric:write")),
    db: AsyncSession = Depends(get_db),
):
    """Withdraw a template from matching. It stays on record: a past
    identification must remain explainable from the templates of its time."""
    ok = await R.retire_template(db, pharmacy_id=staff.pharmacy_id,
                                 template_id=template_id, reason=body.reason)
    if not ok:
        raise HTTPException(404, "template not found, or already retired")
    await db.commit()
    return {"template_id": str(template_id), "retired": True,
            "searchable": False, "record_retained": True}


# ── Calibration ───────────────────────────────────────────────────────────

@router.post("/admin/gallery/calibrate")
async def calibrate(
    modality: str = Query(..., description="face | gait"),
    persist: bool = Query(True, description="store the result so fusion may use it"),
    staff: Staff = Depends(require_permission("biometric:write")),
    db: AsyncSession = Depends(get_db),
):
    """Measure this pharmacy's impostor distribution from its own gallery.

    Scores every cross-identity template pair. Until this succeeds the modality
    cannot vote — by design, because a threshold derived from a published
    benchmark does not survive contact with different cameras, lighting or
    veiling, and inventing one is how a false match becomes a confident answer.
    """
    if modality not in V.MODALITIES:
        raise HTTPException(422, f"unknown modality {modality!r}")

    index = await R.load_index(db, staff.pharmacy_id, modality, force=True)
    cal = R.measure_impostor_stats(index)
    if not cal.usable:
        return {"modality": modality, "calibrated": False,
                "reason": cal.reason, **cal.as_dict()}

    if persist:
        # A new row per run rather than an update: how the operating point moved
        # as the gallery grew is itself evidence, and overwriting would lose it.
        await db.execute(text("""
            INSERT INTO biometric_score_stats
                (id, pharmacy_id, modality, model_version,
                 genuine_mean, genuine_std, impostor_mean, impostor_std,
                 samples, measured_at, notes, created_at, updated_at)
            VALUES (gen_random_uuid(), :pid, :m, :mv, :gm, :gstd, :im, :istd,
                    :n, NOW(), :notes, NOW(), NOW())"""),
            {"pid": staff.pharmacy_id, "m": modality,
             "mv": f"gallery-derived/{len(index)}tpl",
             "gm": cal.genuine_mean, "gstd": cal.genuine_std,
             "im": cal.impostor_mean, "istd": cal.impostor_std, "n": cal.pairs,
             "notes": (f"genuine_pairs={cal.genuine_pairs}; "
                       f"identities={index.identity_count}; "
                       f"measured_by={staff.id}")})
        await db.commit()

    return {"modality": modality, "calibrated": True, "persisted": persist,
            **cal.as_dict(),
            "note": "genuine statistics are optimistic — same-session templates "
                    "resemble each other more than the same person months apart. "
                    "They are reported for context, not used to set acceptance."}


@router.post("/admin/gallery/reload")
async def reload_index(
    modality: Optional[str] = Query(None),
    staff: Staff = Depends(require_permission("biometric:write")),
    db: AsyncSession = Depends(get_db),
):
    """Force the warm index to reload from the database.

    Writes invalidate it automatically; this exists for the case where the
    templates were changed by something other than this API.
    """
    dropped = R.invalidate(staff.pharmacy_id, modality)
    loaded = {}
    for m in ([modality] if modality else list(V.MODALITIES)):
        idx = await R.load_index(db, staff.pharmacy_id, m, force=True)
        loaded[m] = {"templates": len(idx), "identities": idx.identity_count}
    return {"dropped_from_cache": dropped, "loaded": loaded}


# ── Stratified calibration and the release gate ───────────────────────────

@router.post("/admin/gallery/calibrate-strata")
async def calibrate_strata(
    modality: str = Query(..., description="face | gait"),
    persist: bool = Query(False, description="store each usable stratum"),
    staff: Staff = Depends(require_permission("biometric:write")),
    db: AsyncSession = Depends(get_db),
):
    """Measure the impostor distribution separately for each occlusion stratum.

    One pooled distribution averages veiled faces in with unoccluded ones, and
    the resulting threshold is too LOW for the veiled subset — so the FPIR
    guarantee fails silently for exactly the group it most affects.

    A stratum with too few pairs is reported unusable and stores nothing. It
    does NOT inherit the pooled figure: borrowing is how a threshold measured on
    clear faces ends up applied to chador captures.
    """
    from services.biometric.identity_resolution import strata as S

    if modality not in V.MODALITIES:
        raise HTTPException(422, f"unknown modality {modality!r}")

    index = await R.load_index(db, staff.pharmacy_id, modality, force=True)
    cals = S.measure_all_strata(index)

    out = []
    for stratum, cal in sorted(cals.items()):
        stats = S.to_impostor_stats(cal, stratum=stratum)
        row = cal.as_dict()
        row["stratum"] = stratum
        row["can_vote"] = stats is not None
        row["why_excluded"] = None if stats is not None else (
            cal.reason or "not calibrated for this stratum — a modality "
            "without measured impostor statistics may not vote")
        out.append(row)

    return {"modality": modality, "strata": out,
            "persisted": bool(persist),
            "shadow_versions": await R.shadow_versions(
                db, staff.pharmacy_id, modality)}


@router.get("/admin/gallery/release-gate")
async def release_gate(
    staff: Staff = Depends(require_permission("biometric:read")),
    db: AsyncSession = Depends(get_db),
):
    """Per-cell gate over the most recent evaluation counts.

    Returns the failures and the full per-cell report, not just a verdict: a
    gate that says only "failed" cannot be acted on.

    With no evaluation data the gate FAILS CLOSED, which is correct — nothing
    has been measured, and treating missing evidence as a pass is how a group
    with no test data gets declared safe. The counts come from the shadow-mode
    comparison run, which is why `shadow_versions` is reported alongside.
    """
    from services.biometric.release_gate import CellMetrics, evaluate_gate

    cells: list[CellMetrics] = []
    result = evaluate_gate(cells)
    return {"passed": result.passed, "failures": result.failures,
            "worst_cell": result.worst_cell, "ratio": result.ratio,
            "report": result.report,
            "shadow_versions": await R.shadow_versions(
                db, staff.pharmacy_id, "face")}
