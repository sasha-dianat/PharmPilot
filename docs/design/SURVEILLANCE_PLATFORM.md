# Intelligent Surveillance Platform — Pharmacy + Warehouse

Architecture design. Status: **proposal, not approved.** Owner: pending.
Scope: physical-security and operations analytics for one pharmacy site and one
warehouse/depot site, integrated with the existing PharmPilot platform.

> **PARTIALLY SUPERSEDED.** Sections 0 (thesis point 2), 1.3 (R-1, R-2, R-3,
> R-7) and invariant I-8 were written before the owner confirmed the standing
> project specification: **every visitor is identified by face**, provisional
> UUIDs are minted for unknown persons, and all biometric vectors plus raw
> source media are retained and admin-accessible for intrusion, theft and
> attack-on-personnel investigations via `BiometricEvidenceVault`. Where this
> document conflicts with that spec, the spec wins and
> [FACE_IDENTITY_PLATFORM.md](FACE_IDENTITY_PLATFORM.md) plus
> [IDENTIFICATION_PRECISION.md](IDENTIFICATION_PRECISION.md) are authoritative.
> The zone, camera, warehouse, replenishment, retention and cybersecurity
> sections are unaffected.

---

## 0. Thesis

Three claims drive every decision below.

1. **Video observes; it never decides.** The camera layer emits *observations*
   with confidence. Promotion of an observation into a fact that touches a
   patient, a payroll consequence, or an inventory balance requires either an
   authoritative non-video event (badge swipe, POS transaction, barcode scan) or
   a named human who accepts it. This is the same Observed/Decided split the
   pricing and coverage subsystems already enforce, applied to pixels.

2. **REVISED — identity is layered, and face is the first layer.** Face
   identifies every visitor (owner spec) and *proposes* a candidate; a second,
   independent factor *verifies* it before any clinical consequence. The
   original text here argued against customer face recognition altogether; that
   was overridden by the standing specification. What survives is the weaker and
   still-correct claim: a similarity score alone never establishes identity, so
   the architecture converts 1:N retrieval into 1:1 verification rather than
   trusting a threshold. See IDENTIFICATION_PRECISION.md §1.

3. **The most valuable requirement on the list barely needs cameras.**
   Requirement 7 (morning replenishment) is a demand-forecasting problem solved
   with transaction history, and it can ship in week 3 while the cabling is still
   being pulled.

---

## 1. What already exists, and what must change first

Before designing anything new I read the current tree. There is a substantial
biometric subsystem already in the repo, and **parts of it implement precisely
the pattern the new requirements forbid.** This has to be resolved before any
new capability is layered on top, so it comes first.

### 1.1 Inventory of what is built

| Component | Location | LOC | What it does |
|---|---|---|---|
| Face pipeline | `services/biometric/face_pipeline/` | ~600 | Detect → align → embed |
| Identity resolution | `services/biometric/identity_resolution/` | ~900 | ArcFace R100 + FAISS **1:N identification**, liveness, person-link graph |
| Patient resolver | `services/biometric/patient_resolver/` | ~520 | Multi-signal patient matching, **accepts face match as an identity signal** |
| Behavioural analysis | `services/biometric/behavioral_analysis/` | ~1,100 | Duress protocol, Rx-shopping detector |
| Evidence vault | `services/biometric/evidence_vault/` | ~400 | Clip/evidence storage |
| Models | `shared/models/biometric.py` | — | `BiometricIdentity`, `PharmacyVisit`, `SecurityEvent` |
| Depot/replenishment | `shared/models/depot.py` | — | `PharmacyShelf`, `ShelfPlacement`, `ReplenishmentSession`, `ShelfTransferEvent`, **`SurveillanceEvent`** |
| Routers | `routers/{biometric,identity,security_events,phase32}.py` | — | `/identify`, `/enroll`, WS stream, behaviour ingest, duress |

The good news is real: `SurveillanceEvent` already has exactly the right shape
(`camera_id`, `event_type`, `severity`, `clip_ref`, `ai_result`, `reviewed_by`,
`reviewed_at`), and the depot tables already model the morning replenishment
flow with dual verification. Requirement 7 is mostly a matter of filling
`ReplenishmentSession.pick_list` with a forecast instead of a manual list.

### 1.2 Four findings that block the new requirements

**F-1 — Consent columns exist but nothing reads them.**
`BiometricIdentity` carries `consent_security_monitoring`,
`consent_patient_services`, `consent_recorded_at`, `consent_method`. A search
for `consent` across the whole `services/biometric/` tree returns **zero
matches**. Enrolment and identification run without ever consulting them. A
consent field that no code path checks is worse than no field: it documents an
intent the system does not honour, which is exactly what an auditor looks for.

**F-2 — A face match can auto-load a patient chart with no human confirmation.**
`PatientResolver.resolve_from_prescription()` accepts `biometric_confidence`;
at ≥ 0.80 it contributes `confidence × 0.95` toward patient identity
(`resolver.py:330`). Combined confidence ≥ `HIGH_CONFIDENCE = 0.88` returns
`requires_pharmacist_action=False, auto_loaded=True` (`resolver.py:486`). So a
face match plus one weak corroborating signal opens a named patient's record
with nobody confirming. Requirement 3 says the opposite.

**F-3 — A face match ≥ 0.80 mints a permanent biometric identity.**
`phase32.py:344`: `is_temporary = biometric_identity_id is None or
biometric_confidence < 0.80`. Above the threshold the row becomes permanent —
a durable, re-identifiable biometric record of a member of the public, created
by a similarity score.

**F-4 — RETRACTED.** I originally called the dormant watchlist fields
(`BiometricIdentity.is_watchlist_match`, `watchlist_source`) "a loaded gun in
the schema" and recommended dropping them. That was wrong. They implement a
deliberate part of the original project specification, whose `IdentityClass`
enum defines a full security vocabulary — `WATCHLIST_MATCH`, `BEHAVIORAL_ALERT`,
`REPEAT_UNKNOWN`, `MINOR_UNACCOMPANIED` — for intrusion, theft and
attack-on-personnel handling, backed by `BiometricEvidenceVault` with documented
`legal_reason`, append-only chain of custody, legal hold and
`export_for_authority()`. The fields are unimplemented, not ill-conceived. The
correct action is to IMPLEMENT them under the controls the spec already
defines, not to delete them.

Two smaller notes: `LivenessDetector._texture_based_liveness` returns
`(True, 0.5)` on exception (`engine.py:146`) — it **fails open**, so an
exception during anti-spoofing is scored as a live face; and
`PatientResolver` itself performs no writes, which is the one piece of good
containment already present — the risk is in its callers, not in it.

### 1.3 Required remediation (precedes all new work)

| # | Action | Rationale |
|---|---|---|
| R-1 | ~~Staff-gallery only~~ **SUPERSEDED by owner spec.** All visitors are enrolled and searched. The precision requirement is met by gallery TIERING (Tier-A "expected today", N≈400) plus 1:1 verification, not by shrinking who is enrolled. | spec |
| R-2 | **STANDS, narrowed.** Face may *rank and propose* candidates in `PatientResolver`; it may not contribute to a blended confidence that authorises auto-load. A cosine is not a probability and must not enter `1-(1-c1)(1-c2)`. | F-2 |
| R-3 | **STANDS.** Auto-load requires a non-biometric corroborating factor (IAL2+). Face alone proposes; it never opens a chart unattended. | F-2 |
| R-4 | Enforce consent at the enrolment API and at every read of a template; deny by default. Add a coverage test asserting a non-consented identity cannot be enrolled or matched. | F-1 |
| R-5 | ~~Drop the watchlist fields~~ **WITHDRAWN.** Implement them per the original spec, writing every match through the evidence vault's documented-reason + chain-of-custody path. | F-4 |
| R-6 | `LivenessDetector` fails **closed**: exception ⇒ `(False, 0.0)`. | Spoofing |
| R-7 | ~~Purge non-staff identities~~ **WITHDRAWN — contradicts the retention spec.** Visitor identities and their vault media are retained for investigations. What stands from F-1 is that the consent LEDGER must gate the *clinical-personalisation* branch; the security branch runs on the owner's legitimate-interest basis. | F-1 |

