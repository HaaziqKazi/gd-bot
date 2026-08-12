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

## Running unattended: two throttles, not one

An unfocused GD does not simulate, and fixing only the first cause is not enough.

**1. App Nap.** macOS throttles a backgrounded app hard. `GDRL_NO_APP_NAP=1` holds
an `NSProcessInfo` activity (`NSActivityUserInitiated | NSActivityLatencyCritical`)
for the process lifetime — `mod/src/nonap.mm`, which needs an Objective-C++ TU
because there is no C API for it.

`defaults write com.robtop.geometrydashmac NSAppSleepDisabled` does **not** work:
`cfprefsd` ignores `CFFIXED_USER_HOME`, so it writes to the *real* home and never
reaches the sandbox. File I/O is redirected; the preferences system is not.

**2. Occlusion.** GD starts fullscreen, which puts it on its own Space; switching
away makes it fully occluded, and macOS stops refreshing occluded windows no
matter what App Nap has been told. `GDRL_WINDOWED=1` calls
`PlatformToolbox::toggleFullScreen(false, false, false)` so it is an ordinary
window on the current Space.

Measured, ~90s runs on Stereo Madness, `maxX` bit-identical throughout:

| configuration | attempts | rate |
|---|---|---|
| unfocused, nothing done | 0 | player never moves |
| unfocused, App Nap suppressed, fullscreen | 3 | ~0.03/s |
| **windowed + focused** | **32** | **0.41/s** |
| focused fullscreen (baseline) | 37 | 0.41/s |

Windowed costs nothing against the baseline and leaves the screen usable.
Suppressing App Nap alone restores simulation but leaves it ~12× slow — and that
partial result is easy to misread as "the App Nap fix did not work", when in fact
a second, unrelated mechanism is doing the throttling.

## The GD slot lock

GD is an exclusive resource until parallel instances exist: two of them fight over
the sandbox save dir, the bundle-relative Geode log directory, and the window.
When that happened, *both* sides' measurements were silently worthless rather than
obviously broken.

`run_sandbox.sh` takes an atomic `mkdir` lock at `sandbox/.gd.lock` (macOS ships
no `flock(1)`), records the game's pid and start time, refuses a second launch
naming the holder, clears stale locks whose holder is gone, and releases on exit.
`GDRL_NO_LOCK=1` bypasses it.

## The census cross-check

`runCensus` walks `m_sections` — correct, because the grid is what the observation
window queries — and now also compares the total against `m_objects->count()`.

The reason is that a sparse window and a broken traversal look identical from any
single observation. A level opening is legitimately almost empty (Stereo Madness
has ~4 objects in the first 467 units), so "few objects" is not evidence of a bug
and "many objects" is not evidence of correctness. The full-walk comparison is the
one cheap check that separates them. Stereo Madness measures 2384 of 2399; the
shortfall is objects not yet sectioned. Under half logs an error.

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

### Refinement: `bl` alone under-counts, in two different directions

Counting only `bl` was right about `processCommands` and wrong about several
other functions. There are three cases, and they need different tests:

| pattern | meaning | example |
|---|---|---|
| 0 `bl`, 0 of **any** branch, non-virtual | genuinely inlined; unreachable | `processCommands` |
| 0 `bl`, ≥1 `b` | **tail-called** — entry still executes, hook fires | `triggerMoveCommand`, `createFollowCommand` |
| 0 `bl`, 0 `b`, **virtual** | dispatched via `blr` through the vtable; count says nothing | `update`, `spawnXPosition` |

`GJBaseGameLayer::triggerMoveCommand` has zero `bl` call sites and would have
been written off under the old rule. It is reached by `b 0x10cca4` from
`EffectGameObject::triggerObject` — a tail call, which still enters at
instruction 0, so a Geode entry hook on it fires normally. The same is true of
`createFollowCommand` and `createPlayerFollowCommand`. Meanwhile
`processCommands` has zero references of *any* kind, which is what actually
distinguishes it.

```sh
grep -cP '\tbl\t0x10cca4$' gd.asm   # 0  -- not the test
grep -cP '\t0x10cca4$'     gd.asm   # 1  -- tail call; it IS reachable
```

For a virtual, neither count is evidence either way; check the vtable or hook
it and log.

## The motion pipeline: what moves geometry, and when

Static level geometry is not static. A block at (X, Y) may be mid-flight on a
move trigger or scheduled to start moving when the player crosses an x well
ahead of it, which is why the section grid re-buckets during play and why a
one-shot parse of the level is not an observation. This is the map of the
system that does the moving, read out of the arm64 slice.

**It runs once per physics step, not per render frame.** `GJBaseGameLayer::update`
(m1 `0x1229e8`) contains the fixed-step loop — latch at `+0x4b0`
(`add w22,w22,#1; cmp w22,w20; b.ge <exit>`), body from `+0x4bc` — and the
whole motion pipeline sits inside it, in this order:

```
GJEffectManager::updateSpawnTriggers(dt)          update+0x694
GJEffectManager::updateTimers(dt, timeWarp)       update+0x9e8
GJEffectManager::prepareMoveActions(dt, interm.)  update+0x9f8   <- commands step here
processDynamicObjectActions(type=1, dt)           update+0xa44
processTransformActions(visibleFrame)             update+0xa50
processRotationActions()                          update+0xa58
processDynamicObjectActions(type=0, dt)           update+0xa68
processMoveActions()                              update+0xa70
processPlayerFollowActions(dt)                    update+0xa7c
processAdvancedFollowAction(...)                  [inlined loop]
processFollowActions()                            update+0xbf4
processAreaActions(dt, visibleFrame)              update+0xc04
GJEffectManager::postMoveActions()                update+0xc40
```

The identical sequence exists outlined as `GJBaseGameLayer::processMoveActionsStep`
(m1 `0x118368`), whose only caller is `loadUpToPosition` — that is the
level-seek path, not gameplay. Gameplay runs the copy inlined into `update`.

**Step count and per-step `dt`**, decompiled from `update+0x25c`:

```
step     = (timeWarp < 1) ? timeWarp/240 : 1/240        (double 0x3F71111111111111)
acc      = m_extraDelta + dt        , round-tripped through float32
n        = round(acc / step)
consumed = step * n
m_extraDelta = acc - consumed
numSteps = max(round(consumed * 60 / max(timeWarp,1) * 4), 1)
dtPerStep= consumed / numSteps
```

At `timeWarp == 1` this collapses to `numSteps == n` and `dtPerStep == 1/240`
exactly — an independent confirmation of the 240 Hz result, from the code
rather than from measurement. `dtPerStep` is what reaches `prepareMoveActions`
(register `s10`, set by `fcvt s10, d8` at `update+0x568`), so **trigger
durations are denominated in the same seconds as the tick clock**: one tick of
lookahead is 1/240 of any trigger's duration.

