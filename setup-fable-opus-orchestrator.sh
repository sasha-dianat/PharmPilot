#!/usr/bin/env bash
set -Eeuo pipefail

# Fable 5 ↔ Opus 4.8 Claude Code Orchestrator
#
# Installs a project-scoped, Graphify-aware collaboration layer:
#   - Fable 5 as architect / project manager / final quality gate
#   - Opus 4.8 as persistent bounded implementation worker
#   - project-scoped Opus memory
#   - shared project-state + handoff documents
#   - adaptive /delegate-opus skill
#   - SessionStart, SubagentStart, and SubagentStop hooks
#   - path-scoped source-change rules
#   - optional Graphify refresh
#   - optional official Claude Code LSP plugin installation
#
# Existing .claude/settings.json content (including Graphify hooks/plugins) is
# MERGED, never replaced. Existing CLAUDE.md content is preserved and updated
# only inside a marked orchestration block.
#
# Recommended:
#   cd /path/to/project
#   bash setup-fable-opus-orchestrator.sh --launch
#
# Optional:
#   --refresh-graph   Run `graphify .` after setup if the Graphify CLI is on PATH.
#   --install-lsp    Detect common project languages and install matching official
#                    Claude Code LSP plugins. Missing language-server binaries are
#                    installed automatically only for TypeScript/JavaScript and
#                    Python when npm is available; other missing binaries are reported.
#   --preserve-model Do not set project model to `fable`.
#   --no-launch      Configure only (default).
#   --launch         Launch a new Claude Code session with `--model fable`.
#   --root PATH      Configure PATH instead of the current Git/project root.
#   --force          Overwrite orchestrator-owned generated files after backing them up.
#   --dry-run        Print the project root and planned actions; make no changes.
#
# Safe to run repeatedly: generated files and hook entries are idempotently updated.

SCRIPT_VERSION="1.0.0"
AGENT_NAME="opus-implementation-worker"
MIN_CLAUDE_VERSION="2.1.199"

ROOT=""
LAUNCH=0
REFRESH_GRAPH=0
INSTALL_LSP=0
PRESERVE_MODEL=0
FORCE=0
DRY_RUN=0

usage() {
  cat <<'USAGE'
Usage:
  setup-fable-opus-orchestrator.sh [options]

Options:
  --root PATH       Project root (default: Git root, otherwise current directory)
  --launch          Launch Claude Code with Fable 5 after configuration
  --no-launch       Configure only (default)
  --refresh-graph   Run `graphify .` if the Graphify CLI is available
  --install-lsp     Detect and install appropriate official Claude Code LSP plugins
  --preserve-model  Leave an existing project model setting unchanged
  --force           Overwrite orchestrator-owned generated files (with backups)
  --dry-run         Show planned actions only
  -h, --help        Show this help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root)
      [[ $# -ge 2 ]] || { echo "ERROR: --root requires a path" >&2; exit 2; }
      ROOT="$2"; shift 2 ;;
    --launch)
      LAUNCH=1; shift ;;
    --no-launch)
      LAUNCH=0; shift ;;
    --refresh-graph)
      REFRESH_GRAPH=1; shift ;;
    --install-lsp)
      INSTALL_LSP=1; shift ;;
    --preserve-model)
      PRESERVE_MODEL=1; shift ;;
    --force)
      FORCE=1; shift ;;
    --dry-run)
      DRY_RUN=1; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "ERROR: Unknown option: $1" >&2
      usage >&2
      exit 2 ;;
  esac
done

command_exists() { command -v "$1" >/dev/null 2>&1; }

if [[ -z "$ROOT" ]]; then
  if command_exists git && git rev-parse --show-toplevel >/dev/null 2>&1; then
    ROOT="$(git rev-parse --show-toplevel)"
  else
    ROOT="$PWD"
  fi
fi

ROOT="$(cd "$ROOT" 2>/dev/null && pwd -P)" || {
  echo "ERROR: Cannot access project root: $ROOT" >&2
  exit 1
}

if [[ "$ROOT" == "/" || "$ROOT" == "$HOME" ]]; then
  echo "ERROR: Refusing to configure unsafe root: $ROOT" >&2
  echo "Run this from the actual project directory or pass --root /path/to/project." >&2
  exit 1
fi

echo "Fable ↔ Opus Orchestrator v${SCRIPT_VERSION}"
echo "Project root: $ROOT"

if [[ "$DRY_RUN" -eq 1 ]]; then
  cat <<EOF

DRY RUN — planned actions:
  • Preserve existing Graphify installation and existing Claude Code settings/hooks.
  • Create/update .claude/agents/${AGENT_NAME}.md
  • Create/update .claude/skills/delegate-opus/SKILL.md
  • Create/update project context files under docs/ai-context/
  • Create/update project-scoped Opus memory under .claude/agent-memory/${AGENT_NAME}/
  • Create/update orchestration rules under .claude/rules/
  • Create/update hook scripts under .claude/hooks/
  • Merge SessionStart/SubagentStart/SubagentStop hooks into .claude/settings.json
  • $( [[ "$PRESERVE_MODEL" -eq 1 ]] && echo "Preserve existing project model setting" || echo "Set project model to fable" )
  • $( [[ "$REFRESH_GRAPH" -eq 1 ]] && echo "Refresh Graphify graph if CLI is available" || echo "Do not refresh Graphify automatically" )
  • $( [[ "$INSTALL_LSP" -eq 1 ]] && echo "Detect/install official LSP plugins" || echo "Do not alter LSP plugins" )
  • $( [[ "$LAUNCH" -eq 1 ]] && echo "Launch claude --model fable" || echo "Do not launch Claude Code" )
