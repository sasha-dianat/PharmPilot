#!/usr/bin/env bash
# =============================================================================
#  PharmPilot — PostgreSQL Setup (handles multiple PG installations on macOS)
#  Scenario A: Homebrew PostgreSQL (typical dev setup)
#  Scenario B: EDB PostgreSQL (installer-based, runs as postgres user)
#  Scenario C: Both installed (use Homebrew on port 5433 or configure EDB)
# =============================================================================
set -euo pipefail
CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${CYAN}[pg]${NC}   $1"; }
ok()   { echo -e "${GREEN}[ ok]${NC}  $1"; }
warn() { echo -e "${YELLOW}[warn]${NC} $1"; }
err()  { echo -e "${RED}[err]${NC}  $1"; exit 1; }

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PGDATA_BREW="/usr/local/var/postgresql@16"
PG_BREW_BIN="/usr/local/opt/postgresql@16/bin"
EDB_BIN="/Library/PostgreSQL/16/bin"

# ── Detect situation ──────────────────────────────────────────────────────
EDB_RUNNING=false
BREW_RUNNING=false

if pgrep -x postgres >/dev/null 2>&1; then
  PG_PROC=$(ps aux | grep "postgres -D" | grep -v grep | awk '{print $NF}' | head -1)
  if [[ "$PG_PROC" == *"Library/PostgreSQL"* ]]; then
    EDB_RUNNING=true
    log "EDB PostgreSQL detected running on port 5432"
  elif [[ "$PG_PROC" == *"homebrew"* ]] || [[ "$PG_PROC" == *"/usr/local/var"* ]]; then
    BREW_RUNNING=true
    log "Homebrew PostgreSQL detected"
  fi
fi

# ── Strategy: Start Homebrew PG on port 5433 if EDB occupies 5432 ─────────
PG_PORT=5432
PG_URL_HOST="127.0.0.1"

if $EDB_RUNNING; then
  warn "EDB PostgreSQL occupies port 5432."
  warn "Two options:"
  echo ""
  echo "  OPTION A (Recommended): Enter your EDB postgres password to create the pharmpilot DB"
  echo "    The password was set during EDB installer. Check pgAdmin 4 for the password."
  echo ""
  echo "  OPTION B: Use Homebrew PostgreSQL on port 5433 alongside EDB"
  echo ""
  read -p "  Choose [A/B]: " CHOICE
  CHOICE="${CHOICE:-A}"

  if [[ "${CHOICE^^}" == "A" ]]; then
    # Use EDB — ask for password
    echo ""
    read -s -p "  Enter postgres superuser password: " PGPASSWORD
    echo ""
    export PGPASSWORD
    PG_CMD="$EDB_BIN/psql -h 127.0.0.1 -U postgres"

    # Test connection
    $PG_CMD -c "SELECT 'ok'" postgres >/dev/null 2>&1 || err "Wrong password. Try pgAdmin 4 to verify your postgres password."
    ok "Connected to EDB PostgreSQL"

    # Create user and db
    $PG_CMD -tc "SELECT 1 FROM pg_roles WHERE rolname='pharmpilot'" postgres | grep -q 1 || \
      $PG_CMD -c "CREATE USER pharmpilot WITH PASSWORD 'pharmpilot_dev' CREATEDB;" postgres
    $PG_CMD -tc "SELECT 1 FROM pg_database WHERE datname='pharmpilot'" postgres | grep -q 1 || \
      $PG_CMD -c "CREATE DATABASE pharmpilot OWNER pharmpilot;" postgres
    PGPASSWORD="pharmpilot_dev" $EDB_BIN/psql -h 127.0.0.1 -U pharmpilot -d pharmpilot \
      -c "CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"; CREATE EXTENSION IF NOT EXISTS pg_trgm;" >/dev/null 2>&1
    ok "pharmpilot DB ready on EDB PostgreSQL"
    PG_PORT=5432
    PG_URL_HOST="127.0.0.1"

  else
    # Option B: Homebrew on 5433
    PG_PORT=5433
    log "Setting up Homebrew PostgreSQL on port 5433..."

    # Initialise if needed
    if [[ ! -d "$PGDATA_BREW" ]]; then
      "$PG_BREW_BIN/initdb" --locale=en_US.UTF-8 --encoding=UTF8 -D "$PGDATA_BREW"
    fi

    # Modify port in postgresql.conf
    sed -i '' "s/#port = 5432/port = 5433/" "$PGDATA_BREW/postgresql.conf" 2>/dev/null || true
    sed -i '' "s/port = 5432/port = 5433/" "$PGDATA_BREW/postgresql.conf" 2>/dev/null || true

    # Start on port 5433
    "$PG_BREW_BIN/pg_ctl" status -D "$PGDATA_BREW" >/dev/null 2>&1 || \
      "$PG_BREW_BIN/pg_ctl" start -D "$PGDATA_BREW" -l "$PGDATA_BREW/server.log" -w
    sleep 2

    # Create user and db
    PG_CMD="$PG_BREW_BIN/psql -h 127.0.0.1 -p 5433 -U $(whoami)"
    $PG_CMD -tc "SELECT 1 FROM pg_roles WHERE rolname='pharmpilot'" postgres | grep -q 1 || \
      $PG_CMD -c "CREATE USER pharmpilot WITH PASSWORD 'pharmpilot_dev' CREATEDB;" postgres
    $PG_CMD -tc "SELECT 1 FROM pg_database WHERE datname='pharmpilot'" postgres | grep -q 1 || \
      $PG_CMD -c "CREATE DATABASE pharmpilot OWNER pharmpilot;" postgres
    PGPASSWORD="pharmpilot_dev" "$PG_BREW_BIN/psql" -h 127.0.0.1 -p 5433 -U pharmpilot -d pharmpilot \
      -c "CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"; CREATE EXTENSION IF NOT EXISTS pg_trgm;" >/dev/null 2>&1
    ok "pharmpilot DB ready on Homebrew PostgreSQL port 5433"
    PG_URL_HOST="127.0.0.1"
  fi

