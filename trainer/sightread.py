"""The A-legal search driver: choose inputs, replay, backtrack, count honestly.

Nothing else in this repo chooses an action. The best result on record --
12 jumps, ``maxX=3959.183837891``, ~14.8% of Stereo Madness -- came from a human
hand-picking tick numbers and feeding them to ``GDRL_INJECT_SEQ``. This module is
the attempt to get there with no tick number written down anywhere in it.

WHAT MAKES THIS BENCHMARK A
---------------------------

Three properties, each held by a mechanism rather than by discipline:

1. **The policy cannot see a forbidden field.** Every observation the search
   touches goes through :class:`Sight`, which copies out the ALLOWED fields at
   construction and drops the record. ``objectCountTotal``, ``groups[]``,
   ``groupCount``, ``commands[]``, ``pending[]`` and ``speedSegs[]`` are not
   merely unread -- they are not present, and asking for one by name raises
   :class:`ForbiddenField` rather than returning ``None``. That is ``KnownMask``'s
   posture, applied to the contract (``docs/observation-contract.md``). It moves
   contract rulings 1-2 from tier DOC to tier PYTHON for anything built on this
   module, and discharges part of TODO queue item 2b.

2. **Replay is paid for.** A human replays from tick 0 every attempt, so the
   driver does too: a candidate is a whole plan from the start of the level, run
   in the game, and it costs one real attempt. There is no rewind. What makes
   search possible at all is determinism -- the committed prefix reproduces --
   and determinism is measured (README: ~550 null-input attempts, 1473 with
   input, zero divergent), not assumed here.

3. **Every probe is counted, including the failures.** :class:`AttemptLedger` is
   append-only, has no decrement, and is opened *before* the first action of a
   rollout reaches the wire, so an exception mid-attempt still leaves it counted.
   And it is audited against a second, independent witness: the game's own
   ``header.attempt``. If the game says it played more attempts than the ledger
   recorded, the report says so and the run is not a clean Benchmark A number.
   Undercounting therefore requires defeating the game's own counter, not just
   forgetting to increment ours.

WHAT IS NOT VERIFIED HERE
-------------------------

* **That holding jump auto-repeats on landing.** The action space is intervals
  because of that claim (TODO, "Holding the jump button auto-repeats", from Rex,
  recorded 2026-08-15 and explicitly unmeasured). The design exploits it; the
  test suite *assumes* it, in a puppet that was written to behave that way. A
  green test proves the driver exploits auto-repeat if the game has it. It proves
  nothing about the game. The first live run is the measurement -- if hold-chains
  do not clear anything the interval space collapses to taps and the search space
  is much larger.
* **Anything about Stereo Madness.** No tick, x, or object of that level appears
  in this file.
* **That the driver reaches 3959.18.** Nobody has run it against GD. See the
  bottom of this docstring for what "reached" would even mean, because the
  instrument differs.

THROUGHPUT, MEASURED FIRST (see ``bench_loopback`` and the module report)
------------------------------------------------------------------------

Measured 2026-08-15 on this machine, loopback only (``SyntheticGame`` writer, no
game): **~2,600 round-trips/s with an empty object table and ~1,350/s with 120
objects**, flat in the observation stride. That is the Python decode+respond
cost, ~0.4-0.75 ms per round trip.

Two measurement artifacts had to be removed to get that, and both are worth
recording because either one alone would have produced a confident wrong answer:

* ``SyntheticGame`` rebuilds ``attemptTime`` by accumulating a float32 1/240 in a
  Python loop, which is O(tick) *per publish*. At a stride of 512 that dominated
  everything else. It is a property of the fixture; the mod gets ``m_attemptTime``
  for free.
* The loopback writer is a **thread**, so the GIL switch interval bounds the
  round trip. At the 5 ms default the loop measured 155/s no matter what the
  protocol did -- 1/0.0065 s, which is the switch interval and not the protocol.
  Dropping the interval to 50 us moved it to 2,600/s. Against the mod the writer
  is another process and this does not apply.

So Python is not the bottleneck; the game is. Combining with the recorded
figures (README, tier (iii)): a per-tick env loop ran ~500 round-trips/s
*against the game* and cost 5.5 s per 3054-tick attempt versus 3.4 s with the
loop detached. At the current frontier (death tick 3048) the naive per-tick loop
is therefore ~5.5 s/attempt, i.e. ~650 attempts/hour -- too slow to spend
thousands of attempts on, and most of that cost buys observations of a prefix
whose outcome is already known.

**Hence the hybrid, which needs no mod work.** ``advanceSteps`` in the action
block is an observation stride, and the mod fires scheduled inputs on every
physics step whether or not it published one (``telemetry.cpp:1064``). So the
committed prefix is replayed with the observation stride wound right up (a
handful of round trips for thousands of ticks) and the loop drops to stride 1
only inside the watch window near the frontier. The prefix then costs what the
game costs to simulate it and nothing more:

    per-attempt floor ~= prefix_ticks / (GDRL_ENV_DELTA_TICKS * 60fps)
                         + reset cost (~15 frames with GDRL_FAST_RESET=1)

which at ``GDRL_ENV_DELTA_TICKS=32`` is ~1.8 s for a 3048-tick prefix -- the
game's own replay speed, and the real budget constraint. **UNVERIFIED against
the game: this whole paragraph is arithmetic over recorded numbers, not a
measurement of this driver.**

Suggested launch (the tester's, not run here):

    GDRL_ENV=1 GDRL_AUTOPLAY=1 GDRL_BLOCK_INPUT=1 GDRL_PIN_LEVEL=1 \\
    GDRL_FAST_RESET=1 GDRL_ENV_DELTA_TICKS=32 ./scripts/run_sandbox.sh

then ``python3 trainer/sightread.py --budget 400``.

ON THE ACCEPTANCE NUMBER
------------------------

``3959.183837891`` was produced by the mod's own per-attempt ``maxX`` log line.
This driver reports ``observed max x``, sampled from the observations it was
given -- a *different instrument*. Inside the watch window the stride is 1 so the
two can differ by at most one tick of travel (~1.30 units at 1x); outside it they
can differ by a whole stride. When grading against 3959.183837891, read the
mod's log line for the winning attempt. Comparing two instruments and calling the
difference a result is the mistake this repo keeps a rule about.

Run: ``cd trainer && python3 -m pytest test_sightread.py -q``
"""

from __future__ import annotations

import argparse
import heapq
import itertools
import json
import math
import os
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace

import numpy as np

import env as envmod
import schema_generated as sg
from conditioning import Vehicle, derive_vehicle
from env import (
    Action,
    Channel,
    HandshakeError,
    Observation,
    make_loopback_buffer,
    wait_for_shared,
)
from schema_generated import (
    GDRL_MAX_ACTIONS,
    GDRL_TICK_HZ,
    GdrlActionKind,
    GdrlHeaderFlag,
    GdrlObjectKind,
    GdrlStatus,
)

# ---------------------------------------------------------------------------
# A-legality, structurally
# ---------------------------------------------------------------------------

# Every ruling here is a citation of docs/observation-contract.md, not a fresh
# judgement. The reason each is quoted rather than summarised is that a
# paraphrase drifts and a citation does not.
FORBIDDEN_FIELDS: dict[str, str] = {
    "objectCountTotal":
        "FORBIDDEN for POLICY (contract, 'Validity'): m_objects->count() for the "
        "whole level. A human has no channel to it -- not through the progress "
        "bar, not through the screen. It stays a run gate for the EXPERIMENTER "
        "and is read by FrameGate, which never returns it to the search.",
    "groups":
        "FORBIDDEN for POLICY (contract, 'Objects'): group membership is "
        "invisible on screen. Its only use is predicting which objects a trigger "
        "will move, which is future structure rather than perception.",
    "groupCount":
        "FORBIDDEN for POLICY (contract, 'Objects'): rides with groups[].",
    "commands":
        "FORBIDDEN for POLICY (contract, 'Commands'): the struct carries the "
        "SCRIPT -- duration, actionValue1/2, easing -- i.e. a block's entire "
        "future before it happens, not the motion a human sees.",
    "pending":
        "FORBIDDEN, categorically (contract, 'Pending triggers'): Benchmark A's "
        "own definition names it -- the agent cannot inspect future trigger "
        "states that have not occurred.",
    "speedSegs":
        "NEEDS-MEASUREMENT, therefore forbidden-in-effect (contract, 'Speed "
        "segments'): window-limited collection would be allowed and whole-level "
        "collection would not, and startX+bucket looks identical either way. "
        "Unusable as evidence for a Benchmark A claim until the collection site "
        "is measured.",
}

# Header fields whose contract audience is EXPERIMENTER rather than POLICY. They
# are not forbidden -- reading them is not cheating -- but a policy has no
# business with them, so Sight does not carry them and asking says why.
_EXPERIMENTER_ONLY: dict[str, str] = {
    "sectionXFactor": "audience EXPERIMENTER: a decoder constant for checking "
                      "column arithmetic, not a percept.",
    "sectionYFactor": "audience EXPERIMENTER: measured 0; the vertical filter is "
                      "a direct y compare.",
    "frame": "audience EXPERIMENTER: harness bookkeeping.",
    "stepIndex": "audience EXPERIMENTER: harness bookkeeping.",
    "dtIn": "audience EXPERIMENTER: clock cross-check.",
    "dtUsed": "audience EXPERIMENTER: clock cross-check.",
    "isPaused": "audience EXPERIMENTER: harness lifecycle.",
    "inResetDelay": "audience EXPERIMENTER: harness lifecycle.",
    "status": "audience EXPERIMENTER: frame-refusal reason. See FrameGate.",
    "leaked": "audience EXPERIMENTER: input-path integrity.",
    "blocked": "audience EXPERIMENTER: input-path integrity.",
    "timeouts": "audience EXPERIMENTER: input-path integrity.",
    "inputVerdict": "audience EXPERIMENTER: input-path integrity.",
    "levelID": "audience EXPERIMENTER.",
    "pinnedLevelID": "audience EXPERIMENTER.",
    "objectsTruncated": "audience EXPERIMENTER: capacity honesty (objectsDropped "
                        "is the POLICY-side statement of the same thing).",
}


class ForbiddenField(RuntimeError):
    """Raised when a policy-facing view is asked for a field it may not have.

    Deliberately an exception rather than ``None`` or a zero, for the reason
    ``MaskRefused`` exists: a benchmark violation that returns something
    plausible is indistinguishable from a legitimate observation downstream, and
    the whole failure mode this repo keeps rediscovering is a confident answer
    nobody was entitled to.
    """


# The objects table, minus the forbidden columns. Built by subsetting the wire
# dtype rather than by retyping it, so a schema change cannot silently drop a
# field this file thought it was carrying -- and cannot silently add `groups`
# back either, because the name list is explicit.
_ALLOWED_OBJECT_FIELDS = (
    "uniqueID", "objectID", "kind", "objectType",
    "x", "y", "halfW", "halfH", "rotation", "scaleX", "scaleY",
    "isHazard", "isGroupDisabled", "slopeIsHazard",
)
ALLOWED_OBJECT_DTYPE = np.dtype(
    [(n, sg.GdrlObject_DTYPE.fields[n][0]) for n in _ALLOWED_OBJECT_FIELDS]
)

# A guard rather than a comment: if someone adds a forbidden name to the tuple
# above, this fails at import, not in review.
assert not (set(_ALLOWED_OBJECT_FIELDS) & set(FORBIDDEN_FIELDS)), \
    "an allowed object field collides with a FORBIDDEN one"


@dataclass(frozen=True)
class PlayerSense:
    """The agent's own body. All ALLOWED/POLICY -- proprioception is not the
    thing Benchmark A protects (contract, 'Player state')."""

    x: float
    y: float
    y_velocity: float
    gravity: float
    rotation: float
    vehicle_size: float
    player_speed: float
    vehicle_flags: int
    is_upside_down: bool
    is_sideways: bool
    is_on_ground: bool
    is_dashing: bool
    present: bool


