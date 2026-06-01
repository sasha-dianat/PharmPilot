#!/usr/bin/env bash
# Quick health check — run any time to see which services are up
CYAN='\033[0;36m'; GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }

echo -e "${CYAN}PharmPilot Health Check${NC}"
echo "─────────────────────────────────────"

# PostgreSQL
/usr/local/opt/postgresql@16/bin/pg_ctl status -D /usr/local/var/postgresql@16 2>/dev/null | grep -q "running" \
  && ok "PostgreSQL 16    :5432  running" \
  || fail "PostgreSQL 16    :5432  NOT running  →  start: bash scripts/dev.sh"

# Redis
redis-cli ping 2>/dev/null | grep -q PONG \
  && ok "Redis            :6379  running" \
  || fail "Redis            :6379  NOT running  →  brew services start redis"

# Qdrant
curl -sf http://localhost:6333/health 2>/dev/null | grep -q "ok\|true" \
  && ok "Qdrant           :6333  running" \
  || fail "Qdrant           :6333  NOT running"

# FastAPI
curl -sf http://localhost:8000/health 2>/dev/null | grep -q "ok" \
  && ok "FastAPI API      :8000  running  → http://localhost:8000/docs" \
  || fail "FastAPI API      :8000  NOT running  →  check .logs/api.log"

# React frontend
curl -sf http://localhost:3000 2>/dev/null | grep -q "html\|PharmPilot\|vite" \
  && ok "React Frontend   :3000  running  → http://localhost:3000" \
  || fail "React Frontend   :3000  NOT running  →  check .logs/frontend.log"

echo ""
