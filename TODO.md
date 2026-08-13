# gd-rl — open work

Single place for what is left. Companion to `README.md`, which records what has
been **established**; this file records what has **not**.

Status as of commit `41b5eb7` + the 2026-08-12 session below.

> **Read "Session 2026-08-12" first.** It closes Track 0.1, and it invalidates
> the "READY NOW" labels on Tracks 1.2 and 1.3 — the synth portals have never
> fired, on any run, ever.

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
| Forward projection (Objective B) | **Validated against recorded game data** — motion model exact; the 1.9198-tick fire-tick lead is **corrected and independently re-validated** 2026-08-12. Residual is the game's own float32 drift (max `5.6457e-04` units, `0.0031` ticks) |
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

### 1.2 Measure the four unmeasured speed buckets  — **BLOCKED** (was "READY NOW")

**The portals have never fired.** See 2026-08-12; blocked on the synth portal y
fix. The claim below that "synth's speed portals already land at exactly the
requested x" is true as a *placement* claim and was doing silent duty as a
*functional* one.

Synth's speed portals already land at exactly the requested x. Only 1×
(`1.298250437` units/tick) is this repo's own measurement; 0.5×/2×/3×/4× are
community values and are **not** proportional to `m_playerSpeed`
(`1.6/0.9 = 1.778` vs `576.00/311.58 = 1.849`). A 4% error over a 400-tick
horizon is a third of a tile.

- [ ] Null-input run through a synth level with each speed portal.
- [ ] `dx = x[t+1] - x[t]` on the tick after the portal.
- [ ] Replace the four unverified constants in `UNITS_PER_TICK`.

### 1.3 Cross a real vehicle portal  — **BLOCKED** (was "READY NOW")

The ship portal at x=4500 was crossed at x-level on 2026-08-12 and produced no
`MODE` line and no `COND` edge. Same root cause as 1.2.

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

## Session 2026-08-12 — Track 0.1 closed, two new bugs found

Run under an orchestrator + implementer/tester split (`.claude/agents/`). Two
long measurement runs; the two most valuable results came from an agent
reporting its assigned task was *impossible*, and from an agent flagging a hole
in its own headline finding.

### A. Forward projection is validated. The defect is timing, not position.

`trainer/validate_projection.py` drives `ForwardProjector` against the 480
recorded `MOVE` records. The predictor **led the game by 1.9198 ticks =
0.35998 units**, constant across all 463 observation ticks.

| constant delay applied | mean abs error (units) | in ticks |
|---|---|---|
| none | 3.5998e-01 | 1.9199 |
| 1 tick | 1.7248e-01 | 0.9199 |
| 2 ticks | 1.5022e-02 | 0.0801 |
| 1.91970 ticks | **3.433e-05** | 0.0002 |

One constant shift collapses the residual **below the game's own float32 noise
floor** (1.393e-4 mean / 5.646e-4 max vs the theoretical line). Error vs horizon
is **flat, not a ramp** — the signature of a fixed activation offset, where an
accumulating integration error would ramp. The live-command path is clean:
1.555e-4 units mean over 3843 (T,h) pairs, lagging ~6 ppm because GD accumulates
elapsed time in float32 and the predictor uses float64.

**So the motion model and the shape of the motion are right.** Only the fire
tick was wrong. For this linear trigger the two error kinds are not independent
(lead × 0.1875 units/tick); they would decouple on an eased trigger.

Activation tick re-derived **from the displacement record alone**, with no splice
from the other launch: inverting the linear law at each of the 480 records gives
`a = 232.99934`, range [232.99699, 233.00020], sd 9.85e-4 — agreeing with the
`GDRL_PROBE_CMDVEC` value of 233 to 0.00066 ticks.

