"""Loopback tests for GDRL_PRACTICE (wire version 2, checkpoint save/restore).

Same posture as test_env.py's own module docstring, and it applies with extra
force here: SyntheticGame does NOT model the mod's checkpoint decision logic
(GDRL_PRACTICE gating, "no checkpoint held" refusal, resetLevel()'s own
predicted restore). It only logs that a CHECKPOINT_SAVE/RESTORE/CLEAR action
survived the wire, and lets a test set the practice header/validity fields
directly -- see SyntheticGame.publish()'s and .consume()'s own comments for
why the accept/refuse decision is deliberately NOT reimplemented here.

What this file DOES prove:
  * The three new GdrlActionKind values (CHECKPOINT_SAVE=4, RESTORE=5,
    CLEAR=6) round-trip through Channel.respond() -> shared bytes ->
    SyntheticGame.consume() intact, and are not scheduled as button events the
    way PRESS/RELEASE/HOLD are.
  * The four new GdrlObsHeader fields (resumeTick, checkpointTick,
    hasCheckpoint, practiceMode) and the two new GdrlValidity fields
    (fullAttempts, practiceAttempts) round-trip through the seqlock, land at
    the offsets gdrl_schema.hpp's static_asserts demand, and do not alias one
    another.
  * The accounting CONTRACT -- full and practice attempts are reported on
    separate wire fields, and "is this attempt practice-assisted" is a
    question resume_tick answers, never practice_mode alone -- is internally
    consistent in the decoder (env.Observation).

What this file does NOT prove, and cannot without the game:
  * That PlayLayer::markCheckpoint() / getLastCheckpoint() /
    loadFromCheckpoint() actually do what mod/src/telemetry.cpp assumes.
  * That restoring the same checkpoint N times replays bit-identically
    (TODO.md 2.1's own acceptance test -- see scripts/practice_determinism.sh).
  * That the mod's resetLevel() hook actually sets fullAttempts/
    practiceAttempts/resumeTick the way this file assumes for its fixtures.
  * That GD's own resetLevel() restore (the "vanilla" call the mod's comment
    says happens twice) and the mod's CHECKPOINT_RESTORE handler agree.

Run: cd trainer && python3 -m pytest test_practice.py -q
"""

from __future__ import annotations

import pytest

import schema_generated as sg
from env import Action, Channel, SyntheticGame, make_loopback_buffer


@pytest.fixture()
def loop():
    buf = make_loopback_buffer()
    game = SyntheticGame(buf)
    chan = Channel(buf)
    chan.attach()
    yield game, chan
    chan.detach()
    buf.close()


# ---------------------------------------------------------------------------
# Wire enum
# ---------------------------------------------------------------------------

def test_checkpoint_action_kind_values_match_the_mod():
    """Regression-only (tier i): locks the enum values gdrl_schema.hpp defines.

    mod/src/gdrl_schema.hpp: CHECKPOINT_SAVE=4, CHECKPOINT_RESTORE=5,
    CHECKPOINT_CLEAR=6, continuing directly after HOLD=3. If schema.py's
    generator ever renumbers these, every stored practice-mode action log
    silently means something else.
    """
    assert int(sg.GdrlActionKind.CHECKPOINT_SAVE) == 4
    assert int(sg.GdrlActionKind.CHECKPOINT_RESTORE) == 5
    assert int(sg.GdrlActionKind.CHECKPOINT_CLEAR) == 6


# ---------------------------------------------------------------------------
# Action round trip
# ---------------------------------------------------------------------------

def test_checkpoint_save_survives_the_wire_round_trip(loop):
    game, chan = loop
    game.publish(tick=100)
    chan.poll(timeout=1.0)
    chan.respond([Action(target_tick=100, kind=sg.GdrlActionKind.CHECKPOINT_SAVE)])
    assert game.consume() == 1
    assert game.checkpoint_requests == [sg.GdrlActionKind.CHECKPOINT_SAVE]
    # Unlike PRESS/RELEASE/HOLD, a checkpoint action never queues a button --
    # it is applied immediately by the mod, not scheduled for a future tick.
    assert game.scheduled == []


def test_checkpoint_restore_survives_the_wire_round_trip(loop):
    game, chan = loop
    game.publish(tick=100)
    chan.poll(timeout=1.0)
    chan.respond([Action(target_tick=100, kind=sg.GdrlActionKind.CHECKPOINT_RESTORE)])
    game.consume()
    assert game.checkpoint_requests == [sg.GdrlActionKind.CHECKPOINT_RESTORE]
    assert game.scheduled == []


def test_checkpoint_clear_survives_the_wire_round_trip(loop):
    game, chan = loop
    game.publish(tick=100)
    chan.poll(timeout=1.0)
    chan.respond([Action(target_tick=100, kind=sg.GdrlActionKind.CHECKPOINT_CLEAR)])
    game.consume()
    assert game.checkpoint_requests == [sg.GdrlActionKind.CHECKPOINT_CLEAR]
    assert game.scheduled == []