else
  # Only Homebrew — standard setup
  log "Setting up Homebrew PostgreSQL on port 5432..."

  if [[ ! -d "$PGDATA_BREW" ]]; then
    "$PG_BREW_BIN/initdb" --locale=en_US.UTF-8 --encoding=UTF8 -D "$PGDATA_BREW"
  fi

  "$PG_BREW_BIN/pg_ctl" status -D "$PGDATA_BREW" >/dev/null 2>&1 || {
    "$PG_BREW_BIN/pg_ctl" start -D "$PGDATA_BREW" -l "$PGDATA_BREW/server.log" -w
    sleep 2
  }

  PG_CMD="$PG_BREW_BIN/psql -U $(whoami)"
  $PG_CMD -tc "SELECT 1 FROM pg_roles WHERE rolname='pharmpilot'" postgres | grep -q 1 || \
    $PG_CMD -c "CREATE USER pharmpilot WITH PASSWORD 'pharmpilot_dev' CREATEDB;" postgres
  $PG_CMD -tc "SELECT 1 FROM pg_database WHERE datname='pharmpilot'" postgres | grep -q 1 || \
    $PG_CMD -c "CREATE DATABASE pharmpilot OWNER pharmpilot;" postgres
  "$PG_BREW_BIN/psql" -U pharmpilot -d pharmpilot \
    -c "CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"; CREATE EXTENSION IF NOT EXISTS pg_trgm;" >/dev/null 2>&1
  ok "pharmpilot DB ready"
fi

# ── Write DATABASE_URL to .env ─────────────────────────────────────────────
DB_URL="postgresql+asyncpg://pharmpilot:pharmpilot_dev@${PG_URL_HOST}:${PG_PORT}/pharmpilot"
ENV_FILE="$ROOT_DIR/.env"

if [[ -f "$ENV_FILE" ]]; then
  if grep -q "^DATABASE_URL" "$ENV_FILE"; then
    sed -i '' "s|^DATABASE_URL=.*|DATABASE_URL=${DB_URL}|" "$ENV_FILE"
  else
    echo "DATABASE_URL=${DB_URL}" >> "$ENV_FILE"
  fi
fi

ok "DATABASE_URL set to: $DB_URL"
echo ""
echo -e "  ${GREEN}PostgreSQL ready.${NC} Run: bash scripts/dev.sh"
