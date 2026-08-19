"""Tests for the A-legal search driver.

EVIDENTIARY TIER, STATED UP FRONT
---------------------------------

Everything in this file is **tier (i) -- regression only**, with one structural
exception noted below. The driver is exercised against ``sightread_toy``, a
puppet that speaks the protocol and runs the crudest possible cube. Nothing here
is evidence about Geometry Dash, and two things in particular are circular by
construction and must never be cited otherwise:

* **Hold auto-repeat.** The puppet re-jumps on landing while the button is held
  because the driver is designed on the bet that GD does. A green test says the
  driver exploits auto-repeat *if* it exists. It says nothing about whether it
  exists. That measurement needs the game.
* **Solvability.** The puppet's level was written to be solvable by holds, so
  "the search solved it" measures the search against a level chosen to suit it.

The exception: the A-legality tests are not about the game at all. They assert a
property of this Python -- that a policy-facing view cannot return a field the
observation contract forbids -- and that property is exactly as true here as it
would be against the mod, because the same class does the withholding.

Run: cd trainer && python3 -m pytest test_sightread.py -q
"""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
import threading
import time

import numpy as np
import pytest

import schema_generated as sg
import sightread as sr
import sightread_toy as toy
from env import Channel, SyntheticGame, make_loopback_buffer
from schema_generated import GdrlActionKind


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _fast_thread_switching():
    """Stop the GIL switch interval from dominating every timing here.

    Two Python threads ping-ponging are bounded by the switch interval, which at
    the 5 ms default makes an attempt of a few hundred ticks take seconds. This
    is a property of the test harness, not of the protocol -- see
    ``sightread.bench_loopback``, which measures the difference rather than
    asserting it.
    """
    old = sys.getswitchinterval()
    sys.setswitchinterval(0.00002)
    yield
    sys.setswitchinterval(old)


@pytest.fixture()
def one_frame():
    """A single decoded observation from the plain fixture."""
    buf = make_loopback_buffer()
    game = SyntheticGame(buf)
    chan = Channel(buf)
    chan.attach()
    game.publish(tick=12, player_x=500.0, player_y=105.0,
                 objects=[{"x": 560.0, "y": 105.0, "objectType": 2},
                          {"x": 600.0, "y": 105.0, "objectType": 0}])
    obs = chan.poll(timeout=1.0)
    yield obs, game, chan
    chan.detach()
    buf.close()


@pytest.fixture()
def played():
    """A live toy game with an attached channel, ledger and runner."""
    buf = make_loopback_buffer()
    game = toy.ToyGame(buf)
    chan = Channel(buf)
    chan.attach()
    game.start(max_seconds=120.0)
    ledger = sr.AttemptLedger()
    runner = sr.Runner(chan, ledger, timeout=10.0)
    yield game, runner, ledger
    game.stop()
    chan.detach()
    buf.close()


# ---------------------------------------------------------------------------
# A-legality: the forbidden fields are ABSENT, not merely unread
# ---------------------------------------------------------------------------

def test_sight_does_not_carry_the_raw_record(one_frame):
    """The view must not keep a reference the policy could walk back through.

    A wrapper that holds the Observation is a documentation-tier constraint
    wearing a class: ``sight._obs.record['validity']['objectCountTotal']`` still
    works. This asserts the bytes are gone.
    """
    obs, _, _ = one_frame
    sight = sr.Sight.decode(obs)
    for attr in ("record", "header", "validity", "_obs", "obs", "observation"):
        assert not hasattr(sight, attr), f"Sight exposes {attr}"


@pytest.mark.parametrize("name", sorted(sr.FORBIDDEN_FIELDS))
def test_forbidden_fields_refuse_loudly(one_frame, name):
    """Refuse, never return something plausible.

    ``KnownMask``'s posture, applied to the contract: a benchmark violation that
    returns None or 0 is indistinguishable downstream from a legitimate
    observation, which is the failure mode this repo keeps rediscovering.
    """
    obs, _, _ = one_frame
    sight = sr.Sight.decode(obs)
    with pytest.raises(sr.ForbiddenField) as exc:
        getattr(sight, name)
    assert name in str(exc.value)
    # The refusal must carry the reason, not just the verdict.
    assert len(str(exc.value)) > 80


def test_experimenter_only_fields_refuse_with_a_different_reason(one_frame):
    obs, _, _ = one_frame
    sight = sr.Sight.decode(obs)
    with pytest.raises(sr.ForbiddenField, match="not to the policy"):
        sight.sectionXFactor
    with pytest.raises(sr.ForbiddenField, match="not to the policy"):
        sight.inResetDelay


def test_an_unknown_attribute_is_still_an_attribute_error(one_frame):
    """The refusal must not swallow ordinary typos into a benchmark complaint."""
    obs, _, _ = one_frame
    sight = sr.Sight.decode(obs)
    with pytest.raises(AttributeError):
        sight.playerXX


def test_the_objects_table_physically_lacks_the_forbidden_columns(one_frame):
    obs, _, _ = one_frame
    sight = sr.Sight.decode(obs)
    names = set(sight.objects().dtype.names)
    assert "groups" not in names
    assert "groupCount" not in names
    # And the wire really did have them, so this is a removal rather than a
    # coincidence of the fixture.
    assert "groups" in set(obs.record["objects"].dtype.names)
    # numpy raises ValueError for a missing structured field, not KeyError.
    with pytest.raises((KeyError, ValueError)):
        sight.objects()["groups"]


def test_allowed_object_dtype_is_a_strict_subset_of_the_wire(one_frame):
    wire = set(sg.GdrlObject_DTYPE.names)
    allowed = set(sr.ALLOWED_OBJECT_DTYPE.names)
    assert allowed < wire
    assert not (allowed & set(sr.FORBIDDEN_FIELDS))


def test_sight_keeps_the_object_values_it_is_allowed_to_keep(one_frame):
    obs, _, _ = one_frame
    sight = sr.Sight.decode(obs)
    assert len(sight.objects()) == 2
    assert sorted(float(x) for x in sight.objects()["x"]) == [560.0, 600.0]
    assert len(sight.hazards()) == 1
    assert len(sight.solids()) == 1


def test_the_horizon_is_read_from_the_header_not_from_a_constant(one_frame):
    """A driver that hardcoded 1400 would keep working while being 3.9x wrong.

    Item 1 measured the real viewport and ruled the window camera-derived, so it
    is going to change. This publishes a window nothing in the repo uses as a
    default and asserts the driver's horizon follows it.
    """
    obs, game, chan = one_frame
    chan.respond([])
    game.publish(tick=13, player_x=500.0, win_ahead=359.5, win_behind=209.5,
                 win_vert=215.0)
    sight = sr.Sight.decode(chan.poll(timeout=1.0))
    assert sight.horizon_ahead == pytest.approx(359.5, abs=1.0)
    assert sight.horizon_behind == pytest.approx(209.5, abs=1e-6)
    # And the driver must not have imported the mod's old window constants at
    # all. (env.py still defines MOD_WIN_AHEAD for its own fixture; the point is
    # that this module does not pull it in.)
    assert not hasattr(sr, "MOD_WIN_AHEAD")
    assert not hasattr(sr, "MOD_WIN_BEHIND")


def test_progress_refuses_a_fabricated_denominator(one_frame):
    obs, game, chan = one_frame
    chan.respond([])
    game.publish(tick=14, player_x=500.0, level_length=0.0)
    sight = sr.Sight.decode(chan.poll(timeout=1.0))
    assert sight.progress is None


def test_the_search_machinery_contains_no_level_constant():
    """No tick number and no x from Stereo Madness may reach the proposals.

    The acceptance criterion is "from zero hardcoded tick numbers", so the
    record itself is allowed to exist as a stopping *target* in the CLI and
    nowhere else. This checks the parts that choose actions.
    """
    for cls in (sr.CandidateSource, sr.ShipCandidateSource, sr.Sightreader,
                sr.Rhythm, sr.Plan, sr.Interval, sr.Runner,
                sr.select_candidate_source):
        src = inspect.getsource(cls)
        assert "3959" not in src, f"{cls.__name__} references the record x"
        for known_jump_tick in ("325", "712", "1074", "2878", "3048"):
            assert known_jump_tick not in src, \
                f"{cls.__name__} references hand-picked tick {known_jump_tick}"