- [x] Compare predicted vs observed position. Done.
- [x] Quantify mean/max error, error vs horizon, lead/lag. Done, above.
- [x] Land a deterministic regression test carrying the recorded dataset —
      `trainer/test_projection_groundtruth.py`, 40 tests, the repo's **first
      tier-(iii) test**, with the data in `trainer/groundtruth_move_synth.py`
      as plain Python (a `.log` fixture would be silently untracked by
      `.gitignore`'s bare `*.log` rule).
- [x] **Audit of that test, resumed and closed 2026-08-12.** The concern was
      **real, not a false alarm.** `test_pending_path_leads_the_game_by_the_
      measured_amount` computed arrival tick `462.1606008087811`; the helper
      `_recorded_or_interpolated` was asked for ticks 462 and 463, and
      **neither was in the fixture** — both fell in the 73-tick hole between
      `SAMPLES` at tick 400 and tick 473. Both ends of the bracket, and so the
      `truth` the tolerance was measured against, were produced by the linear
      motion law, which is `trajectory.py`'s own model. The headline claim was
      a tier-(ii) claim wearing a tier-(iii) label.

      **The number survived; the evidence did not.** Against the real records
      at 462/463 the lead is `0.359978006` units / `1.919883` ticks, versus
      `0.359972776` / `1.919855` from the interpolant — `5.230e-06` units
      apart, 5% of the assertion's own `abs=1e-4`. Worst case anywhere in the
      gap is `8.4129e-06` units (`4.49e-5` ticks). So `MEASURED_LEAD_UNITS =
      0.35998` and the tolerance were *not* propped up by the interpolation —
      but that could only be established by going back to the log, which is
      what the fixture exists to avoid.

      The helper's own docstring claimed "every sample in that span is on the
      same line" as verification. That check was **vacuous**: there are no
      samples strictly inside 400..473, so it compared the two endpoints of the
      gap against the line drawn through those same two endpoints.

      **Fix, per the "carry more points rather than interpolate" reading:** all
      480 records are now in `groundtruth_move_synth.py` as `RECORDS` /
      `RECORDS_BY_TICK`, re-extracted from the surviving reference log (which
      is still on disk, 480 `MOVE` lines; the 15 existing `SAMPLES` verified
      byte-identical against it, 0 mismatches). `_recorded_or_interpolated` is
      replaced by `_recorded` (exact lookup, never interpolates) plus
      `_truth_at_fractional_tick` (adjacent recorded pair only, with an
      assertion that both bracketing ticks are recorded).

      **Falsification power, measured.** Inject a 0.05-unit non-linearity into
      the game's motion inside the gap, leaving all 15 `SAMPLES` byte
      identical: the old helper's truth moves `0.000e+00` — perfectly blind —
      while the new one moves `2.248e-02` and fails the assertion by 225x.

      **Tier after the fix: (iii)**, with exactly two modelled inputs, both
      named in the module docstring — the still-UNVERIFIED `_assumed_player_x`,
      and one unavoidable sub-tick interpolation across a single 0.1875-unit
      step between two adjacent *recorded* records. The headline test also now
      carries an interpolation-free bracket assertion alongside the precise one.
      Suite: 40 → 45 tests in that file, `trainer/` 167 → 172, all passing.
      (Superseded by the arrival-tick correction later the same day: that file
      is now **50** tests and `trainer/` is **182**.)

### B. The 1.9198 ticks decompose exactly. Crossing-to-activation latency is 0.

Measured off the `GDRL_ENV` channel, 57,009 observations, one attempt to level
completion (x=0 → 6041, tick 1 → 4653):

- **Start x is 0.0 exactly.** The alternative explanation — a player starting at
  x = −2.492 would reach x=300 at exactly tick 233, making the predictor right
  and the defect a coordinate-origin error — is **falsified**. No spin-up ramp
  either: dx is `1.298250436782837` from the very first step.
- **The origin convention is `x(t) = U·(t−1)`, not `U·t`.**
  `max|x − U·(t−1)| = 0.2597` vs `max|x − U·t| = 1.3135` (a whole tick).
- **The crossing:** tick 232 → x=299.8955078125 (short); tick 233 →
  x=301.1937561035156. GD activates on the **first integer tick at which the
  player's own sampled x ≥ the trigger x**.

`233 − 231.08022266 = 1.91977734` = **1.0000** tick of origin convention +
**0.91978** of continuous-vs-integer. No residual. Correct rule:
`fire_tick = 1 + ceil(x_target / U)`.

**`CROSSING_TO_ACTIVATION_TICKS_UNVERIFIED = 1` in the fixture should become 0** —
that 1 was the origin error double-counted as latency. The separate one-tick
*object-displacement* dead time (activation 233, first displacement 234) is
downstream and unaffected.

**This corrects TODO's own prior claim** that the dead time "could account for
half" the gap. It accounts for **none** of it: the dead time is already
structurally present in the predictor's pending path (`elapsed = max(0, ...)`
yields p=0 on the fire tick), so subtracting a tick for it would double-count
and overshoot.