### The active-command struct, verified against its own code

An in-flight move / rotate / scale is a `GroupCommandObject2`. Its binding
layout is not merely plausible — the offsets that `GroupCommandObject2::step`
(m1 `0x44722c`) and `::updateAction` (m1 `0x4472fc`) load and store land
exactly on the binding's declared field order:

| offset | field | how it is used |
|---|---|---|
| `+0x0c` | `m_easingType` | `ldr w0` → arg 2 of `getEasedValue` |
| `+0x10` | `m_easingRate` | `ldr d1`, narrowed → arg 3 |
| `+0x18` | `m_duration` | divisor, clamped to `2^-23` |
| `+0x20` | `m_deltaTime` | `+= dt` every step |
| `+0x30` / `+0x38` | `m_current{X,Y}Offset` | last applied value |
| `+0x40` / `+0x48` | `m_delta{X,Y}` | this step's displacement |
| `+0x70` / `+0x71` | `m_finished` / `m_disabled` | early-outs in `step` |
| `+0x77` / `+0x78` | `m_lockedIn{X,Y}` | gate the X / Y action entirely |
| `+0x190` / `+0x194` | `m_actionType{1,2}` | 1=x, 2=y, 3/4=angular |
| `+0x198` / `+0x1a0` | `m_actionValue{1,2}` | the total to interpolate to |
| `+0x1ac` | `m_deltaTimeInFloat` | elapsed, float32 |
| `+0x1b0` | `m_alreadyUpdated` | skips one elapsed advance |

That matters because field adjacency already produced one wrong claim here
(`m_currentStep`). This time the semantics come from the instructions that
read the fields, not from the order they are declared in.

The interpolator itself, from `updateAction`:

```
elapsed  = m_deltaTimeInFloat
duration = max(m_duration, 2^-23)
p        = clamp(elapsed / duration, 0, 1)
out      = value * GameToolbox::getEasedValue(p, m_easingType, m_easingRate)
displacement_this_step = out - m_current{X,Y}Offset ; then m_current* = out
```

So the remaining travel to any future time is `out(t) - m_currentXOffset`,
against the **live** field rather than a recomputed `out(0)`. The two normally
agree; after a pause or a checkpoint restore they can disagree, and the live
one is what GD will subtract from next step.

Types 3 and 4 run identical code and share one slot (`+0x90`). Which is
rotation and which is transform is **UNVERIFIED**.

### Easing is cocos2d's family, with two quirks worth not "fixing"

`GameToolbox::getEasedValue` (m1 `0x44f990`) is a jump table over 18 curves;
the table was read out of `__TEXT,__const` at `0x1007287a4` and each branch
decoded, and the libm stubs it calls resolve through the indirect symbol table
to `_powf`, `_sinf`, `_cosf`, `_exp2f`.

- `easingType == 0` returns the input **before** the rate is defaulted, so a
  linear trigger with rate 0 stays linear.
- A rate `<= 0` becomes `2.0`.
- **The exponential family does not pin its endpoints, and that is GD's own
  behaviour.** `ExponentialIn(1) = 0.999` (a materialised `-0.001`,
  `0xBA83126F`), `ExponentialInOut(0) = 0.5·2⁻¹⁰`, `ExponentialInOut(1) =
  0.5·(2 − 2⁻¹⁰)`. There is a zero guard on `ExponentialIn` and a one guard on
  `ExponentialOut`; the InOut branch has neither. A move trigger on
  `ExponentialIn` therefore stops 0.1% short of its target permanently.
  `trainer/trajectory.py` reproduces this rather than correcting it, and
  `test_trajectory.py` pins it so nobody tidies it up later.

### Trigger activation is an x-crossing, and that is readable

`EffectGameObject::spawnXPosition` (m1 `0x1741b8`) is exactly:

```cpp
if (m_isSpawnTriggered || m_isTouchTriggered) return m_spawnXPosition;  // +0x678
else                                          return getPosition().x;
```

so an ordinary trigger fires when the player crosses the trigger object's own
x. Everything in the forward projection hangs off that: the player's arrival
tick at any x is computable from the speed profile, so the elapsed time of a
not-yet-fired trigger *by the time the player gets somewhere else* is also
computable. `checkSpawnObjects` (6 `bl` sites, one in `update+0xf0c`) is where
GD does the crossing test.

`EffectGameObject::triggerObject` is the universal "a trigger fired" choke
point — `RandTriggerGameObject`, `CountTriggerGameObject`,
`TransformTriggerGameObject`, `CameraTriggerGameObject`,
`TimerTriggerGameObject` and `ItemTriggerGameObject` all tail-call into it.

## Forward projection: what is and is not computable

Implemented in `trainer/trajectory.py`, tested by `trainer/test_trajectory.py`
(`python3 -m pytest trainer/test_trajectory.py -q`, 48 tests).

**Exactly computable.** Any live `GroupCommandObject2` with a finite duration
and no lock flags — move, rotate, scale. Closed form, above. And any pending
x-activated trigger of those kinds, once its activation x is known, because the
arrival tick is an integral of a piecewise-constant speed profile.

**Conditionally computable** — the maths is exact, the inputs depend on a
future the policy has not chosen:

- `m_lockToPlayerX/Y`, `m_lockToCameraX/Y` — the offset tracks the player or
  camera, so projecting it assumes a player trajectory.
- Follow commands (`m_followXMod`/`m_followYMod` mirroring another group):
  exact if the followed group is exact, conditional otherwise. Not resolved
  recursively.
- Non-unit `m_moveModX`/`m_moveModY`.
- Any arrival time that crosses a speed portal other than 1x — see below.

**Not computable, and flagged rather than guessed:**

