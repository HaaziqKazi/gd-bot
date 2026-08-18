#!/usr/bin/env bash
# The first live run of trainer/sightread.py against real Geometry Dash.
#
# Launches GD with every switch the driver needs on ONE command line with
# run_sandbox.sh, waits for the shared segment, attaches trainer/sightread.py
# in the foreground, checks provenance, and tears down. Modelled on
# scripts/dt_sweep.sh, which already gets the launch/attach/teardown dance
# right (pkill, .gd.lock removal, sleep-12 before attaching a client).
#
# WHY EVERY SWITCH IS INLINE, ON ONE LINE, WITH run_sandbox.sh
# --------------------------------------------------------------------------
# Operational notes (TODO, ~line 2290) and #23's contaminated-run story: shell
# state does not persist between an agent's tool calls, and of every GDRL_*
# switch only GDRL_WINDOWED and GDRL_NO_APP_NAP have inline fallbacks inside
# run_sandbox.sh itself (it reads them from its own environment with a
# default; everything else must be set by whatever invokes it). A run once
# lost ALL its GDRL_* switches between an `export` and the actual launch and
# produced a complete, plausible, worthless data set on the wrong level. That
# class of mistake is why this script sets everything in one place, in one
# process, rather than relying on anything exported earlier.
#
# WHY GDRL_DELTA_TICKS / GDRL_ADAPTIVE ARE DELIBERATELY ABSENT
# --------------------------------------------------------------------------
# Those are the EXP path's dt rewriter. This launch uses the ENV path's
# GDRL_ENV_DELTA_TICKS instead. telemetry.cpp:1363 says the two dt rewriters
# do not compose -- setting both is a "pick one, and the mod will warn you
# picked wrong" situation, not a stronger setting.
#
# SINGLE-INSTANCING
# --------------------------------------------------------------------------
# TODO records a timed-launch wrapper whose OWN pkill timer killed a LATER
# run's game 37s in -- a second invocation raced the first and both games died
# for the second one's benefit. This script never installs a timed background
# kill at all (teardown only happens via the trap below, on THIS invocation's
# own exit), which already rules out that exact race; the lock further below
# additionally refuses to let a second invocation of this script start while
# one is already running, so nothing can pkill a game this script does not own.
#
# Usage:
#   ./scripts/sightread_live.sh [--budget N] [--dt N] [-- extra sightread.py args...]
#
# Env (all optional; CLI flags above take precedence over these):
#   GDRL_ENV_DELTA_TICKS   observation-stride divisor for the ENV-path dt
#                          rewriter. Default 8 -- the largest N the 2026-08-16
#                          wrap could point to any evidence for (it had never
#                          been run; see TODO's dt sweep). A PARAMETER, not a
#                          constant: the orchestrator may be actively
#                          re-measuring whether a larger N is also safe.
#   GDRL_SKIP_STEAM_CHECK  passed through to run_sandbox.sh unchanged
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LOGDIR="$ROOT/sandbox/Geometry Dash.app/Contents/geode/logs"
GD_LOCK="$ROOT/sandbox/.gd.lock"
WRAP_LOCK="$ROOT/sandbox/.sightread_live.lock"

BUDGET=400
DT="${GDRL_ENV_DELTA_TICKS:-8}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --budget) BUDGET="$2"; shift 2 ;;
    --dt)     DT="$2"; shift 2 ;;
    --)       shift; break ;;
    *)        echo "[sightread_live] unknown arg: $1" >&2; exit 1 ;;
  esac
done
EXTRA_ARGS=("$@")

# --- single-instance the WRAPPER, not just the game ------------------------
# mkdir is the atomic primitive here because macOS ships no flock(1) --
# the same reasoning run_sandbox.sh's own GD-process lock uses, one level up:
# this lock is about a second sightread_live.sh, not a second GD.
if ! mkdir "$WRAP_LOCK" 2>/dev/null; then
    holder="$(cat "$WRAP_LOCK/pid" 2>/dev/null || true)"
    if [[ -n "$holder" ]] && kill -0 "$holder" 2>/dev/null; then
        echo "[sightread_live] already running as pid $holder (since " \
             "$(cat "$WRAP_LOCK/since" 2>/dev/null || echo '?'))." >&2
        exit 1
    fi
    echo "[sightread_live] clearing stale lock (holder ${holder:-unknown} is gone)" >&2
    rm -rf "$WRAP_LOCK"
    mkdir "$WRAP_LOCK"
