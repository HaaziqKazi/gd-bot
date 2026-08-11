"""Loopback tests for the environment transport.

The wire format has to be tested without GD running. There is one GD slot, it is
contended, and a test that needs it is a test that does not get run -- so the
protocol is exercised here against ``env.SyntheticGame``, which implements the
game side of the handshake in pure Python over an anonymous mapping.

What this does and does not prove:

  * It DOES prove the encode/decode round trip, the seqlock, the handshake
    refusals, the validity gate, the known/unknown mask, and that HOLD expands
    into the pair of events the mod expands it into.
  * It does NOT prove anything about the game. SyntheticGame fabricates field
    values; it is not a simulator. Every claim about what GD actually does is
    listed as UNVERIFIED at the bottom of mod/src/telemetry.cpp and needs the
    GD slot.

Run: cd trainer && python3 -m pytest test_env.py -q
"""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

import env as envmod
import schema_generated as sg
from env import (
    Action,
    Channel,
    HandshakeError,
    Observation,
    ObservationRejected,
    SyntheticGame,
    derive_kind,
    make_loopback_buffer,
)


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
# Handshake
# ---------------------------------------------------------------------------

def test_handshake_accepts_a_well_formed_segment():
    buf = make_loopback_buffer()
    SyntheticGame(buf)
    chan = Channel(buf)          # validates in __init__
    assert chan.mod_alive
    assert chan.timeouts == 0


def test_handshake_refuses_a_wrong_schema_hash():
    """A mismatched pair must fail at attach, never decode.

    This is the failure the schema hash exists for: two builds generated from
    different versions of schema.py would otherwise decode each other's bytes
    into a full observation of plausible garbage, and nothing downstream could
    detect it.
    """
    buf = make_loopback_buffer()
    game = SyntheticGame(buf)
    game.control["schemaHash"] = sg.GDRL_SCHEMA_HASH ^ 1
    with pytest.raises(HandshakeError, match="schema hash"):
        Channel(buf)


def test_handshake_refuses_a_wrong_wire_version():
    buf = make_loopback_buffer()
    game = SyntheticGame(buf)
    game.control["wireVersion"] = sg.GDRL_WIRE_VERSION + 1
    with pytest.raises(HandshakeError, match="wire version"):
        Channel(buf)


def test_handshake_refuses_a_segment_with_no_magic():
    """An unpublished segment reads as all zeroes, not as an empty observation."""
    buf = make_loopback_buffer()
    with pytest.raises(HandshakeError, match="magic"):
        Channel(buf)


def test_handshake_refuses_a_geometry_mismatch_even_with_a_matching_hash():
    """A matching hash plus a wrong size means the compiler disagreed.

    The generated static_asserts are supposed to make this impossible; if it
    ever happens the right response is to stop, not to decode.
    """
    buf = make_loopback_buffer()
    game = SyntheticGame(buf)
    game.control["obsSize"] = 1
    with pytest.raises(HandshakeError, match="obsSize"):
        Channel(buf)


def test_control_offsets_are_readable_without_the_dtype():
    """The frozen four must be readable by byte offset alone.

    That is the property that lets a Python built against a different wire
    version report the actual mismatch instead of a confusing one, so it is
    checked without going through GdrlControl_DTYPE at all.
    """
    buf = make_loopback_buffer()
    SyntheticGame(buf)
    raw = memoryview(buf)
    assert int(np.frombuffer(raw, np.uint32, 1, sg.OFFSET_MAGIC)[0]) == sg.GDRL_MAGIC
    assert int(np.frombuffer(raw, np.uint16, 1, sg.OFFSET_WIREVERSION)[0]) == sg.GDRL_WIRE_VERSION
    assert int(np.frombuffer(raw, np.uint64, 1, sg.OFFSET_SCHEMAHASH)[0]) == sg.GDRL_SCHEMA_HASH


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------

def test_observation_round_trip(loop):
    game, chan = loop
    game.publish(tick=325, player_x=507.615234375, player_y=105.0)
    obs = chan.poll(timeout=1.0)

    assert obs.tick == 325
    assert obs.retries == 0
    assert obs.status is sg.GdrlStatus.OK
    assert obs.problems() == []
    assert float(obs.header["playerX"]) == pytest.approx(507.615234375)
    assert float(obs.record["players"][0]["x"]) == pytest.approx(507.615234375)


