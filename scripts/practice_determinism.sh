#!/usr/bin/env bash
# TODO.md 2.1's own acceptance test, run live against real Geometry Dash:
# restore the same checkpoint N times with the same subsequent inputs and
# require bit-identical outcomes. See trainer/practice_determinism.py's
# module docstring for the full method and what this does and does not prove.
#
# Modelled directly on scripts/sightread_live.sh, which already gets the
# launch/attach/teardown dance right (pkill, .gd.lock removal, sleep-12
# before attaching a client, a wrapper-level lock so a second invocation
# cannot race this one's teardown). Everything in that script's own header
# comment about WHY EVERY SWITCH IS INLINE / SINGLE-INSTANCING applies here
# unchanged; it is not repeated in full below, only the one addition
# (GDRL_PRACTICE) gets its own note.
#
# WHY GDRL_PRACTICE IS INLINE, ON THIS COMMAND LINE, LIKE EVERY OTHER SWITCH
# --------------------------------------------------------------------------
# mod/src/telemetry.cpp's own comment on GDRL_PRACTICE: it is a
# Benchmark-B-by-definition opt-in (TODO.md "Open decisions -> 0": rewinding
# without paying for the replay is Benchmark B by definition), never
# something a run should carry by accident. This wrapper exists ONLY to run
# TODO.md 2.1's acceptance test, so GDRL_PRACTICE=1 is not optional here the
# way it would be for scripts/sightread_live.sh -- but it is still set on
# this ONE inline command line, for the same reason sightread_live.sh gives:
# shell state does not persist between an agent's tool calls, and a run that
# lost GDRL_PRACTICE between an export and the actual launch would silently
# test nothing (every CHECKPOINT_* action would be refused as a protocol
# error) while still producing a report that looks like it ran.
#
# trainer/practice_determinism.py independently refuses to proceed past its
# first observation if header.practiceMode == 0, so a lost switch fails fast
# and loudly rather than producing a quiet false pass -- but catching it here,
# before GD is even launched with the wrong environment, is cheaper.
#
# WHY GDRL_AUTOPLAY / GDRL_PIN_LEVEL / GDRL_BLOCK_INPUT ARE ALSO SET
# --------------------------------------------------------------------------
# Same reasons as sightread_live.sh: AUTOPLAY gets the mod to enter the
# pinned level itself (no menu-clicking), PIN_LEVEL keeps a MENU REACHED
# mid-run from wandering onto a different level, BLOCK_INPUT stops a stray
# real keypress from being indistinguishable from a scheduled action. None of
# the three interact with checkpoint save/restore; they are the same
# hygiene sightread_live.sh already needs.
#
# WHY GDRL_ENV_DELTA_TICKS / GDRL_ENV_ADAPTIVE ARE DELIBERATELY ABSENT
# --------------------------------------------------------------------------
# trainer/practice_determinism.py always steps with advance_steps=1 (every
# physics tick is its own observation) -- it needs every tick to build the
# bit-identical comparison, not a stride. Setting either dt rewriter here
# would only make the driver wait longer per step for no benefit.
#
# Usage:
#   ./scripts/practice_determinism.sh [--restores N] [--pre-ticks N] \
#       [--attempt-budget-ticks N] [-- extra practice_determinism.py args...]
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LOGDIR="$ROOT/sandbox/Geometry Dash.app/Contents/geode/logs"
GD_LOCK="$ROOT/sandbox/.gd.lock"
WRAP_LOCK="$ROOT/sandbox/.practice_determinism.lock"

RESTORES=4
PRE_TICKS=40
ATTEMPT_BUDGET_TICKS=2000

while [[ $# -gt 0 ]]; do
  case "$1" in
    --restores)              RESTORES="$2"; shift 2 ;;
    --pre-ticks)              PRE_TICKS="$2"; shift 2 ;;
    --attempt-budget-ticks)   ATTEMPT_BUDGET_TICKS="$2"; shift 2 ;;
    --)                       shift; break ;;
    *)  echo "[practice_determinism] unknown arg: $1" >&2; exit 1 ;;
  esac
done
EXTRA_ARGS=("$@")

# --- single-instance the WRAPPER, not just the game ------------------------
# Same mkdir-as-atomic-primitive reasoning as sightread_live.sh: this lock is
# about a second practice_determinism.sh, not a second GD (run_sandbox.sh's
# own .gd.lock is the one-level-up lock for that).
if ! mkdir "$WRAP_LOCK" 2>/dev/null; then
    holder="$(cat "$WRAP_LOCK/pid" 2>/dev/null || true)"
    if [[ -n "$holder" ]] && kill -0 "$holder" 2>/dev/null; then
        echo "[practice_determinism] already running as pid $holder (since " \
             "$(cat "$WRAP_LOCK/since" 2>/dev/null || echo '?'))." >&2
        exit 1
    fi
    echo "[practice_determinism] clearing stale lock (holder ${holder:-unknown} is gone)" >&2
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