# ---------------------------------------------------------------------------
# The action space
# ---------------------------------------------------------------------------

def test_a_hold_becomes_one_hold_action():
    plan = sr.Plan((sr.Interval(100, 40), sr.Interval(200, 8)))
    acts = plan.actions()
    assert [a.kind for a in acts] == [GdrlActionKind.HOLD, GdrlActionKind.HOLD]
    assert [(a.target_tick, a.hold_ticks) for a in acts] == [(100, 40), (200, 8)]


def test_overlapping_intervals_are_refused():
    """Two overlapping HOLDs are not the plan they look like.

    They expand to push,push,release,release and the FIRST release ends the
    chain, so the mod executes something else entirely.
    """
    with pytest.raises(ValueError, match="overlap"):
        sr.Plan((sr.Interval(100, 40), sr.Interval(120, 10)))
    with pytest.raises(ValueError, match="overlap"):
        sr.Plan((sr.Interval(100, 40), sr.Interval(140, 10)))   # touching
    sr.Plan((sr.Interval(100, 40), sr.Interval(141, 10)))       # one tick clear


def test_a_zero_length_hold_is_refused():
    with pytest.raises(ValueError, match="latches"):
        sr.Interval(100, 0)


def test_an_input_before_the_first_observable_tick_is_refused():
    with pytest.raises(ValueError, match="tick that has not happened"):
        sr.Interval(0, 10)


def test_plans_are_hashable_so_the_search_can_deduplicate():
    a = sr.Plan((sr.Interval(10, 5),))
    b = sr.Plan((sr.Interval(10, 5),))
    assert a == b and hash(a) == hash(b)
    assert len({a, b}) == 1


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------

def test_the_ledger_has_no_way_to_decrement():
    led = sr.AttemptLedger()
    assert not any(n in dir(led) for n in ("decrement", "pop", "rollback",
                                           "discard", "reset"))


def test_an_attempt_is_counted_before_it_can_fail():
    """A rollout that throws must still be on the ledger.

    The whole undercount risk is an exception path that skips the increment, so
    the record is opened before the first action reaches the wire.
    """
    led = sr.AttemptLedger()
    rec = led.open("probe", sr.Plan(()), game_attempt=3)
    assert led.count == 1
    with pytest.raises(sr.LedgerViolation):
        led.open("probe", sr.Plan(()), game_attempt=3)
    assert led.count == 1
    assert "never closed" in " ".join(led.audit())
    # Closing hands back the attempt id of the frame the runner is now holding,
    # which is the FIRST frame of the next attempt -- the game has started it and
    # nobody has used it. mark_unused is what stops that showing up as an
    # uncounted attempt, and the Runner calls it at the same moment.
    led.mark_unused(4)
    led.close(rec, game_attempt=4, max_x=1.0, end_tick=10)
    assert led.audit() == [], led.audit()
    assert led.count == 1 and led.game_attempts == 1


def test_the_audit_catches_an_attempt_the_ledger_missed():
    """The game's own counter is the second witness.

    Undercounting has to defeat header.attempt, which this file does not
    control. Here the game advanced by three and the ledger saw one.
    """
    led = sr.AttemptLedger()
    rec = led.open("probe", sr.Plan(()), game_attempt=10)
    led.close(rec, game_attempt=13, max_x=1.0, end_tick=10)
    problems = " ".join(led.audit())
    # ids 10, 11, 12 and 13 are four attempts; the ledger saw one.
    assert "4 attempts" in problems and "ledger recorded 1" in problems
    assert "3 attempt(s) were spent without being counted" in problems
    assert "Benchmark B number wearing an A label" in problems


# ---------------------------------------------------------------------------
# Running against the puppet
# ---------------------------------------------------------------------------

def test_a_rollout_is_deterministic_and_costs_exactly_one_attempt(played):
    game, runner, ledger = played
    runner.prime()
    plan = sr.Plan((sr.Interval(80, 120),))
    first = runner.rollout(plan, watch_from_tick=0)
    before = ledger.count
    second = runner.rollout(plan, watch_from_tick=0)
    assert ledger.count == before + 1
    assert first.max_x == second.max_x
    assert first.end_tick == second.end_tick
    assert game.late_events == 0, "an input was scheduled for a tick already gone"


def test_the_committed_prefix_replays_identically_at_any_stride(played):
    """Winding the observation stride up must not change the trajectory.

    This is the property the hybrid rests on: the stride is an observation
    stride, and the mod fires scheduled inputs on every physics step whether or
    not it published one. If it were not true, replaying a prefix cheaply would
    silently change what is being replayed.
    """
    game, runner, ledger = played
    runner.prime()
    plan = sr.Plan((sr.Interval(60, 200),))
    fine = runner.rollout(plan, watch_from_tick=0)          # stride 1 throughout
    coarse = runner.rollout(plan, watch_from_tick=10 ** 9)  # never drops to 1
    assert coarse.end_tick <= fine.end_tick                # coarse samples less
    # The outcome is the same trajectory; only the SAMPLING differs, so compare
    # what the coarse pass can actually speak for.
    assert coarse.max_x <= fine.max_x + 1e-9
    assert fine.max_x - coarse.max_x <= runner.max_stride * toy.TOY_UNITS_PER_TICK


def test_the_driver_measures_its_own_units_per_tick_and_hop(played):
    game, runner, ledger = played
    runner.prime()
    out = runner.rollout(sr.Plan((sr.Interval(40, 300),)), watch_from_tick=0)
    rhythm = sr.Rhythm()
    rhythm.observe(out.sights)
    assert rhythm.units_per_tick == pytest.approx(toy.TOY_UNITS_PER_TICK, abs=1e-6)
    # The puppet's hop is 2*v0/g = 48 ticks by construction. Allow a tick of
    # slack for where the landing lands relative to the sample.
    assert rhythm.airtime_ticks == pytest.approx(48, abs=2)
    assert rhythm.horizon_ticks == pytest.approx(
        toy.TOY_WIN_AHEAD / toy.TOY_UNITS_PER_TICK, rel=0.05)
    assert rhythm.unmeasured() == []


def test_an_unplaceable_plan_is_reported_and_still_counted(played):
    """A plan whose input is already in the past is not silently absorbed."""
    game, runner, ledger = played
    runner.prime()
    before = ledger.count
    out = runner.rollout(sr.Plan((sr.Interval(1, 10),)), watch_from_tick=0)
    # Two, not one: attaching lands mid-attempt, so the FIRST rollout of a
    # session also spends a `sync` attempt running the in-flight one out. That
    # attempt is real and is on the ledger, which is the entire point -- a
    # driver that hid the run-up would report a smaller number than it played.
    assert ledger.count == before + 2
    assert [r.purpose for r in ledger.records[-2:]] == ["sync", "probe"]
    if not out.ok:
        assert "before the first observable tick" in out.unrunnable


# ---------------------------------------------------------------------------
# The search
# ---------------------------------------------------------------------------

def _walk(node):
    yield node
    for c in node.children:
        yield from _walk(c)


def test_the_search_gets_past_the_first_hazard_and_counts_every_probe(played):
    game, runner, ledger = played
    search = sr.Sightreader(runner, budget=45, target_x=None, verbose=False)
    report = search.run()

    first_hazard_x = game.level.hazards[0].x
    assert report.best_x > first_hazard_x, (
        f"never cleared the first hazard at x={first_hazard_x}: {report.text()}")
    assert report.attempts == len(report.records)
    assert report.attempts <= 45
    assert report.audit == [], report.audit
    assert report.game_attempts == report.attempts, (
        "the ledger and the game disagree about attempts played")


def test_every_probe_including_the_failures_is_on_the_record(played):
    game, runner, ledger = played
    search = sr.Sightreader(runner, budget=20, target_x=None, verbose=False)
    report = search.run()
    # Failures outnumber successes in any real search; the point is that they
    # are all here, with their plans, not just the ones that improved.
    assert len(report.records) == report.attempts
    improved = [r for r in report.records if r.max_x is not None]
    assert len(improved) == len(report.records)
    assert any(r.purpose == "calibrate" for r in report.records)
    assert sum(1 for r in report.records if r.purpose == "probe") >= 5
    # And a plan is never paid for twice.
    plans = [tuple((i.start_tick, i.hold_ticks) for i in r.plan.intervals)
             for r in report.records if r.purpose == "probe"]
    assert len(plans) == len(set(plans))


