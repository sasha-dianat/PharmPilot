# MASTER PROMPT — PharmPilot Intelligent Services (Offline-First)

> Hand this entire document to an implementing engineering agent. It is written as
> an executable specification. Every service must obey **PART 1 (The Offline-First
> Doctrine)** without exception. Build the shared substrate in **PART 2** before any
> individual service in **PART 3**.

---

## PART 0 — ROLE & MISSION

You are a senior ML/AI applications engineer implementing fourteen intelligent
services inside the **PharmPilot** pharmacy platform. The platform already runs:

- **Backend:** FastAPI + SQLAlchemy (async) + PostgreSQL on **port 8001** (never 8000)
- **Frontend:** React 18 + TypeScript + Vite + Tailwind v4 on **port 3001** (never 3000)
- **Already-installed local ML stack:** `scikit-learn`, `numpy`, `pandas`, `torch`,
  `transformers`, `sentence-transformers`, `spaCy`, `joblib`
- **Existing AI modules you will reuse, not rebuild:**
  - `services/ai/provider_registry/` — multi-LLM router with PHI-sensitivity rules and a
    **local Ollama** path (this is your online↔offline LLM switch — use it everywhere)
  - `services/ai/inventory_intelligence/` — `forecaster.py` (Prophet/XGBoost demand) +
    `anomaly_detector.py` (Isolation Forest + CUSUM + diversion screener)
  - `services/ai/pharmacovigilance/` — PRR/ROR + CUSUM signal detection
  - `services/ai/adherence_engine/` — gradient-boosted non-adherence predictor
  - `services/ai/knowledge_engine/` — Qdrant + PubMedBERT RAG, ingestion pipeline
  - `services/ai/clinical_brain/` — clinical LLM consultation + **trainable drug–drug
    interaction module** (reuse for compounding; do not duplicate)
  - `services/graph/` — Neo4j drug-interaction / fraud / supply-chain graph
  - `services/ai/package_verification/` — MobileNetV3 visual embeddings + OCR
- **Existing offline plumbing you will extend:**
  - `~/.pharmpilot/` — local SQLite caches, model store, package index (WAL mode)
  - Frontend: `OfflineIndicator.tsx`, `useOnlineStatus()`, `useOfflineQueue()`, service worker
  - Established degradation pattern: classical ML local → cloud LLM enriches when online

**The 14 services to build:** Predictive Queue Prioritization · Natural Language
Analytics · DUR Override Pattern Intelligence · Controlled-Substance Forgery Detection ·
Personalized Label Simplification · Compounding Compatibility · Counseling Quality ·
Inventory Expiry Waste Prevention · Smart Prescriber Enrichment · End-to-End Rx Workflow
Automation · Patient Lifetime Trajectory · Supply-Chain Early Warning · Automated Clinical
Documentation · Financial Margin Optimization.

---

## PART 1 — THE OFFLINE-FIRST DOCTRINE (non-negotiable law)

Every intelligent service is built as **two brains behind one contract.**

### 1.1 The Two-Tier Brain

| Tier | Name | Always available? | Powered by |
|------|------|-------------------|------------|
| **L** | **LOCAL BRAIN** | ✅ Yes — works with the network cable unplugged | scikit-learn / XGBoost models (joblib at `~/.pharmpilot/models/`), local sentence-transformers & PubMedBERT embeddings on `torch` CPU, FAISS / numpy cosine search, rule engines, statistical methods (CUSUM, Isolation Forest, z-score), **Ollama local LLM** (small models: Llama-3.1-8B / Phi-3 / Qwen2.5) for text generation, SQLite reference caches |
| **C** | **CLOUD BRAIN** | ⚠️ Only when online | Cloud LLMs via `provider_registry` (Claude Opus / GPT-4o), external APIs (RxNorm, OpenFDA, medical-council registries, wholesaler EDI, PBM, shortage feeds), cross-pharmacy aggregated intelligence, nightly retraining/federated updates |