def test_tick_is_the_rounding_of_attempt_time_not_an_exact_multiple():
    """t*240 is NOT an integer, and the decoder must not require one.

    GD accumulates a float32 1/240 into a double, so the residual reaches
    2.039e-05 ticks by 391. SyntheticGame reproduces that accumulation rather
    than emitting tick/240, precisely so a decoder that used `==` would fail
    this test instead of passing the loopback and then failing on the game.
    """
    t = envmod._accumulated_attempt_time(391)
    assert t != 391 / 240.0                 # the residual is real
    assert abs(t * 240 - 391) < 1e-4        # ...and small
    assert round(t * 240) == 391


def test_a_disagreeing_tick_is_reported(loop):
    game, chan = loop
    game.publish(tick=100)
    game.obs["header"]["tick"] = 101        # as if the mod's lround disagreed
    obs = chan.poll(timeout=1.0)
    assert any("round(attemptTime*240)" in p for p in obs.problems())


def test_header_player_fields_must_agree_with_players_zero(loop):
    """The spec's duplicated player fields are used as a consistency check.

    header.playerX/Y/Speed and players[0] describe the same PlayerObject. A
    disagreement means the mod filled one of them from something else, which no
    other signal in the record would reveal.
    """
    game, chan = loop
    game.publish(tick=10, player_x=100.0)
    game.obs["players"][0]["x"] = 999.0
    obs = chan.poll(timeout=1.0)
    assert any("disagrees with players[0].x" in p for p in obs.problems())


# ---------------------------------------------------------------------------
# Validity gate
# ---------------------------------------------------------------------------

def test_leaked_input_voids_the_frame(loop):
    game, chan = loop
    game.publish(tick=50)
    game.obs["validity"]["leaked"] = 3
    obs = chan.poll(timeout=1.0)
    assert any("leaked=3" in p for p in obs.problems())
    with pytest.raises(ObservationRejected, match="leaked"):
        obs.require_ok()


def test_unguarded_input_voids_the_frame(loop):
    """UNGUARDED is not 'probably fine'.

    With GDRL_BLOCK_INPUT off a stray keypress is indistinguishable from a
    policy action -- that contaminated four consecutive runs before anyone
    noticed, with attempts sailing past the first spike and reading as a physics
    anomaly.
    """
    game, chan = loop
    game.publish(tick=50)
    game.obs["validity"]["inputVerdict"] = int(sg.GdrlInputVerdict.UNGUARDED)
    obs = chan.poll(timeout=1.0)
    assert any("UNGUARDED" in p for p in obs.problems())


def test_a_timeout_voids_the_frame(loop):
    """An expired wait means a step ran with no action at all.

    That is indistinguishable from a policy that chose not to jump, which is why
    it can never be silent.
    """
    game, chan = loop
    game.publish(tick=50)
    game.obs["validity"]["timeouts"] = 1
    obs = chan.poll(timeout=1.0)
    assert any("timeouts=1" in p for p in obs.problems())


def test_an_empty_level_voids_the_frame(loop):
    """getMainLevel(id, true) yields 2 objects and runs perfectly happily."""
    game, chan = loop
    game.publish(tick=5, object_count_total=2)
    obs = chan.poll(timeout=1.0)
    assert any("level string" in p for p in obs.problems())


def test_the_seek_path_is_not_a_gameplay_step(loop):
    """prepareMoveActions also fires from loadUpToPosition.

    An observation from the seek path describes a level being fast-forwarded,
    not one being played, and must not be mistaken for a gameplay tick.
    """
    game, chan = loop
    game.publish(tick=5)
    flags = int(game.obs["header"]["flags"])
    game.obs["header"]["flags"] = flags & ~int(sg.GdrlHeaderFlag.GAMEPLAY_STEP)
    obs = chan.poll(timeout=1.0)
    assert any("not a gameplay step" in p for p in obs.problems())


def test_every_status_other_than_ok_is_reported(loop):
    game, chan = loop
    for status in sg.GdrlStatus:
        game.publish(tick=1, status=status)
        obs = chan.poll(timeout=1.0)
        chan.respond([])
        game.consume()
        if status is sg.GdrlStatus.OK:
            assert not any(p.startswith("status=") for p in obs.problems())
        else:
            assert f"status={status.name}" in obs.problems()


# ---------------------------------------------------------------------------
# Objects and the audited kind collapse
# ---------------------------------------------------------------------------

