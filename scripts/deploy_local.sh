#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-deploy}"
HOME_DIR="${HOME:-/tmp}"
SRC="${FOREX_AI_DEV_ROOT:-$PWD}"
RUNTIME_ROOT="${FOREX_AI_RUNTIME_ROOT:-$HOME_DIR/apps/forex-ai}"
RUNTIME_VENV="${FOREX_AI_RUNTIME_VENV:-$HOME_DIR/.venvs/forex-ai-runtime}"
TEST_PYTHON="${FOREX_AI_TEST_PYTHON:-python3}"
REQUIRE_SYNC="${FOREX_AI_RELEASE_REQUIRE_SYNC:-1}"
KEEP_RELEASES="${FOREX_AI_KEEP_RELEASES:-5}"

current_target() {
  if [ -L "$RUNTIME_ROOT/current" ]; then
    readlink -f "$RUNTIME_ROOT/current"
  fi
}

audit_release() {
  local event_type="$1"
  local release_dir="$2"
  [ -n "${FOREX_AI_DB_PATH:-}" ] || return 0
  [ -x "$RUNTIME_VENV/bin/python" ] || return 0
  [ -f "$release_dir/release_manifest.json" ] || return 0
  FOREX_AI_AUDIT_EVENT="$event_type" FOREX_AI_AUDIT_RELEASE="$release_dir" \
    PYTHONPATH="$release_dir/src" "$RUNTIME_VENV/bin/python" -c \
    'import json,os; from pathlib import Path; from forex_ai.journal.db import initialize,log_audit_event; p=Path(os.environ["FOREX_AI_DB_PATH"]); d=Path(os.environ["FOREX_AI_AUDIT_RELEASE"]); initialize(p); m=json.loads((d/"release_manifest.json").read_text()); log_audit_event(p,event_type=os.environ["FOREX_AI_AUDIT_EVENT"],source="deploy",entity_id=m["release_fingerprint"],payload=m)'
}

status() {
  printf 'current=%s\n' "$(current_target || true)"
  if [ -L "$RUNTIME_ROOT/previous" ]; then
    printf 'previous=%s\n' "$(readlink -f "$RUNTIME_ROOT/previous")"
  fi
}

rollback() {
  test -L "$RUNTIME_ROOT/previous" || { echo "No previous release available" >&2; exit 2; }
  PREVIOUS="$(readlink -f "$RUNTIME_ROOT/previous")"
  test -d "$PREVIOUS" || { echo "Previous release missing: $PREVIOUS" >&2; exit 3; }
  CURRENT="$(current_target || true)"
  [ -n "$CURRENT" ] && audit_release "RELEASE_STOP" "$CURRENT"
  ln -sfn "$PREVIOUS" "$RUNTIME_ROOT/current.new"
  mv -Tf "$RUNTIME_ROOT/current.new" "$RUNTIME_ROOT/current"
  if [ -n "$CURRENT" ] && [ -d "$CURRENT" ]; then
    ln -sfn "$CURRENT" "$RUNTIME_ROOT/previous"
  fi
  audit_release "RELEASE_START" "$PREVIOUS"
  audit_release "RELEASE_ROLLBACK" "$PREVIOUS"
  printf 'rolled_back_to=%s\n' "$PREVIOUS"
  exit 0
}

case "$ACTION" in
  status) status; exit 0 ;;
  rollback) rollback ;;
  deploy) ;;
  *) echo "Usage: $0 {deploy|rollback|status}" >&2; exit 2 ;;
esac

command -v git >/dev/null
command -v rsync >/dev/null
git -C "$SRC" rev-parse --is-inside-work-tree >/dev/null

DIRTY="$(git -C "$SRC" status --porcelain)"
if [ -n "$DIRTY" ]; then
  echo "Refusing production deploy: source tree is dirty" >&2
  printf '%s\n' "$DIRTY" >&2
  exit 10
fi

if [ "$REQUIRE_SYNC" = "1" ]; then
  UPSTREAM="$(git -C "$SRC" rev-parse --abbrev-ref '@{upstream}' 2>/dev/null || true)"
  test -n "$UPSTREAM" || { echo "Refusing production deploy: branch has no upstream" >&2; exit 11; }
  AHEAD="$(git -C "$SRC" rev-list --count "$UPSTREAM..HEAD")"
  BEHIND="$(git -C "$SRC" rev-list --count "HEAD..$UPSTREAM")"
  if [ "$AHEAD" != "0" ] || [ "$BEHIND" != "0" ]; then
    echo "Refusing production deploy: source is not synchronized with $UPSTREAM (ahead=$AHEAD behind=$BEHIND)" >&2
    exit 12
  fi
