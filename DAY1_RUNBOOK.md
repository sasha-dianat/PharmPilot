# PharmPilot AI — Day 1 Operations Runbook

## Quick Start (local development)

```bash
# 1. Start PostgreSQL
LC_ALL=C pg_ctl -D ~/.pharmpilot/pgdata -l ~/.pharmpilot/pg.log start

# 2. Start all services (API + frontend + Redis + Qdrant + Neo4j)
LC_ALL=C bash scripts/dev.sh

# 3. Open workstation
open http://localhost:3001
```

**Default credentials:**
| Role | Username | Password |
|------|----------|----------|
| Admin | `admin` | `Admin1234!` |
| Pharmacist | `pharmacist` | *(set during onboarding)* |
| Manager | `pharmacy_manager` | *(set during onboarding)* |

---

## Pharmacy Onboarding (first-time setup)

```bash
# Interactive wizard
python scripts/onboard_pharmacy.py

# Non-interactive (CI/staging)
python scripts/onboard_pharmacy.py --non-interactive --config onboard.json
```

Outputs pharmacy UUID — save it for subsequent commands.

### Seed drug catalog (required before first Rx)
```bash
python scripts/seed_fda_ndc.py --limit 80000 --pharmacy-id <PHARMACY_UUID>
```
~15 minutes. Seeds 50,000–80,000 FDA NDC records with DEA schedules.

### Seed clinical knowledge base
```bash
# Fast (guidelines + FDA labels, ~10 min)
python scripts/seed_knowledge_base.py --sources guidelines,fda

# Full (adds 800+ PubMed abstracts, ~40 min)
python scripts/seed_knowledge_base.py --sources guidelines,fda,pubmed
```

### Seed drug interactions (requires Neo4j)
```bash
python scripts/seed_drug_interactions.py
```
Seeds 50+ critical drug interaction pairs (opioid+benzo, warfarin+NSAIDs, etc.)

---

## First Prescription Workflow

1. **Log in** as pharmacist at http://localhost:3001
2. **Intake Rx** — click "+ New Rx" in the queue panel
3. **DUR check** auto-runs — 0 alerts for most generics
4. **Claim** — click "Claim for Verification" button
5. **Review** — Specialist Council streams findings via SSE
6. **Adjudicate** — click "Verify & Adjudicate" (submits NCPDP D.0 claim)
7. **Fill** → **Dispense** — advance through pipeline
8. **Label** — click "Preview Label" for printable Rx label

**Controlled substance (CII) flow:**
- PDMP auto-queries on claim
- EPCS TOTP required before dispense
- 8-event SHA-256 hash chain audit trail generated

---

## Verified Done Criteria

| Criterion | Status |
|-----------|--------|
| Drug catalog > 10,000 NDCs | ✅ 11,400 records |
| CII drugs correctly flagged | ✅ 460 oxycodone/hydrocodone/fentanyl |
| 3 consecutive Rx lifecycle passes | ✅ Runs 1–3 all PASS |
| 8-event SHA-256 audit trail | ✅ Verified per Rx |
| DUR alerts for opioid+benzo | ✅ Hard-stop enforcement |
| Label preview renders | ✅ All required fields |
| Patient panel with labs/allergies | ✅ eGFR renal flag |
| Knowledge base ingesting | ✅ Guidelines + FDA labels |
| 121 unit tests passing | ✅ All green |
| Staff onboarding CLI | ✅ Interactive + non-interactive |

---

## Key Ports

| Service | Port | Notes |
|---------|------|-------|
| FastAPI | 8001 | Swagger at /docs |
| React Workstation | 3001 | Main pharmacist UI |
| Admin Portal | 3002 | Chain management |
| PostgreSQL | 5433 | Custom cluster at ~/.pharmpilot/pgdata |
| Redis | 6379 | Queue + caching |
| Qdrant | 6334 | PubMedBERT vector store |
| Neo4j | 7687 | Drug interaction graph |

---

## Remaining External Credentials Needed

| Integration | Credential Needed | Where to Obtain |
|-------------|-------------------|-----------------|
| Surescripts ePrescribing | SURESCRIPTS_SENDER_ID + PASSWORD | Surescripts certification (6-12 weeks) |
| PDMP (NarxCare) | NARXCARE_API_KEY | State PDMP program enrollment |
| CoverMyMeds Prior Auth | COVERMYMEDS_API_KEY | CoverMyMeds partner portal |
| FDB drug database | FDB_API_KEY | First DataBank license (~$5k/yr) |
| Medi-Span alternative | MEDSPAN_API_KEY | Wolters Kluwer license |
| ANTHROPIC_API_KEY | Required for ACB clinical brain | console.anthropic.com |
| AWS (production) | AWS credentials + BAA executed | AWS account + HIPAA BAA |

Set all in `.env` file — never commit credentials to git.

---

## Troubleshooting

**PostgreSQL won't start:**
```bash
LC_ALL=C pg_ctl -D ~/.pharmpilot/pgdata start
```

**API 500 errors:**
```bash
tail -50 /tmp/api.log
```

**Frontend blank page:**
- Open browser devtools → Console tab
- ErrorBoundary should catch crashes and show retry button
- Hard reload: Cmd+Shift+R

**Knowledge base "unavailable":**
```bash
# Check Qdrant vector count
curl http://localhost:6334/collections/pharmpilot_clinical_knowledge
# Re-run seeder if 0
python scripts/seed_knowledge_base.py --sources guidelines,fda
```

**DUR alerts not firing:**
```bash
# Verify drug catalog has drug
psql -h 127.0.0.1 -p 5433 -U pharmpilot -d pharmpilot \
  -c "SELECT ndc11, generic_name, dea_schedule FROM drug_products WHERE LOWER(generic_name) LIKE '%oxycodone%' LIMIT 3;"
```
