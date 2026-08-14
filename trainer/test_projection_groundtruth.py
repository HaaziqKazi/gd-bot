"""Forward projection against RECORDED GAME DATA. Tier (iii).

This is the repo's first tier-(iii) test. Read that claim precisely:

  * Tier (i), regression only -- ``predictor_output == helper_using_the_same_
    equations()``. Near-zero evidentiary value. Most of ``test_trajectory.py``
    is deliberately this, and says so.
  * Tier (ii) -- against an independent reimplementation.
  * Tier (iii) -- **against recorded game data.** Every expected value below
    came out of a real Geometry Dash 2.2081 process via ``GDRL_PROBE_MOVE=1``
    and is carried inline in ``trainer/groundtruth_move_synth.py``. Nothing in
    this file computes an expectation from ``trajectory.py``, and nothing here
    would pass if ``trajectory.py``'s motion law were wrong.
  * Tier (iv) -- against the live game, in-process.

The fixture is a plain Python module, not a log file: ``.gitignore`` carries a
bare ``*.log`` rule as well as ignoring ``sandbox/`` and ``backups/``, so a
committed ``.log`` fixture would be silently untracked and this test would pass
on this machine and fail everywhere else.

WHAT THIS TEST ASSERTS, AND WHAT IT DELIBERATELY DOES NOT
---------------------------------------------------------
Asserted against the game:

  1. The interpolator (README (E4)) reproduces the recorded position at every
     sampled tick, to the game's own float32 noise floor.
  2. The one-tick activation dead time is present in the predictor's output:
     the predicted displacement on the activation tick is exactly zero.
  3. The endpoint clamp lands on exactly 90.0 and stays there.
  4. The player's x-vs-tick law: the game's float32 accumulator reproduces both
     recorded player positions bit-exactly, and the origin is ``U * (t - 1)``.
  5. The fire tick. ``SpeedProfile.ticks_to_activation`` names tick 233 for the
     trigger at x=300 -- the measured activation tick -- and names it from
     every observation tick, not just one.
  6. The pending-trigger path, driven end to end through ``ForwardProjector``,
     **agrees with the game**. This assertion used to pin a 1.9197-tick /
     0.35998-unit lead as a measured, un-fixed defect; the correction landed on
     2026-08-12 and the assertions were flipped deliberately, which is what the
     characterisation existed to force.
  7. The residual that remains is the game's own float32 noise and nothing
     else: bounded by the record's measured deviation from the theoretical
     line, and one tick either way is 500x larger, so an off-by-one still fails
     loudly.

NOT EVERY ASSERTION HERE IS TIER (iii), and the exceptions are labelled at the
site. ``test_residual_against_every_one_of_the_480_records`` carries two
tier-(i) assertions -- the residual/negated-deviation identity, in which the
recorded value cancels algebraically, and the equality of ``worst`` with the
fixture constant recomputed from the same records. Both have falsification power
on the predictor and none on GD. See that test's docstring.

WHAT THE CORRECTION WAS, SINCE THIS FILE PINNED THE DEFECT
----------------------------------------------------------
The 1.9198-tick lead decomposed into two independent errors in two different
files, both in the same direction:

  * 1.0000 tick -- the origin convention. This file's own ``_assumed_player_x``
    used ``x(t) = U*t``; the game is ``U*(t-1)``. The defect was in the test
    fixture, not in ``trajectory.py``: the projector works entirely in relative
    ticks and has no absolute-tick origin to get wrong.
  * 0.91978 tick -- continuous-vs-integer crossing. That one was in
    ``trajectory.py``, which fired pending triggers at the continuous crossing
    time; GD fires on the first integer tick at which the player's sampled x is
    past the trigger. Fixed by ``SpeedProfile.ticks_to_activation``.

The one-tick object-displacement dead time accounts for NONE of it and was
already structurally present in the pending path -- see
``test_pending_path_also_carries_the_dead_time``.

THE MODELLED INPUTS, EXHAUSTIVELY -- read this before quoting the tier
----------------------------------------------------------------------
A tier-(iii) label is only worth what the right-hand side of the assertions is
made of. Every value this file compares the predictor against is a recorded
number, with exactly two exceptions, both named here:

  * ``_player_x_at_tick``. The law's origin and its per-tick advance are both
    measured (see ``groundtruth_move_synth.PLAYER_X_RECORDS`` and its
    provenance warning), but it is the continuous line rather than the game's
    float32 accumulator, so it deviates from the game by up to 0.036 units over
    a 400-tick lookahead. That deviation cannot reach the position residuals
    below: the tests place their targets using the same law they measure
    arrival with, so it cancels, and the only path by which it could survive is
    the ceil in the fire tick -- which this trigger's crossing misses by 0.08
    of a tick, 3x the worst-case drift.
  * Sub-tick interpolation in ``_truth_at_fractional_tick``. The projector's
    arrival tick is continuous (463.16) and the game only exists at integer
    ticks, so *some* sub-tick model is unavoidable. It spans one 0.1875-unit
    step between two ADJACENT recorded records and is guarded by an assertion
    that both really are recorded. Assertion (6a) additionally brackets the
    same quantity with no interpolation at all.

A THIRD used to exist and has been removed. Until the fixture carried all 480
records, the helper behind (4) fell back to the linear law between the fifteen
``SAMPLES``, and the arrival tick 462.16 landed in the 73-tick hole between the
samples at ticks 400 and 473 -- so BOTH bracketing values were modelled, by the
predictor's own motion law, across 73 ticks of unrecorded motion. That silently
made (4) a tier-(ii) claim wearing a tier-(iii) label.

The number it produced was not wrong: re-derived against the real records at
462/463 the lead is 0.359978 units / 1.91988 ticks, against 0.359973 / 1.91985
from the interpolant -- a 5.2e-6 unit difference, 5% of the assertion's own
1e-4 tolerance. But the *evidence* was wrong, and the agreement was only
knowable by going back to the log, which is the thing the test exists to avoid
having to do. Falsification power, measured: inject a 0.05-unit non-linearity
into the game's motion inside the gap, leaving all fifteen ``SAMPLES`` byte
identical, and the old helper's truth moves by 0.000e+00 -- perfectly blind --
while the current one moves 2.2e-2 and fails the assertion by 225x.

Run: python3 -m pytest trainer/test_projection_groundtruth.py -q
     python3 -m pytest trainer/ -q        (alongside the rest of the suite)

For the full 480-record analysis, including error-vs-horizon and the
decomposition of the 1.9197-tick lead, run
``python3 trainer/validate_projection.py``.
"""