### C. Player x is float32-accumulated — tier (iv)

`x[n+1] = float32(x[n] + float32(U))` reproduces **all 4,653 samples
bit-exactly**; a double accumulator with a float32 store does not. So per-tick
dx is piecewise constant *per float32 binade* — it takes 9 distinct values across
the run, spread 1.2207e-4 (9.4e-5 relative). **That is the instrument, not
variable speed.** Rounding is upward-biased, so real x runs ahead of `U·t` by
+0.2597 units after 4,652 ticks (+6.9e-5/tick above x=4096) — deterministic
drift, not noise, and ~0.03 units at a 400-tick horizon.

1× reproduced as `1.298250436782837` = `float32(1.298250437)` bit-for-bit, and
cross-checked **bit-identical** against README's Stereo Madness null-input
`maxX = 507.615234375` — different level, different probe, ~550 attempts old. So
player-x motion is not perturbed by the telemetry pass, and the method that
returned the negative result below is sound.

### D. BUG: every synth portal is 90 units too high and has never fired

Every synth object's runtime `m_positionY` is its level-string y **+ 90**,
confirmed 4-for-4 across distinct values, read at `m_positionX/Y`:

| synth level-string y | runtime y |
|---|---|
| 585 (`kGroundY+480`) | 675 |
| 345 (`kGroundY+240`) | 435 |
| 105 (`kGroundY` — all 5 speed portals + the ship portal) | **195** |
| 45 (`kGroundY-60`) | 135 |

`synth.hpp`'s `kGroundY = 105.f` is written as if the level string were runtime
coordinates. The player runs at runtime y=105 (measured constant, 4,653 ticks,
`onGround=1` for 4,652, never jumped). Portal centre is therefore 90 units above
player centre; cube half-height 15 puts the player rect at y ∈ [90,120], so
overlap needs portal half-height > 75. Speed portals are nowhere near 150 tall.

Consequence: `m_playerSpeed = 0.8999999761581421` on **all 57,009 observations**,
past every speed portal (1200/1800/2400/3000/3600) and the ship portal (4500),
out to x=6041. Zero `COND` edges after step 0, zero `MODE` lines.

- [ ] **Fix: place the five speed portals and the ship portal at level-string
      y = 15** so runtime y = 105. Do **not** blanket-shift `kGroundY` — the move
      trigger's x-crossing activation at tick 233 is the one measurement that
      currently works end to end and its y must not be disturbed.
- [ ] The y-misplacement is *sufficient* and quantitatively established; that it
      is the *only* cause is only decidable by the rerun. Do not claim the
      portals work until one has fired.
- [ ] `synth.hpp`'s header comment still cites the falsified "four move triggers
      across 21 levels" census number as justification for generating content.
      Correct or remove it.

### E. BUG: `m_sectionXFactor` is inverted, collapsing the ENV object window to 0.64 units

`mod/src/telemetry.cpp:591-606` treats `m_sectionXFactor` as a **divisor**. The
live value is **0.01** — a multiplier, i.e. `1/width`. Section index is
`floor(x · sxf)`, not `floor(x / sxf)`. With `col1` clamped to `col0 + 63`,
`maxX` becomes `64 × 0.01 = 0.64`, so the advertised window is `[px − 400, 0.64]`.

Evidence: `objectCount = 0` on **4,344 of 4,653** gameplay ticks; max ever 3;
only 3 distinct objects ever entered, all at x=0.0 — on a 13-object level
spanning x=0–6340. Objects at x=0 stayed visible for exactly 309 ticks, and
`400 / 1.298250437 = 308.1`. `telemetry.cpp`'s own UNVERIFIED note (5) posed
this exact question; the answer had been in the logs since 2026-08-11.