EOF
  exit 0
fi

command_exists python3 || {
  echo "ERROR: python3 is required for safe JSON merging and hook scripts." >&2
  exit 1
}

if command_exists claude; then
  CLAUDE_VERSION="$(claude --version 2>/dev/null | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)"
  if [[ -n "$CLAUDE_VERSION" ]]; then
    if ! python3 - "$CLAUDE_VERSION" "$MIN_CLAUDE_VERSION" <<'PY'
import sys
def parts(v):
    return tuple(int(x) for x in v.split(".")[:3])
sys.exit(0 if parts(sys.argv[1]) >= parts(sys.argv[2]) else 1)
PY
    then
      echo "WARNING: Claude Code $CLAUDE_VERSION detected; this setup is designed for >= $MIN_CLAUDE_VERSION." >&2
      echo "         Update Claude Code before relying on all resume/hook behavior." >&2
    else
      echo "Claude Code: $CLAUDE_VERSION"
    fi
  fi
else
  echo "WARNING: 'claude' is not on PATH. Files will be configured, but launch/LSP installation will be skipped." >&2
  LAUNCH=0
  INSTALL_LSP=0
fi

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$ROOT/.claude/backups/fable-opus-$TIMESTAMP"

mkdir -p \
  "$ROOT/.claude/agents" \
  "$ROOT/.claude/skills/delegate-opus" \
  "$ROOT/.claude/hooks" \
  "$ROOT/.claude/rules" \
  "$ROOT/.claude/agent-memory/$AGENT_NAME" \
  "$ROOT/docs/ai-context/decisions" \
  "$BACKUP_DIR"

backup_if_exists() {
  local src="$1"
  if [[ -e "$src" ]]; then
    local rel="${src#"$ROOT"/}"
    local dst="$BACKUP_DIR/$rel"
    mkdir -p "$(dirname "$dst")"
    cp -p "$src" "$dst"
  fi
}

write_owned_file() {
  local path="$1"
  local content_file="$2"
  if [[ -e "$path" ]]; then
    if cmp -s "$path" "$content_file"; then
      rm -f "$content_file"
      return 0
    fi
    backup_if_exists "$path"
    if [[ "$FORCE" -ne 1 ]]; then
      # Orchestrator-owned files are updated by default. --force exists mainly as an
      # explicit acknowledgement when re-running after manual edits; preserve a backup.
      :
    fi
  fi
  mkdir -p "$(dirname "$path")"
  mv "$content_file" "$path"
}

tmpfile() { mktemp "${TMPDIR:-/tmp}/fable-opus.XXXXXX"; }

# ---------------------------------------------------------------------------
# 1) Persistent Opus 4.8 implementation worker
# ---------------------------------------------------------------------------
TMP="$(tmpfile)"
cat >"$TMP" <<'EOF'
---
name: opus-implementation-worker
description: >-
  Primary implementation worker for bounded coding tasks delegated by Fable 5.
  Use for implementation, tests, routine debugging, local refactors, and precise
  corrections after Fable review. Do not use as the owner of cross-system
  architecture, ambiguous product decisions, or irreversible design choices.
model: claude-opus-4-8
effort: high
memory: project
permissionMode: default
---

# Role

You are the persistent **Opus 4.8 implementation worker** in a Fable 5 ↔ Opus
collaboration. Fable 5 owns architecture, difficult reasoning, decomposition,
priority, acceptance criteria, and final approval. You own bounded execution.

## Start-of-task protocol

1. Read `docs/ai-context/CURRENT_HANDOFF.md` first.
2. Read only the shared context that is relevant:
   - `docs/ai-context/PROJECT_STATE.md`
   - `docs/ai-context/ARCHITECTURE_INDEX.md`
   - relevant ADRs under `docs/ai-context/decisions/`
   - applicable `CLAUDE.md` and `.claude/rules/`
3. Consult your project memory before rediscovering code paths or past failures.
4. If `graphify-out/GRAPH_REPORT.md` exists, use Graphify as the first structural
   map for architecture/navigation questions. Prefer targeted Graphify queries
   (`graphify explain`, `graphify path`, or the installed Graphify skill) over
   broad repeated repository scans when appropriate.
5. Inspect the actual source before editing. Graphify is a navigation layer, not
   a substitute for verifying the current code.

## Execution contract

- Implement the handoff exactly within its stated scope.
- Preserve existing architecture and behavior outside scope.
- Do not silently make architectural decisions. If a necessary decision is
  ambiguous, stop with `Status: BLOCKED` and explain the decision Fable must make.
