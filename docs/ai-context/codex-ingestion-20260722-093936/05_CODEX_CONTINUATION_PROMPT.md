# Continuation Prompt

You are Codex working in the PharmPilot AI repository.

Start by reading `AGENTS.md` and `docs/ai-context/CODEX_PROJECT_BIBLE.md`. Then inspect current Git status. Do not assume README or runbook claims are current unless verified against code or checks.

Task: `<NEXT_TASK>`

Acceptance criteria: `<ACCEPTANCE_CRITERIA>`

Required workflow:

1. Verify the requested task against the current code, not just docs.
2. Identify the directly relevant files and avoid broad repository rereads.
3. Formulate a bounded plan before broad or cross-module changes.
4. Preserve existing behavior unless the task explicitly changes it.
5. Implement incrementally with narrow diffs.
6. Maintain PharmPilot invariants: tenant scoping by `pharmacy_id`, advisory-only clinical AI, deterministic pricing, audited Rx state transitions through `RxStateMachine`, and no credential leakage.
7. Run targeted evidenced checks from manifests/CI when the environment permits. If a check cannot run, explain the blocker.
8. Review your diff before final response.
9. Update durable context (`docs/ai-context/CODEX_PROJECT_BIBLE.md`, `AGENTS.md`, or relevant docs) when architecture, behavior, commands, or canonical terminology changes.

Context to keep in mind:

- Canonical description: Iran-first pharmacy operating system with deterministic dispensing, pricing, coverage, catalog, and advisory clinical intelligence.
- `docs/ROADMAP.md` is more candid than `README.md`/`DAY1_RUNBOOK.md` about current maturity.
- Highest-risk known areas are tenant scoping, unauthenticated WebSockets/media/biometric/security routes, mobile staff-auth mismatch/password storage, stale docs, and non-authoritative Docker/CI claims.
