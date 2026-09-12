"""The gallery admin surface, end-to-end against a real database.

The claim this file has to substantiate is the one the expansion was built for:
that fusing a second modality makes identification measurably better, and that
the gallery refuses to answer confidently when it has no right to.

Skipped when the disposable test database is unreachable.
"""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import numpy as np
import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.biometric.identity_resolution import repository as R
from services.biometric.identity_resolution import vector_store as V
from services.platform.routers import biometric_admin as BA


def _test_db_url() -> str | None:
    if os.getenv("TEST_DATABASE_URL"):
        return os.environ["TEST_DATABASE_URL"]
    dotenv = Path(__file__).resolve().parents[2] / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            m = re.match(r"^DATABASE_URL=(.+)$", line.strip())
            if m:
                return re.sub(r"/pharmpilot$", "/pharmpilot_test", m.group(1))
    return None


URL = _test_db_url()
pytestmark = [pytest.mark.asyncio,
              pytest.mark.skipif(not URL, reason="no test database configured")]

N_IDENTITIES = 60          # 60 × 3 templates ⇒ 15,930 impostor pairs > the 1000 floor
TEMPLATES_EACH = 3


class FakeStaff:
    def __init__(self, pharmacy_id, staff_id):
        self.pharmacy_id, self.id = pharmacy_id, staff_id


def _unit(rng: np.random.Generator, dim: int) -> np.ndarray:
    v = rng.normal(size=dim).astype(np.float32)
    return (v / np.linalg.norm(v)).astype(np.float32)


def _at_cosine(base: np.ndarray, target: float, rng: np.random.Generator) -> np.ndarray:
    """A vector at exactly `target` cosine from `base`.

    Perturbing by adding per-component noise does not work here: a unit vector
    in 512 dimensions has components of order 1/sqrt(512) ~= 0.044, so a noise
    scale chosen by eye swamps the signal entirely and every "same person"
    probe lands in the impostor distribution. Constructing along an orthogonal
    direction sets the similarity exactly, which is what the test means to say.
    """
    perp = rng.normal(size=base.shape[0]).astype(np.float32)
    perp -= float(perp @ base) * base
    perp /= np.linalg.norm(perp)
    v = target * base + float(np.sqrt(max(0.0, 1.0 - target ** 2))) * perp
    return (v / np.linalg.norm(v)).astype(np.float32)


# Same-person, different-session similarity. Face is the looser of the two:
# expression, angle and lighting move it more than stride does.
GENUINE_FACE_COS = 0.72
GENUINE_GAIT_COS = 0.80


def face_vec(person: int, capture: int = 0) -> list[float]:
    base = _unit(np.random.default_rng(person), 512)
    if capture == 0:
        return base.tolist()
    return _at_cosine(base, GENUINE_FACE_COS,
                      np.random.default_rng(person * 1000 + capture)).tolist()


def gait_vec(person: int, capture: int = 0) -> list[float]:
    base = _unit(np.random.default_rng(person + 500_000), 64)
    if capture == 0:
        return base.tolist()
    return _at_cosine(base, GENUINE_GAIT_COS,
                      np.random.default_rng(person * 977 + capture)).tolist()


