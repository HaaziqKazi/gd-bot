# gd-rl — open work

Single place for what is left. Companion to `README.md`, which records what has
been **established**; this file records what has **not**.

Status as of commit `05dbf85`.

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

## Track 1 — Synth content + cheap measurements  ← SELECTED

Highest unblock-per-hour. Two of these are available right now with no new work.

### 1.1 Fix the synth move trigger (object id 901)  — BLOCKER

Object 901 is rejected by the level parser; every other object loads. Two
hypotheses already tested and **falsified**: compression is not the cause (raw
and compressed censuses are identical), and the header/object boundary is not
the cause (a sacrificial first block loaded fine while 901 still did not).

What is left is the trigger's **property encoding, which was written from
memory** — the half-remembered-table failure this repo has already paid for.

- [ ] Dump a real level string containing a move trigger. **Fingerdash has four**
      (x = 7813–8455).
- [ ] Read the actual property IDs off it; do not reconstruct from memory.
- [ ] Re-encode 901 in `mod/src/synth.cpp` and confirm it appears in the census.

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

1. [ ] **Which `GJEffectManager` vector holds live `GroupCommandObject2`.**
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
