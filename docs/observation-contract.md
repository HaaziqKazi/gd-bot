# The observation contract

**What the agent is allowed to receive under Benchmark A.**

Written 2026-08-15. Discharges `TODO.md` "Queue for next session" item 2.

Benchmark A (true sightreading, decided 2026-08-14) says the agent receives only
what it may legitimately observe. That sentence is not self-executing. This file
turns it into a per-field ruling, because a blanket verdict is exactly the kind
of claim this repo has learned not to trust: the phantom collision proxy
(README, "The player's own collision proxy was being reported as level geometry")
was a benchmark violation hiding inside a table everyone had already agreed was
fine.

---

## The standard

**A human player at a keyboard.** They can see the screen, hear the music, read
the percentage bar, and remember previous attempts. They cannot see off-screen
geometry, cannot read the level's trigger script, and cannot know how many
objects the level contains.

Two things the standard is *not*:

- **It is not "what is hard to obtain".** `m_sectionXFactor` is buried in the
  engine and takes disassembly to find, and it is still perfectly legitimate —
  it is a decoder constant that describes no geometry. Difficulty of extraction
  is not evidence of illegitimacy.
- **It is not "what a human literally computes".** A human does not compute
  `playerX = 3959.18`. They see their icon at a place on the screen. Exact
  coordinates for *visible* things are a faithful, higher-precision encoding of
  something genuinely perceived. Precision is not the violation; **reach** is.

The violation Benchmark A names is **information the player has no channel to**:
geometry beyond the sensor horizon, engine state that has not yet expressed
itself on screen, and future trigger states that have not occurred.

## Two independent axes

A field needs two verdicts, and conflating them is how a contract becomes
useless.

**Verdict** — may this reach a *policy*?

| | |
|---|---|
| **ALLOWED** | A human has a channel to this information. |
| **FORBIDDEN** | A human does not. Must never enter a policy input, a reward, or a feature derived from either. |
| **NEEDS-MEASUREMENT** | Legitimacy depends on a number nobody has measured yet. Currently treated as forbidden-in-effect: not usable as evidence for a Benchmark A claim until the measurement lands. |

**Audience** — who may read it at all?

| | |
|---|---|
| **POLICY** | May be fed to the agent. |
| **EXPERIMENTER** | The harness, the test suite, the run gate. Reading `objectCountTotal` to refuse a frame where the level never loaded is not cheating — it is deciding whether the *experiment* is valid, a judgement made outside the agent's loop and never returned to it. |

A field can be FORBIDDEN for the policy and mandatory for the experimenter.
Those are not in tension.

## Enforcement tier

Ruling something forbidden does not make it unreachable. Per the Benchmark A
decision, *the horizon is enforced in the mod, never by trusting the policy to
ignore what it was handed.* So each ruling also carries how it is actually held:

| Tier | Meaning |
|---|---|
| **MOD** | The mod does not put it on the wire. Unreachable. |
| **PYTHON** | `env.py` withholds or refuses it. Reachable by editing one file. |
| **DOC** | Written here and nowhere else. **Held by nothing.** |

**Most of this document is currently DOC.** That is stated up front so no one
mistakes the existence of this file for a guarantee. Closing that gap is listed
at the end.

**One ruling moved from DOC toward MOD on 2026-08-15: the scan window.** It was
`px ± GDRL_ENV_WIN_BEHIND/_AHEAD/_VERT`, three constants picked before Benchmark
A existed, and the mod now derives the window from the live camera rect every
physics step (`telemetry.cpp:828-832`, `gdrl::cameraWorldRect`). Geometry
outside the screen is no longer put on the wire, so it is unreachable rather
than merely disapproved of — tier **MOD**.

Two honest qualifications, because "MOD" overstates it otherwise:

* `GDRL_ENV_WIN_*` still exist as **off-by-default overrides** for ablations and
  the Benchmark B oracle. Setting one restores a constant window and the run is
  no longer Benchmark A. The mod logs that in as many words, and the window it
  used is on the wire in `windowMinX..windowMaxY`, but **nothing refuses such a
  frame** — so "A-ness" itself is still DOC, held by a warning line.
* The window is what is *geometrically on screen*, which is an **upper bound**
  on what a human perceives. Fades, effects and draw order are not modelled.

---

## Header — `GdrlObsHeader` (`mod/src/gdrl_schema.hpp:478`)