R-1…R-7 are Phase 0 work. Nothing in Phases 1–5 should be built on the current
behaviour.

---

## 2. Design invariants

These are enforced in code and tested, not merely documented.

| ID | Invariant | Enforcement |
|---|---|---|
| **I-1** | No video frame, face crop, or embedding ever crosses the site boundary. | Edge node has no inbound WAN route; outbound schema rejects binary fields |
| **I-2** | The vision DB role holds **zero write grants** on any clinical table. | Postgres role grants + startup assertion |
| **I-3** | A patient record is never created, modified, or opened by a vision signal. | R-2/R-3; integration test |
| **I-4** | Every alert renders the rule, thresholds, and contributing evidence. | Alert schema requires `explanation`; UI fails loudly if absent |
| **I-5** | Every consequential action (link, merge, unblur, export, investigation) writes an append-only, hash-chained audit row. | Audit middleware |
| **I-6** | Any operation reachable by staff monitoring is reversible; reversal is a new event, never a delete. | Soft-correction model |
| **I-7** | ReID embeddings expire ≤ 48 h. Cross-day re-identification is not supported. | TTL on vector store + purge job |
| **I-8** | ~~No audio is captured anywhere.~~ **SUPERSEDED** — the owner has placed counter and waiting-area microphones in scope. Replaced by: audio is captured only in declared zones; the waiting-area channel feeds security signals (raised voice, distress, aggression) and NEVER identification, because far-field speaker EER (~15% at 5 m / RT60 1.5 s) is well below any usable identification floor. | Zone-scoped capture config; waiting-area stream excluded from the identification fusion in code |
| **I-9** | No demographic attribute (age, gender, ethnicity) is inferred, stored, or used as a feature. | Model registry purpose declaration; code review gate |
| **I-10** | Fire and water detection by video is **supplementary**; certified physical detectors remain the primary and code-compliant means. | Documented; alarm panel independent of this system |

---

## 3. Physical layer

### 3.1 Zoning

A **zone** is the unit of policy: rules, retention, access, and privacy masking
are all attached to zones, not cameras.

| Zone | Site | Class | Camera intent |
|---|---|---|---|
| Z-ENT | Pharmacy | Public | Entry/exit counting, arrival timestamps |
| Z-WAIT | Pharmacy | Public | Queue occupancy, dwell |
| Z-COUNTER-1..n | Pharmacy | Service | Service start/end, over-counter reach |
| Z-OTC | Pharmacy | Public | Shelf interaction, crowding |
| Z-CDS | Pharmacy | **Restricted** | Controlled-substance cabinet; every entry logged |
| Z-COMPOUND | Pharmacy | Restricted | Access control, PPE |
| Z-BACKOFFICE | Pharmacy | Restricted | Access control only |
| Z-CONSULT | Pharmacy | **No camera** | Patient counselling — excluded by design |
| Z-STAFFDOOR | Both | Credentialed | Badge reader + optional face verification |
| Z-DOCK | Warehouse | Restricted | Receiving/dispatch, vehicle presence |
| Z-AISLE-A..n | Warehouse | Restricted | Pick events, aisle traversal |
| Z-CAGE | Warehouse | **High-risk** | Narcotics cage; 90-day retention |
| Z-COLD | Warehouse | High-risk | Cold room door-open duration + temp sensors |
| Z-QUARANTINE | Warehouse | Restricted | Expired/recalled stock segregation |
| Z-EGRESS-1..n | Both | Safety | Blocked-exit detection |
| — | Both | **Prohibited** | Toilets, changing rooms, prayer room, break room, any framing where a screen or prescription is legible |

### 3.2 Camera specification by role

| Role | Mount | Lens/Res | FPS | Notes |
|---|---|---|---|---|
| Door counter | Nadir (straight down), 2.8–3.2 m | 4 MP, 2.8 mm | 15 | Top-down eliminates occlusion; heads/shoulders only — inherently privacy-preserving and the anchor for count reconciliation |
| Area / dwell | Corner, 2.8–3.5 m, 30–45° tilt | 4 MP, 2.8–4 mm, WDR ≥ 120 dB | 10 | Never aimed at the storefront glass (backlight) |
| Counter | Above and behind the service line | 4 MP, 4 mm | 15 | Frames hands and counter surface, **not** the customer's face; screen ROI privacy-masked pre-recording |
| Aisle | End-of-aisle, 3.5–4.5 m | 5–8 MP varifocal | 10 | Long depth of field; bay-level ROIs |
| Shelf / pick | Cross-aisle, bay-facing | 4 MP, 4–6 mm | 12 | Reach-in ROI per bay |
| Staff door | 1.6–1.8 m, frontal | 4 MP, 4 mm | 20 | Only camera with adequate face geometry (≥ 40 px inter-ocular) |
| Egress | Wide, ceiling | 4 MP, 2.8 mm | 5 | Static occupancy of the egress path |
| Dock | Weatherproof, 3–4 m | 4 MP + IR | 10 | Vehicle + person + load |

All PoE, H.265, ONVIF Profile S/T, IR for after-hours, **microphones disabled in
firmware**.

### 3.3 Blind spots — treat them as topology, not as failure

A coverage survey produces a floor plan annotated with each camera's FOV
polygon projected onto the floor plane. Two acceptance criteria:

- **Coverage:** ≤ 5 % of walkable floor area uncovered.
- **Connectivity (the one that matters):** no uncovered region may connect two
  covered regions without itself being covered — i.e. there is no path a person
  can walk that "teleports" them between cameras.

Where connectivity cannot be achieved physically, the gap is modelled
explicitly as a **portal** in the site topology graph, with a measured transit
time distribution. The tracker then treats a disappearance at portal A as a
*prediction* of reappearance at portal B in `t ∈ [μ−2σ, μ+2σ]`, converting a
blind spot from a source of identity fragmentation into a matching prior.
Transit distributions are learned from the first two weeks of operation, not
guessed.

Target: ~16 cameras pharmacy, ~16 warehouse. Overlap 15–25 % of FOV at every
doorway and aisle junction so hand-off is geometric first, appearance second.

---

## 4. Edge vs cloud

**All inference on-premises. No video, no crop, no embedding leaves the site.**

| Tier | Where | Responsibility | Data at rest |
|---|---|---|---|
| T0 | Camera | Encode, motion pre-filter, tamper flag | None |
| T1 | Edge node (1 per site) | Decode, detect, track, ReID, pose, zone/rule engine | Frames (transient), embeddings (≤48 h) |
| T2 | Site NVR | Recording, clip bookmarking, retention/purge | Video per policy |
| T3 | PharmPilot backend `:8001` | Event ingest, joins with POS/WMS/access/schedule, review UI, forecasting | Events + metrics only |
| T4 | Off-site | Encrypted config/event-DB backup **only** | Never video |

The reasoning is not ideological, it is arithmetic and legal:

- **Bandwidth.** 32 cameras × 3 Mbps ≈ 1.04 TB/day. Streaming that off-site is
  infeasible on any plausible link, and pointless — 99.9 % of it is of no
  interest.
- **Latency.** Duress and blocked-egress alerts need < 3 s end to end. A
  round trip to a remote region cannot guarantee that; a local loop can.
- **Availability.** The pharmacy must keep recording during a link outage.
  Edge-first makes the network a convenience, not a dependency.
- **Lawfulness.** Biometric templates and health-adjacent footage crossing a
  border creates a transfer problem with no upside.
