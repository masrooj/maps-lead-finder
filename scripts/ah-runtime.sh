#!/usr/bin/env bash
# AppointHive Sales OS — runtime helpers that avoid hung `docker compose ps`.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

cmd="${1:-status}"

health() {
  curl -sS -m 5 http://127.0.0.1:"${APP_PORT:-8000}"/api/health || echo '{"ok":false,"error":"unreachable"}'
}

listeners() {
  lsof -nP -iTCP:"${APP_PORT:-8000}","${DB_PORT:-5433}" -sTCP:LISTEN 2>/dev/null || true
}

case "$cmd" in
  status|health)
    echo "=== health ==="
    health
    echo
    echo "=== listeners ==="
    listeners
    ;;
  up)
    # Prefer existing containers; bind-mount means code updates without rebuild.
    DOCKER_CLIENT_TIMEOUT=20 COMPOSE_HTTP_TIMEOUT=20 \
      docker compose up -d --no-build 2>/dev/null \
      || docker start maps-lead-finder-app-1 maps-lead-finder-db-1 2>/dev/null \
      || true
    sleep 2
    health
    ;;
  restart-app)
    docker restart maps-lead-finder-app-1
    sleep 2
    health
    ;;
  rebuild)
    echo "Rebuild needs free disk (~8Gi+). Aborting if under 5Gi free."
    avail_kb=$(df -k . | awk 'NR==2{print $4}')
    if [ "${avail_kb:-0}" -lt 5000000 ]; then
      echo "NO-GO: only ${avail_kb}KB free. Free disposable space first."
      exit 2
    fi
    DOCKER_CLIENT_TIMEOUT=120 COMPOSE_HTTP_TIMEOUT=120 docker compose up --build -d
    sleep 3
    health
    ;;
  *)
    echo "Usage: $0 {status|up|restart-app|rebuild}"
    exit 1
    ;;
esac
