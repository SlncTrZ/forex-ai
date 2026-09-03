#!/usr/bin/env bash
set -euo pipefail

NAME="forex-mt5"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PINNED_IMAGE="$(tr -d '\r\n' < "$PROJECT_ROOT/config/mt5_image.txt")"
IMAGE="${FOREX_AI_MT5_IMAGE:-$PINNED_IMAGE}"
HOME_DIR="${HOME:-/tmp}"
UI_PASSWORD_FILE="${FOREX_AI_UI_PASSWORD_FILE:-$HOME_DIR/.config/forex-ai/mt5_ui_password}"
BIND_IP="${FOREX_AI_BIND_IP:-0.0.0.0}"
NOVNC_HOST="${FOREX_AI_NOVNC_HOST:-$BIND_IP}"

case "${1:-status}" in
  start)
    if docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
      echo "$NAME already running"
      exit 0
    fi
    if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
      docker start "$NAME"
      exit 0
    fi
    test -s "$UI_PASSWORD_FILE" || { echo "Missing $UI_PASSWORD_FILE" >&2; exit 2; }
    PW=$(cat "$UI_PASSWORD_FILE")
    docker run -d \
      --name "$NAME" \
      --restart unless-stopped \
      -p 127.0.0.1:18812:18812 \
      -p "$BIND_IP:8080:8080" \
      -p "$BIND_IP:5901:5901" \
      -e NOVNC_HOST="$NOVNC_HOST" \
      -e UI_PASSWORD="$PW" \
      "$IMAGE"
    ;;
  stop)
    docker stop "$NAME"
    ;;
  restart)
    docker restart "$NAME"
    ;;
  logs)
    docker logs --tail "${2:-100}" "$NAME"
    ;;
  status)
    docker ps -a --filter "name=^/${NAME}$" --format 'name={{.Names}} status={{.Status}} ports={{.Ports}}'
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|logs [N]|status}" >&2
    exit 2
    ;;
esac
