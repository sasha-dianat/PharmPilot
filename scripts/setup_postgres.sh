#!/bin/sh
# =============================================================================
#  PharmPilot — PostgreSQL Setup
#  POSIX sh — works on macOS bash 3.2, zsh, sh. No bash 4+ features.
#
#  Strategy: creates a dedicated PharmPilot PostgreSQL cluster at
#  ~/.pharmpilot/pgdata on port 5433. Runs as your user — no passwords,
#  no sudo, no conflict with any existing PostgreSQL installation.
# =============================================================================
GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { printf "${CYAN}[pg]${NC}   %s\n" "$1"; }
ok()   { printf "${GREEN}[ ok]${NC}  %s\n" "$1"; }
warn() { printf "${YELLOW}[warn]${NC} %s\n" "$1"; }
err()  { printf "${RED}[err]${NC}  %s\n" "$1"; exit 1; }

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# ── Find initdb / pg_ctl / psql ────────────────────────────────────────────
find_pg_bin() {
  for dir in \
    "/usr/local/opt/postgresql@16/bin" \
    "/opt/homebrew/opt/postgresql@16/bin" \
    "/Library/PostgreSQL/16/bin" \
    "/usr/lib/postgresql/16/bin" \
    "/usr/local/bin"; do
    if [ -f "$dir/initdb" ]; then
      printf "%s" "$dir"
      return 0
    fi
  done
  err "Cannot find PostgreSQL binaries. Run: brew install postgresql@16"
}

PG_BIN=$(find_pg_bin)
log "PostgreSQL binaries: $PG_BIN"

# Dedicated cluster — no conflict with EDB or any other PG
PHARMPILOT_PGDATA="$HOME/.pharmpilot/pgdata"
PHARMPILOT_LOG="$HOME/.pharmpilot/postgres.log"
PG_PORT=5433
PG_SOCK_DIR="$HOME/.pharmpilot"
PG_USER="$(whoami)"
PG_APP_USER="pharmpilot"
PG_APP_PASS="pharmpilot_dev"
PG_DB="pharmpilot"

mkdir -p "$HOME/.pharmpilot"

# ── Stop any stale PharmPilot PG instance ─────────────────────────────────
if [ -f "$PHARMPILOT_PGDATA/postmaster.pid" ]; then
  log "Stopping stale PharmPilot PostgreSQL instance..."
  LC_ALL=en_US.UTF-8 "$PG_BIN/pg_ctl" stop -D "$PHARMPILOT_PGDATA" -m fast > /dev/null 2>&1 || true
  sleep 1
  rm -f "$PHARMPILOT_PGDATA/postmaster.pid"
fi

# ── Initialise cluster if not yet done ────────────────────────────────────
if [ ! -f "$PHARMPILOT_PGDATA/PG_VERSION" ]; then
  log "Initialising dedicated PharmPilot PostgreSQL cluster..."
  log "  Data dir: $PHARMPILOT_PGDATA"

  LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 \
    "$PG_BIN/initdb" \
    --pgdata="$PHARMPILOT_PGDATA" \
    --username="$PG_USER" \
    --encoding=UTF8 \
    --locale=en_US.UTF-8 \
    --auth=trust \
    > /dev/null 2>&1 \
    || err "initdb failed. Check: $PG_BIN/initdb --version"

  # Configure: port 5433, listen on 127.0.0.1 only, Unix socket in home dir
  PGCONF="$PHARMPILOT_PGDATA/postgresql.conf"
  printf "\nport = %s\n"                     "$PG_PORT"    >> "$PGCONF"
  printf "listen_addresses = '127.0.0.1'\n"               >> "$PGCONF"
  printf "unix_socket_directories = '%s'\n"  "$PG_SOCK_DIR" >> "$PGCONF"
  printf "log_destination = 'stderr'\n"                   >> "$PGCONF"
  printf "logging_collector = off\n"                      >> "$PGCONF"

  # Allow all local connections without password (trust auth)
  printf "# PharmPilot local auth\n"                       > "$PHARMPILOT_PGDATA/pg_hba.conf"
  printf "local all all trust\n"                          >> "$PHARMPILOT_PGDATA/pg_hba.conf"
  printf "host  all all 127.0.0.1/32 trust\n"            >> "$PHARMPILOT_PGDATA/pg_hba.conf"
  printf "host  all all ::1/128      trust\n"             >> "$PHARMPILOT_PGDATA/pg_hba.conf"

  ok "Cluster initialised"
else
  ok "Cluster already initialised at $PHARMPILOT_PGDATA"
fi

# ── Start the cluster (skip if already running) ────────────────────────────
if "$PG_BIN/psql" -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -c "SELECT 1" postgres > /dev/null 2>&1; then
  ok "PostgreSQL already running on port $PG_PORT"