**This blocks every object-window consumer** — Objective A conditioning and all
`trajectory.py` validation against real geometry — not just the speed work.

- [ ] Do not blind-flip the operators. Measure `m_sectionXFactor` directly and
      **also `m_sectionYFactor`** — if y is inverted too, the vertical window has
      the same defect and nobody has looked.
- [ ] Cross-check the arithmetic two independent ways: against the documented
      `section index = floor(x/100)`, and against `m_sections.size()` vs level
      length.
- [ ] **The `100.f` fallback only makes sense under the divisor reading** and is
      wrong by 10,000× under the multiplier one. Prefer failing loudly to a new
      magic constant: a column the coverage mask cannot speak for must not sit
      inside the region Python is told was scanned, or "unknown" silently
      becomes "empty".
- [ ] Check whether `trainer/schema.py` / `env.py` read the header's
      `sectionXFactor` and would be affected.

### F. The `GDRL_ENV` transport had never been run against the live game

Zero `[gdrl] ENV` lines exist in any of the 52 surviving logs (26,932 lines)
before today. It works: 57,009 published steps, and on all rows
`timeouts=0 leaked=0 uiEvents=0 blocked=0 status=OK inputVerdict=CLEAN`, with
`obs.problems()` empty on the first frame.

### G. Further corrections to README / TODO

1. **README ~993 annotates `0.187501179` as `(= 90/480)`.** `90/480 = 0.1875`
   exactly. `0.187501179` is the mean over the **479 non-final** steps; over all
   480 it is `0.187500000`, and the logged dy sum to `89.999999990`. *Confirmed
   independently.*
2. **README's linearity residual (3.99e-4 max / 9.2e-5 rms) is a least-squares
   fit.** Against the *theoretical* line `435 + 90(t−233)/480` it is
   **5.646e-4 max / 2.224e-4 rms**. Both are real; README does not say which fit
   it used, and the lsq figure is ~4× smaller only because the fit absorbs the
   constant part of the float32 drift. Both are in the fixture under distinct
   names.
   **CLOSED 2026-08-12, and it was worse than stated.** Both figures are in the
   fixture under distinct names, each labelled with its fit — verified — and
   `test_the_two_linearity_residuals_are_against_the_two_stated_fits` now
   recomputes each from the 480 `RECORDS` against the fit its name claims, so
   the labels can no longer drift from the numbers. Recomputed:
   theoretical `5.6457e-4` max / `2.2239e-4` rms, lsq `3.9875e-4` / `9.3951e-5`,
   lsq slope `0.187501148182` — all matching the fixture.
   **But README's least-squares pair `3.99e-4 max / 9.2e-5 rms` was not one
   fit's output at all**: no single fit produces both. `9.2e-5` is the fit over
   the **479 non-final** steps (`9.2252e-5`, whose max is `1.6364e-4`);
   `3.99e-4` is the fit over **all 480** (whose rms is `9.3951e-5`). Pairing
   them overstated the max and understated the rms simultaneously — the same
   final-short-step exclusion that already bites the `dy` row. README's table
   and its correction note are fixed; the fixture was right all along.
3. **`trajectory.py:409` encodes `311.58 / TICK_HZ = 1.298250000` under a comment
   citing "measured: 1.298250437".** The code does not carry the number it
   cites — 0.34 ppm, 1e-4 units over 231 ticks. Named so it is not rediscovered
   as a bug. *Confirmed independently.*
4. **A trailing dead step.** CMDVEC removes the command at 715 while the last
   displacement is 713, so tick 714 was also live-but-zero. README records
   `713 = 233 + 480` but not the trailing idle step.