- Reuse established local patterns before introducing new abstractions.
- Run the specified verification. Add focused tests when needed.
- Do not force a second implementation pass when the first pass is correct.
- Never store secrets, credentials, patient-identifiable information, or other
  sensitive production data in agent memory or shared context files.

## Context-efficiency rules

- Do not reread files already sufficiently represented in your current resumed
  conversation unless they may have changed.
- For follow-up corrections in the same workstream, expect Fable to resume this
  same agent instance. Continue from prior context rather than restarting analysis.
- Keep raw logs, search output, and exploratory detail in your subagent context.
  Return a compressed implementation result to Fable.
- Save only durable, reusable discoveries to memory. Do not copy source code or
  transient task chatter into memory.

## Required completion protocol

Before stopping, update `docs/ai-context/CURRENT_HANDOFF.md`:

- Set `Status: READY_FOR_REVIEW` when implementation is complete, or
  `Status: BLOCKED` when a decision/input is required.
- Fill `## Implementation Result`.
- Fill `## Verification Evidence` with commands and outcomes.
- Fill `## Remaining Risks / Questions`.
- Fill `## Durable Learnings` with only information worth preserving.

Update your project memory with concise durable learnings:
- important code locations,
- non-obvious dependencies,
- reusable implementation patterns,
- failed approaches worth avoiding,
- recurring debugging knowledge.

Do not edit `PROJECT_STATE.md` or create an ADR unless Fable explicitly delegated
that documentation change. Fable remains the canonical project-state owner.
EOF
write_owned_file "$ROOT/.claude/agents/$AGENT_NAME.md" "$TMP"

# ---------------------------------------------------------------------------
# 2) Shared project truth
# ---------------------------------------------------------------------------
create_if_missing() {
  local path="$1"
  shift
  if [[ ! -e "$path" ]]; then
    mkdir -p "$(dirname "$path")"
    cat >"$path"
  fi
}

create_if_missing "$ROOT/docs/ai-context/PROJECT_STATE.md" <<'EOF'
# Project State

> Canonical compact project status for Fable 5 and delegated workers.
> Keep this concise. Update after accepted milestones, not after every edit.

## Current Objective

- Define the current project objective here.

## Active Milestones

- [ ] Add active milestones here.

## Accepted System Invariants

- Add project-wide invariants that must survive refactors.

## Recently Accepted Changes

- Add concise accepted changes only.

## Known Risks / Technical Debt

- Add active risks and debt that materially affect planning.

## Next Decisions

- Add unresolved decisions that belong to Fable 5.
EOF

create_if_missing "$ROOT/docs/ai-context/CURRENT_HANDOFF.md" <<'EOF'
# Current Handoff

Status: IDLE
Workstream: none
Owner: Fable 5
Worker: opus-implementation-worker
Last Updated: not started

## Objective

No active delegated task.

## Why

N/A

## Scope

- N/A

## Out of Scope

- N/A

## Existing Decisions / Constraints

- Consult `CLAUDE.md`, applicable `.claude/rules/`, and relevant ADRs.

## Acceptance Criteria

- N/A

## Verification Plan

- N/A

## Escalation Conditions

- Stop and return to Fable rather than guessing when an architectural,
  security, data-integrity, or irreversible product decision is required.

## Implementation Result

Not started.

## Verification Evidence

Not started.

## Remaining Risks / Questions

None recorded.

## Durable Learnings

None recorded.
EOF

create_if_missing "$ROOT/docs/ai-context/ARCHITECTURE_INDEX.md" <<'EOF'
# Architecture Index

This file is a compact index, not a duplicate of the codebase.

## Structural Map

- Preferred structural navigation: `graphify-out/GRAPH_REPORT.md` when present.
- Full Graphify graph: `graphify-out/graph.json` when present.
- Interactive Graphify map: `graphify-out/graph.html` when present.

## Canonical Decisions

- Architectural Decision Records: `docs/ai-context/decisions/`

## High-Level Subsystems

- Add subsystem names and canonical entry points as they become stable.

## Important Cross-System Boundaries

- Add only boundaries that repeatedly matter to planning and review.

## Update Rule

Do not copy large source summaries here. Record stable entry points, boundaries,
and links to ADRs. Use Graphify for structural discovery and the source code for
current implementation truth.
EOF

create_if_missing "$ROOT/docs/ai-context/decisions/README.md" <<'EOF'
# Architectural Decision Records

Create an ADR only for decisions that are costly to reverse or important to
future architectural reasoning.

Suggested filename:

`ADR-YYYYMMDD-short-title.md`

Suggested structure:

```markdown
# ADR: Title

Status: Accepted | Superseded | Proposed
Date: YYYY-MM-DD

## Context
## Decision
## Alternatives Considered
## Consequences
## Supersedes / Superseded By
## Relevant Code / Graphify Nodes
```
EOF

create_if_missing "$ROOT/.claude/agent-memory/$AGENT_NAME/MEMORY.md" <<'EOF'
# Opus Implementation Worker Memory

Keep this file concise. Store detailed durable notes in topic files and link them
from here.

## Stable Codebase Patterns

- None recorded yet.

## Important Code Locations

- None recorded yet.

## Recurring Debugging Knowledge

- None recorded yet.

## Failed Approaches Worth Avoiding