| Field | Verdict | Audience | Reason |
|---|---|---|---|
| `magic`, `version`, `_pad0/1/2` | ALLOWED | EXPERIMENTER | Transport framing. Describes no world state. |
| `flags` | ALLOWED | POLICY | Says which tables the mod *looked at*. This is the agent's own epistemic state, and Objective E needs it: "count 0 because nothing is there" and "count 0 because nobody looked" must stay distinguishable. Withholding it would make the agent *more* confident than the evidence supports, which is the opposite of the benchmark's intent. |
| `tick`, `attemptTime` | ALLOWED | POLICY | Elapsed time. A human perceives duration and the music is a metronome. |
| `dtPerStep`, `timeWarp` | ALLOWED | POLICY | Global rate. `timeWarp` is directly perceived — everything on screen moves faster. `dtPerStep` describes the substep and leaks nothing about the world. |
| `playerX`, `playerY` | ALLOWED | POLICY | The player sees their own icon. Absolute world coordinates are a precise encoding of a perceived thing, and every other position is relative to them. |
| `playerSpeed` | ALLOWED | POLICY | Perceived directly, and announced by a visible portal. |
| `objectCount` | ALLOWED | POLICY | Count of objects **in the window**, i.e. a property of what was scanned, not of the level. Contrast `validity.objectCountTotal` below — same word, opposite ruling. |
| `commandCount`, `pendingCount`, `speedSegCount` | ALLOWED | POLICY | Currently hard 0 with the UNAVAILABLE bit set (`telemetry.cpp:1130-1132`, zeroed at `:1135-1137`). See the table rulings. |
| `attempt` | ALLOWED | POLICY | The game displays "Attempt N" on screen in letters that big. Load-bearing for Objective D. |
| `frame`, `stepIndex`, `dtIn`, `dtUsed` | ALLOWED | EXPERIMENTER | Harness bookkeeping and clock cross-checks. No reason for a policy to see them; no harm if it did. |
| `windowMinX`, `windowMaxX`, `windowMinY`, `windowMaxY` | ALLOWED | POLICY | The **extent of the agent's own senses**. An agent that does not know where its knowledge stops cannot represent uncertainty, which is Objective E. Note carefully: the *field* is allowed unconditionally; the *values* were queue item 1's open question and are now the live camera rect, so these four fields vary per step and are no longer reconstructible from any constant. Knowing where your senses end is legitimate whatever the answer. |
| `sectionXFactor`, `sectionYFactor` | ALLOWED | EXPERIMENTER | Engine indexing constants (`0.01` and `0` — measured). Emitted so the column arithmetic can be checked rather than assumed. Describes no geometry. |
| `levelLength` | **ALLOWED** | POLICY | The hard case, and it resolves cleanly: the game draws a **progress bar**. A human reading the bar at position `p` knows `playerX / levelLength` directly, and combined with any sense of distance travelled that is informationally equivalent to `levelLength` itself. It tells you where the level ends — which is precisely what the bar tells you. |
| `coverageStartCol` | ALLOWED | POLICY | Indexes `coverage[]` into world space. Meaningless without the array it labels. |
| `sectionColumns` | **ALLOWED, with a constraint** | POLICY | `m_sections.size()` — the level's x-extent in 100-unit columns, so a second route to the same fact `levelLength` already gives. Allowed on that ground, and **only** that ground. It must never be used to infer anything about *content* (density, object count, where the geometry stops as opposed to where the level does). If a use is found that needs more than "the level is this long", that use is forbidden. |
| `objectsDropped`, `commandsDropped`, `pendingDropped` | ALLOWED | POLICY | "Some of what was in range did not fit." Truncation the agent cannot see is a silent lie about its own senses. |
| `isDualMode` | ALLOWED | POLICY | There are visibly two icons. |
| `isPaused`, `inResetDelay` | ALLOWED | EXPERIMENTER | Harness lifecycle. |

## Validity — `GdrlValidity` (`gdrl_schema.hpp:430`)

Every field here answers "can this frame be believed", which is an experimenter's
question by construction. The whole struct is **EXPERIMENTER**.

| Field | Verdict | Reason |
|---|---|---|
| `blocked`, `leaked`, `uiEvents`, `timeouts`, `inputVerdict` | ALLOWED (experimenter) | Input-path integrity. `leaked > 0` voids the attempt. This is measurement hygiene, invisible to the agent and irrelevant to it. |
| `status` | ALLOWED (experimenter) | Frame-refusal reason. |
| `levelID`, `pinnedLevelID`, `levelPinned` | ALLOWED (experimenter) | A human sees the level's name on the screen, so even policy exposure would be defensible; there is no reason to take it. |
| `blockInput`, `objectsTruncated` | ALLOWED (experimenter) | Harness configuration and capacity honesty. |
| `objectCountTotal` | **FORBIDDEN for POLICY**, required for EXPERIMENTER | `m_objects->count()` for the **entire level**. A human has no channel to this whatsoever — not through the bar, not through the screen, not through anything. It is also load-bearing as a run gate: `< 10` means the level string never loaded and every number from the run is invalid (`telemetry.cpp:1163`). Keep it, gate on it, never show it. |

