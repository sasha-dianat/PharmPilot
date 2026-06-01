#!/usr/bin/env bash
# =============================================================================
#  PharmPilot AI — Local Development Setup
#  macOS (Homebrew) | Python 3.11+ | Node 18+
#  Run once: bash scripts/setup_local.sh
#  Then every time: bash scripts/dev.sh
# =============================================================================
set -euo pipefail
CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${CYAN}[setup]${NC} $1"; }
ok()   { echo -e "${GREEN}[  ok ]${NC} $1"; }
warn() { echo -e "${YELLOW}[ warn]${NC} $1"; }
err()  { echo -e "${RED}[error]${NC} $1"; exit 1; }

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
log "PharmPilot root: $ROOT_DIR"

# ── 1. Prerequisites check ────────────────────────────────────────────────
log "Checking prerequisites..."

command -v brew  >/dev/null 2>&1 || err "Homebrew not found. Install: /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
command -v python3 >/dev/null 2>&1 || err "Python3 not found."
command -v node  >/dev/null 2>&1 || err "Node.js not found. Install: brew install node"
command -v npm   >/dev/null 2>&1 || err "npm not found."

PYTHON_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
NODE_VER=$(node --version | sed 's/v//' | cut -d. -f1)

[[ $(echo "$PYTHON_VER >= 3.11" | bc -l) -eq 1 ]] || err "Python 3.11+ required (found $PYTHON_VER)"
[[ $NODE_VER -ge 18 ]] || err "Node 18+ required (found $NODE_VER)"
ok "Python $PYTHON_VER, Node $(node --version)"

# ── 2. Homebrew services: PostgreSQL 16 + Redis ───────────────────────────
log "Setting up PostgreSQL 16..."

brew list postgresql@16 >/dev/null 2>&1 || { log "Installing postgresql@16..."; brew install postgresql@16; }

# Initialise data directory if not done yet
PGDATA="/usr/local/var/postgresql@16"
if [[ ! -d "$PGDATA" ]]; then
  log "Initialising PostgreSQL data directory at $PGDATA..."
  /usr/local/opt/postgresql@16/bin/initdb \
    --locale=en_US.UTF-8 \
    --encoding=UTF8 \
    -D "$PGDATA"
  ok "PostgreSQL initialised"
else
  ok "PostgreSQL data directory already exists"
fi

# Start PostgreSQL
/usr/local/opt/postgresql@16/bin/pg_ctl status -D "$PGDATA" >/dev/null 2>&1 || {
  log "Starting PostgreSQL..."
  /usr/local/opt/postgresql@16/bin/pg_ctl start -D "$PGDATA" -l "$PGDATA/server.log"
  sleep 2
}
ok "PostgreSQL running"

# Create DB user and database
/usr/local/opt/postgresql@16/bin/psql -U "$(whoami)" -c "SELECT 1" postgres >/dev/null 2>&1 || true
/usr/local/opt/postgresql@16/bin/psql -U "$(whoami)" -tc "SELECT 1 FROM pg_roles WHERE rolname='pharmpilot'" postgres \
  | grep -q 1 || {
  log "Creating pharmpilot DB user..."
  /usr/local/opt/postgresql@16/bin/psql -U "$(whoami)" -c \
    "CREATE USER pharmpilot WITH PASSWORD 'pharmpilot_dev' CREATEDB;" postgres
}

/usr/local/opt/postgresql@16/bin/psql -U "$(whoami)" -tc "SELECT 1 FROM pg_database WHERE datname='pharmpilot'" postgres \
  | grep -q 1 || {
  log "Creating pharmpilot database..."
  /usr/local/opt/postgresql@16/bin/psql -U "$(whoami)" -c \
    "CREATE DATABASE pharmpilot OWNER pharmpilot;" postgres
}

# Install pg_trgm extension
/usr/local/opt/postgresql@16/bin/psql -U pharmpilot -c \
  "CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"; CREATE EXTENSION IF NOT EXISTS pg_trgm;" \
  pharmpilot >/dev/null 2>&1 && ok "PostgreSQL extensions installed"

log "Setting up Redis..."
brew list redis >/dev/null 2>&1 || { log "Installing redis..."; brew install redis; }
brew services list | grep redis | grep started >/dev/null 2>&1 || {
  log "Starting Redis..."
  brew services start redis
  sleep 1
}
redis-cli ping 2>/dev/null | grep -q PONG && ok "Redis running" || warn "Redis may still be starting"

# ── 3. Qdrant (vector database) ───────────────────────────────────────────
log "Setting up Qdrant vector database..."
QDRANT_DIR="$ROOT_DIR/.qdrant"
QDRANT_BIN="$QDRANT_DIR/qdrant"
mkdir -p "$QDRANT_DIR/storage"