- Player-follow (`createPlayerFollowCommand`: delay / speed / maxSpeed chasing
  the player's y).
- `AdvancedFollowInstance` / `processAdvancedFollowAction` — a steering
  integrator with no closed form.
- Keyframe commands (`createKeyframeCommand` → `tk_spline`).
- Anything whose target group is decided at fire time: `RandTriggerGameObject`,
  `SequenceTriggerGameObject`, `ChanceObject`, and spawn remapping
  (`GJBaseGameLayer::m_spawnRemapTriggers`). The target group is drawn from
  `m_randomSeed` and is not a property of the level.
- Event-activated triggers (touch, collision, count, timer, item).
  `spawnXPosition()` returns a stored value for these that means "where it last
  fired", not "where it will fire".

**Deliberately out of scope:** `EnterEffectInstance` / `processAreaEffects`.
Those are camera-entry animations; whether they perturb the collision rect or
only the sprite is UNVERIFIED, and guessing either way puts a systematic error
into every observation.

The projection emits a **per-object certainty** rather than dropping uncertain
objects, because the failure modes are asymmetric: dropping a moving hazard
claims empty space where a hazard may be, projecting it as static claims a
hazard where there is none. Both are lies; a channel the policy can learn to
distrust is the only option that does not require the environment to guess.

Two implementation notes that are not obvious:

- **Arrival time is a fixpoint.** An object's arrival tick depends on its x and
  its x depends on the arrival tick. Two iterations, because the map contracts
  only while the object's horizontal speed is below the player's. Objects
  outrunning the player horizontally are flagged `CERTAINTY_UNKNOWN` rather
  than iterated — more passes would only produce a more confident wrong answer.
- **Both the now-map and the arrival-map are emitted.** For a static level they
  are identical and the trunk can ignore one; for a moving level their
  *difference* is the entire signal, and a difference needs both terms.

`trajectory.py` composes with `conditioning.py` rather than duplicating it:
trajectory answers *what the geometry will be*, conditioning answers *what it
means*. Player speed appears in both — as a bucket-plus-residual there because
it changes how an input is interpreted, as units-per-tick here because it sets
the horizon. Neither is derivable from the other.

### Only 1x speed is measured

`1.298250437` units/tick at `m_playerSpeed = 0.90` is this repo's own number.
The other four buckets in `UNITS_PER_TICK` are the widely quoted community
values (251.16 / 387.42 / 468.00 / 576.00 units/s) and are **unverified**. They
are not proportional to `m_playerSpeed`: `1.6/0.9 = 1.778` but
`576.00/311.58 = 1.849`, so deriving them from the multiplier would be 4% out.
Over a 400-tick lookahead that is ~20 units — a third of a tile, enough to put
a moving spike on the wrong side of the player. `SpeedProfile.certainty()`
downgrades any arrival time that depends on an unmeasured bucket.

## Telemetry the mod must emit (spec, not implemented)

The projection needs three things per observation that the current plain-text
log lines cannot carry: the live command table, the pending-trigger table, and
per-object group membership. Text is already inadequate here — a few hundred
objects × ~80 bytes each per tick at 240 Hz is not a log, it is a data channel.
The spec below assumes a future binary channel (shared memory ring or unix
socket); the field names are the GD 2.2081 binding names so a mismatch between
C++ and `trajectory.py` is one grep away.

All structs little-endian, naturally aligned, no bitfields.

```c
#define GDRL_OBS_MAGIC 0x4C524447u   /* 'GDRL' */

struct GdrlObsHeader {
    uint32_t magic;              // GDRL_OBS_MAGIC
    uint16_t version;            // bump on any layout change
    uint16_t flags;              // bit0: input-clean, bit1: practice mode
    int32_t  tick;               // lround(PlayLayer::m_attemptTime * 240)
    double   attemptTime;        // PlayLayer::m_attemptTime  (the verified clock)
    float    dtPerStep;          // the 's10' fed to prepareMoveActions this step
    float    timeWarp;           // GJGameState::m_timeWarp
    double   playerX, playerY;   // PlayerObject::getPositionX()/Y()
    float    playerSpeed;        // PlayerObject::m_playerSpeed
    uint32_t objectCount;        // GdrlObject[]        follows
    uint32_t commandCount;       // GdrlGroupCommand[]  follows
    uint32_t pendingCount;       // GdrlPendingTrigger[] follows
    uint32_t speedSegCount;      // GdrlSpeedSegment[]  follows
};

struct GdrlObject {              // one GameObject in the observation window
    int32_t  uniqueID;           // GameObject::m_uniqueID
    int16_t  objectID;           // GameObject::m_objectID
    uint8_t  kind;               // collapsed from GameObject::m_objectType
    uint8_t  groupCount;         // GameObject::m_groupCount   (<= 10)
    int16_t  groups[10];         // GameObject::m_groups (std::array<short,10>*)
    double   x, y;               // m_positionX, m_positionY   (doubles in GD)
    float    halfW, halfH;       // from getObjectRect(): size/2, NOT m_width/m_height
    float    rotation;           // CCNode::getRotation()
    float    scaleX, scaleY;     // m_scaleX, m_scaleY
    uint8_t  isHazard;           // m_objectType == Hazard || m_slopeIsHazard
    uint8_t  isGroupDisabled;    // m_isGroupDisabled
    uint8_t  pad[2];
};

struct GdrlGroupCommand {        // one live GroupCommandObject2
    int32_t  targetGroupID;      // +0x28  m_targetGroupID
    int32_t  centerGroupID;      // +0x2c  m_centerGroupID
    int32_t  commandType;        // +0xd0  m_commandType
    int32_t  actionType1;        // +0x190 m_actionType1   1=x 2=y 3/4=angular
    int32_t  actionType2;        // +0x194 m_actionType2
    double   actionValue1;       // +0x198 m_actionValue1
    double   actionValue2;       // +0x1a0 m_actionValue2
    double   duration;           // +0x18  m_duration
    float    deltaTimeInFloat;   // +0x1ac m_deltaTimeInFloat  (elapsed)
    int32_t  easingType;         // +0x0c  m_easingType
    double   easingRate;         // +0x10  m_easingRate
    double   currentXOffset;     // +0x30  m_currentXOffset
    double   currentYOffset;     // +0x38  m_currentYOffset
    double   currentAngular;     // +0x90  m_currentRotateOrTransformValue
    double   moveModX, moveModY; // +0x80  +0x88
    int32_t  triggerUniqueID;    // +0x158 m_triggerUniqueID
    int32_t  controlID;          // +0x15c m_controlID
    uint8_t  finished;           // +0x70
    uint8_t  disabled;           // +0x71
    uint8_t  lockedInX;          // +0x77
    uint8_t  lockedInY;          // +0x78
    uint8_t  lockToPlayerX;      // +0x73
    uint8_t  lockToPlayerY;      // +0x74
    uint8_t  lockToCameraX;      // +0x75
    uint8_t  lockToCameraY;      // +0x76
    uint8_t  unmodellable;       // NOT a GD field -- set by the mod when the
                                 // command came from a player-follow, advanced
                                 // follow or keyframe source. Explicit, because
                                 // "all lock flags false" is not evidence of
                                 // "it is a plain move".
    uint8_t  pad[3];
};

struct GdrlPendingTrigger {      // an EffectGameObject that has not fired yet
    float    activationX;        // EffectGameObject::spawnXPosition()
    int32_t  targetGroupID;      // m_targetGroupID
    int32_t  centerGroupID;      // m_centerGroupID
    float    duration;           // m_duration
    float    moveOffsetX;        // m_moveOffset.x   (properties 28/29)
    float    moveOffsetY;        // m_moveOffset.y
    float    rotationDegrees;    // m_rotationDegrees (property 68)
    int32_t  times360;           // m_times360        (property 69)
    int32_t  easingType;         // m_easingType
    float    easingRate;         // m_easingRate
    float    spawnTriggerDelay;  // m_spawnTriggerDelay
    int16_t  objectID;           // m_objectID -- which trigger kind
    uint8_t  isTouchTriggered;   // m_isTouchTriggered
    uint8_t  isSpawnTriggered;   // m_isSpawnTriggered
    uint8_t  isMultiTriggered;   // m_isMultiTriggered
    uint8_t  useMoveTarget;      // m_useMoveTarget   (property 100)
    uint8_t  moveTargetMode;     // m_moveTargetMode  (MoveTargetType)
    uint8_t  lockToPlayerX;      // m_lockToPlayerX
    uint8_t  lockToPlayerY;      // m_lockToPlayerY
    uint8_t  targetIsRemapped;   // mod-computed: object is a Rand/Sequence
                                 // trigger, or its group appears in
                                 // GJBaseGameLayer::m_spawnRemapTriggers
    uint8_t  pad[2];
};

struct GdrlSpeedSegment {        // one speed portal boundary ahead
    float    startX;
    int32_t  bucket;             // index into SPEED_MULTIPLIERS
};
```

**Where the mod reads each of these, and the hooks it may use:**

- Emission point: hook `GJEffectManager::prepareMoveActions(float dt, bool)`
  (2 `bl` sites — `update` and `loadUpToPosition`). This is the only
  per-*physics-step* hook verified in this investigation; `update` itself is
  per-render-frame and would resample a per-tick quantity, which is exactly the
  trap that made `maxX` a function of the frame rate.
- Objects: walk `GJBaseGameLayer::m_sections` for the columns spanning the
  observation window (`sectionIndex = floor(x / 100)`), not `m_objects` —
  the grid is the whole point of the grid.
- Commands: `GJEffectManager` holds them in `m_unkVector518`, `m_unkVector530`,
  `m_unkVector560`, `m_unkVector5b0`, `m_unkVector600`, `m_unkMap5c8` and
  `m_unkMap770`. **Which vector holds what is UNVERIFIED** — see below.
- Pending triggers: `EffectGameObject` instances in `m_sections` ahead of the
  player, filtered to those with a motion-relevant `m_objectID`.
- Speed segments: speed-portal objects ahead of the player.

**Do not** design around hooking `GJBaseGameLayer::processCommands` (0
references of any kind) or resampling in `update` (per frame, not per step).

## The env is validated: defaults are clean and observation is passive

Two checks, both on the merged tree, both reusing the 12-jump sequence. They
matter because ~550 attempts and the headline result were measured before the
binary transport existed, and neither the env-var defaults nor the observation
path had been exercised against them.

**Reproducing the 12-jump result** (the README previously recorded the numbers
but not a runnable invocation, so this is it):

```sh
GDRL_AUTOPLAY=1 GDRL_EXP=1 GDRL_BLOCK_INPUT=1 GDRL_PIN_LEVEL=1 \
GDRL_FAST_RESET=1 GDRL_ADAPTIVE=1 GDRL_DELTA_TICKS=8 \
GDRL_INJECT_SEQ="325,712,1074,1162,1266,1798,1934,2154,2318,2482,2686,2878" \
./scripts/run_sandbox.sh
```

**1. Defaults are clean.** 29 consecutive attempts, every one
`maxX=3959.183837891 deathTick=3048 t=12.700000662`, `push=12 rel=12`,
`input[clean blocked=0 leaked=0 ui=0]`. The timing-critical switches all default
to off in code (`GDRL_DELTA_TICKS=0`, `GDRL_FAST_RESET=0`, `GDRL_ADAPTIVE=0`), so
nothing `d9fbdb5` added perturbs the baseline.

**2. Observation does not perturb the simulation.** `GameObject` caches its
collision rect behind a dirty flag, so a telemetry pass calling `getObjectRect()`
could plausibly change when GD recomputes it — and if it did, every result
gathered without telemetry would stop applying the moment telemetry was switched
on, silently, because the numbers would remain self-consistent.

Run with `GDRL_ENV=1` and `trainer/passive_responder.py` attached, answering every
step with no action:

```
12 attempts with attached=1, steps=3054 each   (one per physics tick)
all  maxX=3959.183837891  deathTick=3048
timeouts=0 protoErr=0 across all 19 attempts
```

Bit-identical to the telemetry-off run. Note the client has to be attached for
this to mean anything: with none, the mod times out after `GDRL_ENV_WAIT_US` and
resumes free-running, and an attempt with `timeouts>0` is not evidence.

**Cost, and why decision rate should be decoupled from physics rate.** `steps=3054`
is one blocking round-trip per physics tick, ~500/s through Python even for a
no-op policy. Telemetry-on ran 5.5s per attempt against 3.4s off. A real policy at
5ms per decision would be ~15s per attempt. Since the outcome is piecewise
constant in jump tick with plateaus 29-81 ticks wide, per-tick decisions are not
needed: deciding every 8 ticks is ~380 round-trips instead of 3054, for the same
trajectory.

## The main levels cannot serve the remaining trigger work

Censused all 21 main levels in one launch (`GDRL_CENSUS=1 GDRL_CENSUS_SWEEP=21`).
The content needed to close the trigger measurements is not reachable:

| needed for | what exists | reachable? |
|---|---|---|
| live `GroupCommandObject2` (Probe A) | **4** move triggers total, all in lvl 21 (Fingerdash) at x=7813–8455, all `touch=1` | no — best sequence reaches x=3959 on lvl 1 |
| a vehicle portal by input (lvl 1) | ship at x=7995, cube at x=12555 | no — ~2× current reach |
| speed portals (lvl 1) | none at all | n/a |

These are pre-2.0 levels; they barely use triggers. And the four that exist are
touch-triggered, which forward projection classifies as *not* computable anyway.

**So the trigger measurements need synthetic levels** — a generated level string
with a spawn-triggered move trigger near x≈300, speed portals at known x, and
vehicle portals near the start. That is the unblock for Probe A, the four
unmeasured speed buckets, and `m_deltaTimeInFloat` against the tick clock.

The census cross-check earns its place here: every level reported a consistent
1–3% shortfall against `m_objects` (2384/2399 … 19217/19684), which is the
not-yet-sectioned tail rather than a traversal fault. A silent traversal bug
across 21 levels would otherwise have been invisible.

## Vehicle decode: all seven paths validated

`GDRL_FORCE_VEHICLE=<n>` calls the mode togglers on the live `PlayerObject`,
which reaches every vehicle without solving a level. All seven, `overlap=0`
throughout, `deriveVehicle` labelling each correctly:

```
ship 0b0000001   ball  0b0000010   ufo    0b0000100   wave 0b0001000
robot 0b0010000  spider 0b0100000  swing  0b1000000
```

`overlap=0` everywhere answers an open question: GD does **not** leave a parent
flag set while a child mode is active, so `deriveVehicle`'s derived-first ordering
is defensive rather than load-bearing.

Scope, from the probe's own banner: this validates the **flag decode and read
path only**. A real portal also sets size, gravity and speed and may run further
setup, so forcing a mode is not crossing one. `conditioning.py` is no longer
unexecuted design, but portal transitions remain unvalidated.

Correction while measuring: normal gravity reads **`0.9582`**, not the `0.96`
recorded earlier.

## `prepareMoveActions` fires once per physics step

Not a dedicated test, but the env validation answers it in passing. Telemetry
emits from `prepareMoveActions`, and an attempt ending at `endTick=3048` served
`steps=3054` — 1:1 with ticks to within six boundary steps. The static analysis
said it should; this is the first runtime evidence that it does.

## Synthetic levels (partly working)

`mod/src/synth.cpp` builds a level string in memory and hands it to PlayLayer,
because the main levels contain no reachable move trigger and no speed portals at
all. `GDRL_SYNTH=1` loads it instead of main level 1.

**What works.** The level constructs, loads and is playable: `levelLength=6340`,
13 objects, and the player runs normally (`f=180 pos=(148.0,105.0) ground=1`).
Every portal lands at exactly the requested x:

```
id=202 (2x) @1200   id=203 (3x) @1800   id=1334 (4x) @2400
id=200 (0.5x) @3000 id=201 (1x) @3600   id=13 (ship) @4500
```

Raw and gzip+base64 forms both load identically, so GD passes an uncompressed
level string through unchanged. `GDRL_SYNTH_COMPRESS=1` selects the compressed
form; neither is required.

That is enough to unblock the speed-bucket measurement and a vehicle-portal
crossing, neither of which was reachable in any main level.

**What does not work.** The move trigger (id 901) is rejected — it never appears
in the census while every other object does. Two hypotheses were tested and both
falsified:

- *Compression*: raw and compressed give byte-identical censuses, so the encoding
  is not the cause.
- *First-object-after-header being consumed*: a sacrificial block emitted first
  loaded fine (`id=1 n=3 firstX=50.0`) and 901 was still missing, so the
  header/object boundary is not the cause either.

What remains is the trigger's own property encoding. The property numbers in
`synth.cpp` were written from memory, which is precisely the "half-remembered
table" this repo has already been burned by. **The fix is to stop guessing and
derive the encoding from real data** — dump an actual level string containing a
move trigger (Fingerdash has four) and read the property IDs off it, rather than
iterating on guesses. Everything else in the file is verified by the census.

## Dumping a real level string (`GDRL_DUMP_LEVEL`) — built, not yet run

`mod/src/level_dump.cpp`. Set `GDRL_DUMP_LEVEL=<id>` and the mod decodes that
main level's `m_levelString` and writes two files under the mod save dir:

| file | contents |
|---|---|
| `level-<id>.txt` | the full decoded level string |
| `level-<id>-move-901.txt` | only the move triggers, one per line, raw `key,value,…` |

```sh
GDRL_DUMP_LEVEL=21 ./scripts/run_sandbox.sh    # 21 = Fingerdash
```

It exists for one reason: the synthetic move trigger (object 901) is rejected by
GD's parser and the remaining suspect is a **property encoding written from
memory**. Rather than guess a second time, read the real property IDs off a real
level. Fingerdash is the only main level with move triggers — four of them, at
x = 7813–8455.

Defaults are inert: with the variable unset the mod behaves exactly as before.
It refuses to write when fewer than 10 objects decode, so a
`dontGetLevelString=true` level cannot produce authoritative-looking evidence.

**Status: validated against a real level.** `GDRL_DUMP_LEVEL=21` decoded
Fingerdash — 27283 objects, 177 move triggers. Every assumption held: the `;`
heuristic, the `ZipUtils::decompressString` call, and property key `1` as the
object id.

A real move trigger, verbatim:

```
1,901,2,15,3,135,36,1,51,4,28,-60,29,0,10,4,30,0,85,2
```

key 1 = object id, 2 = x, 3 = y, 36 = touch, 51 = target group, 28 = moveX,
29 = moveY, 10 = duration, 30 = easing, 85 = easing rate.

## The census undercounts triggers by 44x — do not use it to detect them

`runCensus` walks `layer->m_sections`, the section grid (`probes.cpp:541`).
Triggers are not collision geometry and are not reliably in it.

Measured: the census reported **4** move triggers across all 21 main levels. The
level string for Fingerdash **alone** contains **177**, spanning x = 1 … 24993
rather than the census's 7813–8455.

This matters beyond bookkeeping. The recorded blocker "synthetic object 901 is
rejected by the parser" rested entirely on 901 being absent from the census —
and absence from the section grid is not evidence about loading. The property
encoding in `synth.cpp` was also suspected of being wrong "because it was
written from memory"; comparing it against the dump above shows its keys
(`1, 2, 3, 51, 10, 28, 29, 30`) are all correct.

`runObjectListCensus` (`probes.cpp`, runs with `GDRL_CENSUS=1`) walks
`m_objects` directly and settles it:

```
OBJLIST m_objects=13 distinctIds=11
OBJLIST-ID id=901   n=1    firstX=300.0
```

**The synthetic move trigger loads correctly, at exactly the requested x.** It
was never rejected. Use `OBJLIST-ID` rather than `CENSUS-ID` to ask whether an
object loaded; the latter answers a different question (is it in the section
grid) that happens to coincide for collision geometry and not for triggers.

Also settled: all 177 Fingerdash move triggers are touch-triggered, so none
fire on the player crossing their x. Fingerdash cannot serve as the natural
forward-projection test; synthetic levels remain the route.

## Moving geometry: the move pipeline does not write the CCNode position

**Validated against the running game.** `GDRL_PROBE_MOVE=1` on the synth level
records a move trigger's effect per physics step.

`GJBaseGameLayer::processMoveActions` → `moveObjects` (m1 `0x11acb0`) does, per
object:

```
[obj + 0x3b0] += dx      ; double, m_positionX
[obj + 0x3b8] += dy      ; double, m_positionY
dirtifyObjectPos() / dirtifyObjectRect()
```

It **never calls `CCNode::setPosition`**. So `m_obPosition` — what
`getPositionX/Y()` return — is untouched by the move pipeline. Anything reading
object positions for telemetry must read `m_positionX`/`m_positionY`, as
doubles, or it will observe moving geometry standing still.

This is not adjacency reasoning. `GameObject::getRealPosition()` (m1 `0x4ecfc4`)
is literally `ccp((float)*(double*)(this+0x3b0), (float)*(double*)(this+0x3b8))`
— GD's own "where is this object really" accessor reads the same two fields. The
probe also prints the runtime offset (`posOff=0x3b0`) rather than trusting the
disassembly, and logs `cx`/`cy` (the CCNode shadow) alongside so the claim stays
checkable: `cy` stayed pinned at `435.000000000` for all 480 ticks while `y`
ramped to 525.

### Measured motion

Trigger `target=1 offX=0 offY=90 duration=2.0 easing=0`, block starting at
y=435. Two independent GD launches, both giving:

| quantity | measured |
|---|---|
| records | **480**, ticks **234 → 713**, 0 gaps, 0 duplicates |
| displacement | 435 → 525 = **90.000000000** exactly |
| per-tick `dy` | mean `0.187500000` over all 480; `0.187501179` over the 479 **non-final** steps. Logged dy sum to `89.999999990`. Max deviation `7.63e-6` |
| linearity, vs the **least-squares** fit | max residual `3.99e-4` units, rms `9.2e-5` |
| linearity, vs the **theoretical** line `435 + 90(t−233)/480` | max residual `5.646e-4` units, rms `2.224e-4` |

Two corrections to earlier revisions of this table, both from
`trainer/validate_projection.py` (see TODO.md, session 2026-08-12). This table
once annotated `0.187501179` as `(= 90/480)`; `90/480` is `0.1875` exactly, and
the two are different quantities. And it quoted only the least-squares residual
without saying so — that figure is ~4× smaller than the theoretical-line one
purely because the fit absorbs the constant part of the float32 drift. Both fits
are legitimate; which one is meant has to be stated.

The residual is float32 accumulation in `m_deltaTimeInFloat`, not curvature —
genuine easing would swing per-step `dy` by tens of percent. The final step is
short (`0.186935425`) because `p = clamp(elapsed/duration, 0, 1)` lands the
endpoint exactly on 90.0.

**One tick of dead time at activation.** The command goes live at tick 233
(`m_unkVector560` 0→1) but the first nonzero displacement is at tick **234**,
and the last at **713 = 233 + 480**. The activation step itself produces zero
displacement. A predictor that assumes motion begins on the activation tick will
lead the game by exactly one tick.

Scope: only `ActionType` 2 (y-move, linear easing) has been exercised.
Rotation/transform and non-zero easing are unmeasured.

**This dataset has since been used to validate `trainer/trajectory.py` against
the game** — the repo's first tier-(iii) validation. The motion model is exact;
the predictor's *fire tick* was 1.9198 ticks early, and that gap decomposes with
no residual into a `U·t` vs `U·(t−1)` origin convention error plus the
continuous-vs-integer tick gap. Crossing-to-activation latency is **0**, and the
one tick of dead time above is a separate, downstream effect on object
displacement. See TODO.md, session 2026-08-12, sections A–C.

### The live command container is `m_unkVector560`

Measured 0 → 1 → 0 across the trigger's lifetime while the other six candidates
stayed 0 throughout. It is `gd::vector<GroupCommandObject2>` **by value**, not
by pointer.

## What still needs runtime verification

None of this was measured on a running game — another agent held exclusive use
of GD during this work. The following are the specific measurements that would
close the gaps, in rough order of how much they matter:

1. **Which `GJEffectManager` vector holds live `GroupCommandObject2`.** Run a
   level with one known move trigger and log the size of `m_unkVector518`,
   `530`, `560`, `5b0`, `600`, `m_unkMap5c8` and `m_unkMap770` every step.
   Exactly one should go 0 → 1 → 0 across the trigger's duration. Everything in
   the telemetry spec above depends on this and nothing else does.
2. **Per-tick advance for the four unmeasured speed buckets.** Same protocol as
   the 1x measurement: null-input run through a level with a 0.5x / 2x / 3x /
   4x portal, `dx = x[t+1] - x[t]` in the tick after the portal. Replaces four
   unverified constants in `UNITS_PER_TICK`.
3. **That `prepareMoveActions` actually fires per step.** Hook it, count calls
   per attempt, and check the count equals the tick count at `dt = 1/240`.
   Static analysis says it does; the `processCommands` episode says that is not
   the same as knowing.
4. **`m_deltaTimeInFloat` vs the tick clock.** Log both while a 1.0 s move
   trigger runs. If they agree to within the float32 accumulation error, the
   projection's time base is confirmed end to end.
5. **Whether area/enter effects move the collision rect.** Put an enter effect
   on a hazard, disable the sprite, and see whether the player still dies at
   the unanimated position. Decides whether `EnterEffectInstance` belongs in
   the projection at all.
6. **Which of `ActionType` 3 and 4 is rotation and which is transform.** Fire a
   rotate trigger and a scale trigger, log `m_actionType1`.
7. **Timewarp semantics at `timeWarp != 1`.** The decompiled step arithmetic
   says `numSteps` shrinks and `dtPerStep` grows, which reads as coarser steps
   rather than faster time. Log `dtPerStep`, `numSteps` and the player's
   per-step x advance under a timewarp trigger before trusting either reading.
8. **Whether `m_attemptTime` survives a checkpoint restore**, already open in
   the input-clock section, and now load-bearing for the projection too.

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

## The input-placement clock: `m_attemptTime`, via `lround(t * 240)`

`PlayLayer::m_attemptTime` is the replacement, and it is verified rather than
assumed — the `m_currentStep` mistake came from treating a weak observation
("the endpoint is a multiple of 1/240") as a strong property. Four things a
placement key must do, each measured over **78 attempts** in two `dt` regimes:

| property | result |
|---|---|
| **Monotonic** — never runs backwards | `nonMono=0`, all attempts |
| **Commandable** — tick delta equals the `dt` fed | at `dt`=8/240: `tickDeltas[8:48, 7:1]` — 48×8 + 7 = **391 exactly** |
| **Reproducible** — same attempt, same final tick | `finalTick=391`, all attempts, both regimes |
| **Quantised** — `t*240` lands on integers | **not exactly** — see below |

It is a `double` (not the `SeedValueRSV` of the same name on `GJGameState`), so
the value itself does not decay over a long level. But `t*240` is *not* an exact
integer: measured `maxResid = 2.039e-05` ticks. The cause is exact —

```
1/240 as float32 = 0.004166666883975267   (exact double: 0.004166666666666667)
error per tick   = 2.173086e-10 s = 5.215406e-08 ticks
× 391 ticks      = 2.0392e-05 ticks   ==  the measured residual
```

GD accumulates `m_attemptTime` by adding a **float32** `1/240` into a double. The
drift is therefore real, deterministic (byte-identical `maxResid` on every
attempt), and negligible: rounding stays unambiguous until the residual reaches
0.5 ticks, at ~9.59e6 ticks — **11.1 hours** of attempt time. At two minutes it
is 1.5e-3 ticks.

**So: `tick = lround(m_attemptTime * 240)`.** Never `t == n/240.0` — exact
equality fails on the very first tick.

Not yet verified: behaviour across a checkpoint restore, and whether the clock
holds while `m_isPaused`.

## Input injection works, and placement is tick-exact

Everything before this was the null-input trajectory: the cube runs into the
first spike at x=507.6 and dies at tick 391, ~550 times. That proves the
environment is deterministic. It does **not** prove GD is a forward model, which
is what search-and-replay needs — that requires a *chosen* input sequence to
replay identically.

Injection goes through `queueButton(button, push, isPlayer2, timestamp)`, placed
against the verified clock. The guard blocks at `queueButton` rather than
`handleButton`, because the call graph converges before it:

```
queueButton -> [queue] -> processQueuedButtons -> handleButton -> pushButton
```

`handleButton`'s only two call sites are both inside `processQueuedButtons`, so
human and injected input are indistinguishable there. `queueButton` is the last
point where the caller is still known: deliberate injection sets a flag around
its own call, everything else is a stray keypress. Injected pushes are credited
at queue time and debited when they surface at `pushButton`, so the leak detector
does not fire on our own input.

**One jump changes everything.** A single jump injected at tick 325 carries the
player past the first spike — `maxX` 507.6 → **958.1**, dying at the next
obstacle at tick 738 instead of 391.

**Placement resolves to a single tick.** Sweeping the injection tick one at a
time at `dt` = 1/240, where frame and tick coincide:

| injTick | death tick | maxX |
|---|---|---|
| 318 | 412 | `534.878967285` |
| 319 | 413 | `536.177246094` |
| ... | +1 each | +1.2983 each |
| 324 | 418 | `542.668640137` |
| **325** | **738** | **`958.117858887`** |
| 326–331 | 738 | `958.117858887` |

One tick later in, one tick later out, and `maxX` moves by exactly `1.2983` —
the per-tick x advance of `1.298250437`. Then 324 → 325 flips the trajectory
entirely. No smearing, no jitter.

**Determinism holds with inputs.** Eleven injection ticks × 8 repeats at
`dt` = 8/240, plus the sweep above: every group bit-identical in both `maxX` and
death tick, `leaked=0` throughout. The earlier 8-tick granularity was a sampling
artifact of injecting on frame boundaries, not a limit of GD.

`dt` = 1/240 and `dt` = 8/240 produce identical outcomes for the same injection
tick, so small-`dt` physics agrees with large-`dt` physics.

## Multi-input sequences replay bit-identically (n = 12)

One injected jump proves placement is tick-exact. It does not prove GD is a
forward model over a *sequence*, which is what search-and-replay needs: with a
single input there is nothing for error to compound across. The test only starts
being informative at n ≥ 2.

`GDRL_INJECT_SEQ="325,712,1074,…"` plays a whole list; each entry is a jump tick
with an optional `:hold` in ticks (default 8). The deepest sequence found by
greedy search on Stereo Madness is **twelve jumps**:

```
325,712,1074,1162,1266,1798,1934,2154,2318,2482,2686,2878
```

**Replayed 80 consecutive times, every attempt bit-identical:**

```
maxX      = 3959.183837891     80/80
deathTick = 3048               80/80
t         = 12.700000662       80/80   (= 3048/240, with the known float32 drift)
input[clean blocked=0 leaked=0 ui=0]    80/80, all on lvl=1
```

That is 7.80× the null-input `maxX` of `507.615234375` and 4.13× the
single-jump `958.117858887`, or ~14.8 % of the level by `m_levelLength`.

**Determinism does not degrade with sequence length.** Across every clean run —
**1473 admissible attempts covering 631 distinct sequences of length 1 to 12** —
not one sequence produced more than one outcome. Grouping is by the exact
sequence played, printed on each attempt's own line, so the check is an assertion
of identity rather than an eyeball:

| sequence length | attempts | distinct sequences | divergent |
|---|---|---|---|
| 1 | 44 | 1 | 0 |
| 2 | 252 | 48 | 0 |
| 3 | 222 | 80 | 0 |
| 5 | 123 | 44 | 0 |
| 6 | 100 | 82 | 0 |
| 7 | 86 | 49 | 0 |
| 8 | 106 | 72 | 0 |
| 9 | 90 | 53 | 0 |
| 10 | 84 | 57 | 0 |
| 11 | 78 | 63 | 0 |
| 12 | 288 | 82 | 0 |

The greedy path itself, each step the plateau centre of a 4-tick sweep of the
next jump against a fixed prefix:

| jumps | added tick | death tick | maxX |
|---|---|---|---|
| 1 | 325 | 738 | `958.117858887` |
| 2 | 712 | 1107 | `1437.163330078` |
| 3 | 1074 | 1221 | `1585.160156250` |
| 4 | 1162 | 1328 | `1725.069458008` |
| 5 | 1266 | 1822 | `2367.419189453` |
| 6 | 1798 | 1983 | `2576.451904297` |
| 7 | 1934 | 2213 | `2875.070068359` |
| 8 | 2154 | 2354 | `3058.135986328` |
| 9 | 2318 | 2536 | `3294.433837891` |
| 10 | 2482 | 2723 | `3537.223388672` |
| 11 | 2686 | 2945 | `3825.454833984` |
| 12 | 2878 | 3048 | `3959.183837891` |

### Sensitivity survives length, but the landscape is a step function

This is the important negative result, and it is not a determinism failure.

Perturbing the **first** jump of the twelve by −1 tick destroys everything
downstream, exactly as at n = 1 — the run dies at the first spike and only one
of the twelve jumps ever fires:

| jump 1 | maxX | death tick | pushes fired |
|---|---|---|---|
| 324 | `542.668640137` | 418 | 1 / 12 |
| **325** | `3959.183837891` | 3048 | 12 / 12 |
| 326 | `3959.183837891` | 3048 | 12 / 12 |

Perturbing the **last** jump by ±1 changes **nothing**:

| jump 12 | maxX | death tick |
|---|---|---|
| 2850 | `3835.841552734` | 2953 |
| **2851 … 2898** | `3959.183837891` | 3048 |
| 2899 | `3825.454833984` | 2945 |

`2877`, `2878`, `2879` are indistinguishable, and so is every value in a
**48-tick-wide plateau**. Outside it, a *single* tick flips the outcome:
2850 → 2851 and 2898 → 2899 both change `maxX` and death tick. Measured at
1-tick resolution, 21 perturbations × ~4 repeats, every group bit-identical.

So the outcome is **piecewise constant** in each jump tick, with sharp edges.
That follows from the geometry rather than from anything numerical: x advances
`1.298250437` per tick regardless of what the player does vertically, so death
`x` can only ever land on an obstacle's x — the outcome space is discrete, and
between edges the map is flat. Jump 1's plateau happens to start at 325, which
is why ±1 there looks maximally sensitive; it sits on an edge.

**Consequences for search.** A ±1 perturbation is *not* a valid determinism
probe at depth — "outcome unchanged" is the expected answer almost everywhere,
and reading it as a broken replay would be wrong. It also means gradient-like
local search over jump ticks is mostly climbing flat ground; the useful signal
is at plateau edges, which is why the search here sweeps a window rather than
stepping. Conversely the plateaus are why a 4-tick sweep grid found every
obstacle. Over the clean runs (jumps 5-12) the narrowest plateau was 29 ticks
and the widest 81; jump 12's, the only one resolved to 1 tick rather than 4, was
48.

### Adaptive `dt`: tick-exact placement without paying 1 frame per tick

Tick-exact injection needs the frame boundary to coincide with the target tick,
which at fixed `dt` means `dt` = 1/240 — and a 3048-tick attempt then costs 3048
rendered frames. Since the event ticks are known in advance, `GDRL_ADAPTIVE=1`
instead picks the frame size per frame as `min(GDRL_DELTA_TICKS, nextEvent −
now)`. Every frame still consumes a whole number of 1/240 steps and every event
still lands on a frame boundary.

Equivalence is measured, not assumed. The single jump at tick 325 gives the same
outcome under all three regimes:

```
dt = 1/240  (README, earlier)         maxX = 958.117858887  tick 738
adaptive, cap 8/240   44 attempts     maxX = 958.117858887  tick 738
adaptive, cap 32/240  (control)       maxX = 958.117858887  tick 738
```

The cap-32 control is free: any swept jump scheduled past the death tick never
fires, so those attempts *are* the single-jump sequence. A 3048-tick attempt
costs ~95 frames instead of 3048, ~1.6 s wall clock.

### The level pin: `input[clean]` was not enough

A search run silently left Stereo Madness. Reconstructed from the log: GD called
`PlayLayer::pauseGame(unfocused=true)` on a window-focus change, the run sat in
the pause menu for eight seconds, and stray `ESC` keypresses (key `27`, observed
directly once the dispatcher was hooked) walked it out to the level select and
into **Back On Track** — where it happily kept producing attempts reading
`input[clean blocked=0 leaked=0]`, because no *button* had leaked. Two of them
landed in the same sweep group as real attempts and looked exactly like
nondeterminism:

```
swept=1182  maxX=1725.069458008  tick=1328   <- real
swept=1182  maxX= 688.075866699  tick= 530   <- a truncated attempt on another level
```

`GDRL_BLOCK_INPUT` covers the button. It does not cover the *session*. So
`GDRL_PIN_LEVEL=1` now also swallows keyboard and touch events wholesale (the
harness needs neither — all its input goes through `queueButton`), suppresses
`onQuit` and `pauseGame`, re-enters the pinned level if the menu is ever
reached, and — the part that actually matters — stamps `lvl=` and `ui=` on every
`ATTEMPT` and `SEQ` line. A blocked exit that quietly failed would leave no
trace; a level id on every line cannot. Attempts are admissible only when
`lvl=1 leaked=0 ui=0 deathTick>0`; 22 of 977 attempts were rejected by that rule
and none of the reported numbers include them.

The general shape of this is the same trap as `maxX`-per-frame and
`m_currentStep`: **the measurement was clean about the thing it checked and
silent about the thing that had changed.**

### What this does not establish

- **Only cube, only Stereo Madness, only 1× speed.** The 12-jump sequence never
  leaves cube; no portal, no ship, no mini, no gravity flip has been replayed.
- **Not across processes.** Every repeat above is within a single GD launch.
  Cross-process determinism (needed for parallel workers) is still unverified,
  as is anything about `m_randomSeed` / `m_replayRandSeed`.
- **Not to level completion.** 3048 ticks is ~15 % of Stereo Madness. Greedy
  search stalls where it needs two coordinated jumps rather than one; the search
  here only ever appends a single jump per step, and only ever with `hold=8`.
- **Hold duration is untested.** Every jump used an 8-tick hold. Whether a
  longer hold changes cube behaviour (it should, on landing) was not measured.
- **The plateau widths are from a 4-tick grid** except jump 12, which was
  resolved to 1 tick. The others could be off by up to 3 ticks at each edge.

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
only cube has actually been reached. Input injection now exists and reaches
x=3959.183837891 on Stereo Madness, and that is still cube the whole way — so
**every non-cube conditioning path remains unexecuted**, and `conditioning.py`
is unvalidated design rather than validated code.

Two routes to closing that, of different strength. `PlayerObject`'s seven
vehicle togglers are all live and callable on arm64 — `toggleFlyMode` (ship,
bl=12), `toggleBirdMode` (UFO, 13), `toggleRollMode` (ball, 5), `toggleDartMode`
(wave, 15), `toggleRobotMode` (13), `toggleSpiderMode` (13), `toggleSwingMode`
(11 bl + 1 b), all `(bool enable, bool noEffects)` — so the mod can force a mode
and read the flags back without solving a level. That validates the **flag
decode** and nothing more: a portal also sets size, gravity and speed and may
run further setup, so forcing a mode is not crossing one. Reaching a real portal
validates both and is the stronger claim.

Gravity is a separate function, and the obvious-looking one is a trap.
`toggleGravityMode(bool)` at m1 `0x3dbfb0` belongs to **`CreateParticlePopup`**,
a level-editor popup — calling it through a `PlayerObject*` would run popup code
against the wrong `this`. The player-side flip is
`PlayerObject::flipGravity(bool flip, bool noEffects)` at m1 `0x37b40c`, bl=28.
This was caught after the wrong address had already been written down and passed
on: a name matched, and the enclosing class was never checked. **Grepping a
member name does not tell you which class owns it** — the same trap as "an
address is not a call site", one level up.

`m_currentStep` sits next to `m_randomSeed`, `m_replayRandSeed` and
`m_queuedButtons` (`gd::vector<PlayerButtonCommand>`, fed by
`queueButton(button, push, isPlayer2, timestamp)`), so the timestamped
input-injection path and the seed state needed for cross-process determinism are
all in one region of `GJBaseGameLayer`. That adjacency is the *only* thing being
claimed here. `m_currentStep` itself is **not** the physics-tick counter — it
reads 0 on every frame of gameplay; see the correction section above, and use
`lround(m_attemptTime * 240)`.

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
