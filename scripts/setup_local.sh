#!/usr/bin/env bash
# =============================================================================
#  PharmPilot AI — Full Local Development Setup
#  Run once:      bash scripts/setup_local.sh
#  Run every day: bash scripts/dev.sh
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

# ── 1. Prerequisites ──────────────────────────────────────────────────────
log "Checking prerequisites..."
command -v brew   >/dev/null 2>&1 || err "Homebrew missing. Install from https://brew.sh"
command -v python3 >/dev/null 2>&1 || err "Python 3 missing."
command -v node   >/dev/null 2>&1 || err "Node.js missing. Run: brew install node"
command -v npm    >/dev/null 2>&1 || err "npm missing."

PY_OK=$(python3 -c "import sys; print('ok' if sys.version_info >= (3,11) else 'old')")
[ "$PY_OK" = "ok" ] || err "Python 3.11+ required. Install: brew install python@3.12"

NODE_VER=$(node --version | sed 's/v//' | cut -d. -f1)
[ "$NODE_VER" -ge 18 ] 2>/dev/null || err "Node 18+ required. Run: brew install node"
ok "Python $(python3 --version 2>&1 | awk '{print $2}'), Node $(node --version)"

# ── 2. PostgreSQL — delegate entirely to setup_postgres.sh ───────────────
log "Setting up PostgreSQL..."
bash "$ROOT_DIR/scripts/setup_postgres.sh"

# Load the DATABASE_URL that setup_postgres.sh just wrote to .env
[ -f "$ROOT_DIR/.env" ] && set -o allexport && source "$ROOT_DIR/.env" && set +o allexport
DB_URL="${DATABASE_URL:-postgresql+asyncpg://pharmpilot:pharmpilot_dev@127.0.0.1:5433/pharmpilot}"
ok "Database URL: $DB_URL"

# ── 3. Redis ──────────────────────────────────────────────────────────────
log "Setting up Redis..."
brew list redis >/dev/null 2>&1 || brew install redis
redis-cli ping 2>/dev/null | grep -q PONG || {
  log "Starting Redis..."
  redis-server --daemonize yes \
    --logfile "$ROOT_DIR/.logs/redis.log" \
    --port 6379 2>/dev/null || \
  brew services start redis 2>/dev/null || true
  sleep 1
}
redis-cli ping 2>/dev/null | grep -q PONG \
  && ok "Redis running on port 6379" \
  || warn "Redis not responding — it may still be starting"

# ── 4. Qdrant vector database ─────────────────────────────────────────────
log "Setting up Qdrant..."
QDRANT_DIR="$ROOT_DIR/.qdrant"
QDRANT_BIN="$QDRANT_DIR/qdrant"
mkdir -p "$QDRANT_DIR/storage"

if [ ! -f "$QDRANT_BIN" ]; then
  log "Downloading Qdrant binary..."
  ARCH=$(uname -m)
  if [ "$ARCH" = "arm64" ]; then
    URL="https://github.com/qdrant/qdrant/releases/download/v1.9.7/qdrant-aarch64-apple-darwin.tar.gz"
  else
    URL="https://github.com/qdrant/qdrant/releases/download/v1.9.7/qdrant-x86_64-apple-darwin.tar.gz"
  fi
  curl -L --progress-bar "$URL" -o "$QDRANT_DIR/qdrant.tar.gz"
  tar -xzf "$QDRANT_DIR/qdrant.tar.gz" -C "$QDRANT_DIR"
  chmod +x "$QDRANT_BIN"
  rm -f "$QDRANT_DIR/qdrant.tar.gz"
  ok "Qdrant downloaded"
else
  ok "Qdrant already present"
fi

# ── 5. Python virtual environment ─────────────────────────────────────────
log "Setting up Python virtual environment..."
mkdir -p "$ROOT_DIR/.logs"

if [ ! -d "$ROOT_DIR/.venv" ]; then
  python3 -m venv "$ROOT_DIR/.venv"
  ok "Virtual environment created (.venv/)"
else
  ok "Virtual environment already exists"
fi

source "$ROOT_DIR/.venv/bin/activate"
pip install --upgrade pip setuptools wheel -q

log "Installing Python dependencies (takes ~2 min on first run)..."
pip install -q \
  "fastapi==0.115.6" \
  "pydantic[email]" \
  "email-validator" \
  "uvicorn[standard]" \
  "pydantic-settings" \
  "sqlalchemy[asyncio]" \
  "alembic" \
  "asyncpg" \
  "redis" \
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
  "numpy" \
  "scikit-learn" \
  "pandas" \
  "scipy" \
  "beautifulsoup4" \
  "PyPDF2" \
  "spacy" \
  "click"

ok "Python packages installed"

python3 -m spacy download en_core_web_sm -q 2>/dev/null || true
ok "spaCy model ready"

# ── 6. Generate .env secrets (first run only) ─────────────────────────────
log "Checking environment configuration..."
if [ ! -f "$ROOT_DIR/.env" ]; then
  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env" 2>/dev/null || touch "$ROOT_DIR/.env"
fi

# Add any missing required keys
add_if_missing() {
  grep -q "^$1" "$ROOT_DIR/.env" 2>/dev/null || echo "$1=$2" >> "$ROOT_DIR/.env"
}

