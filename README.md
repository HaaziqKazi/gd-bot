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

## Launch gotcha: Steam must be running

Separate from the working-directory issue, and with an identical symptom. GD links
`libsteam_api` and calls `SteamAPI_Init` at startup; with Steam not running that
fails and the process exits a few hundred ms in:

```
[S_API FAIL] SteamAPI_Init() failed; ipcserver GetSteamPath failed.
[S_API] SteamAPI_Init(): did not locate a running instance of Steam.
```

The Geode log again looks perfectly healthy — it reaches `Loading early mods`,
`Continuing next frame...`, `Entry took 0.007s`, and simply stops. Nothing in it
suggests Steam. `run_sandbox.sh` now preflights this and fails with a clear
message.

**This is a real constraint on unattended training:** Steam has to be up. For a
headless rig the fix is to stub `libsteam_api.dylib`, then set
`GDRL_SKIP_STEAM_CHECK=1`.

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

## Physics is fixed-step at 240Hz (determinism holds)

`GJBaseGameLayer::update(float dt)` receives **real wall-clock frame time**, which
jitters (~0.0084s on a 120Hz display). That looked like a threat to determinism —
the entire search-and-replay plan needs the same inputs to give the same result.

They do. GD accumulates the variable render `dt` and consumes it in fixed
**1/240s** physics steps. Measured over 37 consecutive attempts of an identical
input sequence (no input at all) on Stereo Madness:

```
maxX   = 507.615234375   bit-identical across all 37
t      = 1.629166752     bit-identical (= 391/240 exactly)
frames = 316 .. 440      render frames vary by ~40%
dt max = 0.0159 .. 0.0332  including a 4x frame hitch
```

Directly observable in the per-tick data: `t` advances in exact 1/240 increments
and x by exactly `1.298250437` per tick (≈311.6 units/s at "1x", which is
`m_playerSpeed = 0.90` internally).

**Consequence for input injection:** the render:physics ratio is *not* constant —
316–440 render frames covered the same 391 physics ticks. Inputs must therefore be
applied per **physics tick**, not per render frame, or the same intended input
sequence will land on different ticks between runs and reintroduce the
nondeterminism the engine itself does not have.

Still unverified: longer trajectories, trigger-heavy levels, determinism *across
processes* (needed for parallel workers), and sequences that actually contain
inputs.

## The null-input guard: "no input" is not free

Every determinism result above rests on *no input at all* being a perfectly
repeatable input sequence. On a machine anyone is using, it is not. GD captures
the keyboard and mouse whenever it has focus, and one stray click is a jump.

This contaminated four consecutive runs before it was noticed: **195 button
events**, with attempts sailing past the first spike to `x=3790` and `x=7967`
instead of dying at `507.615234375`. It reads as a physics anomaly and is nothing
of the kind. It was found only by hooking `pushButton` and logging every event.

So the premise is now enforced rather than assumed. With `GDRL_BLOCK_INPUT=1` the
probe drops every button-down at `GJBaseGameLayer::handleButton`, and every
`ATTEMPT` line carries its own verdict:

```
input[clean blocked=0 leaked=0]        usable as null-input evidence
input[INVALID blocked=12 leaked=3]     a push got through — attempt is void
input[UNGUARDED blocked=0 leaked=0]    blocking off — not trusted either way
```

Pushes are dropped, releases are not: GD calls `releaseButton` internally on
death and reset (31 call sites against 3 for `pushButton`), and swallowing those
risks latching a button down. `pushButton` is hooked as a second line of defence
— a push that reaches the player anyway arrived by an unmapped route and marks
the attempt `INVALID` rather than being silently absorbed.

**A null-input measurement that does not assert `leaked=0` is not evidence.**

## Simulation rate is ours to set (and the ceiling is the respawn animation)

`GJBaseGameLayer::update(float dt)` receives real frame time and does the delta
accumulation *inline*. Rewriting `dt` on the way in therefore controls how much
simulated time each rendered frame consumes — and the outcome is invariant under
it. Measured over **91 attempts in three regimes, every one bit-identical** at
`maxX=507.615234375`, `t=1.629166752`, every one `leaked=0`:

| `dt` fed to `update` | frames/attempt | attempts/sec | attempts |
|---|---|---|---|
| passthrough (real) | ~160 | 0.35 | 19 |
| `8/240` = 0.033333 | ~112 | 0.56 | 28 |
| `32/240` = 0.133333 | ~76 | 0.88 | 44 |

The frame cost fits exactly, at k = 1, 2 and 8 with no residual:

```
frames = 96/k + 64
```

96 frames of simulation that scale with `dt`, and **64 frames (~1.07s at 60fps)
of fixed death/respawn animation that does not**. That fixed cost is the
throughput ceiling, not `dt`: at k=32 the simulation is already down to 12 frames
and 84% of each attempt is animation, which is why 8× `dt` bought only 2.55×
throughput.

### Cutting the respawn: `GDRL_FAST_RESET=1`

`destroyPlayer` sets `m_inResetDelay` and arms a delayed reset (there is an
`fmov s0, #1.0` immediately before a call in its body, matching the ~1.07s).
`delayedResetLevel` has no `bl` call sites — it is reached through a scheduled
selector, so the delay is not a constant worth patching. Forcing `resetLevel()`
as soon as `m_inResetDelay` is observed skips the wait entirely:

| config | frames/attempt | attempts/sec |
|---|---|---|
| baseline (real `dt`) | ~160 | 0.35 |
| `dt` = 32/240 | ~76 | 0.88 |
| `dt` = 32/240 + fast reset | **15** | **3.9 – 4.1** |

**~11.5× the original rollout rate**, still bit-identical: 175 consecutive
attempts at `maxX=507.615234375`, `t=1.629166752`, every one `input[clean]`.
`fastResets` equalled the attempt count exactly, so GD's own delayed callback
never fired a competing second reset.

Next lever is parallel instances; there is no longer meaningful fixed overhead
left to remove in a single one.

## Inlined bindings: an address is not a call site

Three functions named in earlier plans here are **not hookable in gameplay on
macOS arm64**, despite Geode reporting `Enabled ... hook` at addresses that
reconcile exactly with the 2.2081 bindings. The detours simply never execute.
Confirmed by counting call sites in the shipped arm64 slice:

| symbol | `bl` call sites | status |
|---|---|---|
| `GJBaseGameLayer::processCommands` | **0** | inlined everywhere; not callable |
| `GJBaseGameLayer::getModifiedDelta` | **1** | only `LevelEditorLayer::updateEditor` |
| `GJBaseGameLayer::handleButton` | 2 | live (the input dispatcher) |
| `PlayerObject::pushButton` | 3 | live |
| `GJBaseGameLayer::queueButton` | 10 | live |
| `PlayerObject::releaseButton` | 31 | live |

`processCommands` looked like the per-tick hook the 240Hz result called for — it
even takes `isHalfTick`/`isLastTick`. It does not exist as a callable function in
gameplay. `getModifiedDelta` looked like the way to control `dt`; it survives
only on the editor path. Both were abandoned for `update`'s own `dt` argument.

**A binding carrying an `m1` address means the function *exists*, not that
anything *calls* it.** Check call sites before designing around a hook:

```sh
otool -arch arm64 -tV "Geometry Dash" > gd.asm
grep -cP '\tbl\t0x124490$' gd.asm      # 0 == inlined, do not bother
```

## Correction: `m_currentStep` is not the physics-tick counter

It does not advance during gameplay. `m_currentStep` read `0` on every frame of
**all 91 clean attempts**, spanning thousands of `update` calls, while `t`
advanced normally in exact 1/240 increments.

The earlier claim that it is the tick counter was inferred from its adjacency to
`m_randomSeed`, `m_replayRandSeed` and `m_queuedButtons` in the bindings — never
measured. Adjacency is not semantics. This matters because it was to be the
tick-exact attribution channel for input placement, and `PlayerButtonCommand`
carries an `m_step` field that is presumably keyed to the same counter and is
therefore suspect for the same reason.

Tick-exact input placement still needs a clock. `m_attemptTime` advances in exact
1/240 steps and is the obvious candidate, but it has not been verified as a
placement key.

## Conditioning state: the eight vehicles and their modifiers

GD is eight games sharing a renderer. The same geometry is lethal in cube and
irrelevant in ship; a gravity flip inverts what "up" means. A policy therefore
cannot use one static action mapping — it has to be conditioned on the active
physics regime. The mod emits that regime as a `COND` line whenever any axis of
it changes, plus a `MODE` line from `PlayerObject::switchedToMode` for the
transition itself.

