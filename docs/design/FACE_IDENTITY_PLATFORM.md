# Face Identification of Every Visitor — Design

Revises the identity sections of `SURVEILLANCE_PLATFORM.md` under the owner's
decision that **every person entering the pharmacy is identified by face and
linked to the client database**, with a temporary UUID for anyone not yet known.

Status: **proposal**. Not implemented, not approved.

> **Verification caveat.** Five independent design passes produced this
> (accuracy, identity lifecycle, clinical safety, legal, implementation). Three
> adversarial passes — attacking the accuracy mathematics, hunting patient-harm
> paths, and checking completeness — **did not run**; they hit the session
> limit. Claims sourced from the design passes are marked ◇ where I have not
> personally verified them against code or first principles. Everything in §1 I
> verified myself.

---

## 1. A blocking finding, unrelated to face recognition

The clinical pass went looking for the dispense gate that identity assurance
was supposed to protect. There isn't one. I verified both of these directly.

**`POST /pos/collect-payment` marks any prescription dispensed, by raw SQL.**
[pos.py:169](services/platform/routers/pos.py:169):

```sql
UPDATE prescriptions
SET    status = 'dispensed', updated_at = :now
WHERE  id = :rx_id AND status NOT IN ('cancelled', 'voided')
```

Every status except `cancelled`/`voided` is eligible — including
**`DUR_HOLD`**, the status whose entire purpose is to stop a dispense on a
dangerous interaction. It bypasses `RxStateMachine`, the state-event hash
chain, and the EPCS check for controlled substances. The comment calls it
"best-effort". Collecting payment on a held prescription dispenses it.

**`PENDING_DUR → PENDING_VERIFICATION` is unconditional.**
[state_machine.py:30](services/core/pharmacy_workflow/state_machine.py:30) — no
code requires that a DUR was run, read, or overridden.

◇ The pass further reports that `dur_overrides.py:16` documents "the pharmacist
must explicitly POST an override before the system allows the dispensing step to
proceed", that this is untrue, and that `dur_override_events` is a
`CREATE TABLE IF NOT EXISTS` log with `rx_id TEXT` and no foreign key, read only
by analytics.

**Consequence for this design.** Identity assurance is worth building, but it
cannot be justified as protecting a downstream gate, because none exists. The
DUR pathway is advisory today. **I recommend fixing the POS bypass before any
work here** — it is a smaller change with a larger safety return, and it
doesn't depend on a single camera.

---

## 2. The two-desk model

Your clarification is the most important structural input, and it resolves the
safety tension cleanly, because the two desks have genuinely different identity
authorities.

| | Rx reception desk | OTC desk |
|---|---|---|
| **Identity authority** | Online insurance system: **national ID + name**; scanned or keyed prescription as a second checkpoint | Face, plus whatever the customer volunteers |
| **Face's job** | Pre-fetch a candidate before they reach the counter | Primary identifier |
| **Assurance reachable** | IAL3 (verified) | IAL1–2 |
| **Clinical services** | Full — history, DUR, trajectory | Elicitation + basket-intrinsic checks |
| **Clinical writes** | Permitted | **Never** |
| **Your risk tolerance** | — | Mistakes not fatal (accepted) |

So face never has to carry the weight of a dispensing decision. At the Rx desk
the insurance platform is a genuine third-party assertion; at the OTC desk the
stakes are low by your own assessment, and the design leans into that rather
than fighting it.

### The one exception I'd still engineer out

At OTC assurance the system **may add a warning, and may never suppress one.**
A wrong or empty history producing *"no interactions found"* is the harm path —
selling diclofenac to a warfarin patient because the chart the system pulled
belonged to someone else. A wrongly *added* warning costs seconds; a wrongly
*removed* one is silent.

◇ The clinical pass sharpened this in a way I think is right and hadn't
occurred to me: **all three of "genuinely clean", "wrong chart", and "no chart"
currently render as the same green tick.** The fix is an interface invariant,
not a threshold — below IAL3 the system renders *"history unavailable — verify
manually"*, never *"no interactions found"*. `"No interactions"` is itself a
clinical claim and needs the same assurance as any other.