echo "[practice_determinism] binary: $(shasum -a 256 mod/build/gdrl.probe.geode 2>/dev/null | cut -c1-16 || echo 'not built')"
echo "[practice_determinism] restores=$RESTORES pre_ticks=$PRE_TICKS attempt_budget_ticks=$ATTEMPT_BUDGET_TICKS"

# --- launch, every switch inline on one command line ------------------------
pkill -f "MacOS/Geometry Dash" 2>/dev/null
sleep 2
rm -rf "$GD_LOCK"

GDRL_ENV=1 GDRL_AUTOPLAY=1 GDRL_PIN_LEVEL=1 GDRL_BLOCK_INPUT=1 GDRL_PRACTICE=1 \
./scripts/run_sandbox.sh >/tmp/practice_determinism_game.out 2>&1 &
GD_PID=$!

# --- wait for the shared segment, not a blind sleep --------------------------
echo "[practice_determinism] waiting for the shared segment..."
if ! python3 -c "
import sys
sys.path.insert(0, 'trainer')
from env import wait_for_shared
wait_for_shared('gdrl.env', timeout=90.0).close()
"; then
    echo "[practice_determinism] the shared segment never appeared within 90s;" \
         "see /tmp/practice_determinism_game.out" >&2
    exit 1
fi
if ! kill -0 "$GD_PID" 2>/dev/null; then
    echo "[practice_determinism] GD exited right after publishing the segment;" \
         "see /tmp/practice_determinism_game.out" >&2
    exit 1
fi
# dt_sweep.sh / sightread_live.sh's convention: a moment past first
# publication before a client attaches, rather than racing the very first
# frame.
sleep 12

# --- attach the driver, in the foreground ------------------------------------
# jsonl is durable, flushed per step; see trainer/practice_determinism.py's
# own log_obs(). json is the end-of-run report, written only on a normal
# return.
TS="$(date +%Y%m%d-%H%M%S)"
JSON="/tmp/practice_determinism_${TS}.json"
JSONL="/tmp/practice_determinism_${TS}.jsonl"
echo "[practice_determinism] attaching trainer/practice_determinism.py"
echo "[practice_determinism] per-step (durable) log: $JSONL"
echo "[practice_determinism] end-of-run report (best-effort): $JSON"

python3 trainer/practice_determinism.py \
    --restores "$RESTORES" --pre-ticks "$PRE_TICKS" \
    --attempt-budget-ticks "$ATTEMPT_BUDGET_TICKS" \
    --json "$JSON" --jsonl "$JSONL" \
    ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
DRIVER_STATUS=$?

# --- provenance check --------------------------------------------------------
# Two lines, not one: the ordinary sightread_live.sh check (autoplay actually
# reached the pinned level) AND the GDRL_PRACTICE-specific STAMP line
# (telemetry.cpp's $execute block warns on its own line when practice mode is
# on, precisely so this grep cannot miss it inside a long comma list). A run
# missing either line is not evidence this acceptance test actually exercised
# GDRL_PRACTICE, regardless of what the driver's own exit status says.
NEWLOG="$(ls -t "$LOGDIR" 2>/dev/null | head -1)"
if [[ -n "$NEWLOG" ]]; then
    echo "[practice_determinism] provenance check on $NEWLOG:"
    AUTOPLAY_OK=0
    PRACTICE_OK=0
    if grep -q "gdrl\] autoplay ->" "$LOGDIR/$NEWLOG"; then
        echo "[practice_determinism]   autoplay reached the pinned level: OK"
        AUTOPLAY_OK=1
    else
        echo "[practice_determinism]   MISSING 'gdrl] autoplay ->' -- do not" \
             "trust this run's numbers" >&2
    fi
    if grep -q "gdrl\] STAMP telemetry GDRL_PRACTICE=1" "$LOGDIR/$NEWLOG"; then
        echo "[practice_determinism]   GDRL_PRACTICE=1 reached the mod: OK"
        PRACTICE_OK=1
    else
        echo "[practice_determinism]   MISSING 'GDRL_PRACTICE=1' STAMP line --" \
             "this run may not have exercised checkpoint restore at all" >&2
    fi
    if [[ "$AUTOPLAY_OK" -eq 1 && "$PRACTICE_OK" -eq 1 ]]; then
        echo "[practice_determinism] provenance OK"
    else
        echo "[practice_determinism] provenance VOID" >&2
    fi
else
    echo "[practice_determinism] no log directory found; cannot check provenance" >&2
fi

echo "[practice_determinism] driver exit status: $DRIVER_STATUS"
echo "[practice_determinism] results: $JSON (end-of-run), $JSONL (per-step)"
exit "$DRIVER_STATUS"