def test_the_search_backtracks_rather_than_only_pushing_the_frontier(played):
    """A node must be able to acquire a second child.

    A driver that only extends its best plan reproduces the old greedy stall
    exactly: the right action at decision n often only pays off given a specific
    choice at n-1. Two children of one node means the search went back and tried
    a different choice at that decision after having already committed one.
    """
    game, runner, ledger = played
    search = sr.Sightreader(runner, budget=40, target_x=None, verbose=False)
    search.run()
    assert search.root is not None
    branching = [n for n in _walk(search.root) if len(n.children) > 1]
    assert branching, "no node was ever revisited; this is a greedy driver"


def test_a_cluster_no_single_hop_clears_is_crossed_by_one_interval():
    """The interval hypothesis, tested against the puppet that assumes it.

    THIS IS CIRCULAR AS EVIDENCE ABOUT GD and is here only to show the driver
    can exploit auto-repeat when it exists: the puppet's middle pair is spaced so
    a single hop lands between them, and one held input crosses both.
    """
    buf = make_loopback_buffer()
    level = toy.ToyLevel(hazards=(toy.Hazard(x=300.0), toy.Hazard(x=352.0)),
                         length=620.0)
    game = toy.ToyGame(buf, level=level)
    chan = Channel(buf)
    chan.attach()
    game.start(max_seconds=30.0)
    runner = sr.Runner(chan, sr.AttemptLedger(), timeout=10.0)
    try:
        runner.prime()
        # One hop, started early enough to be airborne over the first of the
        # pair, and the same start held long enough to chain across both.
        start = int((300.0 - 40.0) / toy.TOY_UNITS_PER_TICK)
        single = runner.rollout(sr.Plan((sr.Interval(start, 4),)),
                                watch_from_tick=0)
        held = runner.rollout(sr.Plan((sr.Interval(start, 200),)),
                              watch_from_tick=0)
    finally:
        game.stop()
        chan.detach()
        buf.close()
    assert held.max_x > single.max_x, (
        "holding did not out-perform tapping even in a puppet built to "
        "auto-repeat; the test level is not exercising what it claims")


def test_the_search_can_finish_the_toy_level(played):
    """End to end, with a budget, on a level built to be holdable.

    A regression test for the DRIVER, not a difficulty measurement: the puppet's
    level was chosen to suit the search, and the search's own budget is the only
    thing standing between it and brute force.
    """
    game, runner, ledger = played
    search = sr.Sightreader(runner, budget=250, target_x=None, verbose=False)
    report = search.run()
    assert report.reached_end_of_level, (
        f"did not finish in {report.attempts} attempts:\n{report.text()}")
    assert report.audit == []


# ---------------------------------------------------------------------------
# Escalating backtrack: gradual depth, the no-op guard, and the wall-detection
# methods (_wall_tolerance, _at_wall, _press_was_airborne, _escalate,
# CandidateSource.widen)
#
# TIER (i), REGRESSION/MECHANISM ONLY -- and more deliberately so than
# anywhere else in this file. Most of what follows drives Sightreader against
# a FakeRunner whose "physics" is a hand-written lookup table, not
# sightread_toy's cube. That is by design, not a shortcut: the property under
# test is a property of the SEARCH (does escalation reach the tick position a
# fix needs; does depth grow gradually; does the candidate generator ever
# schedule a press after its own incumbent already died) -- not of any
# physics, real or toy. A FakeRunner makes "the wall" and "the fix" exact,
# chosen numbers instead of something coaxed out of a jump arc, which is what
# lets these tests assert on the search's OWN bookkeeping (backtrack depth,
# which nodes got blocked) rather than only on whether x went up. None of
# this is evidence about Geometry Dash, or even about sightread_toy's puppet.
#
# The live numbers these tests are shaped by (orchestrator, 2026-08-18,
# real-game runs against the committed wall-detection code before this file's
# gradual-patience change): deepest backtrack before death 87 ticks (needed
# ~198), 49/236 post-frontier probes had a start tick at or after the
# incumbent's own death tick (pure waste), and -- after `_escalate` was
# confirmed to fire correctly -- median divergence tick dropped from 2292 to
# 340 and minimum divergence to 3, i.e. flat per-depth patience made
# `_escalate` overshoot almost to the plan's root on nearly every wall,
# discarding an ancestor's own not-yet-exhausted widened search before it had
# a fair chance and never finding a fix either way (best_x unchanged, 3071.5
# vs 3069.8).
# ---------------------------------------------------------------------------

def _mk_sight(tick: int, *, is_on_ground: bool, player_x: float = 0.0) -> sr.Sight:
    """A fabricated Sight with only the fields these tests read set
    meaningfully -- there is no wire record here, so nothing is decoded."""
    return sr.Sight(
        tick=tick, attempt_time=tick / 240.0, attempt=1, flags=0,
        time_warp=1.0, dt_per_step=1.0,
        player_x=player_x, player_y=105.0, player_speed=1.0,
        window_min_x=0.0, window_max_x=200.0, window_min_y=0.0, window_max_y=200.0,
        level_length=2000.0, section_columns=1, coverage_start_col=0,
        object_count=0, objects_dropped=0, is_dual_mode=False,
        player=sr.PlayerSense(
            x=player_x, y=105.0, y_velocity=0.0, gravity=1.0, rotation=0.0,
            vehicle_size=1.0, player_speed=1.0, vehicle_flags=0,
            is_upside_down=False, is_sideways=False, is_on_ground=is_on_ground,
            is_dashing=False, present=True,
        ),
        player2_present=False,
        coverage=np.zeros(1, dtype=bool),
        _objects=np.zeros(0, dtype=[("x", "f4")]),
        unavailable=(),
    )


def _mk_outcome(plan: sr.Plan, *, max_x: float, end_tick: int, first_tick: int = 2,
                sights=(), reached_end: bool = False,
                attempt_index: int = 0) -> sr.Outcome:
    return sr.Outcome(
        attempt_index=attempt_index, game_attempt=1, plan=plan,
        max_x=max_x, max_x_tick=end_tick, max_x_stride=1, end_tick=end_tick,
        first_tick=first_tick, sights=tuple(sights),
        reached_end_of_level=reached_end, unrunnable=None, wall_seconds=0.0,
    )


class _FakeRunner:
    """A ``Runner`` whose physics is a lookup, not a simulation.

    ``outcome_fn(plan)`` returns the kwargs for ``_mk_outcome`` (everything
    but ``plan``/``attempt_index``) for that exact plan -- the ground truth a
    test is built around, instead of something a jump arc happens to produce.
    Real ``AttemptLedger`` bookkeeping is preserved (open/close on every
    call) so ``Sightreader``'s budget loop, ledger count, and audit behave
    exactly as they do against the real ``Runner``.
    """

    def __init__(self, outcome_fn):
        self.ledger = sr.AttemptLedger()
        self._outcome_fn = outcome_fn
        self._attempt = 0
        self.fatal: list = []

    def prime(self) -> None:
        pass

    def rollout(self, plan: sr.Plan, *, purpose: str = "probe",
                watch_from_tick: int = 0) -> sr.Outcome:
        self._attempt += 1
        rec = self.ledger.open(purpose, plan, self._attempt)
        kwargs = self._outcome_fn(plan)
        self.ledger.close(rec, game_attempt=self._attempt, max_x=kwargs["max_x"],
                          end_tick=kwargs["end_tick"], note="", wall_seconds=0.0)
        return _mk_outcome(plan, attempt_index=rec.index, **kwargs)


