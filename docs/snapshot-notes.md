# GD state layout, for a future snapshot/restore

**Research notes for the deferred Benchmark B oracle (`GDRL_SNAPSHOT`).**

Written 2026-08-15. The task was to build state snapshot/restore in `mod/src/`.
It was **descoped mid-session** in favour of an A-legal search over real counted
attempts. **No snapshot code was written and no rewind was ever run.** What
follows is the layout research that had been done at the point of the descope,
recorded so whoever resumes does not re-derive it.

Every claim below carries its tier. The repo's grading applies:

| tier | meaning |
|---|---|
| (i) | validated against itself — near-zero value |
| (ii) | against an independent reimplementation |
| (iii) | against recorded game data |
| (iv) | against the live game |
| **(h)** | **read off a binding header — an assertion about the binding, not about GD** |
| **(d)** | **read off the shipped arm64 disassembly** |

(h) and (d) are not in the repo's usual ladder; they are added here because
almost everything in this file is one of them, and calling that "verified" would
be exactly the mistake this repo keeps paying for.

---

## 0. What was and was not done

**Done:**

- Enumerated the state-bearing classes and counted their members (h).
- Found **GD's own answer** to "what is the physics-relevant state" — see §2.
  This is the single finding worth carrying forward.
- Confirmed the checkpoint machinery is genuinely callable, by call-site count
  in the arm64 slice (d).
- Wrote `mod/tools/gen_snapshot_fields.py`, which derives per-class field lists
  from the binding headers. It is a standalone tool, wired into **nothing**.

**Not done, and not to be inferred from anything here:**

- No `mod/src/snapshot.cpp`. No `GDRL_SNAPSHOT` switch exists.
- **No capture, no restore, no rewind, no divergence test was ever run.** There
  are no per-tick numbers in this file because none were measured.
- No offsets were computed with `offsetof`; the numeric offsets quoted are the
  ones already in `README.md` or read from the disassembly, not re-derived.

---

## 1. The design that was being built, and why it was shaped that way

Recorded because the reasoning outlives the code that never landed.

**The trap.** A snapshot that is *almost* complete is worse than none: it yields
rollouts that silently diverge, which makes an oracle emit wrong ground truth.
So the design question is not "how do I copy state" but "how do I know the list
is complete".

**A hand-curated field list cannot answer that.** "Did anyone remember
`m_dashStartTime`?" has no checkable answer. The intended approach was therefore
to make the field list **mechanical**: parse the binding headers, emit an X-macro
per class, and let C++ decide *per field* (via `std::is_copy_assignable_v` etc.)
whether it can be captured — so that the set of fields that **cannot** be
captured is also derived rather than asserted, and can be printed at runtime.

`mod/tools/gen_snapshot_fields.py` implements the parsing half. Run it and it
prints:

```
GameObject          216 members  0 unparsed
PlayerObject        292 members  3 unparsed
GJBaseGameLayer     410 members  0 unparsed
PlayLayer            99 members  0 unparsed
GJEffectManager      53 members  0 unparsed
```

(h) These are members each class declares *itself*; base-class members are not
included. `PlayLayer` state is therefore `99 + 410`, and a `PlayerObject` is
`292 + 216`.

The three unparsed `PlayerObject` lines are one **anonymous union** at
`PlayerObject.hpp:2356`:

```cpp
union {
    float m_lastMovedTime;
    float m_somethingPlayerSpeedTime;
};
```

One float under two names. Any generated capture must take exactly one of them,
not both. This is the kind of thing a hand-written list gets wrong twice.

**§2 supersedes this design.** The generated-list approach was the best answer
available before `PlayerCheckpoint` and `CheckpointObject` were found. They are
better, because they are RobTop's own enumeration.

---

## 2. THE FINDING: GD already contains a complete state snapshot

**Read this section first.** Practice mode has to restore a mid-level state
exactly, so RobTop already solved the problem this task was posed as. The
structures are in the bindings and the functions are live.

### 2.1 The classes

(h) Field lists read from
`mod/build/bindings/bindings/Geode/binding_arm/<Class>.hpp`, GD 2.2081 ARM.

**`CheckpointObject` — the whole-world snapshot.** 21 members:

```
GJGameState                             m_gameState          // BY VALUE
GJShaderState                           m_shaderState
FMODAudioState                          m_audioState
GameObject*                             m_physicalCheckpointObject
PlayerCheckpoint*                       m_player1Checkpoint
PlayerCheckpoint*                       m_player2Checkpoint
int m_unke78; int m_unke7c; float m_unke80
bool                                    m_ground2Invisible
bool                                    m_streakBlend
int                                     m_uniqueID
int                                     m_respawnID
gd::vector<SavedObjectStateRef>         m_vectorSavedObjectStateRef
gd::vector<SavedActiveObjectState>      m_vectorActiveSaveObjectState
gd::vector<SavedSpecialObjectState>     m_vectorSpecialSaveObjectState
EffectManagerState                      m_effectManagerState
cocos2d::CCArray*                       m_gradientTriggerObjectArray
bool                                    m_unk11e8
gd::unordered_map<int,SequenceTriggerState> m_sequenceTriggerStateUnorderedMap
int                                     m_commandIndex
```

**`PlayerCheckpoint` — the per-player physics state. 185 members.** This is the
authoritative answer to "which `PlayerObject` fields are physics-relevant": GD
copies 185 of `PlayerObject`'s 292+216 and considers that sufficient to resume.
It includes exactly the things the task brief listed — position, velocity,
gravity, rotation, vehicle flags, ground and dash state — plus a long tail that
a hand-written list would have missed, e.g.:

```
m_position, m_lastPosition, m_yVelocity, m_yVelocityUnrounded, m_fallSpeed,
m_gravity, m_gravityMod, m_rotation, m_playerSpeed, m_speedMultiplier,
m_isShip/Ball/Bird/Swing/Dart/Robot/Spider, m_isMini, m_isUpsideDown,
m_isSideways, m_isGoingLeft, m_isOnGround, m_isOnGround2/3/4, m_isDashing,
m_dashX, m_dashY, m_dashAngle, m_dashStartTime, m_dashStartTimeold, m_dashRing,
m_isOnSlope, m_wasOnSlope, m_slopeVelocity, m_slopeAngle, m_slopeStartTime,
m_slopeEndTime, m_currentSlope, m_currentSlope2, m_currentPotentialSlope,
m_collidedObject, m_lastGroundObject, m_preLastGroundObject,
m_collidingWithLeft, m_collidingWithRight, m_collidedTopMinY,
m_collidedBottomMaxY, m_collidedLeftMaxX, m_collidedRightMinX,
m_lastCollisionTop/Bottom/Left/Right, m_jumpBuffered, m_wasJumpBuffered,
m_stateJumpBuffered, m_touchedRing, m_touchedRings, m_ringRelatedSet,
m_touchingRings, m_touchedPad, m_touchedGravityPortal, m_objectSnappedTo,
m_snapDistance, m_lastPortalPos, m_lastActivatedPortal, m_lastFlipTime,
m_lastLandTime, m_lastGroundedPos, m_totalTime, m_fallStartY, m_yStart,
m_stateForce, m_stateForceVector, m_affectedByForces, m_jumpPadRelated, ...
```

Note the shape of the tail: **collision history, ring/pad "already used" sets,
and last-object pointers.** A snapshot that captured only position/velocity/
flags would restore a player who can re-use a ring it already consumed, or who
has forgotten which object it was standing on. That is the concrete form the
"almost correct" failure would have taken.

**`SavedObjectStateRef` — per-object motion state.** 9 members, and this is the
whole of what a moved object carries:

```
GameObject* m_gameObject
double      m_positionX          // <- the fields README already identifies
double      m_positionY          //    (+0x3b0 / +0x3b8), NOT the CCNode pos
float       m_rotationXOffset
float       m_rotationYOffset
float       m_addToCustomScaleX
float       m_addToCustomScaleY
float       m_unkFloat3
float       m_unkFloat4
```

This **independently corroborates** README's "Moving geometry: the move pipeline
does not write the CCNode position" — GD's own save-state reads the doubles, not
the node. It also names four fields beyond position (rotation offsets, scale
adds) that a position-only snapshot would drop.

**`SavedActiveObjectState`** (3 members): `m_gameObject`, `m_activatedByPlayer1`,
`m_activatedByPlayer2` — the per-object "has this been triggered" bit, per
player. **`SavedSpecialObjectState`** (3): `m_gameObject`, `m_animationID`.

**`EffectManagerState` — 19 containers**, including
`gd::vector<GroupCommandObject2> m_vectorGroupCommandObject2`. That is the live
in-flight move/rotate/scale commands, and it corroborates (h) the README result
that the live command container is `GJEffectManager::m_unkVector560`, which is
also `gd::vector<GroupCommandObject2>` by value. The rest are toggle/spawn/
collision/count/timer/pulse/opacity trigger actions and the item-count map.