from __future__ import annotations

import math

import pytest

import groundtruth_move_synth as gt
from trajectory import (
    CERTAINTY_EXACT,
    UNITS_PER_TICK,
    ActionType,
    ForwardProjector,
    GroupCommand,
    ObjectKind,
    ObjectSnapshot,
    PendingTrigger,
    SpeedProfile,
    player_x_at_tick,
    player_x_at_tick_float32,
)

# The game accumulates m_deltaTimeInFloat in float32 over 480 additions; the
# predictor computes elapsed in float64. The recorded divergence from the exact
# linear law peaks at 5.646e-4 units (see validate_projection.py section 2), so
# any tolerance below that would be asserting that GD is more precise than it
# is, and any tolerance far above it would stop catching real defects. 1e-3 is
# ~1/187 of a single 0.1875-unit step.
FLOAT32_NOISE_UNITS = 1e-3

# One step of this trigger's motion. Used to convert a unit error into ticks so
# that timing and position defects are reported in their own units.
UNITS_PER_STEP = gt.MOVE_OFFSET_Y / (gt.DURATION * 240.0)   # 0.1875


def _player_x_at_tick(tick: float) -> float:
    """The player's x at an absolute tick, as the continuous line.

    Was ``_assumed_player_x``, and was ``UNITS_PER_TICK[1] * tick`` -- wrong by
    exactly one tick. Both halves of this are now measured on this very level:
    the origin (x = 0.0 at tick 1) and the per-tick advance. What is still
    modelled is the *continuity*: the game accumulates in float32 and this is
    the line through it. See the module docstring for why that cannot reach the
    residuals here.
    """
    return player_x_at_tick(tick)


def _live_command(elapsed_ticks: float, applied_offset: float) -> GroupCommand:
    """The GroupCommand GD was holding, as the mod would hand it over."""
    return GroupCommand(
        target_group_id=gt.TARGET_GROUP_ID,
        duration=gt.DURATION,
        delta_time_in_float=elapsed_ticks / 240.0,
        easing_type=gt.EASING_TYPE,
        easing_rate=gt.EASING_RATE,
        action_type_2=int(ActionType.OFFSET_Y),
        action_value_2=gt.MOVE_OFFSET_Y,
        current_y_offset=applied_offset,
    )


def _pending_trigger(activation_x: float | None = None) -> PendingTrigger:
    return PendingTrigger(
        activation_x=gt.TRIGGER_X if activation_x is None else activation_x,
        target_group_id=gt.TARGET_GROUP_ID,
        duration=gt.DURATION,
        easing_type=gt.EASING_TYPE,
        easing_rate=gt.EASING_RATE,
        action_type_2=int(ActionType.OFFSET_Y),
        action_value_2=gt.MOVE_OFFSET_Y,
    )


def _target_block(x: float | None = None) -> ObjectSnapshot:
    return ObjectSnapshot(
        object_id=gt.TARGET_OBJECT_ID,
        kind=ObjectKind.SOLID,
        x=gt.TARGET_X if x is None else x,
        y=gt.TARGET_Y_START,
        half_w=15.0,
        half_h=15.0,
        groups=(gt.TARGET_GROUP_ID,),
    )


# --------------------------------------------------------------------------
# 1. The interpolator against the recorded positions
# --------------------------------------------------------------------------

@pytest.mark.parametrize("tick,recorded_y", gt.SAMPLES)
def test_interpolator_matches_recorded_position(tick: int, recorded_y: float):
    """(E4)'s closed form, evaluated at a recorded tick, lands on the game's y.

    The predictor is asked the question the projection actually asks: "given the
    command state, where is the object". Elapsed comes from the measured
    activation tick; nothing else is supplied.
    """
    cmd = _live_command(elapsed_ticks=tick - gt.ACTIVATION_TICK,
                        applied_offset=0.0)
    predicted_y = gt.TARGET_Y_START + cmd.value_at(int(ActionType.OFFSET_Y), 0.0)
    assert predicted_y == pytest.approx(recorded_y, abs=FLOAT32_NOISE_UNITS)


