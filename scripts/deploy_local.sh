#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-deploy}"
PASSWD_HOME="$(getent passwd "$(id -u)" | cut -d: -f6)"
HOME_DIR="${FOREX_AI_HOME:-${PASSWD_HOME:-${HOME:-/tmp}}}"
SRC="${FOREX_AI_DEV_ROOT:-$PWD}"
RUNTIME_ROOT="${FOREX_AI_RUNTIME_ROOT:-$HOME_DIR/apps/forex-ai}"
RUNTIME_VENV="${FOREX_AI_RUNTIME_VENV:-$HOME_DIR/.venvs/forex-ai-runtime}"
TEST_PYTHON="${FOREX_AI_TEST_PYTHON:-$RUNTIME_VENV/bin/python}"
if [ ! -x "$TEST_PYTHON" ]; then
  TEST_PYTHON="python3"
fi
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
  # Older releases have no Market Intelligence entrypoint. Never leave a
  # timer invoking a missing binary after rolling back to v0.0.5/v0.0.4.
  if [ ! -f "$PREVIOUS/scripts/observe_market_intelligence.py" ]; then
    local user_runtime_dir="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    if [ -S "$user_runtime_dir/bus" ]; then
      XDG_RUNTIME_DIR="$user_runtime_dir" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=$user_runtime_dir/bus" \
        systemctl --user disable --now forex-ai-market-intelligence.timer >/dev/null 2>&1 || true
    fi
  fi
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

# Release identity is an exact, sequential annotated Git tag; a commit SHA
# and a timestamp are provenance only. Fail BEFORE staging or runtime mutation.
RELEASE_TAG="$(PYTHONPATH="$SRC/src" "$TEST_PYTHON" -c \
  'import sys; from pathlib import Path; from forex_ai.versioning import assert_release_tag; print(assert_release_tag(Path(sys.argv[1])))' "$SRC")"
GIT_SHA="$(git -C "$SRC" rev-parse HEAD)"
REMOTE_TAG_SHA="$(git -C "$SRC" ls-remote --tags origin "refs/tags/$RELEASE_TAG^{}" | awk 'NR==1 {print $1}')"
if [ "$REMOTE_TAG_SHA" != "$GIT_SHA" ]; then
  echo "Refusing deploy: annotated tag $RELEASE_TAG missing or different on origin" >&2
  exit 14
fi
test -s "$SRC/requirements.lock" || { echo "Missing requirements.lock" >&2; exit 13; }

# React V2 assets must correspond exactly to the reviewed source & npm lock.
# Do not run npm or fetch dependencies during production activation.
if [ -f "$SRC/dashboard/ui/package.json" ]; then
  "$TEST_PYTHON" - "$SRC/dashboard/ui" <<'PY'
import hashlib, json, sys
from pathlib import Path
root = Path(sys.argv[1])
stamp_path = root / "dist/build-integrity.json"
if not (root / "dist/index.html").is_file() or not stamp_path.is_file():
    raise SystemExit("V2 frontend assets missing; run npm ci && npm run build in dashboard/ui")
stamp = json.loads(stamp_path.read_text())
files = ["index.html", "package.json", "package-lock.json", "tsconfig.json",
         "vite.config.ts", "tailwind.config.js", "postcss.config.js"]
files += [p.relative_to(root).as_posix() for p in (root / "src").rglob("*") if p.is_file()]
hash_value = hashlib.sha256()
for name in sorted(files):
    hash_value.update(name.encode())
    hash_value.update(b"\0")
    hash_value.update((root / name).read_bytes())
    hash_value.update(b"\0")
if stamp.get("source_sha256") != hash_value.hexdigest():
    raise SystemExit("V2 frontend build is stale or untrusted; rebuild before deploy")
print("V2 verified frontend source fingerprint", hash_value.hexdigest()[:12])
PY
fi

# Tests include project-relative fixtures; execute from the source root.
cd "$SRC"
"$TEST_PYTHON" -m pytest "$SRC/tests" -q
LIVE_APPROVAL="$SRC/config/live-prospective-approval.json"
# Verify the live MT5 trade_mode and account binding at release time.
# DEMO must not be blocked by an unrelated REAL strategy fingerprint, while
# REAL/CONTEST/UNKNOWN cannot bypass the original approval gate.
FOREX_AI_CONFIG_DIR="$HOME_DIR/.config/forex-ai" PYTHONPATH="$SRC/src" \
    "$TEST_PYTHON" "$SRC/scripts/release_account_preflight.py" --approval "$LIVE_APPROVAL"