- None recorded yet.

## Memory Safety

Never store credentials, secrets, patient-identifiable information, production
records, or other sensitive data here.
EOF

# ---------------------------------------------------------------------------
# 3) Adaptive Fable -> Opus orchestration skill
# ---------------------------------------------------------------------------
TMP="$(tmpfile)"
cat >"$TMP" <<'EOF'
---
name: delegate-opus
description: >-
  Adaptive Fable 5 to Opus 4.8 orchestration for this project. Fable decides
  whether to work directly, retrieve project knowledge first, spawn a new Opus
  implementation workstream, or resume the existing Opus worker. Use for
  substantial coding tasks where bounded implementation can be delegated.
---

# Adaptive Fable 5 ↔ Opus 4.8 Collaboration

You are **Fable 5**, the project lead, architect, uncertainty owner, and final
quality gate. Opus 4.8 is the bounded implementation worker.

The user's task is: `$ARGUMENTS`

## Non-negotiable principle

Do **not** delegate mechanically. Choose the cheapest reliable action.

## Step 1 — Classify the task

Choose exactly one route:

### ROUTE A — FABLE_DIRECT

Use Fable directly when:
- the task is very small and delegation overhead would exceed execution cost;
- the hard part is architecture, cross-system reasoning, requirement conflict,
  security design, data-integrity reasoning, or an irreversible decision;
- the task is too ambiguous to hand off safely.

### ROUTE B — RETRIEVE_FIRST

Use when the correct route depends on project knowledge you do not yet have.
Retrieve only the minimum needed context:
1. Read the compact shared context.
2. When `graphify-out/GRAPH_REPORT.md` exists, consult Graphify before broad
   repository scanning for structural questions.
3. Read the actual relevant source before making a final decision.

Then reclassify as A, C, or D.

### ROUTE C — OPUS_NEW

Spawn `opus-implementation-worker` for a new coherent implementation workstream
when the objective and constraints can be bounded clearly.

### ROUTE D — OPUS_RESUME

For corrections, tests, or continuation of the same workstream in the current
parent session, **resume the existing Opus worker** using its agent ID/name via
`SendMessage`. Do not spawn a fresh worker and pay the context-reconstruction
cost. A resumed worker retains its conversation history.

If this is a new parent session and the old instance cannot be resumed, spawn a
new `opus-implementation-worker`; its `memory: project` and shared handoff files
provide cross-session continuity.

## Step 2 — For delegated work, write the task contract

Before spawning a new worker, rewrite
`docs/ai-context/CURRENT_HANDOFF.md` with:

- `Status: ACTIVE`
- a short `Workstream` identifier
- objective and why it matters
- exact scope
- explicit out-of-scope boundaries
- existing decisions and invariants
- objective acceptance criteria
- verification commands/evidence required
- escalation conditions: decisions Opus must return to Fable instead of guessing

Do not dump the whole project into the handoff. Point to canonical context,
relevant ADRs, Graphify, and exact files/modules where possible.

## Step 3 — Delegate execution

Invoke the custom `opus-implementation-worker`.

The worker:
- has model `claude-opus-4-8`;
- has project-scoped persistent memory;
- receives shared context from the SubagentStart hook;
- must update the handoff before it can stop.

For sequential corrections in the same workstream, resume the same worker.

## Step 4 — Fable review

Review only what is necessary:
1. inspect the resulting diff;
2. inspect verification evidence;
3. check acceptance criteria;
4. check architecture, edge cases, security, and data integrity in proportion to
   task risk.

### PASS

Accept the first pass when all objective gates pass. Do **not** manufacture a
second pass merely to create another loop.

Then:
- set `CURRENT_HANDOFF.md` to `Status: IDLE`;
- update `PROJECT_STATE.md` only if accepted project state materially changed;
- create/update an ADR only for an important architectural decision.

### FAIL

Write precise, bounded findings into the handoff and use ROUTE D to resume the
same Opus worker. Ask for fixes only for actual findings.

### BLOCKED

Fable resolves the decision or asks the user when required. Then resume the same
worker with the resolved instruction.

## Token and memory discipline

- Fable owns global reasoning; Opus owns bounded execution.
- Use Graphify for structural navigation, not as a substitute for source truth.
- Avoid duplicating large summaries in CLAUDE.md, handoff files, memory, and ADRs.
- Keep noisy implementation exploration inside the Opus subagent context.
- Keep shared files concise and canonical.
- Never store secrets or sensitive production/patient data in memory files.
EOF
write_owned_file "$ROOT/.claude/skills/delegate-opus/SKILL.md" "$TMP"

# ---------------------------------------------------------------------------
# 4) Rules: global orchestration + lazily loaded source-change protocol
# ---------------------------------------------------------------------------
TMP="$(tmpfile)"
cat >"$TMP" <<'EOF'
# Fable 5 ↔ Opus 4.8 Orchestration Rules

- The main project session is led by Fable 5.
- Fable owns architecture, decomposition, uncertainty, cross-system decisions,
  and final acceptance.
- `opus-implementation-worker` owns bounded implementation, tests, routine
  debugging, local refactors, and precise correction work.
