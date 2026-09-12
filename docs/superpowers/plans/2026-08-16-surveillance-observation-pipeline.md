# Surveillance Observation Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the already-built identification stack actually observe — receive a capture, classify its occlusion stratum, identify against per-modality galleries, fuse, and persist an explainable observation.

**Architecture:** The identification components exist and are tested in isolation but nothing connects them: `surveillance_observations` is written by zero code paths. This plan builds the spine — occlusion classification that both selects the calibration stratum AND routes to the modalities that survive it, an escalation ladder that ends in a human request rather than a dead end, a per-modality-correct reading builder, an observation recorder, and the ingest endpoints for camera and RF data.

**The governing behaviour:** a covered face is not a failure, it is a routing instruction. Face degraded → lean on periocular, voice and gait. Still unsettled → mint a provisional UUID and ask the counter for the name and national code. The system never shrugs.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, PostgreSQL (127.0.0.1:5433), numpy. No new dependencies — `faiss`, `onnxruntime` and `scikit-image` are absent from this environment and nothing here may require them.

## Global Constraints

- Python interpreter for all commands: `/Users/sashad85/miniforge3/bin/python3`
- Tests run from repo root: `/Users/sashad85/miniforge3/bin/python3 -m pytest`
- No new third-party dependencies. numpy only.
- Every new route MUST carry an auth dependency or be added to the allowlist in `tests/unit/test_route_authentication.py` with a written reason.
- No biometric similarity value, including 1.0, may promote an identity above IAL1. Fusion proposes; it never asserts.
- An uncalibrated modality must not vote. `stats=None` means excluded, by design.
- A stored coordinate MUST carry `rf_uncertainty_m` — enforced by CHECK constraint `ck_surv_obs_rf_uncertainty_required`.
- Migration head is `0045` (twelve migrations landed after this plan was drafted). Task 2 adds `0046`; any later migration chains from that.
- Persian/RTL for user-facing strings; internal identifiers stay English.

---

## What already exists (do not rebuild)

| Component | Path | State |
|---|---|---|
| Calibration + log-λ tail | `services/biometric/identity_resolution/thresholds.py` | Tested |
| Per-modality vector index | `services/biometric/identity_resolution/vector_store.py` | Tested |
| Template enrol/retire, impostor measurement | `services/biometric/identity_resolution/repository.py` | Tested |
| Face engine (margin, liveness fail-closed) | `services/biometric/identity_resolution/engine.py` | Tested |
| Gait encoder | `services/biometric/gait/encoder.py` | Tested |
| Fusion engine | `services/biometric/fusion/__init__.py` | Tested |
| RF positioning | `services/core/rf_mapping/__init__.py` | Tested |
| Evidence vault | `services/biometric/evidence_vault/vault.py` | Built |
| `surveillance_observations` table | migration `0032` | **Applied, written by nothing** |

## File Structure

| File | Responsibility |
|---|---|
| `services/biometric/occlusion.py` (create) | Landmark-visibility → occlusion stratum, AND which modalities survive it. |
| `services/core/surveillance/escalation.py` (create) | The ladder that ends in a human action instead of a dead end. |
| `data/migrations/versions/0046_identify_manually.py` (create) | Extend `ck_surv_obs_fusion_decision` to admit the new outcome. |
| `services/biometric/identity_resolution/vector_store.py` (modify) | `to_readings` must take per-modality gallery sizes, not one shared value. |
| `services/core/surveillance/recorder.py` (create) | Persist a `FusedIdentity` + optional `PositionFix` as one observation row. |
| `services/platform/routers/surveillance.py` (create) | Ingest endpoints for capture observations and RSSI batches; heatmap query. |
| `services/platform/main.py` (modify) | Register the router. |
| `tests/unit/test_occlusion.py` (create) | Stratum classification. |
| `tests/unit/test_surveillance_escalation.py` (create) | The human step, and that a hint is never named in the prompt. |
| `tests/unit/test_surveillance_recorder.py` (create) | Persistence shape and invariants. |
| `tests/unit/test_surveillance_ingest.py` (create) | Endpoint behaviour and auth. |

---

### Task 1: Occlusion stratum classifier

**Why this is first:** two things depend on knowing the stratum, and neither exists.

*Calibration selection.* The design calls for a separate `ImpostorStats` per stratum, but **nothing computes the stratum**, so a single pooled calibration silently returns a threshold too low for veiled faces — failing the FPIR guarantee for exactly the group it most affects.

*Modality routing.* Occlusion is not only a reason to trust face less — it is the signal to lean on what survives. A mask leaves the eyes, so **periocular** is the designated survivor; a chador leaves voice and gait untouched. The stratum must therefore declare which modalities to weight up, so the system analyses the survivors rather than shrugging at a covered face. Note that `periocular` is currently a modality nowhere in the codebase — it appears only in design prose — so this task registers it.

**The ML choice:** classify from **landmark visibility**, not a trained classifier. The face pipeline already produces 5 landmarks with confidences (`services/biometric/face_pipeline/aligner.py` fits a similarity transform to eye centres, nose tip, mouth corners). A surgical mask destroys nose-tip and both mouth corners; a scarf removes forehead/ear context and drops overall detection confidence. Reading the landmarks we already have is deterministic, explainable, needs no training data, no model download, and no GPU — and it degrades to `UNKNOWN` rather than guessing.

**Files:**
- Create: `services/biometric/occlusion.py`
- Test: `tests/unit/test_occlusion.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `classify_occlusion(landmark_conf: np.ndarray, detect_score: float) -> OcclusionStratum`, where `OcclusionStratum` is a `str`-valued enum with members `CLEAR="clear"`, `MASK="mask"`, `SCARF="scarf"`, `SCARF_MASK="scarf_mask"`, `UNKNOWN="unknown"`. Also `STRATUM_LANDMARKS: dict[str, tuple[int, ...]]` naming which of the 5 landmark indices each stratum expects to lose, and `SURVIVING_MODALITIES: dict[str, tuple[str, ...]]` naming which modalities to lean on per stratum. Modifies `services/biometric/fusion/__init__.py` to add a `periocular` entry to `FUSION_FLOORS`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_occlusion.py`:

```python
"""Occlusion stratum from landmark visibility.

The per-stratum calibration design needs to know WHICH stratum a probe is in
before it can pick the right ImpostorStats. Nothing computed that. This does it
from the 5 landmarks the aligner already produces — no model, no training data.

Landmark order is the ArcFace convention:
    0 = left eye, 1 = right eye, 2 = nose tip, 3 = left mouth, 4 = right mouth
"""
from __future__ import annotations

import numpy as np
import pytest

from services.biometric.occlusion import (
    OcclusionStratum, STRATUM_LANDMARKS, classify_occlusion)


def conf(left_eye=0.95, right_eye=0.95, nose=0.95, mouth_l=0.95, mouth_r=0.95):
    return np.array([left_eye, right_eye, nose, mouth_l, mouth_r], dtype=np.float32)


def test_all_landmarks_visible_is_clear():
    assert classify_occlusion(conf(), detect_score=0.95) == OcclusionStratum.CLEAR


def test_lost_mouth_and_nose_is_a_mask():
    """A surgical mask destroys three of the five alignment landmarks."""
    got = classify_occlusion(conf(nose=0.15, mouth_l=0.10, mouth_r=0.12),
                             detect_score=0.90)
    assert got == OcclusionStratum.MASK


def test_low_detection_score_with_intact_mouth_is_a_scarf():
    """A headscarf leaves the mouth but removes hair, ears and jawline context,
    which shows up as a depressed overall detection score."""
    got = classify_occlusion(conf(), detect_score=0.55)
    assert got == OcclusionStratum.SCARF


def test_scarf_and_mask_together():
    got = classify_occlusion(conf(nose=0.12, mouth_l=0.09, mouth_r=0.11),
                             detect_score=0.52)
    assert got == OcclusionStratum.SCARF_MASK


def test_unusable_landmarks_are_unknown_not_guessed():
    """Refusing to classify is safer than guessing: an UNKNOWN stratum has no
    calibration, so fusion excludes the reading rather than scoring it against
    the wrong impostor distribution."""
    got = classify_occlusion(conf(0.05, 0.05, 0.05, 0.05, 0.05), detect_score=0.2)
    assert got == OcclusionStratum.UNKNOWN


def test_wrong_landmark_count_raises():
    with pytest.raises(ValueError, match="5 landmark"):
        classify_occlusion(np.array([0.9, 0.9], dtype=np.float32), detect_score=0.9)


def test_every_stratum_declares_which_landmarks_it_loses():
    for stratum in ("mask", "scarf", "scarf_mask"):
        assert stratum in STRATUM_LANDMARKS
    assert STRATUM_LANDMARKS["mask"] == (2, 3, 4)


def test_stratum_values_match_the_calibration_key_vocabulary():
    """These strings become the ImpostorStats.population key, so they are a
    stored vocabulary, not a display label."""
    assert OcclusionStratum.CLEAR.value == "clear"
    assert OcclusionStratum.SCARF_MASK.value == "scarf_mask"


def test_every_stratum_names_the_modalities_that_survive_it():
    """Occlusion is not only a reason to trust face less — it says what to lean
    on instead. A covered face must route to the survivors, not shrug."""
    from services.biometric.occlusion import SURVIVING_MODALITIES

    for stratum in OcclusionStratum:
        assert stratum.value in SURVIVING_MODALITIES, stratum
        assert SURVIVING_MODALITIES[stratum.value], f"{stratum} names no survivor"


def test_a_mask_routes_to_periocular_because_the_eyes_remain():
    from services.biometric.occlusion import SURVIVING_MODALITIES

    survivors = SURVIVING_MODALITIES["mask"]
    assert "periocular" in survivors
    assert "face" not in survivors        # the lower face is gone


def test_a_chador_routes_to_voice_and_gait():
    """Full-body covering leaves speech and movement untouched."""
    from services.biometric.occlusion import SURVIVING_MODALITIES

    survivors = SURVIVING_MODALITIES["scarf_mask"]
    assert "voice" in survivors and "gait" in survivors


def test_periocular_is_a_registered_fusion_modality():
    """It appeared only in design prose. A survivor the fusion engine does not
    recognise would be excluded as an unknown modality."""
    from services.biometric.fusion import FUSION_FLOORS

    assert "periocular" in FUSION_FLOORS
    assert 0.0 < FUSION_FLOORS["periocular"].min_quality <= 1.0


def test_every_survivor_named_anywhere_is_a_real_fusion_modality():
    from services.biometric.fusion import FUSION_FLOORS
    from services.biometric.occlusion import SURVIVING_MODALITIES

    for stratum, survivors in SURVIVING_MODALITIES.items():
        for m in survivors:
            assert m in FUSION_FLOORS, f"{stratum} names unknown modality {m}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_occlusion.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.biometric.occlusion'`

- [ ] **Step 3: Write minimal implementation**

Create `services/biometric/occlusion.py`:

```python
"""Occlusion stratum from landmark visibility.

Why this is not a trained classifier: the face pipeline already fits a
similarity transform to five landmarks, and their confidences carry the
occlusion signal directly. A surgical mask destroys the nose tip and both mouth
corners — three of the five. A headscarf leaves the mouth intact but removes
hair, ears and jawline, which depresses the detector's overall score without
killing individual points. Reading what we already compute is deterministic,
explainable, needs no training data and no GPU, and can say UNKNOWN.

The stratum matters because the calibration is per-stratum: a single pooled
ImpostorStats returns a threshold that is too LOW for veiled faces, so the FPIR
guarantee silently fails for exactly the group it most affects.
"""
from __future__ import annotations

from enum import Enum

import numpy as np

# ArcFace landmark order: left eye, right eye, nose tip, left mouth, right mouth
LM_LEFT_EYE, LM_RIGHT_EYE, LM_NOSE, LM_MOUTH_L, LM_MOUTH_R = range(5)

# Which landmarks each stratum is expected to lose. Declared rather than
# implied so a reviewer can check the rule against a photograph.
STRATUM_LANDMARKS: dict[str, tuple[int, ...]] = {
    "mask": (LM_NOSE, LM_MOUTH_L, LM_MOUTH_R),
    "scarf": (),          # scarf takes context, not individual points
    "scarf_mask": (LM_NOSE, LM_MOUTH_L, LM_MOUTH_R),
}

# A landmark below this is not located.
LANDMARK_VISIBLE = 0.40
# Below this the detector is struggling with context loss — the scarf signature.
DETECT_CONTEXT_OK = 0.70
# Below this nothing is trustworthy enough to stratify.
DETECT_USABLE = 0.30


class OcclusionStratum(str, Enum):
    CLEAR = "clear"
    MASK = "mask"
    SCARF = "scarf"
    SCARF_MASK = "scarf_mask"
    UNKNOWN = "unknown"


# What to lean on when the face is compromised. This is the half of occlusion
# handling that is easy to miss: the stratum is not only a reason to distrust
# the face, it is the instruction for which other evidence to gather. A mask
# leaves the eyes, so periocular is the designated survivor. A full-body
# covering leaves speech and movement entirely untouched.
SURVIVING_MODALITIES: dict[str, tuple[str, ...]] = {
    "clear":      ("face", "periocular", "voice", "gait"),
    "mask":       ("periocular", "voice", "gait"),
    "scarf":      ("face", "periocular", "voice", "gait"),
    "scarf_mask": ("periocular", "voice", "gait"),
    # Nothing about the face is trustworthy, but a person still walks and
    # speaks. Never an empty tuple: an empty survivor set is how a covered face
    # becomes a silent dead end.
    "unknown":    ("voice", "gait"),
}


def classify_occlusion(landmark_conf: np.ndarray,
                       detect_score: float) -> OcclusionStratum:
    """Which calibration stratum this probe belongs to."""
    conf = np.asarray(landmark_conf, dtype=np.float32).reshape(-1)
    if conf.shape[0] != 5:
        raise ValueError(f"expected 5 landmark confidences, got {conf.shape[0]}")

    eyes_visible = (conf[LM_LEFT_EYE] >= LANDMARK_VISIBLE
                    and conf[LM_RIGHT_EYE] >= LANDMARK_VISIBLE)
    if not eyes_visible or detect_score < DETECT_USABLE:
        # Without the eyes there is no periocular signal either, so there is
        # nothing to stratify. Say so rather than guess: an UNKNOWN stratum has
        # no calibration, and fusion excludes an uncalibrated reading.
        return OcclusionStratum.UNKNOWN

    lower_lost = sum(1 for i in STRATUM_LANDMARKS["mask"]
                     if conf[i] < LANDMARK_VISIBLE)
    masked = lower_lost >= 2
    scarfed = detect_score < DETECT_CONTEXT_OK

    if masked and scarfed:
        return OcclusionStratum.SCARF_MASK
    if masked:
        return OcclusionStratum.MASK
    if scarfed:
        return OcclusionStratum.SCARF
    return OcclusionStratum.CLEAR
```

- [ ] **Step 4: Register periocular as a fusion modality**

Add to `FUSION_FLOORS` in `services/biometric/fusion/__init__.py`, after the `"face"` entry:

```python
    "periocular": ModalityFloor(min_quality=0.40, reliability=0.55,
                                note="the eyes survive a surgical mask; weaker "
                                     "than full face and damaged by eyeglasses "
                                     "and heavy eye cosmetics, both common here"),
```

- [ ] **Step 5: Run test to verify it passes**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_occlusion.py tests/unit/test_biometric_fusion.py -q`
Expected: PASS — 13 new plus the existing 14 fusion tests

- [ ] **Step 6: Commit**

```bash
git add services/biometric/occlusion.py services/biometric/fusion/__init__.py tests/unit/test_occlusion.py
git commit -m "feat(biometric): occlusion stratum, and what survives it

The per-stratum calibration design needs to know which stratum a probe is in
before it can select the right ImpostorStats, and nothing computed it. Derived
from the five landmark confidences the aligner already produces rather than a
trained classifier: a mask destroys nose tip and both mouth corners, a scarf
depresses the detector score without killing individual points. Deterministic,
explainable, no training data, and it returns UNKNOWN rather than guessing —
which fusion then excludes.

SURVIVING_MODALITIES is the half that is easy to miss: the stratum is not only
a reason to distrust the face, it is the instruction for which other evidence
to gather. A mask leaves the eyes, so periocular is the designated survivor and
is registered as a fusion modality here — it had appeared only in design prose,
so a survivor the engine did not recognise would have been excluded as unknown.
No stratum maps to an empty survivor set: that is how a covered face becomes a
silent dead end."
```

---

### Task 2: Escalation ladder — never a dead end

**Why:** `fuse()` currently ends a failed identification with `NO_MATCH` and the
explanation *"No modality cleared its quality floor or had a calibration;
nothing to fuse."* That is a dead end. A fully supervising system does not shrug
at a person it cannot name — it mints a provisional identity **and tells the
counter to obtain the name and national code.** Closing that loop through a
human is what turns an unknown visitor into a known one.

**Two safety properties this task encodes:**

*Elicit before comparing.* When weak candidates exist, the prompt must NOT name
them. Showing "possibly Fatemeh Ahmadi (34%)" makes staff ask *"are you Mrs
Ahmadi?"* — a leading question that a polite person answers yes to, which
corrupts the very verification being attempted. The prompt asks for an
independent answer; the hint is compared against it **afterwards**, by the
system, not by the person asking.

*A hint is below IAL1.* Weak candidates are returned for post-hoc comparison
only. They may never open a chart, and the recorder must not store one as
`biometric_identity_id`.

**Files:**
- Create: `services/core/surveillance/escalation.py`
- Create: `data/migrations/versions/0046_identify_manually.py`
- Test: `tests/unit/test_surveillance_escalation.py`

**Interfaces:**
- Consumes: `fusion.FusedIdentity`, `occlusion.OcclusionStratum` and
  `SURVIVING_MODALITIES` (Task 1).
- Produces: `IDENTIFY_MANUALLY = "identify_manually"`; `StaffAction`
  (`prompt_fa: str`, `needs_provisional: bool`, `hint_identity_ids: list[UUID]`,
  `reason: str`); `escalate(fused, stratum=None) -> StaffAction | None` returning
  `None` when the fused result is already actionable.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_surveillance_escalation.py`:

```python
"""When identification fails, the system asks a human — it does not shrug.

fuse() used to end a failed identification with NO_MATCH and no next step. A
supervising system mints a provisional identity and tells the counter to obtain
the name and national code, so the unknown visitor becomes a known one.
"""
from __future__ import annotations

from uuid import uuid4

from services.biometric.fusion import (
    AUTO, NO_MATCH, REVIEW, Contribution, Exclusion, FusedIdentity)
from services.biometric.occlusion import OcclusionStratum
from services.core.surveillance.escalation import (
    IDENTIFY_MANUALLY, StaffAction, escalate)


def fused(decision, identity=None, confidence=0.0, contribs=(), excluded=()):
    return FusedIdentity(
        identity_id=identity, confidence=confidence, decision=decision,
        margin=0.0, contributions=list(contribs), excluded=list(excluded),
        explanation="")


def test_a_confident_identification_needs_no_escalation():
    assert escalate(fused(AUTO, identity=uuid4(), confidence=0.995)) is None


def test_no_match_escalates_to_a_staff_request():
    action = escalate(fused(NO_MATCH))
    assert isinstance(action, StaffAction)
    assert action.needs_provisional is True
    assert "کد ملی" in action.prompt_fa          # asks for the national code
    assert "نام" in action.prompt_fa             # and the name


def test_the_prompt_never_names_a_weak_candidate():
    """Showing a low-confidence name makes staff ask a leading question that a
    polite person answers yes to, corrupting the verification. Elicit first,
    compare afterwards."""
    maybe = uuid4()
    action = escalate(fused(NO_MATCH, identity=maybe, confidence=0.34))
    assert str(maybe) not in action.prompt_fa
    assert maybe in action.hint_identity_ids      # kept, for later comparison


def test_review_escalates_but_does_not_demand_a_provisional_identity():
    """A review already has a candidate worth confirming; it does not need a new
    provisional record, only a human."""
    action = escalate(fused(REVIEW, identity=uuid4(), confidence=0.5))
    assert action is not None
    assert action.needs_provisional is False


def test_an_occluded_failure_says_so_and_names_what_was_tried():
    """The counter deserves to know the face was covered — it changes how they
    ask, and it is the difference between a system fault and a physical one."""
    action = escalate(
        fused(NO_MATCH, excluded=[Exclusion("face", "quality below floor")]),
        stratum=OcclusionStratum.SCARF_MASK)
    assert "پوشیده" in action.prompt_fa or "پوشش" in action.prompt_fa
    assert "voice" in action.reason and "gait" in action.reason


def test_escalation_decision_value_is_the_stored_vocabulary():
    assert IDENTIFY_MANUALLY == "identify_manually"


def test_hints_are_capped_so_the_ui_cannot_become_a_lineup():
    ids = [uuid4() for _ in range(9)]
    action = escalate(fused(NO_MATCH, identity=ids[0]), hint_ids=ids)
    assert len(action.hint_identity_ids) <= 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_surveillance_escalation.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.core.surveillance.escalation'`

- [ ] **Step 3: Write minimal implementation**

Create `services/core/surveillance/escalation.py`:

```python
"""What to do when identification does not settle.

The fusion engine can return NO_MATCH, and that used to be the end of it. A
supervising system does not stop at "unknown visitor": it mints a provisional
identity and asks the counter for the name and national code, which is how an
unknown person becomes a known one.

Two rules are encoded here rather than left to the UI:

ELICIT BEFORE COMPARING. The prompt never names a weak candidate. Showing
"possibly X" makes staff ask "are you X?", a leading question a polite person
answers yes to — which corrupts the verification being attempted. The hint is
returned separately and compared against the independently-obtained answer.

A HINT IS BELOW IAL1. Weak candidates are for post-hoc comparison only. They
never open a chart and are never stored as the observation's identity.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from services.biometric.fusion import AUTO, NO_MATCH

IDENTIFY_MANUALLY = "identify_manually"

# More than a few candidates turns the screen into a lineup and invites the
# staff member to pick a face rather than ask a question.
MAX_HINTS = 3

_ASK = "لطفاً نام و کد ملی مراجعه‌کننده را بپرسید"
_OCCLUDED = "چهره پوشیده است"


@dataclass
class StaffAction:
    prompt_fa: str
    needs_provisional: bool
    reason: str
    hint_identity_ids: list[UUID] = field(default_factory=list)


def escalate(fused, stratum=None, hint_ids: list[UUID] | None = None
             ) -> StaffAction | None:
    """The human step this result needs, or None if it needs none."""
    if fused.decision == AUTO:
        return None

    hints = list(hint_ids) if hint_ids else (
        [fused.identity_id] if fused.identity_id else [])
    hints = [h for h in hints if h is not None][:MAX_HINTS]

    survivors: tuple[str, ...] = ()
    if stratum is not None:
        from services.biometric.occlusion import SURVIVING_MODALITIES
        survivors = SURVIVING_MODALITIES.get(stratum.value, ())

    if fused.decision == NO_MATCH:
        prompt = _ASK
        if survivors:
            prompt = f"{_OCCLUDED} — {_ASK}"
        reason = ("no modality settled on an identity"
                  + (f"; tried {', '.join(survivors)}" if survivors else ""))
        return StaffAction(prompt_fa=prompt, needs_provisional=True,
                           reason=reason, hint_identity_ids=hints)

    # REVIEW: a candidate exists and is worth confirming, so no new provisional
    # record is needed — only a person.
    prompt = _ASK
    if survivors:
        prompt = f"{_OCCLUDED} — {_ASK}"
    reason = ("a candidate was found but not confirmed"
              + (f"; tried {', '.join(survivors)}" if survivors else ""))
    return StaffAction(prompt_fa=prompt, needs_provisional=False,
                       reason=reason, hint_identity_ids=hints)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_surveillance_escalation.py -q`
Expected: PASS — 7 passed

- [ ] **Step 5: Extend the schema CHECK constraint**

`ck_surv_obs_fusion_decision` currently admits only `auto|review|no_match`, so
the new outcome would be rejected by Postgres. Create
`data/migrations/versions/0046_identify_manually.py`:

```python
"""admit identify_manually as a fusion decision

Revision ID: 0046
Revises: 0045
"""
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_surv_obs_fusion_decision",
                       "surveillance_observations", type_="check")
    op.create_check_constraint(
        "ck_surv_obs_fusion_decision", "surveillance_observations",
        "fusion_decision IS NULL OR fusion_decision IN "
        "('auto', 'review', 'no_match', 'identify_manually')")


def downgrade() -> None:
    op.drop_constraint("ck_surv_obs_fusion_decision",
                       "surveillance_observations", type_="check")
    op.create_check_constraint(
        "ck_surv_obs_fusion_decision", "surveillance_observations",
        "fusion_decision IS NULL OR fusion_decision IN "
        "('auto', 'review', 'no_match')")
```

Apply it. `env.py` reads `DATABASE_URL` from `os.environ` only and ignores
`.env`, so it must be loaded explicitly or alembic silently targets a different
database on port 5432:

```bash
set -a; . ./.env; set +a
/Users/sashad85/miniforge3/bin/python3 -m alembic upgrade head
```

Expected: `Running upgrade 0045 -> 0046`

- [ ] **Step 6: Commit**

```bash
git add services/core/surveillance/escalation.py data/migrations/versions/0046_identify_manually.py tests/unit/test_surveillance_escalation.py
git commit -m "feat(surveillance): escalate to a human instead of dead-ending

fuse() ended a failed identification with NO_MATCH and no next step. A
supervising system mints a provisional identity and asks the counter for the
name and national code, which is how an unknown visitor becomes a known one.

Two rules are encoded rather than left to the UI. The prompt never names a weak
candidate: showing 'possibly X' makes staff ask 'are you X?', a leading question
a polite person answers yes to, which corrupts the verification being attempted
— so the hint is returned separately and compared against the independently
obtained answer. And a hint stays below IAL1: it never opens a chart and is
never stored as the observation's identity."
```

---

### Task 3: Per-modality gallery size in `to_readings`

**Why:** `to_readings` takes a single `gallery_size: int = 1` and applies it to every modality (`vector_store.py:151`). But `FPIR ≈ N · FMR` is a **per-modality** relationship, and the face gallery and the gait gallery hold different numbers of people. Passing one shared N means at least one modality's confidence is computed against the wrong denominator — understating risk for the larger gallery.

**Files:**
- Modify: `services/biometric/identity_resolution/vector_store.py:151-173`
- Test: `tests/unit/test_biometric_vector_store.py` (append)

**Interfaces:**
- Consumes: `Hit`, `fusion.ModalityReading` (existing).
- Produces: `to_readings(hits_by_modality, stats_by_modality, quality_by_modality=None, gallery_size=1, gallery_size_by_modality=None)`. When `gallery_size_by_modality` supplies a modality, it wins; otherwise `gallery_size` is the fallback. Backward compatible.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_biometric_vector_store.py`:

```python
def test_to_readings_uses_a_per_modality_gallery_size():
    """FPIR ~= N * FMR is per-modality. The face gallery and the gait gallery
    hold different numbers of people, so one shared N computes at least one
    modality's confidence against the wrong denominator."""
    from uuid import uuid4
    from services.biometric.identity_resolution import vector_store as V

    alice = uuid4()
    hits = {
        "face": [V.Hit(identity_id=alice, similarity=0.7, template_id=uuid4(),
                       modality="face", quality=0.9)],
        "gait": [V.Hit(identity_id=alice, similarity=0.6, template_id=uuid4(),
                       modality="gait", quality=0.8)],
    }
    readings = V.to_readings(
        hits, stats_by_modality={},
        gallery_size_by_modality={"face": 4000, "gait": 120})

    by_mod = {r.modality: r for r in readings}
    assert by_mod["face"].gallery_size == 4000
    assert by_mod["gait"].gallery_size == 120


def test_to_readings_falls_back_to_the_shared_gallery_size():
    from uuid import uuid4
    from services.biometric.identity_resolution import vector_store as V

    hits = {"face": [V.Hit(identity_id=uuid4(), similarity=0.7,
                           template_id=uuid4(), modality="face", quality=0.9)]}
    readings = V.to_readings(hits, stats_by_modality={}, gallery_size=77)
    assert readings[0].gallery_size == 77
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_biometric_vector_store.py -q -k gallery_size`
Expected: FAIL — `TypeError: to_readings() got an unexpected keyword argument 'gallery_size_by_modality'`

- [ ] **Step 3: Write minimal implementation**

Replace the `to_readings` function in `services/biometric/identity_resolution/vector_store.py`:

```python
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
    confidence against the wrong denominator.
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_biometric_vector_store.py tests/unit/test_biometric_fusion.py -q`
Expected: PASS — all existing tests plus the 2 new ones

- [ ] **Step 5: Commit**

```bash
git add services/biometric/identity_resolution/vector_store.py tests/unit/test_biometric_vector_store.py
git commit -m "fix(biometric): gallery size is per-modality in to_readings

FPIR ~= N * FMR is a per-modality relationship and the face and gait galleries
hold different numbers of people. A single shared gallery_size computed at
least one modality's confidence against the wrong denominator, understating
risk for the larger gallery. Backward compatible: the shared value remains the
fallback."
```

---

### Task 4: Observation recorder

**Why:** `surveillance_observations` was applied in migration 0032 and is written by **zero** code paths. This is the seam that makes every built component produce a durable, reviewable record.

**Files:**
- Create: `services/core/surveillance/__init__.py`
- Create: `services/core/surveillance/recorder.py`
- Test: `tests/unit/test_surveillance_recorder.py`

**Interfaces:**
- Consumes: `fusion.FusedIdentity` (existing), `rf_mapping.PositionFix` (existing), `occlusion.OcclusionStratum` (Task 1), `escalation.IDENTIFY_MANUALLY` (Task 2).
- Produces: `build_observation(*, pharmacy_id, site, action_type, observed_at, fused=None, fix=None, zone_id=None, camera_id=None, rf_device_ref=None, stratum=None, source="edge") -> dict` returning a kwargs dict for `SurveillanceObservation`, and `async def record(db, **kwargs) -> UUID`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_surveillance_recorder.py`:

```python
"""Turning a fused identity and an RF fix into one durable observation.

The table has existed since migration 0032 and nothing wrote to it. These tests
pin the shape, and specifically the two invariants the schema enforces so that a
caller gets a clear Python error rather than an IntegrityError from Postgres.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from services.biometric.fusion import (
    Contribution, Exclusion, FusedIdentity)
from services.biometric.occlusion import OcclusionStratum
from services.core.rf_mapping import PositionFix
from services.core.surveillance.recorder import build_observation

PHARMACY = uuid4()
NOW = datetime(2026, 8, 16, 9, 30, tzinfo=timezone.utc)


def a_fused(identity=None, decision="auto", confidence=0.995):
    return FusedIdentity(
        identity_id=identity or uuid4(), confidence=confidence,
        decision=decision, margin=0.4,
        contributions=[Contribution(modality="face", similarity=0.72,
                                    calibrated=0.999, quality=0.9, weight=0.77,
                                    log_lambda=-38.2)],
        excluded=[Exclusion(modality="gait", reason="quality 0.30 below floor")],
        explanation="2 modalities agree")


def test_fused_result_is_persisted_with_its_working():
    obs = build_observation(
        pharmacy_id=PHARMACY, site="pharmacy", action_type="entry",
        observed_at=NOW, fused=a_fused(), stratum=OcclusionStratum.MASK)

    assert obs["fusion_decision"] == "auto"
    assert obs["modalities_used"] == ["face"]
    # the exclusions travel with the row: an observation that cannot explain
    # what it discarded is not reviewable months later
    assert obs["fusion_detail"]["excluded"][0]["modality"] == "gait"
    assert obs["fusion_detail"]["occlusion_stratum"] == "mask"


def test_rf_fix_is_persisted_with_its_uncertainty():
    fix = PositionFix(x=8.0, y=6.0, uncertainty_m=2.5, method="fingerprint",
                      ap_count=4)
    obs = build_observation(
        pharmacy_id=PHARMACY, site="depot", action_type="movement",
        observed_at=NOW, fix=fix, rf_device_ref="dev-77")

    assert (obs["rf_x"], obs["rf_y"]) == (8.0, 6.0)
    assert obs["rf_uncertainty_m"] == 2.5
    assert obs["rf_method"] == "fingerprint"
    assert obs["rf_device_ref"] == "dev-77"


def test_observation_may_carry_neither_side():
    obs = build_observation(pharmacy_id=PHARMACY, site="depot",
                            action_type="exit", observed_at=NOW)
    assert obs["fusion_decision"] is None
    assert obs["rf_x"] is None


def test_site_must_be_one_the_schema_accepts():
    """ck_surv_obs_site allows only pharmacy|depot. Fail in Python with a clear
    message rather than as an IntegrityError from the driver."""
    with pytest.raises(ValueError, match="site"):
        build_observation(pharmacy_id=PHARMACY, site="warehouse",
                          action_type="entry", observed_at=NOW)


def test_a_coordinate_without_uncertainty_is_refused():
    """Mirrors ck_surv_obs_rf_uncertainty_required. A coordinate without its
    error bar invites false precision on a heatmap."""
    bad = PositionFix(x=1.0, y=2.0, uncertainty_m=0.0, method="trilateration",
                      ap_count=3)
    with pytest.raises(ValueError, match="uncertainty"):
        build_observation(pharmacy_id=PHARMACY, site="depot",
                          action_type="movement", observed_at=NOW, fix=bad)


def test_review_decision_is_recorded_as_review_not_promoted():
    obs = build_observation(
        pharmacy_id=PHARMACY, site="pharmacy", action_type="entry",
        observed_at=NOW, fused=a_fused(decision="review", confidence=0.5))
    assert obs["fusion_decision"] == "review"
    assert obs["biometric_identity_id"] is not None   # candidate is kept
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_surveillance_recorder.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.core.surveillance'`

- [ ] **Step 3: Write minimal implementation**

Create `services/core/surveillance/__init__.py`:

```python
"""Surveillance observation persistence."""
```

Create `services/core/surveillance/recorder.py`:

```python
"""Turn a fused identity and/or an RF fix into one durable observation row.

`surveillance_observations` records what a sensor REPORTED at a moment, with
the confidence and the reason behind it. It never asserts who someone is —
promotion to an identity decision happens elsewhere and needs corroboration this
table cannot supply.

The schema's CHECK constraints are mirrored here so a caller gets a clear Python
error instead of an IntegrityError surfacing from the driver three frames away.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

VALID_SITES = ("pharmacy", "depot")
VALID_DECISIONS = ("auto", "review", "no_match")


def build_observation(*, pharmacy_id: UUID, site: str, action_type: str,
                      observed_at: datetime, fused=None, fix=None,
                      zone_id: str | None = None, camera_id: str | None = None,
                      rf_device_ref: str | None = None, stratum=None,
                      source: str = "edge") -> dict:
    """Kwargs for `SurveillanceObservation`. Validates the schema's constraints."""
    if site not in VALID_SITES:
        raise ValueError(f"site must be one of {VALID_SITES}, got {site!r}")

    row: dict = {
        "pharmacy_id": pharmacy_id,
        "observed_at": observed_at,
        "site": site,
        "zone_id": zone_id,
        "action_type": action_type,
        "camera_id": camera_id,
        "source": source,
        "biometric_identity_id": None,
        "fusion_decision": None,
        "fusion_confidence": None,
        "fusion_margin": None,
        "fusion_detail": None,
        "modalities_used": None,
        "fusion_explanation": None,
        "rf_device_ref": rf_device_ref,
        "rf_x": None, "rf_y": None, "rf_uncertainty_m": None,
        "rf_method": None, "rf_ap_count": None,
    }

    if fused is not None:
        if fused.decision not in VALID_DECISIONS:
            raise ValueError(
                f"fusion decision must be one of {VALID_DECISIONS}, "
                f"got {fused.decision!r}")
        row.update({
            "biometric_identity_id": fused.identity_id,
            "fusion_decision": fused.decision,
            "fusion_confidence": round(float(fused.confidence), 5),
            "fusion_margin": round(float(fused.margin), 5),
            "modalities_used": [c.modality for c in fused.contributions],
            "fusion_explanation": fused.explanation,
            # The working travels with the row. An observation that cannot say
            # what it discarded, and why, is not reviewable after the fact.
            "fusion_detail": {
                "occlusion_stratum": (stratum.value if stratum is not None
                                      else None),
                "runner_up_id": (str(fused.runner_up_id)
                                 if fused.runner_up_id else None),
                "contributions": [
                    {"modality": c.modality, "similarity": c.similarity,
                     "calibrated": c.calibrated, "quality": c.quality,
                     "weight": c.weight, "log_lambda": c.log_lambda}
                    for c in fused.contributions],
                "excluded": [{"modality": e.modality, "reason": e.reason}
                             for e in fused.excluded],
            },
        })

    if fix is not None:
        if not fix.uncertainty_m or fix.uncertainty_m <= 0:
            raise ValueError(
                "an RF coordinate requires a positive uncertainty_m; a "
                "coordinate without its error bar invites false precision")
        row.update({
            "rf_x": round(float(fix.x), 2),
            "rf_y": round(float(fix.y), 2),
            "rf_uncertainty_m": round(float(fix.uncertainty_m), 2),
            "rf_method": fix.method,
            "rf_ap_count": fix.ap_count,
        })

    return row


async def record(db, **kwargs) -> UUID:
    """Persist one observation. Returns its id."""
    from shared.models.surveillance_log import SurveillanceObservation

    obs = SurveillanceObservation(**build_observation(**kwargs))
    db.add(obs)
    await db.flush()
    return obs.id
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_surveillance_recorder.py -q`
Expected: PASS — 6 passed