else
  log "Starting PharmPilot PostgreSQL on port $PG_PORT..."
  LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 \
    "$PG_BIN/pg_ctl" start \
    -D "$PHARMPILOT_PGDATA" \
    -l "$PHARMPILOT_LOG" \
    -w \
    -t 15 \
    > /dev/null 2>&1 || {
      # pg_ctl might return non-zero even when it started OK on some macOS versions.
      # Verify by actually connecting before declaring failure.
      sleep 2
      "$PG_BIN/psql" -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -c "SELECT 1" postgres > /dev/null 2>&1 || {
        printf "\nPostgreSQL failed to start. Last log lines:\n"
        tail -10 "$PHARMPILOT_LOG" 2>/dev/null || true
        err "pg_ctl start failed. Try: rm -f $PHARMPILOT_PGDATA/postmaster.pid and re-run."
      }
    }

  # Final connection verification
  "$PG_BIN/psql" -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -c "SELECT 1" postgres > /dev/null 2>&1 \
    || err "Cannot connect on port $PG_PORT after start"

  ok "PostgreSQL running on port $PG_PORT"
fi

# ── Create app user and database ──────────────────────────────────────────
log "Creating app user '$PG_APP_USER'..."
"$PG_BIN/psql" -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -d postgres \
  -tc "SELECT 1 FROM pg_roles WHERE rolname='$PG_APP_USER'" \
  | grep -q 1 || \
  "$PG_BIN/psql" -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -d postgres \
    -c "CREATE USER $PG_APP_USER WITH PASSWORD '$PG_APP_PASS' CREATEDB;" \
    > /dev/null
ok "User '$PG_APP_USER' ready"

log "Creating database '$PG_DB'..."
"$PG_BIN/psql" -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -d postgres \
  -tc "SELECT 1 FROM pg_database WHERE datname='$PG_DB'" \
  | grep -q 1 || \
  "$PG_BIN/psql" -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -d postgres \
    -c "CREATE DATABASE $PG_DB OWNER $PG_APP_USER;" \
    > /dev/null
ok "Database '$PG_DB' ready"

log "Installing extensions..."
"$PG_BIN/psql" -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" \
  -c "CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"; CREATE EXTENSION IF NOT EXISTS pg_trgm;" \
  > /dev/null 2>&1
ok "Extensions uuid-ossp, pg_trgm installed"

# ── Write .env ────────────────────────────────────────────────────────────
DB_URL="postgresql+asyncpg://${PG_APP_USER}:${PG_APP_PASS}@127.0.0.1:${PG_PORT}/${PG_DB}"
ENV_FILE="$ROOT_DIR/.env"

if [ ! -f "$ENV_FILE" ]; then
  cp "$ROOT_DIR/.env.example" "$ENV_FILE" 2>/dev/null || printf "" > "$ENV_FILE"
fi

if grep -q "^DATABASE_URL" "$ENV_FILE"; then
  sed -i.bak "s|^DATABASE_URL=.*|DATABASE_URL=${DB_URL}|" "$ENV_FILE"
else
  printf "\nDATABASE_URL=%s\n" "$DB_URL" >> "$ENV_FILE"
fi

# Also write the pg_bin path so dev.sh can use it
if grep -q "^PHARMPILOT_PG_BIN" "$ENV_FILE"; then
  sed -i.bak "s|^PHARMPILOT_PG_BIN=.*|PHARMPILOT_PG_BIN=${PG_BIN}|" "$ENV_FILE"
else
  printf "PHARMPILOT_PG_BIN=%s\n"   "$PG_BIN"           >> "$ENV_FILE"
  printf "PHARMPILOT_PGDATA=%s\n"   "$PHARMPILOT_PGDATA" >> "$ENV_FILE"
  printf "PHARMPILOT_PGPORT=%s\n"   "$PG_PORT"           >> "$ENV_FILE"
  printf "PHARMPILOT_PGLOG=%s\n"    "$PHARMPILOT_LOG"    >> "$ENV_FILE"
fi

ok ".env updated"

printf "\n"
printf "${GREEN}╔════════════════════════════════════════════════════╗${NC}\n"
printf "${GREEN}║  PostgreSQL setup complete!                        ║${NC}\n"
printf "${GREEN}╚════════════════════════════════════════════════════╝${NC}\n"
printf "\n"
printf "  Cluster:     %s\n"  "$PHARMPILOT_PGDATA"
printf "  Port:        %s\n"  "$PG_PORT"
printf "  Database URL: %s\n" "$DB_URL"
printf "\n"
printf "  ${CYAN}Next step:${NC} bash scripts/setup_local.sh\n\n"