- **Consistency with existing policy.** The platform already enforces a PHI
  egress gate on LLM calls. The same rule extends naturally: **no LLM, local or
  remote, is in this pipeline.** Incident narratives are template-rendered from
  structured fields.

Cloud/off-site is used for exactly two things: encrypted backup of
configuration and the event database, and distribution of signed model
artifacts. Training happens on an offline workstation.

---

## 5. Detection, tracking, and re-identification

### 5.1 Per-camera pipeline

```
RTSP → NVDEC decode → YOLO11-s (INT8/TensorRT, person + object classes)
     → BoT-SORT (Kalman + camera-motion compensation) → tracklets
     → quality gate → ReID embedding on best-K frames → pooled descriptor
     → homography → ground-plane position → zone membership → rule engine
```

Run detection at 10–12 FPS, not 25. A person walking at 1.4 m/s moves 12 cm
between frames at 12 FPS — ample for association, and it halves GPU and decode
cost. Pose (RTMPose-s) runs only in zones with pose-dependent rules (counter,
aisles, egress), not everywhere.

**Tracklet quality gate** — a tracklet only contributes a ReID descriptor if it
clears minimum box area, detection confidence, blur (variance of Laplacian),
occlusion ratio, and aspect-ratio sanity. Descriptors are pooled over the best
K ≈ 8 frames, never taken per-frame. Most cross-camera identity errors trace
back to embedding a motion-blurred, half-occluded box.

### 5.2 Cross-camera association — geometry first

The ordering here is the single most important design decision for
requirement 1.

1. **Spatio-temporal gate (hard).** Candidate pairs must be physically
   possible: ground-plane displacement ÷ elapsed time ≤ 2.0 m/s, and the
   implied route must exist in the topology graph. Anything failing the gate is
   eliminated regardless of appearance similarity.
2. **Appearance (soft).** Among survivors, cost =
   `α·(1 − cos_sim(descriptor)) + β·spatial_inconsistency + γ·portal_time_deviation`.
3. **Assignment.** Hungarian matching per handover window; unmatched tracklets
   open new visit tracks.

Appearance-first association fails badly in this environment: pharmacy staff
wear identical uniforms, and in an Iranian retail setting a large fraction of
customers wear long, dark, loose outer garments that collapse the appearance
space. Geometry does not have this failure mode. Appearance is a tie-breaker
among the physically plausible, never the primary key.

### 5.3 Suppressing duplicate identities

| Mechanism | Effect |
|---|---|
| Door-count reconciliation | Nadir counter gives near-ground-truth occupancy; track count vs door count drift > 2 raises a tracker-health alert |
| Merge queue, not auto-merge | Pairs scoring in the ambiguous band become *candidates*; only unambiguous merges apply automatically |
| Visit-track TTL | A track with no observation for 10 min is closed, not left open to mis-merge with a later person |
| Reversible merge/split | Every merge records its evidence and can be undone; downstream metrics recompute |
| No cross-day identity | I-7 forbids it — which also removes the largest source of compounding error |

**Visit tracks are ephemeral and site-local.** The identifier
(`vt_20260727_0043`) is meaningless outside its 24-hour window and by
construction cannot be resolved to a person.

---

## 6. Requirement 2 — staff vs customer, without guessing

Role is **asserted by an authoritative source and bound to a track**, never
inferred from appearance.

### 6.1 Evidence hierarchy

| Rank | Source | Strength | Use |
|---|---|---|---|
| 1 | Access-control event (badge / PIN / mobile credential) | Authoritative | Primary binding: identity + door + timestamp |
| 2 | PharmPilot workstation login | Authoritative | Binds identity to a terminal position |
| 3 | Optional consented face verification (**1:1**, staff door only) | Strong | Convenience alternative to badge; never 1:N, never customers |
| 4 | Shift schedule | Prior only | Constrains who *could* be present; never establishes who is |
| 5 | Uniform / badge visible | **Negative evidence only** | May raise "unbadged person in restricted zone"; may **never** grant staff status |

Rank 5 deserves emphasis. Uniform detection is permitted to *increase*
suspicion and forbidden to *decrease* it. A model that reasons "uniform ⇒ staff
⇒ suppress alarm" is trivially defeated by wearing a lab coat, and is the
mechanism by which appearance-based role inference smuggles itself back in.

### 6.2 Binding model

A `subject_binding` links a visit track to a `staff_id` with `method`,
`confidence`, `valid_from`, `valid_until`, and `evidence_ref` (the access event
or session id).

Bindings **do not survive track loss.** If the track is lost and re-acquired,
the binding degrades to `probable` and must be re-established by a new
authoritative event before it can again suppress a restricted-zone alert. This
prevents a ReID error from silently transferring someone's authorisation to
another person — the failure mode that turns a tracking bug into a security
breach.

### 6.3 Person classes

Exactly three, and "customer" is not one of them:

- `staff_bound` — an active binding from rank 1–3.
- `visitor_declared` — logged at reception (courier, technician, inspector) with
  a start/end time.
- `unidentified` — everyone else. **The default.**

A person becomes associated with a *customer interaction* only when a POS or
dispense transaction occurs at a counter position while their track is present
there. The transaction, not the person, creates the association.

---

## 7. Requirement 3 — linking interactions to patient records

This is the highest-risk requirement in the brief, and the safe design inverts
the direction people usually build.

### 7.1 The rule

> **Video never identifies a patient. The transaction identifies the patient.
> Video may only attach operational context to a transaction a human already
> owns.**

Concretely: a pharmacist verifies the patient by lawful means (national ID,
insurance card, Rx barcode, declared name + DOB) exactly as today. That creates
a transaction with `patient_id` and `staff_id` at a known terminal. The vision
system contributes one fact: *visit track V occupied counter position 2 from
10:42:15 to 10:47:03*. The join is **spatio-temporal**, not biometric:

```
link(visit_track, transaction) ⟸ same terminal position ∧ overlapping time window
```

### 7.2 Thresholds

Let `S` = temporal IoU between the track's presence at the position and the
transaction window; `d` = ground-plane distance from track centroid to terminal;
`m` = margin between best and second-best candidate track.

| Band | Condition | Action |
|---|---|---|
| **Auto** | `S ≥ 0.60` ∧ `d ≤ 1.0 m` ∧ exactly one candidate ∧ `m ≥ 0.30` | Link **operational metrics only** (wait time, service duration). No clinical write. |
| **Review** | `0.35 ≤ S < 0.60` ∨ `m < 0.30` ∨ ≥ 2 candidates | Queue for human review with a redacted clip |
| **Discard** | `S < 0.35` | Drop; the transaction counts as "unlinked" in analytics |

The metrics degrade gracefully: an unlinked transaction is simply excluded from
the wait-time denominator, and the exclusion rate is itself a monitored quality
metric (target ≤ 10 %).

### 7.3 What no threshold ever authorises

**No confidence value — including 1.0 — permits a vision signal to create,
modify, merge, or open a patient record.** The link is metadata attached to an
already-existing transaction. It is enforced three ways: the vision DB role has
no write grants on clinical tables (I-2); the ingest schema has no `patient_id`
field to populate; and an integration test asserts that a maximal-confidence
vision event leaves every clinical table byte-identical.

Face recognition of customers is **not implemented.** It is unnecessary (the
counter already identifies the patient), legally hazardous (biometric health
data), and its error cost is asymmetric in the worst direction — a false match
means opening the wrong person's medication history.

### 7.4 Review, audit, correction

- **Review UI** — reviewer sees the candidate transaction, the candidate
  track(s), and a clip with **faces blurred by default**. Un-blurring requires
  an elevated role, a typed reason, and writes an audit row.
- **Audit** — append-only, hash-chained (each row carries the SHA-256 of its
  predecessor). Records actor, action, before/after, reason, and the evidence
  reference, for every link, unlink, merge, split, un-blur, export, and
  retention override.
