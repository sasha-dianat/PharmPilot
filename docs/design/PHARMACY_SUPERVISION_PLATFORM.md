# Integrated Pharmacy Supervision Platform

Master design unifying the clinical, security, inventory, operational and staff
domains. Supersedes nothing; it is the layer that decides **which technology
does which job** and how the existing PharmPilot subsystems compose.

Status: **proposal**. Companion documents:
[SURVEILLANCE_PLATFORM.md](SURVEILLANCE_PLATFORM.md) (zones, cameras, warehouse),
[FACE_IDENTITY_PLATFORM.md](FACE_IDENTITY_PLATFORM.md) (identity assurance),
[IDENTIFICATION_PRECISION.md](IDENTIFICATION_PRECISION.md) (modality evidence).

---

## 0. The organising principle

> **Video is for detecting what did *not* get recorded. Everything that
> generates a record should be verified against the record, not against pixels.**

Every task below is assigned to the cheapest technology that can carry it.
Video is expensive, error-prone, and legally heavy; it earns its place only
where no transactional trace exists — the absence of a scan, the movement with
no matching movement record, the person in a room at an hour with no shift.

Three evidence findings drive the whole design:

| Finding | Number | Consequence |
|---|---|---|
| **DDI alert override rate** | **90%** (CI 85–95%); **88.2%** even for *very severe* alerts | Adding alerts is, by default, adding noise. Design for specificity, not coverage. |
| **Barcode scanning cuts dispensing errors** | **~85%** | Barcode beats video for wrong-drug by an order of magnitude. Buy scanners before cameras. |
| **RFID error detection in pharmacy** | 96 of 132 errors (**73%**) | Strong for controlled-stock traceability; not a general-purpose answer. |

The 90% override figure is the single most important number here. It means a
system that emits more alerts makes the pharmacy *less* safe, because it trains
the pharmacist to dismiss. Every clinical feature below is therefore rationed.

---

## 1. Blocking prerequisite

Nothing in Domain 1 is meaningful until this is fixed. Verified directly:

**`POST /pos/collect-payment` dispenses any prescription by raw SQL.**
[pos.py:169](../../services/platform/routers/pos.py:169) sets
`status='dispensed'` `WHERE status NOT IN ('cancelled','voided')` — which
includes **`DUR_HOLD`**, the status whose purpose is to stop a dispense on a
dangerous interaction. It bypasses `RxStateMachine`, the state-event hash chain
and the EPCS check. And `PENDING_DUR → PENDING_VERIFICATION`
([state_machine.py:30](../../services/core/pharmacy_workflow/state_machine.py:30))
is unconditional — nothing verifies a DUR was run, read or overridden.

**There is currently no enforced clinical gate on dispensing.** Build the gate
before building anything that feeds it.

---

## 2. Technology assignment — the central decision

| Task | Primary | Why not video | Video's role |
|---|---|---|---|
| **Wrong drug picked** | **Barcode / DataMatrix scan** (85% error reduction) | Vision on a shelf cannot beat a scan of the actual pack | OCR fallback when barcode is damaged/absent — `package_verification` already does this |
| **Wrong dose / strength** | Barcode → NDC/IRC → catalogue check | Strength is printed small; OCR is unreliable at shelf distance | None |
| **Wrong patient** | **Insurance eligibility + national ID**, Rx barcode | Face cannot lawfully or reliably assert patient identity | Pre-fetch candidate only; never asserts |
| **Interaction / allergy / duplicate** | **Conventional software** — `interaction/engine.py` | No visual signal exists | None |
| **Counselling completed** | Audio transcript presence + pharmacist attestation | Video shows lips moving, not content | Presence/duration only |
| **Controlled-drug register** | **RFID tag per pack** + weight + register reconciliation | Video cannot count what is inside a cabinet | Cage door + person, for evidence |
| **Stock removed without transaction** | **Reconciliation**: `InventoryMovement` vs pick event | — | **This is video's core job** — the pick with no matching record |
| **Expiry / batch** | Barcode + `InventoryLot` | — | None |
| **Shrinkage** | Cycle count vs system on-hand | — | Corroborating clip only |
| **Unauthorised entry** | **Access control** (badge) | — | **Video's core job** — presence with no badge event |
| **After-hours activity** | Schedule-armed zones | — | **Video's core job** |
| **Blocked fire exit** | — | — | **Video's core job** — trivially reliable, high value |
| **Smoke / water / temperature** | **Certified detectors + sensors** | Video smoke false-alarms on steam, dust, sunlight; not code-compliant | Supplementary early hint only |
| **Queue length / wait time** | **Video** (top-down counting) | — | **Video's core job** |
| **Cold-chain excursion** | Temp/humidity sensors + door-open duration | — | Door duration only |
| **Staff at station** | Workstation login + badge | Video role-inference is unreliable and unfair | Occupancy corroboration |