def test_gradual_escalation_reaches_a_fix_flat_patience_permanently_loses():
    """The failing case this whole section exists to close.

    Manufactured plan: the ONLY working continuation is a specific second
    interval, ``I2_GOOD`` -- confirmed below to be the 6th distinct candidate
    ``CandidateSource`` offers for that slot. Every other second interval, and
    the first interval alone, die at an IDENTICAL (x, tick) "wall" with the
    press airborne at the moment it was due -- the diagnosed real shape: a
    committed interval leaves the puppet unable to react locally (every local
    press after it is issued mid-air and does nothing), and only revising
    THAT interval, not appending past it, can change the outcome.

    This is unsolvable by ``_at_wall``/``_press_was_airborne`` blocking alone
    (both already correctly mark every bad I2 attempt as wasted -- that part
    of this module already worked) -- it needs `_escalate` to revise the
    FIRST interval's own node, and that node's own widened candidate menu has
    to be given enough tries to actually reach candidate #5. With
    ``wall_patience`` alone used at every depth (this module's shape before
    this change), the first interval's node is abandoned -- and permanently
    blocked -- after exactly 3 failures, one short of the fix, and the rest
    of the budget is spent on a doomed alternative first interval instead
    (this is asserted first, below, as the failing case). With patience
    scaled by ``_backtrack_depth`` (this change), the same node gets enough
    tries and the plan is found with zero escalations.
    """
    I1 = sr.Interval(4, 246)
    I2_GOOD = sr.Interval(370, 30)

    def physics(plan: sr.Plan) -> dict:
        ivs = plan.intervals
        if not ivs or ivs[0] != I1:
            # Any other choice for the first interval never gets anywhere --
            # this scenario has exactly one lever, I2, and it is not this one.
            return dict(max_x=10.0, end_tick=10, first_tick=2)
        if len(ivs) == 1:
            return dict(max_x=500.0, end_tick=400, first_tick=2)
        if ivs[1] == I2_GOOD:
            return dict(max_x=1000.0, end_tick=770, first_tick=2, reached_end=True)
        # Every other second interval: the identical wall, press airborne.
        sight = _mk_sight(ivs[1].start_tick, is_on_ground=False)
        return dict(max_x=500.0, end_tick=400, first_tick=2, sights=(sight,))

    # Precondition check on the fixture itself, not a claim about the fix:
    # confirm I2_GOOD really is candidate #5 (0-indexed) for this exact
    # (plan, outcome, rhythm), i.e. that this test is actually exercising
    # "not enough patience" and not some other accident of the generator.
    rhythm_check = sr.Rhythm()
    node1_outcome = _mk_outcome(sr.Plan((I1,)), max_x=500.0, end_tick=400)
    probe_src = sr.CandidateSource(sr.Plan((I1,)), node1_outcome, rhythm_check)
    first_six = [probe_src.next() for _ in range(6)]
    assert first_six[5] == I2_GOOD, (
        f"fixture drifted -- CandidateSource's first six candidates for this "
        f"node are now {first_six}, and I2_GOOD is not the 6th")
    assert I2_GOOD not in first_six[:5], "fixture drifted: the fix is reachable early"

    # -- FLAT patience (this module's shape before this change): fails. -----
    flat_runner = _FakeRunner(physics)
    flat_search = sr.Sightreader(flat_runner, budget=60, target_x=None,
                                 verbose=False, wall_patience=3)
    flat_search._escalate_patience = lambda: flat_search.wall_patience
    flat_report = flat_search.run()
    assert not flat_report.reached_end_of_level, (
        "fixture drifted: flat patience now finds the fix too -- this test "
        "no longer demonstrates the regression it is named for")
    assert flat_report.best_x == 500.0, (
        f"expected flat patience to get permanently stuck at the wall "
        f"(500.0); got best_x={flat_report.best_x}. "
        f"{flat_report.text()}")

    # -- Depth-scaled patience (this change): succeeds. ----------------------
    scaled_runner = _FakeRunner(physics)
    scaled_search = sr.Sightreader(scaled_runner, budget=60, target_x=None,
                                   verbose=False, wall_patience=3)
    scaled_report = scaled_search.run()
    assert scaled_report.reached_end_of_level, (
        f"depth-scaled patience did not find the fix either:\n"
        f"{scaled_report.text()}")
    assert scaled_report.best_plan.intervals == (I1, I2_GOOD)
    assert scaled_report.audit == []


def test_candidate_source_never_schedules_a_press_at_or_after_the_incumbents_death():
    """The no-op guard (orchestrator, 2026-08-18: 49/236 real post-frontier
    probes had ``start_tick >= death_tick`` before this was closed off).

    A press scheduled at or after the tick the incumbent already died on
    cannot affect anything before that death -- the run is already over. This
    checks every candidate ``CandidateSource`` can produce, before AND after
    ``widen()`` (which regenerates the sweep with a wider span and could, in
    principle, reintroduce the edge if the cap were not re-applied every
    regeneration).
    """
    rhythm = sr.Rhythm()
    outcome = _mk_outcome(sr.Plan(()), max_x=500.0, end_tick=400, first_tick=2)
    src = sr.CandidateSource(sr.Plan(()), outcome, rhythm)

    seen = []
    for _ in range(400):
        iv = src.next()
        if iv is None:
            break
        seen.append(iv)
    assert len(seen) > 50, "fixture drifted: too few candidates to be a real check"
    offenders = [iv for iv in seen if iv.start_tick >= outcome.end_tick]
    assert offenders == [], (
        f"{len(offenders)} candidate(s) scheduled at or after the incumbent's "
        f"own death tick ({outcome.end_tick}): {offenders[:5]}")

    src.widen()
    src.widen()
    more = []
    for _ in range(400):
        iv = src.next()
        if iv is None:
            break
        more.append(iv)
    offenders_after_widen = [iv for iv in more if iv.start_tick >= outcome.end_tick]
    assert offenders_after_widen == [], (
        f"widen() reintroduced a no-op candidate: {offenders_after_widen[:5]}")


def test_a_node_with_no_room_before_death_yields_nothing_even_when_flush():
    """Boundary of the guard above: ``earliest_start == death_tick`` (not just
    ``>``) must also yield nothing -- the only tick left is the one nothing
    can be scheduled AT.  Regression for the ``>`` -> ``>=`` edit in
    ``_generate``'s backtrack-not-loop guard.
    """
    rhythm = sr.Rhythm()
    plan = sr.Plan((sr.Interval(10, 40),))     # ends at tick 50
    outcome = _mk_outcome(plan, max_x=100.0, end_tick=51, first_tick=2)  # earliest_start = 51
    src = sr.CandidateSource(plan, outcome, rhythm)
    assert src.next() is None


def test_escalate_patience_grows_with_backtrack_depth():
    """``_escalate_patience`` -- the mechanism behind gradual escalation.

    Must strictly increase with ``_backtrack_depth`` (deeper, more expensive
    revisions require more accumulated evidence before being tried), and must
    equal ``wall_patience`` itself at NO depth -- flat patience at every depth
    is exactly the regression the test above reproduces.
    """
    runner = _FakeRunner(lambda plan: dict(max_x=0.0, end_tick=10))
    search = sr.Sightreader(runner, budget=10, target_x=None, verbose=False,
                            wall_patience=3)
    seen = []
    for depth in range(1, 6):
        search._backtrack_depth = depth
        seen.append(search._escalate_patience())
    assert seen == sorted(seen) and len(set(seen)) == len(seen), (
        f"_escalate_patience is not strictly increasing with depth: {seen}")
    assert all(p > search.wall_patience for p in seen), (
        f"_escalate_patience should exceed the flat wall_patience at every "
        f"depth >= 1 (that flatness is the bug this change closes): {seen} "
        f"vs wall_patience={search.wall_patience}")