@dataclass(frozen=True)
class Sight:
    """One observation as the policy is allowed to receive it.

    Constructed by copying the ALLOWED fields out of the wire record and then
    **dropping the record**. There is no ``.record``, no ``.validity`` and no
    ``.header`` here on purpose: a view that keeps a reference to the whole
    observation is a documentation-tier constraint wearing a class, because
    ``sight._obs.record['validity']['objectCountTotal']`` still works. This one
    does not have the bytes.

    Window bounds come off the header (``windowMinX`` .. ``windowMaxY``) and
    never from ``GDRL_ENV_WIN_*`` or a constant -- item 1's ruling makes the
    window camera-derived and therefore variable, and a driver that hardcoded
    1400 would keep working while being wrong by 3.9x.
    """

    tick: int
    attempt_time: float
    attempt: int
    flags: int
    time_warp: float
    dt_per_step: float

    player_x: float
    player_y: float
    player_speed: float

    window_min_x: float
    window_max_x: float
    window_min_y: float
    window_max_y: float

    level_length: float
    section_columns: int
    coverage_start_col: int
    object_count: int
    objects_dropped: int
    is_dual_mode: bool

    player: PlayerSense
    player2_present: bool
    coverage: np.ndarray
    _objects: np.ndarray
    unavailable: tuple[str, ...]

    # -- construction -----------------------------------------------------
    @classmethod
    def decode(cls, obs: Observation) -> Sight:
        h = obs.header
        p0 = obs.record["players"][0]
        raw = obs.objects()

        allowed = np.empty(len(raw), dtype=ALLOWED_OBJECT_DTYPE)
        for name in _ALLOWED_OBJECT_FIELDS:
            allowed[name] = raw[name]

        return cls(
            tick=int(h["tick"]),
            attempt_time=float(h["attemptTime"]),
            attempt=int(h["attempt"]),
            flags=int(h["flags"]),
            time_warp=float(h["timeWarp"]),
            dt_per_step=float(h["dtPerStep"]),
            player_x=float(h["playerX"]),
            player_y=float(h["playerY"]),
            player_speed=float(h["playerSpeed"]),
            window_min_x=float(h["windowMinX"]),
            window_max_x=float(h["windowMaxX"]),
            window_min_y=float(h["windowMinY"]),
            window_max_y=float(h["windowMaxY"]),
            level_length=float(h["levelLength"]),
            section_columns=int(h["sectionColumns"]),
            coverage_start_col=int(h["coverageStartCol"]),
            object_count=int(h["objectCount"]),
            objects_dropped=int(h["objectsDropped"]),
            is_dual_mode=bool(h["isDualMode"]),
            player=PlayerSense(
                x=float(p0["x"]), y=float(p0["y"]),
                y_velocity=float(p0["yVelocity"]), gravity=float(p0["gravity"]),
                rotation=float(p0["rotation"]), vehicle_size=float(p0["vehicleSize"]),
                player_speed=float(p0["playerSpeed"]),
                vehicle_flags=int(p0["vehicleFlags"]),
                is_upside_down=bool(p0["isUpsideDown"]),
                is_sideways=bool(p0["isSideways"]),
                is_on_ground=bool(p0["isOnGround"]),
                is_dashing=bool(p0["isDashing"]),
                present=bool(p0["present"]),
            ),
            player2_present=bool(obs.record["players"][1]["present"]),
            coverage=np.array(obs.coverage()),
            _objects=allowed,
            unavailable=tuple(obs.unavailable_tables()),
        )

    # -- the refusal ------------------------------------------------------
    def __getattr__(self, name: str):
        # Only reached when normal lookup failed, so it costs nothing on the
        # hot path and cannot shadow a real field.
        if name in FORBIDDEN_FIELDS:
            raise ForbiddenField(
                f"Sight has no {name!r}, and never will: {FORBIDDEN_FIELDS[name]} "
                "If the EXPERIMENTER needs it, read it from the raw Observation "
                "in the harness, where it cannot reach a policy."
            )
        if name in _EXPERIMENTER_ONLY:
            raise ForbiddenField(
                f"Sight does not carry {name!r}: {_EXPERIMENTER_ONLY[name]} "
                "It is allowed, but not to the policy."
            )
        raise AttributeError(name)

    # -- what the agent may compute --------------------------------------
    def objects(self) -> np.ndarray:
        """Objects inside the sensor window, forbidden columns absent."""
        return self._objects

    def hazards(self) -> np.ndarray:
        o = self._objects
        return o[o["kind"] == int(GdrlObjectKind.HAZARD)]

    def solids(self) -> np.ndarray:
        o = self._objects
        return o[o["kind"] == int(GdrlObjectKind.SOLID)]

    @property
    def horizon_ahead(self) -> float:
        """How far ahead of the player the senses reach, in world units.

        Read off the header every frame. When ``telemetry.cpp``'s window becomes
        camera-derived this number changes and everything built on it follows;
        nothing here needs editing.
        """
        return self.window_max_x - self.player_x

    @property
    def horizon_behind(self) -> float:
        return self.player_x - self.window_min_x

    @property
    def progress(self) -> float | None:
        """``playerX / levelLength`` -- what the on-screen progress bar reads.

        ALLOWED on exactly that ground (contract, header table). ``None`` when
        ``levelLength`` is unpopulated, because a fabricated denominator would
        be a confident percentage of nothing.
        """
        if not (self.level_length > 0.0) or not math.isfinite(self.level_length):
            return None
        return self.player_x / self.level_length

    def has_flag(self, flag: GdrlHeaderFlag) -> bool:
        return bool(self.flags & int(flag))

    def __repr__(self) -> str:
        return (f"Sight(tick={self.tick} attempt={self.attempt} "
                f"x={self.player_x:.3f} y={self.player_y:.3f} "
                f"objs={len(self._objects)} ahead={self.horizon_ahead:.1f})")


class FrameGate:
    """EXPERIMENTER-side frame admission. Its inputs never reach the search.

    Reading ``objectCountTotal`` to refuse a frame where the level never loaded
    is not cheating -- it is deciding whether the *experiment* is valid, which
    the contract places outside the agent's loop. The boundary is kept by this
    class being the only thing that touches the raw ``Observation``'s validity
    block, and by it returning reasons (strings, for the operator) rather than
    numbers the search could use.
    """

    @staticmethod
    def reasons(obs: Observation) -> list[str]:
        return obs.problems()

    @staticmethod
    def is_gameplay_sample(obs: Observation) -> bool:
        """Is this frame a usable sample of a live attempt?

        Not the same question as ``problems() == []``: a frame during the reset
        delay is a legitimate part of the protocol and merely uninformative,
        while a leaked keypress invalidates the attempt. Both are refused here;
        the caller separates them via :meth:`reasons`.
        """
        return (obs.status is GdrlStatus.OK
                and obs.has_flag(GdrlHeaderFlag.GAMEPLAY_STEP)
                and bool(obs.record["players"][0]["present"]))

    @staticmethod
    def fatal_reasons(obs: Observation) -> list[str]:
        """Reasons the whole RUN is void, not just this frame.

        A leaked input means a push reached the player by an unmapped route, so
        the trajectory is not the one the plan describes. A timeout means a step
        ran with no action, which is indistinguishable from a policy that chose
        not to act. An empty level means nothing loaded. None of these are
        recoverable by trying again with the same setup, so the driver stops
        rather than accumulating attempts that are not evidence.
        """
        v = obs.validity
        out: list[str] = []
        if int(v["leaked"]) > 0:
            out.append(f"input leaked={int(v['leaked'])}: the attempt is not the "
                       "plan's trajectory")
        if int(v["timeouts"]) > 0:
            out.append(f"action waits expired: timeouts={int(v['timeouts'])}")
        if int(v["objectCountTotal"]) < 10:
            out.append("level string did not load (run gate)")
        if obs.input_verdict is sg.GdrlInputVerdict.UNGUARDED:
            out.append("input UNGUARDED: set GDRL_BLOCK_INPUT=1, or a stray "
                       "keypress is indistinguishable from a policy action")
        return out


# ---------------------------------------------------------------------------
# The action space: intervals, not taps
# ---------------------------------------------------------------------------

@dataclass(frozen=True, order=True)
class Interval:
    """One held input: press at ``start_tick``, release ``hold_ticks`` later.

    Why intervals and not taps: holding jump makes the cube jump again each time
    it lands, so a held input is a *chain* of jumps with two tolerant parameters
    instead of a list of exact ones. Recorded 2026-08-15 from Rex and
    **UNMEASURED** (TODO, 'Holding the jump button auto-repeats'). The transport
    supports it either way -- ``GdrlActionKind.HOLD`` expands into push/release
    at ``telemetry.cpp:456`` -- so if the claim is false this degrades to a tap
    space with a redundant parameter rather than breaking.
    """

    start_tick: int
    hold_ticks: int

    def __post_init__(self) -> None:
        if self.start_tick < 1:
            raise ValueError(
                f"start_tick={self.start_tick}: an input must be scheduled for a "
                "tick that has not happened, and tick 0 is already gone by the "
                "time the first observation is answered"
            )
        if self.hold_ticks < 1:
            raise ValueError(
                f"hold_ticks={self.hold_ticks}: a zero-length hold is a press "
                "with no release, which latches the button down"
            )

    @property
    def end_tick(self) -> int:
        """The tick the release is scheduled for (the button is down before it)."""
        return self.start_tick + self.hold_ticks

    def __repr__(self) -> str:
        return f"({self.start_tick},{self.hold_ticks})"


@dataclass(frozen=True)
class Plan:
    """A whole attempt's input, from tick 0. Immutable and hashable.

    Hashable because the search deduplicates on it: re-running a plan whose
    outcome is already known would spend a real attempt to learn nothing, and
    determinism means the answer cannot have changed.

    Intervals must be sorted and disjoint with at least one tick between them.
    Overlap is not merely untidy -- two overlapping HOLDs expand to
    push,push,release,release, and the *first* release ends the chain, so the
    plan the mod executes is not the plan that was written.
    """

    intervals: tuple[Interval, ...] = ()

    def __post_init__(self) -> None:
        prev: Interval | None = None
        for iv in self.intervals:
            if prev is not None and iv.start_tick <= prev.end_tick:
                raise ValueError(
                    f"intervals {prev} and {iv} overlap or touch: the release of "
                    f"the first lands at tick {prev.end_tick}, at or after the "
                    "push of the second, so the mod would execute a different "
                    "plan than this one describes"
                )
            prev = iv

    # -- construction -----------------------------------------------------
    def appended(self, iv: Interval) -> Plan:
        return Plan(tuple(sorted(self.intervals + (iv,))))

    def with_last_replaced(self, iv: Interval) -> Plan:
        return Plan(tuple(sorted(self.intervals[:-1] + (iv,))))

    # -- use --------------------------------------------------------------
    @property
    def last(self) -> Interval | None:
        return self.intervals[-1] if self.intervals else None

    @property
    def end_tick(self) -> int:
        return self.intervals[-1].end_tick if self.intervals else 0

    def actions(self) -> list[Action]:
        return [Action(target_tick=iv.start_tick, kind=GdrlActionKind.HOLD,
                       hold_ticks=iv.hold_ticks)
                for iv in self.intervals]

    def held_ticks(self) -> int:
        return sum(iv.hold_ticks for iv in self.intervals)

    def __len__(self) -> int:
        return len(self.intervals)

    def __repr__(self) -> str:
        return "[" + " ".join(repr(iv) for iv in self.intervals) + "]"


# ---------------------------------------------------------------------------
# Counting attempts so that undercounting is structurally hard
# ---------------------------------------------------------------------------