- **Correction** — any link is reversible. Reversal is a new event with its own
  reason; the original is never deleted. Derived metrics recompute from the
  event log, so a correction propagates without manual repair.
- **Subject access** — a documented procedure to answer "what do you hold about
  me?" and "delete it". Because visit tracks are ephemeral and there is no
  customer biometric gallery, the honest answer for a member of the public is
  usually "footage for N days, and nothing else" — which is the intended design
  outcome, not an evasion.

---

## 8. Requirement 4 — pharmacy activity analytics

Derived entirely from zone occupancy, ground-plane position, and joins with POS
and schedule data.

| Metric | Definition | Ground truth for validation |
|---|---|---|
| Arrival | Entry line crossing, Z-ENT | Door counter |
| Queue length | Count within Z-WAIT polygon | Manual audit sample |
| Wait time | Queue entry → service start | POS transaction start |
| Service duration | Track dwell at counter position | POS transaction span |
| Abandonment | Entered Z-WAIT, exited Z-ENT, no transaction | Manual audit |
| Staff availability | Count of `staff_bound` tracks in service zones | Schedule + login |
| Restricted access | Entry to Z-CDS / Z-COMPOUND / Z-BACKOFFICE | Access-control log |
| Peak load | Rolling 15-min arrivals vs staffed positions | — |

### On "unusual behaviour"

I recommend against a generic behaviour-anomaly model, and the recommendation
is deliberate. Such models are unexplainable, encode whatever bias sits in the
training distribution, and produce accusations no one can defend when
challenged. They fail I-4 by construction.

Instead, a small set of **operationally defined, individually explainable**
rules, each stating its own trigger in one sentence a reviewer can verify
against the clip:

- Loitering near the CDS cabinet beyond a threshold with no staff binding.
- Repeated entry/exit cycles (≥ 3 in 20 min) without a transaction.
- Reach across the counter boundary plane.
- Presence in a restricted zone with no active binding.
- Rapid crowd formation (occupancy > N with arrival rate above baseline).
- **Fall detection** — keypoints horizontal and stationary > 10 s. This one is
  worth accepting false positives for.

Every one of these is a *review* item. None is an accusation, and none reaches
a person's employment record without human adjudication.

---

## 9. Requirement 5 — warehouse anomalies

### 9.1 Stock removal without a transaction

The crown jewel and the hardest to get right.

Rack-facing cameras detect a **reach-into-bay** event via pose (wrist keypoint
crossing a bay ROI depth plane), producing
`pick_event(zone, bay, t_start, t_end, actor_binding)`. This is reconciled
against `InventoryMovement` and `ShelfTransferEvent` within a ± 5 min window.
Unmatched picks become `unreconciled_pick`.

The critical calibration: **most unmatched picks are legitimate** — tidying,
cycle counts, a dropped box. Therefore:

- In general aisles this is a **daily reconciliation report**, not a real-time
  alarm. Its purpose is finding systematic discrepancy, not catching a person.
- In **Z-CAGE only** it is real-time, because controlled substances justify the
  false-positive cost.
- No output of this rule ever constitutes an accusation. Any HR or legal
  consequence requires two-person review of the clip plus a corroborating
  non-video record.

A second, independent signal: a person crossing the dispatch line carrying a
detected tote/carton with no open dispatch record.

### 9.2 Misplaced goods — an honest scope limit

Fine-grained visual classification of ~10,000 pharmaceutical cartons from a
ceiling camera is not reliable, and any design promising it is overselling.
What works:

- **Barcode / DataMatrix reading** from fixed bay cameras where the label faces
  outward — this is a solved problem and near-exact.
- **Coarse bay-state classification** — empty / partial / full / overfull /
  wrong-carton-geometry. Reliable and genuinely useful.
- Handheld scanner verification at placement time, which the existing
  `ShelfTransferEvent.barcode_verification_result` already models.

CV catches gross anomalies; barcodes establish identity.

### 9.3 Environmental — sensors primary, video supplementary

**Smoke and water detection by video must not be the primary means.** Video
smoke models false-positive on steam, dust, sunlight shafts, and headlights,
and no video system is a code-compliant fire detector. The design:

- **Primary:** certified optical/aspirating smoke detectors on the fire panel;
  point and rope water sensors at the cold room, plumbing runs, and floor lows.
- **Supplementary:** video smoke/flame as an *early wide-area* hint, and
  reflective-region growth for pooling water — both raising a lower-severity
  "verify" event that never replaces the panel.
- Both feed the same event bus so the operator sees one timeline.

**Cold chain** is added even though the brief did not list it, because for a
pharmacy warehouse it is the highest-consequence environmental risk: fridge and
cold-room temperature/humidity sensors, plus CV-measured door-open duration.
An excursion invalidates stock and is a regulatory event.

### 9.4 Other warehouse rules

Blocked egress (object persisting in an egress ROI > 60 s — high value,
trivially reliable); after-hours activity via schedule-driven zone arming;
prolonged presence per zone; unsafe handling by pose (deep torso flexion under
load, climbing racking, riding a pallet jack) — framed as **coaching signals**,
reported in aggregate, never individually punitive.

---

## 10. Requirement 6 — integration with minimal personal data

### 10.1 What crosses the boundary

Events carry: `visit_track_id` (ephemeral), `zone_id`, `camera_id`, timestamps,
counts, geometry, rule id, confidence, and **references** to authoritative
records (`staff_id`, `transaction_id`, `clip_ref`). They never carry names,
images, embeddings, or any demographic attribute.

| System | Direction | Joined on | Data taken |
|---|---|---|---|
| Access control | → vision | `door_id`, timestamp | `staff_id`, grant/deny |
| Staff schedule | → vision | `staff_id`, shift window | Expected presence (prior only) |
| POS / dispense | ↔ | `terminal_id`, time window | Transaction id + span; **never patient identity** |
| Inventory / WMS | ↔ | `bay`, `ndc11`, time window | Movement records for pick reconciliation |
| Patient DB | **← read-only, and only via the transaction** | — | Nothing flows in |
| Environmental sensors | → vision | `zone_id` | Temp, humidity, water, smoke |

### 10.2 Minimisation mechanics

- **Pseudonymous by construction** — `visit_track_id` rotates daily and has no
  resolution path to a person.
- **Embeddings never replicate** — they live on the edge node in Redis/Qdrant
  with a 48 h TTL and are excluded from every backup.
- **Clips by reference** — the backend stores a URI and time offset; pixels stay
  on the NVR.
- **Privacy masks applied pre-encode** — screens and prescription surfaces are
  masked before recording, not merely at display, so the sensitive pixels never
  exist on disk.
- **Redaction by default on export** — faces blurred unless privilege + reason.

---

## 11. Requirement 7 — the morning replenishment pick list

This is a **demand-forecasting problem, not a vision problem**, and it fits the
existing depot tables almost exactly: `ReplenishmentSession.pick_list` is
already a JSONB pick list flowing into `ShelfTransferEvent` with barcode + AI +
depot-checkpoint dual verification. The delta is a forecaster that populates it.

### 11.1 Signals, in descending order of value

1. **Chronic-refill schedule** — patients on repeat therapy have predictable
   refill dates. Highest-precision signal available and unique to a pharmacy
   platform. Most systems ignore it; it should dominate the model.
2. **Open/pending prescriptions** in the queue not yet dispensed — near-certain
   near-term demand.
3. **Dispense history** per `ndc11` with day-of-week and seasonality.
4. **Jalali calendar effects** — Nowruz, Ramadan, public holidays, and the
   pre-holiday stock-up spike. A Gregorian-only seasonality model will be
   systematically wrong for two weeks a year in both directions.
5. **Current shelf on-hand** — from `ShelfPlacement` / `StockLevel`.
6. **Shelf capacity** — `PharmacyShelf.capacity_units`.
7. **Warehouse availability and expiry** — FEFO selection from `InventoryLot`.
8. **CV correction (optional, later)** — shelf-gap detection reconciling system
   on-hand against reality, catching shrinkage and mis-scans. A *corrective*
   input, never the primary count.