**Read the pattern:** video owns four things — *absence of an expected record*,
*occupancy*, *egress safety*, and *evidence capture*. Everything else belongs to
barcode, sensors, access control, or plain software.

---

## 3. Architecture and data flow

```
┌── SITE (pharmacy / warehouse) ─────────────────────────────────────────────┐
│                                                                            │
│  Cameras ─┐   Mics (counter) ─┐   Barcode/RFID ─┐   Sensors ─┐   Badge ─┐  │
│           ▼                   ▼                 ▼            ▼          ▼  │
│    ┌──────────────────────────────────────────────────────────────────┐   │
│    │ EDGE NODE — all inference on-premises                            │   │
│    │  detect · track · face embed · speaker embed · occlusion class   │   │
│    │  zone/rule engine · quality gate · liveness                      │   │
│    └───────────┬──────────────────────────────────────────────────────┘   │
│                │ EVENTS ONLY (no frames, no crops, no raw audio)          │
│    NVR ◄───────┤ clip bookmarks                                           │
│    Evidence Vault ◄── raw media under VMK (HSM/KMS), admin-only            │
└────────────────┼───────────────────────────────────────────────────────────┘
                 │ outbound mTLS
                 ▼
┌── PHARMPILOT :8001 ────────────────────────────────────────────────────────┐
│  RECONCILIATION ENGINE  ← the heart of the system                          │
│    pick events   ↔ InventoryMovement        → unreconciled_pick            │
│    presence      ↔ access_event + schedule  → unauthorised_presence        │
│    CD register   ↔ RFID + weight + dispense → register_discrepancy         │
│    dispense      ↔ barcode scan             → unverified_dispense          │
│    transaction   ↔ counselling transcript   → counselling_gap              │
│         │                                                                  │
│         ├─► Clinical: interaction/engine.py (deterministic) → ranked alerts │
│         ├─► Triage board (existing pattern) → human disposition            │
│         └─► KPI rollups                                                    │
└────────────────────────────────────────────────────────────────────────────┘
```

**The reconciliation engine is the product.** Cameras, tags and sensors are
input devices; the value is in the joins between them and the transactional
record, and in what fails to join.

---

## 4. Domain 1 — Patient and medication safety

### 4.1 Identity (settled in the companion docs)

Identity Assurance Levels; **no biometric score, including 1.0, exceeds IAL1**.
IAL2 = face + one independent non-biometric factor. IAL3 = insurance eligibility
+ pharmacist confirmation, and **only IAL3 permits a clinical write**.

At the Rx desk the customer already speaks their national code for the insurance
lookup — one utterance serving the insurance authority, the IAL2 factor and a
text-dependent voice sample simultaneously. No new ritual, so nothing to adopt.

### 4.2 Clinical review — what exists