- [ ] **Step 5: Commit**

```bash
git add services/core/surveillance/ tests/unit/test_surveillance_recorder.py
git commit -m "feat(surveillance): observation recorder

surveillance_observations has existed since migration 0032 and was written by
nothing. This is the seam. The fused result's full working — per-modality
contributions AND every exclusion with its reason, plus the occlusion stratum —
travels with the row, because an observation that cannot say what it discarded
is not reviewable months later. The schema's CHECK constraints are mirrored in
Python so a caller gets a clear error rather than a driver IntegrityError."
```

---

### Task 5: Ingest and query endpoints

**Files:**
- Create: `services/platform/routers/surveillance.py`
- Modify: `services/platform/main.py` (router import list ~line 109, registration ~line 119)
- Test: `tests/unit/test_surveillance_ingest.py`

**Interfaces:**
- Consumes: `build_observation`/`record` (Task 4), `rf_mapping.locate`, `occlusion.classify_occlusion` (Task 1), `escalation.escalate` (Task 2).
- Produces: `POST /api/v1/surveillance/observations`, `POST /api/v1/surveillance/rf/batch`, `GET /api/v1/surveillance/heatmap`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_surveillance_ingest.py`:

```python
"""Surveillance ingest endpoints.

Static checks, in the style of tests/unit/test_route_authentication.py: these
run without a database and catch the failure that actually happened on this
codebase — a PHI-adjacent route shipped with no auth dependency.
"""
from __future__ import annotations

import re
from pathlib import Path

ROUTER = Path("services/platform/routers/surveillance.py")
MAIN = Path("services/platform/main.py")

AUTH = ("require_permission", "require_pharmacist", "get_current_staff",
        "get_current_user", "require_ws_staff")


def _handlers() -> list[tuple[str, str, str]]:
    src = ROUTER.read_text(encoding="utf-8")
    out = []
    for m in re.finditer(r'@router\.(get|post)\(\s*"([^"]*)"', src):
        seg = src[m.start(): m.start() + 2000]
        sig = seg.split("\n)")[0] if "\n)" in seg[:2000] else seg[:900]
        out.append((m.group(1).upper(), m.group(2), sig))
    return out


def test_the_three_endpoints_exist():
    paths = {(v, p) for v, p, _ in _handlers()}
    assert ("POST", "/observations") in paths
    assert ("POST", "/rf/batch") in paths
    assert ("GET", "/heatmap") in paths


def test_every_surveillance_route_authenticates():
    for verb, path, sig in _handlers():
        assert any(a in sig for a in AUTH), f"{verb} {path} has no auth dependency"


def test_router_is_registered_in_main():
    src = MAIN.read_text(encoding="utf-8")
    assert "surveillance" in src
    assert "/api/v1/surveillance" in src


def test_ingest_never_accepts_raw_media():
    """Frames, crops and audio stay on the edge node. The ingest schema must not
    give them a field to arrive in."""
    src = ROUTER.read_text(encoding="utf-8")
    body = src[src.index("class ObservationIn"):]
    body = body[:body.index("\nclass ")]
    for forbidden in ("image", "frame", "crop", "audio", "embedding", "bytes"):
        assert forbidden not in body.lower(), (
            f"ObservationIn exposes a '{forbidden}' field — raw media and "
            f"embeddings must never cross the site boundary")