def test_escalate_climbs_blocks_and_widens_exactly_the_claimed_nodes():
    """``_escalate`` directly, against a hand-built 4-node chain
    root -> n1 -> n2 -> n3, bypassing any physics.

    Climbing 2 levels from ``best_node=n3`` must reach ``n1``; every node
    strictly between (``n3`` and ``n2``, inclusive of ``n3``) must end up
    blocked and exhausted; ``n1`` (the reopened ancestor) must end up
    unblocked, widened once, and back in the heap; and the bookkeeping
    (``escalations``, ``_backtrack_depth``, the strike counters) must reflect
    exactly one escalation.
    """
    rhythm = sr.Rhythm()
    runner = _FakeRunner(lambda plan: dict(max_x=0.0, end_tick=10))
    search = sr.Sightreader(runner, budget=10, target_x=None, verbose=False)

    def mk(parent, plan, x):
        outcome = _mk_outcome(plan, max_x=x, end_tick=10)
        node = sr.Node(plan=plan, outcome=outcome, parent=parent,
                       depth=(parent.depth + 1 if parent else 0),
                       source=sr.CandidateSource(plan, outcome, rhythm),
                       subtree_best_x=x)
        if parent is not None:
            parent.children.append(node)
        return node

    root = mk(None, sr.Plan(()), 0.0)
    n1 = mk(root, sr.Plan((sr.Interval(4, 10),)), 100.0)
    n2 = mk(n1, sr.Plan((sr.Interval(4, 10), sr.Interval(20, 10))), 100.0)
    n3 = mk(n2, sr.Plan((sr.Interval(4, 10), sr.Interval(20, 10), sr.Interval(40, 10))),
            100.0)

    search.root = root
    search.best_node = n3
    search.best = n3.outcome
    search._backtrack_depth = 2
    search._wall_strikes = 5
    search._airborne_strikes = 0
    widen_calls_before = n1.source.widen_level

    search._escalate()

    assert n3.blocked and n3.exhausted
    assert n2.blocked and n2.exhausted
    assert not n1.blocked and not n1.exhausted, (
        "the reopened ancestor must not itself be left blocked")
    assert root.blocked is False and root.exhausted is False, (
        "escalate must not touch anything above the reopened ancestor"
    )
    assert n1.source.widen_level == widen_calls_before + 1
    assert search.escalations == 1
    assert search._backtrack_depth == 3, "depth must grow by exactly one per call"
    assert search._wall_strikes == 0 and search._airborne_strikes == 0
    assert any(node is n1 for _, _, node in search._heap), (
        "the reopened ancestor must be back in the heap")


def test_escalate_caps_at_the_root_rather_than_erroring():
    """Requesting a backtrack deeper than the tree is tall must land on the
    root, not crash or silently do nothing -- "unbounded depth as a
    capability" (orchestrator) still has to terminate somewhere real.
    """
    rhythm = sr.Rhythm()
    runner = _FakeRunner(lambda plan: dict(max_x=0.0, end_tick=10))
    search = sr.Sightreader(runner, budget=10, target_x=None, verbose=False)
    root_outcome = _mk_outcome(sr.Plan(()), max_x=0.0, end_tick=10)
    root = sr.Node(plan=sr.Plan(()), outcome=root_outcome, parent=None, depth=0,
                   source=sr.CandidateSource(sr.Plan(()), root_outcome, rhythm),
                   subtree_best_x=0.0)
    child_plan = sr.Plan((sr.Interval(4, 10),))
    child_outcome = _mk_outcome(child_plan, max_x=50.0, end_tick=10)
    child = sr.Node(plan=child_plan, outcome=child_outcome, parent=root, depth=1,
                    source=sr.CandidateSource(child_plan, child_outcome, rhythm),
                    subtree_best_x=50.0)
    root.children.append(child)

    search.root = root
    search.best_node = child
    search.best = child.outcome
    search._backtrack_depth = 500        # absurdly deep, tree is only 2 nodes
    search._escalate()

    assert child.blocked and child.exhausted
    assert root.blocked is False and root.exhausted is False
    assert any(node is root for _, _, node in search._heap)


def test_wall_tolerance_is_derived_from_the_measured_hop_not_a_constant():
    """``_wall_tolerance`` scales with measured unit and units-per-tick; it
    is not a fixed number and must move when either measurement does."""
    runner = _FakeRunner(lambda plan: dict(max_x=0.0, end_tick=10))
    search = sr.Sightreader(runner, budget=5, target_x=None, verbose=False)
    search.rhythm.units_per_tick = 2.0
    search.rhythm.airtime_ticks = 100
    tol_a = search._wall_tolerance()
    assert tol_a == pytest.approx(0.05 * 100 * 2.0)

    search.rhythm.airtime_ticks = 40
    tol_b = search._wall_tolerance()
    assert tol_b == pytest.approx(0.05 * 40 * 2.0)
    assert tol_b != tol_a, "tolerance must move when the measured hop does"


def test_at_wall_is_silent_until_three_deaths_cluster_then_fires_on_a_match():
    """``_at_wall`` -- silent (False) before Memory has >= 3 clustered deaths
    at the current best x, then True only for a death within tolerance of
    that x, never for a death somewhere else entirely."""
    runner = _FakeRunner(lambda plan: dict(max_x=0.0, end_tick=10))
    search = sr.Sightreader(runner, budget=5, target_x=None, verbose=False)
    search.rhythm.units_per_tick = 1.3
    search.rhythm.airtime_ticks = 48

    wall_x = 500.0
    def death(x, tick, i):
        return _mk_outcome(sr.Plan((sr.Interval(4, 10),)), max_x=x, end_tick=tick,
                           attempt_index=i)

    # Two deaths at the wall: not yet a confirmed wall.
    search.memory.remember(death(wall_x, 400, 1))
    search.memory.remember(death(wall_x, 400, 2))
    assert search._at_wall(death(wall_x, 400, 3)) is False

    # Third death completes the cluster.
    search.memory.remember(death(wall_x, 400, 3))
    assert search._at_wall(death(wall_x, 400, 4)) is True

    # A death at a genuinely different x is not "the wall", even now.
    assert search._at_wall(death(wall_x - 50.0, 350, 5)) is False


def test_press_was_airborne_true_false_and_unknown():
    """``_press_was_airborne`` -- the three-valued signal, all three checked:
    grounded (False), airborne (True), and outside the watched tail (None,
    which must never be conflated with "grounded" -- that is exactly the
    "measurement changed, not the simulation" mistake this repo has a rule
    about)."""
    runner = _FakeRunner(lambda plan: dict(max_x=0.0, end_tick=10))
    search = sr.Sightreader(runner, budget=5, target_x=None, verbose=False)
    iv = sr.Interval(200, 10)

    grounded_out = _mk_outcome(sr.Plan(()), max_x=0.0, end_tick=10,
                               sights=(_mk_sight(200, is_on_ground=True),))
    assert search._press_was_airborne(grounded_out, iv) is False

    airborne_out = _mk_outcome(sr.Plan(()), max_x=0.0, end_tick=10,
                               sights=(_mk_sight(200, is_on_ground=False),))
    assert search._press_was_airborne(airborne_out, iv) is True

    unwatched_out = _mk_outcome(sr.Plan(()), max_x=0.0, end_tick=10,
                                sights=(_mk_sight(150, is_on_ground=True),
                                        _mk_sight(199, is_on_ground=False)))
    assert search._press_was_airborne(unwatched_out, iv) is None


def test_candidate_source_widen_broadens_span_and_holds_without_forgetting():
    """``CandidateSource.widen`` -- span doubles, the hold menu gains a
    coarser AND a finer multiplier every call, ``widen_level`` counts calls,
    and already-tried candidates stay filtered (``next()`` never repeats
    one) even though ``_generate()`` restarts from candidate 0 internally."""
    rhythm = sr.Rhythm()
    outcome = _mk_outcome(sr.Plan(()), max_x=0.0, end_tick=400, first_tick=2)
    src = sr.CandidateSource(sr.Plan(()), outcome, rhythm)

    span0 = src.max_span_horizons
    mults0 = list(src._hold_mults)
    already = {src.next() for _ in range(5)}

    src.widen()
    assert src.widen_level == 1
    assert src.max_span_horizons == pytest.approx(span0 * 2.0)
    assert max(src._hold_mults) > max(mults0)
    assert min(src._hold_mults) < min(mults0)

    seen_after = []
    for _ in range(30):
        iv = src.next()
        if iv is None:
            break
        seen_after.append(iv)
    assert already.isdisjoint(seen_after), (
        "widen()'s regenerated sweep re-yielded an already-tried candidate")

    src.widen()
    assert src.widen_level == 2


# ---------------------------------------------------------------------------
# Ship candidate generation (TODO Q5)
#
# TIER (i), REGRESSION ONLY -- and more so than the rest of this file. There is
# no ship puppet: `sightread_toy.py` simulates only a cube's ground-contact hop,
# so nothing here plays a ship or measures whether ShipCandidateSource's guesses
# actually clear anything. These tests check three narrow, code-level claims:
# the vehicle switch reads real wire bytes correctly, the ship generator
# produces well-formed Intervals without touching the cube-only hop constant,
# and CandidateSource itself was not touched by any of it. Whether the ship
# generator's PRIOR (thrust scaled by sensor horizon rather than hop period) is
# any good is unmeasured and stays unmeasured until this runs against a live
# ship section.
# ---------------------------------------------------------------------------

