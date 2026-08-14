"""Loopback tests for the environment transport.

The wire format has to be tested without GD running. There is one GD slot, it is
contended, and a test that needs it is a test that does not get run -- so the
protocol is exercised here against ``env.SyntheticGame``, which implements the
game side of the handshake in pure Python over an anonymous mapping.

What this does and does not prove:

  * It DOES prove the encode/decode round trip, the seqlock, the handshake
    refusals, the validity gate, the known/unknown mask, that an unusable
    sectionXFactor is REFUSED rather than substituted for, and that HOLD expands
    into the pair of events the mod expands it into.
  * It does NOT prove anything about the game. SyntheticGame fabricates field
    values; it is not a simulator. Every claim about what GD actually does is
    listed as UNVERIFIED at the bottom of mod/src/telemetry.cpp and needs the
    GD slot.

Run: cd trainer && python3 -m pytest test_env.py -q
"""

from __future__ import annotations

import math
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

# Every value of sectionXFactor that section_factor() must reject, one per
# rejection reason. These are NOT values the game produces -- the measured one
# is envmod.MEASURED_SECTION_X_FACTOR (0.01, live 2026-08-12). They are the
# shapes a corrupt or unread header can take, and the point of injecting them is
# that the decoder must REFUSE rather than substitute a plausible constant.
UNUSABLE_SECTION_FACTORS = [
    pytest.param(0.0, id="zero"),                    # a field the mod never wrote
    pytest.param(-0.01, id="negative"),              # sign flip
    pytest.param(float("nan"), id="nan"),            # 0/0 upstream
    pytest.param(float("inf"), id="posinf"),         # 1/0 upstream
    pytest.param(float("-inf"), id="neginf"),
]


# -- the fixture's window must be the mod's window ---------------------------
#
# Every mask test below reads windowMinX out of the header and recomputes its
# own expectation from it, so all of them pass against ANY internally consistent
# window -- including the one publish() used to emit, whose left edge was snapped
# down to a column boundary and was therefore up to a full 100-unit section wider
# than anything scanObjects() can produce (TODO L5).
#
# MEASURED 2026-08-14: reverting publish()'s left edge to the old
# ``col0 * sec_w`` failed ZERO of the 64 tests that existed before these two were
# added -- the whole L5 fix was ungraded. That is what they are for, and it is
# why they compare header fields directly instead of going through known_mask().

def _mod_scan_window(player_x: float, player_y: float, sxf_f32: float, *,
                     coverage_cols: int, n_cols: int,
                     win_behind: float, win_ahead: float, win_vert: float) -> dict:
    """``scanObjects()``'s window arithmetic, restated from telemetry.cpp:649-808.

    EVERY LINE NUMBER BELOW IS IN telemetry.cpp AS OF COMMIT 8dd5ceb
    (``git show 8dd5ceb:mod/src/telemetry.cpp``). The working tree moved while
    this was being written -- the right-edge ``nextafter`` back-off at
    8dd5ceb:677-680 was measured dead and deleted, which shifts everything after
    :669 -- and no published value changes either way, so this transcription
    keeps the loop and pins the commit rather than chasing the numbers.

    Statement for statement, in the mod's order and the mod's FORM: the window is
    primary (``minX = px - g_winBehind``, :651, published unchanged at :688) and
    the column is DERIVED from it (``col0 = (int)floor(minX * sxf)``, :664). The
    inverse -- deriving the advertised edge from the column -- is a different
    function whenever ``minX`` is not exactly on a column boundary, which for the
    measured sxf is almost always, and it is the bug L5 closed.

    TIER (i). This is a transcription of the C, written out a second time; it
    grades our restatement of the mod, NOT the mod. If telemetry.cpp changes,
    both this and publish() are wrong together and nothing here will say so --
    only reading the C will. What it does catch is publish() drifting away from
    scanObjects() again, which is exactly how L5 happened.

    Every constant comes from the mod: g_winBehind / g_winAhead / g_winVert
    default to 400 / 1400 / 600 at telemetry.cpp:184-186, and the clamp at :670
    is GDRL_COVERAGE_COLS (``coverage_cols`` here is publish()'s stand-in for it,
    so a test can hand the decoder a narrower mask).

    The coverage array covers only the states the fixture can reach. The mod has
    two more: ABSENT for a null ``m_sections[col]`` (:712) and TRUNCATED when the
    object array fills mid-column, neither of which publish() produces -- tests
    that need TRUNCATED write it by hand.
    """
    sxf = float(np.float32(sxf_f32))            # float m_sectionXFactor
    sec_w = 1.0 / sxf                                                    # :649
    min_x = player_x - win_behind                                        # :651
    max_x_requested = player_x + win_ahead                               # :652
    min_y = player_y - win_vert                                          # :653
    max_y = player_y + win_vert                                          # :654

    col0 = int(math.floor(min_x * sxf))                                  # :664
    if col0 < 0:                                                         # :665
        col0 = 0
    col1 = int(math.floor(max_x_requested * sxf))                        # :666
    col1 = min(col1, col0 + coverage_cols - 1)                           # :670

    col_edge = (col1 + 1) * sec_w                                        # :677
    i = 0
    while i < 8 and math.floor(col_edge * sxf) > col1:                   # :678-680
        col_edge = math.nextafter(col_edge, 0.0)
        i += 1
    max_x = min(max_x_requested, col_edge)                               # :681

    coverage = np.empty(sg.GDRL_COVERAGE_COLS, dtype=np.uint8)           # :696-808
    scanned = 0
    for c in range(sg.GDRL_COVERAGE_COLS):
        col = col0 + c
        if col > col1:                                                   # :698
            coverage[c] = int(sg.GdrlCoverage.UNKNOWN)
        elif col >= n_cols:                                              # :705
            coverage[c] = int(sg.GdrlCoverage.ABSENT)
        else:
            coverage[c] = int(sg.GdrlCoverage.SCANNED)
            scanned += 1

    # The (float) stores at :686-691. windowMin/MaxX are float32 on the wire, so
    # the comparison has to happen after that truncation or it is a comparison of
    # a precision the header cannot carry.
    return {
        "coverageStartCol": col0,
        "sectionColumns": n_cols,
        "windowMinX": float(np.float32(min_x)),
        "windowMaxX": float(np.float32(max_x)),
        "windowMinY": float(np.float32(min_y)),
        "windowMaxY": float(np.float32(max_y)),
        "coverage": coverage,
        "scanned": scanned,
        # Not published by the mod. Kept for the disagreement test below: it is
        # the left edge the OLD fixture advertised, i.e. column-primary.
        "snapped_min_x": float(np.float32(col0 * sec_w)),
    }


