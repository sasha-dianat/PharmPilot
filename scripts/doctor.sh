#!/usr/bin/env bash
# =============================================================================
#  PharmPilot stack doctor — diagnose & repair the local environment
#
#  Closes out the recurring "data disappeared / KB unavailable" class of bugs,
#  which are all caused by environment ambiguity:
#    • multiple repo checkouts whose dev.sh rewrites .qdrant/config.yaml to point
#      Qdrant at a DIFFERENT checkout's storage  → KB shows 0 vectors
#    • multiple Postgres clusters racing for port 5433  → app silently switches DBs
#    • macOS idle-sleep killing Qdrant/API mid-session
#
#  Run:  bash scripts/doctor.sh            # diagnose + safe auto-fixes
#        bash scripts/doctor.sh --stop-dupes   # also stop our extra PG cluster
# =============================================================================
set -uo pipefail   # NOT -e: we want to run every check even if one fails

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
[ -f "$ROOT_DIR/.env" ] && set -o allexport && source "$ROOT_DIR/.env" && set +o allexport

PG_BIN="${PHARMPILOT_PG_BIN:-/usr/local/opt/postgresql@16/bin}"
PG_PORT="${PHARMPILOT_PGPORT:-5433}"
QDRANT_PORT=6334
API_PORT=8001
DB_URL="${DATABASE_URL:-postgresql+asyncpg://pharmpilot:pharmpilot_dev@127.0.0.1:5433/pharmpilot}"
PGPASS="$(printf '%s' "$DB_URL" | sed -E 's#.*//[^:]+:([^@]+)@.*#\1#')"
PGUSER="$(printf '%s' "$DB_URL" | sed -E 's#.*//([^:]+):.*#\1#')"
PY="/Users/sashad85/miniforge3/bin/python"; [ -x "$PY" ] || PY="$(command -v python3)"
STOP_DUPES=0; [ "${1:-}" = "--stop-dupes" ] && STOP_DUPES=1

C='\033[0;36m'; G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; N='\033[0m'
ok(){   echo -e "${G}[ ok ]${N} $1"; }
warn(){ echo -e "${Y}[warn]${N} $1"; ISSUES=$((ISSUES+1)); }
bad(){  echo -e "${R}[FAIL]${N} $1"; ISSUES=$((ISSUES+1)); }
info(){ echo -e "${C}[info]${N} $1"; }
up(){ nc -z localhost "$1" >/dev/null 2>&1; }
ISSUES=0

echo "════════════════════════════════════════════════════════════"
echo " PharmPilot stack doctor"
echo " checkout : $ROOT_DIR"
echo "════════════════════════════════════════════════════════════"

# ── 1. Single checkout / Qdrant storage pin ─────────────────────────────────
echo; echo "▸ Qdrant storage pin"
QSTORE="$ROOT_DIR/.qdrant/storage"
QCONF="$ROOT_DIR/.qdrant/config.yaml"
mkdir -p "$QSTORE"
CUR=""; [ -f "$QCONF" ] && CUR="$(grep -E 'storage_path:' "$QCONF" 2>/dev/null | awk '{print $2}')"
if [ "$CUR" != "$QSTORE" ]; then
  warn "config storage_path was '${CUR:-<missing>}' — repinning to this checkout"
  cat > "$QCONF" <<EOF
storage:
  storage_path: $QSTORE

service:
  host: 127.0.0.1
  http_port: $QDRANT_PORT
  grpc_port: $((QDRANT_PORT + 1))
  enable_tls: false

telemetry_disabled: true
EOF
  RESTART_QDRANT=1
else
  ok "storage_path correctly pinned to this checkout"