@pytest.mark.parametrize("tick,recorded_y", gt.SAMPLES)
def test_forward_step_from_earlier_state_matches_recording(tick, recorded_y):
    """Predicting *forward* from the activation tick, not evaluating in place.

    Distinct from the test above: here ``delta_time_in_float`` is zero and the
    whole elapsed time is supplied as ``extra_ticks``, which is the path the
    projector uses for lookahead. A sign error or a seconds/ticks mix-up in
    ``extra_ticks * TICK_SECONDS`` fails here and passes above.
    """
    cmd = _live_command(elapsed_ticks=0.0, applied_offset=0.0)
    horizon = tick - gt.ACTIVATION_TICK
    predicted_y = (gt.TARGET_Y_START
                   + cmd.value_at(int(ActionType.OFFSET_Y), horizon))
    assert predicted_y == pytest.approx(recorded_y, abs=FLOAT32_NOISE_UNITS)


def test_recorded_endpoints():
    """The two endpoints of the recording, which the clamp has to land on."""
    first_tick, first_y = gt.SAMPLES[0]
    last_tick, last_y = gt.SAMPLES[-1]
    assert first_tick == gt.FIRST_MOVE_TICK
    assert last_tick == gt.LAST_MOVE_TICK
    # The game's final step is short so that the endpoint is exact.
    assert last_y == gt.TARGET_Y_END
    cmd = _live_command(elapsed_ticks=last_tick - gt.ACTIVATION_TICK,
                        applied_offset=0.0)
    assert (gt.TARGET_Y_START
            + cmd.value_at(int(ActionType.OFFSET_Y), 0.0)) == pytest.approx(
                gt.TARGET_Y_END, abs=1e-9)


def test_clamp_holds_past_the_end():
    """p = clamp(elapsed/duration, 0, 1): the block stays at 525 afterwards.

    Recorded: the last displacement is at tick 713 and the command sat in
    m_unkVector560 until 715 without moving anything.
    """
    for tick in (gt.LAST_MOVE_TICK, gt.LAST_MOVE_TICK + 1,
                 gt.COMMAND_REMOVED_TICK, gt.COMMAND_REMOVED_TICK + 1000):
        cmd = _live_command(elapsed_ticks=tick - gt.ACTIVATION_TICK,
                            applied_offset=gt.MOVE_OFFSET_Y)
        predicted = gt.TARGET_Y_START + cmd.value_at(
            int(ActionType.OFFSET_Y), 0.0)
        assert predicted == pytest.approx(gt.TARGET_Y_END, abs=1e-9)


# --------------------------------------------------------------------------
# 2. The one-tick activation dead time
# --------------------------------------------------------------------------

def test_activation_step_displaces_nothing():
    """The dead time, asserted directly. THE first thing to check.

    GD's command was live on tick 233 (CMDVEC) and displaced nothing; the first
    displacement was on 234. The predictor reproduces that because elapsed is
    zero on the activation step, so p = 0 and the eased value is 0.
    """
    assert gt.ACTIVATION_DEAD_TIME_TICKS == 1
    cmd = _live_command(elapsed_ticks=0.0, applied_offset=0.0)
    assert cmd.value_at(int(ActionType.OFFSET_Y), 0.0) == 0.0
    assert cmd.delta_over(0.0) == (0.0, 0.0, 0.0)
    # And the very next step is the recorded first displacement.
    first_tick, first_y = gt.SAMPLES[0]
    assert cmd.value_at(int(ActionType.OFFSET_Y), 1.0) == pytest.approx(
        first_y - gt.TARGET_Y_START, abs=FLOAT32_NOISE_UNITS)


def test_pending_path_also_carries_the_dead_time():
    """The dead time is structural in the pending path too, not bolted on.

    ``PendingTrigger.as_command_at(0)`` clamps elapsed to zero, so the fire tick
    itself displaces nothing exactly as GD's activation tick does. This is why
    the 1.9197-tick lead measured below must NOT be "fixed" by subtracting one
    tick for the dead time -- that would double-count it.
    """
    trig = _pending_trigger()
    assert trig.as_command_at(0.0).delta_over(0.0) == (0.0, 0.0, 0.0)
    one_step = trig.as_command_at(1.0).delta_over(0.0)[1]
    first_y = gt.SAMPLES[0][1]
    assert one_step == pytest.approx(first_y - gt.TARGET_Y_START,
                                     abs=FLOAT32_NOISE_UNITS)


# --------------------------------------------------------------------------
# 2b. The player's own motion, and the fire tick it implies
# --------------------------------------------------------------------------

def test_the_recorded_player_x_is_reproduced_by_the_games_own_accumulator():
    """``x[n+1] = float32(x[n] + float32(U))``, against both recorded samples.

    BIT-EXACT, not approximate, and that is the whole evidentiary weight of the
    test: reproducing two 24-bit mantissas exactly, from x = 0.0 at tick 1 with
    no free parameter, is not something a wrong origin or a wrong per-tick
    advance can do by luck. It is also what licenses carrying these two numbers
    at all, since unlike the 480 MOVE records they were transcribed from a
    session record rather than re-read from a surviving log.
    """
    for tick, recorded_x in gt.PLAYER_X_RECORDS:
        assert player_x_at_tick_float32(tick) == recorded_x, (
            f"tick {tick}: accumulator gives "
            f"{player_x_at_tick_float32(tick)!r}, recorded {recorded_x!r}")


