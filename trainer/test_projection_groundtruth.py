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
  4. The pending-trigger path, driven end to end through ``ForwardProjector``,
     **leads the game by 1.9197 ticks / 0.35998 units** -- a known, measured,
     un-fixed defect. It is pinned here as a *characterisation*, not as
     correct behaviour, so that a future correction is forced to update this
     test explicitly rather than sliding past it.
  5. That the whole of (4) is a timing defect: applying one constant tick shift
     collapses the residual to the noise floor of (1). There is no independent
     position error and no error in the shape of the motion.

NOT asserted, because the recording cannot support it: the player's x at any
tick. The probe logged no player position, so (4) rests on assuming
``x(t) = UNITS_PER_TICK[1] * t`` from ``x = 0`` at tick 0. That assumption is
isolated in ``_assumed_player_x`` and named in the tests that depend on it.

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


def _assumed_player_x(tick: float) -> float:
    """UNVERIFIED. The one modelled input in this file; see the docstring.

    Supported on Stereo Madness (README: x at tick 391 is 507.615234375, and
    391 * 1.298250437 = 507.6159), never measured on the synth level.
    """
    return UNITS_PER_TICK[1] * tick


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
# 3. The pending-trigger path end to end -- the measured defect
# --------------------------------------------------------------------------

# Measured by validate_projection.py against the reference log, with the
# player-x assumption stated in the module docstring. Predictor leads the game.
MEASURED_LEAD_TICKS = 1.9197
MEASURED_LEAD_UNITS = 0.35998


def _project_pending(observation_tick: int, activation_x: float | None = None,
                     target_x: float | None = None):
    """(absolute arrival tick, projected object) for the recorded scene."""
    speed = SpeedProfile(player_x=_assumed_player_x(observation_tick))
    # The recorded target sits at x=600, which is 462 ticks away -- well past
    # ForwardProjector's default horizon_ticks=240 (311.6 units). At the default
    # the arrival tick clamps and the projection answers a different question.
    proj = ForwardProjector(speed, horizon_ticks=900)
    out = proj.project([_target_block(target_x)],
                       pending=[_pending_trigger(activation_x)])[0]
    return observation_tick + out.arrival_tick, out


def test_pending_path_leads_the_game_by_the_measured_amount():
    """CHARACTERISATION OF A KNOWN DEFECT, not an assertion of correctness.

    Depends on the UNVERIFIED player-x assumption. If a future change corrects
    the fire tick, this test is *supposed* to fail; update it deliberately.
    """
    recorded = dict(gt.SAMPLES)
    arrival, out = _project_pending(observation_tick=0)

    # The arrival tick is fractional, so compare against the two bracketing
    # recorded ticks rather than inventing an interpolated "recorded" value.
    lo, hi = math.floor(arrival), math.floor(arrival) + 1
    y_lo = _recorded_or_interpolated(lo)
    y_hi = _recorded_or_interpolated(hi)
    truth = y_lo + (y_hi - y_lo) * (arrival - lo)

    position_error = out.y - truth
    assert position_error == pytest.approx(MEASURED_LEAD_UNITS, abs=1e-4)

    timing_error = position_error / UNITS_PER_STEP
    assert timing_error == pytest.approx(MEASURED_LEAD_TICKS, abs=1e-3)

    # It is a lead, not a lag: the predictor puts the block further along its
    # travel than the game does. Sign matters -- a lag would mean the policy
    # sees a hazard late, which is the dangerous direction.
    assert position_error > 0.0

    # And the projection still calls itself exact, which it is entitled to:
    # nothing in the certainty grades covers an activation-tick offset.
    assert out.certainty == CERTAINTY_EXACT
    assert recorded  # fixture actually loaded


def _recorded_or_interpolated(tick: int) -> float:
    """Recorded y at ``tick``, or the linear law between the two records that
    bracket it. The fixture carries 15 of the 480 records, and the recording is
    linear to 5.6e-4 units over the whole 480 steps, so interpolating between
    two *recorded* samples adds nothing beyond that noise floor. Verified: the
    interpolation used below spans ticks 400..473, and every sample in that span
    is on the same line to within FLOAT32_NOISE_UNITS.
    """
    samples = gt.SAMPLES
    if tick <= samples[0][0]:
        return gt.TARGET_Y_START if tick < samples[0][0] else samples[0][1]
    if tick >= samples[-1][0]:
        return samples[-1][1]
    for (t0, y0), (t1, y1) in zip(samples, samples[1:]):
        if t0 <= tick <= t1:
            if t1 == t0:
                return y0
            return y0 + (y1 - y0) * (tick - t0) / (t1 - t0)
    raise AssertionError("unreachable: tick inside the sample range")


def test_the_lead_is_purely_a_timing_defect():
    """One constant tick shift removes the whole residual.

    This is the load-bearing claim: the predictor gets the *shape* of GD's
    motion right and the *start time* wrong. Delaying the predicted activation
    by the measured lead collapses the residual to the float noise floor. If a
    position defect existed independently of the timing defect, no single shift
    could do that.

    The shift is applied by displacing the trigger's activation x rather than by
    editing trajectory.py, which is out of scope here by instruction.
    """
    upt = UNITS_PER_TICK[1]
    shifted_x = gt.TRIGGER_X + MEASURED_LEAD_TICKS * upt

    worst = 0.0
    for target_tick in (300, 400, 473, 500, 600, 700):
        target_x = _assumed_player_x(target_tick)
        arrival, out = _project_pending(observation_tick=0,
                                       activation_x=shifted_x,
                                       target_x=target_x)
        assert arrival == pytest.approx(target_tick, abs=1e-6)
        worst = max(worst, abs(out.y - _recorded_or_interpolated(target_tick)))
    assert worst < FLOAT32_NOISE_UNITS, (
        f"residual {worst:.3e} units survives a constant tick shift, so the "
        "defect is not purely a timing one -- re-run validate_projection.py"
    )


def test_unshifted_residual_is_flat_across_the_horizon():
    """The error does not accumulate with prediction horizon.

    Recorded ticks 300 through 700 are 400 ticks apart, yet the residual is the
    same 0.36 units at both ends. A drifting integrator would ramp; a fixed
    activation offset is flat. Reported at the recorded sample ticks so the
    right-hand side of every comparison is a logged number.
    """
    errors = []
    for target_tick in (300, 353, 400, 473, 500, 592, 600, 700):
        target_x = _assumed_player_x(target_tick)
        arrival, out = _project_pending(observation_tick=0, target_x=target_x)
        assert arrival == pytest.approx(target_tick, abs=1e-6)
        errors.append(out.y - _recorded_or_interpolated(target_tick))
    assert min(errors) == pytest.approx(max(errors), abs=2e-3), (
        f"residual varies across the horizon: {min(errors):.6f} .. "
        f"{max(errors):.6f}")
    assert all(e == pytest.approx(MEASURED_LEAD_UNITS, abs=1e-3)
               for e in errors)


def test_no_projected_motion_before_the_recorded_activation():
    """Nothing moves before the trigger can possibly have fired.

    The strongest statement the recording supports on this side: at every tick
    up to 231 the game had not moved the block, and neither does the predictor.
    Ticks 232 and 233 are exactly where the predictor and the game disagree, so
    they are deliberately excluded here and covered by the lead tests above.
    """
    for target_tick in (0, 50, 100, 200, 225, 231):
        target_x = _assumed_player_x(target_tick)
        _, out = _project_pending(observation_tick=0, target_x=target_x)
        assert out.y == pytest.approx(gt.TARGET_Y_START, abs=1e-12)


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
