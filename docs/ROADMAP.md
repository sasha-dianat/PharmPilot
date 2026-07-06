# PharmPilot — Road to Market-Ready

Status legend: ✅ done · 🟡 partial · ⬜ not started

## Built so far (foundation)
- ✅ Deterministic interaction engine (PK/PD, curated + DDInter bundle), Medical Intelligence console
- ✅ Physician responsibility letter (PHI-safe, editable, audited)
- ✅ Iranian pricing engine (insurer share, مابه‌التفاوت, حق فنی, VAT; Decimal/Rial)
- ✅ Drug catalog + NFI harvester (CLI + GUI, live progress) + Excel/CSV import
- ✅ Daily price-sync → manager-approved proposals
- ✅ Smart insurance-coverage extractor (دارونامه → coverage JSON, fuzzy linkage)
- ✅ Reception affordability flow (per-patient baskets, quote, swap/remove/reduce levers)
- ✅ e-prescription استعلام integration seam (null provider → local fallback)

---

## Phase 1 — Real national data loaded (DATA)  🟡
The engine is done; it needs the full dataset behind it.
- 🟡 Finish NFI crawl (≈10% done) → ingest full catalog (~50–60k products)
- ⬜ Load per-insurer دارونامه (تأمین / سلامت / نیروهای مسلح) via the coverage extractor; clear the review queue
- ⬜ Verify pricing/مابه‌التفاوت on a sample of real prescriptions vs a real pharmacy receipt
- ⬜ Confirm the 30/70 franchise, حق فنی amount, VAT exemptions with the domain expert (tariffs in pricing_ir/config.py)
**Exit:** a pharmacist can quote any real Rx and the numbers match a real receipt.

## Phase 2 — Live insurer integration (INTEGRATION)  ⬜
- ⬜ Real نسخه الکترونیک استعلام adapter (per-pharmacy credentials) behind the EligibilityProvider seam
- ⬜ Real claim submission / adjudication to the insurer (today it's a sandbox)
- ⬜ Prescription intake from the insurer e-prescription system by reference code (not just manual/CV)
- ⬜ NFI web-service feed for the daily price sync (replace file/crawl with the official API once credentialed)
**Exit:** dispensing pulls the authoritative patient share live and submits a real claim.

## Phase 3 — Workflow completeness (PRODUCT)  🟡
- 🟡 Full Rx lifecycle: intake → quote → adjudication → fill → dispense (states exist; wire the reception→fill handoff end to end)
- ⬜ Inventory decrement on dispense + reconciliation with the depot/shelf modules
- ⬜ POS / payment capture for the patient-payable amount
- ⬜ Label printing on dispense (label engine exists — wire to the final step)
- ⬜ Patient-category franchise overrides (کمیته امداد 15%, special-disease 0%)
- ⬜ Returns / partial fills / reversals

## Phase 4 — Hardening & compliance (TRUST)  ⬜
- ⬜ Security review: authz on every endpoint, rate limits, secrets out of code (DB password is currently in .mcp.json/config — move to env/secrets)
- ⬜ PHI audit completeness pass (clinical-safety-reviewer subagent over all egress paths)
- ⬜ Backups + migration rollback drill; single-Alembic-head enforcement in CI
- ⬜ Load/perf test the quote + interaction endpoints at pharmacy volume
- ⬜ Error monitoring / structured logging / alerting in prod

## Phase 5 — Quality & release (SHIP)  🟡
- 🟡 Test coverage: strong on pricing/catalog/interaction; ⬜ add e2e (Playwright) for the reception→dispense happy path
- ⬜ Fix the baseline frontend tsc errors (DashboardShell/useAIProvider/MedReconciliationPage) so the build is clean
- ⬜ CI pipeline running /release-check gates on every PR
- ⬜ Deployment: containerize, env-based config, DB provisioning, proxy config for NFI/insurer access
- ⬜ Pilot with one real pharmacy → feedback loop → GA

---

## Immediate next 3 actions
1. Ingest the harvested backlog + resume the crawl to completion (Phase 1).
2. Obtain + load one insurer دارونامه; validate a real receipt end to end (Phase 1).
3. Move the DB password out of committed config into env/secrets (Phase 4 quick win).