def test_checkpoint_action_can_share_a_block_with_an_ordinary_action(loop):
    """One action block, one PRESS and one CHECKPOINT_SAVE -- both must decode.

    The mod's consumeActions() walks the same array for every kind (telemetry.
    cpp:696-808); nothing here should make one kind starve another.
    """
    game, chan = loop
    game.publish(tick=50)
    chan.poll(timeout=1.0)
    chan.respond([
        Action(target_tick=60, kind=sg.GdrlActionKind.PRESS),
        Action(target_tick=50, kind=sg.GdrlActionKind.CHECKPOINT_SAVE),
    ])
    game.consume()
    assert game.scheduled == [(60, True, 1, 0)]
    assert game.checkpoint_requests == [sg.GdrlActionKind.CHECKPOINT_SAVE]


def test_all_three_checkpoint_kinds_in_one_block_preserve_order(loop):
    game, chan = loop
    game.publish(tick=1)
    chan.poll(timeout=1.0)
    chan.respond([
        Action(target_tick=1, kind=sg.GdrlActionKind.CHECKPOINT_SAVE),
        Action(target_tick=1, kind=sg.GdrlActionKind.CHECKPOINT_CLEAR),
        Action(target_tick=1, kind=sg.GdrlActionKind.CHECKPOINT_RESTORE),
    ])
    game.consume()
    assert game.checkpoint_requests == [
        sg.GdrlActionKind.CHECKPOINT_SAVE,
        sg.GdrlActionKind.CHECKPOINT_CLEAR,
        sg.GdrlActionKind.CHECKPOINT_RESTORE,
    ]


# ---------------------------------------------------------------------------
# Header field round trip
# ---------------------------------------------------------------------------

def test_practice_header_fields_round_trip(loop):
    game, chan = loop
    game.publish(tick=400, resume_tick=384, checkpoint_tick=384,
                 has_checkpoint=True, practice_mode=True)
    obs = chan.poll(timeout=1.0)
    assert obs.resume_tick == 384
    assert obs.checkpoint_tick == 384
    assert obs.has_checkpoint is True
    assert obs.practice_mode is True


def test_practice_header_fields_default_to_the_mods_no_checkpoint_state(loop):
    """publish() with no practice kwargs must mean "nothing happened yet".

    checkpointTick's rest state is -1 (gdrl_schema.hpp: "-1 if none exists"),
    NOT 0 -- 0 is a real, valid tick. A decoder or fixture that defaulted this
    to 0 would make an unset checkpoint indistinguishable from one saved at
    the very first tick of the level.
    """
    game, chan = loop
    game.publish(tick=10)
    obs = chan.poll(timeout=1.0)
    assert obs.resume_tick == 0
    assert obs.checkpoint_tick == -1
    assert obs.has_checkpoint is False
    assert obs.practice_mode is False


def test_has_checkpoint_and_checkpoint_tick_vary_independently_of_resume_tick(loop):
    """Live fields (checkpointTick/hasCheckpoint) vs a latched one (resumeTick).

    A checkpoint can exist (saved mid-attempt) on an attempt that itself began
    at tick 0 -- resumeTick stays 0 for that attempt's whole duration even as
    hasCheckpoint flips to 1 partway through. This is the header comment's own
    example (gdrl_schema.hpp: resumeTick "Latched at the attempt boundary, not
    live" vs checkpointTick/hasCheckpoint "Live, not latched"), checked here as
    a wire-decode property rather than trusted from the comment.
    """
    game, chan = loop
    game.publish(tick=10, resume_tick=0, has_checkpoint=False, checkpoint_tick=-1)
    obs = chan.poll(timeout=1.0)
    assert obs.resume_tick == 0
    assert obs.has_checkpoint is False

    game.publish(tick=11, resume_tick=0, has_checkpoint=True, checkpoint_tick=10)
    obs = chan.poll(timeout=1.0)
    assert obs.resume_tick == 0            # still the same (full) attempt
    assert obs.has_checkpoint is True       # but a checkpoint now exists
    assert obs.checkpoint_tick == 10


# ---------------------------------------------------------------------------
# fullAttempts / practiceAttempts -- the property that protects the benchmark
# ---------------------------------------------------------------------------

