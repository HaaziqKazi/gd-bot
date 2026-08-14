# gd-rl — open work

Single place for what is left. Companion to `README.md`, which records what has
been **established**; this file records what has **not**.

Status as of commit `8dd5ceb` + the 2026-08-13 (L) and 2026-08-14 (M) sessions
below.

> **Read sections L and M first.** Between them they close I, J and K, harden
> the observation decoder, and delete one loop that could never have done
> anything. J turned out to be the dangerous branch: **GD was emitting the
> player's own collision proxy as a SOLID block on top of the player**, on every
> frame, on every level.
>
> **THE BENCHMARK IS DECIDED: Benchmark A, true sightreading** (2026-08-14, by
> Rex). Read "Open decisions → 0" before writing any agent code. It rules out
> engine cloning and lookahead beyond the sensor horizon, promotes
> `GDRL_ENV_WIN_*` from performance knobs to the **sensor definition** enforced
> in the mod, and makes Objectives D and E load-bearing.
>
> **Two claims in this file turned out to be false when written** — see L1 (the
> vacuous-test claim was true of one assertion, not the enclosing test) and M3
> (`COND step=` was *not* 0 in every log; it reads 416 in six lines of one). Both
> conclusions survived; both supporting observations did not. Treat unchecked
> numbers in this file as unchecked, including these.
>
> **Then read "Open decisions", which now leads with a benchmark question that
> outranks everything else in this file.** Whether hidden simulator rollouts are
> allowed is not a tuning knob — it decides whether this project is a planning
> problem or a learning problem, and therefore which of Objectives A–E are
> load-bearing. Do not start Track 4 before answering it.
>
> **Start at "Queue for next session".** Nothing is blocked on a build or a
> measurement; the tree is green at 207 tests and the mod builds universal.

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
| Conditioning (Objective A) | **Mod side evidenced end-to-end** (H, L2, M2): six synth portals fire incl. `cube -> ship`, both `COND` and `MODE` now report it on the same tick, and the phantom that made the geometry untrustworthy is identified and filtered. **The Python side has never been checked against it** — `conditioning.py` vs what the mod actually emits is the open half (Track 1.3) |
| Object observation window | **Fixed, confirmed live, and de-phantomed** (H/E/L2): `objectCount == 0` on 0 of 3,722 ticks, was 4,344 of 4,653; the player's own collision proxy no longer reports as a SOLID block on the player. **Any `objectCount` recorded before 2026-08-13 was inflated by 5–10** and must be recomputed, not trusted |
| Observation decoder honesty | **Hardened** (L3/L4/N): refusal is structurally distinguishable from "known-empty" and cannot be laundered into an array. `test_env.py` regraded by mutation 7/25 → **46/46**, and the harness now lives at `trainer/mutate.py` because the score decays silently as code moves (N2) |
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

### 1.2 Measure the four unmeasured speed buckets  — **UNBLOCKED** (see H)

**The portals now fire.** The y fix landed and was confirmed live; all five speed
portals produce a real `m_playerSpeed` change. The blocker below is resolved and
the measurement is the next thing to run.

**Do not use H's by-product table as the answer.** Player x is float32-accumulated,
so per-tick dx is piecewise constant *per binade* and each of those rows was
sampled at a different x magnitude — they are ±1 ulp, not constants. Measure
where the quantisation is smallest, or by a method robust to it, and deliver the
exact float32 increment per bucket in the form
`1.2982504367828369 == float32(1.298250437)`.

Also re-test the premise: the non-proportionality below was computed from the
*community* values, so if those are wrong the premise may be too.

**Historical note.** The claim that "synth's speed portals already land at
exactly the requested x" was true as a *placement* claim and was doing silent
duty as a *functional* one for the whole life of this item.

Synth's speed portals already land at exactly the requested x. Only 1×
(`1.298250437` units/tick) is this repo's own measurement; 0.5×/2×/3×/4× are
community values and are **not** proportional to `m_playerSpeed`
(`1.6/0.9 = 1.778` vs `576.00/311.58 = 1.849`). A 4% error over a 400-tick
horizon is a third of a tile.

- [ ] Null-input run through a synth level with each speed portal.
- [ ] `dx = x[t+1] - x[t]` on the tick after the portal.
- [ ] Replace the four unverified constants in `UNITS_PER_TICK`.

### 1.3 Cross a real vehicle portal  — **acceptance criterion MET** (H, M2)

Both instruments now fire on a real cube→ship crossing at x=4468.861, on the
same tick, reproduced bit-identically across runs:

```
[gdrl] MODE tick=3092 x=4468.861 from=cube cleared=cube type=5 p1
[gdrl] COND tick=3092 x=4468.861 ship  grav=dn size=1.00 spd=0.90 ...
```

Read the pair together: **`MODE` names the vehicle departed** (it hooks
`switchedToMode`, which *clears* the outgoing vehicle), and `COND` names the one
entered. See M2.

- [x] Drive through the synth ship portal; confirm `COND`/`MODE` lines fire.
- [ ] **Verify the regime the mod reports matches what `conditioning.py`
      expects.** Not done — the mod side is evidenced, the Python side has never
      been checked against it. This is the half that makes Objective A more than
      flag-decoding.
- [ ] Repeat for at least one other vehicle once 1.1 lands. Note only
      `type=5 -> ship` is confirmed live; `19 -> ufo` is disassembly-only.

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

### H. D and E are FIXED and confirmed against the live game

Both landed this session. `mod/src/{synth.hpp,synth.cpp,telemetry.cpp,probes.cpp}`.
Build green and universal; every change sits behind `GDRL_SYNTH` / `GDRL_ENV` /
`GDRL_CENSUS`, all still defaulting to off.

**A portal fired — the first one ever.** From `Geode 2026-08-12 17.15.38.log`,
reproduced byte-identically in two further launches (17.23.09, 17.25.07):

```
[gdrl] COND step=0  x=1163.871 cube grav=dn size=1.00 spd=1.10 gmul=0.96 warp=1.00 dual=0 sideways=0
```