class LedgerViolation(RuntimeError):
    """The ledger and the game disagree about how many attempts were played."""


@dataclass
class AttemptRecord:
    """One real attempt. Opened before the first action reaches the wire."""

    index: int                      # ours, 0-based, dense
    purpose: str                    # "calibrate" | "probe" | "sync" | "verify"
    plan: Plan
    game_attempt_at_open: int
    # Filled in on close. `None` means the attempt was opened and never closed,
    # which is itself reportable: it is an attempt that was spent.
    game_attempt_at_close: int | None = None
    max_x: float | None = None
    end_tick: int | None = None
    note: str = ""
    wall_seconds: float = 0.0

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "purpose": self.purpose,
            "plan": [[iv.start_tick, iv.hold_ticks] for iv in self.plan.intervals],
            "game_attempt_at_open": self.game_attempt_at_open,
            "game_attempt_at_close": self.game_attempt_at_close,
            "max_x": self.max_x,
            "end_tick": self.end_tick,
            "note": self.note,
            "wall_seconds": round(self.wall_seconds, 4),
        }


class AttemptLedger:
    """Append-only record of every attempt spent. No decrement exists.

    The count that matters is not this list's length -- it is this list's length
    **agreed with the game's own attempt counter**. ``header.attempt`` is
    incremented by the mod's ``resetLevel`` hook, i.e. by the game, not by us, so
    it is a genuinely independent witness. If a future edit adds a rollout path
    that forgets to open a record, the audit fails and the report says the number
    is not a Benchmark A number. That is the property asked for: undercounting
    has to defeat two counters, one of which this file does not control.
    """

    def __init__(self, *, on_close: Callable[[AttemptRecord], None] | None = None
                ) -> None:
        self._records: list[AttemptRecord] = []
        self._first_game_attempt: int | None = None
        self._last_game_attempt: int | None = None
        self._open: AttemptRecord | None = None
        #: An attempt the game has STARTED (its first frame was published) that
        #: nothing has used yet. It is not spent, so it must not be counted --
        #: but it is visible in header.attempt, so without tracking it the audit
        #: reports a phantom uncounted attempt on every run that stops on budget.
        self._unused_attempt: int | None = None
        #: Called with every record the instant it closes, i.e. before the
        #: caller that spent the attempt does anything else. TODO ("env.py
        #: clients SIGSEGV when the game dies while they hold the mmap"): an
        #: end-of-run dump is a dump that never happens if the process does not
        #: reach the end, and this repo has already lost two full runs to that.
        #: A callback here rather than a hardcoded file write is what lets the
        #: loopback tests exercise this without touching a filesystem.
        self._on_close = on_close

    # -- witnesses --------------------------------------------------------
    def observe_game_attempt(self, attempt: int) -> None:
        """Feed the game's own counter in, from any frame."""
        if self._first_game_attempt is None:
            self._first_game_attempt = attempt
        if self._last_game_attempt is None or attempt > self._last_game_attempt:
            self._last_game_attempt = attempt

    @property
    def count(self) -> int:
        return len(self._records)

    @property
    def game_attempts(self) -> int:
        """Attempts the GAME says were played across the session.

        ``last - first + 1``, and the ``+1`` is not a fudge: ``header.attempt``
        is an attempt *id*, incremented by the mod's ``resetLevel`` hook. Playing
        ids 7, 8, 9 and 10 is four attempts and a difference of three, and the
        last one has not been reset yet -- the driver stops while still inside
        it. Using the bare difference made the audit report an unexplained
        off-by-one on every clean run, which is exactly how a real discrepancy
        would have been trained out of anyone reading the report.
        """
        if self._first_game_attempt is None or self._last_game_attempt is None:
            return 0
        played = self._last_game_attempt - self._first_game_attempt + 1
        if (self._unused_attempt is not None
                and self._unused_attempt == self._last_game_attempt):
            played -= 1
        return played

    def mark_unused(self, attempt: int) -> None:
        """The game has begun attempt ``attempt`` and nobody has used it."""
        self._unused_attempt = attempt
        self.observe_game_attempt(attempt)

    @property
    def records(self) -> tuple[AttemptRecord, ...]:
        return tuple(self._records)

    # -- the only way to spend one ---------------------------------------
    def open(self, purpose: str, plan: Plan, game_attempt: int) -> AttemptRecord:
        if self._open is not None:
            # An unclosed record is not lost -- it is already counted. Refusing
            # to open a second one keeps the pairing 1:1 so the audit means
            # something.
            raise LedgerViolation(
                f"attempt {self._open.index} was opened and never closed; a "
                "second cannot start. The first is already counted."
            )
        if self._unused_attempt == game_attempt:
            self._unused_attempt = None      # it is being used now
        rec = AttemptRecord(index=len(self._records), purpose=purpose, plan=plan,
                            game_attempt_at_open=game_attempt)
        self._records.append(rec)
        self._open = rec
        self.observe_game_attempt(game_attempt)
        return rec

    def close(self, rec: AttemptRecord, *, game_attempt: int, max_x: float | None,
              end_tick: int | None, note: str = "", wall_seconds: float = 0.0) -> None:
        rec.game_attempt_at_close = game_attempt
        rec.max_x = max_x
        rec.end_tick = end_tick
        rec.note = note
        rec.wall_seconds = wall_seconds
        self.observe_game_attempt(game_attempt)
        self._open = None
        # Ledger bookkeeping above is done first, so a raising callback (a full
        # disk, a bad path) leaves the ledger itself consistent -- the open/close
        # pairing that the audit depends on -- even though the persistence it
        # was asked to do failed. The callback is intentionally NOT swallowed: a
        # driver that silently failed to persist a result would be exactly the
        # "confident answer nobody was entitled to" failure mode this repo keeps
        # a rule about, just moved from the search to the I/O.
        if self._on_close is not None:
            self._on_close(rec)

    # -- the audit --------------------------------------------------------
    def audit(self) -> list[str]:
        """Every way the two counters disagree. Empty means the number is clean."""
        out: list[str] = []
        if self._open is not None:
            out.append(f"attempt {self._open.index} ({self._open.purpose}) was "
                       "opened and never closed")
        played = self.game_attempts
        if played > self.count:
            out.append(
                f"the GAME played {played} attempts but the ledger recorded "
                f"{self.count}. {played - self.count} attempt(s) were spent "
                "without being counted -- the reported number is a Benchmark B "
                "number wearing an A label."
            )
        if played < self.count:
            # Not a violation of the honest-counting property (we over-report),
            # but it means something is wrong with the model of the game's
            # counter and the number should not be trusted either way.
            out.append(
                f"the ledger recorded {self.count} attempts but the game's "
                f"counter advanced by {played}. Over-counting is the safe "
                "direction, but the disagreement is unexplained."
            )
        return out


# ---------------------------------------------------------------------------
# Running one attempt
# ---------------------------------------------------------------------------

class RunAborted(RuntimeError):
    """The run cannot continue: the environment stopped being evidence."""


class GameGone(RunAborted):
    """The game process is no longer there to answer.

    A subclass of :class:`RunAborted`, not a sibling: ``Sightreader.run()``'s
    existing ``except RunAborted`` already stops the search cleanly and reports
    why, so this needed no new handling at that layer -- only a way for
    :class:`Runner` to *tell the difference* between "the level-complete screen
    is showing and stopped publishing" (an ordinary timeout, handled per-plan as
    ``unrunnable``) and "the process is dead" (nothing further can be learned,
    stop the whole run).

    What this does NOT do, and cannot: catch an actual SIGSEGV/SIGBUS from
    ``numpy`` touching a page the OS has pulled out from under the mapping.
    That is a hardware trap, not a Python exception, and no ``try/except`` in
    this file or any other pure-Python code can recover from it -- the process
    dies before a handler could run safely. The mitigation for *that* failure
    mode lives elsewhere: incremental, flushed JSONL writes (see
    ``AttemptLedger(on_close=...)`` and ``main()``) mean a hard crash loses at
    most the attempt in flight, not the run. This class only converts the
    common, non-fatal case -- the game exited and polling now times out -- from
    an uncaught crash into a clean, reported stop.
    """


@dataclass
class Outcome:
    """What one real attempt bought.

    ``max_x`` is the largest ``playerX`` in the frames the driver was *given*.
    That is not the mod's ``maxX``: outside the watch window the stride is large
    and the true maximum can sit between two samples. ``max_x_stride`` records
    the stride that was in force when the maximum was seen so the error bound is
    on the record rather than in someone's head.
    """

    attempt_index: int
    game_attempt: int
    plan: Plan
    max_x: float
    max_x_tick: int
    max_x_stride: int
    end_tick: int
    first_tick: int
    sights: tuple[Sight, ...]         # the tail of the watch window only
    reached_end_of_level: bool
    unrunnable: str | None            # why the plan could not be placed, if so
    wall_seconds: float

    @property
    def ok(self) -> bool:
        return self.unrunnable is None