def _column_tickover_x(k: int) -> float:
    """Smallest world x whose section index ``floor(x * sxf)`` is ``k``.

    Derived from the measured sxf (0.009999999776482582), not tabulated: the
    boundary sits at ``k / sxf``, about ``k * 100.0000022``, and the two neighbours
    of it are where a window-primary and a column-primary left edge disagree by
    the largest amount that still keeps the column the same.
    """
    sxf = envmod.MEASURED_SECTION_X_FACTOR
    x = k * (1.0 / sxf)
    while math.floor(x * sxf) < k:
        x = math.nextafter(x, math.inf)
    while math.floor(math.nextafter(x, -math.inf) * sxf) >= k:
        x = math.nextafter(x, -math.inf)
    return x


def _f32_neighbours_of_player_x(min_x: float) -> tuple[float, float]:
    """The float32 player_x either side of the one that puts minX on ``min_x``.

    player_x arrives as ``p1->getPositionX()`` (8dd5ceb:927), a float
    widened to double, so only float32-representable placements are reachable in
    the game -- and the column boundary always falls strictly BETWEEN two of them
    at these magnitudes, which is what makes the pair straddle the tick-over.
    """
    exact = min_x + envmod.MOD_WIN_BEHIND
    hi = np.float32(exact)
    if float(hi) < exact:
        hi = np.nextafter(hi, np.float32(np.inf))
    lo = np.nextafter(np.float32(hi), np.float32(-np.inf))
    return float(lo), float(hi)


def _agreement_placements():
    """(id, player_x, win_behind, win_ahead, coverage_cols) for the pair below.

    Three groups, and each is here for a reason:

      * the placements the mask tests in this file actually stand on (500, 450,
        12345) -- 500 is the L5 worst case, a full section of phantom window;
      * player_x < g_winBehind, where minX goes negative and the ``col0 < 0``
        clamp at :665 engages while minX itself is published unclamped;
      * boundary-adjacent pairs straddling a column tick-over at k = 1, 5, 119,
        267, where window-primary and column-primary arithmetic diverge most.
    """
    out = [
        pytest.param(500.0, envmod.MOD_WIN_BEHIND, envmod.MOD_WIN_AHEAD, 4,
                     id="the-default-mask-placement"),
        pytest.param(450.0, envmod.MOD_WIN_BEHIND, envmod.MOD_WIN_AHEAD, 4,
                     id="px-450"),
        pytest.param(12345.0, envmod.MOD_WIN_BEHIND, envmod.MOD_WIN_AHEAD, 4,
                     id="px-12345-start-col-119"),
        pytest.param(500.0, envmod.MOD_WIN_BEHIND, 8000.0, sg.GDRL_COVERAGE_COLS,
                     id="window-wide-enough-that-the-64-col-clamp-binds"),
        # THE CONFIGURATION THE MOD ACTUALLY RUNS: all 64 columns available and
        # the mod's own 1400 ahead, so the WINDOW binds at 19 columns and the
        # clamp at :670 never does. Every other placement here has
        # coverage_cols small enough that the clamp wins, which makes the
        # requested edge, the +1 on col1 and the clamp itself unobservable --
        # three mutants survived on exactly that (D5, D6, D7).
        pytest.param(500.0, envmod.MOD_WIN_BEHIND, envmod.MOD_WIN_AHEAD,
                     sg.GDRL_COVERAGE_COLS, id="mod-defaults-window-binds"),
        pytest.param(12345.0, envmod.MOD_WIN_BEHIND, envmod.MOD_WIN_AHEAD,
                     sg.GDRL_COVERAGE_COLS, id="mod-defaults-window-binds-deep"),
        # Past the end of m_sections (268 columns, Stereo Madness), where the
        # fixture's own ABSENT branch is the only thing that can be right.
        pytest.param(26900.0, envmod.MOD_WIN_BEHIND, envmod.MOD_WIN_AHEAD,
                     sg.GDRL_COVERAGE_COLS, id="past-the-end-of-m-sections"),
        pytest.param(100.0, envmod.MOD_WIN_BEHIND, envmod.MOD_WIN_AHEAD, 4,
                     id="window-reaches-left-of-x-zero"),
        pytest.param(0.0, envmod.MOD_WIN_BEHIND, envmod.MOD_WIN_AHEAD, 4,
                     id="level-start"),
        pytest.param(500.0, 0.0, envmod.MOD_WIN_AHEAD, 4,
                     id="no-window-behind"),
    ]
    for k in (1, 5, 119, 267):
        lo, hi = _f32_neighbours_of_player_x(_column_tickover_x(k))
        out.append(pytest.param(lo, envmod.MOD_WIN_BEHIND, envmod.MOD_WIN_AHEAD, 8,
                                id=f"just-below-column-{k}"))
        out.append(pytest.param(hi, envmod.MOD_WIN_BEHIND, envmod.MOD_WIN_AHEAD, 8,
                                id=f"just-above-column-{k}"))
    return out


AGREEMENT_PLACEMENTS = _agreement_placements()


@pytest.mark.parametrize("player_x,win_behind,win_ahead,coverage_cols",
                         AGREEMENT_PLACEMENTS)
