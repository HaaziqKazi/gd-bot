"""Offline tests for practice_determinism.py's own verdict logic.

These do NOT touch the game, the shared segment, or GDRL_PRACTICE at all --
compare_trajectories() is a pure function over two lists of Step, and this
file's whole job is to make sure the function that will decide PASS/FAIL on
the one live run that matters is not itself buggy. A bug here would produce a
false PASS or a false FAIL against real telemetry, which is a worse outcome
than not having this acceptance test at all.

Tier (i), regression-only, same as every other loopback test in this repo --
see test_env.py's and test_practice.py's own docstrings for the same caveat.
This proves the comparator is internally consistent, not that
mod/src/telemetry.cpp's checkpoint restore is deterministic; only a live run
through scripts/practice_determinism.sh proves that.

Run: cd trainer && python3 -m pytest test_practice_determinism.py -q
"""

from __future__ import annotations

from practice_determinism import Step, compare_trajectories


def make_step(tick: int, x: float = 100.0, y: float = 105.0, **overrides) -> Step:
    base = dict(
        tick=tick, attempt=1, x=x, y=y, y_velocity=0.0, rotation=0.0,
        is_on_ground=1, attempt_time=tick / 240.0, resume_tick=40,
        checkpoint_tick=40, has_checkpoint=True, practice_mode=True,
        status="OK",
    )
    base.update(overrides)
    return Step(**base)


def make_traj(ticks: range, **overrides) -> list[Step]:
    return [make_step(t, x=100.0 + t * 1.298250437, **overrides) for t in ticks]


def test_identical_trajectories_are_bit_identical():
    a = make_traj(range(40, 60))
    b = make_traj(range(40, 60))
    result = compare_trajectories(a, b)
    assert result["bit_identical"] is True
    assert result["first_divergence"] is None
    assert result["length_mismatch"] is False


def test_a_single_differing_field_is_caught_as_state_divergence():
    a = make_traj(range(40, 45))
    b = make_traj(range(40, 45))
    b[3] = make_step(b[3].tick, x=b[3].x + 0.5)   # nudge one x value
    result = compare_trajectories(a, b)
    assert result["bit_identical"] is False
    assert result["first_divergence"]["kind"] == "state_divergence"
    assert result["first_divergence"]["index"] == 3
    assert result["first_divergence"]["position_error_units"] == 0.5


def test_a_tick_offset_is_reported_as_timing_not_position():
    """This repo's own rule: a one-tick offset and a genuine position error
    are different defects and must be distinguishable in the report."""
    a = make_traj(range(40, 50))
    b = make_traj(range(41, 51))   # everything shifted one tick late
    result = compare_trajectories(a, b)
    assert result["bit_identical"] is False
    assert result["first_divergence"]["kind"] == "tick_offset"
    assert result["first_divergence"]["index"] == 0
    assert result["first_divergence"]["baseline_tick"] == 40
    assert result["first_divergence"]["other_tick"] == 41
    # A tick_offset divergence must not also claim a position error -- that
    # would conflate the two defects this test exists to keep separate.
    assert "position_error_units" not in result["first_divergence"]


def test_yvelocity_alone_diverging_is_caught_even_with_identical_positions():
    """Position can agree at a tick while velocity (hence next tick's
    position) does not yet -- this must not be reported as bit-identical."""
    a = make_traj(range(40, 45), y_velocity=-5.0)
    b = make_traj(range(40, 45), y_velocity=-5.0)
    b[2] = make_step(b[2].tick, x=b[2].x, y_velocity=-4.9)
    result = compare_trajectories(a, b)
    assert result["bit_identical"] is False
    assert result["first_divergence"]["kind"] == "state_divergence"
    assert result["first_divergence"]["position_error_units"] == 0.0


def test_attempt_time_alone_diverging_is_caught():
    """The backlog-item-8 case: position agrees but the clock does not."""
    a = make_traj(range(40, 45))
    b = make_traj(range(40, 45))
    b[1] = make_step(b[1].tick, x=b[1].x, attempt_time=b[1].attempt_time + 1e-9)
    result = compare_trajectories(a, b)
    assert result["bit_identical"] is False
    assert result["first_divergence"]["kind"] == "state_divergence"
    assert result["first_divergence"]["tick"] == b[1].tick


def test_a_shorter_trajectory_is_a_length_mismatch_even_if_the_overlap_agrees():
    """One leg dying earlier than another is itself non-determinism and must
    not be masked by only comparing the shared prefix."""
    a = make_traj(range(40, 60))
    b = make_traj(range(40, 55))
    result = compare_trajectories(a, b)
    assert result["length_mismatch"] is True
    assert result["bit_identical"] is False   # length_mismatch alone fails it
    # ...even though every tick present in both agrees.
    assert result["first_divergence"] is None


def test_max_position_error_is_tracked_up_to_the_first_divergence():
    a = make_traj(range(0, 5))
    b = [make_step(t, x=a[i].x + 0.1 * i) for i, t in enumerate(range(0, 5))]
    result = compare_trajectories(a, b)
    assert result["first_divergence"]["index"] == 1   # first nonzero nudge
    assert result["max_position_error_units_before_divergence"] == 0.0


def test_empty_trajectories_are_trivially_bit_identical():
    result = compare_trajectories([], [])
    assert result["bit_identical"] is True
    assert result["length_mismatch"] is False