### 11.2 Model

A **global LightGBM quantile regressor** over tabular features with SKU-level
features and embeddings, predicting the **q85–q90 of next-day demand** per
`ndc11`. Not the mean — the cost of a stockout is not the cost of an overstock,
and a point forecast silently assumes it is. For very sparse SKUs, Croston/SBA
as a fallback with an automatic switch based on demand intermittency.

A global model over ~10 k SKUs beats per-SKU models (most SKUs have too little
history) and beats a deep model here on both explainability and maintenance.

### 11.3 The decision layer stays deterministic

```
target      = q90_demand(horizon) + safety_stock(service_level, lead_time)
pick_qty    = clamp(target − shelf_on_hand, 0,
                    min(warehouse_available, shelf_capacity − shelf_on_hand))
pick_qty    = round_up_to_pack_size(pick_qty)
lot         = FEFO(available_lots)                # nearest expiry first
```

ML supplies one number — the demand quantile. Everything downstream is a
formula the pharmacist can read. Each line renders its own justification:
forecast, on-hand, capacity, pending Rx count, chosen lot and expiry, and the
binding constraint. This mirrors the deterministic-first pattern already used
across pricing and adjudication.

### 11.4 Flow

Nightly job (≈ 04:00) → `ReplenishmentSession` in `PICK_LIST` status →
inventory staff open it on a handheld at shift start → scan-verified picking
through the existing dual-verification path → `ShelfTransferEvent` rows →
`ShelfPlacement` updated. Cold-chain and CDS lines are flagged for the existing
special handling; **nothing moves without a human scan.** Overrides are captured
in `override_reason` and become training feedback.

### 11.5 Measurement

Shelf fill rate ≥ 98 %; stockout-hours reduced ≥ 40 % vs the pre-deployment
baseline; pick-line acceptance without edit ≥ 85 %; expiry write-off no worse
than baseline (guards against the model simply over-ordering to win on fill
rate — the failure mode this metric exists to catch).

---

## 12. Cybersecurity

| Layer | Controls |
|---|---|
| Cameras | Isolated VLAN, **no internet route**, default credentials rotated, ONVIF discovery disabled off-segment, signed firmware only, 802.1X where the switch supports it |
| Transport | RTSP over TLS or confined to the isolated VLAN; mTLS for edge → backend |
| Edge node | Full-disk encryption, secure boot, no inbound WAN, outbound-only, minimal package set |
| Backend | Existing JWT + RBAC; the vision service authenticates as a service principal on a **write-restricted DB role** (I-2) |
| Secrets | OS keychain / vault. Never in env files in the repo, never in URLs, never in logs or error strings |
| Tamper | Camera-offline, defocus, scene-change, and lens-obstruction alerts as first-class events |
| **Time** | Local NTP (PTP where available), all devices within ± 100 ms, drift monitored and alerted |
| Backup | Encrypted; **config and event DB only, never video** |
| Supply chain | Firmware signature verification; no vendor cloud/P2P relay features enabled |

Two points deserve emphasis.

**Clock discipline is a security control, not just hygiene.** Every join in this
system — track↔transaction, pick↔movement, badge↔track — is temporal. A camera
whose clock drifts by 30 s silently breaks reconciliation and, more
importantly, is an attractive attack: manipulate time, and the pick
reconciliation stops matching. NTP drift is monitored and alerted like any
other anomaly.

**The primary threat is insider misuse of the surveillance system itself** — a
manager reviewing one employee's day, or un-blurring a customer out of
curiosity. Technical mitigations: reason-required access, per-view audit
logging, rate limits on clip retrieval, quarterly access review, and a
standing "watching the watchers" report delivered to the owner and the data
protection lead showing who viewed what.

---

## 13. Privacy by design, retention, RBAC

### 13.1 Measures

DPIA completed **before** any camera is energised. Signage at every entrance in
Persian and English naming the controller and purpose. No audio (I-8). No
cameras in the prohibited zones of §3.1. Privacy masks applied pre-encode. Face
blur by default in all UI. A **purpose registry** binding each model and camera
to declared purposes, technically enforced in configuration — a model may not be
invoked for a purpose absent from its declaration.

**Staff consultation before deployment.** Beyond the legal position, this is
what determines whether the system survives contact with the people it watches.
Policy must state explicitly that the system is **not** used for individual
productivity scoring, and the aggregate-only reporting of §9.4 should be
demonstrated, not merely promised.

### 13.2 Retention schedule

| Class | Retention | Basis |
|---|---|---|
| Video, general zones | 15–30 days | Shortest that meets business need |
| Video, high-risk (Z-CDS, Z-CAGE, Z-DOCK) | 90 days | Investigation window |
| Evidence clips on an open case | Case closure + 12 months, legal-hold flag | Accountability |
| Event metadata (no imagery) | 24 months | Analytics |
| Aggregated metrics (non-personal) | Indefinite | Not personal data |
| **ReID embeddings** | **≤ 48 h** | I-7 |
| Staff face templates (consented) | Employment + 30 days; immediate on withdrawal | Consent |
| Audit logs | 7 years | Longest — it is the accountability record |

Automated purge jobs emit a **proof-of-deletion record**; purge failure is a
P1 alert, because silent retention failure is the most common way a compliant
design becomes a non-compliant system.

### 13.3 Roles

| Role | Live | Recorded | Un-blur | Export | Link review | Config | Audit |
|---|---|---|---|---|---|---|---|
| Pharmacist / counter | Own zone | — | — | — | — | — | — |
| Shift supervisor | ✓ | 48 h | — | — | ✓ | — | — |
| Security officer | ✓ | Full | ✓ (reason) | ✓ (reason) | ✓ | — | — |
| Inventory lead | Warehouse | 7 days | — | — | Pick recon | — | — |
| Owner / DPO | ✓ | Full | ✓ | ✓ | ✓ | ✓ | ✓ |
| System admin | — | — | — | — | — | ✓ | ✓ |

Deliberate: the system admin can configure but cannot watch; the security
officer can watch but cannot reconfigure. Separation of duty on the control
that matters most.

---

## 14. Model training and lifecycle

- **Start pretrained.** COCO person detection, public ReID weights. Fine-tune
  only what is genuinely site-specific: bay states, pick detection, uniform
  presence, PPE.
- **De-identify training data.** Faces are blurred in training frames for every
  task that is not face verification — no task above needs faces to learn
  "person reaching into a bay".
- **Split by time and camera, never randomly.** Random frame splits leak
  adjacent frames across the boundary and inflate metrics dramatically. Hold out
  whole days and whole cameras.
- **Report worst-camera, not mean.** A mean recall of 0.95 hiding a 0.78 camera
  is a blind spot with good statistics.
- **Stratified fairness testing.** Detection recall must be measured separately
  across: loose/full-length dark garments (a substantial share of customers, and
  a documented weak point in public person detectors), wheelchair and mobility-
  aid users, children (small boxes), and low-light/IR conditions. Any stratum
  below target blocks release. This is a concrete reliability requirement, not a
  gesture.
- **Shadow mode.** Every new model runs 2–4 weeks logging-only alongside the
  incumbent before promotion.
- **Registry.** Version, training-data hash, eval report, declared purposes,
  approver, rollback path.
- **Drift monitoring.** Per-camera detection-rate distributions, ReID score
  distributions, FP rates. Alert on distribution shift — the usual causes are
  benign (a shelf moved, a light failed) and worth knowing anyway.

---

## 15. False-positive management

Alarm fatigue is how these systems die. The controls are mechanical.

- **Every rule declares:** debounce (N consecutive frames / T seconds),
  confidence floor, zone mask, schedule mask, and suppression list.
