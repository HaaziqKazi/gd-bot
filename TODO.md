# gd-rl — open work

Single place for what is left. Companion to `README.md`, which records what has
been **established**; this file records what has **not**.

Status as of commit `fc28dbd` + the 901 resolution below.

---

## Where we actually are

The environment is proven and instrumented. **No learning exists.** Everything
built so far — determinism, tick-exact injection, telemetry, forward projection,
the binary transport — is environment engineering. `trainer/env.py` says so in
its own docstring: *"no reward and no `done` here, because none of those are"*
decided yet.

| Capability | State |
|---|---|
| Deterministic replay, null input | **Proven** — ~550 attempts, bit-identical |
| Deterministic replay, with input | **Proven** — 1473 attempts / 631 sequences, zero divergent |
| Tick-exact input placement | **Proven** — ±1 tick changes outcome at plateau edges |
| Settable simulation rate | **Proven** — `frames = 96/k + 64` |
| Throughput | **~11.5×** baseline (0.35 → 3.9 attempts/sec) |
| Unattended running | **Proven** — `GDRL_WINDOWED`, 0.41/s unfocused |
| Binary env transport | **Built + validated** — defaults clean, observation passive |
| Forward projection (Objective B) | **Built, untested against a real moving object** |
| Conditioning (Objective A) | **Built; only flag-decode validated, never a real portal crossing** |
| Hold duration (Objective C) | **Untouched** |
| In-context failure memory (Objective D) | **Nothing** |
| Uncertainty policy (Objective E) | **Substrate only** (coverage mask + certainty channel) |
| Reward function | **Does not exist** |
| Search algorithm | **Hand-rolled greedy script** — stalls on coordinated jumps |
| Policy / training loop | **Does not exist** |

Deepest reach: 12 jumps, `maxX=3959.183837891`, `deathTick=3048`, ~14.8% of
Stereo Madness.

---

## Track 0 — Ground-truth validation  ← HIGHEST PRIORITY

Two systems are **built and internally self-consistent but never checked against
the running game**. Self-consistency is not evidence: a test of the form
`predictor_output == helper_using_the_same_equations()` has near-zero
evidentiary value. Both need external, game-grounded ground truth.

### 0.1 Forward projection: validate against real moving geometry

`trainer/trajectory.py` predicts where a moving block will be when the player
arrives. The maths is implemented and `test_trajectory.py` passes 48 tests — all
of which compare the predictor against Python fixtures, **none against GD**.

**The blocker is GONE.** It rested on the census, which walks the section grid
and cannot see triggers at all. Measured directly off `m_objects`, the synthetic
move trigger loads fine — `OBJLIST-ID id=901 n=1 firstX=300.0`. A reachable,
x-activated move trigger therefore already exists in the synth level, at x=300,
well inside the proven reach of x=3959.

- [x] Unblock a reachable move trigger — done, see 1.1a.
- [x] **Probe built** (`GDRL_PROBE_MOVE=1`, Codex): samples per physics step off
      `prepareMoveActions`, diffs every object's position against the previous
      step, emits `MOVE tick= id= x= y= dx= dy=` at full precision. Tick from
      `lround(m_attemptTime*240)`.
- [x] **The trigger fires and the command goes live.** With
      `GDRL_PROBE_CMDVEC=1` on the synth level:
      ```
      CMDVEC tick=1    v560=0
      CMDVEC tick=233  v560=1     <- move command live
      CMDVEC tick=481  v560=1
      CMDVEC tick=715  v560=0     <- completed
      ```
      GD loaded the trigger with exactly the authored parameters:
      `MOVE-TRIGGER target=1 offX=0 offY=90 duration=2.0 easing=0`.
- [x] **RESOLVED — it was a wrong-field error, not frame ordering.** The move
      pipeline writes `m_positionX`/`m_positionY` (doubles at +0x3b0/+0x3b8) and
      never calls `CCNode::setPosition`, so `getPositionX/Y()` never changes.
      Probe now reads the right field and emits 480 records; see README,
      "Moving geometry: the move pipeline does not write the CCNode position".
      **Ground truth for forward-projection validation now exists.**
- [x] **Measured, twice, from separate GD launches (byte-identical):** 480
      records, ticks 234→713, displacement exactly 90.0, per-tick
      dy=0.187501179, linear to 4.4 ppm.
