#!/bin/sh
set -eu

# Upstream lprett/mt5linux is not restart-safe as shipped:
# 1) init_wine recreates $WIN_ROOT/server with mkfifo but does not remove stale state.
# 2) apply_mt5_config returns status 1 on non-first-run and main.sh uses set -e.
if ! grep -q 'rm -f $WIN_ROOT/server' /app/src/automation.sh; then
  sed -i 's|  mkfifo -m 666 $WIN_ROOT/server|  rm -f $WIN_ROOT/server\n  mkfifo -m 666 $WIN_ROOT/server|' /app/src/automation.sh
fi
sed -i 's#  test $FIRST_RUN || return$#  test "$FIRST_RUN" || return 0#' /app/src/config.sh

exec /app/src/main.sh