fi

test -s "$SRC/requirements.lock" || { echo "Missing requirements.lock" >&2; exit 13; }

"$TEST_PYTHON" -m pytest "$SRC/tests" -q
PREFLIGHT_DB="$(mktemp --suffix=.db)"
MANIFEST_TMP="$(mktemp --suffix=.json)"
cleanup() { rm -f "$PREFLIGHT_DB" "$MANIFEST_TMP"; }
trap cleanup EXIT
PYTHONPATH="$SRC/src" "$TEST_PYTHON" -c "from pathlib import Path; from forex_ai.journal.db import initialize; initialize(Path('$PREFLIGHT_DB'))"
PYTHONPATH="$SRC/src" "$TEST_PYTHON" "$SRC/scripts/release_manifest.py" --repo "$SRC" --output "$MANIFEST_TMP" >/dev/null

GIT_SHA="$(git -C "$SRC" rev-parse HEAD)"
RELEASE_ID="$(date -u +%Y%m%dT%H%M%SZ)-${GIT_SHA:0:12}"
RELEASES="$RUNTIME_ROOT/releases"
BACKTEST_ROOT="${FOREX_AI_BACKTEST_ROOT:-$RUNTIME_ROOT/backtest}"
STRATEGY_CONFIG="${FOREX_AI_STRATEGY_CONFIG:-$HOME_DIR/.config/forex-ai/strategy.yaml}"
RELEASE_DIR="$RELEASES/$RELEASE_ID"
STAGING="$RELEASES/.staging-$RELEASE_ID-$$"
mkdir -p "$RELEASES" "$BACKTEST_ROOT/data" "$(dirname "$STRATEGY_CONFIG")"
if [ ! -f "$STRATEGY_CONFIG" ]; then
  cp "$SRC/config/strategy.yaml" "$STRATEGY_CONFIG"
fi
chmod 0644 "$STRATEGY_CONFIG"
rm -rf "$STAGING"
mkdir -p "$STAGING"

rsync -a --delete \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  --exclude '*.db' \
  --exclude '*.db-wal' \
  --exclude '*.db-shm' \
  --exclude 'logs/' \
  "$SRC/" "$STAGING/"
cp "$MANIFEST_TMP" "$STAGING/release_manifest.json"

if [ ! -x "$RUNTIME_VENV/bin/python" ]; then
  python3 -m venv "$RUNTIME_VENV"
fi
env -u PYTHONPATH "$RUNTIME_VENV/bin/python" -m pip install --require-hashes --requirement "$STAGING/requirements.lock"
env -u PYTHONPATH "$RUNTIME_VENV/bin/python" -m pip install --no-deps --no-build-isolation "$STAGING"

mv "$STAGING" "$RELEASE_DIR"
OLD_CURRENT="$(current_target || true)"
if [ -n "$OLD_CURRENT" ] && [ -d "$OLD_CURRENT" ]; then
  audit_release "RELEASE_STOP" "$OLD_CURRENT"
  ln -sfn "$OLD_CURRENT" "$RUNTIME_ROOT/previous"
fi
ln -sfn "$RELEASE_DIR" "$RUNTIME_ROOT/current.new"
mv -Tf "$RUNTIME_ROOT/current.new" "$RUNTIME_ROOT/current"

audit_release "RELEASE_START" "$RELEASE_DIR"

mapfile -t OLD_RELEASES < <(find "$RELEASES" -mindepth 1 -maxdepth 1 -type d ! -name '.staging-*' -printf '%T@ %p\n' | sort -nr | tail -n +$((KEEP_RELEASES + 1)) | cut -d' ' -f2-)
for old in "${OLD_RELEASES[@]:-}"; do
  [ -n "$old" ] || continue
  [ "$old" = "$(current_target || true)" ] && continue
  [ -L "$RUNTIME_ROOT/previous" ] && [ "$old" = "$(readlink -f "$RUNTIME_ROOT/previous")" ] && continue
  rm -rf "$old"
done

printf 'release=%s\n' "$RELEASE_ID"
printf 'current=%s\n' "$(current_target)"
printf 'venv=%s\n' "$RUNTIME_VENV"