def test_objects_decode_and_stale_slots_do_not_leak(loop):
    """A shorter observation must not leave the previous one's tail visible.

    The mod clears `known` on every slot the scan did not fill. Without that a
    stale object decodes as a real one, one step out of date and sitting where
    it used to be -- a plausible object in a plausible place, wrong only in
    time, which is the worst failure this channel can have.
    """
    game, chan = loop
    game.publish(tick=1, objects=[{"x": float(i), "y": 0.0, "objectType": 2}
                                  for i in range(5)])
    obs = chan.poll(timeout=1.0)
    assert len(obs.objects()) == 5
    chan.respond([])
    game.consume()

    game.publish(tick=2, objects=[{"x": 0.0, "y": 0.0, "objectType": 2}])
    obs2 = chan.poll(timeout=1.0)
    assert len(obs2.objects()) == 1


def test_kind_collapse_is_audited_not_trusted(loop):
    """The mod's one interpretation is re-derived here and asserted."""
    game, chan = loop
    game.publish(tick=1, objects=[
        {"objectType": 2},    # Hazard
        {"objectType": 0},    # Solid
        {"objectType": 8},    # YellowJumpPad -> INTERACTIVE
        {"objectType": 7},    # Decoration -> OTHER
    ])
    obs = chan.poll(timeout=1.0)
    assert obs.check_kind_collapse() == []
    kinds = [int(o["kind"]) for o in obs.objects()]
    assert kinds == [int(sg.GdrlObjectKind.HAZARD), int(sg.GdrlObjectKind.SOLID),
                     int(sg.GdrlObjectKind.INTERACTIVE), int(sg.GdrlObjectKind.OTHER)]


def test_kind_collapse_disagreement_is_caught(loop):
    game, chan = loop
    game.publish(tick=1, objects=[{"objectType": 2, "kind": int(sg.GdrlObjectKind.SOLID)}])
    obs = chan.poll(timeout=1.0)
    bad = obs.check_kind_collapse()
    assert bad and "wire kind=1" in bad[0]


def test_a_hazardous_slope_is_a_hazard():
    """Slope-that-kills and slope-that-does-not share a GameObjectType.

    m_slopeIsHazard is the only thing that separates them, which is why the raw
    flag is on the wire alongside the derived isHazard.
    """
    assert derive_kind(25, False) == int(sg.GdrlObjectKind.SOLID)
    assert derive_kind(25, True) == int(sg.GdrlObjectKind.HAZARD)


def test_unknown_object_types_default_to_interactive():
    """The conservative direction: a new type that does something is more
    dangerous filed as scenery than scenery is filed as interactive."""
    assert derive_kind(9999, False) == int(sg.GdrlObjectKind.INTERACTIVE)


# ---------------------------------------------------------------------------
# Objective E: known vs empty
# ---------------------------------------------------------------------------

def test_known_mask_marks_unscanned_columns_unknown(loop):
    """An off-screen pit and an empty floor must not be the same bytes."""
    game, chan = loop
    game.publish(tick=1, player_x=500.0, player_y=105.0, coverage_cols=4)
    obs = chan.poll(timeout=1.0)

    mask = obs.known_mask(height=32, width=48, cell_size=30.0, player_col=12)
    assert mask.shape == (32, 48)
    assert mask.any(), "nothing at all was marked known"
    assert not mask.all(), "the whole window was claimed known despite 4 columns"

    # The known region has to line up with the columns the mod said it walked.
    lo, hi = float(obs.header["windowMinX"]), float(obs.header["windowMaxX"])
    origin_x = float(obs.header["playerX"]) - 12 * 30.0
    centres = origin_x + (np.arange(48) + 0.5) * 30.0
    inside = (centres >= lo) & (centres <= hi)
    assert (mask.any(axis=0) == inside).all()


def test_absent_columns_count_as_known_empty(loop):
    """Past the end of m_sections GD itself has no geometry: known-empty.

    Conflating that with 'not looked at' would make the policy permanently
    uncertain about the end of every level.
    """
    game, chan = loop
    game.publish(tick=1, player_x=500.0, coverage_cols=4)
    game.obs["coverage"][4:8] = int(sg.GdrlCoverage.ABSENT)
    game.obs["header"]["windowMaxX"] = float(game.obs["header"]["windowMaxX"]) + 400.0
    obs = chan.poll(timeout=1.0)
    mask = obs.known_mask(height=8, width=48, cell_size=30.0, player_col=12)
    assert mask.any(axis=0).sum() > 4 * 100 / 30


def test_truncated_columns_are_not_known(loop):
    """The array filled mid-column, so what is missing is genuinely unknown."""
    game, chan = loop
    game.publish(tick=1, player_x=500.0, coverage_cols=8)
    game.obs["coverage"][:] = int(sg.GdrlCoverage.TRUNCATED)
    obs = chan.poll(timeout=1.0)
    mask = obs.known_mask(height=8, width=48, cell_size=30.0, player_col=12)
    assert not mask.any()


