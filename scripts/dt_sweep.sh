#!/usr/bin/env bash
# GDRL_ENV_DELTA_TICKS sweep — task #24.
#
# One launch per N: g_envDeltaT (telemetry.cpp:196) is a namespace-scope const
# read once at load and has no wire field, so N cannot be varied within a
# process. Every launch must hit the same binary or the sweep is void.
#
# Input comes from GDRL_INJECT_SEQ (the mod drives its own 12-jump trajectory);
# passive_responder.py attaches only so the ENV path is exercised and the mod
# does not time out and free-run. An attempt with timeouts>0 is not admissible.
#
# Deliberately does NOT set GDRL_DELTA_TICKS / GDRL_ADAPTIVE: those are the EXP
# path's dt rewriter and telemetry.cpp:1363 warns the two do not compose.
set -uo pipefail

ROOT=/Users/rexouyang/Desktop/PersonalProject/gd-bot
LOGDIR="$ROOT/sandbox/Geometry Dash.app/Contents/geode/logs"
SEQ="325,712,1074,1162,1266,1798,1934,2154,2318,2482,2686,2878"
WANT=${WANT:-3}          # attempts per N
cd "$ROOT"

echo "binary: $(shasum -a 256 mod/build/gdrl.probe.geode | cut -c1-16)"
echo

run_one() {
  local N="$1" label="$2"
  pkill -f "MacOS/Geometry Dash" 2>/dev/null; sleep 2; rm -rf sandbox/.gd.lock

  GDRL_ENV=1 GDRL_ENV_DELTA_TICKS="$N" GDRL_EXP=1 \
  GDRL_AUTOPLAY=1 GDRL_PIN_LEVEL=1 GDRL_BLOCK_INPUT=1 \
  GDRL_INJECT_SEQ="$SEQ" \
  ./scripts/run_sandbox.sh >/tmp/dtsweep_game.out 2>&1 &
  local GPID=$!

  sleep 12                                     # let the shm exist
  python3 trainer/passive_responder.py 90 >/tmp/dtsweep_resp.out 2>&1 &
  local RPID=$!

  local NEW t0 elapsed n
  t0=$(date +%s)
  for _ in $(seq 1 22); do
    sleep 5
    NEW=$(ls -t "$LOGDIR" | head -1)
    n=$(grep -c 'gdrl\] SEQ ' "$LOGDIR/$NEW" 2>/dev/null | tr -d ' ')
    [ "${n:-0}" -ge "$WANT" ] 2>/dev/null && break
  done
  elapsed=$(( $(date +%s) - t0 ))

  kill $RPID 2>/dev/null; kill $GPID 2>/dev/null
  pkill -f "MacOS/Geometry Dash" 2>/dev/null; sleep 2; rm -rf sandbox/.gd.lock

  NEW=$(ls -t "$LOGDIR" | head -1)
  local ap prov
  ap=$(grep -c 'autoplay ->' "$LOGDIR/$NEW" 2>/dev/null | tr -d ' ')
  prov=$([ "${ap:-0}" -ge 1 ] && echo OK || echo VOID)

  echo "----- N=$N ($label)  log=$NEW  provenance=$prov  wall=${elapsed}s"
  if [ "$prov" = VOID ]; then echo "   VOID: no autoplay line; discard"; return; fi
  grep -h 'gdrl\] SEQ ' "$LOGDIR/$NEW" 2>/dev/null \
    | grep -o 'maxX=[0-9.]* deathTick=[0-9]* *t=[0-9.]*' | sort | uniq -c
  grep -h 'gdrl\] ENV a=' "$LOGDIR/$NEW" 2>/dev/null | tail -1 \
    | grep -o 'steps=[0-9]* *frames=[0-9]* *endTick=[0-9]*.*' | head -1
  grep -h 'gdrl\] CLKSUM' "$LOGDIR/$NEW" 2>/dev/null | tail -1 \
    | grep -o 'finalTick=[0-9]* *frames=[0-9]*'
  grep -c 'ENV both GDRL_ENV_DELTA_TICKS' "$LOGDIR/$NEW" 2>/dev/null \
    | tr -d ' ' | sed 's/^/   compose-warning-lines: /'
  echo
}

# SPEC overrides the sweep list, e.g. SPEC="8:control-first 16: 32: 64: 8:control-last".
# The default is the full ladder from 1. Always keep a repeated control at both
# ends: it separates between-process drift from a real divergence.
SPEC=${SPEC:-"1:control-first 2: 4: 8: 16: 32: 1:control-last"}
for spec in $SPEC; do
  run_one "${spec%%:*}" "${spec#*:}"
done
echo "binary at end: $(shasum -a 256 mod/build/gdrl.probe.geode | cut -c1-16)"