def _wire_sight(game, chan, *, tick: int, player_x: float,
                vehicle_flags: int = 0) -> sr.Sight:
    """Publish one frame with a chosen ``vehicleFlags`` word and decode it.

    Goes through the real wire path (``SyntheticGame`` -> ``Channel`` ->
    ``Sight.decode``), the same route the ``one_frame`` fixture uses, rather
    than fabricating a ``Sight`` in memory -- this exercises the actual
    ``PlayerSense.vehicle_flags`` plumbing the driver depends on, not a
    hand-built stand-in for it. ``game.publish`` always writes
    ``vehicleFlags=0`` (env.py), so the field is poked afterward, the same
    pattern ``test_env.py`` uses for fields ``publish`` has no parameter for.
    """
    game.publish(tick=tick, player_x=player_x)
    game.obs["players"][0]["vehicleFlags"] = int(vehicle_flags)
    return sr.Sight.decode(chan.poll(timeout=1.0))


def _outcome(*, sights: tuple = (), end_tick: int = 500, first_tick: int = 2,
             max_x: float = 100.0) -> sr.Outcome:
    """A bare :class:`sr.Outcome` for exercising candidate sources directly,
    without spending a real attempt through :class:`sr.Runner`."""
    return sr.Outcome(
        attempt_index=1, game_attempt=1, plan=sr.Plan(()),
        max_x=max_x, max_x_tick=end_tick, max_x_stride=1,
        end_tick=end_tick, first_tick=first_tick, sights=tuple(sights),
        reached_end_of_level=False, unrunnable=None, wall_seconds=0.01,
    )


def test_the_vehicle_switch_reads_the_last_observed_sight():
    """Selection follows the LAST sight in the tail, decoded off real wire bytes."""
    buf = make_loopback_buffer()
    game = SyntheticGame(buf)
    chan = Channel(buf)
    chan.attach()
    try:
        cube_sight = _wire_sight(game, chan, tick=10, player_x=100.0,
                                 vehicle_flags=0)
        ship_sight = _wire_sight(game, chan, tick=11, player_x=101.3,
                                 vehicle_flags=int(sg.GdrlVehicleFlag.SHIP))
    finally:
        chan.detach()
        buf.close()

    rhythm = sr.Rhythm()
    cube_src = sr.select_candidate_source(
        sr.Plan(()), _outcome(sights=(cube_sight,)), rhythm)
    ship_src = sr.select_candidate_source(
        sr.Plan(()), _outcome(sights=(ship_sight,)), rhythm)
    # A tail ending cube-then-ship must follow the LAST sight, not the first.
    mixed_src = sr.select_candidate_source(
        sr.Plan(()), _outcome(sights=(cube_sight, ship_sight)), rhythm)
    reverted_src = sr.select_candidate_source(
        sr.Plan(()), _outcome(sights=(ship_sight, cube_sight)), rhythm)

    assert type(cube_src) is sr.CandidateSource
    assert type(ship_src) is sr.ShipCandidateSource
    assert type(mixed_src) is sr.ShipCandidateSource
    assert type(reverted_src) is sr.CandidateSource


def test_the_vehicle_switch_defaults_to_cube_with_no_sights():
    """An outcome with no stride-1 tail (e.g. unrunnable) cannot know the
    vehicle; it must fall back to the pre-existing cube behaviour, not guess."""
    rhythm = sr.Rhythm()
    src = sr.select_candidate_source(sr.Plan(()), _outcome(sights=()), rhythm)
    assert type(src) is sr.CandidateSource


def test_other_vehicles_also_default_to_cube():
    """Only SHIP switches generators. Ball/UFO/wave/robot/spider/swing are
    unaddressed by this task (TODO Q5 asks for ship only) and must keep
    getting the cube generator rather than a guessed one."""
    buf = make_loopback_buffer()
    game = SyntheticGame(buf)
    chan = Channel(buf)
    chan.attach()
    try:
        ball_sight = _wire_sight(game, chan, tick=10, player_x=100.0,
                                 vehicle_flags=int(sg.GdrlVehicleFlag.BALL))
    finally:
        chan.detach()
        buf.close()
    rhythm = sr.Rhythm()
    src = sr.select_candidate_source(
        sr.Plan(()), _outcome(sights=(ball_sight,)), rhythm)
    assert type(src) is sr.CandidateSource


def test_ship_generator_proposes_sane_candidates_without_a_hop_constant():
    """The ship generator must produce legal, in-range Intervals even when
    ``rhythm.airtime_ticks`` has never been measured -- the realistic ship
    case, since a ship in flight does not cycle ground->air->ground."""
    rhythm = sr.Rhythm()
    rhythm.units_per_tick = 1.298250437
    rhythm.horizon_ticks = 154
    assert rhythm.airtime_ticks is None    # never observed a ground contact

    outcome = _outcome(end_tick=500, first_tick=2)
    src = sr.ShipCandidateSource(sr.Plan(()), outcome, rhythm)

    seen = []
    for _ in range(20):
        iv = src.next()
        if iv is None:
            break
        seen.append(iv)
    assert len(seen) >= 10
    for iv in seen:
        assert iv.start_tick >= 4          # first_tick + 2
        assert iv.hold_ticks >= 1
        assert iv.start_tick <= outcome.end_tick + rhythm.horizon
    # No duplicate proposals from one generator.
    assert len(set(seen)) == len(seen)
    # The sweep's hold-length menu (everything past candidate 0, the
    # sustained-thrust probe) is scaled by the sensor horizon (154), not by
    # the cube fallback hop (FALLBACK_AIRTIME_TICKS = 60): the longest swept
    # hold is the full horizon, not a multiple of 60.
    assert max(iv.hold_ticks for iv in seen[1:]) == 154
    assert 60 not in (iv.hold_ticks for iv in seen), (
        "a hold of exactly the cube fallback hop length appeared -- suspicious "
        "reuse of FALLBACK_AIRTIME_TICKS")


def test_ship_generator_first_candidate_is_sustained_thrust():
    """Candidate 0 is the cheapest probe of "hold is continuous thrust": press
    from the earliest legal tick, held past the death tick -- the ship
    analogue of CandidateSource's own candidate 0."""
    rhythm = sr.Rhythm()
    rhythm.horizon_ticks = 100
    outcome = _outcome(end_tick=500, first_tick=2)
    src = sr.ShipCandidateSource(sr.Plan(()), outcome, rhythm)
    first = src.next()
    assert first.start_tick == 4                      # earliest_start()
    assert first.end_tick > outcome.end_tick           # spans past the death


def test_ship_generator_respects_a_committed_prefix():
    """Candidates must start after the plan's last committed interval ends,
    exactly like CandidateSource -- the ship generator reuses that rule."""
    rhythm = sr.Rhythm()
    rhythm.horizon_ticks = 100
    plan = sr.Plan((sr.Interval(10, 50),))   # ends at tick 60
    outcome = _outcome(end_tick=500, first_tick=2)
    src = sr.ShipCandidateSource(plan, outcome, rhythm)
    for _ in range(15):
        iv = src.next()
        if iv is None:
            break
        assert iv.start_tick >= 61


def test_ship_generator_widen_broadens_the_sweep():
    rhythm = sr.Rhythm()
    rhythm.horizon_ticks = 100
    outcome = _outcome(end_tick=500, first_tick=2)
    src = sr.ShipCandidateSource(sr.Plan(()), outcome, rhythm)
    before_span = src.max_span_horizons
    before_fracs = list(src._hold_fracs)
    src.widen()
    assert src.max_span_horizons == pytest.approx(before_span * 2.0)
    assert max(src._hold_fracs) > max(before_fracs)
    assert min(src._hold_fracs) < min(before_fracs)
    assert src.widen_level == 1


def test_ship_generator_yields_nothing_once_the_plan_leaves_no_room():
    """Same backtrack-not-loop guard as CandidateSource: a committed interval
    that runs past the death tick means this node is exhausted."""
    rhythm = sr.Rhythm()
    rhythm.horizon_ticks = 100
    plan = sr.Plan((sr.Interval(10, 600),))   # ends at 610, past death_tick
    outcome = _outcome(end_tick=500, first_tick=2)
    src = sr.ShipCandidateSource(plan, outcome, rhythm)
    assert src.next() is None


