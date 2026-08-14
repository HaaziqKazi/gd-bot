"""Checks on the forward-projection layer.

Objective B's claim is narrow and testable: given the trigger state GD is
actually holding, this module reproduces GD's own interpolator, and an object
under an active or pending trigger is observed where it *will* be rather than
where it *is*. These tests assert that structurally, on synthetic state, so
they fail on a wiring mistake rather than waiting for a training curve to look
wrong.

Several of them are regression tests for specific decompiled details -- the
2^-23 duration clamp, the rate<=0 default, the type-0 early out. Those are
exactly the places where a plausible reimplementation diverges from GD, and a
divergence there is invisible in aggregate: the projection stays smooth and
sensible-looking and is simply in the wrong place.

Run: python3 -m pytest trainer/test_trajectory.py -q
"""

from __future__ import annotations

import math
import struct

import pytest
import torch

from conditioning import ConditionedTrunk, Regime, regimes_to_tensors
from trajectory import (
    CERTAINTY_CONDITIONAL,
    CERTAINTY_EXACT,
    CERTAINTY_UNKNOWN,
    NUM_KINDS,
    PLAYER_X_FLOAT32_DRIFT_UNITS_PER_240_TICKS,
    PLAYER_X_FLOAT32_DRIFT_UNITS_PER_400_TICKS,
    PLAYER_X_TICK_ORIGIN,
    TICK_HZ,
    TICK_SECONDS,
    UNITS_PER_TICK,
    ActionType,
    EasingType,
    ForwardProjector,
    GroupCommand,
    ObjectKind,
    ObjectSnapshot,
    PendingTrigger,
    SpeedProfile,
    SpeedSegment,
    TrajectoryRaster,
    ease,
    player_x_at_tick,
    player_x_at_tick_float32,
)

# The 1x per-tick advance, measured over 175 bit-identical attempts
# (README, "Physics is fixed-step at 240Hz").
UNITS_PER_TICK_1X = 1.298250437


# --------------------------------------------------------------------------
# Easing: GameToolbox::getEasedValue
# --------------------------------------------------------------------------

def test_easing_none_is_the_identity_even_with_a_rate():
    """The type-0 early out happens before the rate default (m1 0x44f9a4).

    If the rate defaulting were hoisted above it -- the obvious refactor -- a
    linear trigger with easingRate 0 would silently become a power curve.
    """
    for t in (0.0, 0.25, 0.5, 0.75, 1.0):
        assert ease(t, EasingType.NONE, 0.0) == t
        assert ease(t, EasingType.NONE, 3.7) == t


def test_out_of_range_easing_falls_through_to_identity():
    """`cmp w8, #0x11; b.hi` is unsigned, so negatives pass through too."""
    assert ease(0.3, 99, 2.0) == pytest.approx(0.3)
    assert ease(0.3, -4, 2.0) == pytest.approx(0.3)


def test_non_positive_rate_defaults_to_two():
    """fcsel s8, s1, s2, hi with s2 == 2.0 -- rate must be strictly positive."""
    assert ease(0.5, EasingType.EASE_IN, 0.0) == pytest.approx(0.5 ** 2)
    assert ease(0.5, EasingType.EASE_IN, -1.0) == pytest.approx(0.5 ** 2)
    assert ease(0.5, EasingType.EASE_IN, 3.0) == pytest.approx(0.5 ** 3)


def test_ease_in_and_out_are_the_power_pair():
    """EaseIn is powf(t, rate); EaseOut is powf(t, 1/rate). Same jump table,
    reciprocal exponents (m1 0x44fc40 and 0x44fb38)."""
    for t in (0.1, 0.4, 0.9):
        assert ease(t, EasingType.EASE_IN, 2.5) == pytest.approx(t ** 2.5)
        assert ease(t, EasingType.EASE_OUT, 2.5) == pytest.approx(t ** (1 / 2.5))


_EXPONENTIAL = {
    EasingType.EXPONENTIAL_IN,
    EasingType.EXPONENTIAL_OUT,
    EasingType.EXPONENTIAL_IN_OUT,
}


def test_every_non_exponential_easing_pins_the_endpoints():
    """These 15 curves must map 0->0 and 1->1, or a finished trigger lands
    short of its target and the object never arrives where the level designer
    put it. Elastic and Back overshoot in between, which is fine and is exactly
    why the endpoints have to be asserted separately."""
    for e in EasingType:
        if e in _EXPONENTIAL:
            continue
        assert ease(0.0, e, 2.0) == pytest.approx(0.0, abs=1e-6), e.name
        assert ease(1.0, e, 2.0) == pytest.approx(1.0, abs=1e-6), e.name