def test_the_origin_convention_is_t_minus_one_not_t():
    """x(t) = U*(t-1). The one-tick half of the corrected 1.9198-tick lead.

    Both candidate laws are evaluated against the same two recorded positions.
    The right one lands within the float32 accumulation drift; the wrong one is
    off by a whole tick of travel, which is the entire point.
    """
    for tick, recorded_x in gt.PLAYER_X_RECORDS:
        assert abs(_player_x_at_tick(tick) - recorded_x) < 1e-3
        wrong = UNITS_PER_TICK[1] * tick          # the old _assumed_player_x
        assert abs(wrong - recorded_x) == pytest.approx(UNITS_PER_TICK[1],
                                                        abs=1e-3)


def test_the_fire_tick_is_the_measured_activation_tick():
    """(E7) end to end: ceil of the crossing lands on the recorded tick 233.

    Asserted from many observation ticks, not one. The fire tick is computed
    relative to the observation, so a rule that happened to be right at tick 0
    and wrong elsewhere would pass a single-point check; an activation tick is
    a property of the level and must not depend on when it is asked about.
    Observation ticks past the crossing are included deliberately -- there the
    relative fire tick is negative and the absolute one must still be 233.
    """
    for observation_tick in (0, 1, 50, 100, 200, 231, 232, 233, 234, 300):
        speed = SpeedProfile(player_x=_player_x_at_tick(observation_tick))
        fire = observation_tick + speed.ticks_to_activation(gt.TRIGGER_X)
        assert fire == gt.ACTIVATION_TICK, (
            f"observation tick {observation_tick} implies fire tick {fire}, "
            f"recorded activation is {gt.ACTIVATION_TICK}")


def test_rounding_the_crossing_would_miss_the_recorded_tick():
    """Falsification, not corroboration: `round` is ruled out by this record.

    Under the corrected origin the continuous crossing is at 232.080, so round
    gives 232 and ceil gives 233 -- and the game gave 233. The previously
    recorded "not decidable from one trigger" was an artifact of the wrong
    origin, under which every candidate rule landed on 233 by coincidence.

    Still UNVERIFIED and NOT claimed here: how GD breaks an exact tie, and
    whether it quantises against the float32-accumulated x rather than the
    line. See gt.ACTIVATION_TIE_BREAK_UNVERIFIED.
    """
    speed = SpeedProfile(player_x=_player_x_at_tick(0))
    crossing = speed.ticks_to_reach(gt.TRIGGER_X)
    assert round(crossing) == gt.ACTIVATION_TICK - 1 != math.ceil(crossing)
    assert math.ceil(crossing) == gt.ACTIVATION_TICK
    assert gt.CROSSING_TO_ACTIVATION_TICKS == 0
    assert gt.ACTIVATION_TIE_BREAK_UNVERIFIED


# --------------------------------------------------------------------------
# 3. The pending-trigger path end to end -- corrected 2026-08-12
# --------------------------------------------------------------------------

# The residual left after the correction, measured over all 480 recorded ticks
# (see the report in validate_projection.py section 4):
#
#     mean |err|   1.3932e-04 units   0.000743 ticks
#     rms          2.2239e-04 units
#     max |err|    5.6457e-04 units   0.003011 ticks
#
# Those are not free numbers. They are, to every digit, the record's OWN
# deviation from the theoretical line 435 + 90*(t-233)/480 -- gt's
# LINEARITY_RESIDUAL_{MAX,RMS}_THEORETICAL, which were measured before this
# correction existed. The bound below is therefore taken from the fixture rather
# than hand-tuned: widening it would mean claiming the game is noisier than it
# was measured to be.
#
# READ THE "to every digit" CLAIM NARROWLY. The predictor's output IS the
# theoretical line, exactly (max |out.y - line| = 0.000e+00 over all 480 ticks,
# re-derived 2026-08-13). So "the residual equals the record's own deviation
# from the line" is an algebraic identity, not a measurement: it is true for any
# record whatsoever, including randomised ones. See
# test_residual_against_every_one_of_the_480_records, which is where the
# identity is asserted and where it is now labelled tier (i).
#
# What the constant is still good for is a THRESHOLD, and that use is tier (iii):
# in test_pending_path_agrees_with_the_game the left-hand side is
# out.y - (recorded y interpolated across one 0.1875-unit step), so a predictor
# that moved off the line would blow it. The supporting claim that the leftover
# is GD's float32 accumulation at ~6.1 ppm rests on gt.LSQ_SLOPE
# (0.187501148182 against a theoretical 0.1875), not on the identity.
CORRECTED_RESIDUAL_BOUND_UNITS = gt.LINEARITY_RESIDUAL_MAX_THEORETICAL


def _project_pending(observation_tick: int, activation_x: float | None = None,
                     target_x: float | None = None):
    """(absolute arrival tick, projected object) for the recorded scene."""
    speed = SpeedProfile(player_x=_player_x_at_tick(observation_tick))
    # The recorded target sits at x=600, which is 463 ticks away -- well past
    # ForwardProjector's default horizon_ticks=240 (311.6 units). At the default
    # the arrival tick clamps and the projection answers a different question.
    proj = ForwardProjector(speed, horizon_ticks=900)
    out = proj.project([_target_block(target_x)],
                       pending=[_pending_trigger(activation_x)])[0]
    return observation_tick + out.arrival_tick, out


