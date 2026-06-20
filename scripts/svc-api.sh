#!/usr/bin/env bash
# launchd-managed API launcher. Loads .env, waits for Postgres + Qdrant to be
# reachable (so boot order / sleep-wake doesn't crash-loop), then exec's uvicorn
# under launchd's KeepAlive supervision.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -f "$ROOT/.env" ] && set -o allexport && source "$ROOT/.env" && set +o allexport

# Embedding (tokenizers) uses Rust thread parallelism; combined with the async
# event loop this can deadlock the subsequent LLM HTTP call (RAG retrieve →
# synthesize hangs until timeout → KB falls back to extractive). Disable it.
export TOKENIZERS_PARALLELISM=false

PY="${PHARMPILOT_PYTHON:-$(command -v python3)}"
API_PORT="${PHARMPILOT_API_PORT:-8001}"
PG_PORT="${PHARMPILOT_PGPORT:-5433}"
QDRANT_PORT=6334

# Wait up to ~3 min for dependencies before starting (avoids restart storms).
for _ in $(seq 1 90); do
  if nc -z localhost "$PG_PORT" 2>/dev/null && nc -z localhost "$QDRANT_PORT" 2>/dev/null; then
    break
  fi
  sleep 2
done

exec "$PY" -m uvicorn services.platform.main:app --host 127.0.0.1 --port "$API_PORT"