**The Law:** A service must **always return a useful answer from the LOCAL BRAIN.**
The CLOUD BRAIN only *enriches, refines, and widens options* when connectivity exists.
Losing the internet **reduces options and confidence — it never produces an error or a
blank screen.**

### 1.2 The Universal Service Contract

Every intelligent endpoint returns this envelope (extend per service, never remove):

```jsonc
{
  "result":        { /* the actual service payload */ },
  "tier_used":     "local" | "cloud" | "hybrid",
  "confidence":    0.0,            // 0–1; local tier is honestly lower
  "degraded":      true|false,     // true when cloud options were unavailable
  "options_active":["..."],        // which sub-features ran this call
  "options_offline":["..."],       // sub-features skipped because offline
  "model_version": "queue_rank_v3_local",
  "generated_at":  "ISO-8601"
}
```

The frontend **must** surface `tier_used` + `degraded` as a small badge so the
pharmacist always knows whether they are seeing **Local Intelligence** (amber chip)
or **Full Intelligence** (green chip). Reuse `useOnlineStatus()` for the live signal.

### 1.3 The Connectivity Resolver (build once, in PART 2)

A single `IntelligenceTier` resolver decides per-request which brain to use:

```
resolve_tier(service, force=None):
  if force: return force
  if not network_up(): return LOCAL
  if cloud_dependency_healthy(service): return CLOUD/HYBRID
  return LOCAL          # fail closed to local, never to error
```

- `network_up()` = cheap reachability probe (cached 10 s) — does NOT block the request.
- Cloud calls run with a **hard timeout (≤ 2.5 s)**; on timeout, **silently fall back to
  LOCAL** and set `degraded:true`. The user never waits on a dead socket.
- Write-side actions that need the cloud (e.g. registry verification, EDI orders) are
  **queued** via the existing `useOfflineQueue()` / a new backend `OutboxTable` and
  reconciled when connectivity returns. Reads always degrade live.

### 1.4 Training & Model Lifecycle (offline-safe)

- **All LOCAL models train on the pharmacy's own PostgreSQL data**, on-box, via a nightly
  Celery beat job. No model download is ever *required* to function.
- Models are serialized with `joblib` to `~/.pharmpilot/models/{service}/{version}.joblib`
  with a `manifest.json` (feature list, trained_at, row_count, metrics).
- When online, an optional **federated refinement** step pulls improved hyper-params /
  cross-pharmacy priors — but a freshly-installed, never-online box still self-trains and
  works from day one (cold-start uses rule-based priors until enough rows exist).
- Every model exposes `predict_proba` style confidence so the envelope's `confidence` is real.

### 1.5 "Trainable References" pattern (used by #5, #8, #10, #13, #16)

Several services say "trainable references." Implement this uniformly:
a **Reference Corpus** the pharmacist can grow. Each reference doc (textbook dosing table,
council registry export, compatibility chart, SIG phrase bank) is ingested through the
**existing `knowledge_engine`** → chunked → embedded with **local PubMedBERT** → stored in
the local vector index. RAG retrieval therefore works **fully offline**; when online, the
cloud LLM synthesizes retrieved chunks into richer prose. Pharmacist-added references
become training/few-shot signal for that service.

---

## PART 2 — SHARED SUBSTRATE (build first, before any service)

Create `services/ai/intelligence_core/` with:

1. **`tier_resolver.py`** — `IntelligenceTier` (PART 1.3), `network_up()`, timeout-guarded
   `cloud_call(fn, fallback)` helper, `@dual_brain` decorator that wraps any service
   function so it auto-produces the §1.2 envelope.
2. **`local_llm.py`** — thin wrapper over the provider_registry's Ollama path with a
   `generate(prompt, system, max_tokens)` + `embed(text)` API. Single switch:
   `provider_registry` picks Ollama offline, Claude/GPT online — services never branch on this.
3. **`reference_corpus.py`** — the §1.5 trainable-reference ingest/retrieve helper over the
   existing `knowledge_engine`.
4. **`model_store.py`** — joblib save/load + manifest + version pinning at `~/.pharmpilot/models/`.
5. **`feature_store.py`** — reusable SQL→DataFrame feature builders (dispense history,
   patient features, claim outcomes, lot depletion) so services share one feature layer.
