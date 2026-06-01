#!/usr/bin/env bash
# =============================================================================
#  PharmPilot AI — Start all local development services
#  Run:  bash scripts/dev.sh
#  Stop: Ctrl+C
# =============================================================================
set -euo pipefail
CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${CYAN}[dev]${NC}  $1"; }
ok()   { echo -e "${GREEN}[ up]${NC}  $1"; }
warn() { echo -e "${YELLOW}[warn]${NC} $1"; }

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

# Load .env — this sets DATABASE_URL, REDIS_URL, QDRANT_URL, PG_BIN/PORT, etc.
[ -f "$ROOT_DIR/.env" ] && set -o allexport && source "$ROOT_DIR/.env" && set +o allexport

# Defaults (in case .env was not created yet by setup_postgres.sh)
DB_URL="${DATABASE_URL:-postgresql+asyncpg://pharmpilot:pharmpilot_dev@127.0.0.1:5433/pharmpilot}"
PG_BIN="${PHARMPILOT_PG_BIN:-/usr/local/opt/postgresql@16/bin}"
PGDATA_DIR="${PHARMPILOT_PGDATA:-$HOME/.pharmpilot/pgdata}"
PG_PORT="${PHARMPILOT_PGPORT:-5433}"
PG_LOG="${PHARMPILOT_PGLOG:-$HOME/.pharmpilot/postgres.log}"

LOG_DIR="$ROOT_DIR/.logs"
QDRANT_BIN="$ROOT_DIR/.qdrant/qdrant"
QDRANT_STORAGE="$ROOT_DIR/.qdrant/storage"
mkdir -p "$LOG_DIR" "$QDRANT_STORAGE"

# PID list for clean shutdown
PIDS=()
cleanup() {
  echo ""
  log "Shutting down..."
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  pkill -f "uvicorn services.platform.main" 2>/dev/null || true
  pkill -f "vite.*3000"                     2>/dev/null || true
  log "All services stopped."
  exit 0
}
trap cleanup INT TERM

# ── 1. PostgreSQL ─────────────────────────────────────────────────────────
log "Checking PostgreSQL (pharmpilot cluster on port $PG_PORT)..."

if PGPASSWORD="pharmpilot_dev" "$PG_BIN/psql" \
      -h 127.0.0.1 -p "$PG_PORT" \
      -U pharmpilot -d pharmpilot \
      -c "SELECT 1" > /dev/null 2>&1; then
  ok "PostgreSQL      → 127.0.0.1:$PG_PORT  ✓"
elif [ -f "$PGDATA_DIR/PG_VERSION" ]; then
  log "Starting PharmPilot PostgreSQL cluster..."
  LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 \
    "$PG_BIN/pg_ctl" start \
    -D "$PGDATA_DIR" \
    -l "$PG_LOG" \
    -w -t 15 \
    > /dev/null 2>&1 || true
  sleep 2
  PGPASSWORD="pharmpilot_dev" "$PG_BIN/psql" \
      -h 127.0.0.1 -p "$PG_PORT" -U pharmpilot -d pharmpilot \
      -c "SELECT 1" > /dev/null 2>&1 \
    && ok "PostgreSQL      → 127.0.0.1:$PG_PORT  ✓" \
    || { warn "PostgreSQL not responding. Run: bash scripts/setup_postgres.sh"; }
else
  warn "No PharmPilot PG cluster found. Run: bash scripts/setup_postgres.sh"
fi

# ── 2. Redis ──────────────────────────────────────────────────────────────
log "Checking Redis..."
redis-cli -p 6379 ping 2>/dev/null | grep -q PONG || {
  log "Starting Redis..."
  redis-server --daemonize yes \
    --logfile "$LOG_DIR/redis.log" \
    --port 6379 > /dev/null 2>&1 || \
  brew services start redis 2>/dev/null || true
  sleep 1
}
redis-cli -p 6379 ping 2>/dev/null | grep -q PONG \
  && ok "Redis           → localhost:6379  ✓" \
  || warn "Redis not responding — check $LOG_DIR/redis.log"