PREFLIGHT_DB="$(mktemp --suffix=.db)"
MANIFEST_TMP="$(mktemp --suffix=.json)"
cleanup() { rm -f "$PREFLIGHT_DB" "$MANIFEST_TMP"; }
trap cleanup EXIT
PYTHONPATH="$SRC/src" "$TEST_PYTHON" -c "from pathlib import Path; from forex_ai.journal.db import initialize; initialize(Path('$PREFLIGHT_DB'))"
PYTHONPATH="$SRC/src" "$TEST_PYTHON" "$SRC/scripts/release_manifest.py" --repo "$SRC" --output "$MANIFEST_TMP" --require-tag >/dev/null

RELEASE_ID="${RELEASE_TAG}-$(date -u +%Y%m%dT%H%M%SZ)-${GIT_SHA:0:12}"
RELEASES="$RUNTIME_ROOT/releases"
BACKTEST_ROOT="${FOREX_AI_BACKTEST_ROOT:-$RUNTIME_ROOT/backtest}"
STRATEGY_CONFIG="${FOREX_AI_STRATEGY_CONFIG:-$HOME_DIR/.config/forex-ai/strategy.yaml}"
MARKET_CONTEXT_CONFIG="${FOREX_AI_MARKET_CONTEXT_CONFIG:-$HOME_DIR/.config/forex-ai/market-context.yaml}"
RELEASE_DIR="$RELEASES/$RELEASE_ID"
STAGING="$RELEASES/.staging-$RELEASE_ID-$$"
mkdir -p "$RELEASES" "$BACKTEST_ROOT/data" "$(dirname "$STRATEGY_CONFIG")" "$(dirname "$MARKET_CONTEXT_CONFIG")"
# Do not replace an operator's effective strategy on deployment.
# A strategy mismatch must fail closed in readiness, not silently be "fixed".
if [ ! -f "$STRATEGY_CONFIG" ]; then
  cp "$SRC/config/strategy.yaml" "$STRATEGY_CONFIG"
fi
if [ ! -f "$MARKET_CONTEXT_CONFIG" ]; then
  cp "$SRC/config/market-context.yaml" "$MARKET_CONTEXT_CONFIG"
fi
chmod 0644 "$STRATEGY_CONFIG" "$MARKET_CONTEXT_CONFIG"
# Unique staging directories must never overwrite an existing deployment artifact.
if [ -e "$STAGING" ]; then
  echo "Refusing deploy: staging path already exists: $STAGING" >&2
  exit 16
fi
mkdir -p "$STAGING"

rsync -a --delete \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude 'node_modules/' \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  --exclude '*.db' \
  --exclude '*.db-wal' \
  --exclude '*.db-shm' \
  --exclude 'logs/' \
  "$SRC/" "$STAGING/"
# _private is intentionally Git-ignored; validate the 5 internal authorities
# and fingerprint them separately so a deployed release remains auditable.
"$TEST_PYTHON" - "$STAGING/_private" "$STAGING/private_control_manifest.json" <<'PY'
import hashlib, json, re, sys
from datetime import date
from pathlib import Path
directory, output = map(Path, sys.argv[1:])
expected = [
    "00_FOREX_AI_MASTER_ARCHITECTURE.md",
    "01_FOREX_AI_ROLES_AND_BOUNDARIES.md",
    "02_FOREX_AI_RUNTIME_TOPOLOGY.md",
    "03_FOREX_AI_LIFECYCLE_AND_RECOVERY.md",
    "04_FOREX_AI_STRATEGY_AND_EXECUTION_CONTROL.md",
]
actual = sorted(p.name for p in directory.glob("*.md"))
if actual != expected:
    raise SystemExit(f"Refusing deploy: 5 private control documents required, found: {actual}")
digests = {}
for name in expected:
    data = (directory / name).read_bytes()
    revision = re.search(rb"Revision: (20\d{2}-\d{2}-\d{2})", data[:1024])
    if not data or b"PRIVATE CONTROL DOCUMENT" not in data[:1024] or not revision:
        raise SystemExit(f"Refusing deploy: private control revision missing: {name}")
    try:
        revision_date = date.fromisoformat(revision.group(1).decode("ascii"))
    except ValueError:
        raise SystemExit(f"Refusing deploy: invalid control revision: {name}") from None
    if revision_date < date(2026, 10, 31):
        raise SystemExit(f"Refusing deploy: stale private control: {name}")
    digests[name] = hashlib.sha256(data).hexdigest()
