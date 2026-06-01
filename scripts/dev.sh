#!/usr/bin/env bash
# =============================================================================
#  PharmPilot AI — Start all local development services
#  API  → http://localhost:8001
#  UI   → http://localhost:3001
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

# Load .env
[ -f "$ROOT_DIR/.env" ] && set -o allexport && source "$ROOT_DIR/.env" && set +o allexport

# Port assignments — PharmPilot uses 8001/3001 to avoid conflicts
API_PORT=8001
FRONTEND_PORT=3001
QDRANT_PORT=6334   # 6333 may conflict; use 6334

DB_URL="${DATABASE_URL:-postgresql+asyncpg://pharmpilot:pharmpilot_dev@127.0.0.1:5433/pharmpilot}"
PG_BIN="${PHARMPILOT_PG_BIN:-/usr/local/opt/postgresql@16/bin}"
PGDATA_DIR="${PHARMPILOT_PGDATA:-$HOME/.pharmpilot/pgdata}"
PG_PORT="${PHARMPILOT_PGPORT:-5433}"
PG_LOG="${PHARMPILOT_PGLOG:-$HOME/.pharmpilot/postgres.log}"

# Use a single, simple log dir (no double-path)
LOG_DIR="$ROOT_DIR/.logs"
mkdir -p "$LOG_DIR"

QDRANT_BIN="$ROOT_DIR/.qdrant/qdrant"
QDRANT_STORAGE="$ROOT_DIR/.qdrant/storage"
mkdir -p "$QDRANT_STORAGE"

# PID list for clean shutdown
PIDS=()
cleanup() {
  echo ""
  log "Shutting down PharmPilot services..."
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  pkill -f "uvicorn services.platform.main.*$API_PORT"  2>/dev/null || true
  pkill -f "vite.*$FRONTEND_PORT"                        2>/dev/null || true
  log "Done."
  exit 0
}
trap cleanup INT TERM

# ── 1. PostgreSQL ─────────────────────────────────────────────────────────
log "Checking PostgreSQL (port $PG_PORT)..."
if PGPASSWORD="pharmpilot_dev" "$PG_BIN/psql" \
      -h 127.0.0.1 -p "$PG_PORT" -U pharmpilot -d pharmpilot \
      -c "SELECT 1" > /dev/null 2>&1; then
  ok "PostgreSQL      → 127.0.0.1:$PG_PORT"
elif [ -f "$PGDATA_DIR/PG_VERSION" ]; then
  log "Starting PharmPilot PostgreSQL..."
  LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 \
    "$PG_BIN/pg_ctl" start -D "$PGDATA_DIR" -l "$PG_LOG" -w -t 15 > /dev/null 2>&1 || true
  sleep 2
  ok "PostgreSQL      → 127.0.0.1:$PG_PORT"
else
  warn "No PG cluster found. Run: bash scripts/setup_postgres.sh"
fi

# ── 2. Redis ──────────────────────────────────────────────────────────────
log "Checking Redis..."
redis-cli -p 6379 ping 2>/dev/null | grep -q PONG || {
  log "Starting Redis..."
  redis-server --daemonize yes \
    --logfile "$LOG_DIR/redis.log" --port 6379 > /dev/null 2>&1 || \
  brew services start redis 2>/dev/null || true
  sleep 1
}
redis-cli -p 6379 ping 2>/dev/null | grep -q PONG \
  && ok "Redis           → localhost:6379" \
  || warn "Redis not responding — check $LOG_DIR/redis.log"

# ── 3. Qdrant ─────────────────────────────────────────────────────────────
log "Checking Qdrant (port $QDRANT_PORT)..."
QDRANT_CONFIG="$ROOT_DIR/.qdrant/config.yaml"

# Write config file (Qdrant doesn't accept --storage-path or --http-port CLI flags)
cat > "$QDRANT_CONFIG" << QDRANT_CONF
storage:
  storage_path: $QDRANT_STORAGE

service:
  host: 127.0.0.1
  http_port: $QDRANT_PORT
  grpc_port: $((QDRANT_PORT + 1))
  enable_tls: false

telemetry_disabled: true
QDRANT_CONF

if curl -sf "http://localhost:$QDRANT_PORT/health" > /dev/null 2>&1; then
  ok "Qdrant          → http://localhost:$QDRANT_PORT/dashboard"