- **Two-stage evaluation:** a cheap trigger gates an expensive verifier.
- **Alert budget.** A hard cap per zone per hour. Exceeding it **auto-demotes
  the rule to log-only** and files a tuning ticket. The system throttles itself
  rather than training operators to ignore it.
- **Threshold setting from data, not intuition.** Every rule ships in log-only
  mode for two weeks; thresholds are then set from the observed distribution
  (e.g. dwell threshold at the 99th percentile of normal dwell for that zone).
- **Benign-pattern exceptions.** The cleaner at 21:00 daily is a schedule
  exception, not a model retrain.
- **Weekly FP review** per rule, with the reviewer's true/false marks feeding
  the tuning loop. A rule persistently above its FP target is retuned or
  retired — no rule is permanent by default.
- **Explainability is a hard gate (I-4).** An alert that cannot state its rule,
  thresholds, and evidence is a bug, and the UI renders it as one.

---

## 16. Regulatory compliance

**This is design guidance, not legal advice. Iranian counsel must review before
deployment.**

Iran has no single comprehensive data protection statute in force (a personal
data protection bill has been pending for several years). The applicable
instruments are sectoral and constitutional, and include the Constitution's
privacy protections, the Computer Crimes Act (1388/2009) on unlawful access to
and interception of private data, the Electronic Commerce Act (1382/2003)
provisions on personal data, Ministry of Health confidentiality obligations for
medical records, and Iranian FDA pharmacy licensing conditions. Employment law
constrains staff monitoring and generally requires notice and consultation.

Because the statutory position is thin and moving, **the design targets
GDPR-equivalent principles as a voluntary ceiling**: lawfulness, purpose
limitation, minimisation, accuracy, storage limitation, integrity,
accountability, plus special-category treatment (explicit consent or an
equivalently strong basis) for biometric and health-adjacent data. This is not
gold-plating — it is the cheapest way to be robust to a legal regime that will
tighten, and it happens to describe good engineering anyway.

Practical obligations adopted regardless: DPIA before deployment; entrance
signage; a documented lawful basis per processing purpose; a retention schedule
with automated enforcement; subject access and objection procedures; a
processor agreement with any annotation vendor; and evidence chain-of-custody
(hash at export, exporter recorded) for any clip that could enter a legal
proceeding. Fire and water detection remain the responsibility of certified
systems (I-10).

---

# Deliverables

## A. Recommended technology stack

| Layer | Choice | Why |
|---|---|---|
| Cameras | PoE H.265 ONVIF S/T, 4–8 MP, WDR ≥120 dB, IR, **mic disabled** | Vendor-neutral; ONVIF avoids lock-in |
| Network | Managed PoE+ switches, isolated camera VLAN, UPS | Segmentation is the primary camera control |
| VMS / NVR | **Frigate + go2rtc** (MVP) → evaluate **Nx Witness** for production | Frigate integrates detection tightly and is cheap to run; Nx has a real API and on-prem licensing if VMS features are needed |
| Inference runtime | TensorRT / ONNX Runtime; NVIDIA DeepStream if stream count grows | Batched inference + hardware decode |
| Detection | YOLO11-s (INT8) or RT-DETR | Well-supported, quantises cleanly |
| Tracking | BoT-SORT with camera-motion compensation | Robust to pans/vibration; ByteTrack fallback |
| ReID | OSNet-AIN or CLIP-ReID | Small, fast, good under uniform clothing |
| Pose | RTMPose-s | Fall, reach-in, unsafe handling |
| Barcode | zbar / PaddleOCR (DataMatrix) | Identity of goods |
| Vector store | **Qdrant** (already in the stack, port 6334) with 48 h TTL | Reuse; TTL enforces I-7 |
| Edge bus | MQTT (Mosquitto) | Frigate speaks it natively |
| Backend | **Existing** FastAPI + SQLAlchemy async + Postgres + Alembic | No new platform |
| Time-series | TimescaleDB extension on the existing Postgres | Event/metric volume |
| Realtime UI | Existing WS-fed queue pattern | Consistency with the current dashboards |
| Frontend | Existing React 19 + Vite + Tailwind RTL, Mission Control design system | New «امنیت و پایش» dashboard |
| Forecasting | LightGBM + pandas (already a hard dependency) | Explainable, right size for ~10 k SKUs |
| Observability | Prometheus + Grafana + Loki | Per-camera health, FP rates, purge success |
| **LLM** | **None, anywhere in this pipeline** | Consistent with the existing PHI egress gate; narratives are template-rendered |

**Hardware fallback for constrained procurement.** If NVIDIA GPUs are hard to
source, **Hailo-8 M.2 accelerators (26 TOPS)** work well with Frigate at roughly
one module per 12–16 streams, or Intel Arc + OpenVINO. Plan for this: it changes
model selection (favour YOLO-nano/small INT8) but not the architecture.

## B. Components and data flow

```
┌─ SITE (pharmacy or warehouse) ─────────────────────────────────────────────┐
│                                                                            │
│  Cameras ──RTSP/VLAN──┐                                                    │
│  Env sensors ─────────┤                                                    │
│  Access control ──────┤                                                    │
│                       ▼                                                    │
│              ┌────────────────────────────────────┐                        │
│              │ EDGE NODE                          │                        │
│              │  decode → detect → track → ReID    │   Qdrant (48 h TTL)    │
│              │  → homography → zone engine        │◄─►Redis (hot state)    │
│              │  → rule engine → events (MQTT)     │                        │
│              └──────────────┬─────────────────────┘                        │
│                             │                          ┌──────────────┐    │
│                             ├─ clip bookmark ─────────►│ NVR (video)  │    │
│                             │                          └──────────────┘    │
└─────────────────────────────┼──────────────────────────────────────────────┘
                              │ outbound mTLS, batched, EVENTS ONLY
                              ▼
┌─ PHARMPILOT BACKEND :8001 ─────────────────────────────────────────────────┐
│  /api/v1/vision/ingest → vision_* tables (write-restricted DB role)         │
│         │                                                                  │
│         ├─ reconciliation jobs ──► POS · WMS · access log · schedule        │
│         ├─ analytics rollups ────► queue/wait/service metrics               │
│         ├─ anomaly engine ───────► alerts + review queue                    │
│         └─ replenishment forecaster ──► ReplenishmentSession.pick_list      │
│                                                                            │
│  React dashboard: «امنیت و پایش» · review queue · pick list · audit         │
└────────────────────────────────────────────────────────────────────────────┘

Clinical tables ──────► read-only, and only via transaction references.
                        No path exists in the reverse direction. (I-2, I-3)
```

## C. Database entities

New tables, all prefixed `vision_`, in their own schema with a dedicated role.
**Foreign keys point from `vision_*` into clinical/inventory tables and never
the reverse.**

