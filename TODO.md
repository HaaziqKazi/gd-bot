# gd-rl — open work

Single place for what is left. Companion to `README.md`, which records what has
been **established**; this file records what has **not**.

Status as of commit `fbc13ea` + the 2026-08-13 (L), 2026-08-14 (M/N),
2026-08-15 (O/P) and **2026-08-16** sessions below.

> ## RESUME HERE — 2026-08-16 wrap. Read this box before anything else.
>
> **Rex's goal is unchanged: an agent that plays Stereo Madness all the way
> through.** This session ran the game for the first time since both deliverables
> landed. Read "Session 2026-08-16" below in full — it is the only session whose
> findings change the plan rather than extend it.
>
> **Two findings reorder everything:**
>
> 1. **The throughput knob does nothing.** `advanceSteps` is deterministic at
>    every stride and buys **0%** wall clock — ~14 s per 3048-tick attempt at
>    strides 1, 8, 32 and 64 alike, ~822 rendered frames every time. The game is
>    frame-limited at ~1× real time. The `~1.8 s/attempt` figure recorded at the
>    last wrap is wrong by **7.8×**; it measured Python, which was never the
>    constraint. **`GDRL_ENV_DELTA_TICKS` — the knob that actually rewrites dt —
>    was never tested.** It is now the highest-value measurement in the project:
>    the arithmetic at the end of the session section puts a full clear at
>    **12–20 h** today, **~2 h** if that knob proves deterministic at N=8.
>
> 2. **Stereo Madness ends in a ship section** (portals at x=7995 ship, 12555
>    cube, 22935 ship, 24045 ship, none back to cube; last object x=26384).
>    **A cube-only action space cannot clear this level.** Best-ever reach x=3959
>    is 49.5% of the way to the *first* ship portal. Ship is a scoping decision
>    Rex has not yet made — but the sensor is probably already adequate (see Q5a),
>    so this is likely policy work, not sensor work.
>
> **Do this first, in one held GD slot (~10 min, invocations are written out
> verbatim under "Operational notes"):**
> 1. **Re-run Q2** against the current binary — it passed 9/9 but against an
>    artifact that no longer exists, and #22 has since changed `scanObjects`.
>    Validate #22 in the same pass: `uniqueID=2410` must be gone, the 19 `id 8`
>    spikes with x ≤ 4320 must all survive, `id 8` total 167 → **166**.
> 2. **Sweep `GDRL_ENV_DELTA_TICKS`**, N ∈ {1,2,4,8,16,32}. **One launch per N** —
>    it is a load-time `const` with no wire field, so it cannot be interleaved,
>    and every launch must hit the same binary or the sweep is void. Report the
>    largest bit-identical N, the first divergent N, wall clock and `frames=`.
> 3. **Compose check + EXP determinism.** The two dt rewriters do **not**
>    compose (`telemetry.cpp:1363`); this is pick-one. EXP is 2× faster and its
>    determinism has never been established.
>
> **Then** #20 (window pinning) before any acceptance run: sensor width drifts
> **569 → 493** between launches of the same binary, and under Benchmark A that
> means two runs are not the same benchmark.
>
> **Do not trust any run without checking provenance** — see #23. A run this
> session had some switches arrive and others not, wandered onto *Bloodbath*, and
> reported `input[clean]` throughout.

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
| Search algorithm | **`trainer/sightread.py` built 2026-08-15** — best-first over hold *intervals*, real backtracking, A-legality enforced by construction. Solves a 7-hazard puppet level in 12 attempts. **Never run against GD**; acceptance criterion not met. Its interval premise is now **confirmed live** (hold auto-repeats, landing +1 tick, 6/6) — but that is a *cube* law, and Stereo Madness ends in **ship**, which this action space cannot fly |
| Env-loop throughput | **Measured against the game 2026-08-16, and the projection was wrong by 7.8×.** ~14 s per 3048-tick attempt at strides 1/8/32/64 alike, ~822 rendered frames every time — the stride cut round-trips 63× and bought **0%**. The game is frame-limited at ~1× real time; the loopback ~0.4 ms figure measured Python, which was never the constraint. EXP path is **~6.5 s (~2×)**. `GDRL_ENV_DELTA_TICKS`, the only knob that rewrites dt, is **still untested** |
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


## Session 2026-08-15 (second) — the sensor became camera-derived, and a search driver exists

Two implementer agents, run in parallel under the orchestrator split. Neither ran
Geometry Dash. Everything below is code-and-fixture evidence — **tier (i)/(ii)** —
and the honest summary is that the session converted unknowns into *testable*
hypotheses rather than into measurements.