if [[ ! -f "$QDRANT_BIN" ]]; then
  log "Downloading Qdrant..."
  ARCH=$(uname -m)
  if [[ "$ARCH" == "arm64" ]]; then
    QDRANT_URL="https://github.com/qdrant/qdrant/releases/download/v1.9.7/qdrant-aarch64-apple-darwin.tar.gz"
  else
    QDRANT_URL="https://github.com/qdrant/qdrant/releases/download/v1.9.7/qdrant-x86_64-apple-darwin.tar.gz"
  fi
  curl -L "$QDRANT_URL" -o "$QDRANT_DIR/qdrant.tar.gz"
  tar -xzf "$QDRANT_DIR/qdrant.tar.gz" -C "$QDRANT_DIR"
  chmod +x "$QDRANT_BIN"
  rm "$QDRANT_DIR/qdrant.tar.gz"
  ok "Qdrant downloaded"
else
  ok "Qdrant already downloaded"
fi

# ── 4. Python virtual environment ─────────────────────────────────────────
log "Setting up Python virtual environment..."
if [[ ! -d "$ROOT_DIR/.venv" ]]; then
  python3 -m venv "$ROOT_DIR/.venv"
  ok "Virtual environment created at .venv/"
else
  ok "Virtual environment already exists"
fi

source "$ROOT_DIR/.venv/bin/activate"
pip install --upgrade pip setuptools wheel -q

log "Installing Python dependencies (this may take 2-3 minutes)..."
pip install \
  "fastapi==0.115.6" \
  "pydantic[email]" \
  "email-validator" \
  "uvicorn[standard]==0.34.0" \
  "pydantic==2.10.4" \
  "pydantic-settings==2.7.1" \
  "sqlalchemy[asyncio]==2.0.38" \
  "alembic==1.14.0" \
  "asyncpg==0.31.0" \
  "redis==5.2.1" \
  "psycopg2-binary" \
  "python-multipart" \
  "python-dotenv" \
  "httpx" \
  "tenacity" \
  "python-jose[cryptography]" \
  "passlib[bcrypt]" \
  "bcrypt==4.0.1" \
  "pyotp" \
  "anthropic" \
  "qdrant-client" \
  "sentence-transformers" \
  "cryptography" \
  "structlog" \
  "prometheus-client" \
  "opentelemetry-api" \
  "opentelemetry-sdk" \
  "arrow" \
  "prometheus-client" \
  "structlog" \
  "opentelemetry-api" \
  "opentelemetry-sdk" \
  "numpy" \
  "scikit-learn" \
  "pandas" \
  "scipy" \
  "beautifulsoup4" \
  "PyPDF2" \
  "spacy" \
  "click" \
  -q

ok "Core Python packages installed"

# Download spaCy model (small, fast — used for NER in audio extraction)
python3 -m spacy download en_core_web_sm -q 2>/dev/null || true
ok "spaCy model ready"

# ── 5. Environment file ───────────────────────────────────────────────────
log "Setting up environment configuration..."
if [[ ! -f "$ROOT_DIR/.env" ]]; then
  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
  # Auto-generate secrets
  SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  VAULT_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  VAULT_HMAC=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  sed -i '' "s|CHANGE_IN_PRODUCTION_USE_SECRETS_MANAGER|$SECRET_KEY|" "$ROOT_DIR/.env"
  sed -i '' "s|generate_with_openssl_rand_hex_32|$VAULT_KEY|" "$ROOT_DIR/.env"
  echo "" >> "$ROOT_DIR/.env"
  echo "VAULT_MASTER_KEY_HEX=$VAULT_KEY" >> "$ROOT_DIR/.env"
  echo "VAULT_HMAC_SECRET_HEX=$VAULT_HMAC" >> "$ROOT_DIR/.env"
  echo "DATABASE_URL=postgresql+asyncpg://pharmpilot:pharmpilot_dev@localhost:5432/pharmpilot" >> "$ROOT_DIR/.env"
  echo "REDIS_URL=redis://localhost:6379/0" >> "$ROOT_DIR/.env"
  echo "QDRANT_URL=http://localhost:6333" >> "$ROOT_DIR/.env"
  echo "ENVIRONMENT=development" >> "$ROOT_DIR/.env"
  ok ".env created with auto-generated secrets"
  warn "⚠  Add your ANTHROPIC_API_KEY to .env to enable the AI Clinical Brain"
  warn "   Edit: $ROOT_DIR/.env"
else
  ok ".env already exists — skipping"
fi

