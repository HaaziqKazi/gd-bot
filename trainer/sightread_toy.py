"""A puppet that speaks the env protocol. NOT a simulator of Geometry Dash.

WHAT THIS IS FOR
----------------

``trainer/sightread.py`` cannot be tested against GD in CI: there is one game
slot, it is contended, and a test that needs it is a test that does not get run.
So the search driver is exercised against this, which implements the game half of
the protocol -- the seqlock, the observation stride, the schedule, the attempt
reset -- over an anonymous mapping, plus the crudest possible cube.

WHAT A GREEN TEST AGAINST IT PROVES, AND WHAT IT DOES NOT
---------------------------------------------------------

It proves the *driver*: that it replays a committed prefix, that it backtracks,
that it counts every probe, that it never touches a forbidden field, and that it
scales its search by quantities it measured rather than by constants.

It proves **nothing whatsoever about Geometry Dash**, and one assumption
deserves to be named at the top of the file rather than buried:

    **This puppet re-jumps on landing while the button is held, because that is
    the behaviour the design is a bet on.** The claim comes from Rex, recorded
    2026-08-15, and is UNMEASURED (TODO, "Holding the jump button auto-repeats").
    Writing the puppet to behave that way and then passing a test against it is
    circular as evidence about the game -- deliberately so, and stated so nobody
    later cites a green suite as confirmation. Only the live game can confirm it.

Every other number here (jump velocity, gravity, hazard placement, level length)
is invented to make a small, fast, deterministic level. None of them are claims.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field

import numpy as np

import env as envmod
from env import (
    MEASURED_SECTION_X_FACTOR,
    ObservationRejected,
    SyntheticGame,
)
from schema_generated import GdrlStatus

#: Units of world x per tick. The documented 1x figure (README), used so the
#: puppet's scale resembles the game's. The driver MEASURES this rather than
#: importing it; that it happens to match here is a convenience, not a channel.
TOY_UNITS_PER_TICK = 1.298250437

#: Jump arc. Chosen for a 48-tick hop peaking ~90 units above the floor, which
#: is roughly cube-shaped. Invented.
TOY_GROUND_Y = 105.0
TOY_JUMP_V0 = 7.5
TOY_GRAVITY = 0.3125

#: The puppet's sensor window, in the shape item 1 measured (ahead is much
#: shorter than the old 1400 constant). Deliberately NOT the mod's defaults: a
#: driver that hardcoded a width would pass against the defaults and fail here.
TOY_WIN_AHEAD = 200.0
TOY_WIN_BEHIND = 120.0
TOY_WIN_VERT = 215.0


@dataclass(frozen=True)
class Hazard:
    """Something you have to be above when you get there."""

    x: float
    half_w: float = 5.0
    clear_y: float = 125.0          # be at least this high when crossing

    def kills(self, px: float, py: float, half_w: float = 5.0) -> bool:
        return abs(px - self.x) <= (self.half_w + half_w) and py < self.clear_y


@dataclass
class ToyLevel:
    hazards: tuple[Hazard, ...]
    length: float

    @property
    def section_columns(self) -> int:
        return int(math.floor(self.length * MEASURED_SECTION_X_FACTOR)) + 1


def default_level() -> ToyLevel:
    """A short level with three kinds of problem in it.

    * a lone hazard, clearable by any of a wide band of jump ticks;
    * a **pair** close enough together that one hop cannot clear both -- the
      case the interval action space is supposed to collapse into a single held
      input, and the case the old greedy tap search stalled on;
    * a third hazard soon after the pair, whose clearance depends on the *phase*
      of the hop chain, so the right action there only pays off given a specific
      choice at the pair. That is the backtracking case, and it is present by
      construction rather than by luck.
    """
    return ToyLevel(
        hazards=(
            Hazard(x=130.0), Hazard(x=190.0), Hazard(x=250.0),
            Hazard(x=320.0), Hazard(x=390.0),
            Hazard(x=470.0), Hazard(x=540.0),
        ),
        length=620.0,
    )


class ToyGame:
    """The game side: physics, attempts, and the protocol.

    Runs on its own thread because ``Channel.poll`` blocks. The thread does the
    ping-pong the mod does: publish, wait for the answer, apply the schedule,
    simulate ``advanceSteps`` ticks, publish again.
    """

    def __init__(self, buf, level: ToyLevel | None = None, *,
                 units_per_tick: float = TOY_UNITS_PER_TICK,
                 start_attempt: int = 7):
        self.game = SyntheticGame(buf)
        self.level = level or default_level()
        self.upt = units_per_tick
        self.attempt = start_attempt
        self.tick = 0
        self.deaths = 0
        self.completions = 0
        self.max_x_seen = 0.0
        self.best_x_ever = 0.0
        # (tick, push) events, mirroring the mod's schedule table. Cleared on
        # reset, exactly as telemetry.cpp's resetLevel hook does -- a plan does
        # not survive its attempt.
        self._events: list[tuple[int, bool]] = []
        self._button = False
        self._completed = False
        #: Inputs the driver asked for after their tick had gone by. The mod
        #: fires those anyway and logs a protocol error; a nonzero count means
        #: the driver's placement is wrong, so tests assert it is zero.
        self.late_events = 0
        self._reset_player()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.published = 0

    # -- physics ----------------------------------------------------------
    def _reset_player(self) -> None:
        self.x = 0.0
        self.y = TOY_GROUND_Y
        self.vy = 0.0
        self.on_ground = True
        self.max_x_seen = 0.0

    def _reset_attempt(self) -> None:
        self.attempt += 1
        self.tick = 0
        self._events.clear()
        self._button = False
        self._completed = False
        self._reset_player()

    def _step_physics(self) -> str:
        """One tick. Returns "" , "dead" or "done"."""
        # Auto-repeat: a held button re-jumps on every landing. THE ASSUMPTION.
        if self.on_ground and self._button:
            self.vy = TOY_JUMP_V0
            self.on_ground = False

        self.x += self.upt
        if not self.on_ground:
            self.y += self.vy
            self.vy -= TOY_GRAVITY
            if self.y <= TOY_GROUND_Y:
                self.y = TOY_GROUND_Y
                self.vy = 0.0
                self.on_ground = True

        self.max_x_seen = max(self.max_x_seen, self.x)
        self.best_x_ever = max(self.best_x_ever, self.x)

        for h in self.level.hazards:
            if h.kills(self.x, self.y):
                self.deaths += 1
                return "dead"
        if self.x >= self.level.length:
            self.completions += 1
            return "done"
        return ""

    # -- the wire ---------------------------------------------------------
    def _visible_objects(self) -> list[dict]:
        lo, hi = self.x - TOY_WIN_BEHIND, self.x + TOY_WIN_AHEAD
        out = []
        for i, h in enumerate(self.level.hazards):
            if lo <= h.x <= hi and abs(TOY_GROUND_Y - self.y) <= TOY_WIN_VERT:
                out.append({
                    "uniqueID": 1000 + i,
                    "objectID": 8,
                    "objectType": 2,            # GameObjectType::Hazard
                    "x": h.x,
                    "y": TOY_GROUND_Y,
                    "halfW": h.half_w,
                    "halfH": (h.clear_y - TOY_GROUND_Y),
                })
        return out

    def _publish(self) -> None:
        """Publish the current state, with a LIVE player block.

        ``SyntheticGame.publish`` hardcodes ``yVelocity = 0`` and
        ``isOnGround = 1``, which would make the driver's airtime measurement
        impossible -- so the player block is patched afterwards, inside the same
        seqlock write.

        The trick that makes that race-free is the pre-decrement below.
        ``publish`` does ``seq += 1`` (marking a write in progress), fills the
        record, then ``seq += 1`` again (marking it complete). Starting it one
        lower makes those two writes land on (even, odd) instead of (odd, even),
        and the even one it writes is *equal to* the sequence the reader last
        accepted. ``Channel.poll`` accepts only ``seq > last_seq and seq % 2 ==
        0``, so that intermediate value is refused on the ``>`` and the odd one
        on the parity: the reader cannot observe the record until the final even
        write below, after the player block is right. Without this the reader
        can legitimately accept a frame carrying publish()'s placeholder body.
        """
        g = self.game
        g.seq -= 1
        g.publish(
            tick=self.tick,
            attempt=self.attempt,
            player_x=self.x,
            player_y=self.y,
            objects=self._visible_objects(),
            level_length=self.level.length,
            section_columns=self.level.section_columns,
            win_behind=TOY_WIN_BEHIND,
            win_ahead=TOY_WIN_AHEAD,
            win_vert=TOY_WIN_VERT,
        )
        p0 = g.obs["players"][0]
        p0["yVelocity"] = self.vy
        p0["isOnGround"] = 1 if self.on_ground else 0
        g.seq += 1
        g.obs["seq"] = g.seq
        g.control["obsSeq"] = g.seq
        self.published += 1

    # -- the loop ---------------------------------------------------------
    def _await_answer(self, deadline: float) -> bool:
        while int(self.game.control["actSeq"]) != self.game.seq:
            if self._stop.is_set() or time.monotonic() > deadline:
                return False
            # A real sleep, not a spin: two Python threads spinning against each
            # other are bounded by the GIL switch interval (5 ms by default),
            # which would make every measurement here a measurement of the
            # scheduler. See bench_loopback() in sightread.py.
            time.sleep(0.00002)
        return True

    def serve(self, max_seconds: float = 120.0) -> None:
        end = time.monotonic() + max_seconds
        self._publish()
        while not self._stop.is_set() and time.monotonic() < end:
            if not self._await_answer(min(end, time.monotonic() + 5.0)):
                return
            try:
                advance = self.game.consume()
            except ObservationRejected:
                return
            self._drain_schedule()

            if self._completed:
                # The completion frame has been shown; the next answer starts a
                # new attempt, the way a real level-complete does.
                self._reset_attempt()
                self._publish()
                continue

            for _ in range(max(1, advance)):
                self.tick += 1
                self._fire_due()
                verdict = self._step_physics()
                if verdict == "dead":
                    self._reset_attempt()
                    break
                if verdict == "done":
                    self._completed = True
                    break
            self._publish()

    def _fire_due(self) -> None:
        """Apply every event whose tick has arrived.

        Mirrors ``fireDueInputs`` (telemetry.cpp:483-510): an event whose tick
        has already gone by still fires, and is counted as late rather than
        dropped -- silently dropping it would make a scheduling bug look like a
        policy that chose not to press.
        """
        keep: list[tuple[int, bool]] = []
        for ev_tick, push in self._events:
            if ev_tick <= self.tick:
                if ev_tick < self.tick:
                    self.late_events += 1
                self._button = push
            else:
                keep.append((ev_tick, push))
        self._events = keep

    def _drain_schedule(self) -> None:
        for tick, push, _button, _player in self.game.scheduled:
            self._events.append((int(tick), bool(push)))
        self.game.scheduled.clear()

    # -- lifecycle --------------------------------------------------------
    def start(self, max_seconds: float = 120.0) -> ToyGame:
        self._thread = threading.Thread(target=self.serve, args=(max_seconds,),
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
