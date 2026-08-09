#!/usr/bin/env bash
# Show the most recent Geode log from the sandboxed install.
# The Geode log is the primary debugging channel for the mod.
#
# Usage: ./logs.sh           print latest log
#        ./logs.sh -f        follow latest log
#        ./logs.sh -g gdrl   grep latest log for a pattern
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
LOGDIR="$ROOT/sandbox/Geometry Dash.app/Contents/geode/logs"

LATEST="$(ls -t "$LOGDIR" 2>/dev/null | head -1 || true)"
if [[ -z "$LATEST" ]]; then
    echo "no logs in $LOGDIR (has the sandbox been launched yet?)" >&2
    exit 1
fi

case "${1:-}" in
    -f) exec tail -f "$LOGDIR/$LATEST" ;;
    -g) exec grep -i "${2:?pattern required}" "$LOGDIR/$LATEST" ;;
    *)  exec cat "$LOGDIR/$LATEST" ;;
esac