---

## 3. Identity Assurance Levels

All five passes converged on this independently, which is the strongest signal
in the whole exercise.

| IAL | Basis | Permits |
|---|---|---|
| **0** | Provisional / no match | Queue position, footfall. Nothing clinical. |
| **1** | **Face alone**, any score | Non-clinical personalisation; read the *provisional* record only |
| **2** | Face **+ one independent non-biometric factor** — Luhn-valid national code confirmed against `patients.national_id`, Rx barcode, or insurance member match | Read the patient record; class-level elicitation |
| **3** | Insurance eligibility returned a member record **and** affirmative pharmacist confirmation | Full clinical services; **the only level permitting a clinical write** |

**No similarity value, including 1.0, promotes anyone above IAL1.** Face is one
factor. This is compatible with your decision — everyone is still identified,
everyone still gets a provisional UUID, everyone still gets personalised
service. What changes is that the *safety-critical consumer* requires a basis,
not a score.

◇ The arithmetic behind that: at FPIR 1e-4 and ~400 searches/day × 300 days, an
auto-committing system yields roughly 12 wrong-chart-loads per year. A two-second
four-digit confirmation cuts the residual by about four orders of magnitude.

◇ Enforce at the **database layer**, not in application code — there are at
least two raw-SQL write paths that bypass the ORM entirely
(`identity_orchestrator.py:257`, and the `pos.py` bypass in §1).

---

## 4. Accuracy engineering

### 4.1 Shrink N — the only lever that actually works

`FPIR ≈ M · FMR` where M = N identities × m templates. Required per-comparison
FMR at FPIR 1e-3: ◇

| Gallery | N | Required FMR | Measurable? |
|---|---|---|---|
| **Tier A** — expected today (pending/ready Rx, chronic refill due ±7 d, booked consult) | ≈400 | 8e-7 | yes — achievable, TAR ≈0.94 unoccluded |
| **Tier B** — active roster, 18 months | ≈6,000 | 5e-8 | marginal |
| **Tier C** — full roster | 20,000 | 7e-9 | **no** — below any published operating point |

This is the single best idea the exercise produced, and it's one a biometrics
vendor cannot sell you: **PharmPilot already holds the non-biometric context
that defines Tier A.** `prescriptions.status` and the chronic-refill schedule
tell you who is plausibly walking in today. Searching 400 instead of 20,000 buys
roughly 50× of FMR budget for free.

Escalate A → B → C, with the permitted band tightening at each tier.

### 4.2 Occlusion is the majority case, so it's a calibration axis

Hijab, chador, and masks aren't edge cases here. ◇ Mechanically:
`aligner.py:22` fits a similarity transform to five landmarks including nose tip
and both mouth corners — **a surgical mask destroys three of the five**, and the
aligner's own docstring says misalignment costs up to 30% recognition rate.
Hijab removes hair, ears, jawline, forehead.

So: **separate `ImpostorStats` per occlusion stratum** — `{unoccluded,
mask, hijab, hijab+mask, chador, chador+mask}` — selected at query time by an
occlusion classifier, holding the FPIR target in the *worst* stratum. A single
pooled calibration returns a threshold that is too **low** for veiled faces,
and the FPIR guarantee silently fails for exactly the group it most affects.

The hook already exists and is unused: `ImpostorStats.population`
([thresholds.py:57](services/biometric/identity_resolution/thresholds.py:57)),
whose docstring example is literally `"counter-1, hijab subset"`.

Do **not** raise one global threshold to cover the worst stratum — that
collapses match rate for everyone else and pushes the population into
provisional identities, reintroducing the empty-history false negative through
the other door.

### 4.3 The rest of the accuracy stack ◇