def test_pending_path_agrees_with_the_game():
    """The headline. This test used to pin a 1.9197-tick lead as a DEFECT.

    It was written as a characterisation precisely so that a correction would
    be forced to come here and flip it rather than sliding past it. The
    correction landed on 2026-08-12 (origin convention + integer crossing) and
    this is the flip: the predictor now agrees with the game to the game's own
    float32 noise.
    """
    arrival, out = _project_pending(observation_tick=0)

    # The arrival tick is fractional (463.16), so the two bracketing ticks are
    # 463 and 464. Both are recorded -- assert that, because until the fixture
    # carried all 480 records they were not: the old arrival at 462.16 fell in
    # the 73-tick hole between SAMPLES[400] and SAMPLES[473], and both ends of
    # the bracket were produced by the linear law rather than read out of the
    # game.
    lo, hi = math.floor(arrival), math.floor(arrival) + 1
    assert lo in gt.RECORDS_BY_TICK, f"tick {lo} is not a recorded record"
    assert hi in gt.RECORDS_BY_TICK, f"tick {hi} is not a recorded record"
    y_lo, y_hi = gt.RECORDS_BY_TICK[lo], gt.RECORDS_BY_TICK[hi]

    # (a) Model-free bracket. Uses no interpolation of any kind: the recording
    #     is monotone, so whatever the game's y was at a fractional tick it lay
    #     between the two recorded neighbours. The predicted y must lie in that
    #     bracket -- which is the strongest statement the recording supports on
    #     its own, and one the old 1.92-tick lead could not have satisfied
    #     (it sat ~2 steps above y_hi).
    assert y_lo <= out.y <= y_hi, (
        f"predicted {out.y!r} is outside the recorded bracket "
        f"[{y_lo!r}, {y_hi!r}] at ticks {lo}/{hi}")

    # (b) The precise figure, which needs the one unavoidable sub-tick model:
    #     linear across the single 0.1875-unit step between ticks 463 and 464.
    truth = _truth_at_fractional_tick(arrival)
    position_error = out.y - truth
    assert abs(position_error) < CORRECTED_RESIDUAL_BOUND_UNITS, (
        f"position error {position_error:+.3e} units exceeds the game's own "
        f"float32 noise floor {CORRECTED_RESIDUAL_BOUND_UNITS:.3e}")

    # Reported in ticks as well as units, because a timing defect and a
    # position defect are different defects and must not be conflated. One tick
    # on this trigger is 0.1875 units; the residual is under 1/300 of that.
    timing_error = position_error / UNITS_PER_STEP
    assert abs(timing_error) < 0.005

    # The projection calls itself exact, which it is now entitled to.
    assert out.certainty == CERTAINTY_EXACT


def _recorded(tick: int) -> float:
    """The RECORDED m_positionY at an integer tick. No interpolation, ever.

    Outside the recorded span the answer is still recorded, by absence: before
    tick 234 the probe emitted no line, which is the measurement that the block
    had not moved; after 713 it emitted no line, which is the measurement that
    the block sat at its clamped endpoint.

    This function replaces a ``_recorded_or_interpolated`` that fell back to the
    linear law between the fifteen ``SAMPLES``. That fallback was a tier defect,
    not an arithmetic one: the interpolant IS ``trajectory.py``'s motion law, so
    an assertion whose right-hand side came out of it was comparing the
    predictor against its own model and calling the result tier (iii).
    """
    if tick < gt.FIRST_MOVE_TICK:
        return gt.TARGET_Y_START
    if tick > gt.LAST_MOVE_TICK:
        return gt.RECORDS_BY_TICK[gt.LAST_MOVE_TICK]
    return gt.RECORDS_BY_TICK[tick]


def _truth_at_fractional_tick(arrival: float) -> float:
    """Truth at a fractional tick, from the two ADJACENT recorded steps.

    The projector's arrival tick is continuous; the game only exists at integer
    ticks. Some sub-tick model is therefore unavoidable. This is the smallest
    one available: linear between tick ``floor(arrival)`` and ``floor(arrival)+1``,
    both of which are recorded numbers, spanning a single 0.1875-unit step.

    The guard is the point. It fails loudly if either bracketing tick is ever
    not in the record, which is exactly the condition that let the old helper
    silently model 73 ticks of motion.
    """
    lo = math.floor(arrival)
    hi = lo + 1
    if gt.FIRST_MOVE_TICK <= lo and hi <= gt.LAST_MOVE_TICK:
        assert lo in gt.RECORDS_BY_TICK and hi in gt.RECORDS_BY_TICK, (
            f"ticks {lo}/{hi} bracket the arrival but are not both recorded; "
            "this would silently interpolate the predictor's own motion law")
    y_lo, y_hi = _recorded(lo), _recorded(hi)
    return y_lo + (y_hi - y_lo) * (arrival - lo)


