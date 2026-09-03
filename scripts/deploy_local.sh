#!/usr/bin/env bash
set -euo pipefail

SRC="${FOREX_AI_DEV_ROOT:-$PWD}"
RUNTIME_ROOT="${FOREX_AI_RUNTIME_ROOT:-$HOME/apps/forex-ai}"
RUNTIME_VENV="${FOREX_AI_RUNTIME_VENV:-$HOME/.venvs/forex-ai-runtime}"
RELEASE_ID="$(date -u +%Y%m%dT%H%M%SZ)"
if git -C "$SRC" rev-parse --short HEAD >/dev/null 2>&1; then
  RELEASE_ID="${RELEASE_ID}-$(git -C "$SRC" rev-parse --short HEAD)"
fi
RELEASE_DIR="$RUNTIME_ROOT/releases/$RELEASE_ID"

mkdir -p "$RUNTIME_ROOT/releases"
mkdir -p "$RELEASE_DIR"

rsync -a --delete \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  --exclude '*.db' \
  --exclude '*.db-wal' \
  --exclude '*.db-shm' \
  --exclude 'logs/' \
  "$SRC/" "$RELEASE_DIR/"

if [ ! -x "$RUNTIME_VENV/bin/python" ]; then
  python3 -m venv "$RUNTIME_VENV"
  "$RUNTIME_VENV/bin/python" -m pip install --upgrade pip setuptools wheel
fi

"$RUNTIME_VENV/bin/pip" install -e "$RELEASE_DIR"
ln -sfn "$RELEASE_DIR" "$RUNTIME_ROOT/current.new"
mv -Tf "$RUNTIME_ROOT/current.new" "$RUNTIME_ROOT/current"

printf 'release=%s\n' "$RELEASE_ID"
printf 'current=%s\n' "$(readlink -f "$RUNTIME_ROOT/current")"
printf 'venv=%s\n' "$RUNTIME_VENV"