6. **`outbox.py`** — `OutboxTable` (Postgres) for deferred cloud writes + a recon worker.
7. **Frontend `lib/useIntelligenceTier.ts`** — exposes `{tier, degraded}` + a
   `<TierBadge tier degraded />` component (green=Full, amber=Local) reused by every panel.

Register a new router prefix **`/api/v1/intelligence`** in `main.py` (one router file per
service, mounted under sub-prefixes below).

---

## PART 3 — THE FOURTEEN SERVICES

> Each spec gives: **Purpose · LOCAL brain (offline) · CLOUD brain (online extras) ·
> Data · Endpoint · Frontend surface.** Build local first; bolt on cloud second.

---

### ② Predictive Queue Prioritization
- **Purpose:** Score every Rx in the queue for work-order priority + forecast incoming load.
- **LOCAL:** Gradient-boosted ranker (scikit-learn) on local fills. Features: patient
  physically present (biometric check-in flag), drug acuity class (acute vs chronic, from
  local drug cache), refills-remaining, time-since-order, current queue depth, tech capacity.
  Arrival forecast = seasonal-naïve / Holt-Winters on local timestamp history. **Fully works
  offline.**
- **CLOUD:** Cross-pharmacy arrival priors; weather/holiday signals; LLM reorders ties using
  free-text Rx notes. When offline these simply drop from `options_active`.
- **Data:** `prescriptions`, `rx_state_events`, biometric check-in events.
- **Endpoint:** `GET /api/v1/intelligence/queue/ranking`, `GET /queue/forecast`.
- **Frontend:** Sort weight + colored priority pill in **`RxQueue.tsx`**; mini load-forecast
  sparkline at top of queue. TierBadge on the panel header.

---

### ③ Natural Language Analytics — "Ask Your Data"
- **Purpose:** Plain-language questions → SQL → narrative answer over analytics tables.
- **LOCAL:** **Ollama** text-to-SQL constrained to a curated, read-only schema view
  (`claim_transactions`, `prescriptions`, `payment_events`, `inventory_lots`). Guardrails:
  whitelist tables/columns, `LIMIT` injection, SELECT-only parser, parameterized execution.
  Local LLM also writes the short narrative. **Works offline** for the common question set.
- **CLOUD:** Claude/GPT for complex multi-join reasoning, ambiguous phrasing, multi-step
  follow-ups, and richer narrative. Offline → answers still come from local model with
  `degraded:true` and a hint "complex questions need connection."
- **Data:** Read-only analytics views only. **Never** expose PHI columns to the LLM prompt —
  pass schema, not rows; execute SQL server-side; return aggregates.
- **Endpoint:** `POST /api/v1/intelligence/analytics/ask`.
- **Frontend:** Chat box embedded in **`FinancialOperations.tsx`** (and CommandCenter);
  shows generated SQL (collapsible), the answer, and a chart when the result is chartable.

---

### ⑤ DUR Override Pattern Intelligence
- **Purpose:** (a) suggest the most-likely override reason code before the dropdown opens;
  (b) flag pharmacists whose override rates are statistical outliers (QA, not punishment).
- **LOCAL:** Association-rule mining (Apriori / FP-growth, local) over
  `dur_override_events` keyed on `alert_type × drug_class → reason_code` for suggestions;
  per-pharmacist z-score vs peer mean for the consistency monitor. **Fully offline.**
- **CLOUD:** LLM drafts a defensible clinical-notes sentence for the chosen reason; national
  benchmark priors for what "normal" override rates look like. Offline → suggestion still
  ranked locally, notes left to the pharmacist.
- **Data:** `dur_override_events` (already built), `prescriptions`, drug class from local cache.
- **Endpoint:** `GET /api/v1/intelligence/dur/suggest`, `GET /dur/consistency-report`.
- **Frontend:** Pre-selected reason in **`DUROverrideModal.tsx`** with "AI suggested" chip;
  monthly consistency report card in a QA tab.

---

