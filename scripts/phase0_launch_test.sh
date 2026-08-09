#!/usr/bin/env bash
# Phase 0 gate. Answers two questions:
#   1. Does gdrl.probe actually load into the sandboxed GD?
#   2. Does GD honour the $HOME override, i.e. are saves really isolated?
#
# Refuses to run if any GD process is alive, because a live instance writing to
# the real save directory would make the isolation check report a false failure.
set -uo pipefail

P="$HOME/Documents/Projects/Repositories/gd-rl"
APP="$P/sandbox/Geometry Dash.app"
LOGDIR="$APP/Contents/geode/logs"
SBSAVE="$P/sandbox/home/Library/Application Support/GeometryDash"
REAL="$HOME/Library/Application Support/GeometryDash"

echo "=== preflight ==="
if pgrep -f "Geometry Dash.app/Contents/MacOS/Geometry Dash" >/dev/null 2>&1; then
    echo "ABORT: a Geometry Dash process is still running:"
    pgrep -fl "Geometry Dash.app/Contents/MacOS/Geometry Dash"
    exit 1
fi
echo "no GD running, ok"

rm -f "$LOGDIR"/*.log 2>/dev/null
rm -f "$SBSAVE/CCGameManager.dat" "$SBSAVE/CCLocalLevels.dat" 2>/dev/null

BEFORE_GM=$(shasum -a 256 "$REAL/CCGameManager.dat" | awk '{print $1}')
BEFORE_LL=$(shasum -a 256 "$REAL/CCLocalLevels.dat" | awk '{print $1}')
echo "real save baseline: ${BEFORE_GM:0:16} / ${BEFORE_LL:0:16}"

echo
echo "=== launching sandbox ==="
"$P/scripts/run_sandbox.sh" > "$P/launch.out" 2>&1 &
sleep 3
GDPID=$(pgrep -f "gd-rl/sandbox/Geometry Dash.app/Contents/MacOS/Geometry Dash" | head -1)
echo "sandbox pid: ${GDPID:-<none>}"

FOUND=0
WAITED=0
for i in $(seq 1 75); do
    sleep 1
    WAITED=$i
    if grep -qs "gdrl" "$LOGDIR"/*.log 2>/dev/null; then FOUND=1; break; fi
    if [ -n "${GDPID:-}" ] && ! kill -0 "$GDPID" 2>/dev/null; then
        echo "!! process exited on its own after ${i}s"
        break
    fi
done
echo "waited ${WAITED}s; gdrl marker found=${FOUND}"

echo
echo "=== which GeometryDash paths does the process have open? ==="
if [ -n "${GDPID:-}" ] && kill -0 "$GDPID" 2>/dev/null; then
    lsof -p "$GDPID" 2>/dev/null | grep -i "GeometryDash" | awk '{print $NF}' | sort -u | head -15
else
    echo "(process not alive)"
fi

echo
echo "=== our mod's log lines ==="
grep -i "gdrl" "$LOGDIR"/*.log 2>/dev/null | head -20 || echo "(none)"

echo
echo "=== loader: mod load results / errors ==="
grep -iE "loaded|loading|error|fail|unable|incompat" "$LOGDIR"/*.log 2>/dev/null | head -25 || echo "(no log file at all)"

echo
echo "=== save isolation ==="
echo "--- sandbox save dir:"
ls -la "$SBSAVE/" 2>/dev/null || echo "(sandbox save dir empty/missing)"
AFTER_GM=$(shasum -a 256 "$REAL/CCGameManager.dat" | awk '{print $1}')
AFTER_LL=$(shasum -a 256 "$REAL/CCLocalLevels.dat" | awk '{print $1}')
if [ "$BEFORE_GM" = "$AFTER_GM" ] && [ "$BEFORE_LL" = "$AFTER_LL" ]; then
    echo "REAL SAVE UNCHANGED  [ok]"
else
    echo "REAL SAVE MODIFIED   [PROBLEM]"
    echo "  gm: ${BEFORE_GM:0:16} -> ${AFTER_GM:0:16}"
    echo "  ll: ${BEFORE_LL:0:16} -> ${AFTER_LL:0:16}"
fi

echo
echo "=== shutting down sandbox ==="
pkill -f "gd-rl/sandbox/Geometry Dash.app/Contents/MacOS/Geometry Dash" 2>/dev/null
sleep 3
if pgrep -f "gd-rl/sandbox/Geometry Dash.app" >/dev/null 2>&1; then
    echo "still alive, SIGKILL"
    pkill -9 -f "gd-rl/sandbox/Geometry Dash.app" 2>/dev/null
else
    echo "stopped"
fi

echo
echo "=== stdout/stderr from launch ==="
head -25 "$P/launch.out" 2>/dev/null || echo "(empty)"