`spd` had been `0.90` on all 57,009 prior observations. All six now fire — 1.10 @
x=1163.871, 1.30 @ 1758.925, 1.60 @ 2361.310, 0.70 @ 2971.625, 0.90 @ 3570.410,
and `cube -> ship` @ 4468.861. Activation x sits ~36 units before each portal's
centre, which is rect overlap (2× portal rect starts at 1174.5; player
half-width 15 → contact at 1163.87 + 15 = 1178.87).

The fix restates `synth.hpp`'s layout constants in **runtime** y with a measured
`kLevelStringYOffset = 90.f` and a `levelStringY()` converter; `kGroundY` is
deleted, because it was a runtime coordinate being written into a level string.

**The move trigger is provably undisturbed** — verified three ways rather than
assumed, since the forward-projection ground truth depends on it: its
level-string bytes are unchanged (`1,901,2,300,3,45,51,1,10,2,28,0,29,90,30,0`),
its runtime position is `OBJLIST-OBJ i=1 id=901 x=300.0000 y=135.0000`, and
`GDRL_PROBE_CMDVEC` still gives `tick=233 v560=1 … tick=715 v560=0`, identical
to the 2026-08-11 run the ground truth was recorded against.

**Speed table, as a by-product — `±1 ulp`, NOT final constants.** Null-input run,
4,242 ticks, segmented by `m_playerSpeed`:

| `m_playerSpeed` | dx/tick (median) | ×240 |
|---|---|---|
| 0.8999999761581421 | 1.298248291015625 | 311.580 |
| 1.100000023841858 | 1.6142578125 | 387.422 |
| 1.2999999523162842 | 1.949951171875 | 467.988 |
| 1.600000023841858 | 2.39990234375 | 575.977 |
| 0.699999988079071 | 1.04638671875 | 251.133 |

Player x is float32-accumulated (see C), so per-tick dx is piecewise constant
**per float32 binade** and each row above was sampled at a different x magnitude.
Track 1.2's dedicated measurement is still required; do not paste these into
`UNITS_PER_TICK`.

**Section factors, measured:**

- **`m_sectionXFactor = 0.01`** (on the wire as float `0.009999999776482582`) — a
  multiplier, 1/width.