def test_the_fixture_publishes_the_window_the_mod_would(
        loop, player_x, win_behind, win_ahead, coverage_cols):
    """publish() must emit scanObjects()'s window, field for field.

    TIER (i), and deliberately so: both sides are this repo's transcription of
    telemetry.cpp:649-691, so agreement proves the transcription is consistent,
    not that either matches the game. It is worth having anyway -- the fixture
    silently drifted from the mod once already (L5) and every other test in this
    file was blind to it, because they all recompute their expectations from
    whatever window the header happens to carry.

    If someone edits publish() to derive the window from the column again, this
    fails and nothing else does.
    """
    game, chan = loop
    game.publish(tick=1, player_x=player_x, player_y=105.0,
                 coverage_cols=coverage_cols,
                 win_behind=win_behind, win_ahead=win_ahead)
    obs = chan.poll(timeout=1.0)
    h = obs.header

    want = _mod_scan_window(player_x, 105.0, envmod.MEASURED_SECTION_X_FACTOR,
                            coverage_cols=coverage_cols,
                            n_cols=envmod.MEASURED_SECTION_COLUMNS,
                            win_behind=win_behind, win_ahead=win_ahead,
                            win_vert=envmod.MOD_WIN_VERT)

    for field in ("coverageStartCol", "sectionColumns"):
        assert int(h[field]) == want[field], field
    for field in ("windowMinX", "windowMaxX", "windowMinY", "windowMaxY"):
        assert float(h[field]) == want[field], (
            f"{field}: fixture {float(h[field])!r} != mod {want[field]!r}")
    # The derived half: which coverage entries came out SCANNED / ABSENT /
    # UNKNOWN follows from col0 and col1, so a window that agreed by accident
    # while the columns disagreed is still caught. Compared entry by entry
    # rather than by count -- a count cannot tell ABSENT from SCANNED, and the
    # fixture's ABSENT branch had no test at all before this one.
    got_cov = np.asarray(obs.coverage())
    assert (got_cov == want["coverage"]).all(), (
        f"coverage differs at {np.nonzero(got_cov != want['coverage'])[0].tolist()}")
    assert game.scanned_cols == want["scanned"]


def test_a_column_primary_left_edge_is_a_different_window(loop):
    """The agreement test above is not vacuous: the two formulations differ.

    Window-primary (``minX = px - g_winBehind``, telemetry.cpp:710) and
    column-primary (``col0 * secW``, what publish() used to advertise) coincide
    only when minX lands exactly on a column boundary. Over the placements above
    they disagree on most, by up to a full section.

    The two exact gaps asserted here are the ones recorded in TODO L5 and
    re-measured on 2026-08-14 -- 500 -> 100.0 units, 12345 -> 45.0 units. Tier
    (i) regression: it pins the size of the defect L5 removed, so a partial
    revert cannot pass by shrinking the gap instead of closing it.
    """
    gaps = {}
    for p in AGREEMENT_PLACEMENTS:
        player_x, win_behind, win_ahead, coverage_cols = p.values
        w = _mod_scan_window(player_x, 105.0, envmod.MEASURED_SECTION_X_FACTOR,
                             coverage_cols=coverage_cols,
                             n_cols=envmod.MEASURED_SECTION_COLUMNS,
                             win_behind=win_behind, win_ahead=win_ahead,
                             win_vert=envmod.MOD_WIN_VERT)
        gaps[p.id] = w["windowMinX"] - w["snapped_min_x"]

    differing = [k for k, v in gaps.items() if v != 0.0]
    assert len(differing) >= len(gaps) - 2, (
        "almost every placement should distinguish the two left edges; "
        f"only {differing} do")
    assert gaps["the-default-mask-placement"] == 100.0
    assert gaps["px-12345-start-col-119"] == 45.0
    # Left of x=0 the mod publishes the unclamped minX while col0 is clamped to
    # 0, so the column-primary edge is 300 units to the RIGHT -- the one case
    # where snapping made the window too NARROW, not too wide.
    assert gaps["window-reaches-left-of-x-zero"] == -300.0
    assert max(gaps.values()) < 100.0 + 1e-6


def _cell_centres(obs, width: int, cell_size: float, player_col: int) -> np.ndarray:
    """World x of each raster column's centre, the way known_mask lays them out."""
    origin_x = float(obs.header["playerX"]) - player_col * cell_size
    return origin_x + (np.arange(width) + 0.5) * cell_size


def _expected_known_columns(obs, centres: np.ndarray, scanned_cols: int) -> np.ndarray:
    """Which raster columns SHOULD be known, derived a different way.

    known_mask() decides per cell, by flooring ``x * sxf`` into a coverage index.
    This decides by intersecting two world-x intervals instead:

      * the advertised window ``[windowMinX, windowMaxX]`` (inclusive, as the
        decoder reads it), and
      * the world-x range the SCANNED/ABSENT prefix of the coverage array covers,
        ``[startCol * w, (startCol + scanned_cols) * w)`` -- half open, because
        the right edge is the first x belonging to the next column.

    Same specification, different arithmetic: no floor, no array indexing, no
    coverage lookup. It is an independent reimplementation only in the tier-(ii)
    sense of being written from the spec rather than from the code -- it is still
    Python on both sides and says nothing about GD. ``scanned_cols`` is passed in
    because the caller, not the decoder, decides how the fixture filled coverage.
    """
    sec_w = obs.section_width()
    start = int(obs.header["coverageStartCol"])
    return ((centres >= float(obs.header["windowMinX"]))
            & (centres <= float(obs.header["windowMaxX"]))
            & (centres >= start * sec_w)
            & (centres < (start + scanned_cols) * sec_w))


def test_known_mask_marks_unscanned_columns_unknown(loop):
    """An off-screen pit and an empty floor must not be the same bytes."""
    game, chan = loop
    game.publish(tick=1, player_x=500.0, player_y=105.0, coverage_cols=4)
    obs = chan.poll(timeout=1.0)

    mask = obs.known_mask(height=32, width=48, cell_size=30.0, player_col=12).grid()
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
    mask = obs.known_mask(height=8, width=48, cell_size=30.0, player_col=12).grid()
    assert mask.any(axis=0).sum() > 4 * 100 / 30


def test_truncated_columns_are_not_known(loop):
    """The array filled mid-column, so what is missing is genuinely unknown."""
    game, chan = loop
    game.publish(tick=1, player_x=500.0, coverage_cols=8)
    game.obs["coverage"][:] = int(sg.GdrlCoverage.TRUNCATED)
    obs = chan.poll(timeout=1.0)
    mask = obs.known_mask(height=8, width=48, cell_size=30.0, player_col=12).grid()
    assert not mask.any()