fi
echo $$ > "$WRAP_LOCK/pid"
date +%Y-%m-%dT%H:%M:%S > "$WRAP_LOCK/since"

GD_PID=""
cleanup() {
    local status=$?
    [[ -n "$GD_PID" ]] && kill "$GD_PID" 2>/dev/null
    pkill -f "MacOS/Geometry Dash" 2>/dev/null
    sleep 2
    rm -rf "$GD_LOCK"
    rm -rf "$WRAP_LOCK"
    exit "$status"
}
trap cleanup EXIT INT TERM

echo "[sightread_live] binary: $(shasum -a 256 mod/build/gdrl.probe.geode 2>/dev/null | cut -c1-16 || echo 'not built')"
echo "[sightread_live] GDRL_ENV_DELTA_TICKS=$DT  budget=$BUDGET"

# --- launch, every switch inline on one command line ------------------------
pkill -f "MacOS/Geometry Dash" 2>/dev/null
sleep 2
rm -rf "$GD_LOCK"

GDRL_ENV=1 GDRL_AUTOPLAY=1 GDRL_PIN_LEVEL=1 GDRL_BLOCK_INPUT=1 \
GDRL_ENV_DELTA_TICKS="$DT" \
./scripts/run_sandbox.sh >/tmp/sightread_live_game.out 2>&1 &
GD_PID=$!

# --- wait for the shared segment, not a blind sleep --------------------------
# wait_for_shared() is the same utility trainer/sightread.py's own main() uses
# to attach -- reusing it here means "ready" means the same thing in both
# places, rather than this script guessing a sleep duration that drifts from
# what the driver actually needs.
echo "[sightread_live] waiting for the shared segment..."
if ! python3 -c "
import sys
sys.path.insert(0, 'trainer')
from env import wait_for_shared
wait_for_shared('gdrl.env', timeout=90.0).close()
"; then
    echo "[sightread_live] the shared segment never appeared within 90s;" \
         "see /tmp/sightread_live_game.out" >&2
    exit 1
fi
if ! kill -0 "$GD_PID" 2>/dev/null; then
    echo "[sightread_live] GD exited right after publishing the segment;" \
         "see /tmp/sightread_live_game.out" >&2
    exit 1
fi
# dt_sweep.sh's convention: a moment past first publication before a client
# attaches, rather than racing the very first frame.
sleep 12

# --- attach the driver, in the foreground ------------------------------------
# Every closed attempt is written to $JSONL as it happens (see
# trainer/sightread.py's jsonl_writer / AttemptLedger(on_close=...)), flushed
# and fsynced per line -- so a SIGSEGV or a kill of THIS script still leaves
# every attempt played on disk. $JSON is the end-of-run report, written only
# if the driver returns normally; it is a convenience on top of $JSONL, not a
# replacement for it.
TS="$(date +%Y%m%d-%H%M%S)"
JSON="/tmp/sightread_live_${TS}.json"
JSONL="/tmp/sightread_live_${TS}.jsonl"
echo "[sightread_live] attaching trainer/sightread.py (budget=$BUDGET)"
echo "[sightread_live] incremental (durable) results: $JSONL"
echo "[sightread_live] end-of-run report (best-effort):  $JSON"

python3 trainer/sightread.py \
    --budget "$BUDGET" --json "$JSON" --jsonl "$JSONL" \
    ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
DRIVER_STATUS=$?

# --- provenance check --------------------------------------------------------
# The exact check from Operational notes: a run without this line in the
# newest log is not evidence the GDRL_* switches above actually took effect.
NEWLOG="$(ls -t "$LOGDIR" 2>/dev/null | head -1)"
if [[ -n "$NEWLOG" ]]; then
    echo "[sightread_live] provenance check on $NEWLOG:"
    if grep "gdrl\] autoplay ->" "$LOGDIR/$NEWLOG"; then
        echo "[sightread_live] provenance OK"
    else
        echo "[sightread_live] provenance VOID: no 'gdrl] autoplay ->' line" \
             "in the log -- do not trust this run's numbers" >&2
    fi
else
    echo "[sightread_live] no log directory found; cannot check provenance" >&2
fi

echo "[sightread_live] driver exit status: $DRIVER_STATUS"
echo "[sightread_live] results: $JSON (end-of-run), $JSONL (incremental)"
exit "$DRIVER_STATUS"