- **`m_sectionYFactor = 0`**, and this is the measurement, not a hole. It is
  **not** a second inversion. GD's grid is effectively 1-D: `m_sections`' middle
  vector is 1 deep on every level dumped (max y column = 1 on Stereo Madness's
  2,399 objects and on synth's 13), and `scanObjects` never used it — the
  vertical filter is a direct `m_positionY` compare. **Nothing may "fix" y by
  symmetry with x, and nothing may divide by it.**

Two cross-checks, independent of each other and of the code: `1/0.01 = 100`, so
`floor(x·sxf) == floor(x/100)`, README's documented rule; and
`m_sections.size() == floor(levelLength·sxf) + 1`, exact on both levels (Stereo
Madness 26724 → 268 columns measured 268; synth 6340 → 64 measured 64). The
divisor reading would need 2,672,400.

**Result:** `objectCount == 0` on **0** of 3,722 gameplay ticks, against 4,344 of
4,653 before. Window width a constant 1800.0 tracking the player at exactly
−400/+1400; 19 columns described per step (= 1800/100 + 1), `objectsDropped = 0`,
`obs.problems()` empty throughout.

The `100.f` fallback was **removed, not replaced**. A non-positive or non-finite
factor now yields a zero-area window, all 64 columns `UNKNOWN`, `objectCount = 0`
with `OBJECTS_UNAVAILABLE` set (which `env.py` already decodes as "did not
look"), and one `log::error`.

- [ ] **UNVERIFIED:** that GD's own column index is `floor(x·sxf)` *in float*
      rather than some other rounding. The two cross-checks pin the factor, not
      the rounding mode; a boundary object could still be one column out.
- [ ] **UNVERIFIED:** that the +90 level-string→runtime y offset is universal
      rather than a property of these object classes. 4-for-4 from the prior
      session plus 13-for-13 from this one, **all on synth**. No main level
      checked.
- [ ] Inertness after these edits is by construction (all behind `GDRL_*`
      gates), **not** by test — the repo still has no automated inertness test
      and no no-switch baseline diff was run.

### I. `env.py` carried the SAME inversion — fixed, but `test_env.py` was never reached

The C++ fix would have been silently defeated on the Python side. `column_span()`
and `known_mask()` both divided by `sectionXFactor`; with `sxf = 0.01` the index
landed ~10⁴ out of range, `valid` was False everywhere, and **the coverage mask
was incapable of reporting anything as known.** The loopback fixture also
fabricated `sectionXFactor = 100.0, sectionYFactor = 100.0` — values the game
produces neither of.

Fixed in `trainer/env.py`: `section_factor()` / `section_width()` / `column_span()`
now return `None` rather than substituting a constant, `known_mask()` multiplies
and refuses whole-frame on `OBJECTS_UNAVAILABLE` or an unusable factor, and the
fixture publishes `MEASURED_SECTION_X_FACTOR` / `_Y_FACTOR` / `_LEVEL_LENGTH` /
`_COLUMNS` carrying the measured values and their provenance.

**This work was interrupted by a session limit mid-edit**, leaving a missing
`import math` — 27 `test_env.py` failures from one absent line. Added; suite is
**182 passing**.

- [ ] **`trainer/test_env.py` was never touched.** Two things remain:
      - **No test exercises the new refusal path.** `publish()` takes
        `section_x_factor` precisely so a test can inject an unusable value and
        assert the decoder *refuses* rather than substitutes. Nothing does. The
        most valuable branch of this fix is currently unexercised.
      - **The three `known_mask` tests were never graded.** They now run against
        the measured 0.01 only because the fixture's default changed underneath
        them. They pass, but nobody has checked *what they would still catch* —
        and they are exactly the tests that passed happily against a fabricated
        header for the whole life of the file.

### J. BUG — **RESOLVED 2026-08-13, see L2.** It was the player's own collision proxy

*The investigation below is retained for the reasoning trail. The answer: object
1816 is `GJBaseGameLayer::m_player1CollisionBlock` / `m_player2CollisionBlock`,
GD's own per-player collision proxy, carrying the player's position from the
**previous** physics step. It is reachable through the section grid but not
through `m_objects` — which is exactly why the census never saw it. The answer
to "is the player itself being emitted as geometry?" is **yes**.*

### J (original text). BUG (open, uninvestigated): a phantom object is glued to the player

Revealed — **not caused** — by the section-factor fix; the old 0.64-unit window
saw nothing at all. Every `GDRL_ENV` step carries **five entries with
`objectID=1816, objectType=39, kind=1 (SOLID), uniqueID=25`** — the *same*
`uniqueID` five times — at the player's x **lagged one tick**, y=105, 30×30.

**It is not in `m_objects` at all**: the census sees 13 objects on synth and no
1816. It appears to exist only in the section grid.

Consequences: `objectCount` averaged 7.64 where the true in-window count is 2–3,
and **any consumer that does not dedupe by `uniqueID` sees a solid block sitting
on the player.** In neither README nor TODO before now.

- [ ] What is it? Check `objectType=39` / `objectID=1816` **against the
      bindings**, not a community table — key `36` is still open for exactly
      this reason.
- [ ] Why five, with one `uniqueID`? "Spans five columns" does not obviously fit
      entries pinned to the player's lagged x. Resolve rather than assume.
- [ ] Does it appear on a real level, or only synth? Decides artifact vs
      property of the observation path.
- [ ] **Is the player itself being emitted as geometry?** If so every consumer
      sees the player as a solid obstacle on top of itself, which poisons
      Objective A conditioning and all trajectory validation against real
      geometry.
- [ ] **Do not fix before identifying.** Dedupe-by-`uniqueID` vs
      filter-by-object-id is a semantic decision, and filtering by an id whose
      meaning is unknown is the guess this repo bans.

### K. `MODE` never fires, and `COND step=` is always 0 — **BOTH RESOLVED 2026-08-14, see M**

- [x] **`MODE` lines never fire, even on a genuine vehicle change.** Root cause
      found: it was **our guard**, not GD's plumbing. `switchedToMode` *clears*
      the outgoing vehicle — the incoming one is set by the caller *after* it
      returns — so entering ship from cube is `before == after == cube` and the
      `if (before == after) return;` swallowed every entry. Guard removed; the
      line now fires and names the vehicle **departed**. See M2.
- [x] ~~**`COND step=` is 0 in every log ever**~~ — **this claim was false**, and
      it was false when written. In `phantom-synth-jump200.log` the field reads
      `step=0` once (at `x=0.000`) and **`step=416` on all six later lines**
      (verified in the main thread: `1 COND step=0`, `6 COND step=416`). The
      *conclusion* held — `m_currentStep` is not the physics-tick counter and was
      useless as logged — but the supporting observation did not, and it went
      into this file unchecked. `step=` is now `tick=`, wired to
      `lround(m_attemptTime * 240)`. See M3.

---

## Session 2026-08-13 — I, J and the evidence debt closed; the decoder hardened

Tree green at **207 tests** (was 182). Mod builds universal (`x86_64 arm64`).
Two agents were stopped mid-task at the end of the session; what they had
already landed is complete and is described below, and what they had not
reached is in the queue.

### L1. The five mislabelled evidence claims are repaired

All five figures in the independent-validation section above were **re-derived
from scratch and reproduced exactly** — the 20.7%/17.2% bias split, the flat
float32 plateau at ticks 462–464, the 55.8th-percentile placement of
`3.4332e-05`, the bit-exact 391-step reproduction of `507.615234375`. Nothing
in the previous session's measurements failed to hold up.

**One correction to this file's own claim.** The text above says randomised
records leave the vacuous test passing. That is true of the *identity assertion
in isolation* (max violation `0.000e+00`) and false of the *enclosing test*,
whose `worst == approx(...)` check fails at `worst ≈ 500` with garbage records.
The test was never fully blind; one assertion in it was.

Repairs landed in `trajectory.py`, `validate_projection.py`,
`test_projection_groundtruth.py`, `test_trajectory.py`:

- the circular residual identity is relabelled tier (i), and the direct
  `out.y == line` assertion it was standing in for is now made explicitly
  (exact at `0.0` over all 480 ticks). Measured falsification power: a `1e-14`
  perturbation of `ease` fails the new line while the old `abs=1e-12` identity
  still passes;
- `3.4332e-05` is restated as "same noise floor, **zero** free parameters
  instead of one", with the arrival sweep showing the figure is identical for
  every arrival in `[462.0, 464.0]` — it cannot discriminate the one tick the
  two models disagree about. `validate_projection.py` now prints `n_effective`
  when the sweep degenerates to a single distinct value;
- the float32 bias direction is reworded from a universal to "predominantly",
  carrying 20.7% and the `−0.000343` counterexample at tick 232;
- `test_the_documented_float32_drift_bound_is_the_measured_one` no longer claims
  tier (ii) over data it simulates;
- the tick-391-vs-392 framing is replaced by what the evidence supports.

**New finding, worth more than the relabelling:** `CORRECTED_RESIDUAL_BOUND_UNITS`
is a threshold fitted to its own maximum — worst residual `5.6457e-04` against a
bound of `5.646e-04`, a **0.005% margin**. A bound that tight can only ever
pass. The new per-tick tier-(iii) assertion uses a `1e-3` noise floor instead.

Also swept the same four files for claims of the same shape and repaired five
more, including one that was in this file but *not* in the code: `trajectory.py`
presented the player-x values (2026-08-12 run) and the activation tick
(2026-08-11 run) as a single measurement. The run-splicing caveat now lives in
the module, where someone reading the code will meet it.

**Checked and found honest, no change:** `test_aggregate_constants_derive_from_
the_full_record` really is tier (iii) — the fixture's `RECORDS` is byte-identical
to the 480 `MOVE` lines in `backups/reference-logs/Geode 2026-08-11 18.41.23.log`,
and `max |dy_log − dy_from_y| = 1.000e-09`, exactly the quantum it claims.

### L2. J is RESOLVED: the player was being emitted as a solid block

**Object 1816 is `GJBaseGameLayer::m_player1CollisionBlock` /
`m_player2CollisionBlock`** — GD's own per-player collision proxy, created by
`createPlayerCollisionBlock()`. Evidence in
`backups/reference-logs/phantom-synth-behind400.log`:

```
PH-CB tick=1320 p1 ptr=0xac89c1000 uid=25 oid=1816 otype=39
      pos=(1864.222778320,105.0) rect=30x30 inObjects=-1
PH    tick=1320 px=1866.172729492 prevPx=1864.222778320
      hits=5 ptrEqP1=5 atCol=14x1,15x1,16x1,17x1,18x1
```

The block's `m_positionX` at tick *t* equals the player's `getPositionX()` at
*t−1* to all nine decimals. `inObjects=-1` — it is reachable through the section
grid but **not** through `m_objects`, which is precisely why the census never
saw it and why this looked like a phantom.

- **`objectType=39` is `GameObjectType::CollisionObject`**, read off the real
  bindings (`mod/build/_deps/bindings-src/bindings/include/Geode/Enums.hpp`),
  not a community table. It is *not* `Solid` (which is `0`). The `kind=1
  (SOLID)` on the wire was **our own** `collapseKind()` mapping
  `CollisionObject -> SOLID`.
- **Answer to J's dangerous branch: yes.** Every consumer of the object window
  was being shown a 30×30 solid block standing on the player, one tick stale, on
  every frame. That poisons Objective A conditioning and any trajectory
  validation against real geometry.
- **Why five.** It registers once per column it has *ever* entered and those
  registrations are never removed. Measured 5 at `GDRL_ENV_WIN_BEHIND=400` and
  **10 at 900** (`phantom-synth-behind900.log`) — a cleaner explanation than the
  no-dedupe hypothesis, and one that predicts the window-size dependence.
- **Repair: filtered by POINTER, not by object id.** `1816` is the editor's
  Collision Block, a real authorable object a level may legitimately contain —
  and *that* one lives in `m_objects` and should be reported. Filtering by id
  would have silently deleted real geometry. In every sample of both runs
  `hits == ptrEqP1 + ptrEqP2`, so no third object is affected.

Logs preserved: `phantom-synth-behind400.log`, `phantom-synth-behind900.log`,
`phantom-stereomadness.log`, `phantom-synth-jump200.log`,
`phantom-synth-fixedwindow-*.log`.

### L3. `test_env.py` was graded by mutation, and it was catching almost nothing

Not "the tests pass" — a mutation table. **7 of 25 mutants killed before, 25 of
25 after.** *(Corrected 2026-08-14: the "25 of 25" did not survive the L5 window
change — `A7-upper-fencepost` regressed to SURVIVED on the very next edit. See
N2. A mutation score is only true for the tree it was measured on.)* Survivors of the old suite included dropping the
`OBJECTS_UNAVAILABLE` refusal entirely, the refusal returning all-*True*, and
`column_span` substituting `100.0` instead of `None`.

Independently reproduced in the main thread: mutate `env.py` to substitute the
measured constant instead of refusing, and **the old 39 tests pass clean while
the new suite kills it with 11 failures**. That mutation is the original
section-factor bug wearing a different hat, and the file meant to guard against
it did not.

**Root cause of the survivors:** every test in the file used one player
placement, `player_x=500`, where `coverageStartCol` is **0** — so a sign flip on
`start_col`, dropping it entirely, and a `>= 0` fencepost were all no-ops. One
fixture constant was hiding four defects. A nonzero-start-col parametrisation
(`player_x=12345` → `coverageStartCol=119`) is what kills them.

**And the fixture was manufacturing vacuous tests.** The natural refusal test —
publish a bad factor, assert the mask is empty — *passes against a decoder that
substitutes*, because `publish()`'s refusal path also left a zero-area window,
so the mask was empty regardless of what the decoder did. Anyone writing a
refusal test here must restore the window first; `publish()` now takes
`layout_x_factor` so that is one call rather than hand-patching a header.

### L4. `known_mask()` can no longer be mistaken for knowledge

It returned a bit-identical all-False grid for three different situations: no
`x -> column` mapping exists; the mod did not look this frame; the mod looked
and knows nothing. A caller holding only the mask could not tell them apart, and
nothing forced it to try — so "refused" would silently become "known-empty",
the same bug class as the inverted section factor.

Changed **now** because `known_mask()`, `column_span()` and `unavailable_tables()`
had **zero call sites outside the tests**. Nothing consumed them yet, and
Objective E's uncertainty policy is specified to be built directly on this mask.

`known_mask()` returns a `KnownMask` whose `.grid()` raises `MaskRefused` when
refused. Every laundering route is closed: `__array__` raises, so
`np.asarray`/`np.stack`/`torch.as_tensor` cannot convert it; `__bool__` raises
`TypeError` (a dataclass is truthy by default, which would have been its own
silent lie); there is no `.any()` to fall through to, so stale calling code
fails on frame 1 rather than returning a plausible empty grid forever. There is
deliberately **no** `mask_or_empty()` — collapsing a refusal has to appear in
the caller's source.

The asymmetry the design turns on: *"looked and knew nothing"* keeps
`refusal is None` and returns a real all-False array. It is an observation, not
a refusal. Both directions are mutation-pinned; the fingerprint test separates
**four** states.

### L5. The loopback fixture was not the mod at the left window edge

`publish()` claimed "the window arithmetic follows `scanObjects()` rather than
being invented here." At the left edge it did not. The mod makes the *window*
primary (`minX = px - g_winBehind`, `telemetry.cpp:688`) and derives the column
(`:664`); the fixture made the *column* primary and snapped the window left to
the boundary. Measured, and verified independently in the main thread:

| `player_x` | fixture `windowMinX` | mod `windowMinX` | gap |
|---|---|---|---|
| 500 | 0.0 | 100.0 | **100.0** |
| 550 | 100.0 | 150.0 | 50.0 |
| 1000 | 500.0 | 600.0 | 100.0 |
| 12345 | 11900.0 | 11945.0 | 45.0 |

The default `player_x=500` — the frame nearly every mask test stood on — is a
worst case at a full section. The mechanism is the *same* sub-0.01 `sxf` that
caused the original inversion: `100 * 0.009999999776482582 = 0.9999999776`,
which floors to 0, not 1. Every mask test reads `windowMinX` out of the header,
so none of them failed; they were certifying a left-edge geometry the game never
produces. The fixture now mirrors the mod, with `telemetry.cpp` line citations.
*(Corrected 2026-08-14, N1: this explanation is incomplete and too kind. At the
default `player_x=500` the two windows produce **byte-identical masks** — no
cell centre lies in the disputed band — so the tests could not have observed the
error even in principle. Reverting the fix fails zero of the 64 tests.)*

**Two comments were the defect, not the code:**

- The claim that the back-off means window and mask "cannot disagree by a
  rounding step at the boundary" is false — they disagree at the left edge on
  **every** nonzero start column, always by exactly −1 (verified in the main
  thread at columns 1, 5, 119, 267). The true guarantee is stronger and now
  stated structurally: `state` is looked up from the per-cell section index, not
  from the window, so knowledge is the *intersection* of the two and widening
  either alone can only remove cells, never add them. A too-wide window cannot
  manufacture knowledge.
- **The right-edge `nextafter` back-off is inert.** Across 3000 start columns the
  loop takes 4631 steps and the `f32` store erases the result in **every** case,
  bit-identically — it corrects a double by far less than the subsequent
  truncation to `f32`. Dead code under a confident comment. Measured on
  `publish()`; `telemetry.cpp:677-681` is structurally identical (double
  `colEdge`, `(float)maxX` store) so the same erasure is very likely there, but
  **UNVERIFIED** — nobody has measured the mod's copy.

### L6. The `levelLength` cross-check is a diagnostic, not a gate

`floor(levelLength * sxf) + 1 == sectionColumns` is available at decode time and
was unused, and `section_factor()` accepts any finite positive value however
absurd (`1e-30` passes and collapses every world x onto column 0). It is now
exposed as a diagnostic returning `None` when either field is unpopulated. It
deliberately does **not** cause `known_mask()` or `section_factor()` to refuse,
because:

- the identity is measured on exactly **two** levels, and `m_sections.size()`
  may be sized from the rightmost object or a reserve rather than
  `m_levelLength` — those coincide on both levels measured;
- a false reject would blank the uncertainty channel for an *entire level* and
  would present as "refused everywhere", indistinguishable from a genuinely
  corrupt factor — the exact confusion L4 exists to remove;
- the acceptance band is `1/N` wide: 0.37% on Stereo Madness but **1.6% on the
  64-column synth level**, i.e. weakest precisely where we test most.

It does reject `1e-30`, `0.005`, `0.0101`, `100.0`, and the divisor reading.

> **Correction (2026-08-14).** This section as first written said the check
> "would pass `0.0101` on synth" one line above saying it rejects `0.0101`. Both
> cannot be true and the second is the correct one: `floor(6340 * 0.0101) + 1 =
> 65 ≠ 64`, so `0.0101` is rejected on synth *and* on Stereo Madness. The value
> that actually demonstrates the band-width weakness is **`0.01009`** — accepted
> on synth (64 = 64), rejected on Stereo Madness (270 ≠ 268). Verified in the
> main thread. That value is now a harness param
> (`inside-the-synth-band-only`), so the real weakness is pinned by a test
> rather than asserted by a wrong example. The error was mine: I transcribed two
> figures from an agent report into one section without checking they were
> consistent with each other.


---

## Session 2026-08-14 — K closed, one dead loop deleted, one false claim caught

Mod-side only; `mod/src/{main.cpp,telemetry.cpp}`. Build green and universal.
Verified inert with **no** `GDRL_*` switches set: mod loads, no `COND`/`MODE`/`EXP`
output, no errors. No new switches added.

### M1. The right-edge `nextafter` back-off is dead code. Deleted.

Not "probably dead by analogy with the Python copy" — measured on the mod's own
expression over the **entire reachable input domain**. `px` comes from
`getPositionX()`, a float32, so sweeping every float32 in range is not a sample.

```
sweep1  float px in [-2048,131072):  n=2365587456  loopRan=2365587456  wireDiffs=0
        (winAhead=1400: colEdge never wins the min(); winAhead=20000: wins all 2.37e9)
sweep2  (sxf x col1), 20000 sxf values x 65536 cols:  n=1310720000  wireDiffs=0
```

2.37e9 float32 `px` values, the loop ran on every one, **zero** differences in the
stored 4 bytes. Both regimes of the `min()` forced, including a 20000-unit window
where `colEdge` wins every time. The arithmetic: at `col1=63` the loop moves
`colEdge` by `9.095e-13` against an f32 ULP of `4.8828e-4` — a correction of
**1.9e-9 of one ULP**. It operates on a `double` and is then truncated to `float`,
which is coarser by nine orders of magnitude. It could never have survived.

Tier: exhaustive static evaluation of a transcription of the mod's own lines,
compiled by the same clang — a claim about *our* code, matched by its instrument.
**Not** tier (iv): `windowMaxX` was never observed on the wire (that needs
`GDRL_ENV=1` plus a Python peer). The two facts the sweep rests on *are* tier (iv):
`px` is float32, and `sectionXFactor = 0.01` (re-observed today).

**The comment it stood under was also false**, and that was the more valuable half:
it claimed the back-off meant window and mask "cannot disagree by a rounding step
at the boundary." They disagree at the **left** edge on every nonzero start column,
always by exactly −1 — `windowMinX = px - g_winBehind` vs `col0 = floor(minX*sxf)`,
so the window starts partway into column `col0`. The C++ comment now states the
real guarantee structurally, as `env.py` already did (L5): knowledge is the
*intersection* of window and per-cell coverage, so widening either alone can only
remove cells, never add them.

### M2. `MODE` never fired because of **our guard**, not GD's plumbing

`PlayerObject::switchedToMode` **clears the outgoing vehicle**; the flag naming the
incoming one is set by the caller *after* it returns. So a cube→ship entry has
`before == after == cube`, and `if (before == after) return;` swallowed every
transition. The hook was fine all along.

Read off the arm64 slice (GD 2.2081 runs arm64; the bindings' `m1` addresses are
the live ones), `GJBaseGameLayer::switchToFlyMode`:

```
0x1000fcee8  bl 0x100388338   <- PlayerObject::switchedToMode, FIRST
0x1000fcf4c  cmp w22, #0x5 / b.ne ...
0x1000fcf94  bl 0x10038bf50   <- toggleBirdMode(true, ...)   type 19
0x1000fcfc4  bl 0x10038b4d8   <- toggleFlyMode(true, ...)    type 5
```

and inside `switchedToMode` the toggles are only ever called with `enable = 0`
(`mov w1,#0x0` immediately before each `bl`).

Falsified on the way, all three by measurement rather than argument: *the hook is
not installed on this arch* (Geode logs it enabled); *`switchedToMode` is inlined
so the address is not a call site* (**21 `bl`, 0 `b`**); *`switchedToMode` is not
the ship-entry mechanism* (it is the first call in `switchToFlyMode`).

Live, tier (iv), and reproduced bit-identically across two runs:

```
[gdrl] MODE tick=3092 x=4468.861 from=cube cleared=cube type=5 p1
[gdrl] COND tick=3092 x=4468.861 ship  grav=dn size=1.00 spd=0.90 gmul=0.96 ...
```

**Track 1.3's acceptance criterion is now fully met**, with the caveat that a
`MODE` line names the vehicle *departed* and the vehicle *entered* is named by the
`COND` line on the same tick. Recorded in README under "Vehicle mode transitions".

### M3. `step=` is now `tick=`, and TODO's own claim about it was false

`m_currentStep` is out of every log line; `tick=` is `lround(m_attemptTime * 240)`
via a `gdrlTick()` helper (`-1` when there is no PlayLayer).

**The claim in K — "`COND step=` is 0 in every log ever" — was false when
written.** In `phantom-synth-jump200.log` the field reads `step=0` once (at
`x=0.000`) and **`step=416` on all six later lines**; verified independently in the
main thread (`1 COND step=0`, `6 COND step=416`). The *conclusion* survived —
`m_currentStep` is not the physics-tick counter and was useless as logged — but the
supporting observation did not, and it entered this file unchecked. Why it reads
416 in that run was not chased; that run's only `INJECT` line reads `pushed=0`, so
"the run that injected buttons" is **not** a supported explanation.

Cross-validated live rather than merely "it moves now" — implied `dx/dtick` across
the synth level's five speed segments against README's constants: +0.17%, +0.30%,
−0.66%, +0.03%, +0.01%. This validates the **clock**, not the speed constants; the
residuals are dominated by `COND` being sampled per render frame, and the two long
segments landing within 0.03% is what a real clock looks like.

### Still open from this session

- **`GameObjectType 6` is unidentified** — four `MODE tick=0 x=0.000 type=6` lines
  fire per level load (p1 and p2, twice). Logged raw by design. The id→vehicle map
  is confirmed only for `5 → ship`; `19 → ufo` and a branch at `0x29` are
  disassembly-only.
- **Whether `MODE` fires on a mode *exit*** — never observed; the synth level ends
  in ship. The disassembly predicts it would, and would have even under the old
  guard.
- **No tier-(iv) observation of `windowMaxX`.** Needs `GDRL_ENV=1` plus a Python
  peer.
- **Why `m_currentStep` read 416** in that one run rather than 0.


---

## Session 2026-08-14 (cont.) — the L5 fix had never been graded, and one test had regressed

Tree at **235 tests**. Mutation harness landed in the repo and re-run end to end:
**46 of 46 killable mutants killed**, 2 deliberate survivors.

### N1. The entire L5 window fix was ungraded — and at the default placement, ungradeable

Method: revert only `h["windowMinX"] = min_x` back to `col0 * sec_w` in a scratch
copy, run the tree's own suite. **Zero of the 64 pre-existing tests fail.**

L5 explained this as "every mask test reads `windowMinX` out of the header, so
none of them failed." True, but the stronger and worse fact is this: at the
default `player_x=500`, with the raster at `player_col=12, cell=30` starting at
x=140, **no cell centre ever lay in the disputed `[0, 100)` band** — so the mask
bytes are *identical* under both windows. The tests were not merely failing to
assert the left edge; they could not have observed it. Only `player_x=12345` with
`player_col=16` distinguishes the two, and then by exactly 2 raster columns.

Fixed by `test_the_fixture_publishes_the_window_the_mod_would` (16 params) built
on `_mod_scan_window()` — `scanObjects()` restated statement for statement,
window primary, column derived, each line carrying its `telemetry.cpp` citation
against `git show 8dd5ceb`. It asserts `coverageStartCol`, `sectionColumns`, all
four window edges after the float32 store, **and the full 64-entry coverage
array**. Params include the `col0 < 0` clamp, past the end of `m_sections`, the
mod's real production config, and float32-neighbour pairs straddling the
`floor(minX*sxf)` tick-over at k = 1, 5, 119, 267 (derived in-test from the
measured sxf, not tabulated). Reverting `publish()`'s left edge now fails 15 of
16. **Tier (i)**, and the docstring says so: it grades our transcription of the
mod, not the mod — if `telemetry.cpp` changes, both halves are wrong together.

### N2. **L3's "25 of 25" was no longer true.** A mutation table is only true for the tree it was measured on

`A7-upper-fencepost` (`section < COLS` → `<= COLS`), killed in L3, had regressed
to **SURVIVED** — and L5 caused it. `test_columns_past_the_end_of_the_coverage_
array_are_unknown` says in its docstring "the whole array is SCANNED". Once
`publish()` began binding `col1` to the requested window, `player_x=450` with the
mod's default 1400-ahead SCANs only **19 of 64** columns, so `coverage[63]` is
UNKNOWN, the clipped out-of-range read returns UNKNOWN anyway, and the mutant
became invisible. **The test had been passing for the wrong reason.** Fixed with
`win_ahead=8000.0` plus a guard asserting the array really is fully SCANNED.

This is the argument for keeping the harness in the repo rather than in a
scratchpad: a mutation score decays silently as the code moves under it, exactly
like a test that stops testing what its name says.

### N3. Eight more survivors, all closed

First run on the tree as received: **37 of 46 killable killed**. Beyond A7:

| Survivor | Why it lived |
|---|---|
| `A15-window-y-becomes-exclusive` | every test placed row centres strictly inside the y window |
| `C7`, `C8` — the L6 diagnostic | **it had no test at all** |
| `D5`, `D6`, `D7` — right-edge / `col1` | every placement had `coverage_cols` small enough that the clamp won, so the requested-window branch was never taken |
| `D13-absent-columns-published-as-scanned` | the fixture's own ABSENT branch was never reached — the one ABSENT test writes the bytes by hand |
| `D15-refusal-frame-keeps-a-real-window` | nothing asserted the refusal frame's zero-area window |

Four new tests plus the agreement test's whole-coverage-array comparison close
all of them. **46 of 46 after.**

Two deliberate survivors, labelled with their reason in the harness:
`D8-delete-the-right-edge-backoff` (measured inert — a test killing it would pin
a difference that never reaches the f32 wire) and `Z0-control-no-behavioural-
change` (instrument check: if Z0 ever reports KILLED, no other row can be read).

### N4. The harness

`trainer/mutate.py`, no scratchpad dependency — resolves `trainer/` from
`__file__`, copies to a fresh temp dir per mutant, cleans up.

```
python3 trainer/mutate.py                    # 48 mutants against test_env.py
python3 trainer/mutate.py --list | --only left-edge | --killers | --keep
python3 trainer/mutate.py --tests test_env.py test_trajectory.py
```

Exit 0 only if every mutant matched its expectation. A mutant whose anchor no
longer matches is **PATCH-FAILED and counted as a failure**, never silently
skipped — which is what makes N2 detectable at all.

### N5. Known gap left open deliberately

`publish()` still transcribes the right-edge back-off loop **that the mod no
longer has** (M1 deleted it). The agent declined to delete our copy on the
strength of another agent's uncommitted work — correct call — and left a
`DELETE THIS BLOCK once the mod's deletion lands` marker in `env.py`. The
deletion has now landed in the same commit as this note, so **the marker is
actionable: delete the block.** Its mutant `D8` should then be removed too.

### Not verified

- **Nothing in N touches the game.** The agreement test is tier (i) by
  construction; it cannot tell you `publish()` matches `scanObjects()` today,
  only that our two Python restatements agree. The C was read, not run.
- The 1.2e9-value inertness sweep behind `D8` is M1's measurement, not
  reproduced here.
- Assumes numpy's `float32` cast and C's `(float)` cast round identically
  (round-to-nearest-even). **Unmeasured.** If false, the agreement test's four
  window-edge comparisons are where it would surface.
- Tier-(iii) content is only in the *inputs* (26724→268, 6340→64 from the
  2026-08-12 dump, via `MEASURED_*`); the arithmetic exercising them is ours.


### Queue for next session, in order

> **RESUME HERE.** Items 1 and 2 were dispatched on 2026-08-14 and **stopped
> before either produced anything** — the viewport agent was killed at "now I'll
> write the probe", so `mod/` is untouched and no measurement exists. Start both
> from scratch; nothing is half-done and nothing needs unpicking. Item 3 landed
> and is closed. Tree is green at **235 tests**, `trainer/mutate.py` at
> **46/46 killable (47 run)**, mod builds universal.


**Re-scoped 2026-08-14 by the Benchmark A decision.** Items 1 and 2 did not exist
before it and now outrank everything else: under A the sensor definition *is* the
benchmark, and ours has never been justified.

1. **Measure what is actually on screen** — the camera viewport in world units,
   x and y, at 1x and at the other speed buckets. `GDRL_ENV_WIN_AHEAD` is
   currently **1400** units, chosen for convenience before Benchmark A existed.
   At 1x the player covers ~311.6 units/second, so 1400 ahead is ~4.5 seconds of
   lookahead — **plausibly far more than the screen shows**, which would mean the
   agent is already reading geometry the player cannot see. If so, every value
   below the horizon needs re-deriving and any prior run is not a Benchmark A
   run. Measure it; do not reason about it.

2. **Rule on every field the agent receives.** Go through `GdrlObservation` and
   `env.py`'s decode and classify each as legitimate under A, forbidden, or
   needs-measurement — with a reason per field, not a blanket verdict. Known
   hard cases: `levelLength` (a human sees the progress bar → probably fair),
   whole-level `objectCount` (a human does not → probably not), `sectionColumns`,
   and anything derived from `m_sections` outside the window. The output is a
   written contract, and it should say what a *human player* can perceive, since
   that is the standard A appeals to.

3. ~~**Delete `publish()`'s copy of the right-edge back-off**~~ — **DONE**
   2026-08-14. Block removed from `publish()`, mutant `D8` retired with a note
   saying why, and the `telemetry.cpp` citations refreshed for the ~+21-line
   shift `efc32e9` caused (`:688` → `:710`, verified). Harness now reports
   **46/46 killable, 47 run**.

4. **Check `conditioning.py` against what the mod actually emits** (Track 1.3,
   the open half). The mod side is evidenced end to end; the Python side has
   never been compared against it. Under A this matters more, not less — the
   regime is part of what the agent perceives.

5. **Identify `GameObjectType 6`** (M3). Four `MODE type=6` lines per level load,
   unidentified. The id→vehicle map is confirmed only for `5 -> ship`.

6. **Measure the four speed buckets properly** (Track 1.2). Beware float32-binade
   quantisation; the H table is ±1 ulp sampled at different binades, not
   constants. M3 gives a real tick clock now, which this always needed. Note
   item 1 may make this urgent: if the horizon is defined in *seconds of
   lookahead* rather than units, it changes per bucket.

7. Then Track 0.2 (predictor spec + test-tier audit), and only then Track 4.

**A standing item, not a task:** `python3 trainer/mutate.py` should be re-run
after any change to `env.py`, and its result quoted rather than "tests pass".
N2 is the reason — a mutant killed in one session had regressed to SURVIVED by
the next edit, silently, while the suite stayed green.

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

### 0. THE BENCHMARK — **DECIDED 2026-08-14: Benchmark A, true sightreading**

**Decided by Rex.** The project targets **true sightreading**. The agent receives
only what it is legitimately allowed to observe. It may **not** inspect geometry
beyond its sensor horizon, may **not** clone the engine to try alternate futures,
and may **not** read trigger state that has not yet occurred. Attempt 1 really is
attempt 1.

The rejected alternative, recorded so it is not re-litigated: **Benchmark B**,
simulator-assisted minimum-attempt play — inspect ahead, read engine state, clone
the game, roll out invisible futures, discard the ones that die. Legitimate as an
engineering problem, but its answer is known in advance: GD is deterministic with
a 1-bit action per tick, so with cloning it is a search problem that search wins,
and the only real unknown is throughput.

#### What this decides, immediately

- **The MCTS sketch in Track 2 is out of scope as *the agent*.** It assumed
  cloning and not counting hidden branches as attempts. It survives only as an
  **oracle**: B's solved trajectories are the ground truth needed to *score* A
  and to generate training targets. B is the teacher and the upper bound, A is
  the claim. Keep it as a parameter, never a fork.
- **Objectives D (in-context failure memory) and E (uncertainty policy) are now
  load-bearing**, not nice-to-haves. Under B you re-search; under A, memory of
  your own deaths and calibrated knowledge of what you cannot see *are* the
  agent.
- **Objective B (forward projection) survives intact and is legitimate.**
  Predicting where a *visible* moving block will be is perception plus
  extrapolation, which is what a human does. The line is not "no simulation" —
  it is (1) the sensor horizon and (2) whether a failed future costs an attempt.
  The three sessions spent validating `trajectory.py` are not wasted.

#### The metric is attempts-to-completion, not attempt-1 success

Stated so nobody later mistakes the goal for something superhuman: **humans do
not sightread Geometry Dash.** A human clearing Stereo Madness takes roughly
5–30 attempts; hard levels take tens of thousands. "Attempt 1 really is attempt
1" defines the *information* rule, not the success criterion. The benchmark
metric is the **learning curve — attempts-to-completion** — which is exactly
where in-context failure memory earns its place. First-attempt success is a
stretch goal, not the definition.

#### The enforcement rule, and it is not negotiable

**The horizon is enforced in the mod, in `scanObjects` — never in Python, and
never by asking the policy to ignore what it was handed.** An agent trusted to
discard information it received is not a benchmark; it is an honour system. This
promotes `GDRL_ENV_WIN_BEHIND / _AHEAD / _VERT` from performance knobs to **the
sensor definition**, and they must be justified against what is actually on
screen rather than chosen for convenience. Current values (400 / 1400 / 600) were
picked before this decision existed and are **UNVERIFIED as a sensor** — see the
queue.

Two consequences that reach backwards:

- **L2 was benchmark-invalidating, not a nuisance.** For the whole life of the
  ENV transport the observation contained a phantom solid block on the player.
  Under A that is not noise — **the sensor was lying**, and any run on it would
  have been invalid rather than merely degraded.
- **L4's refusal-vs-ignorance distinction is not hygiene.** It is the primitive
  that lets the agent know what it does not know, which under A is half of
  Objective E.

#### Still to settle under A (these are now real questions, not philosophy)

1. **What is actually on screen?** The horizon must be measured, not assumed.
2. **Is a global level fact observable?** A human sees GD's progress bar, so
   level length is arguably fair; `objectCount` over the whole level is
   arguably not. Each field needs a ruling.
3. **Does the agent get audio?** GD is heavily rhythm-cued and human players
   lean on it. Currently we emit none. Excluding it is a defensible choice but
   should be a *stated* one.
4. **Attempt-boundary memory.** What persists between attempts is the whole
   substance of Objective D, and it needs a spec before it needs code.

### 0b. The tier-(iii) evidence is not in the repo (found 2026-08-13)

`.gitignore:18` ignores `backups/`, and `git ls-files backups/reference-logs/`
returns **nothing**. So every log this project cites as game-grounded evidence —
including `Geode 2026-08-11 18.41.23.log`, the 480 `MOVE` records that the only
tier-(iii) tests in the repo are built on, and this session's five `phantom-*.log`
files — exists on exactly one machine and is one `rm` from gone.

This is the repo's own rule turned on itself: *a claim resting on a log that no
longer exists is not evidence*. The tests would still pass, because the fixtures
carry transcribed copies — which is precisely the problem, since nobody could
then re-derive the transcription.

Decide: commit the logs (~1.6 MB for this session's set, and they compress well),
commit a hashed manifest plus a documented way to regenerate them, or accept the
loss explicitly. Not decided unilaterally because it changes what the repo is
for. Note the fixtures' transcriptions **were** verified byte-identical against
the log while it was in hand (L1), so the current state is recoverable-in-
principle today and not tomorrow.

### The four that were already here

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