# -- the refusal path -------------------------------------------------------
#
# section_factor() is the only thing standing between a corrupt header and a
# confident answer about geometry nobody indexed, and until these tests it was
# the one branch of the fix nothing exercised.
#
# All fixture values below come from one of three places, and each test says
# which: (a) the live 2026-08-12 dump, via envmod.MEASURED_* -- the section
# factor 0.01, level length 26724, 268 columns; (b) publish()'s own arithmetic,
# which follows scanObjects(); (c) an explicitly named fabrication, used only to
# reach a branch the honest frame cannot reach, and never asserted to be a state
# GD produces.

@pytest.mark.parametrize("sxf", UNUSABLE_SECTION_FACTORS)
def test_an_unusable_section_factor_is_refused_not_substituted(loop, sxf):
    """Every rejection reason, and what refusal has to look like downstream.

    This is the mod's own bad-factor frame, which publish() mirrors: zero-area
    window, all 64 columns UNKNOWN, OBJECTS_UNAVAILABLE set. The old code had
    ``or 100.0`` here. A substituted section width does not produce a wrong
    mask, it produces a confident one -- so the assertion is not 'the mask is
    empty' (a substituting decoder can also produce an empty mask, and on THIS
    frame it certainly would, from a window of zero area) but that every
    x -> column question answers None and that no grid comes out at all.

    Because the mod sets both refusal conditions on this frame, the mask's
    reason is OBJECTS_UNAVAILABLE: the decoder checks that first on purpose,
    since with no scan the factor field is stale too. The factor guard is
    isolated in the next test.
    """
    game, chan = loop
    game.publish(tick=1, player_x=500.0, section_x_factor=sxf)
    obs = chan.poll(timeout=1.0)

    assert obs.section_factor() is None
    assert obs.section_width() is None
    assert obs.column_span() is None, "a span here is a claim about columns nobody indexed"
    km = obs.known_mask(height=8, width=48, cell_size=30.0, player_col=12)
    assert km.shape == (8, 48)
    assert km.refused
    assert km.refusal is envmod.MaskRefusal.OBJECTS_UNAVAILABLE
    with pytest.raises(envmod.MaskRefused):
        km.grid()
    # The fixture mirrors the mod's own refusal: it also raises
    # OBJECTS_UNAVAILABLE, so a count of 0 reads as 'did not look'.
    assert "objects" in obs.unavailable_tables()
    # And the frame this test stands on is the mod's refusal frame -- claim
    # nothing (telemetry.cpp:620-634 as of 8dd5ceb): a zero-area window and all
    # 64 columns UNKNOWN. Nothing checked that, so publish() could have left a
    # real window here and every assertion above would still hold while the
    # frame had stopped being the one the docstring describes (mutation-measured
    # 2026-08-14: D15 survived).
    h = obs.header
    assert float(h["windowMinX"]) == float(h["windowMaxX"]) == float(h["playerX"])
    assert float(h["windowMinY"]) == float(h["windowMaxY"]) == float(h["playerY"])
    assert (np.asarray(obs.coverage()) == int(sg.GdrlCoverage.UNKNOWN)).all()


@pytest.mark.parametrize("sxf", UNUSABLE_SECTION_FACTORS)
def test_the_factor_refusal_stands_on_its_own(loop, sxf):
    """The bad-factor guard must not be load-bearing on OBJECTS_UNAVAILABLE.

    On the mod's own refusal frame the whole-frame OBJECTS_UNAVAILABLE check
    shadows the factor check, and the zero-area window means an empty mask
    proves nothing. ``layout_x_factor`` builds the geometry from the measured
    0.01 while the header carries the unusable value: a real 4-column window, a
    SCANNED coverage prefix, no OBJECTS_UNAVAILABLE, and the factor as the only
    defect. That header is a FABRICATION for the purpose of reaching one branch;
    it is NOT asserted to be a state GD produces.

    The control frame below is the same fixture with the measured factor in the
    header, and it yields a non-empty grid -- so the refusal here cannot be an
    artifact of a degenerate window, which is precisely how the previous version
    of this test passed against a decoder that substitutes 0.01.
    """
    game, chan = loop

    game.publish(tick=1, player_x=500.0, coverage_cols=4,
                 layout_x_factor=envmod.MEASURED_SECTION_X_FACTOR)
    control = chan.poll(timeout=1.0)
    assert control.known_mask(height=8, width=48, cell_size=30.0,
                              player_col=12).grid().any(), (
        "control frame is empty; the refusal below would prove nothing")

    game.publish(tick=2, player_x=500.0, coverage_cols=4, section_x_factor=sxf,
                 layout_x_factor=envmod.MEASURED_SECTION_X_FACTOR)
    obs = chan.poll(timeout=1.0)

    assert "objects" not in obs.unavailable_tables()
    assert int(obs.coverage()[0]) == int(sg.GdrlCoverage.SCANNED)
    assert float(obs.header["windowMaxX"]) > float(obs.header["windowMinX"])
    assert float(obs.header["windowMaxY"]) > float(obs.header["windowMinY"])

    km = obs.known_mask(height=8, width=48, cell_size=30.0, player_col=12)
    assert km.refusal is envmod.MaskRefusal.NO_SECTION_MAPPING, (
        "an unusable factor was substituted for instead of refused")
    with pytest.raises(envmod.MaskRefused):
        km.grid()
    assert obs.column_span() is None


def test_objects_unavailable_refuses_the_whole_frame(loop):
    """The no-player path leaves last frame's coverage in place; do not read it.

    The factor is the measured one and the coverage array says four columns were
    SCANNED, so every ingredient for a confident mask is present. The flag alone
    has to veto it.
    """
    game, chan = loop
    game.publish(tick=1, player_x=500.0, coverage_cols=4,
                 extra_flags=int(sg.GdrlHeaderFlag.OBJECTS_UNAVAILABLE))
    obs = chan.poll(timeout=1.0)

    assert obs.section_factor() is not None
    assert int(obs.coverage()[0]) == int(sg.GdrlCoverage.SCANNED)
    km = obs.known_mask(height=8, width=48, cell_size=30.0, player_col=12)
    assert km.refusal is envmod.MaskRefusal.OBJECTS_UNAVAILABLE
    with pytest.raises(envmod.MaskRefused):
        km.grid()