Fields, all read live off `PlayerObject` / `GJBaseGameLayer` rather than inferred
from portals passed, so they stay correct through triggers and respawns:

| Axis | Source | Notes |
|---|---|---|
| Vehicle | `m_isShip` `m_isBird` `m_isBall` `m_isDart` `m_isRobot` `m_isSpider` `m_isSwing` | cube is the absence of all seven; `m_isBird` = UFO, `m_isDart` = wave |
| Gravity | `m_isUpsideDown`, `m_gravity` | flag plus continuous multiplier |
| Size | `m_vehicleSize` | 1.0 normal, 0.6 mini |
| Speed | `m_playerSpeed` | 0.9 at "1x" |
| Global rate | `m_gameState.m_timeWarp` | independent of player speed |
| Dual | `m_gameState.m_isDualMode` | **not** `m_player2` — see below |
| Misc | `m_isSideways` `m_isDashing` `m_isOnGround` `m_isPlatformer` | |

**`m_player2` is not a dual-mode test.** GD allocates the second `PlayerObject`
unconditionally and hides it outside dual sections, so `m_player2 != nullptr` is
true on every level. It reported `dual=1` on Stereo Madness on the first run.
`m_gameState.m_isDualMode` is the real flag.

**Baselines are not 1.0.** Normal gravity reads `m_gravity = 0.96`, the same way
"1x" speed reads `m_playerSpeed = 0.90`. Both are normalised against their
measured baseline on the Python side rather than against 1.0.

**Vehicle flags are checked derived-first.** Swing is ship-like and spider is
ball-like, and if GD leaves a parent flag set while a child mode is active,
testing the parent first silently yields the wrong label — a wrong conditioning
input that still trains. `deriveVehicle` orders the checks defensively and the
probe logs an error if more than one vehicle flag is ever set at once, so the
ordering cannot quietly paper over an overlap. No overlap observed so far, but
only cube has actually been reached: with no input the player dies at the first
spike, so every non-cube mode is unverified until input injection exists.

**`m_currentStep` is the physics-tick counter.** It sits next to `m_randomSeed`,
`m_replayRandSeed` and `m_queuedButtons` (`gd::vector<PlayerButtonCommand>`, fed
by `queueButton(button, push, isPlayer2, timestamp)`) — i.e. the timestamped
input-injection path and the seed state needed for cross-process determinism are
all in that one region of `GJBaseGameLayer`.

## Two probe bugs worth not repeating

Both produced confident, plausible-looking numbers from a broken measurement.

**Reading state after delegating.** Hooking `destroyPlayer` and reading position
*after* calling the original reports the respawn point, not the death — every
attempt showed an identical `x=1.298` and `t=1/240`, which reads as a flawless
determinism result and is actually just measuring the reset. Snapshot before
delegating.

**`getMainLevel(id, dontGetLevelString)`.** Passing `true` yields a level object
with no content — 2 objects, `levelLength` 793 instead of 26724. It loads, it
runs, and it produces measurements that look real. `main.cpp` now hard-errors when
a level has fewer than 10 objects.

**`maxX` was a function of the frame rate, not the simulation.** It was sampled
once per render frame in `update()`. That agreed with itself across every run
until the respawn animation was skipped — which deleted the post-death frames
that had been quietly capturing the true endpoint — and `maxX` dropped to
`498.527496338`, short by `9.087738037` units. That is **6.999988 physics
ticks**: at 32 ticks/frame the player advances ~41.5 units between samples, so
the last pre-death sample missed the end. `t` was bit-identical throughout,
because it comes from GD's own accumulator rather than from our sampling.

The metric now snapshots the player position at the attempt boundary, before
delegating. The general trap: a per-frame sample of a per-tick quantity looks
stable exactly as long as the frame rate is stable, and silently becomes a
different measurement the moment anything touches timing.

`destroyPlayer` itself proved unusable as a death signal: it fires every physics
tick with a constant killer id, suggesting a mis-mapped binding. Attempt
boundaries are measured at `resetLevel` instead, which needs no assumptions about
GD's death plumbing.

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
