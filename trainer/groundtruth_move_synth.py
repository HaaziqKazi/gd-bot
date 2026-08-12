"""Recorded ground truth: one move trigger, measured off the running game.

EVIDENTIARY TIER (iii) -- recorded game data. Every number in this module was
read out of a Geode log produced by a real Geometry Dash 2.2081 process. None
of it was produced by ``trainer/trajectory.py``, by any other Python in this
repo, or by reasoning about what GD "should" do. That is the whole point of the
file: it exists so that a test can compare the predictor against the game
rather than against itself.

PROVENANCE
----------
Capture command::

    GDRL_SYNTH=1 GDRL_AUTOPLAY=1 GDRL_PROBE_MOVE=1 GDRL_BLOCK_INPUT=1 \
        ./scripts/run_sandbox.sh

Source log::

    sandbox/Geometry Dash.app/Contents/geode/logs/Geode 2026-08-11 18.41.23.log

Cross-check log, a **separate GD launch** whose 480 ``MOVE`` lines are
byte-identical to the above (``diff`` clean)::

    sandbox/Geometry Dash.app/Contents/geode/logs/Geode 2026-08-11 18.37.10.log

Both are copied to ``backups/reference-logs/``. ``sandbox/`` and ``backups/``
are both gitignored, and ``.gitignore`` also carries a bare ``*.log`` rule, so
**neither log is durable and neither can be committed**. This module is the
durable form of that measurement; it is plain Python so that no ignore rule can
silently unhook it.

THE RECORDED RUN
----------------
Synthetic level (``GDRL_SYNTH=1``). One move trigger, one target block:

    MOVE-TRIGGER tick=1 id=901 target=1 offX=0 offY=90 duration=2.0 easing=0

    trigger object  id=901  at x=300, y=135, group 0   (x-activated)
    target block    id=1    at x=600, y=435, group 1

The probe samples ``GameObject::m_positionX/m_positionY`` (doubles at +0x3b0 /
+0x3b8) after every gameplay ``processMoveActions`` call and emits one ``MOVE``
line per object whose position changed. It also logs ``cx``/``cy``, the CCNode
shadow, which stayed pinned at ``435.000000000`` for all 480 records -- the
move pipeline never calls ``CCNode::setPosition``.

Tick clock is ``lround(PlayLayer::m_attemptTime * 240.0)``.

WHAT IS MEASURED AND WHAT IS INFERRED
-------------------------------------
Measured, directly in the log:

  * 480 displacement records, ticks 234 .. 713, no gaps, no duplicates.
  * Total displacement 435 -> 525 = exactly 90.000000000.
  * ``ACTIVATION_TICK = 233`` -- two independent lines of evidence:
      (a) ``GDRL_PROBE_CMDVEC=1``, log ``Geode 2026-08-11 18.25.25.log``, a
          *different launch*: ``m_unkVector560`` size goes 0 -> 1 on tick 233
          and 1 -> 0 on tick 715. That probe is edge-triggered per physics
          step, so 233 is the first step at which the command existed.
      (b) The displacement record alone, with no reference to (a): inverting
          ``offset = 90 * (t - a) / 480`` for ``a`` at each of the 480 records
          gives a = 232.99934 mean, range [232.99699, 233.00020]. So the tick
          at which the command's elapsed time was zero is 233.000 +/- 0.003.
    (a) and (b) agree, and (b) is independent of the probe that produced (a).

Inferred, NOT measured -- do not treat these as ground truth:

  * The player's x at any tick. The probe logs no player position, so the
    player's arrival tick at the trigger's x=300 is *not* in this data. Any
    statement of the form "the trigger fired 1.92 ticks later than the player
    crossed x=300" rests on assuming x(t) = 1.298250437 * t, which is
    supported on Stereo Madness (README: x(391) = 507.615234375, i.e.
    391 * 1.298250437 - 0.0007) but has never been measured on this synth
    level. See PLAYER_X_ASSUMPTION_UNVERIFIED below.
  * Why the activation step itself displaces nothing. ``m_alreadyUpdated``
    (GroupCommandObject2 +0x1b0) is the obvious candidate and is UNVERIFIED.

SCOPE
-----
One trigger, ``ActionType`` 2 (y-offset), linear easing, duration 2.0 s, 1x
speed, cube, single group, no lock flags, no delay. Rotation, transform,
non-zero easing, non-unit ``m_moveMod*``, spawn delays, multiple commands on
one group, and every non-1x speed bucket are all unmeasured.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Trigger configuration, as GD loaded it (MOVE-TRIGGER line, verbatim)
# --------------------------------------------------------------------------

TRIGGER_OBJECT_ID = 901
TRIGGER_X = 300.0
TRIGGER_Y = 135.0

TARGET_GROUP_ID = 1
MOVE_OFFSET_X = 0.0
MOVE_OFFSET_Y = 90.0
DURATION = 2.0
EASING_TYPE = 0          # EasingType.NONE -- linear
EASING_RATE = 2.0        # not authored; GD's default. Irrelevant at easing 0.

TARGET_OBJECT_ID = 1
TARGET_X = 600.0
TARGET_Y_START = 435.0
TARGET_Y_END = 525.0

# --------------------------------------------------------------------------
# Timing, in ticks
# --------------------------------------------------------------------------

# The step at which the GroupCommandObject2 existed and its elapsed time was
# zero. See "WHAT IS MEASURED" above -- two independent lines of evidence.
ACTIVATION_TICK = 233

# The first step that displaced the block, and the last.
FIRST_MOVE_TICK = 234
LAST_MOVE_TICK = 713

# The step at which m_unkVector560 emptied (CMDVEC, separate launch).
COMMAND_REMOVED_TICK = 715

# ACTIVATION_TICK produces zero displacement. A predictor that starts the
# motion on the activation tick leads the game by exactly one tick.
ACTIVATION_DEAD_TIME_TICKS = FIRST_MOVE_TICK - ACTIVATION_TICK   # == 1

NUM_RECORDS = 480        # == LAST_MOVE_TICK - FIRST_MOVE_TICK + 1

# --------------------------------------------------------------------------
# Aggregate statistics over all 480 records
# --------------------------------------------------------------------------

# Sum of the logged per-step dy, and the endpoint-to-endpoint displacement.
TOTAL_DISPLACEMENT = 90.0

# Mean of the logged dy over all 480 records. Exactly 90/480 to nine places.
DY_MEAN_ALL = 0.187500000

# Mean over the 479 records excluding the final short step. This is the figure
# the README quotes as "per-tick dy mean"; it is NOT the mean over all 480, and
# it is NOT equal to 90/480. Both are recorded here so the discrepancy is not
# rediscovered as a bug.
DY_MEAN_EXCLUDING_FINAL = 0.187501179

# Largest and smallest logged dy. The minimum is the final step, which is short
# because p = clamp(elapsed/duration, 0, 1) lands the endpoint exactly on 90.
DY_MAX = 0.187507629
DY_MIN_FINAL_STEP = 0.186935425

# Residual against the *theoretical* line y = 435 + 90*(t - 233)/480, in units.
# This is float32 accumulation in m_deltaTimeInFloat, not curvature: genuine
# easing would swing per-step dy by tens of percent.
LINEARITY_RESIDUAL_MAX_THEORETICAL = 5.646e-4
LINEARITY_RESIDUAL_RMS_THEORETICAL = 2.224e-4

# Residual against a least-squares fit line, which absorbs the constant part of
# the float32 drift. These are the README's numbers; they are a different
# quantity from the two above and are ~4x smaller for that reason alone.
LINEARITY_RESIDUAL_MAX_LSQ = 3.988e-4
LINEARITY_RESIDUAL_RMS_LSQ = 9.395e-5

# Least-squares slope over all 480 records, units per tick. Exceeds 90/480 =
# 0.1875 by 6.1 ppm: the game's float32 m_deltaTimeInFloat accumulates very
# slightly fast, so a float64 predictor of the same law lags by that fraction.
LSQ_SLOPE = 0.187501148182

# --------------------------------------------------------------------------
# Sampled records: (tick, m_positionY)
# --------------------------------------------------------------------------
# Fifteen of the 480 ``MOVE`` lines, verbatim to the nine decimal places the
# probe printed. Chosen to cover the first three steps, both endpoints, the
# quarter/half/three-quarter points, and the final clamped step. Values are the
# absolute m_positionY, not the delta.
#
# m_positionX was 600.000000000 and the CCNode shadow (cx, cy) was
# (600.000000000, 435.000000000) on every one of the 480 records.

SAMPLES: tuple[tuple[int, float], ...] = (
    (234, 435.187502503),
    (235, 435.374999642),
    (236, 435.562502146),
    (240, 436.312501431),
    (300, 447.562496185),
    (353, 457.499988556),
    (400, 466.312479019),
    (473, 479.999969482),
    (500, 485.062530518),
    (592, 502.312759399),
    (600, 503.812782288),
    (700, 522.563034058),
    (711, 524.625556946),
    (712, 524.813064575),
    (713, 525.000000000),
)

# The probe emits a line only when a position changed, so there is no record at
# tick 233 (or at 714). Both of those steps left m_positionY untouched, which
# for 233 is the dead time and for 714 is the command sitting finished in
# m_unkVector560 until it is removed at 715.
IMPLIED_Y_AT_ACTIVATION = TARGET_Y_START     # 435.0, by absence of a record

# --------------------------------------------------------------------------
# The one thing this data cannot answer
# --------------------------------------------------------------------------

# The probe logged no player position, so the player's arrival tick at the
# trigger's x is not in this data set. Anything that needs it must assume
# x(t) = UNITS_PER_TICK[1] * t from x=0 at tick 0. Under that assumption:
#
#   ticks_to_reach(300)                     = 231.080
#   first integer tick with x >= 300        = 232
#   activation tick (measured)              = 233
#   first displacement (measured)           = 234
#
# so there is one tick between the crossing and the command going live, and one
# more before anything moves. Which of "ceil + 1", "round + 2" and "floor + 2"
# GD actually implements is NOT decidable from one trigger at one x: frac(231.08)
# = 0.08 and all three models land on 233. Disambiguating needs a trigger whose
# arrival tick has a fractional part near 0.5.
PLAYER_X_ASSUMPTION_UNVERIFIED = True
ASSUMED_TICKS_TO_REACH_TRIGGER = 231.080     # 300 / 1.298250437, UNVERIFIED
CROSSING_TO_ACTIVATION_TICKS_UNVERIFIED = 1  # 233 - ceil(231.080)