def test_a_refusal_cannot_be_spent_as_an_empty_grid(loop):
    """The type has to make the mistake impossible, not merely detectable.

    A consumer that never asks about the refusal must not end up holding
    something that behaves like an all-False mask. Every route from a refused
    KnownMask to an array is checked here: .grid(), np.asarray(), truthiness,
    and the ndarray methods a caller would reach for out of habit. The frame is
    the mod's own bad-factor refusal (fixture (b), publish()'s refusal path).
    """
    game, chan = loop
    game.publish(tick=1, player_x=500.0, section_x_factor=0.0)
    obs = chan.poll(timeout=1.0)
    km = obs.known_mask(height=8, width=48, cell_size=30.0, player_col=12)

    with pytest.raises(envmod.MaskRefused):
        km.grid()
    with pytest.raises(envmod.MaskRefused):
        np.asarray(km)                       # cannot be laundered into an array
    with pytest.raises(TypeError):
        bool(km)                             # not silently truthy, not falsy
    for attr in ("any", "all", "sum", "astype", "__getitem__", "__len__", "__iter__"):
        assert not hasattr(km, attr), f"KnownMask.{attr} lets a caller skip the refusal"
    # A refusal has to be legible when it is printed into a log, too.
    assert "REFUSED" in repr(km)


def test_looked_and_knew_nothing_still_yields_a_grid(loop):
    """All-False is an answer, and it must survive as one.

    TRUNCATED everywhere: the mod looked, the mapping is fine, and it cannot
    vouch for a single column. That is knowledge, not a refusal, and collapsing
    it into the refusal branch would be the same erasure in the other direction.
    """
    game, chan = loop
    game.publish(tick=1, player_x=500.0, coverage_cols=4)
    game.obs["coverage"][:] = int(sg.GdrlCoverage.TRUNCATED)
    obs = chan.poll(timeout=1.0)

    km = obs.known_mask(height=8, width=48, cell_size=30.0, player_col=12)
    assert km.refusal is None and not km.refused
    grid = km.grid()
    assert grid.shape == (8, 48)
    assert not grid.any()


def test_refused_and_looked_but_knew_nothing_are_distinguishable(loop):
    """Four frames that a bare all-False array would have flattened into one.

    * TRUNCATED everywhere -- the mod looked, and cannot vouch for what it saw.
    * unusable factor, geometry otherwise normal -- no mapping from x to a
      column at all (fabricated via layout_x_factor; see that test above).
    * OBJECTS_UNAVAILABLE with a good factor -- the mod did not look this frame.
    * the mod's own bad-factor refusal, which raises both conditions at once.

    The old ``known_mask()`` returned the same bytes for all four. What is
    pinned now is that the difference is carried by the mask itself -- the
    refusal reason -- and, independently, that it also survives on the
    Observation, so a consumer that only has the header can still tell them
    apart: ``column_span()`` is None exactly when the factor is unusable, and
    ``unavailable_tables()`` names objects exactly when the flag is set.
    """
    game, chan = loop

    game.publish(tick=1, player_x=500.0, coverage_cols=4)
    game.obs["coverage"][:] = int(sg.GdrlCoverage.TRUNCATED)
    truncated = chan.poll(timeout=1.0)

    game.publish(tick=2, player_x=500.0, coverage_cols=4, section_x_factor=0.0,
                 layout_x_factor=envmod.MEASURED_SECTION_X_FACTOR)
    bad_factor = chan.poll(timeout=1.0)

    game.publish(tick=3, player_x=500.0, coverage_cols=4,
                 extra_flags=int(sg.GdrlHeaderFlag.OBJECTS_UNAVAILABLE))
    no_look = chan.poll(timeout=1.0)

    game.publish(tick=4, player_x=500.0, section_x_factor=0.0)
    mod_refusal = chan.poll(timeout=1.0)

    kw = dict(height=8, width=48, cell_size=30.0, player_col=12)
    fingerprints = []
    for obs in (truncated, bad_factor, no_look, mod_refusal):
        km = obs.known_mask(**kw)
        if km.refused:
            with pytest.raises(envmod.MaskRefused):
                km.grid()
        else:
            assert not km.grid().any()
        fingerprints.append((km.refusal,
                             obs.column_span() is None,
                             "objects" in obs.unavailable_tables()))

    R = envmod.MaskRefusal
    assert fingerprints[0] == (None, False, False)                  # looked, cannot vouch
    assert fingerprints[1] == (R.NO_SECTION_MAPPING, True, False)   # cannot index at all
    assert fingerprints[2] == (R.OBJECTS_UNAVAILABLE, False, True)  # did not look
    assert fingerprints[3] == (R.OBJECTS_UNAVAILABLE, True, True)   # the mod's own refusal
    assert len(set(fingerprints)) == 4, "two refusals are indistinguishable"
    # and the one that is not a refusal is the only one that hands out a grid.
    assert sum(f[0] is None for f in fingerprints) == 1


# -- what the mask actually indexes -----------------------------------------