- [ ] ~~old blocker text below, retained for the reasoning trail~~
      **`MOVE` count is ZERO for the whole run.**
      The command is live for ~480 ticks and no object's `getPositionX/Y` ever
      changes. So the position we sample immediately after `prepareMoveActions`
      is **not** the position the move command writes.
      Hypothesis to test first: `prepareMoveActions` *prepares* the actions and a
      later phase of the same physics step applies them, so the sample is taken
      pre-application — an off-by-phase error, the frame-ordering failure mode.
      Find the apply phase (candidates: `processMoveActions`,
      `processMoveActionsStep`, `GJBaseGameLayer::updateMoveObjectsLastPosition`)
      and sample after it, or read the group transform rather than the object's
      own position. **Until this is resolved, forward projection cannot be
      compared against observed positions at all.**
- [ ] Compare predicted position at player-arrival tick vs observed position.
- [ ] Quantify: mean error, max error, does error grow with horizon, does it
      change near trajectory reversals, does it lead or lag.
- [ ] Investigate the whole path before trusting a number: is the state sampled
      pre- or post-physics? Same frame of reference? Same origin/centre
      convention? Stale by one tick? **Suspect off-by-one-frame first.**
- [ ] Fix discrepancies rather than assuming the predictor is right.
- [ ] Land a deterministic regression test carrying the recorded dataset.

### 0.2 Physics predictor: identify and validate

The "internal physics predictor" is the player-motion model that produces
**arrival ticks** — `SpeedProfile` / `UNITS_PER_TICK` in `trajectory.py`. Forward
projection depends on it: a wrong arrival tick moves the predicted object
position even if the object model is perfect.

- [ ] Document precisely what it claims to model: inputs, outputs, coordinate
      convention, timestep, velocity representation, speed caps, what it assumes
      about future control input, and what it does **not** model (vertical
      motion? collision? gravity?).
- [ ] **Known systematic error, already identified:** four of five entries in
      `UNITS_PER_TICK` are unverified community constants and are demonstrably
      not proportional to `m_playerSpeed` (`1.6/0.9 = 1.778` vs
      `576.00/311.58 = 1.849`). → fix via 1.2.
- [ ] Validate against recorded game trajectories, not against itself. Ground
      truth ranking: predictor vs live game > vs recorded transitions > vs an
      independent reimplementation > vs itself.
- [ ] Check for: systematic drift, timestep mismatch, off-by-one tick, state
      captured before vs after integration.
- [ ] Label the existing self-referential tests as regression-only rather than
      deleting them; supplement with game-grounded ones.
- [ ] Document remaining modelling limitations honestly.

---

## Track 1 — Synth content + cheap measurements  ← SELECTED

Highest unblock-per-hour. Two of these are available right now with no new work.

### 1.1 Synth move trigger (object id 901)  — RESOLVED, was never broken

**Object 901 was never rejected.** It loads at exactly the requested x. Three
hypotheses were tested against it over two sessions — compression, the
header/object boundary, and the property encoding — and all three were
falsified, because the symptom being explained was an artifact of the measuring
instrument rather than a real defect.

The lesson is the repo's own rule applied one level up: *the census answers "is
this object in the section grid", which is not the same question as "did this
object load", and the two only coincide for collision geometry.*

- [x] **Build the dumper.** `mod/src/level_dump.cpp` + `.hpp`, gated on
      `GDRL_DUMP_LEVEL=<id>`, default off. Decompresses `m_levelString` (falls
      back to `ZipUtils::decompressString` when no `;` is present), writes the
      full decoded level to `level-<id>.txt` and a filtered move-trigger-only
      view to `level-<id>-move-901.txt`, both under the mod save dir. Refuses to
      write if fewer than 10 objects decode, so a `dontGetLevelString=true`
      level cannot produce authoritative-looking evidence. Builds green and
      universal (`x86_64 arm64`).
- [x] **Ran it.** `GDRL_DUMP_LEVEL=21 ./scripts/run_sandbox.sh` → **27283 objects,
      177 move triggers**, written to `level-21.txt` and `level-21-move-901.txt`.
      All of Codex's assumptions held: the `;` heuristic, the
      `ZipUtils::decompressString` call, and property key `1` as the object id.
- [x] **Read the real property IDs.** A real Fingerdash move trigger:
      ```
      1,901,2,15,3,135,36,1,51,4,28,-60,29,0,10,4,30,0,85,2
      ```
      key 1=id, 2=x, 3=y, 36=touch, 51=target group, 28=moveX, 29=moveY,
      10=duration, 30=easing, 85=easing rate. Also seen: 20 (103×), 61 (68×),
      57, 62, 58, 59.