def test_residual_against_every_one_of_the_480_records():
    """The corrected residual, over the whole recording rather than one point.

    The target's x is a dial for choosing which recorded tick to interrogate:
    offX = 0, so the block's y-vs-tick law does not depend on its x, and the
    record covers every tick in 234..713. It is NOT a claim that a block was
    ever recorded at that x.

    MIXED TIERS, and the split matters -- this docstring used to claim the
    sharpest assertion here was the strongest one, and it is the weakest.

      * TIER (iii): the residual is inside the game's own float32 noise at every
        one of the 480 recorded ticks. The right-hand side is a recorded number
        and a predictor off the line fails it.
      * TIER (i), REGRESSION ONLY: the residual equals, to 1e-12, the NEGATED
        deviation of the record from the theoretical line. Read as evidence
        about GD this is worth nothing, because ``recorded_y`` cancels
        algebraically: err = out.y - recorded_y and the right-hand side is
        line - recorded_y, so the assertion reduces to out.y == line. Measured
        2026-08-13: replacing every record with ``rec + uniform(-500, 500)``
        leaves it passing at max violation 0.000e+00. It is stated directly
        below instead -- out.y == line, EXACTLY, max deviation 0.000e+00 over
        480 ticks -- which is the whole of what it tests, and is a real
        regression on the predictor: any residual of the predictor's own breaks
        it while still passing a tolerance.
      * TIER (i): ``worst == LINEARITY_RESIDUAL_MAX_THEORETICAL``. Same reason.
        Given out.y == line, ``worst`` is by construction max|line - record|,
        which is how that fixture constant was measured in the first place; the
        assertion compares a fixture constant against a recomputation from the
        same records. It catches a predictor that drifts off the line, and a
        re-paste of RECORDS from a different run. It does not corroborate the
        line.
    """
    step = gt.MOVE_OFFSET_Y / (gt.DURATION * 240.0)
    worst = 0.0
    for target_tick, recorded_y in gt.RECORDS:
        arrival, out = _project_pending(
            observation_tick=0, target_x=_player_x_at_tick(target_tick))
        assert arrival == pytest.approx(target_tick, abs=1e-6)
        err = out.y - recorded_y
        worst = max(worst, abs(err))
        line = gt.TARGET_Y_START + step * (target_tick - gt.ACTIVATION_TICK)
        # Tier (iii): against the recorded value. FLOAT32_NOISE_UNITS rather
        # than CORRECTED_RESIDUAL_BOUND_UNITS on purpose -- the worst of these
        # residuals is 5.6457e-04 against a bound of 5.646e-04, a 0.005% margin,
        # so the tighter constant would be a threshold fitted to this very
        # maximum. The <=-the-bound version is the `worst` assertion below, and
        # it is labelled tier (i) for that reason.
        assert abs(err) < FLOAT32_NOISE_UNITS
        # Tier (i): the identity, stated as itself. Exact, not approximate --
        # if this ever needs a tolerance the predictor has stopped being the
        # line and the tier-(iii) assertion above is the one to trust.
        assert out.y == line, (
            f"predictor is {out.y - line:+.3e} units off the theoretical line "
            f"at tick {target_tick}")
        # Tier (i): the same identity in the form it was historically written
        # in. Kept so the equivalence is visible rather than asserted.
        assert err == pytest.approx(-(recorded_y - line), abs=1e-12)
    assert worst == pytest.approx(gt.LINEARITY_RESIDUAL_MAX_THEORETICAL,
                                  rel=1e-3), (
        f"worst residual {worst:.4e} units is not the record's own linearity "
        "residual; the predictor has acquired an error of its own")
    assert worst / step < 0.004          # in TICKS, reported separately


def test_one_tick_either_way_still_fails_loudly():
    """Falsification power. The tolerance is 300x tighter than an off-by-one.

    A test that agrees with the game is only worth something if it would stop
    agreeing when the fire tick moves. Shifting the trigger's activation x by
    one tick of player travel -- in either direction, since the two are not
    symmetric -- must blow the bound by two orders of magnitude.
    """
    upt = UNITS_PER_TICK[1]
    for shift in (-1.0, +1.0):
        worst = 0.0
        for target_tick in (300, 400, 473, 500, 600, 700):
            _, out = _project_pending(
                observation_tick=0,
                activation_x=gt.TRIGGER_X + shift * upt,
                target_x=_player_x_at_tick(target_tick))
            worst = max(worst, abs(out.y - _recorded(target_tick)))
        assert worst > 100.0 * CORRECTED_RESIDUAL_BOUND_UNITS
        assert worst == pytest.approx(UNITS_PER_STEP, abs=1e-3), (
            f"a {shift:+.0f}-tick shift moved the prediction by {worst:.6f} "
            f"units; one tick of this trigger is {UNITS_PER_STEP} units")


def test_residual_is_flat_across_the_horizon():
    """The error does not accumulate with prediction horizon.

    Recorded ticks 300 through 700 are 400 ticks apart. Before the correction
    this test asserted the residual was the same 0.36 units at both ends -- flat
    but wrong, the signature of a fixed activation offset. It is now flat and
    small, and flatness is still the assertion that would catch a drifting
    integrator, which would ramp.
    """
    errors = []
    for target_tick in (300, 353, 400, 473, 500, 592, 600, 700):
        target_x = _player_x_at_tick(target_tick)
        arrival, out = _project_pending(observation_tick=0, target_x=target_x)
        assert arrival == pytest.approx(target_tick, abs=1e-6)
        errors.append(out.y - _recorded(target_tick))
    assert max(errors) - min(errors) < CORRECTED_RESIDUAL_BOUND_UNITS, (
        f"residual varies across the horizon: {min(errors):.3e} .. "
        f"{max(errors):.3e}")
    assert all(abs(e) < CORRECTED_RESIDUAL_BOUND_UNITS for e in errors)