5. **`ForwardProjector`'s default `horizon_ticks=240` is only 311.6 units** —
   less than the reference target's x=600. At the default the arrival tick clamps
   and the projection silently answers a different question. Not a bug; a trap.
   **Confirmed 2026-08-12, with the count.** The clamp is `trajectory.py:919`
   and `:923`, `tick = max(0.0, min(float(self.horizon_ticks), tick))`. Every
   `ForwardProjector` call site was instrumented and the arrival ticks compared
   against the horizon. **Six tests in `test_trajectory.py` run at the default
   horizon and silently receive a clamped arrival tick:**

   | test | obj.x | arrival returned | unclamped would be |
   |---|---|---|---|
   | `test_static_object_projects_to_itself` | 600 | 240.0 | 462.161 |
   | `test_only_the_targeted_group_moves` | 600 | 240.0 | 462.161 |
   | `test_two_commands_on_one_group_compose_additively` | 600 | 240.0 | 462.161 |
   | `test_unmodellable_command_leaves_the_object_put_and_says_so` | 600 | 240.0 | 462.161 |
   | `test_render_batch_is_per_sample_independent` | 500 | 240.0 | 385.134 |
   | `test_raster_feeds_the_conditioned_trunk_unchanged` | 320 | 240.0 | 246.486 |

   (`test_objects_outside_the_window_are_clipped_not_wrapped` also clamps at
   x=100000, but there the clamp is the point. `test_horizon_caps_the_lookahead`
   and `test_an_object_outrunning_the_player_is_flagged_unknown` clamp against
   explicit horizons.)

   **None of the six currently produces a wrong pass** — none asserts on
   `arrival_tick`, and the three with a `duration` ≤ 240 ticks have `p` clamped
   to 1 at both the clamped and unclamped tick, so `y` is unchanged either way.
   The cost is coverage, not correctness: at the default these tests never
   exercise the arrival-tick fixpoint they appear to be about.
   **This matters to the queued arrival-tick correction.** That task changes
   the fire tick, i.e. exactly the quantity these six tests are blind to, so
   they will not move when it lands and must not be read as evidence it is
   inert. `test_projection_groundtruth.py` passes `horizon_ticks=900`
   explicitly and does exercise it. Left unfixed deliberately: raising the
   horizons changes what six tests cover and is a scoping decision, not a
   trivial edit.
6. **README's null-input death pair `maxX=507.615234375, tick 391`** is one tick
   off the ENV channel's convention — that x is the ENV run's tick-**392**
   sample, bit-identical. Both are right in their own frame (post-physics x at
   tick n = pre-physics x at tick n+1), but nothing states which frame `maxX` is
   in.
7. **The census has only ever been run on the synth level.** `CENSUS lvl=0` is
   the only census line in all 52 logs, so the "censusing all 21 levels" results
   quoted in `synth.hpp` and README are not reproducible from surviving evidence.

### Still UNVERIFIED after this session