`services/ai/clinical_decision_support/interaction/engine.py` already implements
explicit drug–drug, drug–context, drug–disease, duplicate therapy, drug–allergy,
inferred PK and inferred PD checks, with a `degraded` flag and stale-med
suppression. This is a genuine asset and it is **deterministic** — which is what
makes it explainable.

Gaps to close: drug–food is not present; lab-driven escalation exists
(`_apply_lab_escalation`) but depends on lab data being current.

### 4.3 Rationing alerts against the 90% override rate

Given that ~90% of DDI alerts are overridden — and 88.2% of *very severe* ones —
the design constraints are:

1. **Tiered interruption.** Only a small, curated hard-stop set interrupts.
   Everything else is passive, on-screen, non-modal.
2. **Patient-specific suppression.** The literature attributes false positives to
   over-broad screening intervals and failure to incorporate patient
   characteristics. Use the lab, age, renal function and duration data already
   in the model before firing.
3. **Measure override rate per rule as a first-class KPI.** A rule overridden
   >90% of the time is retired or re-scoped. This is the only mechanism that
   resists drift.
4. **Never suppress on uncertain identity.** Below IAL3 the system renders
   *"history unavailable — verify manually"*, never *"no interactions found"*.
   "No interactions" is itself a clinical claim.
5. **Every alert states its rule, inputs and thresholds.** Non-explainable alerts
   are defects.

### 4.4 Incident detection and patient recall

Wrong-drug/dose/patient incidents are found by **reconciliation**, not vision:
a dispense with no barcode scan, a scan whose NDC ≠ the prescribed product, a
dispense at IAL<3. Recall runs off `InventoryLot` + `ShelfTransferEvent` +
dispense records — a batch query, not a camera search. Video supplies the clip
for the investigation once the record has identified it.

---

## 5. Domain 2 — Security and loss prevention

Assets already built: `BiometricEvidenceVault` (two-tier, AES-256-GCM under an
HSM/KMS master key, documented `legal_reason`, append-only chain of custody,
legal hold, `export_for_authority()`), `SecurityEvent`, `SurveillanceEvent`,
duress protocol, `IdentityClass` security vocabulary.

| Threat | Detection | Evidence |
|---|---|---|
| Unauthorised entry | Presence with no badge event ±30 s | Clip + vault |
| After-hours | Schedule-armed zone | Clip + vault |
| CD diversion | RFID + weight + register vs dispense | Real-time in the cage |
| Stock manipulation | `InventoryMovement` anomaly + pick reconciliation | Daily report |
| Tampering | Camera offline/defocus/obstruction | Ops alert |
| Attack on personnel | **Duress protocol + waiting-area audio** (raised voice, distress) | Immediate |
| Suspicious access | Restricted-zone dwell, unbadged presence | Review queue |

**Waiting-area microphone**: retained, but scoped to *non-identifying* security
signals. Far-field speaker EER is ~14.66% at 5 m / RT60 1.5 s — unusable for
identification, entirely adequate for "someone is shouting", which is what the
attack-on-personnel requirement actually needs.

**Legally defensible audit trail** = the vault's chain of custody plus the
hash-chained event log. Hash at export, record the exporter, never delete under
legal hold.

---

## 6. Domain 3 — Inventory control

This is overwhelmingly a **software and barcode** problem, not a vision problem.

Existing: `DrugProduct`, `InventoryLot`, `StockLevel`, `PurchaseOrder`,
`ReceivingRecord`, `InventoryMovement`, `PharmacyShelf`, `ShelfPlacement`,
`ReplenishmentSession`, `ShelfTransferEvent` with barcode + AI + depot-checkpoint
dual verification.

**Reconciliation set** (run nightly, reported as discrepancies):

```
Σ receipts − Σ dispenses − Σ returns − Σ wastage  ==  physical count
per (ndc11, lot, location)
```

Deviations classify as: count error · scan miss · expiry write-off ·
**unexplained**. Only the last escalates. Controlled drugs reconcile per
transaction, not nightly.