def test_the_exponential_family_misses_its_endpoints_and_that_is_gds_bug():
    """Found by the endpoint test above failing, then confirmed against the
    disassembly rather than 'fixed'.

    ExponentialIn has a zero guard (m1 0x44fd58) but no one guard, and carries
    a materialised -0.001 (0xBA83126F). ExponentialOut has a one guard
    (m1 0x44fa54) but no zero guard -- harmless, since 1 - 2^0 is already 0.
    ExponentialInOut (m1 0x44fb10) has neither.

    So a move trigger on ExponentialIn permanently stops 0.1% short. This test
    pins the quirk so that nobody 'cleans it up' later: matching GD is the
    requirement, not producing a tidy curve.
    """
    assert ease(1.0, EasingType.EXPONENTIAL_IN, 2.0) == pytest.approx(0.999)
    assert ease(0.0, EasingType.EXPONENTIAL_IN_OUT, 2.0) == pytest.approx(0.5 * 2 ** -10)
    assert ease(1.0, EasingType.EXPONENTIAL_IN_OUT, 2.0) == pytest.approx(
        0.5 * (2.0 - 2 ** -10)
    )
    # The guards that do exist still work.
    assert ease(0.0, EasingType.EXPONENTIAL_IN, 2.0) == 0.0
    assert ease(1.0, EasingType.EXPONENTIAL_OUT, 2.0) == 1.0
    # The worst endpoint error in the family is exactly the 0.001 constant, so
    # it is a documented bias rather than something the projection has to
    # compensate for. Asserted with <= because ExponentialIn hits the bound
    # dead on -- a strict < here fails, which is how the exact size of the
    # error got pinned down in the first place.
    for e in _EXPONENTIAL:
        assert abs(ease(0.0, e, 2.0)) <= 1e-3, e.name
        assert abs(ease(1.0, e, 2.0) - 1.0) <= 1e-3 + 1e-12, e.name


def test_back_and_elastic_overshoot():
    """(F-style sanity) These curves leave [0,1] on purpose. A clamp added
    'for safety' would quietly turn every Back/Elastic trigger into a
    different animation than the one in the level."""
    back = [ease(t / 100, EasingType.BACK_IN, 2.0) for t in range(101)]
    assert min(back) < -0.05
    elastic = [ease(t / 100, EasingType.ELASTIC_OUT, 0.3) for t in range(101)]
    assert max(elastic) > 1.05


def test_bounce_in_is_the_reflection_of_bounce_out():
    for t in (0.0, 0.2, 0.5, 0.83, 1.0):
        assert ease(t, EasingType.BOUNCE_IN, 2.0) == pytest.approx(
            1.0 - ease(1.0 - t, EasingType.BOUNCE_OUT, 2.0)
        )


# --------------------------------------------------------------------------
# GroupCommand: GroupCommandObject2::updateAction
# --------------------------------------------------------------------------

def _linear_move(dx: float, dy: float, duration: float, elapsed: float = 0.0):
    return GroupCommand(
        target_group_id=1,
        duration=duration,
        delta_time_in_float=elapsed,
        easing_type=int(EasingType.NONE),
        action_type_1=int(ActionType.OFFSET_X),
        action_value_1=dx,
        action_type_2=int(ActionType.OFFSET_Y),
        action_value_2=dy,
    )