def test_cube_generator_is_unchanged_by_the_ship_addition():
    """Pins CandidateSource's output for a fixed input. If this ever moves, the
    ship addition (or anything else) touched the cube path, which the task
    that added ship candidate generation was explicitly told not to do.

    The pinned values were captured from this exact CandidateSource before any
    ship-related code called it, using the same (plan, outcome, rhythm) shape
    as the ship tests above so the two are a direct side-by-side contrast: cube
    holds come out {384,192,96,48,24,6} (rhythm.unit=48 x the hop multipliers),
    ship holds come out {154,77,38,19,9,4} (rhythm.horizon=154 x fractions) --
    provably different constants from provably different code paths.
    """
    rhythm = sr.Rhythm()
    rhythm.units_per_tick = 1.298250437
    rhythm.airtime_ticks = 48
    rhythm.horizon_ticks = 154
    outcome = _outcome(end_tick=500, first_tick=2)

    src = sr.CandidateSource(sr.Plan(()), outcome, rhythm)
    first5 = [(iv.start_tick, iv.hold_ticks) for iv in
              (src.next() for _ in range(5))]
    assert first5 == [(4, 688), (476, 384), (476, 192), (476, 96), (476, 48)]
    assert src._holds() == [384, 192, 96, 48, 24, 6]

    # select_candidate_source must return exactly a CandidateSource, not some
    # wrapper or subclass, when the vehicle is (or defaults to) cube.
    picked = sr.select_candidate_source(sr.Plan(()), outcome, rhythm)
    assert type(picked) is sr.CandidateSource
    picked_first = picked.next()
    assert (picked_first.start_tick, picked_first.hold_ticks) == (4, 688)


# ---------------------------------------------------------------------------
# The throughput instrument
# ---------------------------------------------------------------------------

def test_the_bench_reports_both_sides_of_the_gil_artifact():
    """The bench must show the artifact, not hide it.

    At the 5 ms default switch interval the loopback measures the scheduler, not
    the protocol. A bench that only ran at one interval would report that number
    as the protocol's cost, which is the "the measurement changed, not the
    simulation" failure this repo has a rule about.
    """
    rows = sr.bench_loopback(strides=(1,), objects=(0,), round_trips=120,
                             switch_intervals=(0.005, 0.00005), out=lambda *_: None)
    assert len(rows) == 2
    slow = [r for r in rows if r["switch_interval"] == 0.005][0]
    fast = [r for r in rows if r["switch_interval"] == 0.00005][0]
    assert fast["round_trips_per_s"] > slow["round_trips_per_s"] * 2, (
        f"the GIL artifact did not appear: {rows}. Either the bench stopped "
        "measuring it or this machine schedules differently -- either way the "
        "number in the module docstring should not be trusted until it is "
        "re-derived.")


# ---------------------------------------------------------------------------
# Surviving a live run: the wire cap, incremental persistence, a dead game
#
# These four things are named in TODO's "Things that cost runs" as having
# already cost real GD sessions. Everything below is tier (i) -- regression
# against the puppet or a bare AttemptLedger -- per this file's own posture
# statement at the top. None of it is evidence about Geometry Dash; all of it
# is evidence about whether this driver has a path that avoids repeating a
# mistake already paid for once.
# ---------------------------------------------------------------------------

def _hazard_chain_level(n: int, spacing: float = 100.0,
                        first_x: float = 150.0) -> toy.ToyLevel:
    """``n`` hazards, each independently clearable by one short hop."""
    xs = [first_x + spacing * i for i in range(n)]
    return toy.ToyLevel(hazards=tuple(toy.Hazard(x=x) for x in xs),
                        length=xs[-1] + spacing)


def _clearing_plan(level: toy.ToyLevel, hold_ticks: int = 4,
                   lead: float = 40.0) -> sr.Plan:
    """One short hop per hazard, timed the same way as the cluster test above."""
    upt = toy.TOY_UNITS_PER_TICK
    return sr.Plan(tuple(sr.Interval(int((h.x - lead) / upt), hold_ticks)
                         for h in level.hazards))


def test_a_plan_past_the_wire_cap_still_clears_every_hazard():
    """GDRL_MAX_ACTIONS=8 (schema_generated.py, checked against env.py's
    Channel.respond below): a plan needing 9 intervals must place all 9 across
    two responses, or the player dies at the hazard only the 9th interval
    clears.

    This is deliberately NOT an exception-only test. A regression that caps
    the outgoing batch at 8 and silently drops the tail (rather than looping to
    send the rest on the next response) would raise nothing -- Channel.respond
    never sees more than 8 either way -- and would still fail this test on
    ``reached_end_of_level``, because the 9th hazard is unclearable without the
    9th interval. An exception-only test would have missed exactly that bug.
    """
    assert sr.GDRL_MAX_ACTIONS == 8, (
        "the cap this file was told about does not match schema_generated.py; "
        "the rest of this test's premise (9 > cap) may no longer hold")
    level = _hazard_chain_level(9)
    buf = make_loopback_buffer()
    game = toy.ToyGame(buf, level=level)
    chan = Channel(buf)
    chan.attach()
    game.start(max_seconds=60.0)
    try:
        runner = sr.Runner(chan, sr.AttemptLedger(), timeout=10.0)
        runner.prime()
        plan = _clearing_plan(level)
        assert len(plan.intervals) == 9
        out = runner.rollout(plan, watch_from_tick=0)
    finally:
        game.stop()
        chan.detach()
        buf.close()
    assert out.unrunnable is None, out.unrunnable
    assert out.reached_end_of_level, (
        f"died before the level ended (max_x={out.max_x}); the 9th interval, "
        "past the 8-action wire cap, was most likely never scheduled")
    assert game.late_events == 0, "an input was scheduled for a tick already gone"


def test_a_plan_past_the_wire_cap_never_sends_more_than_the_cap_per_response():
    """The structural half of the claim above, verified AT THE WIRE.

    Spies on ``Channel.respond`` (the exact function TODO says raises above 8)
    to confirm both halves at once: no call ever exceeds the cap, AND the 9
    intervals are not quietly trimmed to 8 while making that true -- they sum
    to 9 across the calls that carried any.
    """
    level = _hazard_chain_level(9)
    buf = make_loopback_buffer()
    game = toy.ToyGame(buf, level=level)
    chan = Channel(buf)
    chan.attach()
    game.start(max_seconds=60.0)

    calls: list[int] = []
    real_respond = chan.respond

    def spy(actions=None, *, advance_steps=1, detach=False):
        calls.append(len(actions or []))
        return real_respond(actions, advance_steps=advance_steps, detach=detach)

    chan.respond = spy
    try:
        runner = sr.Runner(chan, sr.AttemptLedger(), timeout=10.0)
        runner.prime()
        runner.rollout(_clearing_plan(level), watch_from_tick=0)
    finally:
        game.stop()
        chan.detach()
        buf.close()

    assert calls, "the spy never saw a call; the test is not exercising anything"
    assert all(n <= sr.GDRL_MAX_ACTIONS for n in calls), calls
    assert sum(calls) == 9, (
        f"calls carried {sum(calls)} actions total, not 9 -- some intervals "
        f"were dropped rather than deferred to a later response: {calls}")


def test_attempt_ledger_on_close_fires_synchronously_with_the_closed_record():
    """The hook incremental persistence is built on, tested with no game at all.

    Nothing here touches a file or a channel -- just that ``close()`` invokes
    ``on_close`` exactly once, with the same record object, fully populated,
    before ``close()`` returns. If this is true, wiring a file writer onto it
    (below) is the only thing left to trust.
    """
    seen: list[sr.AttemptRecord] = []
    led = sr.AttemptLedger(on_close=seen.append)
    rec = led.open("probe", sr.Plan(()), game_attempt=5)
    assert seen == [], "on_close fired before the attempt even closed"
    led.close(rec, game_attempt=5, max_x=12.5, end_tick=99, note="ok",
             wall_seconds=0.01)
    assert len(seen) == 1
    assert seen[0] is rec
    assert seen[0].max_x == 12.5 and seen[0].end_tick == 99
    assert seen[0].game_attempt_at_close == 5