@pytest.fixture
async def gallery():
    """A calibrated gallery of 60 people on both modalities."""
    engine = create_async_engine(URL)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        pid = (await db.execute(text("SELECT id FROM pharmacies LIMIT 1"))).scalar()
        if not pid:
            pid = uuid.uuid4()
            await db.execute(text(
                "INSERT INTO pharmacies (id,name,npi,address_line1,city,state,"
                "zip_code,phone,created_at,updated_at) VALUES "
                "(:i,'T','1','a','b','c','d','e',now(),now())"), {"i": pid})
        for t in ("biometric_score_stats", "biometric_templates", "biometric_identities"):
            await db.execute(text(f"DELETE FROM {t} WHERE pharmacy_id = :p"), {"p": pid})
        await db.commit()
        R.invalidate(pid)

        staff = FakeStaff(pid, uuid.uuid4())
        ids = []
        for _ in range(N_IDENTITIES):
            iid = uuid.uuid4()
            ids.append(iid)
            await db.execute(text("""
                INSERT INTO biometric_identities
                  (id,pharmacy_id,identity_class,confidence_score,first_seen_at,
                   last_seen_at,total_visits,is_watchlist_match,
                   consent_security_monitoring,consent_patient_services,
                   created_at,updated_at,is_deleted)
                VALUES (:i,:p,'staff_member',0.9,now(),now(),1,false,true,false,
                        now(),now(),false)"""), {"i": iid, "p": pid})
        await db.commit()

        for person, iid in enumerate(ids):
            for cap in range(TEMPLATES_EACH):
                await R.enrol_template(db, pharmacy_id=pid, identity_id=iid,
                                       modality=V.FACE,
                                       embedding=np.asarray(face_vec(person, cap), dtype=np.float32),
                                       model_version="arcface-r100", quality=0.9)
                await R.enrol_template(db, pharmacy_id=pid, identity_id=iid,
                                       modality=V.GAIT,
                                       embedding=np.asarray(gait_vec(person, cap), dtype=np.float32),
                                       model_version="skeleton-v1", quality=0.85)
        await db.commit()
        yield db, staff, ids
    await engine.dispose()


# ── calibration is the gate ───────────────────────────────────────────────

async def test_an_uncalibrated_modality_may_not_vote_however_strong_the_match(gallery):
    """A perfect similarity is still not an identification if we cannot say how
    often an impostor scores that high."""
    db, staff, _ = gallery
    out = await BA.search_gallery(
        BA.GalleryQuery(face_embedding=face_vec(7, capture=0), top_k=3),
        staff=staff, db=db)
    assert out["decision"] == "no_match"
    assert out["contributions"] == []
    assert any("calibration" in e["reason"] for e in out["excluded"])
    # ...even though the raw similarity was a perfect self-match
    assert out["per_modality_hits"]["face"][0]["similarity"] == pytest.approx(1.0, abs=1e-3)


async def test_calibration_is_measured_from_this_gallery_not_assumed(gallery):
    db, staff, _ = gallery
    out = await BA.calibrate(modality=V.FACE, persist=True, staff=staff, db=db)
    assert out["calibrated"] is True
    # 60 identities × 3 templates → C(180,2) − 60·C(3,2) = 15,930 cross pairs
    assert out["pairs"] == 15_930
    assert out["genuine_pairs"] == 180
    # impostor spread for 512-d unit vectors ≈ 1/sqrt(512) ≈ 0.044
    assert out["impostor_std"] == pytest.approx(0.044, abs=0.01)
    assert out["genuine_mean"] > out["impostor_mean"] + 10 * out["impostor_std"]
    # Derived, not guessed: of the three enrolment templates, two sit at
    # GENUINE_FACE_COS from the base and the third pair (the two perturbed ones)
    # lands at roughly its square, since their offsets are independent.
    expected = (2 * GENUINE_FACE_COS + GENUINE_FACE_COS ** 2) / 3
    assert out["genuine_mean"] == pytest.approx(expected, abs=0.05)


async def test_a_gallery_too_small_to_estimate_a_tail_is_refused(gallery):
    """Below 1000 mismatched pairs the tail estimate is noise, and the tail is
    the whole operating point."""
    db, staff, ids = gallery
    idx = await R.load_index(db, staff.pharmacy_id, V.FACE, force=True)
    small = V.ModalityIndex(modality=V.FACE, dim=512)
    small.load([{"identity_id": ids[i], "template_id": uuid.uuid4(),
                 "embedding": np.asarray(face_vec(i), dtype=np.float32),
                 "quality": 0.9} for i in range(10)])
    cal = R.measure_impostor_stats(small)
    assert cal.usable is False and "too few" in cal.reason
    assert len(idx) == N_IDENTITIES * TEMPLATES_EACH


# ── the accuracy claim ────────────────────────────────────────────────────

async def test_one_modality_alone_never_auto_matches(gallery):
    """A single biometric proposes a candidate; it does not establish identity.
    This is the cap that makes the second modality worth having."""
    db, staff, _ = gallery
    await BA.calibrate(modality=V.FACE, persist=True, staff=staff, db=db)
    out = await BA.search_gallery(
        BA.GalleryQuery(face_embedding=face_vec(7, capture=9), top_k=3),
        staff=staff, db=db)
    assert out["decision"] == "review"
    assert len(out["contributions"]) == 1
    assert "single biometric" in out["explanation"]