### 1.1a THE BLOCKER'S PREMISE IS WRONG — re-diagnose before re-encoding

Two measurements taken this session contradict the recorded diagnosis.

**(a) `synth.cpp`'s property encoding is CORRECT.** It writes keys
`1, 2, 3, 51, 10, 28, 29, 30` — every one matches the real Fingerdash encoding
above. The "written from memory, therefore suspect" hypothesis is falsified. The
only keys synth omits are `85` (easing rate, irrelevant at easing=0), `20`
(editor layer), and `61`. **Do not "fix" the encoding; it is not broken.**

**(b) The census is not a valid instrument for detecting triggers.** It walks
`layer->m_sections` (the section grid; `probes.cpp:541,598`), and triggers are
not collision geometry. Direct evidence of the undercount: the census reported
**4** move triggers across all 21 main levels, while Fingerdash's level string
alone contains **177** — a 44× miss, and the census's x-range (7813–8455) is
wrong too (real range is x = 1 … 24993).

So "901 is absent from the census" was never evidence that 901 failed to load.

- [x] **901 LOADS. Measured, not inferred.** Added `runObjectListCensus`
      (`probes.cpp`), which walks `m_objects` directly and prints every
      `m_objectID`. Under `GDRL_SYNTH=1 GDRL_CENSUS=1`:
      ```
      OBJLIST m_objects=13 distinctIds=11
      OBJLIST-ID id=901   n=1    firstX=300.0
      ```
      The move trigger is present at exactly the requested x. It was never
      rejected by the parser; it is simply absent from the section grid, which
      is what the census walks. **The "blocked on content" framing is dissolved
      and Track 0.1 is unblocked.**

### 1.1b Fingerdash is not a usable natural test after all

All **177** move triggers are touch-triggered (`36=1`) — zero are x-activated.
The recorded claim that they are all touch-triggered was right in character even
though its count was wrong by 44×. So Fingerdash cannot supply a trigger that
fires simply by the player crossing its x, which is the case forward projection
needs. Synth remains the route.

- [ ] Confirm what key `36` means. It is assumed to be "touch triggered" from
      GD community tables — **the same class of assumption that just failed**.
      Verify against `EffectGameObject::m_isTouchTriggered` in the bindings
      before relying on it.

**Unblocks:** items 1, 4, 5, 6, 7 of the runtime-verification backlog below, and
all validation of `trainer/trajectory.py` against a real moving object.

### 1.2 Measure the four unmeasured speed buckets  — READY NOW

Synth's speed portals already land at exactly the requested x. Only 1×
(`1.298250437` units/tick) is this repo's own measurement; 0.5×/2×/3×/4× are
community values and are **not** proportional to `m_playerSpeed`
(`1.6/0.9 = 1.778` vs `576.00/311.58 = 1.849`). A 4% error over a 400-tick
horizon is a third of a tile.

- [ ] Null-input run through a synth level with each speed portal.
- [ ] `dx = x[t+1] - x[t]` on the tick after the portal.
- [ ] Replace the four unverified constants in `UNITS_PER_TICK`.

### 1.3 Cross a real vehicle portal  — READY NOW

Synth's ship portal lands correctly. Objective A is currently validated only at
the **flag-decode** level via `GDRL_FORCE_VEHICLE`. A real portal also sets
size, gravity and speed — this is the difference between conditioning being
tested and merely being written.

- [ ] Drive through the synth ship portal; confirm `COND`/`MODE` lines fire.
- [ ] Verify the regime the mod reports matches what `conditioning.py` expects.
- [ ] Repeat for at least one other vehicle once 1.1 lands.

---

## Track 2 — Search throughput (unexplored, highest leverage)

### 2.1 Checkpoint save/restore

`PlayLayer::createCheckpoint()` (m1 `0xa86d0`) and `loadFromCheckpoint()`
(m1 `0xaa038`) both have live addresses and **nobody has touched them**.

Today every search probe replays from tick 0 — testing jump 13 costs 3,048
ticks, so search cost grows quadratically with depth. Restore would collapse
that.

- [ ] Verify `createCheckpoint`/`loadFromCheckpoint` are actually called
      (`bl`/`b` count, and remember: virtuals are evidenced by neither).