### ⑥ Controlled-Substance Forgery & Integrity Detection  *(controlled substances ONLY)*
- **Purpose:** Score a scanned Rx for forgery risk — **gated to controlled-substance Rxs only.**
- **LOCAL:** Two local signals fused:
  1. **Visual** — a small CNN (torch, runs on CPU) fine-tuned on real-vs-suspect Rx images,
     plus classical image-forensics features (font edge entropy, JPEG ghost / ELA, margin &
     logo geometry, ink-uniformity) — all computable offline with OpenCV.
  2. **Behavioral** — per-prescriber prescribing-distribution anomaly: this drug, this dose,
     this frequency vs that prescriber's local history (z-score / Isolation Forest).
  Combined into a 0–1 forgery score. **Fully offline.** Only triggers when the dispensed
  item's schedule ∈ {CII–CV} (check local drug cache).
- **CLOUD:** Live prescriber-license/council validity check; cross-pharmacy "same Rx image
  seen elsewhere" duplicate-fraud detection; PDMP corroboration. Offline → these become
  `options_offline` and the local score still stands with a "verify license when online" note.
- **Data:** `rx_documents` images, `prescriber` history, local schedule lookup.
- **Endpoint:** `POST /api/v1/intelligence/integrity/score` (no-op + skip flag for non-controlled).
- **Frontend:** Red/amber/green integrity banner inside **`RxScanner.tsx`** & VerificationCenter,
  shown **only** for controlled Rxs; one-click "send for pharmacist review."

---

### ⑧ Personalized Label Language Simplification  *(+ reference dosing for pharmacist)*
- **Purpose:** Two outputs from one engine:
  1. **Patient-facing default instructions** — the SIG rewritten in plain, patient-appropriate
     language in the patient's `preferred_language`. This becomes the **default admin
     instruction** printed on the label.
  2. **Pharmacist-facing reference panel** — the **textbook / reference dosing** for that drug,
     retrieved from the **trainable Reference Corpus** (§1.5), shown side-by-side so the
     pharmacist can verify the SIG against authoritative dosing.
- **LOCAL:** Deterministic SIG-expansion grammar (abbreviation → words) + **Ollama** rewrite
  into the target language (template-guided, low-temperature). Reference dosing via **local
  PubMedBERT RAG** over ingested dosing tables. **Fully offline** for ingested languages/drugs.
- **CLOUD:** Cloud LLM for fluent, idiomatic rewriting in less-common languages, pictogram
  suggestions, and literacy-graded phrasing. Offline → local rewrite + retrieved dosing chunks.
- **Data:** SIG from `prescriptions`, `patient.preferred_language`, Reference Corpus.
- **Endpoint:** `POST /api/v1/intelligence/label/simplify` → `{patient_instructions, reference_dosing[]}`.
- **Frontend:** In **`LabelPreview.tsx`**: patient instructions shown as the editable default
  on the label; a collapsible **"Reference dosing (pharmacist)"** card showing retrieved
  textbook ranges with source citations. Pharmacist can "add a reference" to grow the corpus.

---

### ⑩ Compounding Ingredient Compatibility Intelligence  *(reuses existing DDI module)*
- **Purpose:** Flag chemical/physical incompatibilities, BUD conflicts, and dose-range errors
  across a compound's ingredient list — **layered on top of**, not duplicating, the existing
  clinical-brain trainable drug–drug interaction engine.
- **LOCAL:** (1) Query existing **Neo4j/clinical DDI graph** for known incompatible pairs.
  (2) Local rules: acid/base precipitation, solubility class clashes, shortest-ingredient BUD
  wins, per-kg dose-range check vs patient weight. (3) For novel pairs, a local
  physicochemical-similarity model (RDKit-style fingerprints / cached property table) estimates
  compatibility. **Fully offline** over the ingested compatibility references (§1.5).
- **CLOUD:** Cloud LLM literature synthesis on rare combinations; latest USP compatibility chart
  refresh. Offline → local graph + rules + cached charts only, `degraded:true`.