# ── 6. Database migrations ────────────────────────────────────────────────
log "Running database migrations..."
cd "$ROOT_DIR"
DATABASE_URL="postgresql+asyncpg://pharmpilot:pharmpilot_dev@localhost:5432/pharmpilot" \
  PYTHONPATH="$ROOT_DIR" \
  "$ROOT_DIR/.venv/bin/alembic" -c alembic.ini upgrade head 2>&1 || {
  warn "Migration failed — database may already be initialised, or check PostgreSQL is running"
}
ok "Database migrations applied"

# ── 7. Frontend dependencies ──────────────────────────────────────────────
log "Installing frontend dependencies..."
cd "$ROOT_DIR/frontend/workstation"
npm install --silent
ok "Frontend dependencies installed"

# ── 8. Create frontend .env.local ─────────────────────────────────────────
if [[ ! -f "$ROOT_DIR/frontend/workstation/.env.local" ]]; then
  cat > "$ROOT_DIR/frontend/workstation/.env.local" << 'EOF'
VITE_API_URL=http://localhost:8000/api/v1
VITE_WS_URL=ws://localhost:8000
EOF
  ok "Frontend .env.local created"
fi

# ── 9. Seed initial admin user ────────────────────────────────────────────
log "Seeding initial admin user..."
cd "$ROOT_DIR"
PYTHONPATH="$ROOT_DIR" python3 - << 'PYEOF'
import asyncio, sys, os
sys.path.insert(0, os.getcwd())

async def seed():
    try:
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
        from sqlalchemy import select, text
        from shared.models.auth import Staff, StaffRole
        from shared.models.pharmacy import Pharmacy
        from services.platform.auth import hash_password
        from uuid import uuid4

        engine = create_async_engine(
            "postgresql+asyncpg://pharmpilot:pharmpilot_dev@localhost:5432/pharmpilot",
            echo=False
        )
        Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with Session() as db:
            # Check if pharmacy exists
            result = await db.execute(select(Pharmacy).limit(1))
            pharmacy = result.scalar_one_or_none()

            if not pharmacy:
                pharmacy = Pharmacy(
                    name="PharmPilot Demo Pharmacy",
                    npi="1234567890",
                    ncpdp_id="1234567",
                    state="TX",
                    timezone="America/Chicago",
                )
                db.add(pharmacy)
                await db.flush()
                print(f"  Created demo pharmacy: {pharmacy.name} (id={str(pharmacy.id)[:8]}...)")

            # Check if admin exists
            result2 = await db.execute(select(Staff).where(Staff.username == "admin"))
            admin = result2.scalar_one_or_none()

            if not admin:
                admin = Staff(
                    pharmacy_id=pharmacy.id,
                    email="admin@pharmpilot.local",
                    username="admin",
                    hashed_password=hash_password("Admin1234!"),
                    first_name="Pharmacy",
                    last_name="Admin",
                    role=StaffRole.SUPER_ADMIN,
                )
                db.add(admin)

                pharmacist = Staff(
                    pharmacy_id=pharmacy.id,
                    email="pharmacist@pharmpilot.local",
                    username="pharmacist",
                    hashed_password=hash_password("Pharmacist1234!"),
                    first_name="Demo",
                    last_name="Pharmacist",
                    role=StaffRole.PHARMACIST,
                )
                db.add(pharmacist)

                tech = Staff(
                    pharmacy_id=pharmacy.id,
                    email="tech@pharmpilot.local",
                    username="tech",
                    hashed_password=hash_password("Tech1234!"),
                    first_name="Demo",
                    last_name="Technician",
                    role=StaffRole.PHARMACY_TECHNICIAN,
                )
                db.add(tech)
                await db.commit()
                print("  Seed users created")
            else:
                print("  Seed users already exist")

        await engine.dispose()
    except Exception as e:
        print(f"  Seed skipped (will retry on first run): {e}")

asyncio.run(seed())
PYEOF
ok "Seed complete"

# ── Done ─────────────────────────────────────────────────────────────────
cd "$ROOT_DIR"
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║        PharmPilot Local Setup Complete ✓                    ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${CYAN}Next step:${NC}  bash scripts/dev.sh"
echo ""
echo -e "  ${YELLOW}Default login credentials:${NC}"
echo -e "  ┌──────────────┬──────────────┬─────────────────┐"
echo -e "  │ Role         │ Username     │ Password        │"
echo -e "  ├──────────────┼──────────────┼─────────────────┤"
echo -e "  │ Admin        │ admin        │ Admin1234!      │"
echo -e "  │ Pharmacist   │ pharmacist   │ Pharmacist1234! │"
echo -e "  │ Technician   │ tech         │ Tech1234!       │"
echo -e "  └──────────────┴──────────────┴─────────────────┘"
echo ""
echo -e "  ${YELLOW}Optional (for full AI features):${NC}"
echo -e "  Edit .env and add:  ANTHROPIC_API_KEY=sk-ant-..."
echo ""