`UNITS_PER_TICK` indices 0/2/3/4; telemetry passivity for anything other than
player x; whether the observation is pre- or post- the player's *own*
integration (the fixed sample point makes dx correct either way, but
portal-crossing attribution will need it once portals fire); whether the mod's
defaults are inert (still no automated test — see "Corrections from independent
validation"); non-zero easing; rotation/transform (`ActionType` 3/4); non-unit
`m_moveMod*`; lock flags; spawn delays; multiple commands on one group;
multi-group `MOVE-OBJ` parsing (the log only ever has groupCount 0 or 1);
whether `m_alreadyUpdated` (+0x1b0) is the dead-time mechanism.

### Independent validation of the arrival-tick correction (2026-08-12, tester)

The correction from queue item 3 below landed and was checked by an agent that
did not write it, `trainer/` only, no GD launch. Suite `182 passed`
(`test_trajectory.py` 53, `test_projection_groundtruth.py` 50, `test_env.py` 39,
`test_conditioning.py` 25, `test_schema.py` 15). **All five claims survive.**
Two carry evidence weaker than their write-ups assert, and those two are the
resumable work.

**Confirmed, independently re-derived without importing `trajectory.py`'s
logic:**

* Crossing at `232.080223` → `ceil` = 233 (matches the measurement), `round` =
  232 (**falsified**). Under the old `U·t` origin the crossing is `231.080223`
  and `ceil+1` / `round+2` / `floor+2` **all** give 233 — so the earlier "not
  decidable" verdict genuinely was an origin artifact, exactly as claimed.
* Stronger than the `ceil`/`round` framing: the rule need not be inferred. First
  integer tick with **accumulated** x ≥ 300 is 233 by direct read; with the
  line, also 233. Feeding the real float32 x rather than the line at every
  observation tick 1..259, `ticks_to_activation` returns fire tick 233 at **all
  259, zero failures**. Margin is `0.0802` ticks to the boundary against an
  accum-vs-line drift at tick 232 of `−0.000264` ticks — ~300×.
* `maxX = 507.615234375` reproduced **bit-exactly**; five rival models miss by
  ≥ 22 float32 ulps. This independently validates the `UNITS_PER_TICK[1]` →
  `1.2982504367828369` change as a side effect (the old `311.58/240` fails it).
* Drift bound reproduced to every digit: 240-tick window `0.021458` units /
  `0.016528` ticks; 400-tick `0.035763` / `0.027547`. Also `max|x − U(t−1)| =
  0.2597` and `max|x − U·t| = 1.3135`.

**Resume here — three defects in the evidence, not in the code:**

1. **The "residual equals `LINEARITY_RESIDUAL_*_THEORETICAL` to 1e-12,
   sign-flipped" claim is circular.** In
   `test_residual_against_every_one_of_the_480_records`, the assertion
   `err == approx(-(recorded_y - line), abs=1e-12)` is an identity in which
   `recorded_y` cancels algebraically. Demonstrated: replacing every recorded
   value with `rec + uniform(-500, 500)` leaves the assertion passing with
   **max violation `0.000e+00`**. What it actually asserts is `out.y == line`,
   which is true and in fact *exact* (`max |out.y − line| = 0.000e+00` over 480
   ticks), but it is a tier-(i) regression on the predictor and carries **zero**
   information about GD. Same issue with `CORRECTED_RESIDUAL_BOUND_UNITS =
   gt.LINEARITY_RESIDUAL_MAX_THEORETICAL` and the `worst == approx(...)` check:
   a fixture constant compared against a recomputation from the same records.
   The *conclusion* ("the leftover is GD's float32 drift at ~6.1 ppm") is
   supported — but by the separate `LSQ_SLOPE = 0.187501148182` measurement, not
   by this equality. **To do: relabel these as tier (i) in the docstrings.** Do
   not delete them; they do have falsification power on the predictor.

2. **The "corrected model hits exactly the audit's fitted-shift figure
   `3.4332e-05`" agreement is a construction artifact — this repo's
   piecewise-constant trap in a new costume.** Both figures are bit-identical
   (`3.433199998426062e-05`, delta exactly `0.0`), but only because the
   record-minus-line residual sits on a **flat float32 plateau** there: ticks
   462, 463 and 464 all have `record − line = −3.433200e-05`. The old fit's
   arrival was `462.1606`, the corrected one is `463.1604`; both land inside the
   same plateau, so the figure is invariant to the one tick they disagree about.
   It is **not** the corrected model rediscovering a fitted value. The
   defensible statement is "same noise floor, **zero** free parameters instead
   of one." **To do: restate it that way in `validate_projection.py`.**
   Related: the section-4 sweep's `n=464` is `n_effective=1` — the reconciliation
   offered is **real** and visible in the tool's own output (`predicted arrival
   463.160445 .. 463.160445 (distinct: 1)`, and `mean == rms == max`, which is
   only possible if all 464 values are identical). For the record, `3.4332e-05`
   sits at the **55.8th percentile** of the 480 per-record `|err|` (median
   `3.0518e-05`, mean `1.3932e-04`, max `5.6457e-04`) — a near-median single
   sample of a right-skewed distribution, so neither a cherry-pick nor a floor.

3. **The float32 bias direction is stated as universal and is ~79/21.**
   `trajectory.py` says "the rounding is upward-biased, so the real player
   arrives fractionally EARLY, which means the error direction is the predictor
   firing a trigger one tick LATE (the dangerous direction)." Measured over the
   window that actually governs a lookahead: at 240 ticks, **913 of 4413
   (20.7%)** windows are signed-negative — the real player is *behind* the line,
   so the predictor fires **early**, not late. At 400 ticks, 730 of 4253 (17.2%).
   Worst negative `−0.007839` units (`−0.006038` ticks) vs worst positive
   `+0.021458` (`+0.016528`). And the sign is negative **precisely where the
   recorded trigger lives**: at tick 232 the drift is `−0.000343` units, the
   opposite of the stated bias. The `+0.2597` figure is an end-of-run number
   that only builds past x≈2000 (at tick 1000 it is `+0.003624`). The safety
   *conclusion* holds — bounded, sub-0.03-tick, dominant risk is firing late —
   but a reader taking the phrasing as a guarantee would wrongly conclude the
   early-fire direction is impossible. **To do: reword to "predominantly", with
   the 20.7% figure.**

**Tier mismatch to fix while there:**
`test_the_documented_float32_drift_bound_is_the_measured_one` labels itself
**"Tier (ii)"** and says it is "recomputed over the full 4,653-tick recorded
run". No recorded data enters it — it *simulates* 4,653 ticks. That is tier (i),
a regression on a documented constant. `trajectory.py`'s own docstring states
this honestly ("arithmetic on the measured accumulator, not an observation");
the two docstrings disagree about the tier of the same number.

**The provenance gap that is still open, and it is the real one.** The
`ceil`-vs-`round` conclusion survives the weak provenance of the two transcribed
player-x values — they are corroborated by the bit-exact accumulator, by the
Stereo Madness anchor on a *different level*, and by activation tick 233 being
re-derivable from the 480 MOVE records alone (`232.999340 ± 9.854e-04`) off a
log that *is* on disk. But the argument **splices runs**: the player-x law is
from the 2026-08-12 `GDRL_ENV` run, while activation tick 233 is from a
2026-08-11 CMDVEC launch plus the 2026-08-11 MOVE record, and **nothing asserts
the ENV run was on the same synth level.** That is the `input[clean]` failure
mode verbatim — an assertion whose name does not cover the level-identity
question. If it were a different level the crossing argument dissolves.

* To reach durable tier (iii): log the ENV player-x stream to disk **with a
  level-identity assertion**, and re-extract the 232/233 values from the file.
* To reach tier (iv): one launch, one level, `GDRL_PROBE_CMDVEC` **and** player
  `m_positionX` in the same stream, so crossing and activation come from one run.

### Queue for next session, in order

1. **Fix D and E** (both C++, one agent — concurrent `geode build`s race).
   D unblocks Tracks 1.2 and 1.3; E unblocks every object-window consumer.
2. ~~**Finish the stopped audit of `test_projection_groundtruth.py`**~~ — done,
   see A.
3. **Correct `trajectory.py`'s arrival-tick model** per B and C — **landed and
   independently validated**, but only **partially**: the `(t−1)` origin and
   zero crossing latency are in; **float32 accumulation is deliberately NOT** in
   `SpeedProfile`, which still models x as the continuous line. That is a
   defensible call (O(1) closed form vs O(horizon); only bucket 1 has a
   float32-exact `U`) and is documented with a measured bound in
   `trajectory.py`'s "PLAYER X IS FLOAT32-ACCUMULATED" section. Recorded here so
   the item is not read as fully closed.
4. **Rerun the speed measurement** once D lands — the tester's method reproduces
   all four buckets in one ~20s attempt with no further work.
5. Then Track 0.2 (predictor spec + test-tier audit), which A and B now feed.
6. **The three evidence-labelling fixes above.** `trainer/` only, no GD needed.

Task #8 (a synth trigger at x=300.65, arrival 231.5809, frac 0.58) is **largely
superseded**: the activation mechanism is now measured directly rather than
inferred, so no rule-guessing is needed. **Correction 2026-08-12:** it is
superseded outright, not "residual value only if `ceil`-vs-`round` stays open" —
that question is now *closed*, and the two questions still open (`>=` vs `>`;
whether GD quantises against the float32 x or the line) need a crossing landing
within **~0.03 ticks of an integer**, which `frac = 0.58` does not provide. A
**new** trigger x is required; x=300.65 would answer neither.

---

## Previous session's next-step notes (superseded by the above)

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
