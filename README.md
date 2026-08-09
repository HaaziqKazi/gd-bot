# gd-rl

An RL environment for Geometry Dash, built on a Geode mod that reads live game
state out of the running process.

## Setting up from scratch

### Prerequisites

1. **Geometry Dash installed via Steam** (appid `322170`).
2. **Geode installed into that copy** — <https://geode-sdk.org>. Launch GD once
   and confirm it loads. This is not optional: the sandbox is a copy of a
   Geode-*patched* bundle, and the patch (a rewritten `libfmod.dylib`) is the
   entire injection vector. A copy of an unpatched install will not load mods.
3. Xcode Command Line Tools (`xcode-select --install`).

### Bootstrap

**The order matters.** `geode sdk install` fails with *"No Geode profiles found"*
until a profile exists, and the profile points at the sandbox — so the sandbox
must be created first.

```sh
# 1. build tooling
brew install cmake ninja
brew install geode-sdk/geode/geode-cli

# 2. create the sandbox AND register the 'gdrl-sandbox' profile
./scripts/setup_sandbox.sh

# 3. now the SDK will install (needs the profile from step 2)
geode sdk install ~/.geode-sdk
export GEODE_SDK=~/.geode-sdk       # add this to your shell rc
geode sdk install-binaries

# 4. build the mod and verify end-to-end
(cd mod && geode build)
./scripts/phase0_launch_test.sh
```

Expect the **first** build to take several minutes: it clones the bindings repo
via CPM, downloads and runs codegen for GD `2.2081`, and compiles everything twice
(universal). Incremental rebuilds after editing `main.cpp` take seconds.

A successful `phase0_launch_test.sh` prints `probe mod loaded`, an
`Enabled ... hook` line, and `REAL SAVE UNCHANGED [ok]`.

### Verifying the SDK matches the game

The Geode loader installed in the game and the SDK you build against must agree,
or the mod will not load:

```sh
geode sdk version                                        # SDK
python3 -c "import json;print(json.load(open('$(echo ~)/Library/Application Support/Steam/steamapps/common/Geometry Dash/Geometry Dash.app/Contents/geode/resources/geode.loader/mod.json'))['version'])"
```

Both were `5.8.2` here. If they differ, update whichever is behind, and update the
`geode` field in `mod/mod.json` to match.

## Layout

```
gd-rl/
  mod/            Geode mod (C++) — runs inside GD, reads state, injects input
    src/main.cpp
    CMakeLists.txt
    mod.json
  scripts/        Launchers and verification harnesses  [tracked]
    run_sandbox.sh
    logs.sh
    phase0_launch_test.sh
  trainer/        Python side (env wrapper, training)
  sandbox/        Isolated copy of GD used for training  [gitignored, generated]
    Geometry Dash.app
    home/         Redirected home so saves never touch the real install
  backups/        Copies of the real save files          [gitignored, personal]
```

`sandbox/` and `backups/` are deliberately untracked: the former is a ~400MB copy
of the game, the latter contains the real GD profile. Everything under `scripts/`
is source and is tracked — do not put scripts inside `sandbox/`, they will be
silently excluded.

## Environment facts

These were determined empirically on this machine; several are not what the
obvious guess would be.

| Thing | Value |
|---|---|
| GD version (Info.plist) | `2.208` |
| GD version **string mods must declare** | `2.2081` |
| Geode loader in game | `5.8.2` |
| Geode SDK | `5.8.2` (`~/.geode-sdk`, `GEODE_SDK`) |
| GD binary | universal — `x86_64 arm64` |
| Geode.dylib | universal — `x86_64 arm64` |
| App Sandbox entitlement | none |
| GD Steam appid | `322170` |

**Geode injection vector.** Geode does *not* appear in the main binary's load
commands. It patches `Contents/Frameworks/libfmod.dylib` to load
`@rpath/Geode.dylib`, preserving the pristine fmod as `restore_fmod.dylib`. This
survives a bundle copy, which is what makes the sandbox possible.

**Codesign.** `codesign -v` reports "a sealed resource is missing or invalid" on
both the sandbox copy *and the original*. That is pre-existing — Geode modified
the bundle after RobTop signed it. Not a problem, and not caused by the copy.

**Where Geode keeps things.** Mostly inside the app bundle, which is why copying
the bundle isolates them:

- `Contents/geode/mods/*.geode` — installed mod packages (install target)
- `Contents/geode/unzipped/<id>/` — extracted mods
- `Contents/geode/logs/` — **runtime logs; primary debugging channel**
- `Contents/geode/crashlogs/`
- `~/Library/Application Support/GeometryDash/geode/mods/<id>/` — per-mod saved
  data and settings (redirected by the `HOME` override)

## Launch gotcha: working directory

`exec`ing the binary from a shell makes GD exit a few hundred ms after start.
Cause: the bundle ships `steam_appid.txt` in `Contents/Resources`, and
`SteamAPI_Init` looks for it in the *process working directory*. Launched through
Steam or LaunchServices that CWD is set for us; from a shell it is inherited, the
appid check fails, `SteamAPI_RestartAppIfNecessary` asks Steam to relaunch, and
the process quits.

This is nastier than it sounds, because the exit happens *after* Geode has loaded
and installed its hooks — so the Geode log looks completely healthy right up to
the point where the process vanishes. `run.sh` therefore `cd`s to
`Contents/Resources` before exec.

## Save isolation: `HOME` is not enough

Setting only `HOME` does **not** redirect GD's save directory. Verified directly:
the process environment carried the sandbox path while cocos still resolved the
real home, because `NSSearchPathForDirectoriesInDomains` goes through the user's
account record rather than `$HOME`.

`CFFIXED_USER_HOME` is the CoreFoundation-level override those APIs do consult.
`run.sh` sets both. Confirmed by having the mod log the paths the game itself
resolved:

```
HOME            = .../sandbox/home
writable path   = .../sandbox/home/Library/Caches/
writable path 2 = .../sandbox/home/Library/Caches/
mod save dir    = .../sandbox/home/Library/Application Support/GeometryDash/...
```

Note `getWritablePath2()` is RobTop's addition rather than stock cocos. Prefer
asking the game where it will write over watching for writes — a short run that
happens not to save is indistinguishable from successful isolation.

## Build gotcha: architecture

The stock Geode mod template produces an **x86_64-only** dylib on this machine,
which cannot load into the arm64 GD process — and fails *silently*, looking
identical to "mod not loading for some unknown reason".

Cause: the template calls `add_library()` before
`add_subdirectory($ENV{GEODE_SDK})`. Geode's `Platform.cmake` sets the macOS
defaults (universal, deployment target 11.0), but only inside the SDK's own
directory scope, which is processed after our target already exists. The target
therefore kept the toolchain default deployment target of 10.15 — which predates
Apple Silicon — so CMake resolved the empty architecture list to x86_64 alone.

Fix, at the top of `mod/CMakeLists.txt` before `project()`:

```cmake
set(CMAKE_OSX_ARCHITECTURES "arm64;x86_64" CACHE STRING "" FORCE)
set(CMAKE_OSX_DEPLOYMENT_TARGET "11.0" CACHE STRING "" FORCE)
```

Always verify after building:

```sh
T=$(mktemp -d); unzip -oq mod/build/gdrl.probe.geode -d "$T"
lipo -archs "$T/gdrl.probe.dylib"     # expect: x86_64 arm64
```

## Build and run

```sh
export GEODE_SDK=~/.geode-sdk
cd mod && geode build          # -> build/gdrl.probe.geode
```

The Geode CLI profile `gdrl-sandbox` is registered and set as default, so builds
install into the sandbox rather than the live Steam install.

```sh
./scripts/run_sandbox.sh              # launch, isolated from the real install
GDRL_AUTOPLAY=1 ./scripts/run_sandbox.sh   # drive straight into a level
./scripts/logs.sh -f                  # follow the Geode log
./scripts/logs.sh -g gdrl             # grep the log for our mod's output
./scripts/phase0_launch_test.sh       # verify mod loads + saves are isolated
```

## Safety notes

- Real save data (`CCGameManager.dat`, `CCLocalLevels.dat`) is backed up to
  `backups/<timestamp>/` and to `~/.gd-save-backups/<timestamp>/`.
- The sandbox has all gameplay mods stripped (Globed, Eclipse Menu, GDDL, etc.).
  Eclipse Menu in particular would contend with us for control of the update loop.
- Nothing in this project should ever write to the Steam install.