**RFID**: justified for controlled substances only (73% error detection, per-tag
cost acceptable at CD volumes). Not justified for general stock — barcode at
receiving and dispensing carries that at a fraction of the cost.

**Video's contribution**: the unreconciled pick — a reach into a bay with no
matching `InventoryMovement` within ±5 min. In general aisles this is a **daily
report**, not an alarm; most unmatched picks are legitimate (tidying, cycle
counts). Only in the CD cage is it real-time.

---

## 7. Domain 4 — Operational efficiency

| Lever | Mechanism | Expected effect |
|---|---|---|
| **Morning pick list** | LightGBM q85–q90 demand forecast → `ReplenishmentSession.pick_list` | Removes manual list-building; fill rate ↑ |
| **Pre-fetch patient profile** | Face proposes candidate at entry; pharmacist confirms | Saves lookup time at counter |
| **Clinical heads-up** | Deterministic pre-scan on the *confirmed* patient | Pharmacist arrives informed |
| **Queue analytics** | Top-down counting | Staffing to actual demand curve |
| **Counselling capture** | Counter mic → transcript → `ProfileEnrichmentAction` (pharmacist-approved) | Documentation without typing |
| **Receiving** | OCR from package (`package_verification`) | Staff verifies, does not type |

**On "reduce unnecessary staffing":** the honest position is that this system
reduces *wasted* staff time — searching for stock, retyping package data,
building pick lists, hunting for a patient record — rather than headcount. Any
plan that removes a pharmacist from the verification step trades safety for
cost, and the 90% override statistic says the human is already the weakest link
in the alert chain; removing humans makes that worse, not better.

---

## 8. Domain 5 — Staff performance, done defensibly

**Aggregate and process-focused, never individual productivity scoring.**

Measure: queue wait, service duration, verification turnaround, scan compliance
rate, override rate per rule, counselling completion rate, training gaps
inferred from *rule-level* error clusters.

Do **not** measure: individual keystroke rates, time-at-station league tables,
per-person error rankings surfaced outside a formal review process.

Rationale beyond ethics: individual scoring produces gaming, under-reporting of
near-misses, and destroys the incident-reporting culture that safety depends on.
State this in policy and demonstrate the aggregate-only reporting to staff
before deployment — staff consultation is what determines whether the system
survives contact with the people it watches.

---

## 9. Comparison of solution options

| Option | Strengths | Weaknesses | Cost tier | Verdict |
|---|---|---|---|---|
| **Commercial VMS + analytics** (Milestone, Nx, Genetec) | Mature, supported, certified | Retail-generic analytics; no pharmacy semantics; no clinical integration; licence + Iran supply risk | ££££ | Consider for VMS layer only |
| **Retail loss-prevention suite** (StopLift-class) | Proven shrink detection | POS-checkout-centric; no CD/clinical concept | £££ | No |
| **Pharmacy automation** (robotic dispensing, Omnicell/BD-class) | Very strong error reduction, strong CD control | Very high capital; footprint; import/service risk in Iran | £££££ | Aspirational, later |
| **RFID CD-cabinet systems** | Best-in-class controlled-drug traceability | Per-tag cost; limited to CD | ££ | **Yes, CD only** |
| **Barcode + WMS discipline** | 85% dispensing-error reduction; cheapest | Requires process compliance | £ | **Yes — do first** |
| **Open-source CV stack** (Frigate/go2rtc + custom) | Full control, on-prem, integrates with PharmPilot | Build and maintenance effort | ££ | **Yes** |
| **Cloud AI vision APIs** | Fast to start | PHI egress, latency, Iran access, cost at 24/7 scale | £££ | **No** |
| **Build on existing PharmPilot** | Owns the transactional truth; reconciliation is native | Effort concentrated in-house | ££ | **Yes — the core** |

---

## 10. Recommended hybrid

**Barcode-first, reconciliation-centred, video-for-absence, PharmPilot as the brain.**