| Decision | Why |
|---|---|
| **AdaFace IR-101 (WebFace12M)** replaces ArcFace R100, then domain fine-tune on site-collected veiled/masked data with an occlusion-consistency loss | Quality-adaptive margin, designed for surveillance-grade capture. IJB-C TAR@FMR1e-6 ≈0.960 vs ≈0.935. Same 112×112 input and 5-point contract → drop-in for the extractor |
| **Periocular parallel channel**, gated to `{mask, hijab+mask, chador+mask}` only | Weaker alone, but its failures are *uncorrelated* with mask-damaged full-face; fusion recovers 3–8 pts TAR@FMR1e-4 exactly where we're weakest. Running it on unoccluded faces only adds noise. Caveat: eyeglasses and eye cosmetics damage precisely this region |
| **Feature-level pooling over K=5 frames — never score-max** | Score-max over K frames multiplies effective FMR by K, silently burning 5× the FPIR budget while looking like an improvement on a naive TAR plot |
| **AS-Norm** before any threshold; thresholds stored per `(camera, tier, occlusion, model, cohort)` in a table, never as constants | A raw cosine threshold doesn't transfer — sensor response, WDR, gamma, H.265 and lens MTF shift impostor μ by 0.02–0.05 and σ by ±30% between camera models, so an FMR of 1e-6 on camera A can be 1e-4 on camera B |
| **Margin = difference on normalised scores, between distinct identities** | Already implemented in the fix (`best hit per identity`). Ratio tests are rejected: on z-scored values a ratio isn't scale-meaningful and inverts sign when s₂ < 0 |
| **Kinship-aware doubled margin; twins excluded from face commit** | This gallery is family-dense *by construction* — `identity_orchestrator.py:139` auto-enrols the insurance-linked family, so siblings are systematically both enrolled. Siblings score 0.15–0.35 where unrelated pairs score ~0.0±0.05 — 3–7σ into the impostor tail, exactly where the threshold lives. **The highest-probability wrong-chart pathway in this deployment** |
| **RGB + active 850 nm NIR** at both desks | LCD/OLED emit nothing at 850 nm — a replay renders as a dark rectangle. Printed photos fail on ink/paper NIR reflectance *and* planarity. Passive RGB PAD lab numbers (APCER <1%) don't survive domain shift (15–40% cross-dataset), and a mask removes the micro-texture it relies on |
| **Challenge-response is step-up only** (≤2% of encounters) | 3–6 s per person kills the queue, staff learn to skip it, and "remove your mask" is socially loaded here |

### 4.4 Capture geometry ◇

**Dedicated face cameras, not the operational counter camera.** 1.55 m eye
level, 0.7–1.3 m standoff, 6 mm at the Rx desk / 4 mm at OTC on 1/2.8″ 4 MP,
pitch ≤10° down. This reverses `SURVEILLANCE_PLATFORM.md:163`, which framed the
counter camera on hands "not the customer's face" — written under the
now-superseded no-customer-FR decision.

**Frontal fill light is a mandatory line item**, 300–500 lux on the face,
uniformity ≥0.6, 4000–5000 K, CRI ≥90, exposure capped at 1/120 s. A scarf or
chador edge under overhead-only lighting casts a hard shadow across the brow and
eye sockets — landing exactly where the periocular channel needs signal. ◇ For
roughly 60% of encounters, lighting geometry is worth more than a model upgrade.

---

## 5. Identity lifecycle

### 5.1 Merge is a linkage operation, never a data movement ◇

This is the piece that makes reversal safe. Clinical facts **keep the foreign
key they were born with, forever.** Equivalence lives in a separate structure:

- `identity_subjects` — provisional people (the renamed, extended
  `customer_identities` from migration 0002)
- `identity_clusters` + `identity_cluster_members` — bitemporal, append-only,
  `member_ref = 'subject:<uuid>' | 'patient:<uuid>'`

Because nothing was moved, an unmerge is a linkage change, not a data-repair
exercise. **The question "what happens to facts created *after* a merge when
that merge is reversed?"** — the one that sinks naive designs — is answered by
recording, on every clinical write, an `attribution_basis ∈ {observed_member,
human_asserted, cluster_default, imported}` plus the observation id that opened
the encounter. Facts are routed by *what was observed*, not by which cluster was
current.

