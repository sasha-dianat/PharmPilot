#!/usr/bin/env bash
# =============================================================================
#  PharmPilot — Database Setup Script
#  Detects which PostgreSQL installation is running and configures it.
#  Supports: Homebrew PostgreSQL, EDB PostgreSQL, system PostgreSQL
# =============================================================================
set -euo pipefail
CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${CYAN}[db]${NC}   $1"; }
ok()   { echo -e "${GREEN}[ ok]${NC}  $1"; }
warn() { echo -e "${YELLOW}[warn]${NC} $1"; }
err()  { echo -e "${RED}[err]${NC}  $1"; exit 1; }

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# ── Detect running PostgreSQL ──────────────────────────────────────────────
PG_HOST="localhost"
PG_PORT="5432"
PG_SUPERUSER=""
PG_CONNECT_METHOD=""

log "Detecting PostgreSQL installation..."

# Method 1: Homebrew peer auth (most common on macOS)
if psql -U "$(whoami)" -c "SELECT 1" postgres >/dev/null 2>&1; then
  PG_SUPERUSER="$(whoami)"
  PG_CONNECT_METHOD="peer"
  ok "Found PostgreSQL via peer auth (Homebrew) — user=$(whoami)"

# Method 2: Ask for postgres password
elif [[ -n "${PGPASSWORD:-}" ]] && psql -h localhost -U postgres -c "SELECT 1" postgres >/dev/null 2>&1; then
  PG_SUPERUSER="postgres"
  PG_CONNECT_METHOD="password"
  ok "Found PostgreSQL via PGPASSWORD — user=postgres"

# Method 3: Try common default passwords
else
  log "Trying EDB/system PostgreSQL..."
  for PASS in "postgres" "pharmpilot" "password" "admin" ""; do
    if PGPASSWORD="$PASS" psql -h 127.0.0.1 -U postgres -c "SELECT 1" postgres >/dev/null 2>&1; then
      export PGPASSWORD="$PASS"
      PG_SUPERUSER="postgres"
      PG_CONNECT_METHOD="password"
      ok "Found PostgreSQL (password='$PASS') — user=postgres"
      break
    fi
  done
fi

if [[ -z "$PG_SUPERUSER" ]]; then
  echo ""
  warn "Could not auto-connect to PostgreSQL."
  echo -e "  Please enter your PostgreSQL superuser credentials:"
  read -p "  PostgreSQL username [postgres]: " PG_SUPERUSER
  PG_SUPERUSER="${PG_SUPERUSER:-postgres}"
  read -s -p "  PostgreSQL password (blank for peer auth): " PGPASSWORD
  echo ""
  export PGPASSWORD
  PG_CONNECT_METHOD="password"
fi

# Helper function to run psql
run_psql() {
  if [[ "$PG_CONNECT_METHOD" == "peer" ]]; then
    psql -U "$PG_SUPERUSER" "$@"
  else
    PGPASSWORD="${PGPASSWORD:-}" psql -h 127.0.0.1 -U "$PG_SUPERUSER" "$@"
  fi
}

# ── Create pharmpilot user and database ────────────────────────────────────
log "Creating pharmpilot database user..."
run_psql -tc "SELECT 1 FROM pg_roles WHERE rolname='pharmpilot'" postgres \
  | grep -q 1 || {
  run_psql -c "CREATE USER pharmpilot WITH PASSWORD 'pharmpilot_dev' CREATEDB;" postgres
  ok "User 'pharmpilot' created"
}
ok "User 'pharmpilot' exists"

log "Creating pharmpilot database..."
run_psql -tc "SELECT 1 FROM pg_database WHERE datname='pharmpilot'" postgres \
  | grep -q 1 || {
  run_psql -c "CREATE DATABASE pharmpilot OWNER pharmpilot;" postgres
  ok "Database 'pharmpilot' created"
}
ok "Database 'pharmpilot' exists"

log "Installing PostgreSQL extensions..."
PGPASSWORD="pharmpilot_dev" psql -h 127.0.0.1 -U pharmpilot -d pharmpilot \
  -c "CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"; CREATE EXTENSION IF NOT EXISTS pg_trgm;" \
  >/dev/null 2>&1 || \
run_psql -d pharmpilot \
  -c "CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"; CREATE EXTENSION IF NOT EXISTS pg_trgm;" \
  >/dev/null 2>&1
ok "Extensions uuid-ossp, pg_trgm installed"

# ── Write correct DATABASE_URL to .env ────────────────────────────────────
ENV_FILE="$ROOT_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
  # Replace or add DATABASE_URL
  if grep -q "^DATABASE_URL" "$ENV_FILE"; then
    sed -i '' "s|^DATABASE_URL=.*|DATABASE_URL=postgresql+asyncpg://pharmpilot:pharmpilot_dev@127.0.0.1:5432/pharmpilot|" "$ENV_FILE"
  else
    echo "DATABASE_URL=postgresql+asyncpg://pharmpilot:pharmpilot_dev@127.0.0.1:5432/pharmpilot" >> "$ENV_FILE"
  fi
  ok ".env DATABASE_URL updated"
fi

ok "Database setup complete"
echo ""
echo -e "  ${CYAN}Connection:${NC} postgresql://pharmpilot:pharmpilot_dev@127.0.0.1:5432/pharmpilot"
