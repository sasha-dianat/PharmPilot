#!/usr/bin/env bash
# =============================================================================
#  PharmPilot AI — Start all local development services
#  Run: bash scripts/dev.sh
#  Stop: Ctrl+C (stops everything cleanly)
# =============================================================================
set -euo pipefail
CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${CYAN}[dev]${NC}  $1"; }
ok()   { echo -e "${GREEN}[ up]${NC}  $1"; }
warn() { echo -e "${YELLOW}[warn]${NC} $1"; }

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

# Load .env
if [[ -f "$ROOT_DIR/.env" ]]; then
  set -o allexport
  source "$ROOT_DIR/.env"
  set +o allexport
fi

PGDATA="/usr/local/var/postgresql@16"
QDRANT_BIN="$ROOT_DIR/.qdrant/qdrant"
QDRANT_STORAGE="$ROOT_DIR/.qdrant/storage"
LOG_DIR="$ROOT_DIR/.logs"
mkdir -p "$LOG_DIR"

# PID tracking for clean shutdown
PIDS=()
cleanup() {
  echo ""
  log "Shutting down all services..."
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  # Celery worker
  pkill -f "celery.*pharmpilot" 2>/dev/null || true
  log "All services stopped. Goodbye."
  exit 0
}
trap cleanup INT TERM

# ── 1. PostgreSQL ─────────────────────────────────────────────────────────
log "Checking PostgreSQL..."
# Check if any PostgreSQL is already running (EDB or Homebrew)
if PGPASSWORD="pharmpilot_dev" psql -h 127.0.0.1 -U pharmpilot -d pharmpilot -c "SELECT 1" >/dev/null 2>&1; then
  ok "PostgreSQL      → 127.0.0.1:5432 (pharmpilot DB accessible)"
elif PGPASSWORD="pharmpilot_dev" psql -h 127.0.0.1 -p 5433 -U pharmpilot -d pharmpilot -c "SELECT 1" >/dev/null 2>&1; then
  ok "PostgreSQL      → 127.0.0.1:5433 (Homebrew on non-default port)"
else
  # Try starting Homebrew PostgreSQL
  PG_BREW_BIN="/usr/local/opt/postgresql@16/bin"
  PGDATA_BREW="/usr/local/var/postgresql@16"
  if [[ -f "$PG_BREW_BIN/pg_ctl" ]] && [[ -d "$PGDATA_BREW" ]]; then
    log "Starting Homebrew PostgreSQL..."
    "$PG_BREW_BIN/pg_ctl" start -D "$PGDATA_BREW" -l "$PGDATA_BREW/server.log" -w 2>/dev/null || true
    sleep 2
    ok "PostgreSQL      → localhost (Homebrew started)"
  else
    warn "PostgreSQL not accessible. Run: bash scripts/setup_postgres.sh first"
    warn "If using EDB PostgreSQL, ensure pharmpilot user is created."
  fi
fi

# ── 2. Redis ─────────────────────────────────────────────────────────────
log "Checking Redis..."
redis-cli ping 2>/dev/null | grep -q PONG || {
  log "Starting Redis..."
  redis-server --daemonize yes --logfile "$LOG_DIR/redis.log" --port 6379
  sleep 1
}
ok "Redis           → localhost:6379"

# ── 3. Qdrant ────────────────────────────────────────────────────────────
log "Checking Qdrant..."
curl -sf http://localhost:6333/health >/dev/null 2>&1 || {
  if [[ -f "$QDRANT_BIN" ]]; then
    log "Starting Qdrant..."
    "$QDRANT_BIN" --storage-path "$QDRANT_STORAGE" \
      > "$LOG_DIR/qdrant.log" 2>&1 &
    PIDS+=($!)
    sleep 2
    curl -sf http://localhost:6333/health >/dev/null 2>&1 && ok "Qdrant          → localhost:6333" \
      || warn "Qdrant may still be starting — check $LOG_DIR/qdrant.log"
  else
    warn "Qdrant binary not found at $QDRANT_BIN"
    warn "Run: bash scripts/setup_local.sh to install it"
  fi
}
curl -sf http://localhost:6333/health >/dev/null 2>&1 && ok "Qdrant          → localhost:6333" || true

# ── 4. Run pending migrations ─────────────────────────────────────────────
log "Checking database migrations..."
source "$ROOT_DIR/.venv/bin/activate"
PYTHONPATH="$ROOT_DIR" alembic -c alembic.ini upgrade head 2>&1 | grep -v "^$" || true
ok "Migrations      → up to date"

# ── 5. FastAPI backend ────────────────────────────────────────────────────
log "Starting FastAPI backend..."
source "$ROOT_DIR/.venv/bin/activate"
PYTHONPATH="$ROOT_DIR" \
  uvicorn services.platform.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --reload \
    --reload-dir services \
    --reload-dir shared \
    --log-level info \
    > "$LOG_DIR/api.log" 2>&1 &
API_PID=$!
PIDS+=($API_PID)

# Wait for API to be ready
log "Waiting for API to start..."
for i in $(seq 1 15); do
  curl -sf http://localhost:8000/health >/dev/null 2>&1 && break || sleep 1
done
curl -sf http://localhost:8000/health >/dev/null 2>&1 \
  && ok "FastAPI API     → http://localhost:8000" \
  || { warn "API slow to start — check $LOG_DIR/api.log"; }

# ── 6. React workstation ──────────────────────────────────────────────────
log "Starting React workstation..."
cd "$ROOT_DIR/frontend/workstation"
npm run dev -- --port 3000 \
  > "$ROOT_DIR/$LOG_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!
PIDS+=($FRONTEND_PID)
cd "$ROOT_DIR"

# Wait for frontend
for i in $(seq 1 12); do
  curl -sf http://localhost:3000 >/dev/null 2>&1 && break || sleep 1
done
ok "React Frontend  → http://localhost:3000"

# ── 7. Summary ───────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║             PharmPilot Running Locally ✓                    ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${CYAN}Service               URL                          Log${NC}"
echo -e "  ─────────────────────────────────────────────────────────────────"
echo -e "  Workstation UI        ${GREEN}http://localhost:3000${NC}        .logs/frontend.log"
echo -e "  FastAPI + Swagger     ${GREEN}http://localhost:8000/docs${NC}   .logs/api.log"
echo -e "  FastAPI Health        ${GREEN}http://localhost:8000/health${NC}"
echo -e "  PostgreSQL            localhost:5432"
echo -e "  Redis                 localhost:6379"
echo -e "  Qdrant Dashboard      ${GREEN}http://localhost:6333/dashboard${NC}"
echo ""
echo -e "  ${YELLOW}Login:${NC}  admin / Admin1234!   (pharmacist / Pharmacist1234!)"
echo ""
echo -e "  ${CYAN}API Docs:${NC} http://localhost:8000/docs  (interactive Swagger UI)"
echo ""
echo -e "  Press ${RED}Ctrl+C${NC} to stop all services"
echo ""

# ── 8. Tail all logs ──────────────────────────────────────────────────────
log "Tailing logs (Ctrl+C to stop all)..."
tail -f \
  "$LOG_DIR/api.log" \
  "$LOG_DIR/frontend.log" \
  2>/dev/null &
TAIL_PID=$!
PIDS+=($TAIL_PID)

wait "${PIDS[0]}" 2>/dev/null || true
