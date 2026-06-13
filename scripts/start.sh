#!/usr/bin/env bash
# ============================================================
#  PharmPilot — development services launcher
#  Usage:  ./scripts/start.sh [--seed] [--no-frontend]
#
#  Ports (8001/3001 to avoid conflict with other local projects on 8000/3000):
#    1. PostgreSQL (if not running) — port 5433
#    2. Qdrant vector store          — port 6334
#    3. FastAPI backend              — port 8001
#    4. Vite frontend (optional)     — port 3001
#
#  Options:
#    --seed         Re-seed demo patients / prescriptions
#    --no-frontend  Skip the frontend (API-only mode)
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
err()  { echo -e "${RED}✗${NC}  $*"; }

API_PORT=8001
FRONTEND_PORT=3001

SEED=false
FRONTEND=true
for arg in "$@"; do
  case $arg in
    --seed)        SEED=true ;;
    --no-frontend) FRONTEND=false ;;
  esac
done

echo ""
echo "╔════════════════════════════════════════════════════╗"
echo "║            PharmPilot  —  Starting Services        ║"
echo "╚════════════════════════════════════════════════════╝"
echo ""

# ── 1. PostgreSQL ────────────────────────────────────────────
PGDATA="${HOME}/.pharmpilot/pgdata"
if pg_ctl -D "$PGDATA" status > /dev/null 2>&1; then
  ok "PostgreSQL already running (port 5433)"
else
  warn "Starting PostgreSQL…"
  LC_ALL=C pg_ctl -D "$PGDATA" -l /tmp/pharmpilot_pg.log start
  sleep 2
  ok "PostgreSQL started"
fi

# ── 2. Qdrant ────────────────────────────────────────────────
if curl -s http://localhost:6334/collections > /dev/null 2>&1; then
  ok "Qdrant already running (port 6334)"
else
  warn "Starting Qdrant…"
  "$ROOT/.qdrant/qdrant" --config-path "$ROOT/.qdrant/config.yaml" \
    > /tmp/pharmpilot_qdrant.log 2>&1 &
  sleep 3
  if curl -s http://localhost:6334/collections > /dev/null 2>&1; then
    ok "Qdrant started ($(curl -s 'http://localhost:6334/collections/pharmpilot_clinical_knowledge' \
      | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("result",{}).get("points_count","?"))' 2>/dev/null) vectors)"
  else
    err "Qdrant failed to start — check /tmp/pharmpilot_qdrant.log"
  fi
fi

# ── 3. Backend API ───────────────────────────────────────────
if curl -s http://localhost:${API_PORT}/health > /dev/null 2>&1; then
  ok "Backend API already running (port ${API_PORT})"
else
  warn "Starting FastAPI backend…"
  LC_ALL=C uvicorn services.platform.main:app \
    --host 0.0.0.0 --port ${API_PORT} --reload \
    > /tmp/pharmpilot_api.log 2>&1 &
  API_PID=$!
  # Wait up to 10s
  for i in {1..10}; do
    sleep 1
    if curl -s http://localhost:${API_PORT}/health > /dev/null 2>&1; then
      ok "Backend API started (PID $API_PID)"
      break
    fi
    if [[ $i -eq 10 ]]; then
      err "Backend API failed to start — check /tmp/pharmpilot_api.log"
    fi
  done
fi

# ── 4. Apply pending migrations ──────────────────────────────
warn "Checking database migrations…"
DB_URL="postgresql+asyncpg://pharmpilot:pharmpilot_dev@127.0.0.1:5433/pharmpilot"
CURRENT=$(DATABASE_URL="$DB_URL" PYTHONPATH="$ROOT" \
  python3 -m alembic -c "$ROOT/alembic.ini" current 2>/dev/null | grep -v INFO | tr -d '\n')
if [[ "$CURRENT" != *"head"* ]]; then
  warn "Running migrations to head…"
  DATABASE_URL="$DB_URL" PYTHONPATH="$ROOT" \
    python3 -m alembic -c "$ROOT/alembic.ini" upgrade head 2>&1 | grep -E "Running|INFO" | tail -5
  ok "Migrations applied"
else
  ok "Database at head"
fi

# ── 5. Seed demo data ────────────────────────────────────────
if [[ "$SEED" == "true" ]]; then
  warn "Seeding demo data (--reset)…"
  python3 "$ROOT/scripts/seed_demo_data.py" --reset
  ok "Demo data seeded"
else
  # Only seed if DB is empty
  PAT_COUNT=$(psql "postgresql://pharmpilot:pharmpilot_dev@127.0.0.1:5433/pharmpilot" \
    -t -c "SELECT COUNT(*) FROM patients;" 2>/dev/null | tr -d ' ')
  if [[ "${PAT_COUNT:-0}" == "0" ]]; then
    warn "Empty database — seeding demo data…"
    python3 "$ROOT/scripts/seed_demo_data.py"
    ok "Demo data seeded"
  else
    ok "Database has ${PAT_COUNT} patients — skipping seed"
  fi
fi

# ── 6. Frontend ──────────────────────────────────────────────
# Always write the correct API URL (8001) before starting Vite
printf 'VITE_API_URL=http://localhost:%s/api/v1\nVITE_WS_URL=ws://localhost:%s\n' \
  "$API_PORT" "$API_PORT" > "$ROOT/frontend/workstation/.env.local"

if [[ "$FRONTEND" == "true" ]]; then
  if curl -s -o /dev/null -w "%{http_code}" http://localhost:${FRONTEND_PORT} 2>/dev/null | grep -q "200\|304"; then
    ok "Frontend already running (port ${FRONTEND_PORT})"
  else
    warn "Starting frontend…"
    cd "$ROOT/frontend/workstation"
    PORT=${FRONTEND_PORT} npm run dev > /tmp/pharmpilot_fe.log 2>&1 &
    FE_PID=$!
    sleep 5
    if curl -s -o /dev/null -w "%{http_code}" http://localhost:${FRONTEND_PORT} 2>/dev/null | grep -q "200\|304"; then
      ok "Frontend started (PID $FE_PID)"
    else
      err "Frontend failed — check /tmp/pharmpilot_fe.log"
    fi
    cd "$ROOT"
  fi
fi

echo ""
echo "╔════════════════════════════════════════════════════╗"
echo "║  All services ready!                               ║"
echo "╠════════════════════════════════════════════════════╣"
echo "║  Workstation     http://localhost:3001             ║"
echo "║  API             http://localhost:8001             ║"
echo "║  API Docs        http://localhost:8001/docs        ║"
echo "║  Qdrant UI       http://localhost:6334/dashboard   ║"
echo "╠════════════════════════════════════════════════════╣"
echo "║  Login (admin)       admin / PharmPilot2024!       ║"
echo "║  Login (pharmacist)  pharmacist / Pharmacist2024!  ║"
echo "║  Login (tech)        tech / Tech2024!              ║"
echo "╚════════════════════════════════════════════════════╝"
echo ""