# ── 3. Qdrant ─────────────────────────────────────────────────────────────
log "Checking Qdrant..."
curl -sf http://localhost:6333/health > /dev/null 2>&1 || {
  if [ -f "$QDRANT_BIN" ]; then
    log "Starting Qdrant..."
    "$QDRANT_BIN" \
      --storage-path "$QDRANT_STORAGE" \
      > "$LOG_DIR/qdrant.log" 2>&1 &
    PIDS+=($!)
    sleep 2
  else
    warn "Qdrant not found. Run: bash scripts/setup_local.sh"
  fi
}
curl -sf http://localhost:6333/health > /dev/null 2>&1 \
  && ok "Qdrant          → localhost:6333  ✓" \
  || warn "Qdrant not running (optional for non-AI features)"

# ── 4. Apply any pending migrations ───────────────────────────────────────
log "Checking migrations..."
DATABASE_URL="$DB_URL" PYTHONPATH="$ROOT_DIR" \
  alembic -c "$ROOT_DIR/alembic.ini" upgrade head 2>&1 \
  | grep -E "Running upgrade|up to date|head" | head -3 || true
ok "Migrations      → up to date"

# ── 5. FastAPI backend ────────────────────────────────────────────────────
log "Starting FastAPI backend..."
# activate venv if present
[ -f "$ROOT_DIR/.venv/bin/activate" ] && source "$ROOT_DIR/.venv/bin/activate"

DATABASE_URL="$DB_URL" PYTHONPATH="$ROOT_DIR" \
  uvicorn services.platform.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --reload \
    --reload-dir "$ROOT_DIR/services" \
    --reload-dir "$ROOT_DIR/shared" \
    --log-level warning \
    > "$LOG_DIR/api.log" 2>&1 &
PIDS+=($!)

# Wait for API to be ready
log "Waiting for API..."
for i in $(seq 1 20); do
  curl -sf http://localhost:8000/health 2>/dev/null | grep -q "pharmpilot" && break || sleep 1
done
curl -sf http://localhost:8000/health 2>/dev/null | grep -q "pharmpilot" \
  && ok "FastAPI API     → http://localhost:8000  ✓" \
  || warn "API slow to start — check $LOG_DIR/api.log"

# ── 6. React workstation ──────────────────────────────────────────────────
log "Starting React workstation..."
cd "$ROOT_DIR/frontend/workstation"
npm run dev -- --port 3000 --host \
  > "$ROOT_DIR/$LOG_DIR/frontend.log" 2>&1 &
PIDS+=($!)
cd "$ROOT_DIR"

sleep 3
curl -sf http://localhost:3000 > /dev/null 2>&1 \
  && ok "React Frontend  → http://localhost:3000  ✓" \
  || ok "React Frontend  → http://localhost:3000  (still starting...)"

# ── Summary ───────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║              PharmPilot Running  ✓                              ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${GREEN}Workstation UI    ${NC}→  http://localhost:3000"
echo -e "  ${GREEN}API + Swagger     ${NC}→  http://localhost:8000/docs"
echo -e "  ${GREEN}API Health        ${NC}→  http://localhost:8000/health"
echo -e "  ${GREEN}Qdrant Dashboard  ${NC}→  http://localhost:6333/dashboard"
echo ""
echo -e "  ${YELLOW}Login:${NC}  admin / Admin1234!   |   pharmacist / Pharmacist1234!"
echo ""
echo -e "  ${CYAN}Logs:${NC}  .logs/api.log  |  .logs/frontend.log  |  .logs/redis.log"
echo ""
echo -e "  Press ${RED}Ctrl+C${NC} to stop all services"
echo ""

# Tail logs
tail -f "$LOG_DIR/api.log" "$LOG_DIR/frontend.log" 2>/dev/null &
PIDS+=($!)

wait "${PIDS[0]}" 2>/dev/null || true