`GroupCommandObject2` itself has **78 members** (h), consistent with the
offset table in README "The active-command struct" — `m_easingType`,
`m_easingRate`, `m_duration`, `m_deltaTime`, `m_current{X,Y}Offset`,
`m_delta{X,Y}`, `m_finished`, `m_disabled`, `m_lockedIn{X,Y}`,
`m_actionType{1,2}`, `m_actionValue{1,2}`, `m_deltaTimeInFloat`,
`m_alreadyUpdated` are all present under those exact names.

### 2.2 The functions, and they are real call sites

(d) Counted against a fresh `otool -arch arm64 -tV` of the sandbox binary
(1,785,817 lines). Rule 3 of the repo applies — `bl` **and** `b`, and a virtual
is evidenced by neither.

| symbol | m1 addr | `bl` | `b` | any ref | reading |
|---|---|---|---|---|---|
| `PlayLayer::createCheckpoint` | `0xa86d0` | 3 | 0 | 3 | live |
| `PlayLayer::loadFromCheckpoint` | `0xaa038` | 3 | 0 | 3 | live |
| `PlayerObject::loadFromCheckpoint` | `0x39107c` | 2 | 0 | 2 | live |
| `GJEffectManager::saveToState` | `0x285360` | 1 | 0 | 1 | live |
| `GJEffectManager::loadFromState` | `0x285738` | 1 | 0 | 1 | live |
| `GJBaseGameLayer::loadUpToPosition` | `0x120650` | 1 | 0 | 1 | live |
| `GJBaseGameLayer::resetLevelVariables` | `0x128b88` | 3 | 0 | 3 | live |
| `PlayLayer::resetLevel` | `0xaaf88` | 0 | 0 | 0 | **virtual — counts say nothing** |

(d) The two state calls are nested exactly where you would expect:

- `bl 0x285360` (`saveToState`) occurs at `0x1000a8a9c`, i.e. `createCheckpoint + 0x3cc`.
- `bl 0x285738` (`loadFromState`) occurs at `0x1000aa2a4`, i.e. `loadFromCheckpoint + 0x26c`.

So `createCheckpoint`/`loadFromCheckpoint` are a matched save/restore pair that
already carries the effect manager with it.

### 2.3 What this means for whoever resumes

The first thing to try is **not** a hand-built snapshot. It is:

```
CheckpointObject* cp = playLayer->createCheckpoint();   // at tick N
... run to tick M ...
playLayer->loadFromCheckpoint(cp);                      // rewind
```

and then run the divergence test in §4 against it. If it comes back
bit-identical, the whole hand-rolled capture problem evaporates. If it diverges,
the *first divergent field* tells you precisely what practice mode does not
bother to restore, and you have a small, evidenced patch list instead of a
500-field guess. Either outcome is a good day.

Track 2.1 in `TODO.md` already lists `createCheckpoint`/`loadFromCheckpoint` and
notes "nobody has touched them". That is still true, and it is now clear it is
the highest-value untouched thing in this area.

---

## 3. Known gaps in GD's own snapshot

These are the fields a checkpoint restore is **suspected not** to carry, and
each is a candidate first-divergence. All are (h)/inference, none measured.

1. **`PlayLayer::m_attemptTime`** — the repo's tick clock
   (`lround(m_attemptTime * 240)`). It is **not** a member of
   `CheckpointObject`. If practice-mode restore leaves the clock running, then
   after a rewind the tick index no longer matches the physics state, and every
   tick-keyed input placement is wrong. This is already open as backlog item 8
   ("Does `m_attemptTime` survive a checkpoint restore?") and it is now the
   *first* thing to instrument, not the eighth.

2. **`GJBaseGameLayer::m_extraDelta`** (double) — the fixed-step accumulator.
   README's decompiled step arithmetic (`acc = m_extraDelta + dt`,
   `m_extraDelta = acc - consumed`) makes it genuine physics state at any dt
   other than an exact multiple of 1/240. Not in `CheckpointObject`.

3. **RNG.** `GJBaseGameLayer` declares `m_randomSeed`, `m_unk32e0`,
   `m_replayRandSeed` (h). Whether any of them feeds physics is **unknown**;
   ~550 bit-identical null-input attempts (README) are consistent with either
   "physics consumes no RNG" or "it is reseeded per attempt", and those two are
   not distinguished by any measurement in this repo. Not in `CheckpointObject`.

4. **`m_currentStep`.** Do not treat it as a tick counter. README records it
   measured **constant 0** across all 91 clean attempts (iv). It sits in the
   replay/record block next to the seeds.

