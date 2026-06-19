#!/usr/bin/env bash
# =============================================================================
#  Install macOS launchd agents so the PharmPilot stack is SELF-HEALING:
#  Qdrant + API auto-start at login and auto-restart on crash / sleep-wake /
#  reboot (KeepAlive). Postgres is left to Homebrew's own launchd service.
#
#  Install:    bash scripts/install-launchd.sh
#  Status:     launchctl list | grep pharmpilot
#  Uninstall:  bash scripts/install-launchd.sh --uninstall
# =============================================================================
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LA="$HOME/Library/LaunchAgents"
UID_N="$(id -u)"
QLABEL="com.pharmpilot.qdrant"
ALABEL="com.pharmpilot.api"
QPLIST="$LA/$QLABEL.plist"
APLIST="$LA/$ALABEL.plist"

C='\033[0;36m'; G='\033[0;32m'; Y='\033[1;33m'; N='\033[0m'
ok(){ echo -e "${G}[ ok ]${N} $1"; }
info(){ echo -e "${C}[info]${N} $1"; }

unload(){ launchctl bootout "gui/$UID_N/$1" >/dev/null 2>&1; launchctl unload -w "$2" >/dev/null 2>&1; }

if [ "${1:-}" = "--uninstall" ]; then
  unload "$QLABEL" "$QPLIST"; unload "$ALABEL" "$APLIST"
  rm -f "$QPLIST" "$APLIST"
  ok "Uninstalled launchd agents (Qdrant + API)."
  info "Postgres (Homebrew) is unaffected. Stop the stack with: pkill -f 'uvicorn|qdrant'"
  exit 0
fi

# Pick the Python that has the app's deps (miniforge if present).
PY="${PHARMPILOT_PYTHON:-}"
[ -z "$PY" ] && [ -x "$HOME/miniforge3/bin/python" ] && PY="$HOME/miniforge3/bin/python"
[ -z "$PY" ] && PY="$(command -v python3)"
info "checkout : $ROOT"
info "python   : $PY"

mkdir -p "$LA" "$ROOT/logs"
chmod +x "$ROOT/scripts/svc-qdrant.sh" "$ROOT/scripts/svc-api.sh"

gen_plist(){  # $1 label  $2 wrapper  $3 plist-path
  cat > "$3" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$1</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$ROOT/scripts/$2</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>PHARMPILOT_PYTHON</key><string>$PY</string>
  </dict>
  <key>StandardOutPath</key><string>$ROOT/logs/$1.out.log</string>
  <key>StandardErrorPath</key><string>$ROOT/logs/$1.err.log</string>
</dict>
</plist>
EOF
}

gen_plist "$QLABEL" "svc-qdrant.sh" "$QPLIST"
gen_plist "$ALABEL" "svc-api.sh"    "$APLIST"
ok "wrote $QPLIST"
ok "wrote $APLIST"

# (re)load
unload "$QLABEL" "$QPLIST"; unload "$ALABEL" "$APLIST"
launchctl load -w "$QPLIST" && ok "loaded Qdrant agent (auto-start + auto-restart)"
launchctl load -w "$APLIST" && ok "loaded API agent (auto-start + auto-restart)"

# Postgres: hand to Homebrew's launchd if available (already its own service).
if command -v brew >/dev/null 2>&1; then
  brew services start postgresql@16 >/dev/null 2>&1 && ok "Postgres handed to brew services (auto-restart)" \
    || info "Postgres: ensure your Homebrew postgresql@16 service is running"
fi

echo
ok "Self-healing installed. Qdrant + API now relaunch on crash / sleep / reboot."
info "Status : launchctl list | grep pharmpilot"
info "Logs   : $ROOT/logs/$QLABEL.err.log  ·  $ROOT/logs/$ALABEL.err.log"
info "Remove : bash scripts/install-launchd.sh --uninstall"