@pytest.mark.parametrize("player_x,player_col,cell_size,scanned,win_ahead", [
    # start_col == 0; the case every pre-existing test used.
    pytest.param(500.0, 12, 30.0, 4, envmod.MOD_WIN_AHEAD, id="start-col-zero"),
    # A cell straddling the right window edge: centre outside, left edge inside.
    pytest.param(450.0, 12, 30.0, 4, envmod.MOD_WIN_AHEAD, id="cell-straddles-the-edge"),
    # Deep into the level, so coverageStartCol is 119 rather than 0, and the
    # leftmost cells the WINDOW admits land in the FIRST covered column
    # (section == 0). Note the window starts at 11945, partway into column 119,
    # so the cells at 11880..11940 are in that column but outside the window --
    # which is the left-edge disagreement, and it resolves to UNKNOWN.
    pytest.param(12345.0, 16, 30.0, 4, envmod.MOD_WIN_AHEAD, id="nonzero-start-col"),
    pytest.param(12345.0, 12, 30.0, 4, envmod.MOD_WIN_AHEAD, id="nonzero-start-col-shifted"),
    # The full 64-column array with cells wide enough to reach its far end. This
    # needs a window that actually reaches column 63: with the mod's default
    # 1400 ahead the window binds at 19 columns and the 64-column clamp never
    # does, so the far end of the array would never be exercised. 8000 is a
    # value of GDRL_ENV_WIN_AHEAD, not a fabricated header.
    pytest.param(500.0, 2, 200.0, sg.GDRL_COVERAGE_COLS, 8000.0, id="whole-coverage-array"),
])
def test_the_known_columns_are_the_window_intersected_with_the_scan(
        loop, player_x, player_col, cell_size, scanned, win_ahead):
    """Which raster columns are known, checked by interval arithmetic instead.

    Tier (ii) against ``_expected_known_columns``, which answers the same
    question without flooring anything or touching the coverage array; tier (i)
    with respect to GD, since both sides are this repo's own description of the
    protocol. Parametrised over the placements that move the parts of the
    indexing expression independently: coverageStartCol away from 0 (a sign or a
    dropped term stops cancelling), a cell whose centre and left edge fall on
    opposite sides of the window edge, and cells that run off the end of the
    64-column array.
    """
    game, chan = loop
    game.publish(tick=1, player_x=player_x, player_y=105.0, coverage_cols=scanned,
                 win_ahead=win_ahead)
    assert game.scanned_cols == scanned, (
        "the fixture's window bound before the mask did, so this case is not "
        "testing the geometry it names")
    obs = chan.poll(timeout=1.0)

    mask = obs.known_mask(height=8, width=48, cell_size=cell_size, player_col=player_col).grid()
    centres = _cell_centres(obs, 48, cell_size, player_col)
    expected = _expected_known_columns(obs, centres, scanned)

    assert expected.any(), "fixture placed nothing known in view; the case is vacuous"
    assert not expected.all(), "fixture covered the whole view; boundaries untested"
    got = mask.any(axis=0)
    bad = np.nonzero(got != expected)[0]
    assert bad.size == 0, (
        f"columns {bad.tolist()} at x={centres[bad].tolist()} disagree: "
        f"mask={got[bad].tolist()} expected={expected[bad].tolist()}")


def test_the_first_covered_column_is_included(loop):
    """coverage[0] is a column the mod walked, not a fencepost.

    With coverageStartCol == 119 the leftmost cells in view index to section 0.
    A lower bound of ``> 0`` instead of ``>= 0`` would silently drop them, and
    every pre-existing test was blind to it because the fixture's start column
    was 0 and no cell ever indexed there.
    """
    game, chan = loop
    game.publish(tick=1, player_x=12345.0, coverage_cols=4)
    obs = chan.poll(timeout=1.0)
    start = int(obs.header["coverageStartCol"])
    assert start > 0, "fixture no longer produces a nonzero start column"

    centres = _cell_centres(obs, 48, 30.0, 16)
    first_col = np.floor(centres * obs.section_factor()).astype(np.int64) - start == 0
    assert first_col.any(), "no cell in view indexes to the first covered column"

    # Only the cells the advertised window actually admits. Since windowMinX is
    # `player_x - win_behind` (telemetry.cpp:710) and col0 is floor(minX * sxf)
    # (:664), the first covered column starts LEFT of the window: the mod walks
    # the whole of column col0 but advertises a window that begins partway into
    # it. So a cell can index to section 0 and still, correctly, be outside the
    # window. Asserting over those was testing the old fixture's snapped-left
    # window, not the mod's.
    in_win = ((centres >= float(obs.header["windowMinX"]))
              & (centres <= float(obs.header["windowMaxX"])))
    target = first_col & in_win
    assert target.any(), "no in-window cell indexes to the first covered column"

    mask = obs.known_mask(height=8, width=48, cell_size=30.0, player_col=16).grid()
    assert mask.any(axis=0)[target].all()


def test_columns_past_the_end_of_the_coverage_array_are_unknown(loop):
    """Index 64 must not read as a copy of index 63.

    The decoder clips the index before indexing so the gather stays in bounds;
    the clip is only safe because ``valid`` throws the clipped entries away
    afterwards. Here the whole array is SCANNED and the window is widened past
    its right edge by hand (a fabrication, to reach the out-of-range branch --
    the mod is not claimed to advertise a window wider than it scanned), so a
    clip that survived into the result would read as extra known columns.

    The raster is 50 units per cell so that EVERY 100-unit section past the end
    contains at least one cell centre -- including section 64 itself, the single
    index an upper bound written ``<=`` would let through. A coarser raster can
    step straight over it and the mutant lives.

    ``win_ahead`` is 8000 (a value of GDRL_ENV_WIN_AHEAD, telemetry.cpp:185, not
    a fabricated header) because the whole array has to actually be SCANNED for
    the out-of-range read to be distinguishable. It was NOT, from the moment the
    fixture started binding col1 to the requested window (L5): with the mod's
    default 1400 ahead only 19 columns are SCANNED and coverage[63] is UNKNOWN,
    so the clipped read returned UNKNOWN and the ``<=`` mutant survived while
    this test stayed green. The guard below is the part that would have said so.
    """
    game, chan = loop
    game.publish(tick=1, player_x=450.0, coverage_cols=sg.GDRL_COVERAGE_COLS,
                 win_ahead=8000.0)
    assert (np.asarray(game.obs["coverage"]) == int(sg.GdrlCoverage.SCANNED)).all(), (
        "this test needs the whole coverage array SCANNED, or a clipped "
        "out-of-range read is indistinguishable from an honest UNKNOWN")
    start = int(game.obs["header"]["coverageStartCol"])
    end_x = (start + sg.GDRL_COVERAGE_COLS) / envmod.MEASURED_SECTION_X_FACTOR
    game.obs["header"]["windowMaxX"] = end_x + 2000.0
    obs = chan.poll(timeout=1.0)

    centres = _cell_centres(obs, 200, 50.0, 0)
    beyond = centres >= end_x
    assert beyond.sum() >= 5, "fixture no longer puts cells past the array"
    first_past = np.floor(centres[beyond] * obs.section_factor()).astype(np.int64) - start
    assert first_past.min() == sg.GDRL_COVERAGE_COLS, "no cell indexes the first out-of-range column"

    mask = obs.known_mask(height=8, width=200, cell_size=50.0, player_col=0).grid()
    assert not mask.any(axis=0)[beyond].any()