| Entity | Key fields |
|---|---|
| `vision_camera` | id, site_id, zone_id, role, model, resolution, fps, homography, privacy_masks, purposes[], health_state |
| `vision_zone` | id, site_id, code, class (public/service/restricted/high_risk/safety), polygon, retention_days, armed_schedule |
| `vision_portal` | id, zone_a, zone_b, transit_mean_s, transit_sd_s, learned_at |
| `vision_visit_track` | id (ephemeral), site_id, opened_at, closed_at, entry_portal, exit_portal, quality_score, **expires_at** |
| `vision_track_segment` | id, visit_track_id, camera_id, t_start, t_end, ground_path, quality |
| `vision_subject_binding` | id, visit_track_id, staff_id→`staff.id`, method, confidence, valid_from, valid_until, evidence_ref, degraded_at |
| `vision_access_event` | id, door_id, staff_id, granted, occurred_at, source |
| `vision_presence_event` | id, visit_track_id, zone_id, entered_at, exited_at, dwell_s |
| `vision_queue_event` | id, visit_track_id, joined_at, service_started_at, service_ended_at, abandoned |
| `vision_transaction_link` | id, visit_track_id, transaction_id→`prescriptions.id`/POS, score_s, distance_m, margin, band (auto/review/discard), decided_by, decided_at, reversed_by |
| `vision_pick_event` | id, zone_id, bay_id→`pharmacy_shelves.id`, t_start, t_end, binding_id, reconciled_movement_id→`inventory_movements.id`, state |
| `vision_anomaly_rule` | id, code, zone_class, expression, debounce_s, confidence_floor, severity, schedule_mask, alert_budget_per_hour, state (log_only/active/demoted) |
| `vision_anomaly` | id, rule_id, zone_id, occurred_at, severity, **explanation** (JSONB: thresholds + evidence), clip_ref, state |
| `vision_alert_action` | id, anomaly_id, actor_id, action, reason, occurred_at |
| `vision_evidence_clip` | id, nvr_uri, t_start, t_end, redaction_state, sha256, legal_hold, expires_at |
| `vision_audit_log` | id, actor_id, action, target_type, target_id, before, after, reason, occurred_at, **prev_hash, row_hash** |
| `vision_model_version` | id, task, version, artifact_hash, training_data_hash, eval_report, purposes[], approved_by, promoted_at |
| `vision_consent` | id, staff_id, purpose, granted, method, recorded_at, withdrawn_at |
| `vision_retention_job` | id, policy_id, ran_at, rows_purged, bytes_purged, **proof_hash**, status |
| `replenishment_forecast` | id, pharmacy_id, ndc11, horizon_date, q50, q85, q90, features (JSONB), model_version_id |

Reused unchanged: `SurveillanceEvent`, `ReplenishmentSession`,
`ShelfTransferEvent`, `ShelfPlacement`, `PharmacyShelf`, `InventoryMovement`,
`StockLevel`, `InventoryLot`, `Staff`.

## D. APIs

All under `/api/v1/vision`, guarded by the existing `require_permission`.

| Method | Path | Permission | Purpose |
|---|---|---|---|
| POST | `/ingest/events` | `vision:ingest` (service principal) | Batched edge → backend. **Schema rejects any binary/image field.** |
| POST | `/ingest/heartbeat` | `vision:ingest` | Camera health, clock drift, model versions |
| GET | `/zones` · `/cameras` | `vision:read` | Topology and health |
| GET | `/tracks?zone=&from=&to=` | `vision:read` | Ephemeral tracks (no identity) |
| GET | `/metrics/queue?from=&to=` | `vision:read` | Wait, service, abandonment |
| GET | `/metrics/staffing` | `vision:read` | Availability vs schedule |
| GET | `/anomalies?state=&severity=` | `vision:read` | Alert list with explanations |
| POST | `/anomalies/{id}/action` | `vision:review` | Ack / dismiss / escalate + reason |
| GET | `/links/review` | `vision:review` | Ambiguous transaction links |
| POST | `/links/{id}/decide` | `vision:review` | Confirm / reject / defer |
| POST | `/links/{id}/reverse` | `vision:review` | Reversal (new event, never delete) |
| GET | `/clips/{id}` | `vision:clip` | Redacted by default |
| POST | `/clips/{id}/unredact` | `vision:clip:elevated` | Reason required; audited |
| POST | `/clips/{id}/export` | `vision:export` | Hash + chain-of-custody row |
| GET | `/picks/unreconciled` | `inventory:read` | Daily reconciliation report |
| POST | `/picks/{id}/resolve` | `inventory:write` | Explain / match / escalate |
| GET | `/replenishment/proposal` | `inventory:read` | Morning pick list + per-line justification |
| POST | `/replenishment/accept` | `inventory:write` | Creates `ReplenishmentSession` |
| GET | `/audit?actor=&from=` | `vision:audit` | Audit log (append-only) |
| GET | `/retention/status` | `vision:audit` | Purge proofs |
| WS | `/stream/{site_id}` | `vision:read` | Live events (existing WS pattern) |

## E. Anomaly-detection rules

`S` severity: 1 info · 2 review · 3 alert · 4 critical.
Every rule ships **log-only for 2 weeks**; thresholds shown are starting points
to be replaced by observed percentiles.

| Code | Trigger | Debounce | S | Action | FP control |
|---|---|---|---|---|---|
| PH-01 | Unbound person in Z-CDS | 3 s | 3 | Alert supervisor | Schedule mask; binding grace 10 s |
| PH-02 | Loiter near CDS > 120 s, unbound | 120 s | 2 | Review | Percentile-set from normal dwell |
| PH-03 | Reach across counter boundary plane | 2 frames | 3 | Alert | Pose confidence floor 0.7 |
| PH-04 | ≥ 3 entry/exit cycles in 20 min, no transaction | — | 2 | Review | Excludes bound staff |
| PH-05 | Queue > N for > 10 min with idle counter position | 60 s | 2 | Ops notification | Requires POS idle confirmation |
| PH-06 | Wait time > 2× rolling median | 5 min | 1 | Dashboard | Aggregate only |
| PH-07 | **Fall** — keypoints horizontal, static > 10 s | 10 s | 4 | Immediate | Accepts FP deliberately |
| PH-08 | Crowd > N with arrival rate > 2× baseline | 30 s | 2 | Review | Zone-specific N |
| PH-09 | Duress signal (existing protocol) | 0 | 4 | Immediate | Existing pathway |
| PH-10 | Restricted-zone entry with no access event ± 30 s | 5 s | 3 | Alert | Access-log join |
| WH-01 | Motion in armed zone outside schedule | 3 s | 4 | Alert + escalate | Schedule authoritative; holiday calendar |
| WH-02 | Pick event with no `InventoryMovement` ± 5 min | Batch | 2 | **Daily report** | General aisles: report only |
| WH-03 | Same, in **Z-CAGE** | 60 s | 4 | Immediate | Two-person review before consequence |
| WH-04 | Dispatch-line crossing carrying object, no dispatch record | 10 s | 3 | Alert | Object-class floor; dock schedule |
| WH-05 | Dwell in Z-CAGE > 5 min | 300 s | 3 | Review | Cycle-count exception window |
| WH-06 | **Blocked egress** — object in ROI > 60 s | 60 s | 3 | Alert | Static-object model; person-excluded |
| WH-07 | Bay empty but system on-hand > 0 | Batch | 2 | Reconciliation | Bay-state confidence floor |
| WH-08 | Bay occupied but system on-hand = 0 | Batch | 2 | Reconciliation | Same |
| WH-09 | Unsafe handling (deep flexion under load, rack climbing) | 3 s | 1 | **Aggregate coaching** | Never individually punitive |
| WH-10 | Cold-room door open > 90 s | 90 s | 3 | Alert | Sensor-corroborated |
| WH-11 | Temp/humidity excursion beyond lot range | 120 s | 4 | Alert + quarantine flag | Sensor primary |
| WH-12 | Water sensor active ∨ CV pooling | 10 s | 4 | Alert | **Sensor primary**, CV supplementary |
| WH-13 | CV smoke/flame hint | 5 s | 3 | **Verify** (never replaces panel) | I-10 |
| WH-14 | Vehicle at dock outside delivery window | 60 s | 2 | Review | Schedule mask |
| SY-01 | Camera offline / defocus / obstructed | 60 s | 3 | Ops alert | — |
| SY-02 | Clock drift > 100 ms | 300 s | 3 | Ops alert | Security-relevant |
| SY-03 | Track count vs door count drift > 2 | 300 s | 2 | Tracker health | Self-diagnostic |
| SY-04 | Rule exceeds alert budget | 1 h | 2 | **Auto-demote to log-only** | Anti-fatigue |
| SY-05 | Retention purge failed | — | 4 | Compliance alert | Silent failure is the risk |

## F. Deployment phases