def test_unavailable_tables_are_distinguishable_from_empty_ones(loop):
    """count == 0 with the flag set is not the same statement as count == 0.

    The commands / pending / speedSegs tables are unpopulated in wire version 1
    because the measurements that would make them trustworthy have not been
    taken. A decoder that read the zero counts as 'a static world' would be
    asserting something nobody observed.
    """
    game, chan = loop
    game.publish(tick=1)
    obs = chan.poll(timeout=1.0)
    assert int(obs.header["commandCount"]) == 0
    assert set(obs.unavailable_tables()) == {"commands", "pending", "speedSegs"}
    assert "objects" not in obs.unavailable_tables()


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def test_action_round_trip_and_seq_pairing(loop):
    game, chan = loop
    game.publish(tick=100)
    chan.poll(timeout=1.0)
    chan.respond([Action(target_tick=325, kind=sg.GdrlActionKind.PRESS)])
    assert game.consume() == 1
    assert game.scheduled == [(325, True, 1, 0)]


def test_hold_expands_to_a_push_and_a_release(loop):
    """Objective C's action duration, executed as one decision.

    A press/release-per-frame action space cannot express a hold across the many
    ticks between two observations; HOLD carries its own release so the policy
    does not have to be present for it.
    """
    game, chan = loop
    game.publish(tick=100)
    chan.poll(timeout=1.0)
    chan.respond([Action(target_tick=325, kind=sg.GdrlActionKind.HOLD, hold_ticks=8)])
    game.consume()
    assert game.scheduled == [(325, True, 1, 0), (333, False, 1, 0)]


def test_zero_length_hold_is_refused():
    """A HOLD with no release latches the button down.

    GD calls releaseButton internally on death and reset, so a latched button is
    not obviously fatal -- which is exactly why it has to be refused here rather
    than discovered later as a policy that mysteriously holds forever.
    """
    with pytest.raises(ValueError, match="hold_ticks"):
        Action(target_tick=1, kind=sg.GdrlActionKind.HOLD, hold_ticks=0)


def test_too_many_actions_is_refused_rather_than_silently_truncated(loop):
    game, chan = loop
    game.publish(tick=1)
    chan.poll(timeout=1.0)
    too_many = [Action(target_tick=i, kind=sg.GdrlActionKind.PRESS)
                for i in range(sg.GDRL_MAX_ACTIONS + 1)]
    with pytest.raises(ValueError, match="exceeds the wire capacity"):
        chan.respond(too_many)


def test_a_stale_action_sequence_is_refused(loop):
    """Executing a plan computed for a different tick would look like a policy
    decision, not like a bug."""
    game, chan = loop
    game.publish(tick=1)
    chan.poll(timeout=1.0)
    chan.respond([])
    game.consume()

    game.publish(tick=2)
    chan.poll(timeout=1.0)
    chan._act_view["seq"] = 0            # forge a stale reply
    chan._control["actSeq"] = game.seq
    with pytest.raises(ObservationRejected, match="action seq"):
        game.consume()


def test_advance_steps_is_carried_through(loop):
    game, chan = loop
    game.publish(tick=1)
    chan.poll(timeout=1.0)
    chan.respond([], advance_steps=8)
    assert game.consume() == 8


def test_advance_steps_must_be_at_least_one(loop):
    game, chan = loop
    game.publish(tick=1)
    chan.poll(timeout=1.0)
    with pytest.raises(ValueError, match="advance_steps"):
        chan.respond([], advance_steps=0)


# ---------------------------------------------------------------------------
# The seqlock and the ping-pong
# ---------------------------------------------------------------------------

def test_reader_never_returns_a_half_written_observation(loop):
    """The odd sequence must hold the reader off mid-write.

    The ping-pong protocol already stops the writer from starting n+1 before n
    is answered -- but that guarantee dies the moment a bounded wait times out
    and the game runs on, and a torn read there would surface as an object in an
    impossible place and be blamed on physics.
    """
    game, chan = loop
    game.publish(tick=1)
    chan.poll(timeout=1.0)

    game.seq += 1                       # odd: a write is "in progress"
    game.obs["seq"] = game.seq
    game.obs["header"]["tick"] = 2
    with pytest.raises(TimeoutError):
        chan.poll(timeout=0.15)

    game.seq += 1
    game.obs["seq"] = game.seq
    assert chan.poll(timeout=1.0).tick == 2


