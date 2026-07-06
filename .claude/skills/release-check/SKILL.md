---
name: release-check
description: Pre-commit/pre-release gate for PharmPilot — run the full backend test suite, frontend typecheck, migration-head check, pricing conservation smoke, and graphify refresh; report a single PASS/FAIL table.
---

# Release Check

Run every gate below, collect results, and present one PASS/FAIL table at the
end. Do not stop at the first failure — run everything, then summarize.

1. **Backend tests**:
   `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit -q`
   (Known-flaky exclusion: none — the interaction-engine tests are bundle-isolated.)
2. **Frontend typecheck**: `cd frontend/workstation && npx tsc --noEmit`.
   Pre-existing failures in DashboardShell.tsx / useAIProvider.ts /
   MedReconciliationPage.tsx are BASELINE — only report NEW errors beyond those.
3. **Migration chain**: exactly one Alembic head —
   `DATABASE_URL="postgresql+asyncpg://pharmpilot:change_in_production@127.0.0.1:5433/pharmpilot" /Users/sashad85/miniforge3/bin/python -m alembic heads`
   must print a single revision; also `alembic upgrade head` applies cleanly.
4. **Pricing conservation smoke**: quote a basket via
   POST /api/v1/pricing/quote (login as pharmacist Pharmacist2024! or admin
   PharmPilot2024!) and assert insurer + patient == grand_total on the totals.
5. **Backend boot**: `curl -s http://127.0.0.1:8001/health` returns ok (restart
   uvicorn if the running process predates the code being checked).
6. **Graph refresh**: `graphify update .`
7. **Working tree**: `git status --short` — list anything uncommitted.

Finish with the table and an overall verdict: READY TO COMMIT / NOT READY plus
the exact failures to fix.