- Use `/delegate-opus <task>` for adaptive routing rather than blindly delegating.
- Resume the same Opus worker for continuation/corrections in the same current
  session workstream whenever possible.
- Shared project truth lives under `docs/ai-context/`.
- Opus-specific durable memory lives under
  `.claude/agent-memory/opus-implementation-worker/`.
- Graphify is the preferred structural navigation layer when
  `graphify-out/GRAPH_REPORT.md` exists; verify current implementation in source.
- Never store credentials, secrets, patient-identifiable information, or
  production records in AI memory/context files.
EOF
write_owned_file "$ROOT/.claude/rules/00-fable-opus-orchestration.md" "$TMP"

# Generate a practical path-scoped rule from actual top-level project directories.
python3 - "$ROOT" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
out = root / ".claude" / "rules" / "10-source-change-protocol.md"
ignore = {
    ".git", ".claude", ".github", ".idea", ".vscode", ".venv", "venv",
    "node_modules", "dist", "build", "coverage", ".next", ".nuxt",
    "graphify-out", "docs", "vendor", "target", "__pycache__", ".cache",
}
preferred = [
    "src", "app", "apps", "packages", "lib", "server", "client", "frontend",
    "backend", "api", "services", "components", "tests", "test",
]
paths = []
for name in preferred:
    if (root / name).is_dir():
        paths.append(f"{name}/**")
if not paths:
    dirs = [
        p.name for p in root.iterdir()
        if p.is_dir() and p.name not in ignore and not p.name.startswith(".")
    ]
    paths = [f"{name}/**" for name in sorted(dirs)[:16]]
if not paths:
    paths = ["**/*.py", "**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx", "**/*.go", "**/*.rs"]

frontmatter = ["---", "paths:"]
frontmatter += [f'  - "{p}"' for p in paths]
frontmatter += ["---", ""]
body = """# Source Change Protocol

When modifying source code in these paths:

1. Obey the active handoff scope and accepted architecture.
2. If structural navigation is needed and Graphify output exists, consult it
   before broad repeated Grep/Glob scans.
3. Verify the current source before editing; generated knowledge can be stale.
4. Do not silently make cross-system or irreversible architectural decisions.
5. Run focused verification and record evidence in the active handoff.
6. Keep transient logs and exploration out of shared long-term memory.
"""
content = "\n".join(frontmatter) + body
if not out.exists() or out.read_text(encoding="utf-8") != content:
    out.write_text(content, encoding="utf-8")
PY

# ---------------------------------------------------------------------------
# 5) Hook scripts
# ---------------------------------------------------------------------------
TMP="$(tmpfile)"
cat >"$TMP" <<'PY'
#!/usr/bin/env python3
"""Inject compact dynamic context at session startup/resume/compaction."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], cwd: Path) -> str:
    try:
        return subprocess.check_output(
            cmd, cwd=cwd, stderr=subprocess.DEVNULL, text=True, timeout=3
        ).strip()
    except Exception:
        return ""


def clipped(path: Path, max_chars: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            return text[:max_chars].rstrip() + "\n… [clipped]"
        return text.rstrip()
    except Exception:
        return "(unavailable)"


try:
    event = json.load(sys.stdin)
except Exception:
    event = {}

root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd()).resolve()
branch = run(["git", "branch", "--show-current"], root) or "(not a git branch)"
status = run(["git", "status", "--short"], root)
status_lines = status.splitlines()
if len(status_lines) > 24:
    status = "\n".join(status_lines[:24]) + f"\n… +{len(status_lines)-24} more changed paths"
if not status:
    status = "(clean or unavailable)"

handoff = clipped(root / "docs/ai-context/CURRENT_HANDOFF.md", 4200)
project_state = clipped(root / "docs/ai-context/PROJECT_STATE.md", 3000)
graph_report = root / "graphify-out/GRAPH_REPORT.md"
graph_status = (
    f"available: {graph_report.relative_to(root)}"
    if graph_report.exists()
    else "not built in this project; Graphify may still be installed as a skill"
)

context = f"""[Fable↔Opus orchestration context]
Main-session role: Fable 5 = architect, manager, uncertainty owner, final quality gate.
Worker role: Opus 4.8 = bounded implementation, testing, routine debugging, precise fixes.
Routing skill: /delegate-opus <task>
Continuation rule: resume the same Opus worker for the same workstream when possible.
Graphify: {graph_status}
Git branch: {branch}
Git status:
{status}

CURRENT HANDOFF (compact snapshot):
{handoff}

PROJECT STATE (compact snapshot):
{project_state}
"""
print(context)
PY
write_owned_file "$ROOT/.claude/hooks/session_context.py" "$TMP"
chmod +x "$ROOT/.claude/hooks/session_context.py"

TMP="$(tmpfile)"
cat >"$TMP" <<'PY'
#!/usr/bin/env python3
"""Inject bounded shared context into the Opus implementation worker."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def clipped(path: Path, max_chars: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            return text[:max_chars].rstrip() + "\n… [clipped]"
        return text.rstrip()
    except Exception:
        return "(unavailable)"


try:
    event = json.load(sys.stdin)
except Exception:
    event = {}