def test_jsonl_writer_persists_every_attempt_even_though_nothing_closed_it(
        tmp_path):
    """Durability, not just correctness.

    TODO: "env.py clients SIGSEGV when the game dies while they hold the mmap
    ... Any driver must write results incrementally, per attempt. An
    end-of-run dump is a dump that never happens. Cost two full runs." The file
    handle ``jsonl_writer`` opens is deliberately never closed by this test --
    that is the point. If the data were only visible after a clean close, a
    process that dies (or is killed) mid-run would still lose it, and this
    would not be a fix for what TODO describes.
    """
    path = tmp_path / "attempts.jsonl"
    buf = make_loopback_buffer()
    game = toy.ToyGame(buf)
    chan = Channel(buf)
    chan.attach()
    game.start(max_seconds=60.0)
    try:
        ledger = sr.AttemptLedger(on_close=sr.jsonl_writer(str(path)))
        runner = sr.Runner(chan, ledger, timeout=10.0)
        search = sr.Sightreader(runner, budget=15, target_x=None, verbose=False)
        report = search.run()
    finally:
        game.stop()
        chan.detach()
        buf.close()

    # A completely separate read of the path -- not the writer's own `fh`.
    lines = path.read_text().splitlines()
    assert len(lines) == report.attempts == ledger.count
    decoded = [json.loads(line) for line in lines]
    assert [d["index"] for d in decoded] == list(range(len(decoded)))
    # Cross-check one record against the ledger's in-memory copy: same
    # attempt, reached two independent ways (the object vs. its JSONL line).
    assert decoded[-1] == ledger.records[-1].as_dict()


def test_game_gone_is_a_run_aborted_so_existing_abort_handling_still_works():
    """Structural: GameGone must not need Sightreader.run() to learn a new
    except clause. It is a RunAborted subclass so the existing
    ``except RunAborted`` -- which already reports and stops cleanly -- covers
    it for free. A refactor that broke the subclass relationship would still
    pass every other test here and silently reopen the crash this closes."""
    assert issubclass(sr.GameGone, sr.RunAborted)


def test_a_confirmed_dead_process_is_detected_before_the_full_timeout():
    """A process confirmed gone must not cost a full poll timeout to notice.

    Regression for the SIGSEGV story: the fix is to stop touching the mapping
    once the writer is known to be gone, not to wait out a timeout hoping it
    reappears. ``modPid`` is pointed at a subprocess that has already exited --
    a real, previously-valid, now-guaranteed-dead pid, not a magic number --
    so ``mod_process_alive()`` is exercised for real rather than stubbed.
    """
    dead = subprocess.Popen(["true"])
    dead.wait()

    buf = make_loopback_buffer()
    game = toy.ToyGame(buf)
    chan = Channel(buf)
    chan.attach()
    game.start(max_seconds=30.0)
    try:
        ledger = sr.AttemptLedger()
        runner = sr.Runner(chan, ledger, timeout=5.0)
        runner.prime()                       # normal contact, game genuinely alive
        game.stop()                          # the writer thread stops answering
        game.game.control["modPid"] = dead.pid   # ...and now looks dead too

        t0 = time.perf_counter()
        with pytest.raises(sr.GameGone):
            runner.rollout(sr.Plan((sr.Interval(40, 4),)), watch_from_tick=0)
        elapsed = time.perf_counter() - t0
    finally:
        chan.detach()
        buf.close()

    assert elapsed < runner.timeout, (
        f"took {elapsed:.2f}s against a {runner.timeout}s timeout -- this "
        "means the dead process was found reactively (after the full wait) "
        "rather than proactively, which is still correct but not the "
        "improvement this test is for")
    # The sync record _await_fresh_attempt opened before discovering the
    # process was gone must still be closed, not left dangling -- an open
    # record would poison the ledger's audit on the very next call.
    assert ledger.audit() == []


def test_the_full_search_survives_the_process_dying_mid_search_not_just_at_sync():
    """End to end, and a DIFFERENT code path than the test above.

    The previous test kills the process before the very first rollout, so
    ``GameGone`` is raised from inside ``_await_fresh_attempt`` (no per-plan
    ``except TimeoutError`` there -- see that method's docstring). This one
    lets the calibration rollout complete normally and kills the process only
    once the search is already inside its main probe loop, so ``GameGone`` is
    raised from ``rollout()``'s MAIN BATCH loop instead -- the one that DOES
    have an ``except TimeoutError`` clause, which must NOT swallow it (GameGone
    is a RunAborted, not a TimeoutError). If a future edit changed that except
    clause to catch bare ``Exception`` or ``RunAborted`` instead of exactly
    ``TimeoutError``, this is the test that would catch it: the search would
    then mislabel a dead game as an ordinary "unrunnable" plan and keep
    probing a game that no longer exists, at length seq(0) every time.
    """
    dead = subprocess.Popen(["true"])
    dead.wait()

    buf = make_loopback_buffer()
    game = toy.ToyGame(buf)
    chan = Channel(buf)
    chan.attach()
    game.start(max_seconds=30.0)
    try:
        ledger = sr.AttemptLedger()
        runner = sr.Runner(chan, ledger, timeout=3.0)
        search = sr.Sightreader(runner, budget=50, target_x=None, verbose=False)

        real_rollout = runner.rollout
        killed = {"done": False}

        def rollout_then_die(*args, **kwargs):
            out = real_rollout(*args, **kwargs)
            # Kill the game only once, right after the FIRST rollout (the
            # calibration probe) returns successfully -- so the NEXT rollout
            # call discovers death from inside its own main batch loop, not
            # before it starts.
            if not killed["done"]:
                killed["done"] = True
                game.stop()
                game.game.control["modPid"] = dead.pid
            return out

        runner.rollout = rollout_then_die
        report = search.run()
    finally:
        chan.detach()
        buf.close()

    assert report.attempts >= 2, (
        "the death must be discovered from the SECOND rollout, not the first "
        f"-- got only {report.attempts} attempt(s), so this did not exercise "
        "the code path it claims to")
    assert "aborted" in report.stopped_because, report.text()
    assert ledger.audit() == []


def test_an_alive_but_unresponsive_game_stops_the_search_cleanly_not_a_crash():
    """A hang is not the same claim as a crash, and must not be confused with
    one -- but neither may take main() down with it.

    ``mod_process_alive()`` is stubbed to keep answering True (the process
    genuinely did not die) while ``poll`` is made to stop answering after the
    very first (real) contact, standing in for a real hang. ``Sightreader.run()``
    must still return a normal ``SearchReport`` -- not raise -- with
    ``stopped_because`` naming what happened rather than reporting a
    misleadingly clean "budget exhausted" or "search space exhausted".

    The hang is placed right after ``prime()`` -- inside the "run out the
    in-flight attempt" sync phase every first rollout of a session does (see
    ``_await_fresh_attempt``) -- deliberately, because THAT call site has no
    per-plan ``except TimeoutError`` of its own (only the main batch loop in
    ``rollout()`` does, which turns an ordinary timeout there into a per-plan
    "unrunnable" and keeps searching -- also correct, and also covered, by
    ``test_a_plan_past_the_wire_cap_...`` and the driver's own design; it is
    not a crash either). This test is for the OTHER call site: the one that
    used to propagate a bare exception straight out of ``Sightreader.run()``.
    """
    buf = make_loopback_buffer()
    game = toy.ToyGame(buf)
    chan = Channel(buf)
    chan.attach()
    game.start(max_seconds=30.0)
    try:
        ledger = sr.AttemptLedger()
        runner = sr.Runner(chan, ledger, timeout=1.0)
        search = sr.Sightreader(runner, budget=10, target_x=None, verbose=False)

        real_poll = chan.poll
        calls = {"n": 0}

        def hang_after_a_few(timeout=5.0):
            calls["n"] += 1
            if calls["n"] <= 1:
                return real_poll(timeout=timeout)
            raise TimeoutError("stub: the game stopped answering")

        chan.poll = hang_after_a_few
        chan.mod_process_alive = lambda: True

        report = search.run()
    finally:
        game.stop()
        chan.detach()
        buf.close()

    assert "game stopped responding" in report.stopped_because, report.text()
    assert report.stopped_because.startswith("aborted")
    assert report.attempts >= 1
    assert ledger.audit() == [], (
        "the abort must not leave a dangling open record behind")
