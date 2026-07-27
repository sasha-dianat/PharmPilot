#!/usr/bin/env bash
set -Eeuo pipefail
PROJECT="${1:-$(pwd)}"
cd "$PROJECT"
[[ -f AGENTS.md ]] || { echo "AGENTS.md is missing in $PROJECT" >&2; exit 1; }
[[ -f docs/ai-context/CODEX_PROJECT_BIBLE.md ]] || {
  echo "docs/ai-context/CODEX_PROJECT_BIBLE.md is missing in $PROJECT" >&2
  exit 1
}
PROMPT_FILE="docs/ai-context/CODEX_CONTINUATION_PROMPT.md"
[[ -f "$PROMPT_FILE" ]] || { echo "$PROMPT_FILE is missing" >&2; exit 1; }
exec codex \
  --ask-for-approval on-request \
  -C "$PROJECT" \
  --sandbox workspace-write \
  "$(cat "$PROMPT_FILE")"