def test_a_full_episode_of_steps(loop):
    """The shape of a real rollout: many ticks, one action, no protocol errors."""
    game, chan = loop
    seen = []
    game.publish(tick=0)
    obs = chan.poll(timeout=1.0)
    for tick in range(1, 40):
        seen.append(obs.tick)
        actions = [Action(target_tick=25, kind=sg.GdrlActionKind.HOLD, hold_ticks=8)] \
            if obs.tick == 20 else []
        chan.respond(actions)
        game.consume()
        game.publish(tick=tick)
        obs = chan.poll(timeout=1.0)

    assert seen == list(range(39))
    assert chan.protocol_errors == 0
    assert game.scheduled == [(25, True, 1, 0), (33, False, 1, 0)]


def test_step_index_and_tick_advance_together(loop):
    """The in-band answer to an UNVERIFIED question.

    Whether m_attemptTime is updated before or after prepareMoveActions inside a
    physics step was never established, so the mod emits its own step counter
    alongside the tick. A constant offset between them is correctable; a varying
    one invalidates action placement entirely. Here they must move in lockstep.
    """
    game, chan = loop
    prev = None
    game.publish(tick=0, step_index=0)
    obs = chan.poll(timeout=1.0)
    for tick in range(1, 10):
        if prev is not None:
            assert obs.tick - prev[0] == obs.step_index - prev[1]
        prev = (obs.tick, obs.step_index)
        chan.respond([])
        game.consume()
        game.publish(tick=tick, step_index=tick)
        obs = chan.poll(timeout=1.0)


def test_poll_times_out_rather_than_blocking_forever(loop):
    game, chan = loop
    with pytest.raises(TimeoutError, match="no observation"):
        chan.poll(timeout=0.05)


def test_detach_is_idempotent_and_safe(loop):
    """A trainer dying must not wedge the game.

    The mod only ever blocks while pyAttached is 1, so clearing it is the
    mechanism; the bounded wait is only the backstop for a process that died
    without clearing it.
    """
    game, chan = loop
    chan.detach()
    chan.detach()
    assert int(chan._control["pyAttached"]) == 0


def test_reader_survives_a_writer_racing_it():
    """Concurrent publish and poll must never yield a torn record.

    A torn observation is the one failure that would be blamed on the
    simulation, so the retry path is exercised rather than reasoned about.
    """
    buf = make_loopback_buffer()
    game = SyntheticGame(buf)
    chan = Channel(buf)
    chan.attach()

    stop = threading.Event()
    ticks = []

    def writer():
        t = 1
        while not stop.is_set() and t < 400:
            game.publish(tick=t, player_x=float(t))
            t += 1
            time.sleep(0.0001)

    th = threading.Thread(target=writer)
    th.start()
    try:
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline and len(ticks) < 30:
            try:
                obs = chan.poll(timeout=0.2)
            except TimeoutError:
                break
            # The invariant: whatever came back is internally consistent, i.e.
            # the header and the player block describe the same moment.
            assert float(obs.header["playerX"]) == float(obs.tick)
            assert float(obs.record["players"][0]["x"]) == float(obs.tick)
            assert obs.problems() == []
            ticks.append(obs.tick)
    finally:
        stop.set()
        th.join()
        chan.detach()
        buf.close()

    assert len(ticks) >= 5, "the writer never got far enough to be a race"
    assert ticks == sorted(ticks)


# ---------------------------------------------------------------------------
# Composition with the rest of the trainer
# ---------------------------------------------------------------------------

def test_a_decoded_player_makes_a_conditioning_regime(loop):
    """The wire feeds Objective A without any C++ interpretation in between.

    Everything semantic -- the vehicle label, the mini threshold, the speed
    bucket, the gravity baseline -- happens on this side of the boundary.
    """
    from conditioning import Regime, Vehicle

    game, chan = loop
    game.publish(tick=1)
    game.obs["players"][0]["vehicleFlags"] = int(sg.GdrlVehicleFlag.SHIP)
    game.obs["players"][0]["vehicleSize"] = 0.6
    obs = chan.poll(timeout=1.0)

    p = obs.record["players"][0]
    regime = Regime.from_wire(
        vehicle_flags=int(p["vehicleFlags"]),
        is_upside_down=bool(p["isUpsideDown"]),
        is_sideways=bool(p["isSideways"]),
        vehicle_size=float(p["vehicleSize"]),
        player_speed=float(p["playerSpeed"]),
        gravity=float(p["gravity"]),
        is_dual_mode=bool(obs.header["isDualMode"]),
        time_warp=float(obs.header["timeWarp"]),
    )
    assert regime.vehicle is Vehicle.SHIP
    assert regime.mini is True
    assert regime.speed_bucket() == 1