def test_heatmap_is_read_only():
    for verb, path, _ in _handlers():
        if path == "/heatmap":
            assert verb == "GET"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_surveillance_ingest.py -q`
Expected: FAIL — `FileNotFoundError: services/platform/routers/surveillance.py`

- [ ] **Step 3: Write minimal implementation**

Create `services/platform/routers/surveillance.py`:

```python
"""Surveillance ingest and query.

What crosses this boundary is EVENTS ONLY — no frames, no crops, no audio, no
embeddings. The edge node does detection, embedding and matching; what arrives
here is a decision with its working. `ObservationIn` deliberately has no field
that raw media could arrive in, and a test enforces that.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.core.rf_mapping import (AccessPoint, RadioMap, RssiSample,
                                      occupancy_heatmap, locate)
from services.core.surveillance.recorder import record
from services.platform.auth import require_permission
from services.platform.database import get_db
from shared.models.auth import Staff
from shared.models.surveillance_log import SurveillanceObservation

router = APIRouter(tags=["surveillance"])


class ContributionIn(BaseModel):
    modality: str
    similarity: float
    calibrated: float = 0.0
    quality: float = 0.0
    weight: float = 0.0
    log_lambda: float = 0.0


class ExclusionIn(BaseModel):
    modality: str
    reason: str


class ObservationIn(BaseModel):
    """A decision and its working. No media fields, by design."""
    site: str
    action_type: str
    observed_at: datetime
    zone_id: Optional[str] = None
    camera_id: Optional[str] = None
    identity_id: Optional[UUID] = None
    decision: Optional[str] = None
    confidence: Optional[float] = None
    margin: Optional[float] = None
    occlusion_stratum: Optional[str] = None
    contributions: list[ContributionIn] = Field(default_factory=list)
    excluded: list[ExclusionIn] = Field(default_factory=list)
    explanation: Optional[str] = None


class RssiIn(BaseModel):
    ap_id: str
    rssi_dbm: float


class RfBatchIn(BaseModel):
    site: str
    device_ref: str
    observed_at: datetime
    zone_id: Optional[str] = None
    samples: list[RssiIn]
    access_points: list[dict] = Field(default_factory=list)


@router.post("/observations", status_code=201)
async def ingest_observation(
    body: ObservationIn,
    staff: Staff = Depends(require_permission("clinical:write")),
    db: AsyncSession = Depends(get_db),
):
    """Record one capture observation from the edge."""
    from services.biometric.fusion import Contribution, Exclusion, FusedIdentity
    from services.biometric.occlusion import OcclusionStratum

    fused = None
    if body.decision is not None:
        fused = FusedIdentity(
            identity_id=body.identity_id,
            confidence=body.confidence or 0.0,
            decision=body.decision,
            margin=body.margin or 0.0,
            contributions=[Contribution(**c.model_dump())
                           for c in body.contributions],
            excluded=[Exclusion(**e.model_dump()) for e in body.excluded],
            explanation=body.explanation or "")

    stratum = None
    if body.occlusion_stratum:
        try:
            stratum = OcclusionStratum(body.occlusion_stratum)
        except ValueError:
            raise HTTPException(400, f"unknown occlusion stratum "
                                     f"{body.occlusion_stratum!r}")

    try:
        obs_id = await record(
            db, pharmacy_id=staff.pharmacy_id, site=body.site,
            action_type=body.action_type, observed_at=body.observed_at,
            fused=fused, zone_id=body.zone_id, camera_id=body.camera_id,
            stratum=stratum)
    except ValueError as e:
        raise HTTPException(400, str(e))
    await db.commit()
    return {"observation_id": str(obs_id)}


@router.post("/rf/batch", status_code=201)
async def ingest_rf_batch(
    body: RfBatchIn,
    staff: Staff = Depends(require_permission("inventory:write")),
    db: AsyncSession = Depends(get_db),
):
    """Locate a device from an RSSI batch and record the fix.

    This locates a DEVICE, not a person: it is for staff handsets in the depot
    and feeds nothing in the biometric path.
    """
    aps = {a["ap_id"]: AccessPoint(**a) for a in body.access_points}
    samples = [RssiSample(s.ap_id, s.rssi_dbm) for s in body.samples]
    fix = locate(samples, aps, RadioMap([]))
    if fix is None:
        return {"observation_id": None,
                "reason": "not enough access points for a fix"}

    obs_id = await record(
        db, pharmacy_id=staff.pharmacy_id, site=body.site,
        action_type="movement", observed_at=body.observed_at,
        fix=fix, zone_id=body.zone_id, rf_device_ref=body.device_ref)
    await db.commit()
    return {"observation_id": str(obs_id), "x": fix.x, "y": fix.y,
            "uncertainty_m": fix.uncertainty_m, "method": fix.method}


@router.get("/heatmap")
async def heatmap(
    site: str = Query("depot"),
    width_m: float = Query(20.0),
    height_m: float = Query(15.0),
    cell_m: float = Query(2.0),
    staff: Staff = Depends(require_permission("inventory:read")),
    db: AsyncSession = Depends(get_db),
):
    """Occupancy density from recorded RF fixes."""
    rows = (await db.execute(
        select(SurveillanceObservation.rf_x, SurveillanceObservation.rf_y)
        .where(SurveillanceObservation.pharmacy_id == staff.pharmacy_id,
               SurveillanceObservation.site == site,
               SurveillanceObservation.rf_x.isnot(None)))).all()
    points = [(float(x), float(y)) for x, y in rows]
    return {"site": site, "cell_m": cell_m, "points": len(points),
            "grid": occupancy_heatmap(points, width_m, height_m, cell_m)}
```

Modify `services/platform/main.py` — add `surveillance` to the router import list (~line 109) and register it after the `biometric_admin` line (~line 119):

```python
    app.include_router(surveillance.router,  prefix="/api/v1/surveillance", tags=["surveillance"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_surveillance_ingest.py tests/unit/test_route_authentication.py -q`
Expected: PASS — 5 new plus the existing route-auth suite

Then confirm the app builds:

Run: `/Users/sashad85/miniforge3/bin/python3 -c "from services.platform.main import create_app; print(len(create_app().routes))"`
Expected: a route count 3 higher than before

- [ ] **Step 5: Commit**

```bash
git add services/platform/routers/surveillance.py services/platform/main.py tests/unit/test_surveillance_ingest.py
git commit -m "feat(surveillance): ingest and heatmap endpoints

Events only across the boundary — ObservationIn has no field that a frame,
crop, audio buffer or embedding could arrive in, and a test enforces it. All
three routes authenticate, checked statically in the style that caught ten open
routes earlier. RF ingest locates a DEVICE, not a person."
```

---

### Task 6: Full-suite verification

- [ ] **Step 1: Run the whole unit suite**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit -q`
Expected: all pass except the known pre-existing `test_integrations_sandbox.py::test_notifications_sandbox_success_shape_no_network_and_masked_logs`, which is order-dependent and green in isolation.

Do **not** export `DATABASE_URL` before running the suite — some unit tests connect to the real database when it is present and the run will hang.

- [ ] **Step 2: Confirm no new unauthenticated routes**

Run: `/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit/test_route_authentication.py -q`
Expected: PASS

- [ ] **Step 3: Commit any fixes and update the collaboration ledger**

Append an entry to `docs/ai-context/AI_COLLABORATION.md` using the template at the bottom of that file: branch/commit, files and behaviour changed, checks actually run, remaining risks, next action.

```bash
git add docs/ai-context/AI_COLLABORATION.md
git commit -m "docs: ledger entry for the surveillance observation pipeline"
```

---

## Phase roadmap for the surveillance & CV section

**Seven phases.** This plan is Phase 1.

| # | Phase | Status | Needs its own plan? |
|---|---|---|---|
| **1** | **Observation pipeline** — occlusion stratum, per-modality N, recorder, ingest | **This plan** | — |
| 2 | Voice modality — speaker embedding from the existing diarizer into `ModalityReading`; text-dependent verification on the spoken national code | Not started | Yes |
| 3 | Calibration & release gate — per-stratum impostor measurement, per-demographic-cell gate, shadow mode | Partly built (`repository.measure_impostor_stats`) | Yes |
| 4 | RF positioning live — AP registry, radio-map survey capture, fingerprint storage | Engine built, no survey data | Yes |
| 5 | Zone rules & reconciliation — the absence-detection engine (pick vs `InventoryMovement`, presence vs badge, after-hours arming, blocked egress) | Not started | Yes |
| 6 | Review & investigation UI — triage board, evidence-vault access with documented reason, chain-of-custody surfacing | Vault built, no UI | Yes |
| 7 | Hardening — model registry, drift monitoring, retention/purge with proof-of-deletion, RBAC audit | Not started | Yes |

Phases 2–7 are independent subsystems. Each should get its own plan when it starts, so each produces working, testable software on its own.

**Deliberately not in this roadmap:** an iris extractor. The evidence in `docs/design/IDENTIFICATION_PRECISION.md` puts it behind voice for this setting on cooperation cost, and `FUSION_FLOORS` already admits it the moment a calibrated reading exists — so nothing needs building until a capture device is bought.