add_if_missing "SECRET_KEY"            "$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
add_if_missing "VAULT_MASTER_KEY_HEX"  "$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
add_if_missing "VAULT_HMAC_SECRET_HEX" "$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
add_if_missing "REDIS_URL"             "redis://localhost:6379/0"
add_if_missing "QDRANT_URL"            "http://localhost:6333"
add_if_missing "ENVIRONMENT"           "development"
# DATABASE_URL is written by setup_postgres.sh — don't overwrite it
grep -q "^DATABASE_URL" "$ROOT_DIR/.env" || \
  add_if_missing "DATABASE_URL" "postgresql+asyncpg://pharmpilot:pharmpilot_dev@127.0.0.1:5433/pharmpilot"

ok ".env ready"

# Reload with all keys now present
set -o allexport && source "$ROOT_DIR/.env" && set +o allexport
DB_URL="${DATABASE_URL}"

# ── 7. Run database migrations ────────────────────────────────────────────
log "Running database migrations..."
DATABASE_URL="$DB_URL" PYTHONPATH="$ROOT_DIR" \
  "$ROOT_DIR/.venv/bin/alembic" -c "$ROOT_DIR/alembic.ini" upgrade head 2>&1 \
  && ok "Migrations applied — database at head" \
  || warn "Migration warning (may already be up to date)"

# ── 8. Seed demo users ────────────────────────────────────────────────────
log "Seeding demo accounts..."
DATABASE_URL="$DB_URL" PYTHONPATH="$ROOT_DIR" python3 - << PYEOF
import asyncio, sys, os
sys.path.insert(0, os.getcwd())

# Ensure all models are registered before any query
from shared.models.pharmacy import Pharmacy
from shared.models.auth import Staff, StaffRole
from shared.models.patient import Patient, PatientAllergy, LabResult, ClinicalNote
from shared.models.insurance import PatientInsurance, InsurancePlan
from shared.models.prescription import Prescription, PrescriptionFill, DURAlert, RxStateEvent
from shared.models.claims import ClaimTransaction, ERA835Record, DIRFeeAdjustment
from shared.models.inventory import DrugProduct, InventoryLot, StockLevel, PurchaseOrder, PurchaseOrderLine, ReceivingRecord
from shared.models.biometric import BiometricIdentity, PharmacyVisit, SecurityEvent
from shared.models.audio import AudioTranscript, ProfileEnrichmentAction

async def seed():
    try:
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
        from sqlalchemy import select
        from services.platform.auth import hash_password

        db_url = os.environ.get("DATABASE_URL", "")
        if not db_url:
            print("  No DATABASE_URL — skipping seed")
            return

        engine = create_async_engine(db_url, echo=False)
        Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with Session() as db:
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
                print(f"  Created pharmacy: {pharmacy.name}")

            result2 = await db.execute(select(Staff).where(Staff.username == "admin"))
            if not result2.scalar_one_or_none():
                for username, role, first, last, pw in [
                    ("admin",      StaffRole.SUPER_ADMIN,          "Pharmacy", "Admin",      "Admin1234!"),
                    ("pharmacist", StaffRole.PHARMACIST,           "Demo",     "Pharmacist", "Pharmacist1234!"),
                    ("tech",       StaffRole.PHARMACY_TECHNICIAN,  "Demo",     "Technician", "Tech1234!"),
                ]:
                    db.add(Staff(
                        pharmacy_id=pharmacy.id,
                        email=f"{username}@pharmpilot.local",
                        username=username,
                        hashed_password=hash_password(pw),
                        first_name=first,
                        last_name=last,
                        role=role,
                    ))
                await db.commit()
                print("  Created: admin / pharmacist / tech")
            else:
                print("  Demo accounts already exist")

        await engine.dispose()
    except Exception as e:
        print(f"  Seed skipped: {e}")

asyncio.run(seed())
PYEOF
ok "Seed complete"

# ── 9. Frontend dependencies ──────────────────────────────────────────────
log "Installing frontend dependencies..."
cd "$ROOT_DIR/frontend/workstation"
npm install --silent 2>/dev/null || npm install
ok "Frontend dependencies installed"

# Create frontend .env.local
[ -f "$ROOT_DIR/frontend/workstation/.env.local" ] || cat > "$ROOT_DIR/frontend/workstation/.env.local" << 'EOF'
VITE_API_URL=http://localhost:8000/api/v1
VITE_WS_URL=ws://localhost:8000
EOF
ok "Frontend .env.local ready"

# ── Done ──────────────────────────────────────────────────────────────────
cd "$ROOT_DIR"
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║            PharmPilot Local Setup Complete ✓                    ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${CYAN}Start everything:${NC}  bash scripts/dev.sh"
echo ""
echo -e "  ${YELLOW}Login credentials:${NC}"
echo -e "  ┌──────────────┬──────────────┬─────────────────┐"
echo -e "  │ Role         │ Username     │ Password        │"
echo -e "  ├──────────────┼──────────────┼─────────────────┤"
echo -e "  │ Admin        │ admin        │ Admin1234!      │"
echo -e "  │ Pharmacist   │ pharmacist   │ Pharmacist1234! │"
echo -e "  │ Technician   │ tech         │ Tech1234!       │"
echo -e "  └──────────────┴──────────────┴─────────────────┘"
echo ""
echo -e "  ${YELLOW}Enable AI features:${NC}  Add to .env → ANTHROPIC_API_KEY=sk-ant-..."
echo ""
