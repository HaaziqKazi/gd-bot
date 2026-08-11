"""Objective B: forward-projected geometry.

A static occupancy map of a GD 2.2 level is a lie. A block at (X, Y) may be
mid-flight on a move trigger, spinning on a rotate trigger, or scheduled to
start moving the moment the player crosses an x a hundred units ahead. By the
time the player is standing where the map said the block was, the block is
somewhere else. The policy needs to see *where an object will be when the
player reaches it*, not where it is at observation time.

This module computes that projection. It is deliberately not a learned model:
GD's motion system is a small set of closed-form interpolators over state that
the mod can read directly, so the projection is arithmetic, and arithmetic that
is exact is worth more than arithmetic that is approximately learned.

--------------------------------------------------------------------------
WHERE THE NUMBERS COME FROM
--------------------------------------------------------------------------

Everything below was read out of the shipped arm64 slice of GD 2.2081
(``sandbox/Geometry Dash.app/Contents/MacOS/Geometry Dash``) and reconciled
against the Geode 2.2081 bindings. Addresses quoted as ``m1 0x...`` are broma
offsets; add 0x100000000 for the address in a ``otool -arch arm64 -tV`` dump.
Nothing here was inferred from a field name.

  (E1) The motion pipeline runs once per *physics step*, not per render frame.
       ``GJBaseGameLayer::update`` (m1 0x1229e8) contains a step loop whose
       latch is at +0x4b0 (``add w22,w22,#1; cmp w22,w20; b.ge <exit>``) over a
       body starting at +0x4bc, and every motion call sits inside it:

           GJEffectManager::updateSpawnTriggers(dt)     update+0x694
           GJEffectManager::updateTimers(dt, timeWarp)  update+0x9e8
           GJEffectManager::prepareMoveActions(dt, ..)  update+0x9f8
           processDynamicObjectActions(type=1, dt)      update+0xa44
           processTransformActions(visibleFrame)        update+0xa50
           processRotationActions()                     update+0xa58
           processDynamicObjectActions(type=0, dt)      update+0xa68
           processMoveActions()                         update+0xa70
           processPlayerFollowActions(dt)               update+0xa7c
           processAdvancedFollowAction(...)  [inlined loop over instances]
           processFollowActions()                       update+0xbf4
           processAreaActions(dt, visibleFrame)         update+0xc04
           GJEffectManager::postMoveActions()           update+0xc40

       The identical sequence appears outlined in
       ``GJBaseGameLayer::processMoveActionsStep`` (m1 0x118368), which is
       called only from ``loadUpToPosition`` -- i.e. it is the level-seek path,
       not the gameplay path. Gameplay runs the copy inlined into ``update``.

  (E2) The step count and per-step dt are computed at update+0x25c:

           step     = (timeWarp < 1) ? timeWarp/240 : 1/240      (as double)
           acc      = m_extraDelta + dt, round-tripped through float32
           n        = round(acc / step)
           consumed = step * n
           numSteps = max(round(consumed * 60 / max(timeWarp,1) * 4), 1)
           dtPerStep= consumed / numSteps

       At timeWarp == 1 this collapses to numSteps == n and
       dtPerStep == 1/240 exactly, which is the regime everything in the README
       was measured in. dtPerStep is what reaches prepareMoveActions (it is
       ``s10``, set by ``fcvt s10, d8`` at update+0x568). So trigger durations
       are in the same seconds as the 240 Hz tick clock, and one tick of
       lookahead is 1/240 of a trigger's duration. Behaviour at timeWarp != 1
       is UNVERIFIED -- see NEEDS-RUNTIME-VERIFICATION at the bottom.

  (E3) An active move/rotate/scale command is a ``GroupCommandObject2``. Its
       binding layout is not merely plausible, it is confirmed: the offsets
       used by ``GroupCommandObject2::step`` (m1 0x44722c) and
       ``::updateAction`` (m1 0x4472fc) land exactly on the binding's field
       order -- +0x0c m_easingType, +0x10 m_easingRate, +0x18 m_duration,
       +0x20 m_deltaTime, +0x30/+0x38 m_current{X,Y}Offset, +0x40/+0x48
       m_delta{X,Y}, +0x70 m_finished, +0x71 m_disabled, +0x77/+0x78
       m_lockedIn{X,Y}, +0x190/+0x194 m_actionType{1,2}, +0x198/+0x1a0
       m_actionValue{1,2}, +0x1ac m_deltaTimeInFloat, +0x1b0 m_alreadyUpdated.
       Field adjacency burned this repo once (m_currentStep); this time the
       semantics come from the instructions that read them.

  (E4) The interpolator, decompiled from ``updateAction``, is exactly:

           elapsed  = m_deltaTimeInFloat                       (float32 seconds)
           duration = max(m_duration, 2^-23)                   (0x3E80.. = 2^-23)
           p        = clamp(elapsed / duration, 0, 1)
           out      = value * GameToolbox::getEasedValue(p, easing, rate)

       and the object is displaced by ``out - m_currentXOffset`` this step, with
       m_currentXOffset then set to ``out``. So the *remaining* displacement to
       a future time is ``out(t) - m_currentXOffset``, using the live field
       rather than a recomputed out(0). That distinction matters after a pause
       or a checkpoint restore, where the two can disagree.

  (E5) ``GameToolbox::getEasedValue`` (m1 0x44f990) is cocos2d's easing family.
       Its 18-entry jump table was read out of __TEXT,__const at 0x1007287a4
       and each branch decoded; the libm stubs it calls resolve (via the
       indirect symbol table) to _powf, _sinf, _cosf and _exp2f. ``ease()``
       below reproduces it branch for branch. Two details that a from-memory
       implementation would get wrong: easingType 0 returns the input
       untouched *before* the rate is defaulted, and a rate <= 0 becomes 2.0.

  (E6) A trigger that has not fired yet is still schedulable, because its
       activation x is readable. ``EffectGameObject::spawnXPosition``
       (m1 0x1741b8) is:

           if (m_isSpawnTriggered || m_isTouchTriggered) return m_spawnXPosition;
           else                                          return getPosition().x;

       So an x-activated trigger fires when the player crosses the trigger
       object's own x. That is the hook the whole pending-trigger projection
       hangs off: the player's arrival tick at the trigger's x is computable,
       and therefore so is how long the trigger will have been running by the
       time the player arrives anywhere further along.

--------------------------------------------------------------------------
WHAT IS AND IS NOT PROJECTABLE
--------------------------------------------------------------------------

Exactly projectable (CERTAINTY_EXACT):
  * Any live GroupCommandObject2 with a finite duration and no lock flags --
    move, rotate, scale/transform. Closed form, (E4).
  * Any pending x-activated trigger of those kinds, once the player's arrival
    tick at its activation x is known.

Conditionally projectable (CERTAINTY_CONDITIONAL): the maths is exact but the
inputs depend on a future the policy has not chosen yet.
  * m_lockToPlayerX / m_lockToPlayerY / m_lockToCameraX / m_lockToCameraY:
    the command's offset tracks the player or camera, so projecting it means
    assuming a player trajectory.
  * Follow commands (target group mirrors another group scaled by
    m_followXMod / m_followYMod): exact if the followed group is itself
    exactly projectable, conditional otherwise. Not resolved recursively here.
  * Non-unit m_moveModX / m_moveModY. The multipliers are applied outside
    updateAction and this module does not model where.

Not projectable (CERTAINTY_UNKNOWN) -- these must be flagged, never silently
projected as static, because "confidently wrong" is worse than "known unknown":
  * Player-follow commands (createPlayerFollowCommand: delay, speed, maxSpeed
    chasing the player's y).
  * AdvancedFollowInstance / processAdvancedFollowAction -- a steering
    integrator, no closed form; would need step simulation.
  * Keyframe commands (createKeyframeCommand -> tk_spline).
  * Anything targeting a group reached through a random or sequence trigger
    (RandTriggerGameObject, SequenceTriggerGameObject, ChanceObject) or through
    spawn remapping (GJBaseGameLayer::m_spawnRemapTriggers,
    SpawnTriggerGameObject::addRemap) -- the target group ID is drawn at
    runtime from m_randomSeed and is not a property of the level.
  * Triggers whose activation is an event rather than an x: touch, collision,
    count, timer, item. spawnXPosition() returns a stored value for these that
    means "where it last fired", not "where it will fire".

Deliberately out of scope: EnterEffectInstance / processAreaEffects. Those are
camera-entry animations. Whether they perturb the collision rect or only the
rendered sprite is UNVERIFIED, and guessing either way would put a systematic
error into every observation.

--------------------------------------------------------------------------
HOW IT COMPOSES WITH conditioning.py
--------------------------------------------------------------------------

conditioning.py's ConditionedTrunk takes ``in_channels`` as a parameter and
assumes nothing about the channel layout beyond "spatial grid". This module
produces exactly that grid. The split of responsibilities is:

    trajectory.py  -> WHAT the geometry will be    (observation)
    conditioning.py-> WHAT the geometry MEANS      (regime, via FiLM)

They must not duplicate each other. Player speed appears in both, but for
different reasons and in different forms: conditioning.py carries it as a
bucket + residual because it changes how an input is interpreted; here it is
consumed as units-per-tick because it sets the lookahead horizon. Neither is
derivable from the other, and collapsing them would lose one of the two.

--------------------------------------------------------------------------
NEEDS-RUNTIME-VERIFICATION
--------------------------------------------------------------------------

Everything above is static analysis. GD was not run during this work. The
measurements that would close the gaps, in order of how much rests on them
(full protocol in README, "What still needs runtime verification"):

  1. Which GJEffectManager vector actually holds live GroupCommandObject2.
     The whole telemetry path depends on it and nothing else does.
  2. Per-tick advance for the four unmeasured speed buckets in UNITS_PER_TICK.
     Currently community values; a 4% error is a third of a tile at horizon.
  3. That GJEffectManager::prepareMoveActions really fires once per physics
     step. Static analysis says yes; processCommands is why that is not enough.
  4. m_deltaTimeInFloat against lround(m_attemptTime * 240), to confirm this
     module's time base end to end.
  5. Whether EnterEffectInstance perturbs the collision rect or only the
     sprite. Decides whether area effects belong in the projection.
  6. Which of ActionType.ANGULAR_A / ANGULAR_B is rotation and which is
     transform -- they run identical code.
  7. Timewarp behaviour at timeWarp != 1; see (E2).

Until (1) and (2) land, this module is exercised only on synthetic state by
test_trajectory.py, which is a test of the interpolator and the bookkeeping,
not of the numbers coming out of the game.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import IntEnum

import torch

# --------------------------------------------------------------------------
# Time base
# --------------------------------------------------------------------------

# GD's fixed physics step. See README, "Physics is fixed-step at 240Hz", and
# (E2) above for the derivation from update()'s own step-count arithmetic.
TICK_HZ = 240
TICK_SECONDS = 1.0 / TICK_HZ

# GroupCommandObject2::updateAction clamps the divisor to this before dividing
# (immediate 0x3E80000000000000 == 2^-23). A zero-duration trigger therefore
# does not divide by zero, it snaps to p == 1 on the first step.
MIN_DURATION = 2.0 ** -23


# --------------------------------------------------------------------------
# Easing -- a branch-for-branch reimplementation of GameToolbox::getEasedValue
# --------------------------------------------------------------------------

class EasingType(IntEnum):
    """Must stay numerically identical to Geode's ``enum class EasingType``.

    The values are also the indices GD uses into the jump table at
    __TEXT,__const 0x1007287a4, so a renumbering here silently mismaps every
    trigger in every level.
    """

    NONE = 0
    EASE_IN_OUT = 1
    EASE_IN = 2
    EASE_OUT = 3
    ELASTIC_IN_OUT = 4
    ELASTIC_IN = 5
    ELASTIC_OUT = 6
    BOUNCE_IN_OUT = 7
    BOUNCE_IN = 8
    BOUNCE_OUT = 9
    EXPONENTIAL_IN_OUT = 10
    EXPONENTIAL_IN = 11
    EXPONENTIAL_OUT = 12
    SINE_IN_OUT = 13
    SINE_IN = 14
    SINE_OUT = 15
    BACK_IN_OUT = 16
    BACK_IN = 17
    BACK_OUT = 18


# Back-easing overshoot. GD materialises 0x402CE6B0 == 2.70158f and
# 0xBFD9CD60 == -1.70158f directly; the InOut variant uses the x1.525 pair
# 0x406612FF == 3.5949097f and 0xC02612FF == -2.5949097f.
_BACK_S = 1.70158
_BACK_S_INOUT = 2.5949097


def _bounce_time(t: float) -> float:
    """GameToolbox::bounceTime (m1 0x44f8a8), thresholds compared as doubles.

    The four cutoffs are the exact doubles 4/11, 8/11 and 10/11 that GD
    materialises via movk sequences, not the float32 approximations -- the
    comparison is done after ``fcvt d1, s0``.
    """
    if t < 4.0 / 11.0:
        return 7.5625 * t * t
    if t < 8.0 / 11.0:
        t -= 6.0 / 11.0                       # 0xBF0BA2E9 == -0.5454545f
        return 7.5625 * t * t + 0.75
    if t < 10.0 / 11.0:
        t -= 9.0 / 11.0                       # 0xBF51745D == -0.8181818f
        return 7.5625 * t * t + 0.9375
    t -= 21.0 / 22.0                          # 0xBF745D17 == -0.9545455f
    return 7.5625 * t * t + 0.984375          # 0x3F7C0000


def ease(t: float, easing: int, rate: float) -> float:
    """Reproduce ``GameToolbox::getEasedValue(t, easing, rate)`` exactly.

    Order of operations matters and is taken from the prologue at m1 0x44f990:

        if (easingType == 0) return t;                 // cbz w0, epilogue
        rate = (rate > 0.0f) ? rate : 2.0f;            // fcsel ..., hi
        if ((unsigned)(easingType - 1) > 17) return t;  // out-of-range passthrough

    The rate default is applied *after* the type-0 early out, so
    ``ease(t, NONE, 0.0)`` is the identity rather than a linear-with-rate-2
    curve. And the range check is unsigned, so a negative easingType falls
    through to passthrough rather than indexing off the front of the table.
    """
    if easing == 0:
        return t
    if rate <= 0.0:
        rate = 2.0
    if not (0 <= easing - 1 <= 17):
        return t

    e = EasingType(easing)

    if e is EasingType.EASE_IN:
        return math.pow(t, rate)
    if e is EasingType.EASE_OUT:
        return math.pow(t, 1.0 / rate)
    if e is EasingType.EASE_IN_OUT:
        t2 = t + t
        if t2 < 1.0:
            return 0.5 * math.pow(t2, rate)
        return 1.0 - 0.5 * math.pow(2.0 - t2, rate)

    if e is EasingType.SINE_IN:
        return 1.0 - math.cos(t * math.pi / 2.0)
    if e is EasingType.SINE_OUT:
        return math.sin(t * math.pi / 2.0)
    if e is EasingType.SINE_IN_OUT:
        return -0.5 * (math.cos(math.pi * t) - 1.0)

    # The exponential family does not pin its endpoints, and that is GD's
    # behaviour rather than an approximation introduced here:
    #
    #   ExponentialIn(1)    = 2^0 - 0.001    = 0.999
    #   ExponentialInOut(0) = 0.5 * 2^-10    = 0.00048828125
    #   ExponentialInOut(1) = 0.5*(2-2^-10)  = 0.99951171875
    #
    # There is a zero guard on ExponentialIn (fcmp/b.ne at m1 0x44fd58) and a
    # one guard on ExponentialOut (m1 0x44fa54), but none at all in the InOut
    # branch (m1 0x44fb10). The -0.001 is a materialised constant
    # (0xBA83126F), not rounding. Consequence: a move trigger on
    # ExponentialIn stops 0.1% short of its target for good, which on a
    # 600-unit move is 0.6 units of standing error. Small, but it is real, it
    # persists after the trigger finishes, and "correcting" it here would put
    # this module permanently out of step with the game.
    if e is EasingType.EXPONENTIAL_IN:
        return t if t == 0.0 else math.pow(2.0, 10.0 * (t - 1.0)) - 0.001
    if e is EasingType.EXPONENTIAL_OUT:
        return t if t == 1.0 else 1.0 - math.pow(2.0, -10.0 * t)
    if e is EasingType.EXPONENTIAL_IN_OUT:
        t2 = t + t
        if t2 < 1.0:
            return 0.5 * math.pow(2.0, 10.0 * (t2 - 1.0))
        return 0.5 * (2.0 - math.pow(2.0, -10.0 * (t2 - 1.0)))

    if e is EasingType.ELASTIC_IN:
        if t == 0.0 or t == 1.0:
            return t
        s = rate / 4.0
        t -= 1.0
        return -math.pow(2.0, 10.0 * t) * math.sin((t - s) * 2.0 * math.pi / rate)
    if e is EasingType.ELASTIC_OUT:
        if t == 0.0 or t == 1.0:
            return t
        s = rate / 4.0
        return math.pow(2.0, -10.0 * t) * math.sin((t - s) * 2.0 * math.pi / rate) + 1.0
    if e is EasingType.ELASTIC_IN_OUT:
        if t == 0.0 or t == 1.0:
            return t
        s = rate / 4.0
        t = t * 2.0 - 1.0
        if t < 0.0:
            return -0.5 * math.pow(2.0, 10.0 * t) * math.sin((t - s) * 2.0 * math.pi / rate)
        return math.pow(2.0, -10.0 * t) * math.sin((t - s) * 2.0 * math.pi / rate) * 0.5 + 1.0

    if e is EasingType.BOUNCE_OUT:
        return _bounce_time(t)
    if e is EasingType.BOUNCE_IN:
        return 1.0 - _bounce_time(1.0 - t)
    if e is EasingType.BOUNCE_IN_OUT:
        if t < 0.5:
            return (1.0 - _bounce_time(1.0 - t * 2.0)) * 0.5
        return _bounce_time(t * 2.0 - 1.0) * 0.5 + 0.5

    if e is EasingType.BACK_IN:
        return t * t * ((_BACK_S + 1.0) * t - _BACK_S)
    if e is EasingType.BACK_OUT:
        t -= 1.0
        return t * t * ((_BACK_S + 1.0) * t + _BACK_S) + 1.0
    if e is EasingType.BACK_IN_OUT:
        t2 = t + t
        if t2 < 1.0:
            return 0.5 * (t2 * t2 * ((_BACK_S_INOUT + 1.0) * t2 - _BACK_S_INOUT))
        t2 -= 2.0
        return 0.5 * (t2 * t2 * ((_BACK_S_INOUT + 1.0) * t2 + _BACK_S_INOUT)) + 1.0

    return t  # unreachable; kept so a future enum addition degrades to static


# --------------------------------------------------------------------------
# Player horizon
# --------------------------------------------------------------------------

# Horizontal advance per physics tick, indexed by speed bucket, matching
# conditioning.SPEED_MULTIPLIERS = (0.7, 0.9, 1.1, 1.3, 1.6).
#
# ONLY INDEX 1 IS MEASURED. 1.298250437 units/tick at m_playerSpeed 0.90 is
# from this repo's own per-tick data (README, "Physics is fixed-step at
# 240Hz"), and 1.298250437 * 240 = 311.58 units/s.
#
# The other four are the widely quoted community values (251.16, 387.42,
# 468.00, 576.00 units/s) divided by 240. They are NOT verified here and they
# are NOT proportional to m_playerSpeed -- the ratio 1.6/0.9 = 1.778 does not
# equal 576.00/311.58 = 1.849, so deriving them from the multiplier would be
# wrong by 4%. Over a 400-tick lookahead a 4% speed error is ~20 units, which
# is a third of a tile: enough to project a moving spike onto the wrong side of
# the player. Measure them before trusting them.
UNITS_PER_TICK = (
    251.16 / TICK_HZ,   # 0.5x   UNVERIFIED
    311.58 / TICK_HZ,   # 1x     measured: 1.298250437
    387.42 / TICK_HZ,   # 2x     UNVERIFIED
    468.00 / TICK_HZ,   # 3x     UNVERIFIED
    576.00 / TICK_HZ,   # 4x     UNVERIFIED
)
SPEED_VERIFIED = (False, True, False, False, False)


@dataclass(frozen=True)
class SpeedSegment:
    """One constant-speed stretch of level, starting at ``start_x``.

    Built from the speed portals the mod can see ahead of the player. Kept
    piecewise rather than collapsed to a single average because the horizon is
    an *integral*, and averaging speed over a portal boundary gets the arrival
    time at every x past the boundary wrong.
    """

    start_x: float
    bucket: int

    @property
    def units_per_tick(self) -> float:
        return UNITS_PER_TICK[self.bucket]


class SpeedProfile:
    """Player x as a function of tick, and its inverse.

    The inverse -- "how many ticks until the player is at x" -- is the horizon
    every projection in this module is expressed against. Working in ticks
    rather than seconds is not cosmetic: the tick is the only clock in this
    system that is exactly reproducible (README, "The input-placement clock"),
    and a projection keyed to wall-clock seconds would drift against the very
    simulation it is predicting.
    """

    def __init__(self, player_x: float, segments: list[SpeedSegment] | None = None):
        if segments is None:
            segments = [SpeedSegment(start_x=player_x, bucket=1)]
        if not segments:
            raise ValueError("SpeedProfile needs at least one segment")
        segs = sorted(segments, key=lambda s: s.start_x)
        if segs[0].start_x > player_x:
            raise ValueError(
                f"first segment starts at {segs[0].start_x} which is ahead of the "
                f"player at {player_x}; the segment covering the player is required"
            )
        self.player_x = float(player_x)
        self.segments = segs

    def _bucket_at(self, x: float) -> int:
        bucket = self.segments[0].bucket
        for seg in self.segments:
            if seg.start_x > x:
                break
            bucket = seg.bucket
        return bucket

    def ticks_to_reach(self, x: float) -> float:
        """Ticks until the player's x equals ``x``. Negative if already past.

        Fractional on purpose. Rounding to an integer tick here would quantise
        every arrival time to the same 1.3-unit grid the player moves on, which
        is exactly the resolution the projection is trying to resolve below.
        """
        if x <= self.player_x:
            return (x - self.player_x) / UNITS_PER_TICK[self._bucket_at(self.player_x)]

        ticks = 0.0
        cursor = self.player_x
        for i, seg in enumerate(self.segments):
            seg_start = max(seg.start_x, self.player_x)
            if seg_start > x:
                break
            seg_end = self.segments[i + 1].start_x if i + 1 < len(self.segments) else math.inf
            stretch_end = min(seg_end, x)
            if stretch_end <= cursor:
                continue
            ticks += (stretch_end - cursor) / seg.units_per_tick
            cursor = stretch_end
            if cursor >= x:
                break
        if cursor < x:                       # past the last known portal
            ticks += (x - cursor) / self.segments[-1].units_per_tick
        return ticks

    def x_at_tick(self, tick: float) -> float:
        """Forward map, for tests and for sanity-checking ticks_to_reach."""
        if tick <= 0:
            return self.player_x + tick * UNITS_PER_TICK[self._bucket_at(self.player_x)]
        remaining = tick
        cursor = self.player_x
        for i, seg in enumerate(self.segments):
            if seg.start_x > cursor and seg.start_x > self.player_x:
                pass
            seg_end = self.segments[i + 1].start_x if i + 1 < len(self.segments) else math.inf
            if seg_end <= cursor:
                continue
            span_ticks = (seg_end - cursor) / seg.units_per_tick
            if remaining <= span_ticks:
                return cursor + remaining * seg.units_per_tick
            remaining -= span_ticks
            cursor = seg_end
        return cursor + remaining * self.segments[-1].units_per_tick

    def certainty(self, x: float) -> float:
        """1.0 while the arrival time rests only on measured speeds."""
        bucket = self._bucket_at(x)
        return 1.0 if SPEED_VERIFIED[bucket] else CERTAINTY_CONDITIONAL


# --------------------------------------------------------------------------
# Certainty grades
# --------------------------------------------------------------------------

# The projection emits a per-object certainty rather than dropping uncertain
# objects, because the two failure modes are not symmetric. Dropping a moving
# hazard makes the observation claim empty space where a hazard may be;
# projecting it as static makes the observation claim a hazard where there is
# none. Both are lies. A channel the policy can learn to distrust is the only
# option that does not require the environment to guess.
CERTAINTY_EXACT = 1.0        # closed form over state read live off the process
CERTAINTY_CONDITIONAL = 0.5  # exact maths, inputs depend on an unchosen future
CERTAINTY_UNKNOWN = 0.0      # no closed form; position at arrival is a guess


# --------------------------------------------------------------------------
# Live commands (GroupCommandObject2 mirror)
# --------------------------------------------------------------------------

class ActionType(IntEnum):
    """The ``type`` selector in GroupCommandObject2::updateAction.

    Derived from the switch at m1 0x447374, not from a header: type 1 writes
    m_currentXOffset (+0x30) / m_deltaX (+0x40), type 2 writes
    m_currentYOffset (+0x38) / m_deltaY (+0x48), and types 3 and 4 share the
    single m_currentRotateOrTransformValue slot (+0x90). Which of 3 and 4 is
    rotation and which is transform is UNVERIFIED -- they run identical code,
    so this module treats them as one channel and lets the caller label it.
    """

    NONE = 0
    OFFSET_X = 1
    OFFSET_Y = 2
    ANGULAR_A = 3
    ANGULAR_B = 4


@dataclass
class GroupCommand:
    """One live GroupCommandObject2, as the mod should hand it over.

    Field names mirror the binding so that a mismatch between this and the C++
    side is a grep away. Only the fields the interpolator actually reads are
    carried; the struct has 60-odd members and copying all of them across the
    wire every tick would cost more than the projection saves.
    """

    target_group_id: int
    duration: float                       # m_duration, seconds
    delta_time_in_float: float            # m_deltaTimeInFloat, elapsed seconds
    easing_type: int = int(EasingType.NONE)   # m_easingType
    easing_rate: float = 2.0              # m_easingRate

    action_type_1: int = int(ActionType.NONE)   # m_actionType1
    action_value_1: float = 0.0                 # m_actionValue1
    action_type_2: int = int(ActionType.NONE)   # m_actionType2
    action_value_2: float = 0.0                 # m_actionValue2

    current_x_offset: float = 0.0         # m_currentXOffset
    current_y_offset: float = 0.0         # m_currentYOffset
    current_angular: float = 0.0          # m_currentRotateOrTransformValue

    finished: bool = False                # m_finished
    disabled: bool = False                # m_disabled
    locked_in_x: bool = False             # m_lockedInX
    locked_in_y: bool = False             # m_lockedInY
    lock_to_player_x: bool = False        # m_lockToPlayerX
    lock_to_player_y: bool = False        # m_lockToPlayerY
    lock_to_camera_x: bool = False        # m_lockToCameraX
    lock_to_camera_y: bool = False        # m_lockToCameraY
    move_mod_x: float = 1.0               # m_moveModX
    move_mod_y: float = 1.0               # m_moveModY

    # Not a GD field. Set by the mod when it knows the command came from a
    # source this module cannot model (player-follow, advanced follow,
    # keyframe/spline). Explicit rather than inferred from the field pattern,
    # because "all the lock flags are false" is not evidence of "it is a plain
    # move".
    unmodellable: bool = False

    def value_at(self, action_type: int, extra_ticks: float) -> float:
        """The interpolator output ``extra_ticks`` from now, per (E4).

        ``extra_ticks`` may be negative; p is clamped to [0, 1] the same way GD
        clamps it, so asking about the past before the command started returns
        the start value rather than extrapolating backwards.
        """
        if action_type == int(ActionType.NONE):
            return 0.0
        if self.action_type_1 == action_type:
            value = self.action_value_1
        elif self.action_type_2 == action_type:
            value = self.action_value_2
        else:
            return 0.0

        if self.disabled:
            return self._current(action_type)

        elapsed = self.delta_time_in_float + extra_ticks * TICK_SECONDS
        p = elapsed / max(self.duration, MIN_DURATION)
        p = 0.0 if p < 0.0 else (1.0 if p > 1.0 else p)
        return value * ease(p, self.easing_type, self.easing_rate)

    def _current(self, action_type: int) -> float:
        if action_type == int(ActionType.OFFSET_X):
            return self.current_x_offset
        if action_type == int(ActionType.OFFSET_Y):
            return self.current_y_offset
        return self.current_angular

    def delta_over(self, extra_ticks: float) -> tuple[float, float, float]:
        """(dx, dy, dangle) still to be applied between now and ``extra_ticks``.

        Measured against the live m_current*Offset rather than against a
        recomputed value_at(0). They should agree, and when they do not, the
        live field is the one GD will subtract from on the next step.

        m_lockedInX / m_lockedInY suppress the corresponding action outright
        (``ldrb w8, [x19, #0x77]; tbnz`` at the head of the type-1 branch), so
        a locked axis contributes nothing rather than contributing its eased
        value.
        """
        if self.disabled:
            return 0.0, 0.0, 0.0

        dx = 0.0
        if not self.locked_in_x:
            dx = self.value_at(int(ActionType.OFFSET_X), extra_ticks) - self.current_x_offset
        dy = 0.0
        if not self.locked_in_y:
            dy = self.value_at(int(ActionType.OFFSET_Y), extra_ticks) - self.current_y_offset

        ang = 0.0
        for at in (int(ActionType.ANGULAR_A), int(ActionType.ANGULAR_B)):
            if at in (self.action_type_1, self.action_type_2):
                ang = self.value_at(at, extra_ticks) - self.current_angular
                break
        return dx, dy, ang

    @property
    def certainty(self) -> float:
        if self.unmodellable:
            return CERTAINTY_UNKNOWN
        if (self.lock_to_player_x or self.lock_to_player_y
                or self.lock_to_camera_x or self.lock_to_camera_y):
            return CERTAINTY_CONDITIONAL
        if self.move_mod_x != 1.0 or self.move_mod_y != 1.0:
            return CERTAINTY_CONDITIONAL
        return CERTAINTY_EXACT


# --------------------------------------------------------------------------
# Pending triggers (not yet crossed)
# --------------------------------------------------------------------------

@dataclass
class PendingTrigger:
    """An EffectGameObject that will fire when the player crosses its x.

    This is the half of the problem that reading live state cannot solve: at
    observation time no GroupCommandObject2 exists for it yet, so the only
    trace of it is the trigger object sitting in the level. Its configuration
    is fully readable off EffectGameObject (m_moveOffset, m_duration,
    m_easingType, m_easingRate, m_targetGroupID), and (E6) gives the x at
    which it will fire.
    """

    activation_x: float                     # EffectGameObject::spawnXPosition()
    target_group_id: int                    # m_targetGroupID
    duration: float                         # m_duration
    easing_type: int = int(EasingType.NONE)  # m_easingType
    easing_rate: float = 2.0                # m_easingRate

    action_type_1: int = int(ActionType.NONE)
    action_value_1: float = 0.0
    action_type_2: int = int(ActionType.NONE)
    action_value_2: float = 0.0

    # Set by the mod for triggers whose real target group is only decided at
    # fire time: RandTriggerGameObject / SequenceTriggerGameObject outcomes and
    # anything reached through GJBaseGameLayer::m_spawnRemapTriggers.
    target_is_remapped: bool = False
    # Spawn-trigger delay chain: seconds between the crossing and the effect.
    spawn_delay: float = 0.0

    def as_command_at(self, ticks_since_fire: float) -> GroupCommand:
        """The GroupCommand this trigger will have produced, ``ticks_since_fire``
        after it fires. Elapsed is clamped at zero -- a trigger cannot have run
        for a negative time, and letting it go negative would make value_at
        clamp to p = 0 and silently look identical to "has not fired".
        """
        elapsed = max(0.0, ticks_since_fire * TICK_SECONDS - self.spawn_delay)
        return GroupCommand(
            target_group_id=self.target_group_id,
            duration=self.duration,
            delta_time_in_float=elapsed,
            easing_type=self.easing_type,
            easing_rate=self.easing_rate,
            action_type_1=self.action_type_1,
            action_value_1=self.action_value_1,
            action_type_2=self.action_type_2,
            action_value_2=self.action_value_2,
            # A pending trigger has applied nothing yet, so the "already
            # applied" baseline is zero rather than the live field.
            current_x_offset=0.0,
            current_y_offset=0.0,
            current_angular=0.0,
        )

    @property
    def certainty(self) -> float:
        return CERTAINTY_UNKNOWN if self.target_is_remapped else CERTAINTY_EXACT


# --------------------------------------------------------------------------
# Objects
# --------------------------------------------------------------------------

class ObjectKind(IntEnum):
    """Coarse gameplay role, collapsed from GameObjectType.

    GameObjectType has 46 values, most of which differ only in which portal
    they are. The policy needs "will this kill me / can I stand on it / does it
    change my regime", and the regime change itself is already conditioned on
    by conditioning.py. Four buckets keeps the channel count down without
    losing anything FiLM is not already handling.
    """

    HAZARD = 0        # GameObjectType::Hazard, Slope-as-hazard
    SOLID = 1         # Solid, Breakable, Slope, CollisionObject
    INTERACTIVE = 2   # pads, rings, portals, collectibles
    OTHER = 3         # Decoration, Modifier, Special


NUM_KINDS = len(ObjectKind)


@dataclass
class ObjectSnapshot:
    """One GameObject as of the observation tick.

    x/y are m_positionX / m_positionY (doubles in GD, so no precision is lost
    on the wire at float64). half_w / half_h come from the object rect rather
    than from m_width / m_height, because m_width/m_height are the untransformed
    sprite extents and the rect is what collision actually uses.
    """

    object_id: int
    kind: ObjectKind
    x: float
    y: float
    half_w: float
    half_h: float
    rotation: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    groups: tuple[int, ...] = ()


@dataclass
class ProjectedObject:
    """An ObjectSnapshot advanced to the tick the player reaches it."""

    source: ObjectSnapshot
    arrival_tick: float
    x: float
    y: float
    rotation: float
    vx: float = 0.0          # units per tick at arrival
    vy: float = 0.0
    certainty: float = CERTAINTY_EXACT

    @property
    def moved(self) -> float:
        return math.hypot(self.x - self.source.x, self.y - self.source.y)


# --------------------------------------------------------------------------
# The projection
# --------------------------------------------------------------------------

_VELOCITY_PROBE_TICKS = 1.0


class ForwardProjector:
    """Advance every object to the tick the player will reach it.

    The fixpoint. An object's arrival tick depends on the object's x, and the
    object's x depends on the arrival tick, because the object is moving. GD
    itself never solves this -- it just steps -- so there is no ground truth to
    match; the honest thing is a small fixed number of iterations and a
    documented residual.

    Two iterations are used. One is not enough: a block moving toward the
    player at half the player's speed converges only after the second pass. Ten
    would be wasted -- the map is a contraction whenever the object's x-speed is
    below the player's, which it is for essentially all level geometry, and the
    residual after two passes is (v_obj/v_player)^2 of the first-pass error.
    Objects moving *faster* than the player horizontally do not converge at all;
    those are flagged rather than iterated, since more iterations would just
    produce a more confidently wrong number.
    """

    ITERATIONS = 2

    def __init__(
        self,
        speed: SpeedProfile,
        horizon_ticks: int = 240,
    ):
        if horizon_ticks <= 0:
            raise ValueError("horizon_ticks must be positive")
        self.speed = speed
        self.horizon_ticks = horizon_ticks

    # -- group -> effects ------------------------------------------------

    @staticmethod
    def _index_commands(commands: list[GroupCommand]) -> dict[int, list[GroupCommand]]:
        by_group: dict[int, list[GroupCommand]] = {}
        for c in commands:
            if c.disabled:
                continue
            by_group.setdefault(c.target_group_id, []).append(c)
        return by_group

    @staticmethod
    def _index_pending(pending: list[PendingTrigger]) -> dict[int, list[PendingTrigger]]:
        by_group: dict[int, list[PendingTrigger]] = {}
        for p in pending:
            by_group.setdefault(p.target_group_id, []).append(p)
        return by_group

    def _transform_at(
        self,
        obj: ObjectSnapshot,
        tick: float,
        by_group_cmd: dict[int, list[GroupCommand]],
        by_group_pending: dict[int, list[PendingTrigger]],
    ) -> tuple[float, float, float, float]:
        """(x, y, rotation, certainty) for ``obj`` at ``tick`` from now.

        Contributions from every command targeting every group the object is in
        are summed. That is what GD does -- moveObjects is called once per
        command and each call adds its own delta to the object position, so two
        move triggers on the same group compose additively rather than the last
        one winning.
        """
        dx = dy = drot = 0.0
        certainty = CERTAINTY_EXACT

        for gid in obj.groups:
            for cmd in by_group_cmd.get(gid, ()):
                cdx, cdy, cdr = cmd.delta_over(tick)
                if cmd.certainty == CERTAINTY_UNKNOWN:
                    # Do not move it, but do record that it will move. A guessed
                    # position is worse than a flagged static one.
                    certainty = min(certainty, CERTAINTY_UNKNOWN)
                    continue
                dx += cdx
                dy += cdy
                drot += cdr
                certainty = min(certainty, cmd.certainty)

            for trig in by_group_pending.get(gid, ()):
                fire_tick = self.speed.ticks_to_reach(trig.activation_x)
                if fire_tick > tick:
                    continue                      # not fired by then
                if trig.certainty == CERTAINTY_UNKNOWN:
                    certainty = min(certainty, CERTAINTY_UNKNOWN)
                    continue
                cmd = trig.as_command_at(tick - fire_tick)
                cdx, cdy, cdr = cmd.delta_over(0.0)
                dx += cdx
                dy += cdy
                drot += cdr
                certainty = min(certainty, trig.certainty,
                                self.speed.certainty(trig.activation_x))

        return obj.x + dx, obj.y + dy, obj.rotation + drot, certainty

    def project(
        self,
        objects: list[ObjectSnapshot],
        commands: list[GroupCommand] | None = None,
        pending: list[PendingTrigger] | None = None,
    ) -> list[ProjectedObject]:
        by_cmd = self._index_commands(commands or [])
        by_pending = self._index_pending(pending or [])

        out: list[ProjectedObject] = []
        for obj in objects:
            tick = self.speed.ticks_to_reach(obj.x)
            x = y = rot = 0.0
            certainty = CERTAINTY_EXACT

            for _ in range(self.ITERATIONS):
                tick = max(0.0, min(float(self.horizon_ticks), tick))
                x, y, rot, certainty = self._transform_at(obj, tick, by_cmd, by_pending)
                tick = self.speed.ticks_to_reach(x)

            tick = max(0.0, min(float(self.horizon_ticks), tick))
            x, y, rot, certainty = self._transform_at(obj, tick, by_cmd, by_pending)

            # Velocity at arrival, by finite difference over one tick. Sampling
            # rather than differentiating the easing analytically: the eased
            # curve is piecewise and some branches (bounce, elastic) are not
            # smooth, and the policy wants "where is it going next tick", which
            # is the difference quotient, not the derivative.
            x2, y2, _, _ = self._transform_at(
                obj, tick + _VELOCITY_PROBE_TICKS, by_cmd, by_pending
            )
            vx = (x2 - x) / _VELOCITY_PROBE_TICKS
            vy = (y2 - y) / _VELOCITY_PROBE_TICKS

            # Non-convergence guard, per the class docstring: if the object is
            # outrunning the player horizontally the fixpoint does not contract
            # and the arrival tick is not meaningful.
            player_speed = UNITS_PER_TICK[self.speed._bucket_at(obj.x)]
            if abs(vx) >= player_speed:
                certainty = CERTAINTY_UNKNOWN

            out.append(
                ProjectedObject(
                    source=obj,
                    arrival_tick=tick,
                    x=x,
                    y=y,
                    rotation=rot,
                    vx=vx,
                    vy=vy,
                    certainty=certainty,
                )
            )
        return out


# --------------------------------------------------------------------------
# Rasterisation into the observation window
# --------------------------------------------------------------------------

class TrajectoryRaster:
    """Projected objects -> the (C, H, W) grid ConditionedTrunk consumes.

    Channel layout:

        0 .. NUM_KINDS-1                 occupancy NOW,      per kind
        NUM_KINDS .. 2*NUM_KINDS-1       occupancy AT ARRIVAL, per kind
        2*NUM_KINDS                      vx at arrival (units/tick)
        2*NUM_KINDS + 1                  vy at arrival
        2*NUM_KINDS + 2                  certainty

    Both the now-map and the arrival-map are emitted, not just the arrival-map.
    They are different questions and the policy needs both: the now-map is what
    the player will collide with in the next few ticks, the arrival-map is what
    it will collide with when it gets there. For a static level the two are
    identical and the network can learn to ignore one; for a moving level their
    *difference* is the signal, and a difference is only available if both
    terms are present.

    The certainty channel is written at the arrival footprint, so "this cell
    might contain a hazard I could not project" is spatially located rather
    than being a single scalar the trunk would have to broadcast itself.
    """

    NUM_CHANNELS = 2 * NUM_KINDS + 3
    VX_CHANNEL = 2 * NUM_KINDS
    VY_CHANNEL = 2 * NUM_KINDS + 1
    CERTAINTY_CHANNEL = 2 * NUM_KINDS + 2

    def __init__(
        self,
        height: int = 32,
        width: int = 48,
        cell_size: float = 30.0,
        player_col: int | None = None,
    ):
        if height <= 0 or width <= 0:
            raise ValueError("window must be non-empty")
        if cell_size <= 0:
            raise ValueError("cell_size must be positive")
        self.height = height
        self.width = width
        # 30 units is one GD tile. Keeping the cell exactly one tile means a
        # block occupies exactly one cell, so quantisation never merges two
        # blocks or splits one -- which is the failure mode that makes a
        # sub-tile grid look higher resolution while being strictly worse.
        self.cell_size = cell_size
        # The player sits a quarter of the way in, so three quarters of the
        # window is lookahead. Behind matters only for the few ticks it takes
        # to clear an object; ahead is where the decisions are.
        self.player_col = width // 4 if player_col is None else player_col

    def render(
        self,
        projected: list[ProjectedObject],
        player_x: float,
        player_y: float,
        device: torch.device | str = "cpu",
        dtype: torch.dtype = torch.float32,
    ) -> torch.Tensor:
        """One observation, shape (NUM_CHANNELS, height, width)."""
        grid = torch.zeros(
            (self.NUM_CHANNELS, self.height, self.width), device=device, dtype=dtype
        )
        if not projected:
            return grid

        origin_x = player_x - self.player_col * self.cell_size
        origin_y = player_y - (self.height // 2) * self.cell_size

        def stamp(channel: int, cx: float, cy: float, hw: float, hh: float,
                  value: float, signed: bool = False) -> None:
            """Write ``value`` over the footprint of a box.

            Two combine rules, because occupancy and velocity overlap
            differently. Occupancy takes the max, so two objects sharing a cell
            still read 1. Velocity takes the *larger magnitude*, keeping the
            sign: a plain max would silently erase every downward-moving object
            that shares a cell with a zero, which is every downward-moving
            object in a freshly zeroed grid. That bug renders a falling hazard
            as stationary -- the exact class of error this module exists to
            remove -- and it is invisible unless something asserts on the sign.
            """
            c0 = int(math.floor((cx - hw - origin_x) / self.cell_size))
            c1 = int(math.floor((cx + hw - origin_x) / self.cell_size))
            r0 = int(math.floor((cy - hh - origin_y) / self.cell_size))
            r1 = int(math.floor((cy + hh - origin_y) / self.cell_size))
            c0 = max(c0, 0); r0 = max(r0, 0)
            c1 = min(c1, self.width - 1); r1 = min(r1, self.height - 1)
            if c0 > c1 or r0 > r1:
                return
            view = grid[channel, r0:r1 + 1, c0:c1 + 1]
            if signed:
                view.copy_(torch.where(view.abs() >= abs(value), view,
                                       torch.full_like(view, value)))
            else:
                torch.maximum(view, torch.as_tensor(value, dtype=dtype, device=device),
                              out=view)

        for p in projected:
            src = p.source
            k = int(src.kind)
            hw = src.half_w * abs(src.scale_x)
            hh = src.half_h * abs(src.scale_y)

            stamp(k, src.x, src.y, hw, hh, 1.0)
            stamp(NUM_KINDS + k, p.x, p.y, hw, hh, 1.0)
            stamp(self.VX_CHANNEL, p.x, p.y, hw, hh, p.vx, signed=True)
            stamp(self.VY_CHANNEL, p.x, p.y, hw, hh, p.vy, signed=True)
            # Certainty is stored as 1 - certainty so that an all-zero channel
            # means "everything is exactly known", which is the correct default
            # for a static level and for a zero-filled tensor.
            stamp(self.CERTAINTY_CHANNEL, p.x, p.y, hw, hh, 1.0 - p.certainty)

        return grid

    def render_batch(
        self,
        projected: list[list[ProjectedObject]],
        player_xy: list[tuple[float, float]],
        device: torch.device | str = "cpu",
        dtype: torch.dtype = torch.float32,
    ) -> torch.Tensor:
        """Stack per-sample renders into (B, C, H, W).

        The length check is not defensive clutter. conditioning.py's
        ConditionedTrunk.forward silently broadcasts when the obs batch and the
        regime batch disagree, which turns a wiring bug into a model that
        trains on the wrong conditioning and converges to something plausible.
        That failure is not detectable downstream, so it is caught here.
        """
        if len(projected) != len(player_xy):
            raise ValueError(
                f"batch mismatch: {len(projected)} projections vs "
                f"{len(player_xy)} player positions"
            )
        if not projected:
            return torch.zeros(
                (0, self.NUM_CHANNELS, self.height, self.width),
                device=device, dtype=dtype,
            )
        return torch.stack(
            [
                self.render(p, x, y, device=device, dtype=dtype)
                for p, (x, y) in zip(projected, player_xy)
            ]
        )