### 5.2 Split — the dangerous direction

Two people wrongly fused into one identity. Automatic **quarantine first**,
human partition second: any split detection at review severity opens a
quarantine immediately, before a human looks. Quarantine degrades reads — facts
group by origin, and CDS is barred from emitting "no interactions".

◇ On reversal, quarantined medication rows **stay in the review set** flagged
`unverified-source`; never dropped, never staled, never counted toward a clean
report. Dropping a quarantined warfarin is suppression, and if it really was
that patient's, the reversal itself becomes the harm event.

### 5.3 Tune for false-match, accept duplicates ◇

Under occlusion, bias every operating point so errors become **new provisional
subjects**, not wrong matches. Then invest in consolidation. A duplicate is a
data-quality problem; a false match is a clinical one.

### 5.4 Sever the direct biometric → patient edge ◇

Drop `BiometricIdentity.patient_id` ([biometric.py:56](shared/models/biometric.py:56))
and `Patient.biometric_identity_id`. Keep `person_links` as the family/caregiver
graph **only** — forbid `relationship='self'`, since identity equivalence now
lives in `identity_cluster_members`.

---

## 6. Consent and lawful basis ◇

**Two galleries, two lawful bases, never joined:**

| Gallery | Basis | Size |
|---|---|---|
| **Clinical** | Explicit opt-in consent | 2,000–8,000 |
| **Security** | Legitimate interest; only identities with a substantiated `SecurityEvent` | <50 |

A patient may refuse clinical biometrics and still be recorded by CCTV. The two
are separable and hard-enforced.

- **Consent as an append-only versioned ledger**, not the four booleans on
  `biometric_identities` — which I confirmed earlier are read by zero code paths.
- **Refusal recorded against `HMAC-SHA256(national_code, pepper)`**, never
  against a face template. No national code → session-scoped refusal, and the
  frame is dropped before an embedding is ever computed.
- **Hard age floor: no face under 18 is embedded.** Guardian consent covers the
  child's clinical record as normal, but cannot authorise biometric enrolment.
- **CORRECTED — template compromise is recoverable if templates are protected.**
  I originally wrote that templates cannot be reissued and that model rotation
  was the only remedy. That holds for the **raw embeddings** Tier 1 stores today,
  but not in general: **ISO/IEC 24745:2022** makes *renewability* a mandatory
  property of a protected biometric reference, alongside irreversibility and
  unlinkability — a protected template can be revoked and re-derived **without
  recapturing the biometric**. Storing ISO 24745-conformant protected references
  in Tier 1 turns a breach from "re-enrol every patient" into "revoke and
  re-derive". Tier 2 keeps the raw media under the VMK unchanged, because
  investigations need the original evidence. See
  [IDENTIFICATION_PRECISION.md](IDENTIFICATION_PRECISION.md) §4.
