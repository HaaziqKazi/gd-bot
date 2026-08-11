#!/usr/bin/env bash
# Launch the sandboxed Geometry Dash, isolated from the real install.
#
# Four things here are load-bearing and all were established the hard way:
#
# 1. CWD must be Contents/Resources. The bundle ships steam_appid.txt there and
#    SteamAPI_Init looks for it in the *process working directory*. Launched via
#    Steam or LaunchServices that CWD is set for us; from a shell it is inherited,
#    the appid check fails, SteamAPI_RestartAppIfNecessary asks Steam to relaunch,
#    and the process quits ~300ms in -- *after* Geode has loaded and hooked, so
#    the Geode log looks perfectly healthy right up to the point it vanishes.
#
# 2. HOME alone does NOT redirect saves. Verified: the process environment carried
#    the sandbox path while cocos still resolved the real home, because
#    NSSearchPathForDirectoriesInDomains goes through the user's account record
#    rather than $HOME. CFFIXED_USER_HOME is the CoreFoundation-level override
#    those APIs do consult. Both are set.
#
#    Caveat: cfprefsd ignores CFFIXED_USER_HOME, so anything going through
#    `defaults` / CFPreferences still escapes to the real home. File I/O is
#    redirected; the preferences system is not.
#
# 3. Steam must be running, or GD exits a few hundred ms in with the same
#    deceptive signature as (1). Preflighted below.
#
# 4. The game must not be occluded. App Nap suppression alone (GDRL_NO_APP_NAP)
#    got an unfocused GD from zero attempts to ~3 in 93s -- running, but ~12x
#    slow. The residual throttle is occlusion: GD starts fullscreen, which puts
#    it on its own Space, and macOS stops refreshing occluded windows regardless
#    of App Nap. GDRL_WINDOWED keeps it a normal window on the current Space,
#    which restores the full 0.41 attempts/sec while leaving the screen usable.
#
# Env:
#   GDRL_HOME             override the sandbox home (for parallel instances)
#   GDRL_AUTOPLAY=1       drive straight into a level
#   GDRL_NO_APP_NAP=1     hold an NSProcessInfo activity (default on here)
#   GDRL_WINDOWED=1       force windowed instead of fullscreen (default on here)
#   GDRL_SKIP_STEAM_CHECK=1  bypass the Steam preflight
#   GDRL_NO_LOCK=1        bypass the single-instance slot lock
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
APP="$ROOT/sandbox/Geometry Dash.app"
SANDBOX_HOME="${GDRL_HOME:-$ROOT/sandbox/home}"
LOCK="$ROOT/sandbox/.gd.lock"

if [[ ! -d "$APP" ]]; then
    echo "no sandbox app at: $APP" >&2
    echo "create it with: ./scripts/setup_sandbox.sh" >&2
    exit 1
fi

# --- Steam preflight -------------------------------------------------------
if [[ "${GDRL_SKIP_STEAM_CHECK:-0}" != "1" ]] && ! pgrep -x steam_osx >/dev/null 2>&1; then
    echo "Steam is not running -- GD will exit silently a few hundred ms after launch." >&2
    echo "Start it with:  open -a Steam" >&2
    echo "(then wait for it to finish signing in before relaunching)" >&2
    exit 1
fi

# --- Single-instance slot --------------------------------------------------
# GD is an exclusive resource until parallel instances land: two of them fight
# over the sandbox save dir, the bundle-relative Geode log directory, and the
# window. When that happened before, *both* sides' measurements were silently
# worthless rather than obviously broken. Fail loudly instead.
#
# mkdir is the atomic primitive here because macOS ships no flock(1).
if [[ "${GDRL_NO_LOCK:-0}" != "1" ]]; then
    if ! mkdir "$LOCK" 2>/dev/null; then
        holder="$(cat "$LOCK/pid" 2>/dev/null || true)"
        if [[ -n "$holder" ]] && kill -0 "$holder" 2>/dev/null; then
            echo "GD slot is held by pid $holder (since $(cat "$LOCK/since" 2>/dev/null || echo '?'))." >&2
            echo "Wait for it, or set GDRL_NO_LOCK=1 if you are certain." >&2
            exit 1
        fi
        echo "[run] clearing stale lock (holder ${holder:-unknown} is gone)" >&2
        rm -rf "$LOCK"
        mkdir "$LOCK"
    fi
    date +%Y-%m-%dT%H:%M:%S > "$LOCK/since"
    trap 'rm -rf "$LOCK"' EXIT INT TERM
fi

mkdir -p "$SANDBOX_HOME/Library/Application Support/GeometryDash"

echo "[run] app  : $APP"
echo "[run] HOME : $SANDBOX_HOME"
echo "[run] logs : $APP/Contents/geode/logs"

cd "$APP/Contents/Resources"

# Not exec'd: the lock needs a live parent to clean up after, and the pid file
# has to name the process a would-be second launcher should wait on.
env HOME="$SANDBOX_HOME" CFFIXED_USER_HOME="$SANDBOX_HOME" \
    GDRL_NO_APP_NAP="${GDRL_NO_APP_NAP:-1}" \
    GDRL_WINDOWED="${GDRL_WINDOWED:-1}" \
    "$APP/Contents/MacOS/Geometry Dash" "$@" &
GD_PID=$!
[[ "${GDRL_NO_LOCK:-0}" != "1" ]] && echo "$GD_PID" > "$LOCK/pid"

wait "$GD_PID"
