#!/usr/bin/env bash
# Launch the sandboxed Geometry Dash, isolated from the real install.
#
# Two things are load-bearing here and both were established the hard way:
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
# Geode's own state (mods, logs, crashlogs) lives inside the .app bundle and is
# therefore already isolated by virtue of this being a copy.
#
# Env:
#   GDRL_HOME      override the sandbox home (used for parallel instances)
#   GDRL_AUTOPLAY  set to 1 to have the mod drive straight into a level
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
APP="$ROOT/sandbox/Geometry Dash.app"
SANDBOX_HOME="${GDRL_HOME:-$ROOT/sandbox/home}"

if [[ ! -d "$APP" ]]; then
    echo "no sandbox app at: $APP" >&2
    echo "recreate it with: ditto \"<steam>/Geometry Dash.app\" \"$APP\"" >&2
    exit 1
fi

# GD links libsteam_api and calls SteamAPI_Init on startup. With Steam not
# running that fails and the process exits a few hundred ms in -- again *after*
# Geode has loaded, hooked, and logged a healthy startup, so the Geode log gives
# no hint. Fail loudly here instead of letting it look like a mod problem.
#
# This is a real constraint on unattended training: Steam must be running. The
# eventual fix for a headless rig is to stub libsteam_api.dylib, at which point
# set GDRL_SKIP_STEAM_CHECK=1.
if [[ "${GDRL_SKIP_STEAM_CHECK:-0}" != "1" ]] && ! pgrep -x steam_osx >/dev/null 2>&1; then
    echo "Steam is not running -- GD will exit silently a few hundred ms after launch." >&2
    echo "Start it with:  open -a Steam" >&2
    echo "(then wait for it to finish signing in before relaunching)" >&2
    exit 1
fi

mkdir -p "$SANDBOX_HOME/Library/Application Support/GeometryDash"

echo "[run] app  : $APP"
echo "[run] HOME : $SANDBOX_HOME"
echo "[run] logs : $APP/Contents/geode/logs"

cd "$APP/Contents/Resources"

exec env HOME="$SANDBOX_HOME" CFFIXED_USER_HOME="$SANDBOX_HOME" \
    "$APP/Contents/MacOS/Geometry Dash" "$@"