def test_no_projected_motion_before_the_recorded_activation():
    """Nothing moves before the trigger fired, INCLUDING on the fire tick.

    Ticks 232 and 233 used to be excluded here: they were exactly where the
    predictor and the game disagreed. They are now included, and they are the
    sharp end of the test. 232 is the last tick at which the player is short of
    the trigger (recorded x = 299.8955) and 233 is the activation tick itself,
    which GD spends displacing nothing. A predictor that fired at the
    continuous crossing would move the block at 232; one that dropped the dead
    time would move it at 233.
    """
    for target_tick in (0, 50, 100, 200, 225, 231, 232, 233):
        target_x = _player_x_at_tick(target_tick)
        _, out = _project_pending(observation_tick=0, target_x=target_x)
        assert out.y == pytest.approx(gt.TARGET_Y_START, abs=1e-12), (
            f"the block moved by tick {target_tick}; the game's first "
            f"displacement is tick {gt.FIRST_MOVE_TICK}")

    # And the very next tick is the recorded first displacement, to the noise
    # floor -- the other side of the same edge.
    _, out = _project_pending(
        observation_tick=0, target_x=_player_x_at_tick(gt.FIRST_MOVE_TICK))
    assert out.y == pytest.approx(gt.RECORDS_BY_TICK[gt.FIRST_MOVE_TICK],
                                  abs=FLOAT32_NOISE_UNITS)


# --------------------------------------------------------------------------
# 4. Fixture integrity -- cheap, and it catches a corrupted paste
# --------------------------------------------------------------------------

def test_fixture_is_internally_consistent():
    """Guards the fixture, not the predictor. No trajectory.py involved.

    Deliberately tier (i) in the sense that it checks the recorded numbers
    against each other -- but the numbers it relates were measured, so a typo in
    one of them shows up as an inconsistency rather than as a silently wrong
    expectation everywhere else in the file.
    """
    assert gt.NUM_RECORDS == gt.LAST_MOVE_TICK - gt.FIRST_MOVE_TICK + 1
    assert gt.TARGET_Y_END - gt.TARGET_Y_START == gt.TOTAL_DISPLACEMENT
    assert gt.TOTAL_DISPLACEMENT == gt.MOVE_OFFSET_Y
    assert gt.NUM_RECORDS == gt.DURATION * 240
    assert gt.DY_MEAN_ALL == pytest.approx(
        gt.TOTAL_DISPLACEMENT / gt.NUM_RECORDS, abs=1e-9)

    # The activation tick, re-derived from each sampled record on its own. This
    # is the measurement that makes ACTIVATION_TICK independent of the CMDVEC
    # run it was originally read from.
    span = gt.DURATION * 240.0
    for tick, y in gt.SAMPLES:
        implied = tick - (y - gt.TARGET_Y_START) * span / gt.MOVE_OFFSET_Y
        assert implied == pytest.approx(gt.ACTIVATION_TICK, abs=0.005), (
            f"record (tick={tick}, y={y}) implies activation tick "
            f"{implied:.6f}, not {gt.ACTIVATION_TICK}")

    # Monotone and never overshooting the target.
    ys = [y for _, y in gt.SAMPLES]
    assert ys == sorted(ys)
    assert max(ys) == gt.TARGET_Y_END


def test_full_record_is_complete_and_contiguous():
    """RECORDS is the whole log, not a subsample. No trajectory.py involved.

    The 73-tick-gap defect this file was audited for was only possible because
    a truth lookup could silently fall between records. This is the assertion
    that makes that structurally impossible.
    """
    ticks = [t for t, _ in gt.RECORDS]
    assert len(gt.RECORDS) == gt.NUM_RECORDS == 480
    assert ticks == list(range(gt.FIRST_MOVE_TICK, gt.LAST_MOVE_TICK + 1))
    assert len(gt.RECORDS_BY_TICK) == gt.NUM_RECORDS
    ys = [y for _, y in gt.RECORDS]
    assert ys == sorted(ys)                     # monotone, as recorded
    assert ys[0] > gt.TARGET_Y_START
    assert ys[-1] == gt.TARGET_Y_END
    assert ys[-1] - gt.TARGET_Y_START == gt.TOTAL_DISPLACEMENT


def test_samples_are_a_subset_of_the_full_record():
    """SAMPLES must be the same numbers as RECORDS, not a re-paste of them."""
    for tick, y in gt.SAMPLES:
        assert tick in gt.RECORDS_BY_TICK
        assert gt.RECORDS_BY_TICK[tick] == y, (
            f"SAMPLES and RECORDS disagree at tick {tick}: "
            f"{y} vs {gt.RECORDS_BY_TICK[tick]}")