class Runner:
    """Drives the channel. The only thing in this module that spends attempts.

    Invariant: exactly one observation is outstanding (polled, not yet answered)
    between calls. The protocol is a ping-pong -- the mod will not publish n+1
    before n is answered -- so anything that breaks that invariant deadlocks, and
    keeping it in one class is why the search never has to think about it.
    """

    def __init__(self, channel: Channel, ledger: AttemptLedger, *,
                 max_stride: int = 2048, timeout: float = 20.0,
                 keep_sights: int = 400, verbose: bool = False):
        self.chan = channel
        self.ledger = ledger
        self.max_stride = max_stride
        self.timeout = timeout
        self.keep_sights = keep_sights
        self.verbose = verbose
        self._pending: Observation | None = None
        self._cur_attempt: int | None = None
        #: True when the outstanding observation is the first frame of an
        #: attempt nothing has used yet. Without it every rollout after the
        #: first would burn a second, pointless attempt running out an attempt
        #: that had only just started -- and, worse, would REPORT it, so the
        #: attempt count would silently double. The counting is honest either
        #: way; this makes it accurate as well.
        self._fresh = False
        self.fatal: list[str] = []

    # -- protocol plumbing ------------------------------------------------
    def _poll(self) -> Observation:
        # Cheap and proactive: `os.kill(pid, 0)` (behind mod_process_alive())
        # costs one syscall and catches the common case -- the game already
        # exited -- before spending a whole `timeout` finding out the slow way.
        # It cannot close the race where the process dies mid-poll; the
        # TimeoutError branch below is the fallback for that.
        if not self.chan.mod_process_alive():
            raise GameGone(
                f"mod process (pid {self.chan.mod_pid}) is not running; "
                "refusing to poll a segment nothing is writing to"
            )
        try:
            obs = self.chan.poll(timeout=self.timeout)
        except TimeoutError as exc:
            # Distinguish "the process is dead" from "it is alive but not
            # answering" (e.g. the level-complete screen, or a real stall).
            # Only the former stops the whole run; see GameGone's docstring.
            if not self.chan.mod_process_alive():
                raise GameGone(
                    f"the game process (pid {self.chan.mod_pid}) is gone: {exc}"
                ) from exc
            raise
        self.ledger.observe_game_attempt(obs.attempt)
        fatal = FrameGate.fatal_reasons(obs)
        if fatal:
            self.fatal.extend(fatal)
            raise RunAborted("; ".join(fatal))
        self._pending = obs
        return obs

    def _answer(self, actions: list[Action], stride: int) -> Observation:
        if self._pending is None:
            raise RuntimeError("nothing to answer: _poll first")
        stride = max(1, min(int(stride), self.max_stride))
        self.chan.respond(actions, advance_steps=stride)
        self._pending = None
        return self._poll()

    def prime(self) -> Observation:
        """First contact. Returns the first observation, unanswered."""
        obs = self._poll()
        self._cur_attempt = obs.attempt
        return obs

    # -- attempt boundaries -----------------------------------------------
    def _await_fresh_attempt(self, purpose_hint: str) -> Observation:
        """Run whatever attempt is in flight to its end and return the next one's
        first frame.

        Letting the current attempt die costs a real attempt, and it is recorded
        as one (purpose ``sync``). That is the point: a driver that quietly
        discarded the run-up would be reporting fewer attempts than it played.
        """
        if self._pending is None:
            self.prime()
        obs = self._pending
        assert obs is not None

        # Already at the start of an attempt nothing has used? Then no sync
        # attempt is needed.
        if self._fresh or (self._cur_attempt is not None
                           and obs.attempt != self._cur_attempt):
            self._cur_attempt = obs.attempt
            self._fresh = False
            return obs

        rec = self.ledger.open("sync", Plan(()), obs.attempt)
        t0 = time.perf_counter()
        start_attempt = obs.attempt
        max_x = float(obs.header["playerX"])
        try:
            while True:
                obs = self._answer([], self.max_stride)
                if FrameGate.is_gameplay_sample(obs):
                    max_x = max(max_x, float(obs.header["playerX"]))
                if obs.attempt != start_attempt:
                    break
        finally:
            self.ledger.close(rec, game_attempt=obs.attempt, max_x=max_x,
                              end_tick=int(obs.tick),
                              note=f"ran out the in-flight attempt before {purpose_hint}",
                              wall_seconds=time.perf_counter() - t0)
        self._cur_attempt = obs.attempt
        self._fresh = False
        return obs

    # -- the rollout ------------------------------------------------------
    def rollout(self, plan: Plan, *, purpose: str = "probe",
                watch_from_tick: int = 0) -> Outcome:
        """Play ``plan`` from the start of a fresh attempt. Costs one attempt.

        ``watch_from_tick`` is where the observation stride drops to 1. Before
        it the loop winds the stride up as far as the next unscheduled interval
        allows, which is what keeps the cost of replaying a long committed prefix
        at the game's own simulation speed instead of at Python's round-trip
        rate. The inputs are unaffected: the mod fires scheduled inputs on every
        physics step whether or not it published one.
        """
        obs = self._await_fresh_attempt(purpose)
        first_tick = int(obs.tick)
        game_attempt = obs.attempt
        rec = self.ledger.open(purpose, plan, game_attempt)
        t0 = time.perf_counter()

        remaining = list(plan.intervals)
        unrunnable: str | None = None
        # An interval whose start has already gone by cannot be placed: the mod
        # would fire it LATE and log a protocol error, and the trajectory would
        # not be the plan's. Detected here rather than absorbed.
        too_late = [iv for iv in remaining if iv.start_tick <= first_tick]
        if too_late:
            unrunnable = (f"intervals {too_late} start at or before the first "
                          f"observable tick {first_tick}")

        sights: list[Sight] = []
        max_x = float(obs.header["playerX"])
        max_x_tick = first_tick
        max_x_stride = 1
        end_tick = first_tick
        stride = 1
        reached_end = False

        try:
            while True:
                batch: list[Action] = []
                if not unrunnable:
                    # Schedule everything that fits and is still in the future.
                    # GDRL_MAX_ACTIONS caps the block; the mod's schedule table
                    # holds kMaxScheduled=64 events and a HOLD is two, so a batch
                    # of 8 can never overrun it.
                    while remaining and len(batch) < GDRL_MAX_ACTIONS:
                        iv = remaining[0]
                        if iv.start_tick <= int(obs.tick):
                            unrunnable = (f"interval {iv} became unschedulable at "
                                          f"tick {int(obs.tick)}")
                            break
                        batch.append(Action(target_tick=iv.start_tick,
                                            kind=GdrlActionKind.HOLD,
                                            hold_ticks=iv.hold_ticks))
                        remaining.pop(0)

                cur = int(obs.tick)
                if cur >= watch_from_tick:
                    stride = 1
                else:
                    # Stop short of the watch window and of the next interval we
                    # still have to schedule -- with a two-tick margin, because
                    # an input has to be scheduled strictly before its tick.
                    targets = [watch_from_tick]
                    if remaining:
                        targets.append(remaining[0].start_tick - 2)
                    stride = max(1, min(targets) - cur)

                obs = self._answer(batch, stride)

                if obs.attempt != game_attempt or int(obs.tick) < end_tick:
                    # The attempt ended, and this frame is the first of the next
                    # one. Handing it to the next rollout is what keeps replay
                    # costing exactly one attempt per plan.
                    self._fresh = True
                    self.ledger.mark_unused(obs.attempt)
                    break
                if FrameGate.is_gameplay_sample(obs):
                    x = float(obs.header["playerX"])
                    if x > max_x:
                        max_x, max_x_tick, max_x_stride = x, int(obs.tick), stride
                    end_tick = int(obs.tick)
                    if stride == 1:
                        sights.append(Sight.decode(obs))
                        if len(sights) > self.keep_sights:
                            del sights[0]
                    lvl = float(obs.header["levelLength"])
                    if lvl > 0.0 and x >= lvl:
                        reached_end = True
                        break
        except TimeoutError as exc:
            # The game stopped publishing. At high x that is most likely the
            # level-complete screen; it is NOT asserted to be, because the two
            # look identical from here.
            unrunnable = (f"the game stopped publishing at tick {end_tick}, "
                          f"x={max_x:.3f} ({exc}). If x is near levelLength this "
                          "may be a completion rather than a fault -- from this "
                          "side the two are indistinguishable.")
        finally:
            wall = time.perf_counter() - t0
            self.ledger.close(rec, game_attempt=(self._pending.attempt
                                                 if self._pending is not None
                                                 else game_attempt),
                              max_x=max_x, end_tick=end_tick,
                              note=unrunnable or "", wall_seconds=wall)

        self._cur_attempt = self._pending.attempt if self._pending else game_attempt
        return Outcome(
            attempt_index=rec.index,
            game_attempt=game_attempt,
            plan=plan,
            max_x=max_x,
            max_x_tick=max_x_tick,
            max_x_stride=max_x_stride,
            end_tick=end_tick,
            first_tick=first_tick,
            sights=tuple(sights),
            reached_end_of_level=reached_end,
            unrunnable=unrunnable,
            wall_seconds=wall,
        )


# ---------------------------------------------------------------------------
# What the driver measures for itself, instead of being told
# ---------------------------------------------------------------------------

# Fallbacks, used only when the corresponding thing has not been observed yet.
# They are hyperparameters of the SEARCH, not facts about any level: none of them
# encodes a position, an obstacle or a tick number in Stereo Madness. Every one
# is UNVERIFIED as a description of GD and is replaced by a measurement as soon
# as one exists.
FALLBACK_AIRTIME_TICKS = 60         # UNVERIFIED: a plausible cube hop, ~0.25 s
FALLBACK_HORIZON_TICKS = 240        # UNVERIFIED: ~1 s of lookahead


@dataclass
class Rhythm:
    """Level-agnostic quantities the driver measures from its own senses.

    Everything here is derived from observations rather than from a constant, so
    the search stays correct at other speeds and other window widths. In
    particular ``units_per_tick`` is *not* the documented 1.298250437 -- that
    number is used only as a cross-check, because at 2x/3x/4x it is wrong and a
    search built on it would silently mis-convert every distance.
    """

    units_per_tick: float | None = None
    airtime_ticks: int | None = None
    horizon_ticks: int | None = None
    samples: int = 0
    jumps_seen: int = 0

    #: The documented 1x figure, for a sanity comparison only (README).
    DOCUMENTED_UPT_1X = 1.298250437

    def observe(self, sights: tuple[Sight, ...] | list[Sight]) -> None:
        if len(sights) < 2:
            return
        dxs = []
        for a, b in zip(sights, sights[1:]):
            dt = b.tick - a.tick
            if dt == 1 and b.attempt == a.attempt:
                dxs.append(b.player_x - a.player_x)
        if dxs:
            upt = statistics.median(dxs)
            if upt > 0:
                self.units_per_tick = upt
                self.samples += len(dxs)

        # Airtime: ground -> air -> ground, in ticks. Proprioception, ALLOWED.
        left: int | None = None
        spans: list[int] = []
        for s in sights:
            if s.player.is_on_ground:
                if left is not None and s.tick > left:
                    spans.append(s.tick - left)
                    left = None
            else:
                if left is None:
                    left = s.tick
        if spans:
            # Median rather than mean: a hop interrupted by the attempt ending
            # is a short outlier, and there is no reason to let it pull the unit
            # the whole search is scaled by.
            self.airtime_ticks = max(1, int(round(statistics.median(spans))))
            self.jumps_seen += len(spans)

        if self.units_per_tick:
            ahead = statistics.median([s.horizon_ahead for s in sights])
            if ahead > 0:
                self.horizon_ticks = max(1, int(ahead / self.units_per_tick))

    # -- what the generator asks for --------------------------------------
    @property
    def unit(self) -> int:
        """The tick quantum the search scales by: one hop, if one was seen."""
        return self.airtime_ticks or FALLBACK_AIRTIME_TICKS

    @property
    def horizon(self) -> int:
        return self.horizon_ticks or FALLBACK_HORIZON_TICKS

    def unmeasured(self) -> list[str]:
        out = []
        if self.units_per_tick is None:
            out.append("units_per_tick")
        if self.airtime_ticks is None:
            out.append(f"airtime_ticks (using fallback {FALLBACK_AIRTIME_TICKS}, "
                       "UNVERIFIED)")
        if self.horizon_ticks is None:
            out.append(f"horizon_ticks (using fallback {FALLBACK_HORIZON_TICKS}, "
                       "UNVERIFIED)")
        return out

    def upt_disagreement(self) -> float | None:
        """|measured - documented| at 1x, for the report. Not a gate.

        A large value is not necessarily wrong: at 2x it *should* disagree. It is
        reported so a wrong measurement is visible rather than absorbed.
        """
        if self.units_per_tick is None:
            return None
        return abs(self.units_per_tick - self.DOCUMENTED_UPT_1X)


# ---------------------------------------------------------------------------
# Episodic memory
# ---------------------------------------------------------------------------

@dataclass
class Memory:
    """What the last attempts said. Under A this is most of what is known.

    The sensor reaches ~277 ticks ahead (item 1, measured), so the driver cannot
    see what kills it until it is nearly on top of it. Everything useful about
    the level therefore arrives as the history of its own deaths.
    """

    outcomes: dict[Plan, Outcome] = field(default_factory=dict)
    deaths: list[tuple[float, int]] = field(default_factory=list)   # (x, tick)
    duplicates_skipped: int = 0

    def remember(self, outcome: Outcome) -> None:
        self.outcomes[outcome.plan] = outcome
        if outcome.ok:
            self.deaths.append((outcome.max_x, outcome.end_tick))

    def seen(self, plan: Plan) -> bool:
        return plan in self.outcomes

    def death_wall(self, tolerance: float) -> tuple[float, int] | None:
        """The x the search keeps dying at, if there is one.

        Reported rather than acted on: a driver that dies at the same x twenty
        times is stuck, and saying so in the report is more useful than any
        automatic response this file could justify.
        """
        if not self.deaths:
            return None
        best = max(x for x, _ in self.deaths)
        near = [(x, t) for x, t in self.deaths if abs(x - best) <= tolerance]
        if len(near) < 3:
            return None
        return best, near[-1][1]


# ---------------------------------------------------------------------------
# The search
# ---------------------------------------------------------------------------