- **Data:** Compound formula entry (`core/compounding/`), DDI graph, Reference Corpus.
- **Endpoint:** `POST /api/v1/intelligence/compound/compatibility`.
- **Frontend:** Inline incompatibility flags + BUD warning in the compounding screen; severity
  chips with source (graph vs rule vs reference).

---

### ⑪ Counseling Quality Assessment
- **Purpose:** Score each counseling transcript against a coverage rubric + read patient
  comprehension/sentiment, feeding both QA and the adherence engine.
- **LOCAL:** Transcript already produced by the Whisper pipeline with speaker labels.
  Rubric coverage via **local zero-shot/NLI classifier** (a small `transformers` model) over
  required elements (indication, dose, timing, side-effects, storage, missed-dose, when-to-call);
  sentiment via a local fine-tuned classifier; comprehension heuristics (did patient paraphrase
  back?). **Fully offline.**
- **CLOUD:** Cloud LLM for nuanced rubric scoring, rationale, and coaching feedback prose.
  Offline → checkmarks + scores from local models, no prose rationale.
- **Data:** `audio_transcripts` (diarized), patient context.
- **Endpoint:** `POST /api/v1/intelligence/counseling/assess`.
- **Frontend:** Post-consult scorecard in **`AudioIntelligence.tsx`**; ✓/✗ rubric grid +
  sentiment gauge; feeds a follow-up flag into PatientAdherence.

---

### ⑫ Inventory Expiry Waste Prevention  *(inside the existing stock-ML panel)*
- **Purpose:** Predict which lots will expire before consumption; recommend return/transfer/
  dispense-sequence. **Implement in the SAME panel as the existing stock-ML component.**
- **LOCAL:** Per-lot survival/depletion model: combine existing `forecaster.py` demand curve
  with each `InventoryLot` quantity + expiry → days-to-depletion vs days-to-expiry with
  confidence interval. Rank "at-risk" lots; suggest FEFO dispense order + return quantity.
  **Fully offline** (pure local data + already-trained forecaster).
- **CLOUD:** Wholesaler return-window/credit eligibility lookup; sister-branch transfer
  matching (who needs this NDC). Offline → return *suggestion* still computed; actual return
  order is **queued via outbox** until online.
- **Data:** `inventory_lots`, demand forecast, wholesaler return rules (online).
- **Endpoint:** `GET /api/v1/intelligence/inventory/expiry-risk`.
- **Frontend:** New "Expiry Risk" section **added to `InventoryIntelligence.tsx`** (same panel
  as Stock Brain): at-risk lot table (days-to-expiry vs days-to-depletion bars), recommended
  action chips, "queue return" button that respects offline outbox.

---

### ⑬ Smart Prescriber Registry Enrichment
- **Purpose:** Validate + enrich prescriber records and learn each prescriber's normal
  prescribing pattern to flag deviations.
- **LOCAL:** Build per-prescriber prescribing-distribution profiles (drug class, dose ranges,
  volume) from local fills; flag new Rxs that deviate (z-score / Isolation Forest). Local
  council-number format validation (checksum/structure rules). **Fully offline.**
- **CLOUD:** Live validation against external medical-council / NPI / GMC registries; suspension
  status; specialty-vs-prescription consistency check. These are **deferred to the outbox** when
  offline and reconciled later; until then the record is marked "pending external verification."
- **Data:** `prescribers`, `prescriber_medical_registrations`, fills history, external registries.
- **Endpoint:** `POST /api/v1/intelligence/prescriber/enrich`, `GET /prescriber/{id}/profile`.
- **Frontend:** Prescriber profile card with "verified ✓ / pending ⧖ / anomaly ⚠"; deviation
  flag surfaced in VerificationCenter.

---

### ⑮ End-to-End Rx Workflow Intelligent Automation  *(the Rx Copilot)*
- **Purpose:** Over the existing Rx state machine, compute a per-step **automation-confidence**
  and auto-advance steps the pharmacist has authorized, with full audit + reversibility.