root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd()).resolve()
graph = root / "graphify-out/GRAPH_REPORT.md"

context = f"""You are starting as the persistent Opus 4.8 implementation worker.

Use this order:
1. CURRENT_HANDOFF is the active task contract.
2. PROJECT_STATE and relevant ADRs are canonical shared project truth.
3. Your project memory prevents rediscovery of durable codebase knowledge.
4. Graphify is {'available at graphify-out/GRAPH_REPORT.md' if graph.exists() else 'not currently built in this project'}.
5. Verify current implementation in source before editing.

Do not redesign architecture silently. Escalate ambiguous cross-system,
security, data-integrity, or irreversible decisions to Fable.

CURRENT_HANDOFF:
{clipped(root / 'docs/ai-context/CURRENT_HANDOFF.md', 9000)}

PROJECT_STATE:
{clipped(root / 'docs/ai-context/PROJECT_STATE.md', 4500)}

ARCHITECTURE_INDEX:
{clipped(root / 'docs/ai-context/ARCHITECTURE_INDEX.md', 3500)}
"""

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "SubagentStart",
        "additionalContext": context,
    }
}))
PY
write_owned_file "$ROOT/.claude/hooks/opus_start_context.py" "$TMP"
chmod +x "$ROOT/.claude/hooks/opus_start_context.py"

TMP="$(tmpfile)"
cat >"$TMP" <<'PY'
#!/usr/bin/env python3
"""Require a structured handoff before the Opus worker can stop."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

try:
    event = json.load(sys.stdin)
except Exception:
    event = {}

root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd()).resolve()
path = root / "docs/ai-context/CURRENT_HANDOFF.md"

try:
    text = path.read_text(encoding="utf-8", errors="replace")
except Exception:
    print(
        "Before stopping, create/update docs/ai-context/CURRENT_HANDOFF.md and "
        "record status, implementation result, verification, risks/questions, "
        "and durable learnings.",
        file=sys.stderr,
    )
    sys.exit(2)

status_match = re.search(r"(?mi)^Status:\s*(.+?)\s*$", text)
status = status_match.group(1).strip().upper() if status_match else ""

def section(name: str) -> str:
    pattern = rf"(?ms)^##\s+{re.escape(name)}\s*$\n(.*?)(?=^##\s+|\Z)"
    m = re.search(pattern, text)
    return m.group(1).strip() if m else ""

def meaningful(value: str) -> bool:
    v = value.strip().lower()
    return bool(v) and v not in {
        "not started.", "not started", "n/a", "- n/a", "none recorded.", "none recorded"
    }

problems: list[str] = []

if status not in {"READY_FOR_REVIEW", "BLOCKED"}:
    problems.append("set `Status: READY_FOR_REVIEW` or `Status: BLOCKED`")

if status == "READY_FOR_REVIEW":
    required = [
        "Implementation Result",
        "Verification Evidence",
        "Remaining Risks / Questions",
        "Durable Learnings",
    ]
    for name in required:
        value = section(name)
        if not meaningful(value):
            problems.append(f"fill `## {name}` with a concrete result")
elif status == "BLOCKED":
    if not meaningful(section("Remaining Risks / Questions")):
        problems.append("explain the blocking decision/question in `## Remaining Risks / Questions`")

if problems:
    print(
        "Opus completion gate: before stopping, update CURRENT_HANDOFF.md:\n- "
        + "\n- ".join(problems)
        + "\nKeep it concise; do not fabricate a second implementation pass.",
        file=sys.stderr,
    )
    sys.exit(2)

sys.exit(0)
PY
write_owned_file "$ROOT/.claude/hooks/opus_stop_gate.py" "$TMP"
chmod +x "$ROOT/.claude/hooks/opus_stop_gate.py"

# ---------------------------------------------------------------------------
# 6) Merge the orchestration protocol into CLAUDE.md without touching Graphify
# ---------------------------------------------------------------------------
backup_if_exists "$ROOT/CLAUDE.md"

python3 - "$ROOT/CLAUDE.md" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
begin = "<!-- FABLE_OPUS_ORCHESTRATION:BEGIN -->"
end = "<!-- FABLE_OPUS_ORCHESTRATION:END -->"
block = """<!-- FABLE_OPUS_ORCHESTRATION:BEGIN -->
## Fable 5 ↔ Opus 4.8 Collaboration Protocol

- Use Fable 5 as the main-session architect, project manager, uncertainty owner,
  and final quality gate.
- Use `/delegate-opus <task>` for adaptive routing. Fable may work directly,
  retrieve project knowledge first, spawn a new Opus workstream, or resume the
  existing Opus worker.
- Delegate bounded implementation, tests, routine debugging, local refactors,
  and precise review fixes to `opus-implementation-worker`.
- Resume the same Opus worker for continuation/corrections in the same current
  workstream whenever possible; do not pay for needless context reconstruction.
- Canonical shared context:
  - `docs/ai-context/PROJECT_STATE.md`
  - `docs/ai-context/CURRENT_HANDOFF.md`
  - `docs/ai-context/ARCHITECTURE_INDEX.md`
  - `docs/ai-context/decisions/`