def test_aggregate_constants_derive_from_the_full_record():
    """The headline aggregates recomputed from all 480 records.

    Until RECORDS existed these constants were hand-carried numbers that
    nothing could check. This makes them tier (iii) in the file that uses them.

    TOLERANCE, and why it is not tighter. The dy constants were read from the
    probe's own ``dy=`` column; RECORDS carries the ``y=`` column. Both are
    printed to nine decimals, so differencing two of them carries up to 1e-9 of
    quantisation. Measured against the log's dy column directly, the worst
    disagreement over all 480 records is exactly 1.0e-9 -- the quantum, not a
    defect. 2e-9 is that bound with one bit of headroom. A tighter tolerance
    here would be asserting the log printed more digits than it did.
    """
    ys = [y for _, y in gt.RECORDS]
    dy = [ys[0] - gt.TARGET_Y_START] + [b - a for a, b in zip(ys, ys[1:])]
    assert sum(dy) == pytest.approx(gt.TOTAL_DISPLACEMENT, abs=1e-8)
    assert sum(dy) / len(dy) == pytest.approx(gt.DY_MEAN_ALL, abs=2e-9)
    assert sum(dy[:-1]) / (len(dy) - 1) == pytest.approx(
        gt.DY_MEAN_EXCLUDING_FINAL, abs=2e-9)
    assert max(dy) == pytest.approx(gt.DY_MAX, abs=2e-9)
    assert min(dy) == dy[-1] == pytest.approx(gt.DY_MIN_FINAL_STEP, abs=2e-9)


def test_the_two_linearity_residuals_are_against_the_two_stated_fits():
    """G2: the theoretical-line residual and the least-squares one, recomputed.

    README quotes the least-squares pair without saying so, and it is ~4x
    smaller only because the fit absorbs the constant part of the float32
    drift. Both are named in the fixture; this recomputes each from the 480
    records against the fit its name claims, so the labels cannot drift apart
    from the numbers.

    WHAT TIER THAT IS: a fixture constant checked against a recomputation from
    the same records, so it says nothing about the predictor -- ``trajectory``
    is not imported into this test's arithmetic at all. It is tier (iii) about
    the FIXTURE (RECORDS is a verbatim transcription of the log's y column, and
    these constants are correct summaries of it) and tier (i) about everything
    else. Its real job is catching a swapped label or a re-pasted RECORDS.
    """
    n = len(gt.RECORDS)
    ts = [float(t) for t, _ in gt.RECORDS]
    ys = [y for _, y in gt.RECORDS]

    # (1) Against the THEORETICAL line y = 435 + 90*(t - 233)/480.
    step = gt.MOVE_OFFSET_Y / (gt.DURATION * 240.0)
    r_th = [ys[i] - (gt.TARGET_Y_START + step * (ts[i] - gt.ACTIVATION_TICK))
            for i in range(n)]
    assert max(abs(v) for v in r_th) == pytest.approx(
        gt.LINEARITY_RESIDUAL_MAX_THEORETICAL, rel=1e-3)
    assert (sum(v * v for v in r_th) / n) ** 0.5 == pytest.approx(
        gt.LINEARITY_RESIDUAL_RMS_THEORETICAL, rel=1e-3)

    # (2) Against the LEAST-SQUARES fit, which has two free parameters.
    mt, my = sum(ts) / n, sum(ys) / n
    slope = (sum((ts[i] - mt) * (ys[i] - my) for i in range(n))
             / sum((t - mt) ** 2 for t in ts))
    icpt = my - slope * mt
    r_ls = [ys[i] - (slope * ts[i] + icpt) for i in range(n)]
    assert slope == pytest.approx(gt.LSQ_SLOPE, rel=1e-9)
    assert max(abs(v) for v in r_ls) == pytest.approx(
        gt.LINEARITY_RESIDUAL_MAX_LSQ, rel=1e-3)
    assert (sum(v * v for v in r_ls) / n) ** 0.5 == pytest.approx(
        gt.LINEARITY_RESIDUAL_RMS_LSQ, rel=1e-3)

    # The whole point of carrying both: they are NOT the same quantity.
    assert (gt.LINEARITY_RESIDUAL_RMS_THEORETICAL
            > 2.0 * gt.LINEARITY_RESIDUAL_RMS_LSQ)


def test_activation_tick_rederives_from_every_one_of_the_480_records():
    """ACTIVATION_TICK independent of the CMDVEC probe, at full resolution.

    The SAMPLES version of this check saw 15 records. This sees all 480, so the
    quoted range [232.99699, 233.00020] is actually the range.
    """
    span = gt.DURATION * 240.0
    implied = [t - (y - gt.TARGET_Y_START) * span / gt.MOVE_OFFSET_Y
               for t, y in gt.RECORDS]
    assert min(implied) == pytest.approx(232.99699, abs=1e-5)
    assert max(implied) == pytest.approx(233.00020, abs=1e-5)
    assert sum(implied) / len(implied) == pytest.approx(232.99934, abs=1e-5)
    assert all(abs(v - gt.ACTIVATION_TICK) < 0.005 for v in implied)


def test_samples_are_on_the_recorded_line_to_the_noise_floor():
    """The 15 samples reproduce the recorded linearity, not just a trend.

    If someone re-captures and pastes in records from a run with different
    trigger parameters, the sampled points stop being collinear at 1e-3 and this
    fails before any of the predictor tests produce a confusing near-miss.
    """
    step = gt.MOVE_OFFSET_Y / (gt.DURATION * 240.0)
    for tick, y in gt.SAMPLES:
        expected = gt.TARGET_Y_START + step * (tick - gt.ACTIVATION_TICK)
        assert y == pytest.approx(expected, abs=FLOAT32_NOISE_UNITS)