- ◇ Iranian position: no comprehensive in-force data protection statute and no
  authority to register with; the most directly on-point instrument is the
  professional-secrecy offence in the Islamic Penal Code (Ta'zirat). Verify with
  counsel — the responsible pharmacist's personal licence is exposed here, which
  is why they should personally sign the DPIA's clinical section.

---

## 7. Implementation delta

| Area | Decision |
|---|---|
| Vector search | **Exact brute-force NumPy, Postgres-backed** — not FAISS, not Qdrant. Already done in the fix |
| Edge/backend split | Detection, landmarks, occlusion class, quality gate, alignment, liveness **and embedding** on the desk node; **gallery search on the backend**. Only a 512-d vector + metadata crosses (~3.5 KB/probe). Crops never leave the desk |
| Detector | SCRFD-10GF ONNX primary, MediaPipe demoted to fallback; explicit occlusion classifier; 3-point alignment fallback (eye centres + nose bridge) when mouth corners are masked |
| Thresholds | `identity_match_thresholds(model_id, occlusion_bucket, fmr_target, tau, tau_review, measured_at, n_pairs)` — replacing what the fix left as a constructor argument |
| Still to fix | `PatientResolver` — remove `biometric_confidence` as an identity signal; it combines a raw cosine with fuzzy-name similarity through `1−(1−c₁)(1−c₂)` ([resolver.py:432](services/biometric/patient_resolver/resolver.py:432)), treating a cosine as an independent probability. That is the same category error `thresholds.py` was written to kill |

### Anti-click-through ◇

Two seconds buys a glance, not a parse — so the confirmation card carries
exactly five fields: **name in large Persian type, father's name, Jalali DOB,
last 4 of national code, most recent dispensed drug.** Father's name is the
field that actually discriminates in Iran, where surname collision is high and
given names cluster heavily. Plus: a 400 ms arm delay before the confirm key is
live (defeats stale-keystroke passthrough), forced two-candidate choice whenever
a runner-up sits inside the margin, and per-staff reflex detection — confirms
under 800 ms logged, >20% over 50 confirmations forces that person into
permanent two-candidate mode.

---

## 8. Phases

| Phase | Content |
|---|---|
| **0** | **Fix the POS dispense bypass (§1).** Independent of everything below |
| **1** | Consent ledger; sever biometric→patient edge; remove `biometric_confidence` from `PatientResolver`; IAL enum enforced by DB trigger |
| **2** | Capture rig at both desks — cameras, NIR, fill lighting. Enrolment of consenting patients. **No matching yet** |
| **3** | Calibration: collect impostor pairs per occlusion stratum; populate the thresholds table; run identification in **shadow mode**, logging only |
| **4** | Tier-A gallery search live at the Rx desk as pre-fetch only. Insurance lookup remains the authority |
| **5** | OTC desk: elicitation-only clinical surface, IAL1–2 |
| **6** | Identity clusters, merge/split, consolidation tooling |

Phases 2–3 cannot be compressed: **without measured impostor statistics the
engine correctly refuses to auto-accept anything**, which is now enforced in
code.

---

## 9. Targets

| Metric | Target |
|---|---|
| FPIR, Tier-A gallery, worst occlusion stratum | ≤1e-3 |
| End-to-end identification rate, **per demographic cell** | ≥0.85, max/min ratio ≤2.0 |
| Detection recall + quality-pass rate, per cell | Gated separately — see below |
| Wrong-chart-load events | **0** (structurally, via IAL) |
| Duplicate provisional subjects | ≤8%, consolidated within 30 days |
| Confirmation latency | p50 ≤2 s |
| Reflex-confirm rate | <5% per staff member |

◇ The release gate is **per-demographic-cell at a single fixed threshold**, with
no averaging — a failing cell blocks release. The subtle point: differential
performance most often enters through the **detector and the quality gate**, not
the matcher. If detection recall is lower on chador, those customers never reach
the matcher, per-cell matcher FNIR looks fine, and the *system* discriminates
while every matcher metric passes. So the headline number must be end-to-end
per-cell identification rate, not matcher accuracy.

◇ This requires amending invariant I-9 in `SURVEILLANCE_PLATFORM.md` ("no
demographic attribute is inferred, stored, or used") with a narrow carve-out:
demographics are never inferred by a model and never used as a recognition
feature, but **are** recorded in a separate consented, access-controlled
evaluation set used solely for the release gate and never joined to the
operational gallery. You cannot test for differential performance without
labels, and quietly violating the invariant to run the gate is worse than
amending it openly.

---

## 10. Open items

1. **The POS bypass (§1) needs a decision now** — it's independent of this design.
2. Adversarial verification did not run. Re-run when the session limit resets:
   `Workflow({scriptPath, resumeFromRunId: 'wf_75c19fb9-e0d'})` replays the five
   completed dives from cache and runs only the three critics.
3. Enrolment logistics: who enrols the first 2,000 patients, and when.
4. Cold start — the gallery is empty on day one; Tier-A is meaningless until the
   roster is enrolled.
5. Whether the security gallery is built at all. It is separable and small; it
   is also the part with the least clinical justification.