- [ ] **Verify restore is deterministic** — restore the same checkpoint N times
      with the same subsequent inputs and require bit-identical outcomes. If it
      is not, the whole idea dies here.
- [ ] Check whether `m_attemptTime` survives a restore (backlog item 8 — already
      open, and load-bearing for both the input clock and the projection).
- [ ] Check `m_randomSeed` / `m_replayRandSeed` behaviour across restore.

### 2.2 Decouple decision rate from physics rate

Measured: 3,054 blocking round-trips per attempt, 5.5s vs 3.4s telemetry-off. A
realistic 5 ms policy makes it ~15s/attempt. This blocks any training run.

- [ ] Action repeat / frame skip: decide at k ticks, hold between decisions.
- [ ] Measure the determinism cost (there should be none — held input is still
      exact input) and the throughput gain.

### 2.3 Cross-process determinism

Never tested. All repeats to date are **within a single GD launch**. Gates
parallel workers entirely.

- [ ] Same sequence, two separate GD launches, compare bit-for-bit.
- [ ] If it diverges, find the seed/state that differs before building workers.

---

## Track 3 — Objective C: hold duration & sub-frame timing

`hold=8` everywhere; hold duration has **never been varied**.

- [ ] Sweep `INJECT_HOLD` **at `DELTA_TICKS=1`**. Mandatory: `processQueuedButtons`
      drains per *physics step*, so at k>1 both push and release snap to the
      frame's first step and any "minimum hold quantum" is a frame-clock
      artifact. Same trap as the old `maxX` bug, new costume.
- [ ] Answer **C2: is cube jump height hold-invariant?** If yes, C's entire
      payoff moves to ship/UFO/robot — which needs Track 1.3's portals anyway.
- [ ] Design the action space: duration output (`hold for N ticks`) vs
      high-frequency policy (120–240 Hz) with an LSTM/Transformer context.

---

## Track 4 — The agent (nothing exists)

Do not start before the reward and training regime are decided (see Open
Decisions).

- [ ] **Reward function.** `maxX`? progress delta? death penalty? shaped by
      section? Shapes everything downstream.
- [ ] **Search algorithm.** Replace the greedy append-one-jump script; it stalls
      wherever two *coordinated* jumps are required, and it will. Beam / MCTS
      over input sequences, exploiting determinism + Track 2.1 restore.
- [ ] **Policy head** on `ConditionedTrunk`. The trunk exists; nothing consumes
      its features.
- [ ] **Objective D — in-context failure memory.** Trajectory + death point into
      a context buffer; attention reads the failure token on the next attempt and
      suppresses the action. Nothing built.