- **LOCAL:** Ensemble that fuses the other local services' confidences:
  - DUR step → local clinical-brain + history ("seen safe N times") confidence
  - Adjudication step → **local claim-rejection predictor** (build as local GBM on
    `claim_transactions`) — only the clean-claim probability
  - Verification step → existing package-verification visual match confidence
  Each step auto-clears only when local confidence ≥ a **pharmacist-set threshold** (settings
  screen, per alert type). **Fully offline** — automation actually *increases* value offline
  because it removes manual clicks when connectivity is flaky.
- **CLOUD:** Cloud LLM second-opinion on borderline DUR cases; live adjudication submission.
  Offline → claim submission **queued via outbox**; everything else still auto-advances locally.
- **Data:** `rx_state_events`, all sibling-service outputs, claim history.
- **Endpoint:** `GET /api/v1/intelligence/rx/{id}/copilot`, `POST /rx/{id}/auto-advance`.
- **Frontend:** "Copilot" rail in **`VerificationCenter.tsx`**: each step shows confidence,
  what the AI did, and an undo; a settings panel for per-step auto-clear thresholds. Every
  auto-action writes an audit row (pharmacist remains the accountable signer — AI proposes,
  pharmacist can always override).

---

### ⑯ Patient Lifetime Health Trajectory Modeling  *(+ visualization panel)*
- **Purpose:** Turn a patient's full history into a longitudinal story: med changes vs lab
  trends, disease-progression signals, predicted future state, and care-gap alerts — with a
  dedicated visualization panel.
- **LOCAL:** Local time-series/sequence model (start with gradient-boosted trend + change-point
  detection; optionally a small local LSTM on `torch`) over `prescriptions`, `lab_results`,
  `clinical_notes`. Correlate med starts with lab deltas; rule-based care-gap detection
  (e.g., diabetic w/o recent monitoring). **Fully offline.**
- **CLOUD:** Cloud LLM narrative ("here's this patient's story"), population-level trajectory
  priors for richer future-state prediction. Offline → charts + local predictions + terse
  rule-based narrative.
- **Data:** `patients`, `prescriptions`, `lab_results`, `clinical_notes`, allergies.
- **Endpoint:** `GET /api/v1/intelligence/patient/{id}/trajectory`.
- **Frontend:** **New panel `PatientTrajectory.tsx`** (linked from `PatientPanel.tsx` and
  `ClinicalIntelligence.tsx`): a vertical timeline of med events overlaid on lab-trend lines
  (Recharts ComposedChart), risk-badge strip (polypharmacy / adherence / deterioration /
  cost — reuse adherence_engine), care-gap alert cards, and a "predicted trajectory" forecast
  band. TierBadge indicates whether the narrative is full or local.

---

### ⑰ Supply-Chain Disruption Early Warning
- **Purpose:** Predict NDC-level supply disruptions early and recommend buffer-stock /
  alternative / prescriber-switch actions.
- **LOCAL:** CUSUM (reuse pharmacovigilance/anomaly infra) on **local order-fill-rate** and
  local price history per NDC; combine with demand forecast to compute stockout risk and
  suggested buffer. **Fully offline** from the pharmacy's own ordering data.
- **CLOUD:** National shortage feeds, wholesaler fill-rate signals across the network, OpenFDA
  recall surge, knowledge_engine web signals → much earlier, broader warnings. Offline →
  local-only signal with `degraded:true`.
- **Data:** purchase orders / receiving history, price history, demand forecast, (online) feeds.
- **Endpoint:** `GET /api/v1/intelligence/supply/risk`.
- **Frontend:** "Supply Risk" cards in **`InventoryIntelligence.tsx`** / CommandCenter:
  at-risk NDCs, confidence, recommended buffer qty, alternative suggestions; "order buffer"
  respects offline outbox.

---

### ⑱ Automated Clinical Documentation Generation
- **Purpose:** Auto-draft structured SOAP / MTM (CMR/TMR/MAP) notes from the consultation
  transcript + patient context; pharmacist reviews & signs in seconds.
- **LOCAL:** **Ollama** structured extraction over the diarized transcript + local patient
  context → SOAP fields; deterministic MTM templates pre-filled from structured data.
  **Fully offline.**
