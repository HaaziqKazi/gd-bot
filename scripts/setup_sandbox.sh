#!/usr/bin/env bash
# Build the sandboxed Geometry Dash used for training, from the real install.
#
# The sandbox is a full copy of a *Geode-patched* GD bundle. It has to be a copy
# of a patched one, because Geode injects by rewriting Contents/Frameworks/
# libfmod.dylib to load @rpath/Geode.dylib -- there is no Geode entry in the main
# binary's load commands. Copying the bundle carries that patch along; installing
# Geode into a fresh copy afterwards would also work but is more steps.
#
# Everything this creates is gitignored and disposable. Rerun it any time the
# sandbox gets into a bad state.
#
# Usage:
#   ./scripts/setup_sandbox.sh                 # default Steam location
#   ./scripts/setup_sandbox.sh --source <app>  # explicit .app path
#   ./scripts/setup_sandbox.sh --force         # replace an existing sandbox
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

SOURCE_APP="$HOME/Library/Application Support/Steam/steamapps/common/Geometry Dash/Geometry Dash.app"
REAL_SAVE="$HOME/Library/Application Support/GeometryDash"
DEST_APP="$ROOT/sandbox/Geometry Dash.app"
SANDBOX_HOME="$ROOT/sandbox/home"
PROFILE_NAME="gdrl-sandbox"
FORCE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --source) SOURCE_APP="$2"; shift 2 ;;
        --force)  FORCE=1; shift ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 1 ;;
    esac
done

say() { printf '\n[setup] %s\n' "$*"; }

# ---------------------------------------------------------------- preflight
say "checking source install"
if [[ ! -d "$SOURCE_APP" ]]; then
    echo "no GD app bundle at:" >&2
    echo "  $SOURCE_APP" >&2
    echo "Install Geometry Dash via Steam, or pass --source <path to .app>." >&2
    exit 1
fi

if [[ ! -f "$SOURCE_APP/Contents/Frameworks/Geode.dylib" ]]; then
    echo "source install has no Geode.dylib -- Geode is not installed into it." >&2
    echo "Install Geode first (https://geode-sdk.org), launch GD once to confirm" >&2
    echo "it loads, then rerun this script." >&2
    exit 1
fi
echo "  source ok, Geode present"

if pgrep -f "Geometry Dash.app/Contents/MacOS/Geometry Dash" >/dev/null 2>&1; then
    echo "a Geometry Dash process is running -- quit it first so its save is" >&2
    echo "flushed and not copied mid-write." >&2
    exit 1
fi

if [[ -d "$DEST_APP" && $FORCE -eq 0 ]]; then
    echo "sandbox already exists at $DEST_APP" >&2
    echo "pass --force to replace it." >&2
    exit 1
fi

# ---------------------------------------------------------------- backups
# Cheap insurance, taken before anything else touches the machine. The real
# profile represents actual playtime and is not reproducible.
if [[ -f "$REAL_SAVE/CCGameManager.dat" ]]; then
    STAMP="$(date +%Y%m%d-%H%M%S)"
    for dir in "$ROOT/backups/$STAMP" "$HOME/.gd-save-backups/$STAMP"; do
        mkdir -p "$dir"
        cp "$REAL_SAVE/CCGameManager.dat" "$REAL_SAVE/CCLocalLevels.dat" "$dir/" 2>/dev/null || true
    done
    say "backed up real saves to backups/$STAMP and ~/.gd-save-backups/$STAMP"
else
    say "no real save found to back up (fresh GD install?)"
fi

# ---------------------------------------------------------------- copy
say "copying app bundle (~600MB, takes a moment)"
rm -rf "$DEST_APP"
mkdir -p "$ROOT/sandbox"
ditto "$SOURCE_APP" "$DEST_APP"
echo "  copied"

# ---------------------------------------------------------------- strip mods
# Gameplay mods are not wanted in a training instance. Eclipse Menu especially:
# it would contend with us for control of the update loop. Globed is networked.
say "stripping gameplay mods from the sandbox"
rm -f  "$DEST_APP/Contents/geode/mods/"*.geode           2>/dev/null || true
rm -rf "$DEST_APP/Contents/geode/unzipped/"*             2>/dev/null || true
rm -rf "$DEST_APP/Contents/geode/logs/"*                 2>/dev/null || true
rm -rf "$DEST_APP/Contents/geode/crashlogs/"*            2>/dev/null || true
rm -rf "$DEST_APP/Contents/geode/config/"*               2>/dev/null || true
echo "  mods dir now: $(ls -A "$DEST_APP/Contents/geode/mods" 2>/dev/null | wc -l | tr -d ' ') entries"

# ---------------------------------------------------------------- sandbox home
# GD resolves its save dir through NSSearchPathForDirectoriesInDomains, which
# honours CFFIXED_USER_HOME (not $HOME alone -- see README). run_sandbox.sh sets
# both; this just makes sure the tree exists.
say "creating redirected home"
mkdir -p "$SANDBOX_HOME/Library/Application Support/GeometryDash"
mkdir -p "$SANDBOX_HOME/Library/Caches"
echo "  $SANDBOX_HOME"

# ---------------------------------------------------------------- geode profile
# Makes `geode build` install into the sandbox instead of the live Steam install.
say "registering Geode CLI profile '$PROFILE_NAME'"
if command -v geode >/dev/null 2>&1; then
    if geode profile list 2>/dev/null | grep -q "$PROFILE_NAME"; then
        geode profile remove "$PROFILE_NAME" >/dev/null 2>&1 || true
    fi
    geode profile add --name "$PROFILE_NAME" "$DEST_APP" 2>&1 | tail -1
    geode profile switch "$PROFILE_NAME" >/dev/null 2>&1 || true
    geode profile list 2>/dev/null | sed 's/^/  /'
else
    echo "  geode CLI not found -- see README prerequisites" >&2
fi

# ---------------------------------------------------------------- done
cat <<EOF

[setup] sandbox ready.

Next:
  export GEODE_SDK=~/.geode-sdk
  (cd mod && geode build)
  ./scripts/phase0_launch_test.sh

EOF