1. **PharmPilot is the system of record and the reconciliation engine.** Every
   other component is a sensor feeding it.
2. **Barcode everywhere a product is touched** — receiving, shelf transfer,
   dispensing. Highest measured return of any intervention here.
3. **RFID only on controlled substances.**
4. **Open-source CV on-prem** (Frigate + custom tracker/rule service), assigned
   only to the four jobs video is best at.
5. **Counter microphones** for identity verification and counselling capture;
   **waiting-area mic** for security signals only.
6. **Sensors, not video**, for smoke, water and temperature — with certified
   detectors remaining the code-compliant primary.
7. **Access control as the identity authority for staff**, never uniform
   appearance.
8. **Evidence Vault** for all raw media, admin-accessible under documented reason
   with chain of custody — as originally specified.

Justification: it puts money where the evidence is (barcode 85%), it keeps the
legally heavy modality (video) confined to tasks with no alternative, it reuses
the substantial PharmPilot subsystems already built, and it degrades gracefully —
if every camera fails, dispensing safety is untouched.

---

## 11. Safeguards

**Privacy & consent.** Purpose-separated galleries: clinical (explicit opt-in)
and security (legitimate interest, small). Consent as an append-only versioned
ledger, not booleans. Refusal recorded against `HMAC-SHA256(national_code,
pepper)`, never a face template. No face under 18 embedded. No cameras in
toilets, prayer room, staff rest areas, or the counselling room; privacy masks
applied **pre-encode** on screens and prescription surfaces.

**Retention.** General video 15–30 d · high-risk zones 90 d · evidence under
legal hold until case closure · event metadata 24 mo · aggregate metrics
indefinite · **ReID embeddings ≤48 h** · audit logs 7 y. Purge jobs emit
proof-of-deletion; purge failure is a P1.

**Template protection.** Store **ISO/IEC 24745-conformant protected references**
in the operational tier — irreversibility, unlinkability and **renewability**,
so a compromised template can be revoked and re-derived without recapture. Raw
media stays in the vault under the VMK for investigations.

**Cybersecurity.** Camera VLAN with no internet route; mTLS edge→backend;
write-restricted DB role for vision; secrets in a vault, never in URLs or logs;
NTP discipline monitored as a security control (every reconciliation join is
temporal); tamper detection as a first-class event.

**Access control.** Separation of duty: the sysadmin configures but cannot
watch; the security officer watches but cannot reconfigure. Un-blur and export
require role + typed reason + audit row. A standing "watching the watchers"
report to the owner.

### Against FR error, bias, alert fatigue and false accusation

| Risk | Safeguard |
|---|---|
| **False match → wrong chart** | IAL gating; face never exceeds IAL1; margin test; per-occlusion calibration |
| **Bias** | Per-demographic-cell release gate at a fixed threshold, no averaging; **detector and quality-gate recall gated separately** — that is where differential performance usually enters |
| **Uncalibrated thresholds** | Engine refuses to auto-accept without measured impostor statistics (implemented) |
| **Alert fatigue** | Tiered interruption; per-rule override-rate KPI; auto-demotion of rules exceeding an alert budget |
| **False accusation** | No rule outputs an accusation. Unreconciled picks are a *daily report*. Any HR or legal consequence requires two-person review plus a corroborating non-video record |
| **Function creep** | Purpose registry enforced in configuration; new purpose requires DPIA amendment + owner sign-off |

---

## 12. Human review and investigation

1. **Trigger** — reconciliation discrepancy or rule event enters the triage board
   (existing pattern).
2. **Triage** — dispositions: expected · explained · needs research · escalate.
3. **Investigation** — evidence retrieved from the vault under documented
   `legal_reason`; access logged append-only; legal hold applied if it may
   become a case.
4. **Two-person rule** for anything with employment or legal consequence.
5. **Correction** — every link, merge and disposition is reversible; reversal is
   a new event, never a delete.