class CandidateSource:
    """Proposals for one node, widening, ordered by a stated prior.

    Every number this yields is derived from something observed: the tick the
    attempt ended at, the measured hop length, and the measured sensor horizon.
    No level constant enters. The *ordering* is a heuristic prior and is
    UNVERIFIED -- it is a bet about where a jump has to start relative to where
    the death happened, not a measurement of one.
    """

    def __init__(self, plan: Plan, outcome: "Outcome", rhythm: Rhythm, *,
                 max_span_horizons: float = 2.0):
        self.plan = plan
        self.outcome = outcome
        self.death_tick = outcome.end_tick
        self.first_tick = outcome.first_tick
        self.rhythm = rhythm
        self.max_span_horizons = max_span_horizons
        self._tried: set[Interval] = set()
        #: How many times ``widen()`` has been called. 0 means "never widened".
        #: Feeds both the start-tick span (via ``max_span_horizons``, already a
        #: multiplicative parameter) and the hold-length menu (via
        #: ``_hold_mults``), because a stall that survives one widening should
        #: get MORE room next time, not the same room again.
        self.widen_level = 0
        self._hold_mults: list[float] = [8, 4, 2, 1, 0.5, 0.125]
        self._gen = self._generate()

    def widen(self) -> None:
        """Broaden the spread after repeated failure. Diversity, not depth.

        Called on a node that is being forced back into contention by
        escalating backtrack (:class:`Sightreader`). Doubling
        ``max_span_horizons`` widens the start-tick sweep; adding a coarser and
        a finer hold multiplier widens the hold-length menu. Both grow with
        every call, so a site that stalls again after being widened once gets
        widened further rather than being offered the same neighbourhood again.
        Re-running ``_generate()`` re-yields already-tried intervals too, but
        ``next()`` filters those through ``_tried`` at zero cost -- nothing here
        spends an attempt by itself.
        """
        self.widen_level += 1
        self.max_span_horizons *= 2.0
        lo = self._hold_mults[-1] / 2.0
        hi = self._hold_mults[0] * 2.0
        self._hold_mults = [hi] + self._hold_mults + [lo]
        self._gen = self._generate()

    # -- what the senses said about the place it died ---------------------
    def obstacle_span_ticks(self) -> int | None:
        """How many ticks of travel the hazards it died among occupy.

        Read off the last frame the driver was given before the attempt ended:
        take the hazards at or ahead of the player, keep the run of them
        separated by less than one hop's travel, and measure from the player to
        the far edge. Converted to ticks with the MEASURED units-per-tick.

        This is the one place the sensor feeds the search directly, and it is
        the interval action space's whole point: a cluster two hazards wide
        cannot be crossed by one hop, but it can be crossed by a hold that lasts
        as long as the cluster does. ``None`` when nothing was in view -- which
        is a real answer (it died to something it never saw, or to a fall) and
        must not be confused with a span of zero.
        """
        if not self.outcome.sights or self.rhythm.units_per_tick is None:
            return None
        last = self.outcome.sights[-1]
        hz = last.hazards()
        if len(hz) == 0:
            return None
        xs = np.sort(np.asarray(hz["x"], dtype=float)
                     + np.asarray(hz["halfW"], dtype=float))
        ahead = xs[xs >= last.player_x]
        if len(ahead) == 0:
            return None
        gap = self.rhythm.unit * self.rhythm.units_per_tick
        far = ahead[0]
        for edge in ahead[1:]:
            if edge - far > gap:
                break
            far = edge
        return max(1, int((far - last.player_x) / self.rhythm.units_per_tick))

    # -- the space --------------------------------------------------------
    def _earliest_start(self) -> int:
        last = self.plan.last
        floor_tick = self.first_tick + 2
        if last is not None:
            floor_tick = max(floor_tick, last.end_tick + 1)
        return floor_tick

    def _holds(self) -> list[int]:
        """Hold lengths, longest first.

        Longest first is the hypothesis under test: if holding really does
        auto-repeat, the forgiving long hold clears whole stretches and should be
        found in one probe rather than in a sweep of taps. If it does not, these
        cost four attempts per node and the tap-sized ones take over.
        """
        u = self.rhythm.unit
        return sorted({max(1, int(m * u)) for m in self._hold_mults}, reverse=True)

    def _start_passes(self) -> list[list[int]]:
        """Candidate start ticks, ordered by a prior about where jumps go.

        The prior: to be airborne over the thing that killed you at tick D, the
        jump has to begin roughly half a hop earlier. So the sweep is centred on
        ``D - unit/2`` and walks outward, coarse first, refining on later passes.
        Coarse-first matters because the outcome is piecewise constant in jump
        tick with plateaus tens of ticks wide (README), so a coarse pass has a
        real chance of landing inside the right plateau instead of walking to it
        one tick at a time.
        """
        u = self.rhythm.unit
        lo = self._earliest_start()
        # A press scheduled AT the tick the incumbent already died on cannot
        # affect anything before that death -- the run is over by then. Capped
        # at death_tick - 1, not death_tick, so ``t <= hi`` can never propose a
        # no-op. Measured live (orchestrator, 2026-08-18): 49/237 post-frontier
        # probes in the un-guarded generator had start_tick >= the incumbent's
        # own death tick, pure waste that this closes off structurally rather
        # than leaving to chance.
        hi = max(lo, self.death_tick - 1)
        span = int(self.max_span_horizons * self.rhythm.horizon)
        lo = max(lo, hi - span)
        centre = max(lo, min(hi, self.death_tick - u // 2))

        passes: list[list[int]] = []
        seen: set[int] = set()
        step = max(1, u // 4)
        while True:
            this_pass: list[int] = []
            offsets = [0]
            k = step
            while centre - k >= lo or centre + k <= hi:
                offsets.extend((-k, k))
                k += step
            for off in offsets:
                t = centre + off
                if lo <= t <= hi and t not in seen:
                    seen.add(t)
                    this_pass.append(t)
            if this_pass:
                passes.append(this_pass)
            if step == 1:
                break
            step = max(1, step // 2)
        return passes

    def _generate(self):
        # If the committed plan's last hold is still down when the attempt ends,
        # there is no room to APPEND anything: every interval that would fit
        # starts after the run was already over. Yielding nothing here is not a
        # dead end, it is the backtrack -- the node goes exhausted, the parent
        # resurfaces, and the parent's generator proposes a DIFFERENT last
        # interval, which is the only change that can help.
        #
        # Without this the search grew plans into the far future forever
        # ([(2,330) (333,384) (718,384) ...]), every probe scoring identically
        # because none of its inputs happened before the death, and the priority
        # kept picking that node because its subtree best was the frontier. It
        # looked like a search and was a loop.
        # A start at or after death_tick can never affect the outcome (see
        # _start_passes' hi cap for the same rule applied to the sweep below),
        # so "no room to append anything before the death" is now ">=" rather
        # than "greater than" -- lo == death_tick is exactly the case where the
        # only tick left is the one nothing can be scheduled AT.
        if self._earliest_start() >= self.death_tick:
            return

        # 0. The cheapest probe of the hold hypothesis: hold from as early as the
        #    plan allows until well past where the last attempt ended. If large
        #    stretches really are cleared by simply holding, this finds them for
        #    one attempt instead of a sweep.
        lo = self._earliest_start()
        if lo < self.death_tick:
            yield Interval(lo, max(1, self.death_tick - lo + 4 * self.rhythm.unit))

        # 1. The one candidate the SENSOR shapes: a hold as long as the hazard
        #    cluster it died in, started half a hop before it got there. If a
        #    held input really is a chain of jumps, this is the shape that
        #    crosses a cluster no single hop can.
        span = self.obstacle_span_ticks()
        if span is not None:
            u = self.rhythm.unit
            for lead in (u // 2, u, u // 4):
                start = self.death_tick - lead
                if lo <= start < self.death_tick:
                    yield Interval(start, span + u)

        # 2. The sweep: coarse pass first, and every hold shape at each start
        #    before moving on. Holds were the OUTER loop here at first, and it
        #    spent an entire budget walking one hold length across the range
        #    before trying a second -- the start tick matters far more than the
        #    hold length does, so a pass has to vary both or it is not a coarse
        #    pass at all.
        for starts in self._start_passes():
            for start in starts:
                for hold in self._holds():
                    yield Interval(start, hold)

    def next(self) -> Interval | None:
        for iv in self._gen:
            if iv in self._tried:
                continue
            self._tried.add(iv)
            return iv
        return None


class ShipCandidateSource:
    """Proposals for one node when the player was last seen in a SHIP.

    Ship is continuous control: thrust accelerates the player upward while the
    button is held, and gravity pulls it down while released (README, "in a
    ship, holding is continuous thrust rather than a re-triggered jump" --
    also TODO Q5/Q5a). :class:`CandidateSource` has nothing sensible to
    propose there -- its whole menu is scaled by ``Rhythm.airtime_ticks``, a
    ground-contact hop *period*, and a ship in flight does not produce the
    ground->air->ground cycle that measurement is built from (``Rhythm``
    keeps it ``None`` and falls back to ``FALLBACK_AIRTIME_TICKS``, a cube
    number, if nothing here stopped that).

    This class never reads ``rhythm.unit``/``rhythm.airtime_ticks`` for
    exactly that reason. Its start-tick and hold-length menus are scaled by
    ``rhythm.horizon`` instead -- a real, vehicle-agnostic measurement (the
    sensor's own reach, README item 1) -- and use the SAME coarse-to-fine
    sweep shape as :class:`CandidateSource` because that structure (wide
    passes first, refining on later ones) is a property of piecewise-constant
    outcomes in tick space, not of hopping specifically.

    UNVERIFIED, IN FULL. Nobody has run this against a live ship section --
    Stereo Madness's ship portals (x=7995, 22935, 24045, TODO Q5) have never
    been reached by this driver, and no ship-equivalent of the hop-period
    constant has ever been measured to replace the one this class refuses to
    borrow. The only established fact this design leans on is that the
    ``Interval(start_tick, hold_ticks)`` action space itself is
    vehicle-agnostic (README) -- what start ticks and hold lengths are worth
    TRYING for a ship is a guess, stated as one, not a measurement.

    Deliberately NOT ported from :class:`CandidateSource`:

    * ``obstacle_span_ticks`` -- the sensor-shaped candidate that spans a
      hazard cluster. Its gap threshold is ``rhythm.unit * units_per_tick``,
      a cube hop distance; there is no measured ship-equivalent gap to
      substitute, and fabricating one would be exactly the "confident guess"
      this repo's rules forbid. Omitted rather than faked.
    * the airborne-press / wall-detection use of ``player.is_on_ground`` in
      :class:`Sightreader` -- TODO Q5a flags that a ship can rest against
      floor OR ceiling with ``m_isOnGround`` as a single bit, so that signal
      may not mean the same thing here. Left alone (out of this task's
      scope); a ship run will fall back to :class:`Memory`'s slower,
      several-deaths-at-the-same-x wall detection instead, which does not
      depend on ``is_on_ground`` at all.
    """

    def __init__(self, plan: Plan, outcome: "Outcome", rhythm: Rhythm, *,
                 max_span_horizons: float = 2.0):
        self.plan = plan
        self.outcome = outcome
        self.death_tick = outcome.end_tick
        self.first_tick = outcome.first_tick
        self.rhythm = rhythm
        self.max_span_horizons = max_span_horizons
        self._tried: set[Interval] = set()
        self.widen_level = 0
        # Hold lengths as fractions of the sensor HORIZON, not of a hop period
        # -- there is nothing hop-shaped to fraction here. Spans a brief tap
        # to a hold nearly as long as the horizon itself: a ship's vertical
        # correction could need anything from a nudge to sustained thrust
        # across a whole screen of ceiling/floor geometry, and nothing
        # measured yet says which.
        self._hold_fracs: list[float] = [1.0, 0.5, 0.25, 0.125, 0.0625, 0.03125]
        self._gen = self._generate()

    def widen(self) -> None:
        """Broaden the spread after repeated failure. Mirrors CandidateSource."""
        self.widen_level += 1
        self.max_span_horizons *= 2.0
        lo = self._hold_fracs[-1] / 2.0
        hi = self._hold_fracs[0] * 2.0
        self._hold_fracs = [hi] + self._hold_fracs + [lo]
        self._gen = self._generate()

    # -- the space --------------------------------------------------------
    def _earliest_start(self) -> int:
        last = self.plan.last
        floor_tick = self.first_tick + 2
        if last is not None:
            floor_tick = max(floor_tick, last.end_tick + 1)
        return floor_tick

    def _holds(self) -> list[int]:
        h = max(1, self.rhythm.horizon)
        return sorted({max(1, int(f * h)) for f in self._hold_fracs}, reverse=True)

    def _start_passes(self) -> list[list[int]]:
        """Candidate start ticks, coarse pass first. No hop-relative bias:

        unlike :class:`CandidateSource` (centred half a hop before the death
        tick, on the theory a jump must begin before the obstacle it clears),
        there is no equivalent lead-in law for thrust, so the sweep centres on
        the death tick itself and widens outward in ``horizon``-scaled steps.
        """
        lo = self._earliest_start()
        # Same no-op guard as CandidateSource._start_passes: a thrust interval
        # beginning AT the tick the incumbent already died on cannot change
        # anything that happens before that death. Capped at death_tick - 1 so
        # ``t <= hi`` can never propose one. This class shipped with the
        # un-guarded ``max(lo, self.death_tick)`` its cube counterpart was
        # fixed out of; nothing had exercised it, because no ship section has
        # ever been reached live.
        hi = max(lo, self.death_tick - 1)
        span = int(self.max_span_horizons * self.rhythm.horizon)
        lo = max(lo, hi - span)
        centre = max(lo, min(hi, self.death_tick - 1))

        passes: list[list[int]] = []
        seen: set[int] = set()
        step = max(1, self.rhythm.horizon // 4)
        while True:
            this_pass: list[int] = []
            offsets = [0]
            k = step
            while centre - k >= lo or centre + k <= hi:
                offsets.extend((-k, k))
                k += step
            for off in offsets:
                t = centre + off
                if lo <= t <= hi and t not in seen:
                    seen.add(t)
                    this_pass.append(t)
            if this_pass:
                passes.append(this_pass)
            if step == 1:
                break
            step = max(1, step // 2)
        return passes

    def _generate(self):
        # Same backtrack-not-loop guard as CandidateSource: no room to append
        # anything after the committed tail means this node is exhausted, not
        # a dead end to grind on.
        if self._earliest_start() > self.death_tick:
            return

        # 0. Cheapest probe of the "hold is sustained thrust" hypothesis:
        #    thrust from as early as the plan allows to well past where the
        #    last attempt ended.
        lo = self._earliest_start()
        if lo <= self.death_tick:
            yield Interval(lo, max(1, self.death_tick - lo + self.rhythm.horizon))

        # 1. The sweep: coarse pass first, every hold at each start before
        #    moving to the next start -- same ordering rationale as
        #    CandidateSource (start tick matters far more than hold length).
        for starts in self._start_passes():
            for start in starts:
                for hold in self._holds():
                    yield Interval(start, hold)

    def next(self) -> Interval | None:
        for iv in self._gen:
            if iv in self._tried:
                continue
            self._tried.add(iv)
            return iv
        return None


def _last_known_vehicle(outcome: "Outcome") -> Vehicle:
    """The vehicle active at the end of ``outcome``'s tail, if known.

    Read off the LAST :class:`Sight` in the tail the driver was given -- the
    vehicle in force at (or just before) the point where any candidate
    appended after this outcome will begin. ``Vehicle.CUBE`` when no sight is
    available (an unrunnable outcome, or a rollout whose stride never dropped
    to 1), which reproduces this file's only behaviour before vehicle
    awareness existed: propose cube-shaped candidates. Proprioception only
    (``player.vehicle_flags``, ALLOWED) -- no forbidden field is touched.
    """
    if not outcome.sights:
        return Vehicle.CUBE
    return derive_vehicle(outcome.sights[-1].player.vehicle_flags)


def select_candidate_source(plan: Plan, outcome: "Outcome", rhythm: Rhythm, *,
                             max_span_horizons: float = 2.0
                             ) -> "CandidateSource | ShipCandidateSource":
    """Choose the cube or the ship generator for a node, from what was seen.

    The only switch: :class:`ShipCandidateSource` when the last observed
    vehicle was SHIP, :class:`CandidateSource` (unchanged) otherwise --
    including every other vehicle (ball, UFO, wave, robot, spider, swing).
    Nothing has been measured about any of those either; cube's generator is
    the pre-existing default and this function does not widen its scope
    beyond what was asked for (TODO Q5: ship only). Picking the wrong one
    costs nothing but wasted probes -- both generators still only produce
    legal ``Interval``s, so a misclassified vehicle degrades the search, it
    does not corrupt it.
    """
    vehicle = _last_known_vehicle(outcome)
    if vehicle == Vehicle.SHIP:
        return ShipCandidateSource(plan, outcome, rhythm,
                                   max_span_horizons=max_span_horizons)
    return CandidateSource(plan, outcome, rhythm,
                           max_span_horizons=max_span_horizons)


@dataclass
class Node:
    """A committed plan, and the search's opinion of it."""

    plan: Plan
    outcome: Outcome
    parent: "Node | None"
    depth: int
    source: "CandidateSource | ShipCandidateSource"
    subtree_best_x: float
    attempts_spent: int = 0
    exhausted: bool = False
    children: list["Node"] = field(default_factory=list)
    #: Set when this node's own outcome is a confirmed repeat of a detected
    #: wall (see ``Sightreader._wall_tolerance``/``_at_wall``) or a press that
    #: landed while airborne (``_press_was_airborne``). A blocked node is never
    #: pushed to the heap: nothing past a wall's own arrival point can change
    #: whether it was hit, so proposing MORE intervals after it is not a
    #: different bet, it is the same bet again. Kept separate from
    #: ``exhausted`` (which means "this node's generator ran dry") so a report
    #: can tell "gave up" from "was pruned" apart.
    blocked: bool = False

    def priority(self, penalty: float) -> float:
        """Higher is expanded first.

        ``subtree_best_x`` rather than ``outcome.max_x`` is what makes
        backtracking happen: a node whose child found a new frontier keeps a high
        score, so once that child has burned enough probes without paying, the
        *parent* resurfaces and starts proposing alternatives to the very
        decision the child made. That is the "the right action at n only pays off
        given a specific choice at n-1" case, and a driver without it reproduces
        the old greedy stall exactly.
        """
        return self.subtree_best_x - penalty * self.attempts_spent


@dataclass
class SearchReport:
    attempts: int
    game_attempts: int
    duplicates_skipped: int
    best_x: float
    best_plan: Plan
    best_attempt_index: int
    target: float | None
    reached_target: bool
    reached_end_of_level: bool
    audit: list[str]
    unmeasured: list[str]
    rhythm: Rhythm
    wall_seconds: float
    stopped_because: str
    fatal: list[str]
    records: tuple[AttemptRecord, ...]
    #: How many times escalating backtrack fired (see ``Sightreader._escalate``).
    #: Zero means the run never needed it -- the frontier never stalled.
    escalations: int = 0

    def text(self) -> str:
        lines = []
        lines.append("=" * 72)
        lines.append("sightread: A-legal search driver -- report")
        lines.append("=" * 72)
        lines.append(f"stopped because      : {self.stopped_because}")
        lines.append(f"attempts (ledger)    : {self.attempts}"
                     "   <- every probe, including failures and sync attempts")
        lines.append(f"attempts (game says) : {self.game_attempts}"
                     "   <- independent witness: header.attempt")
        lines.append(f"plans skipped as dup : {self.duplicates_skipped}"
                     "   (cost no attempt: determinism means the answer is known)")
        lines.append(f"best observed max x  : {self.best_x:.9f}"
                     f"  (attempt #{self.best_attempt_index})")
        lines.append(f"best plan            : {self.best_plan}")
        if self.target is not None:
            verdict = "REACHED" if self.reached_target else "not reached"
            lines.append(f"target {self.target:.9f} : {verdict}")
        if self.reached_end_of_level:
            lines.append("reached end of level : yes (playerX >= levelLength)")
        lines.append(f"wall clock           : {self.wall_seconds:.1f}s")
        if self.rhythm.units_per_tick is not None:
            lines.append(f"measured units/tick  : {self.rhythm.units_per_tick:.6f} "
                         f"(documented 1x: {Rhythm.DOCUMENTED_UPT_1X}; "
                         f"|diff| {self.rhythm.upt_disagreement():.6f})")
        lines.append(f"measured hop (ticks) : {self.rhythm.airtime_ticks} "
                     f"from {self.rhythm.jumps_seen} landings")
        lines.append(f"measured horizon     : {self.rhythm.horizon_ticks} ticks")
        lines.append(f"wall escalations     : {self.escalations}")
        if self.unmeasured:
            lines.append("UNMEASURED, fallback in use: " + ", ".join(self.unmeasured))
        if self.fatal:
            lines.append("FATAL (the run is not evidence):")
            for f in self.fatal:
                lines.append(f"  - {f}")
        if self.audit:
            lines.append("ATTEMPT-COUNT AUDIT FAILED -- this is not a clean A number:")
            for a in self.audit:
                lines.append(f"  - {a}")
        else:
            lines.append("attempt-count audit  : clean (ledger agrees with the game)")
        lines.append("-" * 72)
        lines.append("every attempt, in order:")
        for r in self.records:
            lines.append(
                f"  #{r.index:<4d} {r.purpose:<9s} "
                f"x={0.0 if r.max_x is None else r.max_x:>12.3f} "
                f"endTick={-1 if r.end_tick is None else r.end_tick:<6d} "
                f"{r.wall_seconds:6.2f}s  {r.plan}"
                + (f"  [{r.note}]" if r.note else "")
            )
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {
            "attempts": self.attempts,
            "game_attempts": self.game_attempts,
            "duplicates_skipped": self.duplicates_skipped,
            "best_x": self.best_x,
            "best_plan": [[iv.start_tick, iv.hold_ticks]
                          for iv in self.best_plan.intervals],
            "target": self.target,
            "reached_target": self.reached_target,
            "reached_end_of_level": self.reached_end_of_level,
            "audit": self.audit,
            "unmeasured": self.unmeasured,
            "units_per_tick": self.rhythm.units_per_tick,
            "airtime_ticks": self.rhythm.airtime_ticks,
            "horizon_ticks": self.rhythm.horizon_ticks,
            "wall_seconds": self.wall_seconds,
            "stopped_because": self.stopped_because,
            "fatal": self.fatal,
            "records": [r.as_dict() for r in self.records],
            "escalations": self.escalations,
        }


class Sightreader:
    """Best-first search over plans, one real attempt per edge.

    The shape is deliberately simple, because the expensive resource is attempts
    and not CPU: pop the most promising node, ask it for one candidate, play it,
    and put both the node and any child back. Every branch that is *considered*
    costs nothing; every branch that is *evaluated* costs an attempt and is on
    the ledger. There are no hidden rollouts, which is the whole difference
    between this and the Benchmark B oracle.
    """

    def __init__(self, runner: Runner, *, budget: int = 400,
                 target_x: float | None = None, patience: float = 8.0,
                 watch_lead_horizons: float = 1.25, verbose: bool = True,
                 progress=print, wall_patience: int = 3):
        self.runner = runner
        self.ledger = runner.ledger
        self.budget = budget
        self.target_x = target_x
        self.patience = patience
        self.watch_lead_horizons = watch_lead_horizons
        self.verbose = verbose
        self.progress = progress
        self.memory = Memory()
        self.rhythm = Rhythm()
        self.best: Outcome | None = None
        #: The tree node the current ``self.best`` outcome sits at, kept in
        #: lockstep with it. Escalating backtrack walks up FROM here, because
        #: every probe fired after a wall is confirmed shares this node's whole
        #: plan as its prefix (measured: 236/236 in the live run that motivated
        #: this).
        self.best_node: Node | None = None
        #: The search tree's root, kept so the shape of the search (did it ever
        #: revisit a decision, or only push the frontier?) is inspectable rather
        #: than inferred from the attempt log.
        self.root: Node | None = None
        self._counter = itertools.count()
        self._heap: list[tuple[float, int, Node]] = []
        self.stopped_because = "budget exhausted"

        #: Base number of probes since the last escalation that still land on
        #: the same detected wall (or land with the decisive press taken while
        #: airborne) before forcing a backtrack. NOT the threshold actually
        #: used once the search has already escalated once for this wall --
        #: see ``_escalate_patience``, which scales this by
        #: ``_backtrack_depth``. Live evidence (orchestrator, 2026-08-18) is
        #: why: holding this flat at every depth reached the plan's root in a
        #: handful of probes (median divergence tick 340, minimum 3, on a
        #: 5-interval committed plan where the actual fix needed only one
        #: interval revised) -- ``_escalate`` was blocking each reopened
        #: ancestor's own not-yet-exhausted local search before it had a real
        #: chance, not because the wall needed root-deep revision but because
        #: three failed probes is too little evidence that a WIDENED sweep
        #: (dozens to hundreds of candidates) has nothing left to offer.
        self.wall_patience = wall_patience
        self._wall_strikes = 0
        self._airborne_strikes = 0
        #: How many ancestor-levels ``_escalate`` climbs on its NEXT trigger.
        #: Starts at 1 (the immediate parent of ``best_node``, i.e. "try a
        #: different last interval") and grows every time escalating to the
        #: current depth is *also* followed by landing back on the same wall --
        #: this is item 2's actual fix: interval N-1, then N-2, then N-3, not a
        #: single fixed hop.
        self._backtrack_depth = 1
        #: How many times ``_escalate`` has actually fired. Reported, not acted
        #: on further -- a run that escalated zero times never needed this file
        #: at all, and one is a useful thing to be able to tell from a distance.
        self.escalations = 0

    # -- bookkeeping ------------------------------------------------------
    def _penalty(self) -> float:
        """World units a node forfeits per failed probe.

        Scaled by the measured units-per-tick so it means the same thing at any
        speed: ``patience`` failed probes cost the node the equivalent of
        ``patience`` * 8 ticks of progress. It is a search knob, not a claim.
        """
        upt = self.rhythm.units_per_tick or Rhythm.DOCUMENTED_UPT_1X
        return self.patience * upt

    def _push(self, node: Node) -> None:
        if node.exhausted:
            return
        heapq.heappush(self._heap,
                       (-node.priority(self._penalty()), next(self._counter), node))

    def _propagate(self, node: Node, x: float) -> None:
        cur: Node | None = node
        while cur is not None:
            if x > cur.subtree_best_x:
                cur.subtree_best_x = x
            cur = cur.parent

    def _watch_from(self, death_tick: int) -> int:
        lead = int(self.watch_lead_horizons * self.rhythm.horizon)
        return max(0, death_tick - lead)

    # -- wall detection and escalating backtrack ---------------------------
    def _escalate_patience(self) -> int:
        """Probes the CURRENT level gets before ``_escalate`` is allowed to
        climb past it, gradual so cheap (shallow) revisions are exhausted
        before expensive (deep) ones are even offered.

        ``self.wall_patience * 2 ** self._backtrack_depth``: at
        ``_backtrack_depth == 1`` (the very first escalation this wall has
        seen -- still deciding whether to revise ``best_node``'s own last
        interval at all) that is already ``2 * wall_patience``, not
        ``wall_patience`` itself. Reason: the level being judged is not a
        single fixed candidate, it is a whole widened sweep (``widen()``
        roughly doubles both the start-tick span and the hold menu each time
        it fires) -- ``wall_patience`` failures is nowhere near enough
        evidence that a sweep with dozens of untried candidates left has
        nothing in it, and treating it as if it were is exactly what turned
        depth-1 escalations into de-facto jumps to the plan root (see
        ``wall_patience``'s docstring for the live numbers). Doubling per
        depth means the total cost of exhausting depths ``1..d`` before
        finally reaching depth ``d+1`` is bounded (a geometric series), so
        root is still reachable given a persistent wall -- ``_escalate``
        keeps unbounded depth as a capability -- it is just no longer free.
        """
        return int(self.wall_patience * (2 ** self._backtrack_depth))

    def _wall_tolerance(self) -> float:
        """How close two deaths' x must be to count as "the same place".

        Derived from the measured hop, not a level constant: a twentieth of
        one hop's travel is far tighter than any real gap between obstacles a
        held input is meant to cross, and far looser than float noise on a
        deterministic replay (README: bit-identical x on an identical prefix).
        """
        upt = self.rhythm.units_per_tick or Rhythm.DOCUMENTED_UPT_1X
        return max(1e-6, 0.05 * self.rhythm.unit * upt)

    def _at_wall(self, out: Outcome) -> bool:
        """Is ``out`` a repeat of the death site :class:`Memory` has flagged?

        ``Memory.death_wall`` only starts answering once >= 3 deaths have
        clustered at the current best x, so this is silent for the first
        couple of probes past a new frontier -- exactly the window in which a
        death there might still be a fluke rather than a wall.
        """
        wall = self.memory.death_wall(self._wall_tolerance())
        return wall is not None and abs(out.max_x - wall[0]) <= self._wall_tolerance()

    def _press_was_airborne(self, out: Outcome, iv: Interval) -> bool | None:
        """Was the player off the ground when ``iv``'s press was due?

        A-legal: reads only ``player.is_on_ground``, an ALLOWED proprioceptive
        field :class:`Rhythm.observe` already reads every attempt -- nothing
        new is exposed to get this signal. If holding really does only
        re-jump on landing (this module's stated, UNVERIFIED bet), a press
        issued while airborne does nothing at all: the interval was scheduled,
        counted, and had zero effect. That makes this a sharper and much
        earlier signal that an interval was wasted than waiting for several
        identical deaths to accumulate in ``Memory`` -- one occurrence already
        says the press did nothing, where the death-site route needs three.

        Returns ``None`` when the watch window did not cover the press tick,
        which must never be read as "grounded": absence of evidence here is
        not evidence of the ground, and misreading it that way would be
        exactly the "measurement changed, not the simulation" mistake this
        repo keeps a rule about.
        """
        for s in out.sights:
            if s.tick == iv.start_tick:
                return not s.player.is_on_ground
        return None

    def _escalate(self) -> None:
        """Force an ancestor of ``best_node`` back into contention.

        This is item 2's actual mechanism. Climbs ``self._backtrack_depth``
        steps up the champion path from ``best_node`` (depth 1 is its parent,
        i.e. "try a different last interval"; depth 2 is the grandparent,
        i.e. "the last interval was never the problem, try a different one
        before it"; and so on). Every node strictly between the reopened
        ancestor and ``best_node`` -- including ``best_node`` itself -- is
        blocked: everything in that span has only ever led back to the
        current wall, so proposing yet another interval after it is the same
        bet again, not a different one (item, "stop generating candidates
        that lead into it unchanged"). The reopened ancestor is widened
        (diversity: a broader start-tick sweep and hold-length menu) before
        being pushed, and the depth grows by one for next time, so a site
        that survives one escalation gets pushed back further on the next.
        """
        if self.best_node is None:
            return
        anc = self.best_node
        for _ in range(self._backtrack_depth):
            if anc.parent is None:
                break
            anc = anc.parent
        cur = self.best_node
        while cur is not anc and cur is not None:
            cur.blocked = True
            cur.exhausted = True
            cur = cur.parent
        anc.source.widen()
        anc.blocked = False
        anc.exhausted = False
        self._push(anc)
        self.escalations += 1
        self._backtrack_depth += 1
        self._wall_strikes = 0
        self._airborne_strikes = 0

    def _say(self, msg: str) -> None:
        if self.verbose:
            self.progress(msg)

    # -- one evaluated edge ----------------------------------------------
    def _evaluate(self, plan: Plan, purpose: str, watch_from: int) -> Outcome:
        out = self.runner.rollout(plan, purpose=purpose, watch_from_tick=watch_from)
        self.rhythm.observe(out.sights)
        self.memory.remember(out)
        if self.best is None or out.max_x > self.best.max_x:
            flag = "  <- NEW FRONTIER"
            self.best = out
        else:
            flag = ""
        self._say(f"  #{out.attempt_index:<4d} {purpose:<9s} "
                  f"x={out.max_x:>12.3f} endTick={out.end_tick:<6d} "
                  f"{plan}{flag}"
                  + (f"  [{out.unrunnable}]" if out.unrunnable else ""))
        return out

    # -- the loop ---------------------------------------------------------
    def run(self) -> SearchReport:
        t0 = time.perf_counter()

        try:
            # `prime()` used to sit outside this try. That meant a game that
            # was already gone before the very first poll -- attach raced a
            # crash, or GD never got past the loading screen -- raised
            # GameGone/TimeoutError straight out of run(), past every handler
            # below, and out of main() as an unhandled crash: no report, no
            # "stopped_because", just a traceback. Moving it inside is the
            # whole fix; RunAborted (GameGone's base) is already handled here.
            self.runner.prime()
            # Attempt 1 is a calibration rollout with no input at all, observed
            # at stride 1 from the first tick. It buys the three numbers the
            # search is scaled by -- units per tick, hop length, sensor horizon --
            # and it costs exactly one attempt, on the ledger like everything
            # else. Nothing is assumed that this could measure.
            root_out = self._evaluate(Plan(()), "calibrate", watch_from=0)
            root = Node(plan=Plan(()), outcome=root_out, parent=None, depth=0,
                        source=select_candidate_source(Plan(()), root_out,
                                                       self.rhythm),
                        subtree_best_x=root_out.max_x)
            self.root = root
            self.best_node = root
            self._push(root)

            while self._heap and self.ledger.count < self.budget:
                if self._done():
                    break
                _, _, node = heapq.heappop(self._heap)
                iv = node.source.next()
                if iv is None:
                    node.exhausted = True
                    continue
                try:
                    cand = node.plan.appended(iv)
                except ValueError:
                    # Overlaps the committed tail. Not a candidate; costs nothing.
                    self._push(node)
                    continue
                if self.memory.seen(cand):
                    self.memory.duplicates_skipped += 1
                    self._push(node)
                    continue

                node.attempts_spent += 1
                out = self._evaluate(cand, "probe",
                                     self._watch_from(node.outcome.end_tick))
                self._push(node)
                became_best = self.best is out
                if became_best:
                    self._backtrack_depth = 1
                    self._wall_strikes = 0
                    self._airborne_strikes = 0
                if out.ok:
                    child = Node(plan=cand, outcome=out, parent=node,
                                 depth=node.depth + 1,
                                 source=select_candidate_source(cand, out,
                                                                self.rhythm),
                                 subtree_best_x=out.max_x)
                    node.children.append(child)
                    self._propagate(node, out.max_x)
                    if became_best:
                        self.best_node = child
                        self._push(child)
                    else:
                        # Wall detection (item 1): a death that repeats an
                        # already-flagged site, or a press that landed on a
                        # cube already off the ground, cannot be fixed by
                        # yet another interval appended after it -- block the
                        # child instead of handing it a fresh CandidateSource
                        # to explore forever. That is what let 236/236 probes
                        # in the live run that motivated this share one
                        # 5-interval prefix: every failed probe manufactured a
                        # brand-new, zero-penalty child that outranked every
                        # already-probed ancestor, so the search never ran out
                        # of "fresh" territory to push one tick deeper into.
                        airborne = self._press_was_airborne(out, iv)
                        at_wall = self._at_wall(out)
                        if airborne or at_wall:
                            child.blocked = True
                            child.exhausted = True
                            if airborne:
                                self._airborne_strikes += 1
                            if at_wall:
                                self._wall_strikes += 1
                            patience = self._escalate_patience()
                            if (self._airborne_strikes >= patience
                                    or self._wall_strikes >= patience):
                                self._escalate()
                        else:
                            self._push(child)
        except RunAborted as exc:
            self.stopped_because = f"aborted: {exc}"
        except TimeoutError as exc:
            # Reachable when the process is confirmed ALIVE but has stopped
            # answering (GameGone above already handles the confirmed-dead
            # case). A genuine hang is not evidence either, so this stops the
            # run the same way rather than crashing main() with a traceback --
            # but it is reported under its own name so a hang is never read as
            # a clean "level completed"/"budget exhausted" stop.
            self.stopped_because = f"aborted: game stopped responding: {exc}"
        except KeyboardInterrupt:
            self.stopped_because = "interrupted"

        if self._done():
            self.stopped_because = ("target reached" if self._hit_target()
                                    else "level completed")
        elif self.ledger.count >= self.budget:
            self.stopped_because = f"budget of {self.budget} attempts exhausted"
        elif not self._heap and not self.stopped_because.startswith("aborted"):
            self.stopped_because = "search space exhausted"

        return self.report(time.perf_counter() - t0)

    def _hit_target(self) -> bool:
        return (self.target_x is not None and self.best is not None
                and self.best.max_x >= self.target_x)

    def _done(self) -> bool:
        if self.best is None:
            return False
        return self._hit_target() or self.best.reached_end_of_level

    def report(self, wall: float) -> SearchReport:
        best = self.best
        return SearchReport(
            attempts=self.ledger.count,
            game_attempts=self.ledger.game_attempts,
            duplicates_skipped=self.memory.duplicates_skipped,
            best_x=best.max_x if best else float("nan"),
            best_plan=best.plan if best else Plan(()),
            best_attempt_index=best.attempt_index if best else -1,
            target=self.target_x,
            reached_target=self._hit_target(),
            reached_end_of_level=bool(best and best.reached_end_of_level),
            audit=self.ledger.audit(),
            unmeasured=self.rhythm.unmeasured(),
            rhythm=self.rhythm,
            wall_seconds=wall,
            stopped_because=self.stopped_because,
            fatal=list(self.runner.fatal),
            records=self.ledger.records,
            escalations=self.escalations,
        )


# ---------------------------------------------------------------------------
# Throughput measurement (run this before believing any cost estimate)
# ---------------------------------------------------------------------------

def bench_loopback(strides=(1, 8, 64), objects=(0, 120), round_trips=2000,
                   switch_intervals=(0.005, 0.00005), out=print) -> list[dict]:
    """Python-side round-trip cost over the loopback. Not an attempts/sec figure.

    Two artifacts are swept rather than assumed away, because either alone
    produces a confident wrong number:

    * ``SyntheticGame`` rebuilds ``attemptTime`` with an O(tick) Python loop per
      publish. Memoised here; the mod pays nothing for it.
    * The writer is a thread, so the GIL switch interval bounds the round trip.
      At the 5 ms default this measures 1/0.0065 s = ~155/s *whatever the
      protocol costs*. Sweeping the interval is what shows that the number was
      the scheduler, not the wire. Against the mod the writer is a separate
      process and this does not apply, which is why the real-game figure
      (~500/s, README) is bounded by the game rather than by this.
    """
    import threading

    from env import SyntheticGame

    step = float(np.float32(1.0 / GDRL_TICK_HZ))
    cache = {"tick": 0, "t": 0.0}
    real_attempt_time = envmod._accumulated_attempt_time

    def memo_attempt_time(tick: int) -> float:
        if tick < cache["tick"]:
            cache["tick"], cache["t"] = 0, 0.0
        t = cache["t"]
        for _ in range(tick - cache["tick"]):
            t += step
        cache["tick"], cache["t"] = tick, t
        return t

    results: list[dict] = []
    old_switch = sys.getswitchinterval()
    envmod._accumulated_attempt_time = memo_attempt_time
    try:
        for switch in switch_intervals:
            sys.setswitchinterval(switch)
            for n_obj in objects:
                for stride in strides:
                    buf = make_loopback_buffer()
                    game = SyntheticGame(buf)
                    chan = Channel(buf)
                    chan.attach()
                    objs = [{"x": 500.0 + 30 * i, "y": 105.0, "objectType": 2}
                            for i in range(n_obj)]
                    stop = threading.Event()

                    def writer():
                        tick = 1
                        game.publish(tick=tick, player_x=float(tick), objects=objs)
                        while not stop.is_set():
                            deadline = time.monotonic() + 2.0
                            while int(game.control["actSeq"]) != game.seq:
                                if stop.is_set() or time.monotonic() > deadline:
                                    return
                            try:
                                adv = game.consume()
                            except Exception:
                                return
                            tick += adv
                            game.publish(tick=tick, player_x=float(tick),
                                         objects=objs)

                    th = threading.Thread(target=writer, daemon=True)
                    th.start()
                    try:
                        chan.poll(timeout=2.0)
                        t0 = time.perf_counter()
                        for _ in range(round_trips):
                            chan.step([], advance_steps=stride, timeout=2.0)
                        dt = time.perf_counter() - t0
                    finally:
                        stop.set()
                        th.join(timeout=1.0)
                        chan.detach()
                        buf.close()
                    row = {"switch_interval": switch, "objects": n_obj,
                           "stride": stride,
                           "round_trips_per_s": round_trips / dt,
                           "sim_ticks_per_s": round_trips * stride / dt}
                    results.append(row)
                    out(f"switch={switch:<9g} objects={n_obj:4d} stride={stride:4d}  "
                        f"{row['round_trips_per_s']:8.1f} round-trips/s  "
                        f"{row['sim_ticks_per_s']:10.1f} sim-ticks/s")
    finally:
        envmod._accumulated_attempt_time = real_attempt_time
        sys.setswitchinterval(old_switch)
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

#: The record to beat, from README ("12 jumps"). It is a TARGET, never an input
#: to the search: no tick, x or object of Stereo Madness appears anywhere in the
#: proposal machinery, and passing --target only changes when the driver stops.
RECORD_MAX_X = 3959.183837891


def jsonl_writer(path: str) -> Callable[[AttemptRecord], None]:
    """Open ``path`` for append and return a callback that writes one attempt.

    Every call writes one JSON line and flushes it to the OS immediately --
    that is the durability property, not "gets written by the time the process
    exits", because the whole reason this exists is that the process might not.
    ``fsync`` is included: ``flush()`` alone only moves the bytes out of
    Python's buffer into the OS page cache, which already survives this
    process being killed or SIGSEGV-ing, but not a hard power loss; the fsync
    is cheap at one line per real attempt (never more than a few per second)
    and removes the need to reason about which failure modes it does and does
    not cover.

    The file is opened once, in append mode, and kept open for the life of the
    run -- so restarting a crashed driver at the same path adds to the record
    of that path rather than silently discarding what a previous process wrote.
    """
    fh = open(path, "a", buffering=1)   # line-buffered

    def _write(rec: AttemptRecord) -> None:
        fh.write(json.dumps(rec.as_dict()) + "\n")
        fh.flush()
        os.fsync(fh.fileno())

    return _write


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="A-legal search driver for gd-rl.",
        epilog="Launch GD first with GDRL_ENV=1 (see the module docstring).")
    ap.add_argument("--budget", type=int, default=400,
                    help="hard cap on REAL attempts, failures included")
    ap.add_argument("--target", type=float, default=RECORD_MAX_X,
                    help="stop when observed max x reaches this "
                         f"(default: the {RECORD_MAX_X} record). "
                         "Use -1 to run the whole budget.")
    ap.add_argument("--patience", type=float, default=8.0,
                    help="ticks-equivalent a node forfeits per failed probe; "
                         "smaller backtracks sooner")
    ap.add_argument("--wall-patience", type=int, default=3,
                    help="BASE count of consecutive probes that land back on "
                         "the same death site, or land with the press taken "
                         "while airborne, before escalating backtrack forces "
                         "an earlier interval back into contention -- the "
                         "actual threshold used doubles at every escalation "
                         "depth (Sightreader._escalate_patience), so this is "
                         "the cost of the FIRST backtrack, not of every one")
    ap.add_argument("--watch-horizons", type=float, default=1.25,
                    help="how many measured sensor horizons before the frontier "
                         "the observation stride drops to 1")
    ap.add_argument("--max-stride", type=int, default=2048,
                    help="observation stride cap while replaying the prefix")
    ap.add_argument("--timeout", type=float, default=20.0,
                    help="seconds to wait for one observation")
    ap.add_argument("--shm", default="gdrl.env", help="shared segment name")
    ap.add_argument("--json", default=None, help="write the full report here, "
                    "at the end of the run")
    ap.add_argument("--jsonl", default=None,
                    help="append each closed attempt here AS IT HAPPENS, "
                         "flushed and fsynced immediately -- survives a crash "
                         "mid-run. Defaults to '<json>.jsonl' when --json is "
                         "given and this is not; pass explicitly to get "
                         "incremental durability without an end-of-run file, "
                         "or to name it yourself")
    ap.add_argument("--bench", action="store_true",
                    help="measure loopback round-trip cost and exit "
                         "(no game needed)")
    args = ap.parse_args(argv)

    if args.bench:
        bench_loopback()
        return 0

    jsonl_path = args.jsonl or (f"{args.json}.jsonl" if args.json else None)
    on_close = jsonl_writer(jsonl_path) if jsonl_path else None
    if jsonl_path:
        print(f"appending each closed attempt to {jsonl_path}")

    # From here on, a dead/vanished game must not crash this process -- see
    # GameGone. Sightreader.run() already converts GameGone/RunAborted/a bare
    # poll TimeoutError into a clean stop with a populated report, so the only
    # gap left here is BEFORE run() is reached: wait_for_shared timing out (the
    # game never started) and chan.attach() racing a crash between the segment
    # appearing and the handshake completing. Both are narrow windows, but per
    # TODO's "cost two full runs" story, narrow is not the same as impossible.
    try:
        buf = wait_for_shared(args.shm, timeout=30.0)
    except HandshakeError as exc:
        print(f"never found a live game to attach to: {exc}", file=sys.stderr)
        return 2

    chan = Channel(buf)
    ledger = AttemptLedger(on_close=on_close)
    runner = Runner(chan, ledger, max_stride=args.max_stride, timeout=args.timeout)
    search = Sightreader(runner, budget=args.budget,
                         target_x=None if args.target < 0 else args.target,
                         patience=args.patience,
                         watch_lead_horizons=args.watch_horizons,
                         wall_patience=args.wall_patience)
    try:
        chan.attach()
    except HandshakeError as exc:
        print(f"attach failed, no attempts were played: {exc}", file=sys.stderr)
        buf.close()
        return 2

    try:
        report = search.run()
    finally:
        # Best-effort: if the segment really is gone, these are backstops (see
        # Channel.detach's own docstring), not the primary defense. They are
        # not expected to raise, but see the module-level note below GameGone:
        # nothing here can promise that against a torn/unmapped segment.
        chan.detach()
        buf.close()

    print(report.text())
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report.as_dict(), fh, indent=2)
        print(f"\nfull ledger written to {args.json}")
    if report.fatal:
        print(f"\nFATAL, run stopped early: {'; '.join(report.fatal)}",
              file=sys.stderr)
    return 0 if not report.audit and not report.fatal else 1


if __name__ == "__main__":
    raise SystemExit(main())