def test_full_and_practice_attempts_are_independent_wire_fields():
    """Locks the byte offsets gdrl_schema.hpp's static_asserts demand.

    mod/src/gdrl_schema.hpp: offsetof(GdrlValidity, fullAttempts) == 56,
    offsetof(GdrlValidity, practiceAttempts) == 64 -- eight bytes apart, two
    distinct int64 fields, never one counter shared by both attempt kinds. If
    the Python dtype generator ever aliased these (same offset, or one
    overlapping the other's bytes), incrementing one would silently corrupt
    the other and no test that only ever set one of them would catch it.
    """
    off_full = sg.GdrlValidity_DTYPE.fields["fullAttempts"][1]
    off_practice = sg.GdrlValidity_DTYPE.fields["practiceAttempts"][1]
    assert off_full == 56
    assert off_practice == 64
    assert off_full != off_practice


def test_full_and_practice_attempts_do_not_alias_through_the_seqlock(loop):
    """The same independence, exercised through an actual publish/poll round trip
    rather than read off the dtype description alone."""
    game, chan = loop
    game.publish(tick=1, full_attempts=5, practice_attempts=0)
    obs = chan.poll(timeout=1.0)
    assert obs.full_attempts == 5
    assert obs.practice_attempts == 0

    game.publish(tick=2, full_attempts=5, practice_attempts=3)
    obs = chan.poll(timeout=1.0)
    assert obs.full_attempts == 5      # unchanged
    assert obs.practice_attempts == 3  # changed independently

    game.publish(tick=3, full_attempts=0, practice_attempts=3)
    obs = chan.poll(timeout=1.0)
    assert obs.full_attempts == 0      # can move down/independently too
    assert obs.practice_attempts == 3


def test_is_practice_attempt_is_derived_from_resume_tick_not_practice_mode(loop):
    """THE property that protects Benchmark A.

    practice_mode is a whole-RUN switch: it can be 1 while the CURRENT attempt
    still began at tick 0 (the run's very first attempt, before any checkpoint
    exists -- see mod/src/telemetry.cpp's own comment on
    g_gdrlAttemptIsPractice). A decoder that classified "is this attempt
    practice-assisted" by reading practice_mode instead of resume_tick would
    mislabel that first attempt as not-A-eligible when it is a genuine tick-0
    attempt, OR -- the more dangerous direction -- would mislabel a
    checkpoint-resumed attempt as a full one if practice_mode were ever 0 by a
    bookkeeping bug while resume_tick was nonzero. is_practice_attempt must
    track resume_tick and only resume_tick.
    """
    game, chan = loop

    # Whole run has GDRL_PRACTICE=1, but THIS attempt began at tick 0 (no
    # checkpoint existed yet) -- must NOT read as a practice-assisted attempt.
    game.publish(tick=0, practice_mode=True, resume_tick=0, has_checkpoint=False,
                 full_attempts=1, practice_attempts=0)
    obs = chan.poll(timeout=1.0)
    assert obs.practice_mode is True
    assert obs.is_practice_attempt is False

    # Later attempt in the same run, now resumed from a checkpoint.
    game.publish(tick=1, practice_mode=True, resume_tick=384, has_checkpoint=True,
                 full_attempts=1, practice_attempts=1)
    obs = chan.poll(timeout=1.0)
    assert obs.practice_mode is True
    assert obs.is_practice_attempt is True


def test_a_full_attempt_is_never_indistinguishable_from_a_practice_attempt(loop):
    """Hardened version of the property above: sweep every (practice_mode,
    resume_tick) combination the wire can carry and check the classification
    is a pure function of resume_tick, in both directions.

    This is tier (i) -- SyntheticGame does not decide these values, the test
    does, so this proves the DECODER's classification is self-consistent, not
    that the mod produces only these combinations. TODO.md 2.1's live
    acceptance test is what checks the mod's side.
    """
    game, chan = loop
    tick = 0
    for practice_mode in (False, True):
        for resume_tick in (0, 1, 40, 384, 2367):
            tick += 1
            game.publish(tick=tick, practice_mode=practice_mode,
                         resume_tick=resume_tick,
                         has_checkpoint=resume_tick != 0)
            obs = chan.poll(timeout=1.0)
            assert obs.is_practice_attempt == (resume_tick != 0), (
                f"practice_mode={practice_mode} resume_tick={resume_tick}: "
                f"is_practice_attempt={obs.is_practice_attempt}"
            )


def test_no_accessor_silently_sums_full_and_practice_attempts():
    """There is no `.total_attempts` (or similarly named) property on
    Observation. A caller who wants a combined figure must write
    `obs.full_attempts + obs.practice_attempts` at its own call site, where the
    fact that it is mixing an A number with a practice-assisted one is visible
    to whoever reads that line -- not buried inside env.py as a convenience
    that looks like a single, clean "attempts" count.
    """
    import env as envmod
    forbidden = ("total_attempts", "all_attempts", "attempts_total", "attempt_count")
    for name in forbidden:
        assert not hasattr(envmod.Observation, name), (
            f"Observation.{name} exists -- a summed attempt accessor is exactly "
            "the shortcut that lets a practice-assisted result be reported as "
            "an A result"
        )