async def test_face_and_gait_together_identify_where_face_alone_only_proposes(gallery):
    """The expansion's whole purpose, stated as a test: the same probe that one
    modality could only refer for review is resolved when a second, independent
    modality agrees."""
    db, staff, ids = gallery
    await BA.calibrate(modality=V.FACE, persist=True, staff=staff, db=db)
    await BA.calibrate(modality=V.GAIT, persist=True, staff=staff, db=db)

    person = 11
    face_only = await BA.search_gallery(
        BA.GalleryQuery(face_embedding=face_vec(person, capture=4), top_k=3),
        staff=staff, db=db)
    both = await BA.search_gallery(
        BA.GalleryQuery(face_embedding=face_vec(person, capture=4),
                        gait_embedding=gait_vec(person, capture=4), top_k=3),
        staff=staff, db=db)

    assert face_only["decision"] == "review"
    assert both["decision"] == "auto"
    assert both["identity_id"] == str(ids[person])
    assert {c["modality"] for c in both["contributions"]} == {V.FACE, V.GAIT}
    assert both["confidence"] >= face_only["confidence"]


async def test_an_impostor_is_never_auto_matched_on_either_path(gallery):
    db, staff, _ = gallery
    await BA.calibrate(modality=V.FACE, persist=True, staff=staff, db=db)
    await BA.calibrate(modality=V.GAIT, persist=True, staff=staff, db=db)
    out = await BA.search_gallery(
        BA.GalleryQuery(face_embedding=face_vec(9_999),
                        gait_embedding=gait_vec(8_888), top_k=3),
        staff=staff, db=db)
    assert out["decision"] != "auto"


async def test_the_search_always_shows_its_working(gallery):
    """An identification the owner cannot interrogate is one they cannot defend."""
    db, staff, _ = gallery
    await BA.calibrate(modality=V.FACE, persist=True, staff=staff, db=db)
    out = await BA.search_gallery(
        BA.GalleryQuery(face_embedding=face_vec(3, capture=2), top_k=5),
        staff=staff, db=db)
    assert out["explanation"]
    assert out["gallery_size"] == N_IDENTITIES
    assert len(out["per_modality_hits"][V.FACE]) == 5
    for h in out["per_modality_hits"][V.FACE]:
        assert {"identity_id", "similarity", "template_id", "template_quality"} <= h.keys()


# ── probes that must be refused rather than guessed ───────────────────────

async def test_an_unusable_gait_clip_says_so_instead_of_asking_for_one(gallery):
    """Collapsing 'you sent nothing' into 'what you sent was unusable' would
    have the caller re-send the same clip forever."""
    db, staff, _ = gallery
    kp = np.random.default_rng(1).normal(size=(4, 17, 3)).astype(np.float32)
    kp[:, :, 2] = 0.9
    with pytest.raises(HTTPException) as e:
        await BA.search_gallery(BA.GalleryQuery(gait_keypoints=kp.tolist()),
                                staff=staff, db=db)
    assert e.value.status_code == 422
    assert "unusable" in str(e.value.detail) and "12" in str(e.value.detail)


async def test_a_wrong_dimension_probe_is_rejected_at_the_door(gallery):
    db, staff, _ = gallery
    with pytest.raises(HTTPException) as e:
        await BA.search_gallery(BA.GalleryQuery(face_embedding=[0.1] * 128),
                                staff=staff, db=db)
    assert e.value.status_code == 422 and "512" in str(e.value.detail)


async def test_an_empty_probe_is_rejected(gallery):
    db, staff, _ = gallery
    with pytest.raises(HTTPException) as e:
        await BA.search_gallery(BA.GalleryQuery(), staff=staff, db=db)
    assert e.value.status_code == 422


# ── enrolment, retirement, tenancy ────────────────────────────────────────

async def test_enrolment_is_confined_to_the_caller_s_pharmacy(gallery):
    db, staff, _ = gallery
    stranger = uuid.uuid4()
    with pytest.raises(HTTPException) as e:
        await BA.enrol_template(BA.EnrolTemplate(
            identity_id=stranger, modality=V.FACE, model_version="x",
            embedding=face_vec(1)), staff=staff, db=db)
    assert e.value.status_code == 404