fi
OTHER=$(ls -d /Users/sashad85/*/.qdrant/storage 2>/dev/null | grep -v "$QSTORE" | head -3)
[ -n "$OTHER" ] && warn "other checkout storages exist (ignore unless you switch checkouts):" && echo "$OTHER" | sed 's/^/        /'

# ── 2. Postgres ─────────────────────────────────────────────────────────────
echo; echo "▸ Postgres (port $PG_PORT)"
PGDIRS="$(ps aux | grep '[p]ostgres -D' | sed -E 's#.* -D ([^ ]+).*#\1#' | sort -u)"
PGCOUNT="$(printf '%s\n' "$PGDIRS" | grep -c .)"
info "$PGCOUNT postgres cluster(s) running: $(printf '%s ' $PGDIRS)"
if [ "$PGCOUNT" -gt 1 ]; then
  warn "multiple Postgres clusters — only ONE should own :$PG_PORT (this silently switches your data source)"
fi
if up "$PG_PORT"; then
  ok "Postgres listening on :$PG_PORT"
else
  warn "Postgres :$PG_PORT is DOWN — starting Homebrew cluster"
  LC_ALL=C "$PG_BIN/pg_ctl" start -D /usr/local/var/postgresql@16 \
    -l "$HOME/.pharmpilot/postgres.log" -w -t 15 >/dev/null 2>&1 \
    || LC_ALL=C "$PG_BIN/pg_ctl" start -D "${PHARMPILOT_PGDATA:-$HOME/.pharmpilot/pgdata}" \
       -l "$HOME/.pharmpilot/postgres.log" -w -t 15 >/dev/null 2>&1
  up "$PG_PORT" && ok "Postgres started" || bad "could not start Postgres — start it manually"
fi
# Optionally stop OUR extra cluster (~/.pharmpilot/pgdata) if it's not the one on 5433
if [ "$STOP_DUPES" = 1 ]; then
  for d in $PGDIRS; do
    if [ "$d" = "$HOME/.pharmpilot/pgdata" ]; then
      warn "stopping duplicate cluster $d (per --stop-dupes)"
      "$PG_BIN/pg_ctl" stop -D "$d" -m fast >/dev/null 2>&1 && ok "stopped $d"
    fi
  done
fi
# Data sanity
if up "$PG_PORT"; then
  export PGPASSWORD="$PGPASS"
  Q(){ "$PG_BIN/psql" -h 127.0.0.1 -p "$PG_PORT" -U "$PGUSER" -d pharmpilot -tA -c "$1" 2>/dev/null; }
  PH="$(Q 'SELECT name FROM pharmacies ORDER BY created_at LIMIT 1;')"
  if [ -n "$PH" ]; then
    ok "DB reachable — pharmacy: $PH"
    info "data: stock>0=$(Q 'SELECT count(*) FROM stock_levels WHERE quantity_on_hand>0;') | movements=$(Q 'SELECT count(*) FROM inventory_movements;') | claims=$(Q 'SELECT count(*) FROM claim_transactions;') | dur_alerts=$(Q 'SELECT count(*) FROM dur_alerts;') | security=$(Q 'SELECT count(*) FROM security_events;')"
    [ "$(Q 'SELECT count(*) FROM stock_levels WHERE quantity_on_hand>0;')" = "0" ] && \
      warn "no stocked inventory — reseed: DATABASE_URL=\$DATABASE_URL python scripts/seed_inventory.py --reset"
  else
    bad "DB on :$PG_PORT has no demo pharmacy — wrong cluster, or needs seeding (scripts/seed_*.py)"
  fi
fi

# ── 3. Qdrant ───────────────────────────────────────────────────────────────
echo; echo "▸ Qdrant (port $QDRANT_PORT)"
if [ "${RESTART_QDRANT:-0}" = 1 ] && up "$QDRANT_PORT"; then
  pkill -f "qdrant --config-path" >/dev/null 2>&1; sleep 3
fi
if ! up "$QDRANT_PORT"; then
  warn "Qdrant down — starting (caffeinated)"
  caffeinate -i "$ROOT_DIR/.qdrant/qdrant" --config-path "$QCONF" > "$ROOT_DIR/logs/qdrant.log" 2>&1 &
  for _ in $(seq 1 12); do up "$QDRANT_PORT" && break; sleep 1; done
fi
if up "$QDRANT_PORT"; then
  ok "Qdrant listening on :$QDRANT_PORT"
  VEC="$(curl -s "http://localhost:$QDRANT_PORT/collections/pharmpilot_clinical_knowledge" | "$PY" -c 'import sys,json;print(json.load(sys.stdin).get("result",{}).get("points_count") or 0)' 2>/dev/null)"
  if [ "${VEC:-0}" -gt 0 ] 2>/dev/null; then
    ok "KB collection loaded — $VEC vectors"
  else
    warn "KB collection empty. Reload from your embed file:"
    echo "        python scripts/load_points.py KBdata/pharmpilot_kb_points.jsonl.gz"
  fi
else
  bad "Qdrant did not start — check logs/qdrant.log"
fi

# ── 4. API + frontend ───────────────────────────────────────────────────────
echo; echo "▸ App"
up "$API_PORT" && ok "API on :$API_PORT" || warn "API down — start the stack: bash scripts/dev.sh"
up 3001 && ok "Frontend on :3001" || info "Frontend not running (bash scripts/dev.sh starts it)"

# ── Summary ─────────────────────────────────────────────────────────────────
echo; echo "════════════════════════════════════════════════════════════"
if [ "$ISSUES" = 0 ]; then
  echo -e "${G} ✓ Stack healthy — no issues.${N}"
else
  echo -e "${Y} ⚠ ${ISSUES} issue(s) addressed/flagged above.${N}"
  echo "   Durable fix: use ONE checkout ($ROOT_DIR) and run only its dev.sh;"
  echo "   keep the Homebrew Postgres and stop the others; keep the lid open."
fi
echo "════════════════════════════════════════════════════════════"