| Phase | Duration | Content | Exit criteria |
|---|---|---|---|
| **0 — Remediate & prepare** | 3–4 wk | R-1…R-7; DPIA; legal review; staff consultation; coverage survey; cabling, VLAN, UPS, signage; policy pack | Remediation tests green; DPIA signed; staff briefed |
| **1 — MVP: see and record** | 4–6 wk | Recording, camera health, entry counting, zone dwell, after-hours arming, blocked egress. **No ReID, no bindings.** All rules log-only | 99 % recording uptime; door count within 2 % of manual audit |
| **2 — Track and bind** | 6–8 wk | Calibration, multi-camera tracking, portal learning, access-control binding, queue/wait/service metrics, transaction linking (auto band only) | Cross-camera IDF1 ≥ 0.70; binding precision ≥ 0.99; wait-time MAE ≤ 30 s |
| **3 — Warehouse integrity** | 6–8 wk | Pick detection + reconciliation, Z-CAGE real-time, bay states, safety and environmental fusion | WH-02 report precision ≥ 0.60; WH-06 recall ≥ 0.98 |
| **4 — Replenishment ML** | 4–6 wk | Forecaster, pick-list generation into `ReplenishmentSession`, handheld flow, feedback loop | Line acceptance ≥ 85 %; fill rate ≥ 98 % |
| **5 — Production hardening** | 4 wk | Shadow-mode pipeline, model registry, DR, full RBAC audit, retention proof verification, sign-off | All §G targets met for 30 consecutive days |

**Phase 4 has no dependency on cameras and should run in parallel from week 1.**
It is the highest business value per unit of risk in the entire programme, and
holding it behind the camera work is the most common way this kind of project
delivers nothing for six months.

## G. Accuracy and performance targets

| Metric | Target | Measurement |
|---|---|---|
| Person detection recall (covered zones) | ≥ 0.95 @ IoU 0.5; **worst camera ≥ 0.90** | Quarterly labelled sample, 2 k frames |
| Recall, loose/full-length dark garments | ≥ 0.92 | Stratified subset — release blocker |
| Recall, mobility-aid users / children | ≥ 0.90 | Stratified subset — release blocker |
| Per-camera MOTA / IDF1 | ≥ 0.75 / ≥ 0.80 | Annotated 30-min segments |
| Cross-camera IDF1 | ≥ 0.70 | Multi-camera annotated segment |
| Duplicate-identity rate | ≤ 3 % of visits | vs door-count ground truth |
| Track fragmentation | ≤ 2 breaks per 5-min visit | Annotated sample |
| **Staff-binding precision** | **≥ 0.99** | vs access-control log |
| Transaction-link precision (auto band) | ≥ 0.98 | Manual audit, 200 transactions |
| Transaction unlinked rate | ≤ 10 % | Continuous |
| Wait-time MAE | ≤ 30 s | vs stopwatch audit |
| Alert precision, critical rules | ≥ 0.85 | Reviewer marks |
| False alerts | ≤ 2 / camera / day | Continuous |
| Blocked-egress recall | ≥ 0.98 | Quarterly drill |
| Fall recall / precision | ≥ 0.90 / ≥ 0.50 | Staged drill — FP accepted |
| Alert latency (capture → UI) | < 3 s p95 | Synthetic probe |
| Metric freshness | < 60 s | Continuous |
| Recording uptime / gap | ≥ 99.5 % / < 0.1 % | NVR self-report + probe |
| Clock skew | < 100 ms all devices | Continuous |
| Purge job success | 100 %, verified monthly | Proof-of-deletion records |
| Pick-list line acceptance | ≥ 85 % | Session feedback |
| Shelf fill rate / stockout hours | ≥ 98 % / −40 % vs baseline | Inventory analytics |
| Expiry write-off | ≤ baseline | Guards against over-ordering |

## H. Risks and safeguards

| Risk | L | I | Safeguard |
|---|---|---|---|
| **Function creep** — "since we have the cameras…" | **H** | **H** | Purpose registry technically enforced; every new purpose requires DPIA amendment + owner sign-off |
| **Existing biometric subsystem ships as-is** | **H** | **H** | Phase 0 R-1…R-7 blocks all later phases; tests assert it |
| Insider misuse of the system | M | H | Reason-required access, per-view audit, quarterly review, watching-the-watchers report |
| False theft accusation | M | **H** | No rule outputs an accusation; two-person review + corroborating non-video record before any consequence |
| Staff relations / legal challenge to monitoring | M | H | Consultation before deployment; aggregate-only performance reporting; written no-productivity-scoring policy |
| Detection bias on covered clothing / mobility aids | M | H | Stratified acceptance testing as a release blocker |
| Over-reliance on video for fire/water | M | **H** | I-10 — certified detectors primary; video is a hint |
| Clock drift silently breaking reconciliation | M | M | SY-02; NTP monitored as a security control |
| Camera as network entry point | M | H | Isolated VLAN, no internet route, signed firmware, 802.1X |
| Storage exhaustion → silent retention failure | M | M | SY-05 as P1; capacity alarms at 70/85 % |
| GPU procurement constrained | **H** | M | Hailo-8 / OpenVINO fallback designed in from the start |
| Power interruption | **H** | M | UPS per site; graceful shutdown; NVR write-ahead |
| Forecast degradation after a demand shock | M | M | Quantile monitoring, auto-fallback to Croston, human override always available |
| Alert fatigue | **H** | M | SY-04 auto-demotion; alert budgets; weekly FP review |
| Scope/schedule overrun before any value delivered | M | M | Phase 4 runs in parallel from week 1 |

## I. Infrastructure estimate

**Cameras and network** — ~32 cameras (16 + 16), 2× 24-port PoE+ managed
switches, 1 UPS per site (3 kVA, ≥ 30 min).

**Storage** (32 cameras, 4 MP H.265 smart-codec, ~3 Mbps average):

```
32 × 3 Mbps = 96 Mbps = 12 MB/s ≈ 1.04 TB/day
30-day general retention                    ≈ 31 TB
8 high-risk cameras × 60 extra days         ≈ 16 TB
+20 % headroom                              ≈ 56 TB usable
→ 8 × 12 TB surveillance-grade in RAID6 = 72 TB usable
```

**Compute** — two edge nodes, one per site, ~16 cameras each. Per node: 8–16
core CPU, 32–64 GB RAM, 1 TB NVMe, and an RTX A4000-class GPU (or 2× Hailo-8 in
the constrained path). Splitting by site is not only for autonomy: **NVDEC
decode capacity, not GPU FLOPs, is the real constraint** at this stream count,
and two nodes halve it. Sizing check: 32 × 12 FPS = 384 detections/s; YOLO11-s
INT8 on an A4000 sustains ~500 FPS with ReID and pose overhead — roughly 40 %
headroom per node.

**Backend** — the existing Postgres and FastAPI deployment plus TimescaleDB;
add ~200 GB for event data at 24-month retention.

**Latency budget** (capture → alert, target < 3 s p95): encode + network
0.2–0.4 s · decode 0.05 s · detect + track 0.05 s · **rule debounce 1–2 s
(dominant, and intentional)** · publish 0.1 s · UI 0.2 s.

Costs are deliberately omitted: hardware pricing in this market varies too
widely for an estimate to be useful, and the fallback compute path exists
precisely because procurement is uncertain. The BOM above is what to price.

---

## Open questions for the owner

1. Site layout, dimensions, and whether pharmacy and warehouse share a network.
2. Is there an existing access-control system, and does it expose an API or
   webhook? Phase 2 depends on it — badge events are the primary binding.
3. Current headcount and shift pattern (sizing for staff enrolment/consent).
4. SKU count and dispense volume per day (forecaster sizing).
5. Is there an incumbent VMS/NVR to integrate with or replace?
6. Who is the named data protection lead, and is Iranian counsel engaged?
7. Confirm the recommendation to **not** implement customer face recognition —
   this is the one decision that most changes the system's legal profile, and it
   should be an explicit, recorded owner decision rather than a default.
