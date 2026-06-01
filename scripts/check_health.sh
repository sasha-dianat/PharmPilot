#!/usr/bin/env bash
# Quick health check — run any time to see which services are up
CYAN='\033[0;36m'; GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }

# Load ports from .env if present
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$ROOT_DIR/.env" ] && set -o allexport && source "$ROOT_DIR/.env" && set +o allexport
PG_PORT="${PHARMPILOT_PGPORT:-5433}"
PG_BIN="${PHARMPILOT_PG_BIN:-/usr/local/opt/postgresql@16/bin}"
API_PORT=8001
FRONTEND_PORT=3001
QDRANT_PORT=6334

echo -e "${CYAN}PharmPilot Health Check${NC}"
echo "─────────────────────────────────────────────────────────────"

# PostgreSQL (pharmpilot cluster)
PGPASSWORD="pharmpilot_dev" "$PG_BIN/psql" -h 127.0.0.1 -p "$PG_PORT" \
    -U pharmpilot -d pharmpilot -c "SELECT 1" > /dev/null 2>&1 \
  && ok "PostgreSQL       :$PG_PORT  running  (pharmpilot cluster)" \
  || fail "PostgreSQL       :$PG_PORT  NOT running  →  bash scripts/setup_postgres.sh"

# Redis
redis-cli -p 6379 ping 2>/dev/null | grep -q PONG \
  && ok "Redis            :6379   running" \
  || fail "Redis            :6379   NOT running  →  brew services start redis"

# Qdrant
curl -sf "http://localhost:$QDRANT_PORT/health" 2>/dev/null | grep -q "ok\|true\|status" \
  && ok "Qdrant           :$QDRANT_PORT  running  → http://localhost:$QDRANT_PORT/dashboard" \
  || fail "Qdrant           :$QDRANT_PORT  NOT running  (optional for non-AI use)"

# FastAPI
curl -sf "http://localhost:$API_PORT/health" 2>/dev/null | grep -q "pharmpilot\|ok" \
  && ok "FastAPI API      :$API_PORT  running  → http://localhost:$API_PORT/docs" \
  || fail "FastAPI API      :$API_PORT  NOT running  →  check .logs/api.log"

# React frontend
curl -sf "http://localhost:$FRONTEND_PORT" 2>/dev/null | head -1 | grep -qi "html\|doctype\|vite" \
  && ok "React Frontend   :$FRONTEND_PORT  running  → http://localhost:$FRONTEND_PORT" \
  || fail "React Frontend   :$FRONTEND_PORT  NOT running  →  check .logs/frontend.log"

echo ""