- When `graphify-out/GRAPH_REPORT.md` exists, consult Graphify first for
  structural/architecture navigation before broad repeated searches; verify the
  current implementation in source before editing.
- Accept a correct first implementation pass when objective gates pass. Do not
  force a second pass merely to create a loop.
- Never store secrets, credentials, patient-identifiable information, or
  production records in AI memory/context files.
<!-- FABLE_OPUS_ORCHESTRATION:END -->"""

text = path.read_text(encoding="utf-8") if path.exists() else ""
if begin in text and end in text:
    prefix, rest = text.split(begin, 1)
    _, suffix = rest.split(end, 1)
    new = prefix.rstrip() + "\n\n" + block + suffix
else:
    new = text.rstrip() + ("\n\n" if text.strip() else "") + block + "\n"
path.write_text(new, encoding="utf-8")
PY

# ---------------------------------------------------------------------------
# 7) Safely merge project settings. Existing Graphify hooks/plugins survive.
# ---------------------------------------------------------------------------
SETTINGS="$ROOT/.claude/settings.json"
backup_if_exists "$SETTINGS"

python3 - "$SETTINGS" "$PRESERVE_MODEL" <<'PY'
from __future__ import annotations
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
preserve_model = sys.argv[2] == "1"

if path.exists():
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"ERROR: {path} contains invalid JSON at line {exc.lineno}, column "
            f"{exc.colno}. Refusing to overwrite it."
        )
else:
    data = {}

if not isinstance(data, dict):
    raise SystemExit(f"ERROR: {path} must contain a top-level JSON object.")

if not preserve_model:
    data["model"] = "fable"

hooks = data.setdefault("hooks", {})
if not isinstance(hooks, dict):
    raise SystemExit("ERROR: existing `hooks` setting is not a JSON object.")

entries = {
    "SessionStart": {
        "matcher": "startup|resume|compact",
        "hooks": [{
            "type": "command",
            "command": "python3",
            "args": ["${CLAUDE_PROJECT_DIR}/.claude/hooks/session_context.py"],
            "timeout": 10
        }]
    },
    "SubagentStart": {
        "matcher": "opus-implementation-worker",
        "hooks": [{
            "type": "command",
            "command": "python3",
            "args": ["${CLAUDE_PROJECT_DIR}/.claude/hooks/opus_start_context.py"],
            "timeout": 10
        }]
    },
    "SubagentStop": {
        "matcher": "opus-implementation-worker",
        "hooks": [{
            "type": "command",
            "command": "python3",
            "args": ["${CLAUDE_PROJECT_DIR}/.claude/hooks/opus_stop_gate.py"],
            "timeout": 10
        }]
    }
}

owned_script_names = {
    "session_context.py",
    "opus_start_context.py",
    "opus_stop_gate.py",
}

def group_is_ours(group: object) -> bool:
    if not isinstance(group, dict):
        return False
    for h in group.get("hooks", []):
        if not isinstance(h, dict):
            continue
        for arg in h.get("args", []):
            if any(name in str(arg) for name in owned_script_names):
                return True
        cmd = str(h.get("command", ""))
        if any(name in cmd for name in owned_script_names):
            return True
    return False

for event, new_group in entries.items():
    current = hooks.setdefault(event, [])
    if not isinstance(current, list):
        raise SystemExit(f"ERROR: existing hooks.{event} is not a JSON array.")
    # Replace only this orchestrator's prior group; leave Graphify and every
    # unrelated hook untouched.
    current[:] = [g for g in current if not group_is_ours(g)]
    current.append(new_group)

path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY

# ---------------------------------------------------------------------------
# 8) Graphify integration: preserve installation, optionally refresh the graph
# ---------------------------------------------------------------------------
if [[ -f "$ROOT/graphify-out/GRAPH_REPORT.md" ]]; then
  echo "Graphify: existing project graph detected and integrated."
else
  echo "Graphify: installed integration will be preserved; no project graph detected."
  echo "          Build one inside Claude Code with: /graphify ."
fi

if [[ "$REFRESH_GRAPH" -eq 1 ]]; then
  if command_exists graphify; then
    echo "Refreshing Graphify project graph..."
    (cd "$ROOT" && graphify .)
  else
    echo "WARNING: --refresh-graph requested, but the Graphify CLI is not on PATH." >&2
    echo "         The Claude Code Graphify skill may still be installed; run /graphify . in Claude Code." >&2
  fi
fi

# ---------------------------------------------------------------------------
# 9) Optional LSP intelligence
# ---------------------------------------------------------------------------
install_plugin() {
  local plugin="$1"
  echo "Installing/enabling Claude Code plugin: $plugin"
  if ! claude plugin install "$plugin@claude-plugins-official" --scope project; then
    echo "WARNING: Could not install $plugin. Try from Claude Code: /plugin install $plugin@claude-plugins-official" >&2
  fi
}

has_files() {
  local regex="$1"
  find "$ROOT" \
    \( -path "$ROOT/.git" -o -path "$ROOT/node_modules" -o -path "$ROOT/.venv" \
       -o -path "$ROOT/venv" -o -path "$ROOT/dist" -o -path "$ROOT/build" \
       -o -path "$ROOT/graphify-out" -o -path "$ROOT/target" \) -prune -o \
    -type f -regextype posix-extended -regex "$regex" -print -quit 2>/dev/null | grep -q .
}

if [[ "$INSTALL_LSP" -eq 1 ]]; then
  echo "Detecting project languages for LSP support..."

  # macOS BSD find does not support -regextype. Use Python for portable detection.
  LANGS="$(python3 - "$ROOT" <<'PY'
from pathlib import Path
import os, sys
root = Path(sys.argv[1])
ignore = {".git","node_modules",".venv","venv","dist","build","graphify-out","target",".next","coverage"}
exts = set()
count = 0
for base, dirs, files in os.walk(root):
    dirs[:] = [d for d in dirs if d not in ignore]
    for f in files:
        exts.add(Path(f).suffix.lower())
        count += 1
        if count > 50000:
            break
    if count > 50000:
        break
langs = []
if exts & {".ts",".tsx",".js",".jsx",".mts",".cts",".mjs",".cjs"}: langs.append("typescript")
if exts & {".py",".pyi"}: langs.append("python")
if ".go" in exts: langs.append("go")
if ".rs" in exts: langs.append("rust")
if exts & {".c",".cc",".cpp",".cxx",".h",".hpp"}: langs.append("cpp")
if ".cs" in exts: langs.append("csharp")
if ".swift" in exts: langs.append("swift")
print(" ".join(langs))
PY
)"

  if [[ "$LANGS" == *"typescript"* ]]; then
    if ! command_exists typescript-language-server; then
      if command_exists npm; then
        echo "Installing TypeScript language server..."
        npm install -g typescript-language-server typescript || \
          echo "WARNING: TypeScript language-server installation failed." >&2
      else
        echo "WARNING: npm not found; install typescript-language-server and typescript manually." >&2
      fi
    fi
    command_exists typescript-language-server && install_plugin "typescript-lsp"
  fi

  if [[ "$LANGS" == *"python"* ]]; then
    if ! command_exists pyright-langserver; then
      if command_exists npm; then
        echo "Installing Pyright language server..."
        npm install -g pyright || echo "WARNING: Pyright installation failed." >&2
      else
        echo "WARNING: pyright-langserver not found. Install Pyright, then rerun --install-lsp." >&2
      fi
    fi
    command_exists pyright-langserver && install_plugin "pyright-lsp"
  fi

  if [[ "$LANGS" == *"go"* ]]; then
    command_exists gopls && install_plugin "gopls-lsp" || \
      echo "WARNING: Go detected but gopls is not on PATH; install gopls first." >&2
  fi

  if [[ "$LANGS" == *"rust"* ]]; then
    command_exists rust-analyzer && install_plugin "rust-analyzer-lsp" || \
      echo "WARNING: Rust detected but rust-analyzer is not on PATH; install it first." >&2
  fi

  if [[ "$LANGS" == *"cpp"* ]]; then
    command_exists clangd && install_plugin "clangd-lsp" || \
      echo "WARNING: C/C++ detected but clangd is not on PATH; install it first." >&2
  fi

  if [[ "$LANGS" == *"csharp"* ]]; then
    command_exists csharp-ls && install_plugin "csharp-lsp" || \
      echo "WARNING: C# detected but csharp-ls is not on PATH; install it first." >&2
  fi

  if [[ "$LANGS" == *"swift"* ]]; then
    command_exists sourcekit-lsp && install_plugin "swift-lsp" || \
      echo "WARNING: Swift detected but sourcekit-lsp is not on PATH." >&2
  fi

  [[ -n "$LANGS" ]] || echo "No supported source language detected for automatic LSP setup."
fi

# ---------------------------------------------------------------------------
# 10) Final validation and instructions
# ---------------------------------------------------------------------------
echo
echo "Configuration complete."
echo
echo "Installed:"
echo "  • Fable main-session project model: $( [[ "$PRESERVE_MODEL" -eq 1 ]] && echo "preserved" || echo "fable" )"
echo "  • Opus worker: .claude/agents/$AGENT_NAME.md"
echo "  • Adaptive skill: /delegate-opus"
echo "  • Shared context: docs/ai-context/"
echo "  • Opus project memory: .claude/agent-memory/$AGENT_NAME/"
echo "  • Hooks merged into: .claude/settings.json"
echo "  • Graphify: preserved and referenced, not reinstalled or overwritten"
echo "  • Backup of changed pre-existing files: ${BACKUP_DIR#"$ROOT"/}"
echo
echo "Important:"
echo "  1. New/updated project agents may require a fresh Claude Code session if the"
echo "     .claude/agents directory did not exist when the current session started."
echo "  2. Use: /delegate-opus <substantial task>"
echo "  3. Inside the same workstream, Fable should resume the same Opus worker rather"
echo "     than spawn a new one."
echo "  4. Run /hooks to inspect the merged hooks and /status to inspect active settings."
echo "  5. Run claude doctor if Claude Code reports a settings/plugin error."

if [[ "$LAUNCH" -eq 1 ]]; then
  echo
  echo "Launching a new Fable 5 Claude Code session..."
  cd "$ROOT"
  exec claude --model fable
fi
