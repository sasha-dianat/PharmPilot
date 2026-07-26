# Continuation Prompt

You are continuing work on PharmPilot AI, an Iran-first pharmacy operations and medication-intelligence platform.

## Required context

Before substantial work:

1. Read the root `AGENTS.md` completely.
2. Read `docs/ai-context/CODEX_PROJECT_BIBLE.md` completely.
3. Inspect `docs/ROADMAP.md` only for current intent, then verify every relevant claim against code.
4. Inspect current Git status and preserve unrelated or pre-existing changes.
5. Read only the files, models, migrations, tests, and client paths directly relevant to the task. Use targeted exploration rather than rescanning the repository.

## Task

<NEXT_TASK>

## Acceptance criteria

<ACCEPTANCE_CRITERIA>

## Working method

- First verify that the requested feature or defect still exists in current code.
- Identify the affected trust boundaries: authenticated pharmacy, patient/Rx parent ownership, PHI egress, clinical authority, audit trail, pricing provenance, schema, and transaction ownership.
- Formulate a bounded, dependency-aware plan before broad or cross-module changes. State assumptions and stopping points.
- Preserve existing behavior unless the task explicitly changes it or it conflicts with an agreed invariant.
- Implement incrementally using established repository patterns. Keep deterministic domain logic in `services/core/`; keep routers thin.
- Derive tenant scope from authenticated identity. Add two-pharmacy negative tests for tenant-sensitive behavior.
- Never directly update `Prescription.status`; use `RxStateMachine` and preserve hash-chain events.
- Keep clinical AI advisory, pricing arithmetic deterministic in `Decimal` Rial, and external facts proposal/approval-driven.
- Do not silently convert unavailable, sandbox, OCR, crawler, LLM, payment, claim, or persistence results into authoritative success.
- Use reviewed Alembic migrations for schema changes; do not add request-time DDL.

## Verification and handoff

- Run the smallest evidenced lint, typecheck, unit, integration, or UI checks covering the change. Broaden only when prerequisites exist.
- Do not install dependencies, use the network, start services, or run migrations against real data unless the user has authorized it.
- Review the complete diff before finishing. Look specifically for unscoped IDs, direct status writes, internal commits, float money, spoofable actor fields, degraded-mode ambiguity, and unrelated churn.
- Report the outcome first, files changed, checks run and results, checks not run and why, remaining risks, and any human decision still required.
- Update `docs/ai-context/CODEX_PROJECT_BIBLE.md`, `AGENTS.md`, `docs/ROADMAP.md`, or task-specific durable documentation whenever architecture, behavior, commands, terminology, data authority, or invariants change.

Begin by summarizing the verified current state and proposing the bounded plan for `<NEXT_TASK>`.
