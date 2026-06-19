#!/usr/bin/env bash
# launchd-managed Qdrant launcher. Always pins storage_path to THIS checkout
# (defeats the "wrong-checkout storage → 0 vectors" bug), then exec's Qdrant so
# launchd's KeepAlive supervises the real process.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p "$ROOT/.qdrant/storage" "$ROOT/logs"

cat > "$ROOT/.qdrant/config.yaml" <<EOF
storage:
  storage_path: $ROOT/.qdrant/storage

service:
  host: 127.0.0.1
  http_port: 6334
  grpc_port: 6335
  enable_tls: false

telemetry_disabled: true
EOF

exec "$ROOT/.qdrant/qdrant" --config-path "$ROOT/.qdrant/config.yaml"