## Player state — `GdrlPlayerState[2]` (`gdrl_schema.hpp:188`)

**All ALLOWED, POLICY.** Every field is the agent's own body: position, velocity,
gravity direction, rotation, size, vehicle, whether it is touching the ground,
whether it is dashing. A human perceives all of it, and proprioception is not
the thing Benchmark A is protecting.

`players[1].present` is ALLOWED for the same reason `isDualMode` is: you can see
whether there are two of you.

The `header.playerX/Y/Speed` duplication of `players[0]` is a deliberate
cross-check (`gdrl_schema.hpp:570`), not a second source.

## Coverage — `coverage[64]` (`GdrlCoverage`, `gdrl_schema.hpp:144`)

**ALLOWED, POLICY, and load-bearing.** Per-column epistemic state across the
window: `UNKNOWN` / `SCANNED` / `TRUNCATED` / `ABSENT`. This is the substrate
Objective E is built on, and `KnownMask` already refuses to let ignorance be
laundered into an array.

One wrinkle worth naming: `ABSENT` means "past the end of `m_sections`", i.e.
past the end of the level. That leaks the level's extent — the same fact
`levelLength` gives, allowed on the same progress-bar grounds. Consistent, and
noted so it is not rediscovered as a surprise.

## Objects — `objects[256]` (`gdrl_schema.hpp:231`)

Every entry is inside the scan window by construction, so the table's legitimacy
rests entirely on **whether the window matches the screen**. That was queue item
1 and it is now measured (569.0 × 320.0 world units, +215/−105 vertical, Stereo
Madness at 1x and zoom 1.0) and implemented: the window is the camera rect, not
three constants. The aggregate NEEDS-MEASUREMENT that used to hang over every
row below is therefore **discharged for the configuration that was measured**,
and only that one — see item 1c for the list of speeds, levels, zooms and aspect
ratios still unmeasured, and note that no run has yet confirmed the camera-derived
window on the live game.

| Field | Verdict | Reason |
|---|---|---|
| `x`, `y`, `halfW`, `halfH`, `rotation`, `scaleX`, `scaleY` | NEEDS-MEASUREMENT → ALLOWED | Geometry a player sees, at higher precision. Precision is not the violation; reach is, and reach is item 1. |
| `objectID`, `objectType`, `kind` | ALLOWED | The sprite is on screen. A human identifies a spike by looking at it. |
| `isHazard`, `slopeIsHazard` | **ALLOWED — deliberate concession, stated as one** | This one is not free, so it is argued rather than asserted. A human learns "spikes kill" from *prior experience of other levels*, not from this level's first attempt — they arrive already knowing. Handing over `isHazard` grants that same prior knowledge instead of making the agent re-derive it by dying, which would turn Objective D into a sprite-recognition exercise. **What this concedes:** hazard identity is given, not learned. If a future claim rests on the agent having *discovered* what kills it, this ruling invalidates that claim and must be revisited. `slopeIsHazard` rides along because two visually distinct slopes share a `GameObjectType`, so it is the only way to audit the collapse. |
| `uniqueID` | ALLOWED | Object permanence within the window — a human tracks "that block" across frames and across attempts. Objective D's episodic memory is explicitly legitimate under A. |
| `isGroupDisabled` | ALLOWED | A disabled object is not rendered. This is literally "is it on screen". |
| `known` | ALLOWED | Slot validity. `known=0` is "no object here", not "an object at (0,0)". |
| `groups[10]`, `groupCount` | **FORBIDDEN for POLICY** | **Group membership is invisible.** Nothing on screen tells a player that a block belongs to group 7. Its only use is to predict which objects a trigger will move — which is future structure, not perception. *Narrow exception:* a forward projector may use groups to attribute an **already-observed** motion to the objects visibly undergoing it, because that is extrapolation from something seen. Group ID as a categorical policy feature is forbidden outright. |

## Commands — `commands[64]` (`GdrlGroupCommand`, `gdrl_schema.hpp:281`)

**Currently unpopulated**, `COMMANDS_UNAVAILABLE` set, blocked on identifying
which `GJEffectManager` container holds live `GroupCommandObject2`
(`telemetry.cpp:1123-1126`).

**Ruling: FORBIDDEN for POLICY as specified.** This changes what should be built,
so it is worth being precise about why.

A live `GroupCommandObject2` is a motion **in progress**, and a human watching a
block slide across the screen certainly perceives that. But the struct does not
carry the motion — it carries the **script**: `duration`, `actionValue1/2`,
`easingType`, `easingRate`, `moveModX/Y`. That is the block's entire future,
exactly, before it happens. A player sees position and infers velocity; they do
not read the easing curve or the endpoint.