def test_linear_move_is_exactly_proportional_to_elapsed():
    cmd = _linear_move(dx=120.0, dy=-60.0, duration=1.0)
    dx, dy, _ = cmd.delta_over(TICK_HZ // 2)     # half a second
    assert dx == pytest.approx(60.0)
    assert dy == pytest.approx(-30.0)


def test_move_completes_exactly_at_its_duration_and_never_overshoots():
    """p is clamped at 1 (fcmp/b.pl at m1 0x447340). Past the duration the
    offset must sit still, not keep accumulating -- an unclamped projection
    walks the whole level off-screen over a long horizon."""
    cmd = _linear_move(dx=90.0, dy=0.0, duration=0.5)
    at_end, _, _ = cmd.delta_over(0.5 * TICK_HZ)
    way_past, _, _ = cmd.delta_over(50.0 * TICK_HZ)
    assert at_end == pytest.approx(90.0)
    assert way_past == pytest.approx(90.0)


def test_negative_lookahead_clamps_rather_than_extrapolating_backwards():
    """p is clamped at both ends, so asking about a time before the command
    started returns the start value rather than running the interpolator into
    negative progress. The fixpoint in ForwardProjector can hand out small
    negative lookaheads for objects moving toward the player, so this is a
    reachable path, not a hypothetical one."""
    cmd = _linear_move(dx=90.0, dy=0.0, duration=1.0, elapsed=0.1)
    cmd.current_x_offset = 9.0                      # 0.1s of a 1s / 90-unit move
    behind, _, _ = cmd.delta_over(-10.0 * TICK_HZ)
    assert behind == pytest.approx(-9.0)            # back to the start, no further
    assert cmd.delta_over(-1000.0 * TICK_HZ)[0] == pytest.approx(-9.0)


def test_zero_duration_snaps_instead_of_dividing_by_zero():
    """m_duration is clamped to 2^-23 before the divide (immediate
    0x3E80000000000000). A guard of `if duration == 0: return value` would
    agree here but disagree for a duration of, say, 1e-9."""
    cmd = _linear_move(dx=45.0, dy=0.0, duration=0.0)
    dx, _, _ = cmd.delta_over(1.0)
    assert dx == pytest.approx(45.0)

    tiny = _linear_move(dx=45.0, dy=0.0, duration=1e-9)
    # 1e-9 < 2^-23, so GD uses 2^-23 and a single tick still saturates it.
    assert tiny.delta_over(1.0)[0] == pytest.approx(45.0)


def test_delta_is_measured_against_the_live_applied_offset():
    """GD subtracts m_currentXOffset, not a recomputed value_at(0). Half-done
    move, half a second left: the remaining travel is 60, not 120."""
    cmd = _linear_move(dx=120.0, dy=0.0, duration=1.0, elapsed=0.5)
    cmd.current_x_offset = 60.0
    dx, _, _ = cmd.delta_over(0.5 * TICK_HZ)
    assert dx == pytest.approx(60.0)


def test_locked_axis_contributes_nothing():
    """m_lockedInX gates the whole type-1 branch (ldrb +0x77; tbnz)."""
    cmd = _linear_move(dx=120.0, dy=90.0, duration=1.0)
    cmd.locked_in_x = True
    dx, dy, _ = cmd.delta_over(TICK_HZ)
    assert dx == 0.0
    assert dy == pytest.approx(90.0)


def test_disabled_command_is_frozen():
    """GroupCommandObject2::step returns immediately on m_disabled (+0x71)."""
    cmd = _linear_move(dx=120.0, dy=0.0, duration=1.0)
    cmd.disabled = True
    assert cmd.delta_over(TICK_HZ) == (0.0, 0.0, 0.0)


def test_eased_move_shares_endpoints_with_linear_but_not_the_middle():
    lin = _linear_move(dx=100.0, dy=0.0, duration=1.0)
    eased = _linear_move(dx=100.0, dy=0.0, duration=1.0)
    eased.easing_type = int(EasingType.EASE_IN_OUT)
    eased.easing_rate = 2.0

    assert lin.delta_over(TICK_HZ)[0] == pytest.approx(eased.delta_over(TICK_HZ)[0])
    mid_l = lin.delta_over(TICK_HZ // 4)[0]
    mid_e = eased.delta_over(TICK_HZ // 4)[0]
    assert abs(mid_l - mid_e) > 1.0


def test_angular_actions_share_one_channel():
    """Types 3 and 4 write the same slot (+0x90) in updateAction, so a command
    carrying either must surface on the rotation output."""
    for at in (ActionType.ANGULAR_A, ActionType.ANGULAR_B):
        cmd = GroupCommand(
            target_group_id=7, duration=1.0, delta_time_in_float=0.0,
            action_type_1=int(at), action_value_1=180.0,
        )
        _, _, drot = cmd.delta_over(TICK_HZ)
        assert drot == pytest.approx(180.0), at.name


def test_lock_to_player_downgrades_certainty_but_still_projects():
    cmd = _linear_move(dx=100.0, dy=0.0, duration=1.0)
    cmd.lock_to_player_x = True
    assert cmd.certainty == CERTAINTY_CONDITIONAL
    assert cmd.delta_over(TICK_HZ)[0] == pytest.approx(100.0)


def test_unmodellable_command_reports_unknown():
    cmd = _linear_move(dx=100.0, dy=0.0, duration=1.0)
    cmd.unmodellable = True
    assert cmd.certainty == CERTAINTY_UNKNOWN


# --------------------------------------------------------------------------
# Speed profile / horizon
# --------------------------------------------------------------------------

def test_one_tick_at_1x_advances_the_measured_distance():
    """The single number this whole horizon rests on."""
    assert UNITS_PER_TICK[1] == pytest.approx(UNITS_PER_TICK_1X, abs=1e-6)


def test_the_1x_constant_is_the_float32_the_game_actually_adds():
    """Tier (ii) FOR THE FLOAT32 ENCODING, AND FOR NOTHING ELSE.

    The module used to carry 311.58/240 = 1.298250000 under a comment citing
    the measured 1.298250437 -- the code did not hold the number it cited. It
    now holds float32(1.298250437), which is what the game adds, and this
    rebuilds that value from the decimal rather than trusting the literal.

    Scope of the (ii): what is independently reimplemented here is the float32
    rounding (via struct, against the module's own _f32), so what is falsified
    is a mistyped or mis-expanded literal. The DECIMAL 1.298250437 is the same
    README measurement the module cites, carried in this file as
    UNITS_PER_TICK_1X -- this test cannot tell you it is the right number, only
    that the module holds the float32 of it. The game-grounded check on the
    value itself is the Stereo Madness anchor below and the recorded player-x
    samples in test_projection_groundtruth.
    """
    reconstructed = struct.unpack("<f", struct.pack("<f", UNITS_PER_TICK_1X))[0]
    assert UNITS_PER_TICK[1] == reconstructed
    assert UNITS_PER_TICK[1] != 311.58 / TICK_HZ        # the old, rounded value


def test_stereo_madness_first_spike_is_391_ticks_of_travel_away():
    """A real anchor rather than a synthetic one: no-input Stereo Madness dies
    at maxX = 507.615234375, bit-identical over 175 attempts.

    The claim, stated as what the evidence supports: 391 accumulated float32
    steps from x = 0 reproduce 507.615234375 bit-exactly (see the test below);
    on the continuous line the same distance is 390.999471 ticks of TRAVEL,
    which is what is asserted here.

    Nothing here settles what ABSOLUTE tick number the game or README would put
    on that step. Converting travel to an absolute tick needs the origin
    convention, x = 0 at tick 1, which is measured on a different level in a
    different channel; this test does not exercise it and an earlier version of
    this docstring used it to call README's "tick 391" off by one. That
    off-by-one is UNVERIFIED here and is left to TODO G6.
    """
    profile = SpeedProfile(player_x=0.0)
    assert profile.ticks_to_reach(507.615234375) == pytest.approx(391.0, abs=0.01)


def test_the_float32_accumulator_reproduces_the_stereo_madness_death_x():
    """Tier (iii), and from a level this module was never fitted to.

    THE CLAIM: 391 accumulated float32 steps from x = 0 reproduce README's
    null-input Stereo Madness death x, 507.615234375, BIT-EXACTLY -- and 390
    steps do not (506.3169860839844). Different level, different probe, ~550
    attempts apart, no free parameters. That is corroboration of the float32
    accumulation, on a step COUNT, which is what the accumulator is a function
    of.

    ``player_x_at_tick_float32(392)`` is how 391 steps are spelled here, because
    the function's origin is x = 0 at tick 1. The absolute tick number is
    therefore the origin convention's contribution, not this record's: the
    measurement is the step count. An earlier version of this docstring read the
    agreement as corroborating the origin convention as well, which it cannot --
    a run with x = 0 at tick 0 would produce the identical 507.615234375 from
    the identical 391 steps.
    """
    assert player_x_at_tick_float32(392) == 507.615234375
    assert player_x_at_tick_float32(391) != 507.615234375


def test_the_player_x_origin_is_tick_one():
    """x(t) = U*(t-1). Regression (tier i) on the convention constant itself."""
    assert PLAYER_X_TICK_ORIGIN == 1
    assert player_x_at_tick(1) == 0.0
    assert player_x_at_tick_float32(1) == 0.0
    assert player_x_at_tick(2) == UNITS_PER_TICK[1]
    with pytest.raises(ValueError):
        player_x_at_tick_float32(0)


def test_the_documented_float32_drift_bound_is_the_measured_one():
    """Tier (i), regression on a documented constant. NO recorded data enters.

    SpeedProfile models player x as a line; the game accumulates in float32.
    The module documents the resulting divergence over a lookahead window as a
    bounded known error, so the bound has to stay honest. This recomputes it.

    The tier used to read "(ii)" here and the text used to say "recomputed over
    the full 4,653-tick recorded run". Neither is true and trajectory.py's own
    docstring says so ("this bound is arithmetic on the measured accumulator,
    not an observation of a mis-predicted trigger"). 4,653 is the length of the
    run the accumulator law was characterised on; what runs below is a
    SIMULATION of 4,653 ticks of that law. No sample of the game's x is read,
    and no observation of a mis-predicted trigger exists at all. If the law
    itself were wrong this test would happily reproduce the wrong bound.
    """
    u = UNITS_PER_TICK[1]
    xs = []
    x = 0.0
    step = struct.unpack("<f", struct.pack("<f", u))[0]
    for t in range(1, 4654):
        xs.append(x)
        x = struct.unpack("<f", struct.pack("<f", x + step))[0]
    drift = [xs[i] - u * i for i in range(len(xs))]

    for window, bound in ((240, PLAYER_X_FLOAT32_DRIFT_UNITS_PER_240_TICKS),
                          (400, PLAYER_X_FLOAT32_DRIFT_UNITS_PER_400_TICKS)):
        worst = max(abs(drift[i + window] - drift[i])
                    for i in range(len(drift) - window))
        assert worst == pytest.approx(bound, rel=0.02), (
            f"{window}-tick drift is {worst:.4f} units, module documents "
            f"{bound}")
        # In ticks, reported separately from units on purpose.
        assert worst / u < 0.03


def test_activation_is_quantised_to_the_next_integer_tick():
    """Regression (tier i) on the (E7) rule; the game-grounded check for it
    lives in test_projection_groundtruth.

    ticks_to_reach is continuous and ticks_to_activation is not, and the two
    must not be quietly interchangeable: GD evaluates the trigger once per
    physics step, so activation lands on a whole tick.
    """
    profile = SpeedProfile(player_x=0.0)
    for x in (10.0, 300.0, 1234.5):
        cont = profile.ticks_to_reach(x)
        fire = profile.ticks_to_activation(x)
        assert fire == math.ceil(cont)
        assert 0.0 <= fire - cont < 1.0
        assert float(fire).is_integer()

    # An exact hit activates on that same tick rather than the next one -- the
    # `>=` reading of (E7). UNVERIFIED: no recorded crossing lands on equality.
    assert profile.ticks_to_activation(UNITS_PER_TICK[1] * 5.0) == 5.0

    # A trigger already behind the player fired in the past, and how far in the
    # past is information the projection needs; it must not clamp to zero.
    behind = SpeedProfile(player_x=500.0)
    assert behind.ticks_to_activation(100.0) < 0.0


def test_ticks_to_reach_and_x_at_tick_are_inverses():
    profile = SpeedProfile(
        player_x=100.0,
        segments=[
            SpeedSegment(start_x=0.0, bucket=1),
            SpeedSegment(start_x=400.0, bucket=3),
            SpeedSegment(start_x=900.0, bucket=0),
        ],
    )
    for x in (100.0, 250.0, 400.0, 640.0, 900.0, 1500.0):
        t = profile.ticks_to_reach(x)
        assert profile.x_at_tick(t) == pytest.approx(x, abs=1e-6), x


def test_a_speed_portal_changes_arrival_time_past_it_only():
    """Averaging speed across a portal would move the arrival time of every x
    behind the portal too, which is why the profile is piecewise."""
    slow = SpeedProfile(player_x=0.0, segments=[SpeedSegment(0.0, 1)])
    fast = SpeedProfile(
        player_x=0.0,
        segments=[SpeedSegment(0.0, 1), SpeedSegment(500.0, 4)],
    )
    assert slow.ticks_to_reach(400.0) == pytest.approx(fast.ticks_to_reach(400.0))
    assert fast.ticks_to_reach(1500.0) < slow.ticks_to_reach(1500.0)


def test_unverified_speed_buckets_are_reported_as_conditional():
    """1x is measured; the other four are community numbers. The projection is
    allowed to use them, but it is not allowed to claim they are exact."""
    profile = SpeedProfile(player_x=0.0, segments=[SpeedSegment(0.0, 4)])
    assert profile.certainty(10.0) == CERTAINTY_CONDITIONAL
    assert SpeedProfile(player_x=0.0).certainty(10.0) == CERTAINTY_EXACT


def test_profile_rejects_a_gap_in_front_of_the_player():
    with pytest.raises(ValueError):
        SpeedProfile(player_x=0.0, segments=[SpeedSegment(500.0, 1)])


# --------------------------------------------------------------------------
# The projection itself
# --------------------------------------------------------------------------

def _block(x: float, y: float = 0.0, groups=(), kind=ObjectKind.HAZARD):
    return ObjectSnapshot(
        object_id=1, kind=kind, x=x, y=y, half_w=15.0, half_h=15.0, groups=groups
    )


def test_static_object_projects_to_itself():
    proj = ForwardProjector(SpeedProfile(player_x=0.0))
    out = proj.project([_block(600.0, 30.0)])
    assert out[0].x == pytest.approx(600.0)
    assert out[0].y == pytest.approx(30.0)
    assert out[0].certainty == CERTAINTY_EXACT
    assert out[0].moved == pytest.approx(0.0)


def test_a_block_under_an_active_move_is_seen_where_it_will_be():
    """The whole point of the module, in one assertion.

    A hazard 600 units ahead is drifting up 300 units over 2 seconds. The
    player reaches its x in ~462 ticks, which is 1.93 s, so by then it has
    risen almost the full 300 -- from harmless-looking floor level to head
    height. A static map would place it on the floor.
    """
    cmd = GroupCommand(
        target_group_id=5, duration=2.0, delta_time_in_float=0.0,
        action_type_1=int(ActionType.OFFSET_Y), action_value_1=300.0,
    )
    proj = ForwardProjector(SpeedProfile(player_x=0.0), horizon_ticks=600)
    out = proj.project([_block(600.0, 0.0, groups=(5,))], commands=[cmd])[0]

    arrival = SpeedProfile(player_x=0.0).ticks_to_reach(600.0)
    assert out.arrival_tick == pytest.approx(arrival, abs=1e-6)
    assert out.y == pytest.approx(300.0 * (arrival * TICK_SECONDS) / 2.0)
    assert out.y > 280.0
    assert out.vy > 0.0
    assert out.certainty == CERTAINTY_EXACT


def test_only_the_targeted_group_moves():
    cmd = GroupCommand(
        target_group_id=5, duration=1.0, delta_time_in_float=0.0,
        action_type_1=int(ActionType.OFFSET_Y), action_value_1=300.0,
    )
    proj = ForwardProjector(SpeedProfile(player_x=0.0))
    moved, still = proj.project(
        [_block(600.0, 0.0, groups=(5,)), _block(600.0, 0.0, groups=(6,))],
        commands=[cmd],
    )
    assert moved.moved > 100.0
    assert still.moved == pytest.approx(0.0)


def test_two_commands_on_one_group_compose_additively():
    """moveObjects is called once per command and each adds its own delta, so
    two move triggers on the same group sum rather than the later one winning."""
    a = GroupCommand(
        target_group_id=5, duration=1.0, delta_time_in_float=0.0,
        action_type_1=int(ActionType.OFFSET_Y), action_value_1=100.0,
    )
    b = GroupCommand(
        target_group_id=5, duration=1.0, delta_time_in_float=0.0,
        action_type_1=int(ActionType.OFFSET_Y), action_value_1=50.0,
    )
    proj = ForwardProjector(SpeedProfile(player_x=0.0))
    out = proj.project([_block(600.0, 0.0, groups=(5,))], commands=[a, b])[0]
    assert out.y == pytest.approx(150.0)


def test_a_pending_trigger_fires_only_once_the_player_crosses_it():
    """Two identical hazards. The trigger that moves them sits at x = 300.

    The near hazard is at x = 200 -- the player reaches it *before* crossing
    the trigger, so it must be projected static. The far hazard is at x = 900;
    by the time the player gets there the trigger has been running for the
    difference, so it must be projected moved. Getting this backwards is the
    single most likely mistake in the whole module, hence a test that only
    passes if the ordering is right.
    """
    trig = PendingTrigger(
        activation_x=300.0, target_group_id=9, duration=1.0,
        action_type_1=int(ActionType.OFFSET_Y), action_value_1=300.0,
    )
    proj = ForwardProjector(SpeedProfile(player_x=0.0), horizon_ticks=2000)
    near, far = proj.project(
        [_block(200.0, 0.0, groups=(9,)), _block(900.0, 0.0, groups=(9,))],
        pending=[trig],
    )
    assert near.moved == pytest.approx(0.0)
    assert far.y == pytest.approx(300.0)     # 900 is far enough that it finishes


def test_a_pending_trigger_is_partially_applied_mid_flight():
    """Regression only (tier i): the expectation uses the module's own clocks.

    It moved with the 2026-08-12 arrival-tick correction and is kept because it
    pins the *relationship* the correction changed. The elapsed time runs from
    the ACTIVATION tick -- the first integer tick past the trigger, which is
    what GD fires on -- not from the continuous crossing. Written against
    ticks_to_reach for both ends, as it was, it silently asserted the old
    behaviour and would have gone on doing so.
    """
    trig = PendingTrigger(
        activation_x=300.0, target_group_id=9, duration=2.0,
        action_type_1=int(ActionType.OFFSET_Y), action_value_1=480.0,
    )
    profile = SpeedProfile(player_x=0.0)
    proj = ForwardProjector(profile, horizon_ticks=2000)
    out = proj.project([_block(600.0, 0.0, groups=(9,))], pending=[trig])[0]

    fire = profile.ticks_to_activation(300.0)
    assert fire == math.ceil(profile.ticks_to_reach(300.0))    # 232, not 231.08
    elapsed = (profile.ticks_to_reach(600.0) - fire) * TICK_SECONDS
    assert 0.0 < out.y < 480.0
    assert out.y == pytest.approx(480.0 * elapsed / 2.0, rel=1e-9)


def test_spawn_delay_pushes_the_start_back():
    fast = PendingTrigger(
        activation_x=0.0, target_group_id=9, duration=1.0,
        action_type_1=int(ActionType.OFFSET_Y), action_value_1=240.0,
    )
    delayed = PendingTrigger(
        activation_x=0.0, target_group_id=9, duration=1.0, spawn_delay=0.5,
        action_type_1=int(ActionType.OFFSET_Y), action_value_1=240.0,
    )
    proj = ForwardProjector(SpeedProfile(player_x=0.0), horizon_ticks=2000)
    a = proj.project([_block(300.0, 0.0, groups=(9,))], pending=[fast])[0]
    b = proj.project([_block(300.0, 0.0, groups=(9,))], pending=[delayed])[0]
    assert b.y < a.y


def test_a_remapped_target_group_is_flagged_not_guessed():
    """Random and spawn-remapped triggers pick their target group at fire time
    from m_randomSeed. Projecting them would be inventing a level."""
    trig = PendingTrigger(
        activation_x=100.0, target_group_id=9, duration=1.0,
        action_type_1=int(ActionType.OFFSET_Y), action_value_1=300.0,
        target_is_remapped=True,
    )
    proj = ForwardProjector(SpeedProfile(player_x=0.0), horizon_ticks=2000)
    out = proj.project([_block(900.0, 0.0, groups=(9,))], pending=[trig])[0]
    assert out.certainty == CERTAINTY_UNKNOWN
    assert out.moved == pytest.approx(0.0)


def test_unmodellable_command_leaves_the_object_put_and_says_so():
    cmd = GroupCommand(
        target_group_id=5, duration=1.0, delta_time_in_float=0.0,
        action_type_1=int(ActionType.OFFSET_Y), action_value_1=300.0,
        unmodellable=True,
    )
    proj = ForwardProjector(SpeedProfile(player_x=0.0))
    out = proj.project([_block(600.0, 0.0, groups=(5,))], commands=[cmd])[0]
    assert out.certainty == CERTAINTY_UNKNOWN
    assert out.moved == pytest.approx(0.0)


def test_arrival_fixpoint_accounts_for_horizontal_motion():
    """An object drifting toward the player is met sooner than its static x
    suggests. One iteration of the fixpoint under-corrects; two is what the
    projector runs, and the result must lie between the naive answer and the
    true meeting point.
    """
    cmd = GroupCommand(
        target_group_id=5, duration=4.0, delta_time_in_float=0.0,
        action_type_1=int(ActionType.OFFSET_X), action_value_1=-400.0,
    )
    profile = SpeedProfile(player_x=0.0)
    proj = ForwardProjector(profile, horizon_ticks=2000)
    out = proj.project([_block(900.0, 0.0, groups=(5,))], commands=[cmd])[0]

    assert out.x < 900.0                               # it came toward us
    assert out.arrival_tick < profile.ticks_to_reach(900.0)
    assert out.vx < 0.0


def test_an_object_outrunning_the_player_is_flagged_unknown():
    """The fixpoint does not contract when |v_object_x| >= v_player, so more
    iterations would only produce a more confident wrong answer."""
    fast = UNITS_PER_TICK[1] * TICK_HZ * 3.0    # three times player speed
    cmd = GroupCommand(
        target_group_id=5, duration=10.0, delta_time_in_float=0.0,
        action_type_1=int(ActionType.OFFSET_X), action_value_1=fast * 10.0,
    )
    proj = ForwardProjector(SpeedProfile(player_x=0.0), horizon_ticks=2000)
    out = proj.project([_block(600.0, 0.0, groups=(5,))], commands=[cmd])[0]
    assert out.certainty == CERTAINTY_UNKNOWN


def test_horizon_caps_the_lookahead():
    """Beyond the horizon the projection must stop advancing, or a level-long
    move trigger would be integrated over the whole level for an object the
    player will not see for a minute."""
    cmd = GroupCommand(
        target_group_id=5, duration=60.0, delta_time_in_float=0.0,
        action_type_1=int(ActionType.OFFSET_Y), action_value_1=6000.0,
    )
    short = ForwardProjector(SpeedProfile(player_x=0.0), horizon_ticks=60)
    long_ = ForwardProjector(SpeedProfile(player_x=0.0), horizon_ticks=2400)
    obj = _block(9000.0, 0.0, groups=(5,))
    assert short.project([obj], commands=[cmd])[0].arrival_tick == pytest.approx(60.0)
    assert long_.project([obj], commands=[cmd])[0].y > \
        short.project([obj], commands=[cmd])[0].y


def test_projector_rejects_a_non_positive_horizon():
    with pytest.raises(ValueError):
        ForwardProjector(SpeedProfile(player_x=0.0), horizon_ticks=0)


# --------------------------------------------------------------------------
# Rasterisation
# --------------------------------------------------------------------------

def test_raster_shape_and_channel_budget():
    r = TrajectoryRaster(height=16, width=24)
    grid = r.render([], player_x=0.0, player_y=0.0)
    assert grid.shape == (TrajectoryRaster.NUM_CHANNELS, 16, 24)
    assert TrajectoryRaster.NUM_CHANNELS == 2 * NUM_KINDS + 3
    assert torch.count_nonzero(grid) == 0


def test_now_and_arrival_channels_disagree_for_a_moving_object():
    """If they ever agree for a moving object the whole layer is a no-op, and
    a no-op that renders plausible pictures is the failure this test exists to
    catch."""
    cmd = GroupCommand(
        target_group_id=5, duration=2.0, delta_time_in_float=0.0,
        action_type_1=int(ActionType.OFFSET_Y), action_value_1=300.0,
    )
    proj = ForwardProjector(SpeedProfile(player_x=0.0), horizon_ticks=900)
    out = proj.project([_block(600.0, 0.0, groups=(5,))], commands=[cmd])

    r = TrajectoryRaster(height=32, width=48, cell_size=30.0)
    grid = r.render(out, player_x=0.0, player_y=0.0)

    now = grid[int(ObjectKind.HAZARD)]
    later = grid[NUM_KINDS + int(ObjectKind.HAZARD)]
    assert now.sum() > 0
    assert later.sum() > 0
    assert not torch.equal(now, later)
    # And it moved *up*: the arrival footprint sits in higher rows.
    assert later.nonzero()[:, 0].min() > now.nonzero()[:, 0].max()


def test_static_level_leaves_the_certainty_channel_empty():
    """Stored as 1 - certainty so a zero tensor means 'fully known', which is
    the right default for the static case and for an unfilled buffer."""
    proj = ForwardProjector(SpeedProfile(player_x=0.0))
    out = proj.project([_block(200.0, 0.0)])
    grid = TrajectoryRaster(height=16, width=24).render(out, 0.0, 0.0)
    assert grid[TrajectoryRaster.CERTAINTY_CHANNEL].sum() == 0.0


def test_uncertainty_is_written_at_the_object_not_globally():
    cmd = GroupCommand(
        target_group_id=5, duration=1.0, delta_time_in_float=0.0,
        action_type_1=int(ActionType.OFFSET_Y), action_value_1=300.0,
        unmodellable=True,
    )
    proj = ForwardProjector(SpeedProfile(player_x=0.0))
    out = proj.project([_block(200.0, 0.0, groups=(5,))], commands=[cmd])
    grid = TrajectoryRaster(height=16, width=24).render(out, 0.0, 0.0)
    ch = grid[TrajectoryRaster.CERTAINTY_CHANNEL]
    assert ch.sum() > 0
    assert ch.sum() < ch.numel()          # localised, not a broadcast scalar


def test_velocity_channels_carry_sign():
    up = GroupCommand(
        target_group_id=5, duration=4.0, delta_time_in_float=0.0,
        action_type_1=int(ActionType.OFFSET_Y), action_value_1=400.0,
    )
    down = GroupCommand(
        target_group_id=6, duration=4.0, delta_time_in_float=0.0,
        action_type_1=int(ActionType.OFFSET_Y), action_value_1=-400.0,
    )
    proj = ForwardProjector(SpeedProfile(player_x=0.0), horizon_ticks=900)
    out = proj.project(
        [_block(300.0, 0.0, groups=(5,)), _block(600.0, 0.0, groups=(6,))],
        commands=[up, down],
    )
    grid = TrajectoryRaster(height=32, width=48).render(out, 0.0, 0.0)
    vy = grid[TrajectoryRaster.VY_CHANNEL]
    assert vy.max() > 0.0
    assert vy.min() < 0.0


def test_objects_outside_the_window_are_clipped_not_wrapped():
    proj = ForwardProjector(SpeedProfile(player_x=0.0))
    far = proj.project([_block(100000.0, 0.0)])
    behind = proj.project([_block(-100000.0, 0.0)])
    r = TrajectoryRaster(height=16, width=24)
    assert r.render(far, 0.0, 0.0).sum() == 0.0
    assert r.render(behind, 0.0, 0.0).sum() == 0.0


def test_render_batch_rejects_a_length_mismatch():
    """Explicitly not the silent-broadcast behaviour ConditionedTrunk.forward
    has. A batch wiring bug must be a crash, not a slow-motion training bug."""
    r = TrajectoryRaster(height=8, width=8)
    with pytest.raises(ValueError):
        r.render_batch([[], [], []], [(0.0, 0.0), (0.0, 0.0)])


def test_render_batch_is_per_sample_independent():
    proj = ForwardProjector(SpeedProfile(player_x=0.0))
    a = proj.project([_block(200.0, 0.0)])
    b = proj.project([_block(500.0, 90.0, kind=ObjectKind.SOLID)])
    r = TrajectoryRaster(height=16, width=24)

    batched = r.render_batch([a, b], [(0.0, 0.0), (0.0, 0.0)])
    assert batched.shape == (2, TrajectoryRaster.NUM_CHANNELS, 16, 24)
    assert torch.equal(batched[0], r.render(a, 0.0, 0.0))
    assert torch.equal(batched[1], r.render(b, 0.0, 0.0))


# --------------------------------------------------------------------------
# Composition with the conditioning trunk
# --------------------------------------------------------------------------

def test_raster_feeds_the_conditioned_trunk_unchanged():
    """The interface contract between Objective A and Objective B: the raster's
    channel count is exactly what ConditionedTrunk's in_channels wants, and the
    regime conditioning still discriminates once trajectory channels are in
    the stack."""
    torch.manual_seed(0)
    trunk = ConditionedTrunk(
        in_channels=TrajectoryRaster.NUM_CHANNELS, channels=32, num_blocks=2
    )
    torch.manual_seed(1)
    for head in trunk.film.heads:
        torch.nn.init.normal_(head.weight, std=0.1)
        torch.nn.init.normal_(head.bias, std=0.1)

    proj = ForwardProjector(SpeedProfile(player_x=0.0))
    out = proj.project([_block(200.0, 0.0), _block(320.0, 60.0, kind=ObjectKind.SOLID)])
    obs = TrajectoryRaster(height=16, width=24).render(out, 0.0, 0.0)[None]

    from conditioning import Vehicle

    cube = trunk(obs, **regimes_to_tensors([Regime(vehicle=Vehicle.CUBE)]))
    ship = trunk(obs, **regimes_to_tensors([Regime(vehicle=Vehicle.SHIP)]))
    assert cube.shape == ship.shape
    assert (cube - ship).abs().max().item() > 1e-3


def test_identical_geometry_different_futures_gives_different_observations():
    """The complement of conditioning.py's central test. There, one observation
    read eight ways by the regime. Here, one *static* level read two ways by
    the trigger state -- which is the failure a static map cannot represent at
    all, no matter how the trunk is conditioned."""
    obj = _block(600.0, 0.0, groups=(5,))
    rising = GroupCommand(
        target_group_id=5, duration=2.0, delta_time_in_float=0.0,
        action_type_1=int(ActionType.OFFSET_Y), action_value_1=300.0,
    )
    proj = ForwardProjector(SpeedProfile(player_x=0.0), horizon_ticks=900)
    r = TrajectoryRaster(height=32, width=48)

    static_grid = r.render(proj.project([obj]), 0.0, 0.0)
    moving_grid = r.render(proj.project([obj], commands=[rising]), 0.0, 0.0)

    assert not torch.equal(static_grid, moving_grid)
    # The "now" half is identical -- the level really is the same level.
    now = slice(0, NUM_KINDS)
    assert torch.equal(static_grid[now], moving_grid[now])