6. **Closure** — outcome recorded; the rule that fired is scored for precision,
   feeding the tuning loop.

---

## 13. Phases, cost tiers, KPIs

| Phase | Content | Tier |
|---|---|---|
| **0** | Fix the POS dispense bypass; enforce the DUR gate | £ |
| **1** | **Barcode discipline end-to-end** + reconciliation engine + nightly discrepancy report | £ |
| **2** | Morning replenishment forecast into `ReplenishmentSession` | £ |
| **3** | Cameras: entry counting, queue, after-hours arming, blocked egress, camera health. Log-only | ££ |
| **4** | Sensors: temperature, water, smoke (certified primary) | £ |
| **5** | Access control integration + staff binding; restricted-zone rules | ££ |
| **6** | Counter mics: voice verification + counselling capture | ££ |
| **7** | RFID on controlled substances + cage real-time | ££ |
| **8** | Face identification with calibrated, occlusion-stratified thresholds | £££ |
| **9** | Warehouse pick reconciliation; consolidation tooling | ££ |

Phases 1–2 need **no cameras at all** and carry most of the measurable benefit.

**KPIs**

| Metric | Target |
|---|---|
| Barcode scan compliance at dispense | ≥98% |
| Dispensing error rate | ↓ ≥80% vs baseline |
| Alert override rate, hard-stop tier | ≤40% (and any rule >90% retired) |
| Unexplained shrinkage | ↓ ≥50% |
| CD register discrepancies | 0 unexplained |
| Shelf fill rate / stockout hours | ≥98% / −40% |
| Wrong-chart-load events | **0** |
| False security alerts | ≤2 / camera / day |
| Blocked-egress detection recall | ≥98% |
| Purge-job success | 100%, verified monthly |

---

## 14. Limitations and failure modes

**Proven** — barcode error reduction; deterministic interaction checking;
reconciliation-based discrepancy detection; access-control-based authorisation;
sensor-based environmental monitoring; queue counting.

**Experimental / unproven here** — face identification under chador at the
required FPIR; far-field speaker identification; gait (no published evaluation
under chador *at all*); video smoke detection; automated behaviour anomaly
detection.

**Failure modes to plan for**: power loss (UPS + graceful degradation — the
pharmacy must dispense safely with every camera down); clock drift silently
breaking temporal joins; GPU procurement constraints (plan the Hailo/OpenVINO
fallback from day one); storage exhaustion causing silent retention failure;
staff working around a system they were not consulted about; and the model
degrading after a demand shock.

**The honest boundary**: this system cannot safely identify every patient from
surveillance alone, and does not try to. It proposes; the insurance platform and
the pharmacist decide.

---

**Sources**

- [Override rate of DDI alerts: systematic review and meta-analysis](https://journals.sagepub.com/doi/10.1177/14604582241263242)
- [Overriding DDI alerts in CDS: a scoping review](https://pubmed.ncbi.nlm.nih.gov/35673040/)
- [High-priority DDI CDS overrides: appropriateness and adverse drug events](https://pubmed.ncbi.nlm.nih.gov/32337561/)
- [CDS alert appropriateness: review and proposal for improvement](https://pmc.ncbi.nlm.nih.gov/articles/PMC4052586/)
- [Effect of Bar-Code Technology on the Safety of Medication Administration (NEJM)](https://www.nejm.org/doi/full/10.1056/NEJMsa0907115)
- [Barriers to pharmacy bar code scanning for medication dispensing](https://pmc.ncbi.nlm.nih.gov/articles/PMC2744715/)
- [RFID in medication management](https://www.pharmacytimes.com/view/radio-frequency-identification-is-revolutionizing-medication-management)
- [ISO/IEC 24745:2022 — Biometric information protection](https://www.iso.org/standard/75302.html)
- [NIST FRTE 1:N Identification](https://pages.nist.gov/frvt/html/frvt1N.html)