def test_a_scanned_column_outside_the_window_is_not_known(loop):
    """Being in the coverage array is not on its own enough to be known.

    known_mask() ANDs the coverage state with the advertised window. The window
    is narrowed here to half the scanned span by hand -- the fixture normally
    makes the two agree exactly, which is why nothing caught the AND being
    dropped. Tier (i): it pins the documented conjunction, not the game.
    """
    game, chan = loop
    game.publish(tick=1, player_x=500.0, coverage_cols=4)
    game.obs["header"]["windowMaxX"] = 200.0
    obs = chan.poll(timeout=1.0)

    centres = _cell_centres(obs, 48, 30.0, 12)
    outside = (centres > 200.0) & (centres < 400.0)
    assert outside.any()
    # Those columns really are SCANNED -- the only thing excluding them is the window.
    idx = np.floor(centres[outside] * obs.section_factor()).astype(np.int64)
    assert (obs.coverage()[idx] == int(sg.GdrlCoverage.SCANNED)).all()

    mask = obs.known_mask(height=8, width=48, cell_size=30.0, player_col=12).grid()
    assert not mask.any(axis=0)[outside].any()


def test_the_player_row_sits_at_half_the_grid_height(loop):
    """Rows are laid out from the player, and one row off is a real error.

    Tier (i): this pins the raster convention known_mask() shares with
    TrajectoryRaster (origin_y = playerY - (height // 2) * cell), nothing about
    GD. The vertical window is narrowed by hand to +/- 20 so that exactly the two
    rows whose centres bracket the player survive; a shifted origin moves which
    two those are.
    """
    game, chan = loop
    game.publish(tick=1, player_x=500.0, player_y=105.0, coverage_cols=4)
    game.obs["header"]["windowMinY"] = 105.0 - 20.0
    game.obs["header"]["windowMaxY"] = 105.0 + 20.0
    obs = chan.poll(timeout=1.0)

    height = 32
    mask = obs.known_mask(height=height, width=48, cell_size=30.0, player_col=12).grid()
    rows = set(np.nonzero(mask.any(axis=1))[0].tolist())
    assert rows == {height // 2 - 1, height // 2}


def test_both_window_edges_are_inclusive_in_both_axes(loop):
    """A cell centre exactly on a window edge is inside the window.

    Tier (i): it pins the decoder's convention (``>=`` / ``<=`` on all four
    edges, env.py known_mask), which mirrors the mod's own object filter --
    ``m_positionY < minY || m_positionY > maxY`` at 8dd5ceb:762, i.e.
    rejects strictly outside, keeps the edge.

    Every other test places cell centres strictly inside the window, so the
    vertical edges could be flipped to ``>`` / ``<`` with the whole suite green
    (mutation-measured 2026-08-14: A15 survived). The four edges are set BY HAND
    onto exact cell centres; that is a fabricated header, and it is the only way
    to put a centre on an edge without relying on a coincidence of the fixture's
    arithmetic.
    """
    game, chan = loop
    game.publish(tick=1, player_x=500.0, player_y=105.0, coverage_cols=4)
    height, width, cell, player_col = 8, 48, 30.0, 12

    # The same layout known_mask() uses (env.py: origin_x / origin_y).
    origin_x = 500.0 - player_col * cell
    origin_y = 105.0 - (height // 2) * cell
    cols_x = origin_x + (np.arange(width) + 0.5) * cell
    rows_y = origin_y + (np.arange(height) + 0.5) * cell

    lo_row, hi_row = 2, 5
    # Columns 3 and 6 have centres at 245 and 335, inside the SCANNED span
    # [0, 400) that player_x=500 with coverage_cols=4 produces -- so the only
    # thing deciding the edges here is the window comparison.
    lo_col, hi_col = 3, 6
    h = game.obs["header"]
    h["windowMinY"], h["windowMaxY"] = rows_y[lo_row], rows_y[hi_row]
    h["windowMinX"], h["windowMaxX"] = cols_x[lo_col], cols_x[hi_col]
    obs = chan.poll(timeout=1.0)

    mask = obs.known_mask(height=height, width=width, cell_size=cell,
                          player_col=player_col).grid()
    assert set(np.nonzero(mask.any(axis=1))[0].tolist()) == set(range(lo_row, hi_row + 1))
    assert set(np.nonzero(mask.any(axis=0))[0].tolist()) == set(range(lo_col, hi_col + 1))


# -- the factor itself, against the live measurement -------------------------

def test_the_section_factor_reproduces_the_measured_section_count(loop):
    """Tier (iii): the arithmetic has to close on numbers read out of the game.

    m_levelLength 26724 and m_sections.size() 268 were dumped live from Stereo
    Madness on 2026-08-12 (envmod.MEASURED_LEVEL_LENGTH / _SECTION_COLUMNS).
    ``floor(length * sxf) + 1 == 268`` closes exactly under the multiplier
    reading. The divisor reading -- the defect this file failed to catch for its
    whole life -- would need 2,672,401 columns, so the same expression written
    with a division is checked here to NOT close.
    """
    game, chan = loop
    game.publish(tick=1)
    obs = chan.poll(timeout=1.0)

    sxf = obs.section_factor()
    assert sxf == envmod.MEASURED_SECTION_X_FACTOR
    assert math.floor(envmod.MEASURED_LEVEL_LENGTH * sxf) + 1 == envmod.MEASURED_SECTION_COLUMNS
    assert math.floor(envmod.MEASURED_LEVEL_LENGTH / sxf) + 1 != envmod.MEASURED_SECTION_COLUMNS
    # section_width() is 1 / sxf, i.e. 100 units per column, not the factor.
    assert obs.section_width() == pytest.approx(100.0, abs=1e-3)


def test_the_level_length_cross_check_agrees_on_both_measured_levels(loop):
    """``floor(levelLength * sxf) + 1 == sectionColumns``, on the two live pairs.

    Tier (iii) in its inputs: 26724 -> 268 (Stereo Madness) and 6340 -> 64 (the
    synth test level) were both dumped from the running game on 2026-08-12
    (telemetry.cpp:597-600), and they are the ONLY two levels this identity has
    ever been measured on -- which is exactly why L6 made it a diagnostic and not
    a gate. The arithmetic being checked is this repo's, so the check itself is
    tier (i); what is tier (iii) is that it has to close on those numbers.

    Written because the diagnostic had no test at all: mutation-measured
    2026-08-14, both dropping the ``+ 1`` and replacing the whole comparison with
    ``return True`` survived the entire suite (C7, C8).
    """
    game, chan = loop
    for length, cols in ((envmod.MEASURED_LEVEL_LENGTH, envmod.MEASURED_SECTION_COLUMNS),
                         (envmod.MEASURED_SYNTH_LEVEL_LENGTH,
                          envmod.MEASURED_SYNTH_SECTION_COLUMNS)):
        game.publish(tick=1, level_length=length, section_columns=cols)
        obs = chan.poll(timeout=1.0)
        assert obs.level_length_agrees_with_section_count() is True, (length, cols)


@pytest.mark.parametrize("sxf,agrees_stereo,agrees_synth", [
    # The measured factor, and the four wrong readings L6 claims it rejects.
    pytest.param(envmod.MEASURED_SECTION_X_FACTOR, True, True, id="measured"),
    pytest.param(100.0, False, False, id="the-divisor-reading"),
    pytest.param(1e-30, False, False, id="positive-finite-and-absurd"),
    pytest.param(0.005, False, False, id="half"),
    pytest.param(0.0101, False, False, id="one-percent-high"),
    # And the honest limit of it: the acceptance band is 0.37% wide on Stereo
    # Madness and 1.58% on the synth level, so a factor can be wrong and still
    # pass on the shorter one. 0.01009 is that value -- computed from the band
    # [63/6340, 64/6340), not tabulated.
    pytest.param(0.01009, False, True, id="inside-the-synth-band-only"),
])
def test_the_level_length_cross_check_rejects_the_wrong_readings(
        loop, sxf, agrees_stereo, agrees_synth):
    """What the diagnostic can and cannot see, including where it is blind.

    The ``inside-the-synth-band-only`` case is the reason L6 refuses to let this
    gate ``known_mask()``: on the level this repo tests most, a 0.9% error in the
    factor passes. A gate that weak, blanking an entire level's uncertainty
    channel when it fires, is a worse failure than reporting and continuing.
    """
    game, chan = loop
    game.publish(tick=1, section_x_factor=sxf,
                 layout_x_factor=envmod.MEASURED_SECTION_X_FACTOR)
    obs = chan.poll(timeout=1.0)
    assert obs.level_length_agrees_with_section_count() is agrees_stereo

    game.publish(tick=2, section_x_factor=sxf,
                 layout_x_factor=envmod.MEASURED_SECTION_X_FACTOR,
                 level_length=envmod.MEASURED_SYNTH_LEVEL_LENGTH,
                 section_columns=envmod.MEASURED_SYNTH_SECTION_COLUMNS)
    obs = chan.poll(timeout=1.0)
    assert obs.level_length_agrees_with_section_count() is agrees_synth


def test_the_level_length_cross_check_is_a_diagnostic_not_a_gate(loop):
    """It answers None when it cannot ask, and it never blanks the mask.

    L6's decision, pinned: a factor that is positive, finite and absurd
    (1e-30 collapses every world x onto column 0) is REPORTED as disagreeing and
    still produces a grid, because a false reject would present as "refused
    everywhere" -- indistinguishable from a genuinely corrupt factor, which is
    the exact confusion L4 exists to remove.
    """
    game, chan = loop
    # Cannot ask: the factor is unusable, so there is no arithmetic to do.
    game.publish(tick=1, section_x_factor=0.0)
    assert chan.poll(timeout=1.0).level_length_agrees_with_section_count() is None

    # Cannot ask: levelLength / sectionColumns unpopulated. Fabricated headers,
    # named as such -- they stand in for a frame where the mod could not read
    # m_levelLength or m_sections.
    game.publish(tick=2, level_length=0.0)
    assert chan.poll(timeout=1.0).level_length_agrees_with_section_count() is None
    game.publish(tick=3, section_columns=0)
    assert chan.poll(timeout=1.0).level_length_agrees_with_section_count() is None

    # Disagrees, and still hands out a grid rather than a refusal.
    game.publish(tick=4, player_x=500.0, coverage_cols=4, section_x_factor=1e-30,
                 layout_x_factor=envmod.MEASURED_SECTION_X_FACTOR)
    obs = chan.poll(timeout=1.0)
    assert obs.level_length_agrees_with_section_count() is False
    assert obs.section_factor() is not None, "still a usable multiplier, however absurd"
    km = obs.known_mask(height=8, width=48, cell_size=30.0, player_col=12)
    assert not km.refused
    km.grid()
    assert not obs.problems(), "the diagnostic must not be wired into problems()"


def test_column_span_is_exactly_what_the_coverage_array_indexes(loop):
    """The span's endpoints, checked by re-indexing them.

    Not by restating ``start * w, (start + COLS) * w`` -- that would only prove
    the line was copied correctly. The low end must index to the start column and
    the high end must be the first x that indexes one past the last column the
    array holds, using the mod's own floor(x * sxf) expression.
    """
    game, chan = loop
    game.publish(tick=1, player_x=12345.0, coverage_cols=4)
    obs = chan.poll(timeout=1.0)

    start = int(obs.header["coverageStartCol"])
    sxf = obs.section_factor()
    lo, hi = obs.column_span()
    assert math.floor(lo * sxf) == start
    assert math.floor(hi * sxf) == start + sg.GDRL_COVERAGE_COLS
    assert math.floor(math.nextafter(hi, 0.0) * sxf) == start + sg.GDRL_COVERAGE_COLS - 1


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