- **CLOUD:** Cloud LLM for higher-fidelity, guideline-aware narrative and richer assessment/
  plan. Offline → local draft (clearly tagged "local draft — review carefully").
- **Data:** `audio_transcripts`, patient meds/labs, MTM session context.
- **Endpoint:** `POST /api/v1/intelligence/docs/draft-soap`, `POST /docs/draft-mtm`.
- **Frontend:** "Generate note" in **`DictateNote.tsx`** / clinical-services screen → editable
  SOAP draft with confirm-to-save; pharmacist is always the signer.

---

### ⑲ Financial Intelligence & Margin Optimization
- **Purpose:** Connect "what we dispensed" to "what we could have earned"; surface
  below-cost fills, generic-substitution upside, and DIR-fee exposure with concrete actions.
- **LOCAL:** Margin computed locally from cached AWP/WAC/AAC + reimbursement on
  `claim_transactions`; flag below-MAC fills; local optimizer (linear program / greedy) over
  cached generic-equivalent table to suggest substitutions maximizing margin while minimizing
  patient cost; DIR exposure projection from local fill patterns. **Fully offline** on cached pricing.
- **CLOUD:** Live price refresh, live PBM MAC lists, current DIR schedules, cross-pharmacy
  benchmark margins. Offline → computed on last-cached pricing with a "prices as of <date>" stamp.
- **Data:** `claim_transactions`, `payment_events`, drug pricing cache, alternatives table.
- **Endpoint:** `GET /api/v1/intelligence/finance/margin-insights`.
- **Frontend:** "Margin Optimizer" section in **`FinancialOperations.tsx`**: below-cost alert
  list, substitution-opportunity table (current vs optimal margin + patient-cost delta), DIR
  exposure gauge with the specific patient/switch actions that reduce it.

---

## PART 4 — FRONTEND CONVENTIONS (apply to all)

1. Every intelligent panel header carries `<TierBadge tier degraded />`
   (green **Full Intelligence** / amber **Local Intelligence**).
2. When `degraded:true`, show a one-line note listing `options_offline` so the pharmacist
   knows exactly which richer options will return on reconnect (e.g. "License verification
   and national benchmarks resume when online").
3. All TanStack Query hooks use the `const data = raw ?? DEMO_DATA` fallback so the preview
   browser (which can't reach `localhost:8001`) still renders.
4. Reuse `useOfflineQueue()` for any action that writes to the cloud; show queued-count.
5. Abbreviations keep working with the global `pp-abbr` tooltip scanner.

---

## PART 5 — BUILD ORDER & ACCEPTANCE

**Order:** PART 2 substrate → then services in this dependency order:
⑲/⑫/⑰ (pure-local, reuse forecaster) → ⑤/⑬ (local stats on existing tables) →
③/⑱/⑧ (local-LLM via Ollama) → ⑪/⑥/⑩ (local model + RAG) → ② (ranker) →
⑯ (trajectory + panel) → ⑮ (Copilot — depends on the others' confidences).

**Acceptance test for EVERY service (the offline gate):**
1. Run the call **with the network cable unplugged** (simulate: force `network_up()=False`).
   → It returns a valid §1.2 envelope, `tier_used:"local"`, `degraded:true`, useful `result`.
2. Run the same call **online.**
   → Same shape, `tier_used:"cloud"|"hybrid"`, higher `confidence`, more `options_active`,
   identical UI components (only richer content).
3. **No code path raises on connectivity loss.** Cloud timeouts fall back within 2.5 s.
4. Local model trains from an empty start using rule-based priors (cold-start works).
5. PHI never leaves the box to a non-BAA provider; offline LLM = Ollama local; logs masked.

**Non-negotiables:** ports 8001/3001 · AI proposes, pharmacist confirms (no auto-dispense,
no auto-substitute without sign-off) · controlled-substance forgery (#6) gated to CII–CV only ·
#8 prints simplified instructions as default **and** shows reference dosing to pharmacist ·
#10 reuses the existing DDI module · #12 lives in the existing stock-ML panel · #16 ships a
dedicated visualization panel.
```
```