output.write_text(json.dumps({"schema": "forex-ai-private-controls-v1", "sha256": digests}, indent=2) + "\n")
PY
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

if [ -f "$LIVE_APPROVAL" ]; then
  # Deployment is never permission to arm an MT5 REAL account.
  USER_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
  USER_BUS="$USER_RUNTIME_DIR/bus"
  test -S "$USER_BUS" || { echo "Refusing live-approved deploy: user systemd bus unavailable at $USER_BUS" >&2; exit 15; }
  export XDG_RUNTIME_DIR="$USER_RUNTIME_DIR"
  export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$USER_BUS}"
  USER_UNIT_DIR="$HOME_DIR/.config/systemd/user"
  mkdir -p "$USER_UNIT_DIR"
  for unit in \
    forex-ai-auto-live-week.service forex-ai-auto-live-week.timer \
    forex-ai-execute-pending.service forex-ai-execute-pending.timer \
    forex-ai-weekend-close.service forex-ai-weekend-close.timer; do
    cp "$RELEASE_DIR/deploy/$unit" "$USER_UNIT_DIR/$unit"
    chmod 0644 "$USER_UNIT_DIR/$unit"
  done
  systemctl --user daemon-reload
  # Keep the REAL-account automatic arming scheduler disabled by default.
  # Any future activation requires a distinct owner-authorized operation.
  systemctl --user disable --now forex-ai-auto-live-week.timer
  systemctl --user enable --now \
    forex-ai-execute-pending.timer \
    forex-ai-weekend-close.timer
fi

# Dashboard is a separate owner-only service on a 0600 Unix socket.
# Restart after the symlink switch so its static assets/code match the release.
USER_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
USER_BUS="$USER_RUNTIME_DIR/bus"
test -S "$USER_BUS" || { echo "Dashboard deploy requires an authenticated user systemd bus" >&2; exit 18; }
export XDG_RUNTIME_DIR="$USER_RUNTIME_DIR"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$USER_BUS}"
USER_UNIT_DIR="$HOME_DIR/.config/systemd/user"
mkdir -p "$USER_UNIT_DIR"
cp "$RELEASE_DIR/deploy/forex-ai-dashboard.service" "$USER_UNIT_DIR/forex-ai-dashboard.service"
chmod 0644 "$USER_UNIT_DIR/forex-ai-dashboard.service"
# Monitoring must be installed on every release, including DEMO. It reads MT5
# and writes observations only; it is not permission to execute trades.
for unit in forex-ai-market-intelligence.service forex-ai-market-intelligence.timer; do
  cp "$RELEASE_DIR/deploy/$unit" "$USER_UNIT_DIR/$unit"
  chmod 0644 "$USER_UNIT_DIR/$unit"
done
systemctl --user daemon-reload
systemctl --user enable --now forex-ai-market-intelligence.timer
systemctl --user enable --now forex-ai-dashboard.service
systemctl --user restart forex-ai-dashboard.service
# Service activation is asynchronous. Probe the real socket with a bounded gate,
# then assert the service is still active. Do not mistake startup latency for failure.
SOCKET_READY=0
for attempt in $(seq 1 50); do
  if [ -S "$USER_RUNTIME_DIR/forex-ai-dashboard.sock" ]; then
    SOCKET_READY=1
    break
  fi
  sleep 0.2
done
test "$SOCKET_READY" = "1" || { echo "Dashboard socket not available" >&2; exit 20; }
systemctl --user is-active --quiet forex-ai-dashboard.service || { echo "Dashboard health gate failed" >&2; exit 19; }

mapfile -t OLD_RELEASES < <(find "$RELEASES" -mindepth 1 -maxdepth 1 -type d ! -name '.staging-*' -printf '%T@ %p\n' | sort -nr | tail -n +$((KEEP_RELEASES + 1)) | cut -d' ' -f2-)
for old in "${OLD_RELEASES[@]:-}"; do
  [ -n "$old" ] || continue
  [ "$old" = "$(current_target || true)" ] && continue
  [ -L "$RUNTIME_ROOT/previous" ] && [ "$old" = "$(readlink -f "$RUNTIME_ROOT/previous")" ] && continue
  printf 'old_release_retained_for_manual_review=%s\n' "$old"
done

printf 'release=%s\n' "$RELEASE_ID"
printf 'current=%s\n' "$(current_target)"
printf 'venv=%s\n' "$RUNTIME_VENV"