- [ ] **Objective E — uncertainty policy.** Substrate is good (coverage
      `UNKNOWN`/`SCANNED`/`TRUNCATED`/`ABSENT` in the wire format, plus
      `trajectory.py`'s certainty channel). No policy consumes it. Conservative
      defaults: mid-screen ship height, delay jump to platform edge.
- [ ] **Distillation** — search solutions into a reactive policy.

---

## Runtime-verification backlog (from README, verbatim priority)

1. [x] **RESOLVED — it is `m_unkVector560`.** Measured 0 -> 1 -> 0 across the
       trigger's lifetime while the other six stayed 0. Note it is
       `gd::vector<GroupCommandObject2>` **by value**, not by pointer.
       Candidates: `m_unkVector518`, `530`, `560`, `5b0`, `600`, `m_unkMap5c8`,
       `m_unkMap770`. Log all seven sizes per step with one known move trigger;
       exactly one should go 0 → 1 → 0. *Everything in the telemetry spec depends
       on this and nothing else does.* **Blocked on 1.1.**
2. [ ] **Per-tick advance for four speed buckets.** → Track 1.2, ready now.
3. [x] ~~`prepareMoveActions` fires per step~~ — **confirmed** (`endTick=3048`
       served `steps=3054`).
4. [ ] **`m_deltaTimeInFloat` vs the tick clock.** Log both during a 1.0s move
       trigger. Confirms the projection's time base end to end. **Blocked on 1.1.**
5. [ ] **Do area/enter effects move the collision rect?** Enter effect on a
       hazard, sprite disabled, see if the player still dies at the unanimated
       position. Decides whether `EnterEffectInstance` belongs in the projection.
       **Blocked on 1.1.**
6. [ ] **Which of `ActionType` 3 and 4 is rotation vs transform.** **Blocked on 1.1.**
7. [ ] **Timewarp semantics at `timeWarp != 1`.** Decompiled arithmetic says
       `numSteps` shrinks and `dtPerStep` grows — coarser steps, not faster time.
       Log `dtPerStep`, `numSteps`, per-step x advance before trusting either.
8. [ ] **Does `m_attemptTime` survive a checkpoint restore?** Load-bearing for
       both the input clock and the projection. → folds into Track 2.1.

---

## NEXT SESSION — start here

Ground truth for forward projection now **exists** and is committed. The
comparison itself has **not been run** — that is the immediate next task.

- [ ] **Compare `trajectory.py`'s prediction against the recorded data.**
      Two agents were spawned for this and stopped before writing any files, so
      there is nothing half-finished to clean up. Recreate as:
      - `trainer/validate_projection.py` — parse `MOVE`/`MOVE-TRIGGER`/`MOVE-OBJ`
        records from a Geode log, build the equivalent `GroupCommand` /
        `ObjectSnapshot`, drive `ForwardProjector`, and report mean error, max
        error, error vs prediction horizon, and lead/lag. Report position error
        in **units** and timing error in **ticks separately** — a one-tick
        offset and a genuine position error are different defects.
      - `trainer/test_projection_groundtruth.py` — a regression test carrying a
        dozen recorded tick/position pairs inline. This would be the repo's
        **first tier-(iii) test**: validated against recorded game data rather
        than against itself.
      - Reference log: `sandbox/Geometry Dash.app/Contents/geode/logs/Geode 2026-08-11 18.41.23.log`
        (480 MOVE records). Note `sandbox/` is gitignored — re-capture with
        `GDRL_SYNTH=1 GDRL_AUTOPLAY=1 GDRL_PROBE_MOVE=1 GDRL_BLOCK_INPUT=1 ./scripts/run_sandbox.sh`
        if the log is gone.
      - **First thing to check:** the one-tick activation dead time. Motion
        starts at tick 234, not the activation tick 233. If the projector
        assumes displacement begins at activation it leads by exactly one tick.

- [ ] **Track 0.2 is untouched.** No `predictor_spec.md`, no test-evidentiary
      audit, no independent validation of `SpeedProfile`/`UNITS_PER_TICK` was
      written. The one real datum: at x=300 the predictor says arrival at tick
      `300/1.298250437 = 231.08`; the trigger fired at 233. That 1.92-tick gap
      is an **upper bound** on predictor error — it has not been decomposed into
      predictor error vs GD's trigger-activation tick vs the newly-measured
      one-tick activation dead time, and the dead time alone could account for
      half of it.

---

## Corrections from independent validation (2026-08-11)

- [ ] **There is NO automated test asserting the mod's defaults are inert.**
      This claim has been repeated in commit messages, agent briefs and code
      comments, and it is false. The Python suite (`test_env`,
      `test_conditioning`, `test_trajectory`, `test_schema`) is pure Python and
      reads nothing from `mod/`. The only evidence that `GDRL_*` switches
      default to off is the README's 29-attempt run. **Stop citing a guard that
      does not exist** — either write one, or describe the evidence accurately.
      Writing one is not trivial: it means launching GD twice and diffing an
      attempt trace, so it belongs with the cross-process work in 2.3.
- [ ] **`MOVE-SUM` has never been observed with non-zero counters.** The synth
      level has no hazard, so autoplay never dies, so `resetLevel` never runs
      during gameplay — the only `MOVE-SUM` emitted is the pre-gameplay
      `steps=0 records=0 tracked=0`. The accounting exists precisely to
      distinguish "hook never fired" from "nothing moved" and is itself
      untested at runtime. Add a hazard to the synth level, or force a reset.
- [ ] **The `processMoveActions` detour is now installed unconditionally.** Its
      body early-returns on `!g_probeMove`, so behaviour is inert, but it is a
      new always-present trampoline where there was none. Offsetting: the
      `prepareMoveActions` hook became *less* eager (now gated on
      `g_probeCmdVec` alone).
- [ ] `MOVE-OBJ` dumps every object once per attempt — 13 lines on synth, but
      ~2400 on Stereo Madness. Cap it before running there.

### Partial evidence toward Track 2.3 (cross-process determinism)

Two **separate GD launches** produced **byte-identical** 480-line MOVE traces
(`diff` clean). That is the first cross-process determinism evidence in the
repo. It covers a *moving-object* trace, not a player trajectory, so it is
suggestive rather than closing 2.3 — but it is real, and it was free.

---

## Known gaps and hygiene

- [ ] **Dual mode conditioning.** Schema now carries player 2, but confirm
      `Regime` in `conditioning.py` consumes a second vehicle rather than one
      vehicle + a `dual` bool. Dual sections can run two different vehicles.
- [ ] **Greedy search stalls** where two coordinated jumps are needed — it only
      ever appends one.
- [ ] **Plateau widths for jumps 2–11** were resolved on a 4-tick grid and could
      be off by up to 3 ticks per edge. Only jump 12 is resolved to 1 tick.
- [ ] **Scope of every result so far:** cube only, Stereo Madness only, 1× speed
      only, single process. No portal, ship, mini, or gravity flip has ever been
      *replayed*.
- [ ] `mod/src/experiments.cpp` is explicitly disposable — delete once its
      findings are fully in the README.
- [ ] `cfprefsd` ignores `CFFIXED_USER_HOME`, so anything going through
      `defaults`/CFPreferences still escapes the sandbox. File I/O is redirected;
      preferences are not.
- [ ] **A sandboxed Codex run can corrupt `mod/build/`.** Observed: it left
      `build/bindings/codegen/Codegen` as a **0-byte file** and pointed
      `GEODE_CLI` in `CMakeCache.txt` at a nonexistent `mod/.offline-geode`
      stub — presumably working around having no network. Both fail in a
      confusing way, because the bindings CMake *skips rebuilding Codegen when
      the file merely exists* and then fails to exec it: `Abnormal exit with
      child return code: exec format error`. Repair without a full rebuild:
      ```sh
      rm -f mod/build/CMakeCache.txt mod/build/bindings/codegen/Codegen
      cd mod && GEODE_SDK=~/.geode-sdk geode build
      ```
      If you delegate mod builds to a sandboxed worker, verify
      `lipo -archs mod/build/gdrl.probe.dylib` afterwards.

---

## Open decisions (need a human)

1. **Reward function** — what is the agent actually maximising?
2. **Training regime** — search-and-distill (the original plan) vs online RL.
   Determines whether Track 2.1 is essential or merely nice.
3. **Target levels** — stay on Stereo Madness, or move to synth levels built to
   exercise specific mechanics? Synth gives control the main levels cannot
   (there are only *four* move triggers across all 21 main levels).
4. **When to stop measuring and start learning.** There is always one more
   verification; the environment is already good enough for a first policy.

---

## Reference

### Reproduce the 12-jump result
```
sequence: 325,712,1074,1162,1266,1798,1934,2154,2318,2482,2686,2878
hold=8 each, cube, Stereo Madness
expect: maxX=3959.183837891  deathTick=3048  t=12.700000662
        push=12 rel=12  lvl=1  input[clean blocked=0 leaked=0 ui=0]
```

### Constants
| Thing | Value |
|---|---|
| Physics timestep | 1/240 s, fixed |
| Tick clock | `lround(PlayLayer::m_attemptTime * 240.0)` — **never** `t == n/240` |
| x per tick at 1× | `1.298250437` |
| `m_playerSpeed` at 1× | `0.90` |
| Normal gravity | `0.9582` (corrected from 0.96) |
| Null-input death | `maxX=507.615234375`, tick 391 |
| Section index | `floor(x / 100)` |

### Methodology rules earned the hard way
- **An address is not a call site.** Verify with `bl` *and* `b` counts; zero of
  both means inlined. Virtuals are evidenced by neither.
- **Grepping a member name does not tell you which class owns it.**
  (`toggleGravityMode` belongs to `CreateParticlePopup`, not `PlayerObject`.)
- **A validity assertion only covers the failure mode it was designed for.**
  `input[clean]` meant clean-of-buttons and said nothing about which level was
  loaded — the game silently drifted to *Back On Track* and kept reporting clean.
- **When a metric moves, ask whether the measurement changed, not the
  simulation.** `maxX` was once frame-sampled and was measuring the frame rate.
- **±1 tick is not a determinism probe at depth.** Outcomes are piecewise
  constant in jump tick (jump 12 has a 48-tick plateau); "unchanged" is expected
  almost everywhere. Probe at plateau edges, or use repeated identical replay.
- Label anything unmeasured **UNVERIFIED**. Gaps named honestly beat confident
  guesses.