5. **The section grid.** `m_sections` is
   `gd::vector<gd::vector<gd::vector<GameObject*>*>*>` (h) — a vector of
   *pointers* to vectors of *pointers* to vectors. Any copy-assignment of the
   outer vector copies only the top-level pointers, so the buckets stay shared
   and are **not** snapshotted. README states the grid re-buckets during play.
   Whether `loadFromCheckpoint` rebuilds it (plausibly via `loadUpToPosition`)
   is unknown. Same shape applies to `m_sectionSizes`, `m_nonEffectObjects`,
   `m_nonEffectObjectsSizes`, `m_collisionBlockSections`,
   `m_collisionBlockSectionSizes`, `m_nonEffectObjectsFlags`.

6. **The button queue.** `m_queuedButtons` is
   `gd::vector<PlayerButtonCommand>` (h). An input queued but not yet drained at
   the rewind point is state. Not in `CheckpointObject`.

7. **Objects created or destroyed** between snapshot and restore (particles,
   effect instances) are not recoverable by any field copy. Level `GameObject`s
   are believed stable within an attempt; unverified.

---

## 4. The divergence test, unrun

Recorded so it is not re-designed. This is the test the brief specified and it
is the right one, because determinism is already established here (README:
~550 null-input attempts bit-identical; 1473 attempts / 631 sequences with
input, zero divergent).

> Snapshot at tick N, run to M, restore to N, run to M again. The second run
> must be **bit-identical to the first, tick for tick**. The first divergent
> tick and field names the subsystem you failed to capture.

Design points that were settled before the descope, and that are easy to get
wrong:

- **Run it at `dt = 1/240`.** `prepareMoveActions(float dt, bool intermediate)`
  takes an intra-frame flag, and `update`'s step loop passes a different value
  for the last step of a frame (h). At any larger dt, capture and restore can
  land at different positions within a frame, and physics would then differ for
  a reason that has nothing to do with snapshot completeness. `GDRL_EXP=1
  GDRL_DELTA_TICKS=1` already gives exactly one step per frame.
- **Capture and restore at the same point inside the step.** The established
  per-step hook is `GJEffectManager::prepareMoveActions`, before delegating —
  after that step's `processQueuedButtons` (`update+0x7b8`) and before the
  motion pipeline (`update+0x9f8`) (d, from README). Restoring at any other
  point re-runs a different fragment of the step.
- **Exclude the seek call.** `loadUpToPosition` also calls
  `prepareMoveActions` and also runs with a live `PlayLayer`, so
  `PlayLayer::get() != nullptr` does not identify the gameplay call.
  `telemetry.cpp` brackets its own `update` delegate with a flag; do the same.
- **Compare more than the player.** A per-tick digest should carry the named
  player scalars *and* a hash over every object's `m_positionX`/`m_positionY`,
  or moving-geometry divergence is invisible until it kills someone.
- **Snapshot ticks must exercise different subsystems**: on the ground, mid-air,
  across a portal, and inside a moving-platform section. Same-tick agreement on
  a flat cube run proves very little.
- **Report position error (units) and timing error (ticks) separately**, and
  suspect an off-by-one-frame before suspecting the physics.

---

## 5. Keeping the oracle out of the A observation path

The constraint (Benchmark B is an oracle, never the agent) was to be enforced
structurally, not by convention. The design, for the record:

- Snapshot code lives in its own translation unit, includes neither
  `gdrl_schema.hpp` nor `telemetry.hpp`, and never touches the shared segment.
  No new wire field, so `GDRL_SCHEMA_HASH` is unchanged and `env.py` cannot
  decode anything new.
- Output goes to the Geode log only. A future oracle transport must be a
  **separate** shm segment, never a new field on `GdrlObservation`.
- **Hard runtime refusal**: if `GDRL_ENV=1` and `GDRL_SNAPSHOT=1` are both set,
  refuse to arm the snapshot facility and log an error. Rewinding the game
  underneath an attached policy would feed the A agent observations from a
  timeline it did not play, which is a benchmark violation that would look like
  a physics bug.

None of this is implemented, because no snapshot code exists.

---

## 6. Current tree state

- `mod/src/` was **not modified** by this session. The mod is byte-identical in
  behaviour to before it.
- `mod/tools/gen_snapshot_fields.py` was added. It is not referenced by
  `mod/CMakeLists.txt` and nothing includes its output; it writes
  `mod/src/snapshot_fields.hpp` by default, which **does not currently exist**
  and which nothing would compile if it did.
- Build verified after the descope: `lipo -archs mod/build/gdrl.probe.dylib` →
  `x86_64 arm64`. That build also included another agent's in-flight
  `mod/src/viewport.cpp`, which compiled clean.
