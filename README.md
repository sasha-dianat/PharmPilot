# PharmPilot AI — Intelligent Pharmacy Operations Platform

PharmPilot AI is a pharmacy operations platform combining an end-to-end dispensing
workflow (intake → DUR → claim → adjudication → fill → dispense → label) with an
**AI Pharmacist Assistant** of nine clinical decision-support modules. It runs in a
**sandbox pilot mode that needs zero live third-party credentials**, so a pilot can
start before Surescripts/PDMP/FDB certifications land.

## Architecture

- **Backend** — FastAPI + SQLAlchemy (async), PostgreSQL, Alembic migrations applied
  on boot. Vector store: Qdrant. Optional drug-interaction graph: Neo4j.
- **Frontend** — React + Vite + TypeScript + Tailwind + TanStack Query (pharmacist
  workstation on :3001, admin portal on :3002).
- **AI** — deterministic-first clinical engines; LLMs draft prose only (never decide
  verdicts or invent facts). Cloud LLM optional; local RAG via Qdrant + Ollama.

## AI Pharmacist Assistant — clinical modules

Every module is **deterministic-first and advisory-only**: hardcoded, cited clinical
facts decide the verdict; an LLM (when present) only drafts prose, and a validator
rejects any output that adds an off-list drug, escalates severity, or issues a
directive. Each endpoint is permission-gated (`clinical:read`), tenant-scoped,
fail-safe, and writes one `ClinicalAuditLog` row. All work with **zero LLM credentials**.

| Module | What it does | Endpoint |
|---|---|---|
| A — Explainable CDS | Deterministic drug-safety rules with full rationale | `POST /api/v1/cds/evaluate` |
| B — Counselling Generator | Cited facts + LLM translate/level (5 languages, 5 levels) | `POST /api/v1/counselling/generate` |
| C — Lab-Based Safety Monitoring | 12 drug→lab rules (warfarin/INR, ACE-ARB/K⁺, metformin/eGFR, digoxin, lithium, amiodarone, statins/CK, NSAIDs, clozapine/ANC, SSRI/Na⁺, loop diuretics, anticoagulants/CBC) | `POST /api/v1/lab-safety/assess` |
| D — Physician Communication | Deterministic SBAR/SOAP/letter templates + LLM translate/tone | `POST /api/v1/physician-message/generate` |
| E — ADR Detective | Deterministic causality + LLM-prose-only | `POST /api/v1/adr/assess` |
| F — Second Brain (RAG) | Retrieve-first clinical Q&A, refuse-on-no-evidence, grounded | `POST /api/v1/second-brain/query` |
| G — Pharmacogenomics | Deterministic CPIC rules, no fabrication | `POST /api/v1/pgx/interpret` |
| H — Polypharmacy / Deprescribing | Deterministic; never recommends abrupt-stop (taper cautions) | `POST /api/v1/polypharmacy/review` |
| I — Medication Reconciliation | Deterministic; 8 discrepancy types, brand→generic matching | `POST /api/v1/med-reconciliation/reconcile` |

**Plus** Offline Drug Intelligence — per-drug RAG monograph (7 sections) reusing the
Second Brain engine, with a council-reference hook: `POST /api/v1/drug-intelligence/monograph`.

## RAG / local-LLM reality

RAG has two stages with different hardware needs:

- **Retrieval** (local embeddings + Qdrant) is fast and production-real; quality
  scales with how much clinical KB you ingest.
- **Generation** (LLM prose synthesis) is optional. With no local model, the RAG
  modules **degrade to cited-extractive** — they return the actual retrieved source
  snippets instead of paraphrased prose; all safety invariants still hold.

On modest/CPU-only hardware a small local model is too slow for the synthesis
timeout and **pointing the stack at it makes responses slower, not better** — leave
`OLLAMA_MODEL` unset for instant cited-extractive RAG. Fluent synthesis needs a
GPU/cloud model; it is not required for pilot.

## Quick start

See [`DAY1_RUNBOOK.md`](DAY1_RUNBOOK.md) for the full operations runbook (start
services, onboard a pharmacy, seed the drug catalog + knowledge base, run the first
prescription, key ports, troubleshooting, and the external credentials needed for
production).

```bash
# Start PostgreSQL, then all services
LC_ALL=C pg_ctl -D ~/.pharmpilot/pgdata -l ~/.pharmpilot/pg.log start
LC_ALL=C bash scripts/dev.sh
open http://localhost:3001
```

## Tests

```bash
python -m pytest tests/ -q                       # full suite: 402 passed, 1 skipped
cd frontend/workstation && npx tsc --noEmit      # type-check: clean
```

## Safety & compliance posture

Advisory-only AI (never autonomous), per-call clinical audit logging, tenant
isolation, controlled-substance EPCS/PDMP flow with an 8-event SHA-256 audit hash
chain, and sandbox mode for credential-free pilots. Never commit credentials — set
them in `.env` (see `.env.example`).