- **FORBIDDEN:** `duration`, `actionValue1`, `actionValue2`, `easingType`, `easingRate`, `moveModX`, `moveModY`, `targetGroupID`, `centerGroupID`, `commandType`, `actionType1/2`, `triggerUniqueID`, `controlID`, the lock flags.
- **Legitimate substitute:** observed motion across successive frames. Objective B stays intact — projection over *visible* geometry from *observed* displacement is perception plus extrapolation, which is what a human does.
- `currentXOffset`, `currentYOffset`, `currentAngular`, `deltaTimeInFloat`, `finished`, `disabled` describe the present, and the present is already in `objects[]` at full precision. They add nothing a policy needs.

**Consequence:** the blocker on this table is now moot for the agent. If it is
ever populated it should be EXPERIMENTER-only — as ground truth to *score*
projection against, never as projector input.

## Pending triggers — `pending[64]` (`GdrlPendingTrigger`, `gdrl_schema.hpp:355`)

**Currently unpopulated**, `PENDING_UNAVAILABLE` set.

**Ruling: FORBIDDEN. Categorically, for POLICY and for anything feeding a
policy.** This is the sharpest ruling in the document and the only one that needs
no argument, because Benchmark A's own definition names it: the agent *"cannot
inspect future trigger states that have not occurred."* That is this table,
field for field — `activationX` is where a trigger *will* fire, `moveOffsetX/Y`
is what it *will* do, `duration` is how long it *will* take.

The `PENDING_UNAVAILABLE` flag was previously "blocked on the trigger-objectID →
kind mapping". As of this contract it is **permanent by policy, not blocked by
engineering.** Nobody should complete that mapping expecting to populate this
table for the agent. The comment at `telemetry.cpp:1127-1128` should say so.

Legitimate under A: a trigger object that is **on screen** may appear in
`objects[]` like any other object — a human sees the portal. What is forbidden
is the pre-computed effect of one that has not fired.

## Speed segments — `speedSegs[16]` (`GdrlSpeedSegment`, `gdrl_schema.hpp:411`)

**Currently unpopulated**, `SPEEDSEGS_UNAVAILABLE` set, blocked on the four
unmeasured `UNITS_PER_TICK` entries (queue item 6).

**Ruling: NEEDS-MEASUREMENT, and the answer depends on how it is collected.**

- **Window-limited** (segments whose `startX` is inside the scan window): ALLOWED. A speed portal is a large visible object; a player sees it coming and knows what it does.
- **Whole-level** (every segment in the level, as the natural implementation would): **FORBIDDEN.** That is the level's entire speed script, most of it hundreds of units off screen.

The struct as declared cannot tell these apart — `startX` plus `bucket` looks
identical either way. So the ruling attaches to the **collection site**, and
whoever populates it must window-limit it exactly as `scanObjects()` does, and
say so at the call site.

---

## What this contract changes

Four rulings alter what gets built, rather than describing what exists:

1. **`objectCountTotal` must not reach a policy.** It is currently on the wire in a struct Python hands over whole. Nothing stops it today.
2. **`groups[]` must not reach a policy** except through an observed-motion projector.
3. **`commands[]` should never be populated for the agent.** The blocker that has held it is no longer the reason it is empty.
4. **`pending[]` must never be populated for the agent, at all.** Its UNAVAILABLE flag is now a policy statement.

And one that does not change code but bounds a future claim:

5. **`isHazard` is a granted prior, not a learned fact.** Any result that depends on the agent having discovered what kills it is invalid under this contract as written.

## What this contract does not settle

- **Whether the window matches the screen *in configurations nobody measured*.** Item 1 measured one: Stereo Madness, 1x, zoom 1.0, ~16:9. The mod's window is now derived rather than configured, which is what makes it correct at other zooms *by construction* — but "by construction" is an argument, and item 1c lists what has not been run. Item 1b is sharper still: under `kResolutionFixedHeight` the visible **width** follows the OS window's aspect ratio, so two runs at different aspects are not the same benchmark and nothing currently pins it.
- **Enforcement.** Rulings 1–4 are tier **DOC** — written here, held by nothing. The mod still emits `objectCountTotal` and `groups[]`, and `env.py` still hands the whole record over. A policy-facing view that withholds the FORBIDDEN fields structurally (the way `KnownMask` already withholds a refused mask) is the obvious next step, and until it exists this file is an intention rather than a constraint.
- **Whether `env.py`'s derived accessors leak.** `level_length_agrees_with_section_count()` and `column_span()` combine allowed fields; combinations of allowed fields are not automatically allowed, and no one has audited them for that.
- **Music.** A human hears the beat and many GD levels are synchronised to it. The agent receives no audio at all. This is the one place the agent is *poorer* than the standard, and it is worth knowing before attributing a failure to the policy.