Verified in the main thread rather than taken from the agents' reports: `geode
build` green, `lipo -archs` → `x86_64 arm64`, `python3 -m pytest trainer/ -q` →
**269 passed**, `python3 trainer/mutate.py` → **49 of 49 killable killed, 50 run**.

### O. The camera-derived sensor (item 1's ruling, implemented)

**First, the correction, because it is worth more than the feature.**
`gdrl::cameraWorldRect()` was **declared and never defined**. The previous
session's wrap-up box, and the task brief written from it, both asserted it was
"already implemented in `viewport.cpp`". It was not; the tree built because
nothing called it. Likewise `readWorldTransformNeutrally()` — the state-neutral
transform reader `viewport.hpp` devotes a paragraph to — was **dead code**;
`sampleViewport()` called `nodeToWorldTransform()` directly, so the 2026-08-15
viewport numbers were taken through the non-neutral path and `GDRL_VERIFY_XFORM`
had never verified the path it was written for.

*The measurements survive — it was a probe, and perturbation there is harmless.
The safety argument does not: it was documented where it was not applied.* The
generalisable lesson, and it is this repo's recurring one wearing yet another
costume: **a header's prose describes intent, and intent is not implementation.**
`grep` for the definition, not the declaration.

What now exists:

- **`cameraWorldRect()` implemented** (`viewport.cpp`), taking `GJBaseGameLayer*`
  — no `PlayLayer::get()` on the hot path — and routed through
  `readWorldTransformNeutrally()`, which now has a caller. It never logs and
  never touches the probe's accumulators, so probe and observation path cannot
  perturb each other's numbers.
- **`scanObjects()` uses it every physics step.** The window is the live camera
  rect (~569 × 320 at zoom 1), not `px ± 400/1400/600`.
- **`rotated` refuses; `sizeMismatch` warns.** The asymmetry is deliberate. A
  rotated camera makes the four-corner rect a *bounding box*, which **overstates**
  what is visible — lookahead the player does not have, so the scan refuses.
  `sizeMismatch` is a failed cross-check against `m_cameraWidth/Height`, but the
  rect is still correct-by-construction under zoom, so it is used and warned
  about. `rotated` is OR'd from two independent witnesses (`m_cameraAngle`, and
  the transform's off-diagonal shear); if they disagree, one of them is not
  describing the render transform, which is itself worth reporting.
- **`valid == false` claims nothing.** Extracted into `refuseScan()`, shared with
  the bad-`m_sectionXFactor` path so both refusals emit a byte-identical frame.
  No fallback to the old constants, ever — matching the posture of the deleted
  `: 100.f` default.
- **`GDRL_ENV_WIN_*` are now per-axis, detected by presence rather than value**,
  with the `envInt` fallback changed to `0` so a stale 400 cannot be resurrected
  by a later refactor. Setting one warns, once, that the run is not Benchmark A.
  `_VERT` remains a **half**-extent; old ablation command lines still mean what
  they meant.
- **The coverage clamp stays and now reports when it binds.** ~7 of 64 columns
  are needed at 569 units, so it is slack on Stereo Madness at zoom 1 — but
  `secW` is *read from the game*, and a finer grid or a zoomed-out camera needs
  more. Binding now means the sensor is being silently narrowed below the screen,
  so it warns with columns-wanted vs columns-held. (N2 is why this is not left
  silent.)
- **Python needed no decoder change** — `known_mask()` has only ever read the
  four window bounds off the header, so an asymmetric window already worked. What
  was wrong was the *naming*: `MOD_WIN_*` invited reading 400/1400/600 as the
  mod's window and is now `FIXTURE_WIN_*`, with `MEASURED_CAM_*` added as a
  record of the probe numbers and explicitly not a definition.

**The guarantee, stated precisely.** "`GDRL_*` unset == unmodded run" is
**untouched**: `cameraWorldRect` has exactly one caller, inside the `g_envOn`
gate. "`GDRL_ENV=1` perturbs nothing" is **weakened** — under `GDRL_ENV` the scan
now additionally walks the object layer's parent chain once per physics step.
That claim was already UNVERIFIED (item 4); it is now UNVERIFIED **(7)**, with
the `GDRL_VERIFY_XFORM` run that would settle it named in the file header.

**Not verified, and this list is the point of the section:**

- That the sensor is correct live. **No attempt was run.** The stale default-off
  byte-identity check is now staler, not fresher.
- Every viewport number (569.0 × 320.0, 359.5/209.5, +215/−105, 6146/6146
  samples) is **inherited from the earlier probe log**. The implementing agent
  measured none of them and did not open the log.
- That `nodeToWorldTransform()`'s cache write cannot perturb rendering. Restored
  by construction; never measured; **now on the hot path.**
- The 1e-4° / 1e-6 rotation thresholds are reasoned from the float noise floor,
  not measured, and `m_cameraAngle` has never been observed nonzero — so the
  refusal branch is untested against anything. **If any part of Stereo Madness
  rotates the camera, the agent goes blind there by design.** Nobody has checked
  whether it does. This is the highest-risk unknown in section O.
- Per-step cost of the transform walk. Not measured.
- **UNVERIFIED (8), introduced deliberately:** object membership is a *centre*
  test against the window. At ±600 the slack was irrelevant; at +215/−105 an
  object whose centre is up to half its height outside is dropped while part of
  it is on screen. It **understates**, which is safe under A, but has never been
  counted. Rect-overlap was rejected for now because it would report objects
  outside the advertised window while Python intersects the object list *with*
  that window — changing both descriptions at once is how they drift apart.

### P. `trainer/sightread.py` — the A-legal search driver

**The throughput measurement, which is the most decision-relevant result of the
session.** Loopback only, this machine:

| GIL switch interval | objects | stride 1 | stride 64 |
|---|---|---|---|
| 0.005 (default) | 0 | 155 rt/s | 157 rt/s |
| 0.00005 | 0 | **2,524 rt/s** | 2,531 rt/s |
| 0.00005 | 120 | **1,165 rt/s** | 1,217 rt/s |

**Python is not the bottleneck; the game is.** Decode+respond costs ~0.4 ms
empty, ~0.86 ms with 120 objects, and is flat in the stride.

Two measurement artifacts had to be removed first, and **either one alone gives a
confident wrong answer** — worth remembering next time this is re-measured:

- **The 155/s figure is the GIL, not the protocol.** `1/0.0065 s` ≈ the 5 ms
  switch interval. The loopback writer is a *thread*; against the mod it is
  another process and this does not apply. Re-measuring on the loopback without
  sweeping `setswitchinterval` concludes the transport is 16× slower than it is.
- `SyntheticGame._accumulated_attempt_time` is O(tick) **per publish**, a Python
  loop, and dominates everything at large strides. Fixture cost, not protocol
  cost — the mod gets `m_attemptTime` free.

**The consequence for a full clear.** Against README's game-attached figures
(~500 rt/s; 5.5 s per 3054-tick attempt with telemetry vs 3.4 s without), the
naive per-tick loop costs ~5.5 s/attempt at the current frontier — ~650
attempts/hour, and nearly all of it buys observations of a prefix whose outcome
is already known. The **stride hybrid needs no mod work**: `advanceSteps` is an
observation stride, and the mod fires scheduled inputs on every physics step
regardless (`telemetry.cpp:1064`), so a committed prefix replays in a handful of
round trips and the loop drops to stride 1 only near the frontier. The
per-attempt floor becomes the game's own replay speed,
`prefix_ticks / (GDRL_ENV_DELTA_TICKS × 60fps) + reset`, ≈ **1.8 s for a
3048-tick prefix at `DELTA_TICKS=32`** — ~2000 attempts/hour. **That last figure
is arithmetic over recorded numbers, UNVERIFIED against the game.**

**What is real in the driver:** the A-legal accessor (`Sight` copies the allowed
fields and **drops the record** — there is no `.header` to reach through;
`groups` / `objectCountTotal` / `commands` / `pending` / `speedSegs` raise
`ForbiddenField` carrying the contract's reason, and the objects array's dtype
physically lacks `groups`/`groupCount`). The interval action space
(`Interval`/`Plan`), which **refuses overlapping holds** because two overlapping
HOLDs expand to push,push,release,release and the *first* release ends the chain.
`AttemptLedger`, which has no decrement, is opened before the first action
reaches the wire, and is audited against the game's own attempt field. `Runner`
with the stride hybrid. `Sightreader`, a best-first search whose priority is
`subtree_best_x − patience·attempts_at_node`, which is the term that makes
ancestors resurface instead of the frontier running away.

Against the puppet: a 7-hazard level solved in **12 attempts with 4 nodes having
more than one child** — genuine backtracking, clean ledger audit. Bounds are read
from the observation header only, so it is already correct under section O's
window.

**Corrections to the docs, found by building against them:**

- **`header.attempt` is an attempt *id*, not a count.** Playing ids 7–10 is four
  attempts and a difference of three, and the last is not reset while you are
  still in it. The first audit reported an unexplained off-by-one on every clean
  run — which is precisely how a real discrepancy gets trained out of a reader.
  Count is `last − first + 1`, minus one if the game has begun an attempt nobody
  used. **Anyone counting attempts from this field needs the same correction.**
- **A design defect worth recording, because it is the old greedy stall in a new
  costume.** A node whose last hold is still down when the attempt ends has no
  room to append. The first version happily proposed intervals *after* the death,
  growing plans into the far future forever — every probe scoring identically
  while the priority kept re-picking that node. **It looked like a search and was
  a loop.** The fix: such a node yields nothing and goes exhausted, because *the
  backtrack is the correct response to "there is nothing left to append".*
- README's "~3.9 attempts/sec came from a different path" is right and
  load-bearing: that was a 391-tick attempt with no env loop, and it does not
  carry to a 3048-tick prefix.

**Not verified:**

- **Hold auto-repeat — the whole interval design is a bet on it.** The puppet was
  *written* to auto-repeat, so the test that a cluster no single hop clears is
  crossed by one interval is **circular as evidence about GD**, and says so in
  its docstring. The first live run is the measurement.
- Replay determinism under the stride hybrid; that a large `advanceSteps` is safe
  across a death; that the level-complete screen is distinguishable from a
  stalled publisher (**it currently is not, and the driver says so rather than
  guessing**).
- The candidate *ordering* is a heuristic prior (jump starts ~half a hop before
  the death tick), unmeasured; only one candidate is shaped by the sensor.
  `FALLBACK_AIRTIME_TICKS=60` / `FALLBACK_HORIZON_TICKS=240` are used only until
  the calibration attempt measures them and are labelled UNVERIFIED.
- Solvability of the puppet measures the search against a level chosen to suit
  it.

**Grading caution for whoever runs it:** the driver reports **observed max x**,
sampled from the frames it was given — a *different instrument* from the mod's
per-attempt `maxX` log line that produced `3959.183837891`. At stride 1 they can
differ by one tick of travel (~1.30 units); outside the watch window, by a whole
stride. **Grade against the mod's log line.**

---

## Session 2026-08-16 — the game was finally run, and the cost model was wrong

The first session in which both 2026-08-15 deliverables met Geometry Dash. Five
questions were put to the live game (Q1–Q5). Four came back clean. The fifth
found that **the throughput knob this project has been planning around does
nothing**, and that Stereo Madness cannot be cleared by the action space we
have.

Nothing was committed by the agents; every number below is from the live game or
from a level string the game itself dumped.

### Q1. Holding jump auto-repeats — **CONFIRMED, tier (iv)**

The bet the entire interval action space rests on is real, and the toy level's
circular tests are no longer the only evidence.

Decisive attempt `hold1000@60`: press at tick 60, release scheduled for 1060,
attempt died at 783 — **the release never fired, so the whole arc came from one
press.**

| landing (`isOnGround` 0→1) | — | 163 | 268 | 373 | 478 | 583 | 688 |
|---|---|---|---|---|---|---|---|
| next jump onset (`yVelocity` 0→11.18) | 60 | 164 | 269 | 374 | 479 | 584 | 689 |

**Seven jumps from one press. Onset is landing + 1 tick, 6 of 6.** Period 105
ticks (first arc 104, from `y=105.000` against the `y=107.516` it settles to).

Controls: `hold8@100` → `maxX=507.615234375`, identical to null input;
`hold400@100` → `maxX=523.194458008`, reproduced bit-identically on repeat.

**Still UNVERIFIED:** constant across *speeds* (lvl 1 has no speed portals, so
untestable there) and across *heights* (all six landings on one surface).
**And it is a cube law** — in a ship, holding is continuous thrust, not a
re-triggered jump, so this does not transfer. See Q5.

### Q2. Default-off byte-identity — **PASS, tier (iv), but against a dead binary**

Gameplay half, 9/9 attempts, every one:
`maxX=3959.183837891 deathTick=3048 t=12.700000662 push=12 rel=12
input[clean blocked=0 leaked=0 ui=0 uiTot=0]`, **`VIEWPORT` count 0**, zero gdrl
WARN/ERROR. Baseline was 3/3; got 9/9. Menu half, no `GDRL_*` at all: the entire
mod output was two `ISOLATION` lines and `forced windowed mode`.

**Caveat that must not be lost.** This was measured against
`29f7eef7…` (Aug 15 23:14). That artifact **no longer exists** — the binary was
rebuilt during the session. The tester's own ruling, and the right one: *re-run
rather than argue it forward*, because Q2's whole value is that it is measured
rather than reasoned. **Q2 must be re-run against the post-#22/#23 binary before
it is quoted again.** ~70 seconds.

### Q3. **The knob was wrong, and the cost model collapsed**

Two knobs were being conflated, including in the brief that requested this
measurement:

- **`advanceSteps`** — a *wire field*, observation stride. Cannot touch physics.
- **`GDRL_ENV_DELTA_TICKS`** — an *env var* (`telemetry.cpp:196`, namespace-scope
  `const`, read **once at load**) that rewrites the dt fed to
  `GJBaseGameLayer::update`, packing N physics steps into one rendered frame.

**Only the second can buy throughput, and it was never tested.** What was tested
is `advanceSteps`, and it is deterministic:

| attempt | 3 | 4 | 5 | 6 | 7 | 9 | 10 |
|---|---|---|---|---|---|---|---|
| stride | 1 | 32 | 1 | 32 | 8 | 32 | **64** |
| published obs | 3292 | 103 | 3288 | 103 | 412 | 103 | 52 |
| maxX | all seven `3959.183837891` | | | | | | |

Interleaved in one process so drift cancels; graded against the mod's `ATTEMPT`
line, not a sampled max; `env[timeouts=0 protoErr=0]` throughout.

**Inputs fire on every physics step regardless of stride — confirmed
empirically, not read off the comment.** At stride 32 the published ticks are
1, 33, 65 … and **not one of the twelve jump ticks** (326, 713, 1075, 1163, 1267,
1799, 1935, 2155, 2319, 2483, 2687, 2879) is published — 326 mod 32 = 6 — yet the
trajectory is bit-identical to stride 1.

**THE FINDING THAT MATTERS.** Wall clock per full-length (3048-tick) replay:

| stride | 1 | 32 | 1 | 32 | 8 | 32 | 64 |
|---|---|---|---|---|---|---|---|
| wall clock | 14 s | 13 s | 14 s | 15 s | 14 s | 14 s | 14 s |
| rendered frames | ~822 every time | | | | | | |

**~13–15 s at every stride. The stride cut round-trips 63× (3292 → 52) and
bought 0%.** The game is frame-limited at ~1× real time, not protocol-limited.
3048 ticks / 240 Hz = 12.7 s of game time.

The **~1.8 s per 3048-tick attempt** projection recorded at the 2026-08-15 wrap
is wrong by **7.8×**. It was a loopback measurement of Python's cost, and Python
was never the constraint.

For contrast the older EXP path (`GDRL_ADAPTIVE=1 GDRL_DELTA_TICKS=8
GDRL_FAST_RESET=1`) ran the same 3048 ticks in **388 frames / ~6.5 s — ~2×
faster**. So `env.py:936`'s claim that `advanceSteps` is *"a strictly better knob
than experiments.cpp's adaptive-dt scheme"* needs **reversing, not
qualifying**: equal-and-safer on correctness, 1.0× against 2.2× on throughput.
The honest claim is that `advanceSteps` is correctness-preserving with **no**
throughput effect — a smaller claim than the docstring makes.

**They do not compose.** `telemetry.cpp:1363-1368` already warns that setting
both `GDRL_ENV_DELTA_TICKS` and `GDRL_DELTA_TICKS` leaves the effective dt to
whichever `$modify GJBaseGameLayer::update` hook is innermost — a linker detail.
This is a **pick-one** decision, not a stacking opportunity.

**Consequence for the sweep design:** `g_envDeltaT` is read once at load and has
no wire field, so an N-sweep **cannot be interleaved in one process** — it is one
launch per N, and all of them must hit the same binary or the sweep is void.

### Q4 (amended). The camera sensor is right, but the width **drifts between launches**

The original Q4 verdict ("the object scan is correct") should read: *correct for
collision geometry; blind to non-touch triggers; and it reports one false HAZARD
at the origin early in every episode.*

**The width is not stable: 569.0 world units on one launch, 493.0 on another,
same binary, identical physics.** Two independent observers, tier (iv) each.
Proposed mechanism, **UNVERIFIED**: GD's persisted window size escaping the
sandbox via `cfprefsd`, which `scripts/run_sandbox.sh`'s own header caveat #2
already warns ignores `CFFIXED_USER_HOME` — *"File I/O is redirected; the
preferences system is not."*

**Under Benchmark A the sensor definition IS the benchmark**, so two runs at
different widths are not the same benchmark and their attempts-to-completion
numbers are not comparable. **This promotes item 1b from hygiene to a blocker on
the acceptance run.**

### Q4a. The phantom is GD's **anti-cheat spike** — a third injected object

Not the collision proxy `8dd5ceb` identified and filtered; that filter does not
cover this one. `m_anticheatSpike` is a real binding
(`bindings/2.2081/GeometryDash.bro:8219`). It is a real `GameObject` at
`(0, 105)` with `objectType` Hazard, so `collapseKind` maps it to **HAZARD** —
a false hazard for the first ~210 ticks of **every episode**, for as long as the
camera's behind-window still contains x=0. The player spawns inside its rect and
does not die, which is why it survived this long.

Corroborated two independent ways: live `m_objects` vs `m_sections`, and
re-derived from the dumped level string — `id 8` counts **166** in the string
against **167** live.

Filtered by **pointer identity**, not id or position: `id 8` is the ordinary
spike and the other 166 must all still be reported.

### Q5. **Stereo Madness ends in a ship section**

Tier (iii), from the game's own dumped level string.
`DUMP_LEVEL 1: 2291 objects, 0 move triggers`. Runtime y = level y + 90.

| id | x | y (runtime) | mode | ≈ tick @ 1.298250437 u/tick |
|---|---|---|---|---|
| 13 | **7995** | 255 | ship | ~6158 |
| 12 | **12555** | 239 | cube | ~9671 |
| 13 | **22935** | 239 | ship | ~17666 |
| 13 | **24045** | 405 | ship | ~18521 |

**Speed portals: 200/201/202/203/1334 → all count 0.** Max object id in the
level is 142.

`README.md:1066`'s *"ship at x=7995, cube at x=12555"* is **confirmed exactly**
and **incomplete** — it omits the ship portals at 22935 and 24045. Its *"speed
portals: none at all"* is confirmed.

**The load-bearing consequence: the last cube portal is at 12555, ship portals
follow at 22935 and 24045, and there is no cube portal after. Last object
x=26384, `levelLength` 26724. The level ENDS IN SHIP, so a cube-only action
space cannot clear it.** Deepest reach `3959.183837891` ≈ tick 3050 is 49.5% of
the way to the *first* ship portal.

Id→vehicle mapping rests on the repo's own `kIdShipPortal = 13`
(`mod/src/synth.cpp:41`), not re-measured live.

### Q5a. Ship mode is probably NOT a second sensor project

Accidental tier (iv) evidence, salvaged from the contaminated run below —
*Bloodbath* starts in ship and the mod tracked it correctly:

```
COND tick=0   x=0.000   ship grav=dn size=1.00 spd=0.90 gmul=0.96 ...
COND tick=452 x=586.055 ship grav=dn size=1.00 spd=0.70 gmul=0.94 ...
ATTEMPT 1 lvl=10565740 maxX=687.566162109 t=2.287500119 frames=300
MODE tick=549 x=0.000 from=ship cleared=cube type=6 p1
```

Vehicle correctly reported, tick clock consistent (`t=2.2875` → tick 549 under
`lround(t*240)`), `maxX` accumulating, gravity multiplier tracking, a speed
portal firing. `MODE`/`COND` read the same `PlayerObject` fields the ENV wire
does, so the plumbing is not vehicle-specific. `GdrlPlayerState`
(`gdrl_schema.hpp:189-202`) already publishes `yVelocity`, `gravity`, `rotation`,
`vehicleSize`, `isUpsideDown`, `isSideways`, `isOnGround`, plus an uncollapsed
`vehicleFlags` bitfield. **No field is missing.**

Three things UNVERIFIED, and one is a real risk:
1. `yVelocity` semantics in ship — same field, and ship is vertical-velocity
   driven, so it should be *more* informative. Never observed in ship.
2. **`isOnGround` in ship.** A ship can rest against floor *or* ceiling and
   `m_isOnGround` is one bit. If it cannot distinguish them, a ship policy
   cannot tell "landed" from "hit the ceiling" from that field alone.
   `rotation` and `yVelocity` sign may disambiguate — that is the difference
   between a **sensor gap** and **decoder work**.
3. Whether Q1's auto-repeat law survives. It almost certainly does not.

Cheapest close, no new code: `GDRL_SYNTH=1` already builds a level with a ship
portal near the start (`kIdShipPortal = 13`). One launch answers all three.

### The move-trigger census is **void**, and this was already known

`README.md:1066` claims **4** move triggers total across all 21 main levels, all
in lvl 21, all `touch=1`. Measured this session by two independent parsers (the
level string directly, and the mod's own C++ extraction) agreeing exactly:

| dump | records | `id 901` | key 11 (touch) |
|---|---|---|---|
| `level-1.txt` | 2291 | **0** | — |
| `level-21.txt` | 27283 | **177** | **absent on all 177** |

Level 21 alone holds **177**, spanning **x=1.0 to 24993.5**, and **none is
touch-triggered** — so the claim is wrong in *both* directions, and 7 (not 4)
fall inside the quoted 7813–8455 window. Per the measured rule that only
`touch=1` objects get sectioned, 0 of 177 should have been sectioned, yet the
census reported 4. **Treat the whole census row as void, not merely
undercounted.**

**And `probes.cpp:1014-1020` already documented this exact 44× undercount** —
*"The section walk below is not a valid instrument for triggers... Every
conclusion about whether a synthetic object 'loaded' that rested on the section
walk is therefore unfounded."* The finding existed in the C++ and never
propagated to the README or to the synth-track justification. **This is a
distribution failure, not a discovery.**

**Consequence for `mod/src/main.cpp:412-415`** (*"the main levels contain no
reachable move trigger and no speed portals at all"*): the speed-portal half
stands, verified on lvl 1. **The move-trigger half does not** — 177 exist on lvl
21, starting at x=1.0, so reachability is no longer obviously the binding
constraint. The synth track may still be justified; **not on this number.**

### Triggers, and why the sensor's blindness is nearly moot here

`scanObjects` reads `m_sections`, and GD never registers non-touch-triggered
triggers there. Across 21 levels **every** missing id is a trigger id (22–33,
56–59, 105, 744, 899, 900, 901, 915, 1006, 1007, 1049); **not one solid, hazard,
portal, pad, ring or slope is ever missing.** So the agent loses no collision
geometry.

Whether an A-legal agent may see triggers at all is an **open scoping decision**
— triggers are the level's *future* rather than its present, and they are not
rendered, so a human sightreading cannot see them either. But note
`probes.cpp:958-959`: **Stereo Madness is a 2013 level, from long before 2.0
introduced triggers**, and its dump confirms `0 move triggers`. For the current
goal the question is nearly moot.

### A contamination mode that the methodology rules already predicted

Both the tester and the debugger independently hit a run in which the `GDRL_*`
vars never reached the game and something walked GD into *Bloodbath*. Each
nearly misdiagnosed it as the defect they were hunting. Every health signal
stayed quiet: `input[clean]`, `ui=0`, `uiTot=0` all looked fine while a human
played a different level. The run produced a complete, plausible, worthless data
set.

**This file's own "Methodology rules earned the hard way" already records the
identical failure** — *"`input[clean]` meant clean-of-buttons and said nothing
about which level was loaded — the game silently drifted to Back On Track and
kept reporting clean."* The rule was written down and the guard was never built,
so it happened again with a different level name. See #23.

**Diagnosis (investigation only — no code was written).** `GDRL_PIN_LEVEL` has
no logic defect. `g_pinLevel` (`main.cpp:121`, duplicated at `telemetry.cpp:254`)
is `const bool` read once at static-init; both read the same string so they
cannot disagree. The full enforcement path was read — the wrong-level check at
`main.cpp:451`, the `WRONG_LEVEL` status wiring at `telemetry.cpp:1189`, and
`sightread.py`'s `FrameGate` refusing `GdrlStatus.WRONG_LEVEL` — **and the
`GDRL_ENV` channel already refuses correctly.** If the var never reached the
process, the guard was `false` by correct, intended design. *The off-state
operating correctly on a var that silently never arrived is the whole failure.*

The real gap is on the **log** path, which runs regardless of `GDRL_ENV`:
between wrong-level detection and the queued scene-replace landing on the next
frame, `resetLevel()` can still fire and emit an ordinary-looking
`ATTEMPT … input[clean blocked=0 leaked=0 ui=0 uiTot=0]` line for the wrong
level — distinguishable only by `lvl=`, which nobody greps. The comment at
`main.cpp:449-450` already describes this exact failure from a *previous*
incident; the guard built in response only covers "wandered via menu mid-run",
not "never had the var at all".

**Unresolved contradiction, and it sharpens the root cause.** `inputVerdict()`
returns `"clean"` only when `GDRL_BLOCK_INPUT` was observed as `1`. The
contaminated run's line reads `input[clean …]`, **not `UNGUARDED`** — so
`GDRL_BLOCK_INPUT` *did* reach that process. That makes the incident **partial
propagation, not total**: some switches arrived and some did not, which is a
different and more alarming root cause than "the env was lost". Settle this
before building the guard; it changes where the fix belongs.

**Design settled, not written:** (1) stamp every `GDRL_*` switch the process
actually observed — 39 distinct names are read across `mod/src/*.cpp` — into a
startup log line, so "did the vars arrive" is answerable from the log with zero
inference; (2) tag any attempt produced while pinned-and-wrong as a distinct
`log::error` `ATTEMPT-REFUSED` line so a naive scan cannot mistake it for data.
**Note (2) would not have caught this incident** — with `g_pinLevel` false the
branch is never entered. **(1) is the one that closes it.**

### What this session did NOT do

- **No agent played anything.** The acceptance criterion is untouched.
- Q2 not re-run against the current binary; **it is stale by construction.**
- `GDRL_ENV_DELTA_TICKS` **never set** — determinism and throughput both
  UNVERIFIED, and it is now the highest-value measurement in the project.
- EXP path determinism never established, though it is 2× faster.
- Item 1b (#20) still not implemented, and now blocking.
- Ship: nothing built, nothing decided.

### The arithmetic that should drive next session

Calculation from measured inputs, **not** a measurement — inputs solid,
conclusion an order of magnitude:

- 26724 units ÷ 1.298250437 ≈ **20,585 ticks ≈ 86 s of game time** for one
  complete run.
- Frame-limited at ~1× real time, so an attempt costs ≈ `deathTick/240` seconds.
- Best-ever is 3048 ticks / 12 jumps → a full clear is ~80–120 jump decisions.
- The driver solved a 7-hazard puppet in 12 attempts (~1.7/hazard); real
  geometry with backtracking will be worse — call it 10–20 attempts per decision.

→ order of **1,000–2,000 attempts averaging ~40 s ≈ 12–20 hours** at today's
throughput. At EXP's measured 2×, ~6–10 h. If `GDRL_ENV_DELTA_TICKS=8` proves
deterministic at ~8×, ~2 h.

**So the dt sweep is not tuning. It is the difference between "run it overnight"
and "not feasible in this project's current shape."**

### Operational notes — read before the next live run

**Preserved evidence** (`backups/2026-08-16/`, gitignored but durable — the
originals live in the sandbox save dir and are one `rm -rf sandbox/` from gone):
`level-1.txt` (2292 records, **0** `id 901`), `level-21.txt` (27284 records,
**177** `id 901` — reproduced by a third independent parser at wrap),
`level-21-move-901.txt`, and `telemetry_repaired.cpp`.

**Verbatim invocations**, from the repo root. Record the binary hash first,
every session:
```sh
shasum -a 256 "sandbox/Geometry Dash.app/Contents/geode/mods/gdrl.probe.geode" \
              mod/build/gdrl.probe.geode
```

Q2 / EXP determinism — **unshifted** ticks (this path is the origin of the `+1`
convention):
```sh
GDRL_AUTOPLAY=1 GDRL_EXP=1 GDRL_BLOCK_INPUT=1 GDRL_PIN_LEVEL=1 \
GDRL_FAST_RESET=1 GDRL_ADAPTIVE=1 GDRL_DELTA_TICKS=8 \
GDRL_INJECT_SEQ="325,712,1074,1162,1266,1798,1934,2154,2318,2482,2686,2878" \
./scripts/run_sandbox.sh
```

ENV path (#22 validation, id-8 census) — **+1-shifted** ticks
`326,713,1075,1163,1267,1799,1935,2155,2319,2483,2687,2879`, hold 8 each:
```sh
GDRL_ENV=1 GDRL_AUTOPLAY=1 GDRL_PIN_LEVEL=1 GDRL_BLOCK_INPUT=1 ./scripts/run_sandbox.sh
```

dt sweep — **one launch per N**, N ∈ {1,2,4,8,16,32}, N=1 and N=32 repeated last:
```sh
GDRL_ENV=1 GDRL_ENV_DELTA_TICKS=N GDRL_AUTOPLAY=1 GDRL_PIN_LEVEL=1 \
GDRL_BLOCK_INPUT=1 ./scripts/run_sandbox.sh
```

Compose check (both dt rewriters on, deliberately): add `GDRL_ENV_DELTA_TICKS=8`
to the EXP invocation and watch the `telemetry.cpp:1363` warning plus
`header.dtIn`/`dtUsed` to see which hook won.

Synth ship — **omit `GDRL_PIN_LEVEL`**, it pins level 1 and the synth level is
not level 1:
```sh
GDRL_SYNTH=1 GDRL_ENV=1 GDRL_AUTOPLAY=1 GDRL_BLOCK_INPUT=1 ./scripts/run_sandbox.sh
```

**Provenance check for every run above, until #23 exists:**
```sh
grep "gdrl\] autoplay ->" "sandbox/Geometry Dash.app/Contents/geode/logs/$(ls -t 'sandbox/Geometry Dash.app/Contents/geode/logs' | head -1)"
```

#### Things that cost runs

- **`GDRL_MAX_ACTIONS = 8`.** `Channel.respond()` raises on >8 actions, so a
  12-jump plan must span ≥2 responses.
- **`env.py` clients SIGSEGV when the game dies while they hold the mmap** —
  numpy reads unmapped pages. **Any driver must write results incrementally, per
  attempt.** An end-of-run dump is a dump that never happens. Cost two full runs.
- **A timed-launch wrapper collided with itself** — an earlier wrapper's `pkill`
  timer killed a *later* run's game 37 s in. Whatever wraps `run_sandbox.sh`
  needs single-instancing too, not just the game.

#### Looked wrong, was not

- **`objectCount=1` on Stereo Madness** was nearly filed as a failure. The level
  string says exactly 1 object in that window. **The level dump is a cheap
  oracle for the object scan — make it the first check, not the last.**
- **The 569→493 width variance is not caused by any switch.**
  `GDRL_PROBE_VIEWPORT` was suspected and ruled out: probe-on run = 493,
  probe-off runs = 569 *and* 493. It tracks the OS window only.

#### The methodological one worth carrying forward

**A null result needs a positivity control.** The first Q1 design returned
`hold8`/`hold400`/`hold2000` → identical `maxX=542.668640137`, i.e. "holding
does nothing". That was **false**: the cube died airborne at tick 418 and the
hold never spanned a landing. The control that rescued it — *"did the cube land
at all during the hold?"* — is trivial and was missing. This nearly went into a
report as a finding that would have collapsed `sightread.py`'s action space.

#### Counter and field gotchas

- **`ENV a=… steps=` is published observations, not physics steps** — it reads
  `103` for a 3048-tick attempt at stride 32.
- The `ENV a=` and `ATTEMPT` counters are separate statics in different
  translation units. They agreed in every observed run; do not assume it.
- **`level-1-move-901.txt` is 0 bytes** — correct (lvl 1 has none), but
  indistinguishable from a failed write.
- **`frames=` is the honest throughput number**: ~822 per 3048-tick attempt on
  the ENV path at *every* stride, ~388 on the EXP path. That invariance is the
  mechanism behind 14 s vs 6.5 s.

---

### Queue for the session after next (superseded by the RESUME box above)

> **RESUME HERE — 2026-08-15 (second wrap). Read this box before anything else.**
>
> **THE NEXT SESSION'S GOAL, set by Rex: an agent that plays Stereo Madness all
> the way through.** Everything below is ordered to serve that and nothing else.
>
> **The single most valuable thing you can do first is RUN THE GAME.** Both of
> this session's deliverables — the camera-derived sensor and the search driver —
> are complete, green and **have never been run against Geometry Dash**. Between
> them they carry six live hypotheses that one afternoon of running settles and
> no amount of further coding does. Do not write more code before this.
>
> The first live run must measure, in this order:
> 1. **Does holding jump auto-repeat, and on which tick after landing?** The
>    entire interval action space is a bet on this. The toy level was *written*
>    to auto-repeat, so every passing test about it is circular as evidence about
>    GD and says so in its own docstring.
> 2. **Re-verify the default-off byte-identity guarantee.** It was last checked
>    before two large refactors of `telemetry.cpp` and `viewport.cpp`. It is now
>    stale twice over. Baseline to reproduce: 3/3 attempts at
>    `maxX=3959.183837891`, 0 VIEWPORT lines with no `GDRL_*` set.
> 3. **Is replay deterministic under the stride hybrid?** The driver's whole
>    throughput argument rests on replaying a committed prefix at
>    `GDRL_ENV_DELTA_TICKS=32` and getting the same trajectory as at stride 1.
>    Untested. If it is false the search cost model collapses.
> 4. **Does the camera-derived window behave live?** Sensor width should be
>    ~569 units, not 1800. Watch for the new one-time warnings (coverage clamp
>    binding, `sizeMismatch`) and for the `rotated` refusal — if any part of
>    Stereo Madness rotates the camera the agent goes **blind** there by design,
>    and nobody has checked whether it does.
> 5. **Then, and only then, run the driver for real.** Acceptance criterion:
>    autonomously reach or beat `maxX=3959.183837891` with zero hardcoded tick
>    numbers, reporting every attempt including failed probes.
>
> Tree state at wrap: **builds clean, `lipo` → `x86_64 arm64`, 269 tests pass,
> `mutate.py` 49 of 49 killable killed** (all three verified in the main thread,
> not taken from an agent's report).
>
> **Two claims in the PREVIOUS version of this box were false**, and the way they
> were false is the reusable lesson:
> - It said `viewport.hpp` "exists so `telemetry.cpp` can call
>   `cameraWorldRect()`". **`cameraWorldRect()` had no implementation at all** —
>   declared at `viewport.hpp:83`, defined nowhere. The tree built only because
>   nothing called it. A header carrying 80 lines of confident prose about how a
>   function behaves is not evidence the function exists.
> - `readWorldTransformNeutrally()`, the state-neutral reader that header
>   promises the observation path uses, was **dead code with no caller**.
>   `sampleViewport()` called `nodeToWorldTransform()` directly. So the
>   2026-08-15 viewport measurements were taken through the *non*-neutral path,
>   and `GDRL_VERIFY_XFORM` had never verified anything on the path it was
>   written for. The measurements stand (it was a probe); the safety argument did
>   not apply where it was claimed to.
>
> Both are now really implemented and really wired in. See sections O and P.
>
> **Throughput is the binding constraint on a full clear, not intelligence.**
> Measured this session: Python decode+respond is ~0.4 ms empty / ~0.86 ms with
> 120 objects — **Python is not the bottleneck, the game is.** A full Stereo
> Madness attempt is ~20,600 ticks (~86 s of game time), and under Benchmark A a
> search must **replay the prefix every attempt** — rewinding without paying for
> the replay is Benchmark B by definition. So attempt cost grows with depth. The
> stride hybrid is the answer and needs no mod work; see section P.
>
> Still open and now urgent: **1b** is RULED but not implemented (pin the window
> size, not the design width — see item 1b), **2b** (enforce the contract; the
> driver's `Sight` layer discharges part of it for the driver only, nothing else),
> **1c** (what item 1 still did not measure).
>
> A **Benchmark B oracle track** opened 2026-08-15 alongside A (state
> snapshot/restore, `GDRL_SNAPSHOT`). It is an oracle only — ground truth to
> score A against, **a parameter and never a fork**, and its knowledge must not
> be reachable from the A observation path. See "Open decisions → 0".


**Re-scoped 2026-08-14 by the Benchmark A decision.** Items 1 and 2 did not exist
before it and now outrank everything else: under A the sensor definition *is* the
benchmark, and ours has never been justified.

1. ~~**Measure what is actually on screen**~~ — **MEASURED** 2026-08-15 with
   `GDRL_PROBE_VIEWPORT` (`mod/src/viewport.cpp`, default off). Stereo Madness,
   1x, zoom 1.0.

   **The visible viewport is 569.0 × 320.0 world units**, player 36.8% across it.
   Against the config — and note `_VERT` is a *half*-extent
   (`telemetry.cpp:652-653`), so its overreach is larger than it looks:

   | axis | configured | visible | overreach |
   |---|---|---|---|
   | ahead | 1400 | **359.5** | **3.9×** |
   | behind | 400 | **209.5** | 1.9× |
   | vertical | ±600 (1200 tall) | **320 tall** (+215 / −105) | **3.75×** |

   **The suspicion is confirmed: no run to date is a Benchmark A run.** The real
   lookahead is ~1.15 s at 1x, not ~4.5 s. The real vertical field is also
   **asymmetric** where ours was symmetric — the player sits low on screen.

   Believable because three *independent* sources agree on 569×320: the inverted
   `m_objectLayer->nodeToWorldTransform()`, the design resolution, and GD's own
   `m_cameraWidth/Height`. The probe also emits `pscr=` (the player mapped
   *forward* into screen points) so a screenshot can falsify it without trusting
   the mod.

   **Ruling: the window is now camera-derived, not constant.** Retuning the three
   numbers would have been wrong — zoom varies across levels and no constant
   tracks it, and `kResolutionFixedHeight` means the visible *width* is a
   function of the OS window's aspect ratio, so a constant is not imprecise but
   *undefined*. `scanObjects()` should take the live camera rect each step;
   `GDRL_ENV_WIN_*` survive as optional off-by-default overrides for ablations
   and the B oracle.

   **THE RULING IS NOW IMPLEMENTED** (2026-08-15, second session). See section O
   for what landed and what it cost. In one line: `scanObjects()` takes
   `gdrl::cameraWorldRect()` each step, refuses rather than falls back, and
   `GDRL_ENV_WIN_*` survive as per-axis opt-in overrides that warn the run is not
   Benchmark A. **It has not been run against the game.**

1b. **Pin the aspect ratio** — **RULED 2026-08-15, NOT IMPLEMENTED.**

   The facts: `viewport.cpp:168-176`. The resolution policy is
   `kResolutionFixedHeight`, so the design **height** is pinned at 320 world
   units and the design **width** is recomputed as
   `ceil(screenW / (screenH / 320))`. Two different OS window sizes in the
   2026-08-15 run both produced 569 world units, because both are ~16:9:

   | window | `screenW/(screenH/320)` | design width |
   |---|---|---|
   | 960×540 (fullscreen) | 568.89 | 569 |
   | 396×223 (`GDRL_WINDOWED`) | 568.30 | 569 |

   So the vertical world extent is invariant and the horizontal one is a function
   of the OS window's aspect ratio. UNVERIFIED at any non-16:9 aspect — the
   formula predicts it, no run has been done.

   **The ruling: pin the window size, not the design width.** Rejected: forcing a
   fixed design width by overriding the resolution policy. That decouples the
   sensor from the screen — the agent would receive a window that is not what is
   rendered, which is the exact defect item 1 just fixed. A camera-derived sensor
   is *correct* at any aspect; the only problem is that two runs at different
   aspects are not the same benchmark. Two mechanisms, both wanted:

   1. **Pin the OS window size for benchmark runs** (`scripts/run_sandbox.sh` and
      the `GDRL_WINDOWED` path at `main.cpp:389`). Today `GDRL_WINDOWED` merely
      *happens* to keep ~16:9 — it inherits whatever the display gives it, so a
      fullscreen run on a non-16:9 display silently changes the sensor width.
   2. **Assert the derived design width at attempt start and stamp it into the
      observation header**, so a run is checkable from its own log rather than
      from a claim about how it was launched. Mismatch should be loud — refuse,
      do not substitute.

   Not merely hygiene: under A the sensor definition *is* the benchmark, so an
   unpinned aspect means attempts-to-completion is measured against a sensor of
   unstated width. Same class of defect as the 3.9× overreach, just smaller and
   harder to see.

1c. **Still unmeasured after item 1**, and worth not forgetting: every speed
   bucket other than 1x, every level other than Stereo Madness, anything past
   tick 391, any non-16:9 aspect, any *varying* zoom (this run held 1.0
   throughout), and any nonzero camera angle — the four-corner bounding path is
   untested against a rotated camera. The probe measures **geometry, not
   perceptibility**: fades, effects and draw order are not modelled, so the rect
   is an upper bound on what a human sees. Also UNVERIFIED and now on the hot
   path: that `nodeToWorldTransform()`'s cocos cache recomputation cannot perturb
   what is rendered.

2. ~~**Rule on every field the agent receives.**~~ — **DONE** 2026-08-15.
   `docs/observation-contract.md`. Every wire field ruled ALLOWED / FORBIDDEN /
   NEEDS-MEASUREMENT against what a human player can perceive, on two axes
   (verdict, and audience: POLICY vs EXPERIMENTER) plus an enforcement tier
   (MOD / PYTHON / **DOC**).

   The predicted hard cases resolved as guessed — `levelLength` **allowed** (the
   progress bar gives `playerX / levelLength` directly), whole-level
   `objectCountTotal` **forbidden for the policy** but kept as the run gate. Four
   rulings were *not* predicted and they change what gets built:

   - **`groups[]` is FORBIDDEN for the policy.** Group membership is invisible on
     screen; its only use is predicting which objects a trigger will move. Narrow
     exception for attributing an *already-observed* motion.
   - **`commands[]` should never be populated for the agent.** The struct carries
     the *script* (`duration`, `actionValue1/2`, easing), not the motion — a
     block's entire future before it happens. The `GJEffectManager` container
     hunt that has blocked it is moot for the agent; if ever populated it is
     EXPERIMENTER-only, to *score* projection, never to feed it.
   - **`pending[]` must never be populated at all.** Benchmark A's own definition
     names it: "cannot inspect future trigger states that have not occurred."
     `PENDING_UNAVAILABLE` is now **permanent by policy, not blocked by
     engineering** — nobody should finish the trigger-objectID → kind mapping
     expecting to fill this table.
   - **`speedSegs[]` legitimacy attaches to the collection site**, not the
     struct: window-limited is allowed, whole-level is forbidden, and `startX` +
     `bucket` looks identical either way. Whoever populates it (item 6) must
     window-limit exactly as `scanObjects()` does and say so at the call site.

   And one that bounds a future claim rather than changing code: **`isHazard` is
   a granted prior, not a learned fact** — a deliberate concession, argued in the
   doc rather than asserted. Any result resting on the agent having *discovered*
   what kills it is invalid under the contract as written.

   **The gap the contract names about itself:** rulings 1–4 are tier **DOC** —
   held by nothing. The mod still emits `objectCountTotal` and `groups[]`, and
   `env.py` still hands the whole record over. A policy-facing view that
   structurally withholds the FORBIDDEN fields (the way `KnownMask` already
   withholds a refused mask) is the follow-on task; until it exists the contract
   is an intention, not a constraint. Also unaudited: whether `env.py`'s derived
   accessors leak, since a combination of allowed fields is not automatically
   allowed.

2b. **Enforce the contract structurally** (new, created by item 2). Build the
   policy-facing view; move rulings 1–4 from tier DOC to tier PYTHON or MOD.
   Audit `level_length_agrees_with_section_count()` and `column_span()` for
   combination leaks. Mutation-grade whatever lands.

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

8. **`trainer/sightread.py` — the A-legal search driver.** **BUILT 2026-08-15
   (second session); the acceptance criterion is NOT met because it has never
   been run against the game.** See section P. The spec below is retained
   because it is what the implementation was graded against, and because the
   parts marked "design points" are still the parts most likely to be wrong.

   **There was no search driver.** The 12-jump / `maxX=3959.183837891` / ~14.8%
   best on record came from a human hand-picking tick numbers and feeding them
   through `GDRL_INJECT_SEQ`. Nothing in this repo chooses an action.

   Acceptance criterion, deliberately falsifiable: **autonomously reach or beat
   `maxX=3959.183837891` from zero hardcoded tick numbers**, reporting the
   attempt count it took — *including every failed probe*. An attempt count that
   quietly excludes exploration is the Benchmark B number wearing an A label.

   Design points that decide whether it works:

   - **Replaying a known-good prefix is A-legal**, and must be, or the search is
     impossible. A human replays from the start every attempt; only the tail past
     their best is uncertain. Determinism makes the prefix reproducible. What is
     forbidden is rewinding *without paying for the replay* — that is B.
   - **Backtracking is the whole task.** The old greedy approach stalls on
     coordinated jumps, where the right action at decision *n* only pays off
     given a specific choice at *n−1*. A driver that only pushes the frontier
     forward will reproduce that stall exactly.
   - **The action space is intervals, not points** — see the HOLD note below.
   - **Episodic memory is the main information source, not a nice-to-have.** With
     the horizon now measured at ~277 ticks, the driver cannot see what killed it
     until it is nearly on top of it. What it remembers from last attempt is most
     of what it knows. Objective D is load-bearing in the strongest sense.
   - **A-legality must be structural**, not remembered: route observation reads
     through one accessor layer that *cannot* return the contract's FORBIDDEN
     fields (`objectCountTotal`, `groups[]`). Copy `KnownMask`'s posture — refuse
     rather than return something misleading. This discharges part of 2b.
   - **Measure env-loop throughput before building on it.** Stereo Madness is
     ~20,600 ticks; a per-tick Python round trip against `GDRL_ENV_WAIT_US`
     (250 ms) may be far too slow for thousands of attempts. Hybrid fallback:
     committed prefix mod-side via `GDRL_INJECT_SEQ`, env loop only near the
     frontier. The ~3.9 attempts/sec on record came from a different path.

### Holding the jump button auto-repeats — the action space is intervals

Recorded 2026-08-15 (from Rex; **confirm by measurement before relying on it**,
per this repo's standing rule). **Holding jump makes the cube jump again each
time it lands.** A held input is a *chain* of jumps, not one jump, and large
stretches of GD levels are cleared by simply holding through them. This is
standard play.

Two consequences, both load-bearing for item 8:

- **Model actions as intervals (start tick, release tick), not as taps.** Tap
  modelling searches a combinatorial space of individual jump ticks — roughly
  what the hand-tuned 12-jump sequence was. With holds, whole sections collapse
  to two tolerant parameters.
- **It may explain the "coordinated jumps" stall.** A run of jumps that appears
  to need several precisely-coordinated taps may be one hold with forgiving start
  and end ticks. Worth testing directly; it is a far easier target.

The transport already supports this end to end — `GdrlAction.holdTicks`, expanded
into push/release at `telemetry.cpp:456`, and `GDRL_INJECT_SEQ`'s `tick:hold`
syntax at `experiments.cpp:180`. **No mod work needed.**

Open and unmeasured: whether the re-jump fires on the landing tick or some ticks
after (exactly what a search would exploit); whether holding through a **ring/orb**
activates it or a discrete tap is required; and what a held input means in the
**ship** section, where control is continuous altitude rather than auto-repeat —
the action representation has to survive that change of meaning at the portal.

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

  **Opened 2026-08-15 as a parallel track**, on that footing exactly. First
  deliverable is the foundation everything else in B needs: **state snapshot and
  restore** (`GDRL_SNAPSHOT`) — capture the physics-relevant state at tick N, run
  forward, rewind, try a different action. Without it there are no hidden
  rollouts, only real attempts.

  Two design constraints that are not negotiable and were stated at dispatch:

  1. **A parameter, never a fork.** No B copy of `trainer/` or `mod/src/`. If a
     file is being duplicated, the design is wrong.
  2. **The oracle's knowledge must be unreachable from A's observation path.** If
     B knows a branch dies at tick 4000, that fact must not be able to travel to
     the A policy by any route. Nothing B builds may feed `GdrlObservation` or
     `env.py`'s decode.

  The acceptance test is unusually strong here and should not be softened:
  **snapshot at N, run to M, restore to N, run to M again — bit-identical, tick
  for tick.** Determinism is already proven (~550 null-input attempts, 1473 with
  input, zero divergent), so any divergence is a field the snapshot failed to
  capture, and the first divergent tick names the subsystem. An almost-correct
  snapshot is worse than none: it yields rollouts that silently diverge, which
  makes the oracle produce wrong ground truth — and wrong ground truth would
  corrupt the A scores it exists to provide.

  **Deferred 2026-08-15, same day, when the project reprioritised toward playing
  a level.** No snapshot code was ever written. But the layout research that ran
  first produced `docs/snapshot-notes.md`, and it changes how this should be
  resumed:

  **GD already contains a complete state snapshot and nobody here had looked at
  it.** Practice mode must do exactly this job. `PlayerCheckpoint` declares
  **185 members** — RobTop's own answer to "which `PlayerObject` fields are
  physics-relevant", including a tail a hand-built list would have missed:
  collision history (`m_lastCollisionTop/Bottom/Left/Right`), and consumed
  ring/pad sets (`m_touchedRings`, `m_ringRelatedSet`, `m_touchedPad`). That tail
  *is* the almost-correct failure mode made concrete — a position-and-velocity
  snapshot restores a player who can spend a ring they already used.
  `CheckpointObject` carries `GJGameState` by value plus `EffectManagerState`,
  and `createCheckpoint` / `loadFromCheckpoint` are live in the binary
  (bl-counted against `otool -tV`, and they nest).

  **So the first move on resume is to call GD's own checkpoint functions and run
  the divergence test — not to hand-build a 500-field capture.** Either it comes
  back bit-identical and the problem evaporates, or the first divergent field
  yields a small, evidenced patch list.

  Suspected gaps in GD's own snapshot, each a candidate first-divergence:
  `m_attemptTime` is **not** in `CheckpointObject` (this promotes backlog item 8),
  nor are `m_extraDelta`, the RNG seeds, `m_queuedButtons`, or the section grid —
  where any copy-assign shares the buckets.

  Two independent corroborations fell out of it: `SavedObjectStateRef` stores
  position **doubles** rather than the CCNode position, confirming the move-pipeline
  finding from GD's own save path; and `EffectManagerState::m_vectorGroupCommandObject2`
  being a `vector<GroupCommandObject2>` corroborates the `m_unkVector560`
  identification — which is the container the `commands[]` table was blocked on.
  (That table is now forbidden to the policy anyway; this matters only for the
  oracle.)

  **Evidence tier: (h) headers and (d) disassembly. The game was run zero times.**
  "GD contains a complete state snapshot" is a claim about what the struct
  *declares*, not about what restoring it *does*.
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