async def test_retiring_a_template_removes_it_from_matching_but_keeps_the_record(gallery):
    db, staff, ids = gallery
    detail = await BA.identity_detail(ids[0], staff=staff, db=db)
    tid = uuid.UUID(detail["templates"][0]["template_id"])
    before = len(await R.load_index(db, staff.pharmacy_id, V.FACE))

    out = await BA.retire_template(tid, BA.RetireTemplate(reason="poor capture"),
                                   staff=staff, db=db)
    assert out["searchable"] is False and out["record_retained"] is True
    after = len(await R.load_index(db, staff.pharmacy_id, V.FACE))
    assert after == before - 1

    again = await BA.identity_detail(ids[0], staff=staff, db=db)
    row = next(t for t in again["templates"] if t["template_id"] == str(tid))
    assert row["active"] is False and row["retired_reason"] == "poor capture"


async def test_retiring_the_same_template_twice_is_refused(gallery):
    db, staff, ids = gallery
    detail = await BA.identity_detail(ids[1], staff=staff, db=db)
    tid = uuid.UUID(detail["templates"][0]["template_id"])
    await BA.retire_template(tid, BA.RetireTemplate(reason="first"), staff=staff, db=db)
    with pytest.raises(HTTPException) as e:
        await BA.retire_template(tid, BA.RetireTemplate(reason="again"),
                                 staff=staff, db=db)
    assert e.value.status_code == 404


async def test_a_write_invalidates_the_warm_index(gallery):
    """Otherwise a freshly enrolled person stays invisible until a restart."""
    db, staff, ids = gallery
    await R.load_index(db, staff.pharmacy_id, V.FACE)
    assert any(c["modality"] == V.FACE for c in R.cache_state())
    await R.enrol_template(db, pharmacy_id=staff.pharmacy_id, identity_id=ids[0],
                           modality=V.FACE,
                           embedding=np.asarray(face_vec(0, 7), dtype=np.float32),
                           model_version="arcface-r100", quality=0.8)
    assert not any(c["modality"] == V.FACE and c["pharmacy_id"] == str(staff.pharmacy_id)
                   for c in R.cache_state())


# ── browse and stats ──────────────────────────────────────────────────────

async def test_stats_report_whether_each_modality_may_actually_vote(gallery):
    db, staff, _ = gallery
    before = await BA.gallery_stats(staff=staff, db=db)
    assert before["modalities"][V.FACE]["can_vote"] is False
    assert "calibrate" in before["modalities"][V.FACE]["why_excluded"]

    await BA.calibrate(modality=V.FACE, persist=True, staff=staff, db=db)
    after = await BA.gallery_stats(staff=staff, db=db)
    assert after["modalities"][V.FACE]["can_vote"] is True
    assert after["modalities"][V.FACE]["active_templates"] == N_IDENTITIES * TEMPLATES_EACH
    assert after["modalities"][V.GAIT]["can_vote"] is False   # not calibrated yet


async def test_browse_filters_by_modality_and_paginates(gallery):
    db, staff, _ = gallery
    page = await BA.browse_gallery(q=None, modality=V.GAIT, linked=None,
                                   limit=10, offset=0, staff=staff, db=db)
    assert page["total"] == N_IDENTITIES and len(page["identities"]) == 10
    row = page["identities"][0]
    assert set(row["enrolled_modalities"]) == {V.FACE, V.GAIT}
    assert row["face_templates"] == TEMPLATES_EACH


async def test_identity_detail_never_returns_the_embeddings(gallery):
    """Template metadata is administrable; the vectors themselves are not an
    API response."""
    db, staff, ids = gallery
    detail = await BA.identity_detail(ids[2], staff=staff, db=db)
    assert detail["templates"]
    for t in detail["templates"]:
        assert "embedding" not in t
    assert "consent" in detail["identity"]


async def test_an_identity_from_another_pharmacy_is_not_visible(gallery):
    db, staff, _ = gallery
    with pytest.raises(HTTPException) as e:
        await BA.identity_detail(uuid.uuid4(), staff=staff, db=db)
    assert e.value.status_code == 404