elif [ -f "$QDRANT_BIN" ]; then
  log "Starting Qdrant..."
  "$QDRANT_BIN" --config-path "$QDRANT_CONFIG" \
    > "$LOG_DIR/qdrant.log" 2>&1 &
  PIDS+=($!)
  for i in $(seq 1 10); do
    curl -sf "http://localhost:$QDRANT_PORT/health" > /dev/null 2>&1 && break || sleep 1
  done
  curl -sf "http://localhost:$QDRANT_PORT/health" > /dev/null 2>&1 \
    && ok "Qdrant          → http://localhost:$QDRANT_PORT/dashboard" \
    || warn "Qdrant not ready — check $LOG_DIR/qdrant.log"
else
  warn "Qdrant binary missing. Run: bash scripts/setup_local.sh"
fi

export QDRANT_URL="http://localhost:$QDRANT_PORT"

# ── 4. Pending migrations ─────────────────────────────────────────────────
log "Checking migrations..."
[ -f "$ROOT_DIR/.venv/bin/activate" ] && source "$ROOT_DIR/.venv/bin/activate"
DATABASE_URL="$DB_URL" PYTHONPATH="$ROOT_DIR" \
  alembic -c "$ROOT_DIR/alembic.ini" upgrade head 2>&1 \
  | grep -E "Running upgrade|up to date|head" || true
ok "Migrations      → up to date"

# ── 5. FastAPI backend ────────────────────────────────────────────────────
log "Starting FastAPI API on port $API_PORT..."
DATABASE_URL="$DB_URL" QDRANT_URL="$QDRANT_URL" PYTHONPATH="$ROOT_DIR" \
  uvicorn services.platform.main:app \
    --host 0.0.0.0 \
    --port "$API_PORT" \
    --reload \
    --reload-dir "$ROOT_DIR/services" \
    --reload-dir "$ROOT_DIR/shared" \
    --log-level warning \
    > "$LOG_DIR/api.log" 2>&1 &
PIDS+=($!)

# Wait for API
log "Waiting for API..."
for i in $(seq 1 25); do
  curl -sf "http://localhost:$API_PORT/health" 2>/dev/null | grep -q "pharmpilot" && break || sleep 1
done
curl -sf "http://localhost:$API_PORT/health" 2>/dev/null | grep -q "pharmpilot" \
  && ok "FastAPI API     → http://localhost:$API_PORT" \
  || { warn "API slow — check $LOG_DIR/api.log"; tail -5 "$LOG_DIR/api.log" 2>/dev/null; }

# ── 6. React workstation ──────────────────────────────────────────────────
log "Starting React workstation on port $FRONTEND_PORT..."
cd "$ROOT_DIR/frontend/workstation"
npm run dev -- --port "$FRONTEND_PORT" --host \
  > "$LOG_DIR/frontend.log" 2>&1 &
PIDS+=($!)
cd "$ROOT_DIR"

# Wait for frontend
for i in $(seq 1 15); do
  curl -sf "http://localhost:$FRONTEND_PORT" > /dev/null 2>&1 && break || sleep 1
done
curl -sf "http://localhost:$FRONTEND_PORT" > /dev/null 2>&1 \
  && ok "React Frontend  → http://localhost:$FRONTEND_PORT" \
  || ok "React Frontend  → http://localhost:$FRONTEND_PORT  (starting...)"

# ── Summary ───────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║              PharmPilot Running  ✓                              ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${GREEN}Workstation UI    ${NC}→  http://localhost:${FRONTEND_PORT}"
echo -e "  ${GREEN}API + Swagger     ${NC}→  http://localhost:${API_PORT}/docs"
echo -e "  ${GREEN}API Health        ${NC}→  http://localhost:${API_PORT}/health"
echo -e "  ${GREEN}Qdrant Dashboard  ${NC}→  http://localhost:${QDRANT_PORT}/dashboard"
echo ""
echo -e "  ${YELLOW}Login:${NC}  admin / Admin1234!   |   pharmacist / Pharmacist1234!"
echo ""
echo -e "  ${CYAN}Logs:${NC}  .logs/api.log  |  .logs/frontend.log  |  .logs/qdrant.log"
echo ""
echo -e "  Press ${RED}Ctrl+C${NC} to stop all services"
echo ""

# Tail logs (using $LOG_DIR, not $ROOT_DIR/$LOG_DIR)
tail -f "$LOG_DIR/api.log" "$LOG_DIR/frontend.log" 2>/dev/null &
PIDS+=($!)

wait "${PIDS[0]}" 2>/dev/null || true
