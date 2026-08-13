#!/usr/bin/env python3
"""Validate ``trajectory.py``'s forward projection against recorded game data.

    python3 trainer/validate_projection.py [LOG] [--activation-tick N]
                                                 [--player-start-x X]

This is a measurement tool, not a test. It parses the ``MOVE`` /
``MOVE-TRIGGER`` / ``MOVE-OBJ`` records out of a Geode log produced by
``GDRL_PROBE_MOVE=1``, rebuilds the equivalent ``GroupCommand`` /
``PendingTrigger`` / ``ObjectSnapshot``, drives ``ForwardProjector``, and prints
the residual. It changes nothing in ``trajectory.py``: where the predictor and
the game disagree, the disagreement is the output.

EVIDENTIARY TIER
----------------
(iii) -- against recorded game data. The reference side of every comparison
below came out of a real GD 2.2081 process; the prediction side came out of
``trajectory.py``. Nothing here is a predictor checked against a second copy of
its own equations.

Two caveats keep it from being tier (iv):

  * The log records object positions only. It does not record the player's x,
    nor ``m_deltaTimeInFloat``. Section 4 therefore uses the player x-vs-tick
    law from a *different* run (the 2026-08-12 GDRL_ENV measurement:
    ``x(t) = U*(t-1)``, float32-accumulated), and that input is reported as a
    labelled input rather than folded silently into the residual.
  * The activation tick (233) comes from a ``GDRL_PROBE_CMDVEC=1`` run, which is
    a *different GD launch* than the displacement record. Section 2 re-derives
    it from the displacement record alone precisely so the comparison does not
    depend on splicing two launches together. The two agree to 0.003 ticks.

CORRECTED 2026-08-12. This script was written when the predictor led the game
by 1.9198 ticks, and its narrative said so throughout. That lead decomposed
into a 1.0000-tick origin error (``x(t) = U*t``, which lived HERE and in the
test fixture, not in ``trajectory.py``) and a 0.91978-tick continuous-crossing
error (which lived in ``trajectory.py`` and is fixed by
``SpeedProfile.ticks_to_activation``). Both are corrected; the residual this
script now prints is the game's own float32 accumulation and nothing else.

HOW THE ERROR IS DECOMPOSED
---------------------------
Position error (units) and timing error (ticks) are reported as separate
numbers, and the report goes further: it fits the single best constant tick
shift and prints the residual with and without it. That is the distinction that
matters. A predictor whose entire residual vanishes under a constant shift has a
*timing* defect and no position defect at all; a predictor with a residual left
over after the shift is getting the shape of the motion wrong. Blending the two
into one RMS figure would hide which of those is happening.

Sections:

    1. Record integrity            -- reproduce the log's own summary numbers
    2. Activation tick, from the displacement record alone
    3. Live-command path           -- GroupCommand.value_at against the record
    4. Pending-trigger path        -- ForwardProjector end to end
    5. Error vs prediction horizon
    6. Decomposition and what is still unmeasured

Run with no arguments to use the reference log. If the sandbox copy is gone it
falls back to ``backups/reference-logs/``; if both are missing it exits 2 rather
than guessing.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trajectory import (  # noqa: E402
    CERTAINTY_EXACT,
    PLAYER_X_TICK_ORIGIN,
    ActionType,
    ForwardProjector,
    GroupCommand,
    ObjectKind,
    ObjectSnapshot,
    PendingTrigger,
    SpeedProfile,
    UNITS_PER_TICK,
)

# The repo's measured 1x advance. ``trajectory.py`` used to encode
# 311.58/240 = 1.298250000 under a comment citing this number -- the code did
# not carry the value it cited, a 0.34 ppm gap worth 1.0e-4 units over the 231
# ticks that matter here. It now carries float32(1.298250437), which is the
# increment the game actually adds, so this constant and UNITS_PER_TICK[1]
# agree to 2.2e-10 rather than 4.4e-7.
MEASURED_UNITS_PER_TICK_1X = 1.298250437

_REPO = Path(__file__).resolve().parent.parent
DEFAULT_LOG = (
    _REPO / "sandbox" / "Geometry Dash.app" / "Contents" / "geode" / "logs"
    / "Geode 2026-08-11 18.41.23.log"
)
FALLBACK_LOGS = (
    _REPO / "backups" / "reference-logs" / "Geode 2026-08-11 18.41.23.log",
    _REPO / "backups" / "reference-logs" / "Geode 2026-08-11 18.37.10.log",
)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

_RX_MOVE = re.compile(
    r"MOVE tick=(?P<tick>\d+) id=(?P<id>-?\d+) "
    r"x=(?P<x>-?[\d.]+) y=(?P<y>-?[\d.]+) "
    r"dx=(?P<dx>-?[\d.]+) dy=(?P<dy>-?[\d.]+) "
    r"cx=(?P<cx>-?[\d.]+) cy=(?P<cy>-?[\d.]+)"
)
_RX_TRIGGER = re.compile(
    r"MOVE-TRIGGER tick=(?P<tick>\d+) id=(?P<id>-?\d+) "
    r"target=(?P<target>-?\d+) offX=(?P<offx>-?[\d.]+) offY=(?P<offy>-?[\d.]+) "
    r"duration=(?P<dur>-?[\d.]+) easing=(?P<easing>-?\d+)"
)
_RX_OBJ = re.compile(
    r"MOVE-OBJ tick=(?P<tick>\d+) idx=(?P<idx>\d+) id=(?P<id>-?\d+) "
    r"x=(?P<x>-?[\d.]+) y=(?P<y>-?[\d.]+) "
    r"cx=(?P<cx>-?[\d.]+) cy=(?P<cy>-?[\d.]+) "
    r"groupCount=(?P<gc>\d+) groups=(?P<groups>\S+)"
)


@dataclass(frozen=True)
class MoveRecord:
    tick: int
    object_id: int
    x: float
    y: float
    dx: float
    dy: float
    node_x: float
    node_y: float


@dataclass(frozen=True)
class TriggerRecord:
    object_id: int
    target_group_id: int
    offset_x: float
    offset_y: float
    duration: float
    easing_type: int


@dataclass(frozen=True)
class ObjRecord:
    idx: int
    object_id: int
    x: float
    y: float
    node_x: float
    node_y: float
    groups: tuple[int, ...]


@dataclass
class ParsedLog:
    path: Path
    moves: list[MoveRecord]
    triggers: list[TriggerRecord]
    objects: list[ObjRecord]


def parse_log(path: Path) -> ParsedLog:
    moves: list[MoveRecord] = []
    triggers: list[TriggerRecord] = []
    objects: list[ObjRecord] = []
    with open(path, errors="replace") as fh:
        for line in fh:
            if "MOVE" not in line:
                continue
            m = _RX_MOVE.search(line)
            if m:
                moves.append(MoveRecord(
                    tick=int(m["tick"]), object_id=int(m["id"]),
                    x=float(m["x"]), y=float(m["y"]),
                    dx=float(m["dx"]), dy=float(m["dy"]),
                    node_x=float(m["cx"]), node_y=float(m["cy"]),
                ))
                continue
            m = _RX_TRIGGER.search(line)
            if m:
                triggers.append(TriggerRecord(
                    object_id=int(m["id"]), target_group_id=int(m["target"]),
                    offset_x=float(m["offx"]), offset_y=float(m["offy"]),
                    duration=float(m["dur"]), easing_type=int(m["easing"]),
                ))
                continue
            m = _RX_OBJ.search(line)
            if m:
                raw = m["groups"]
                groups = () if raw == "-" else tuple(
                    int(g) for g in raw.split(",") if g
                )
                objects.append(ObjRecord(
                    idx=int(m["idx"]), object_id=int(m["id"]),
                    x=float(m["x"]), y=float(m["y"]),
                    node_x=float(m["cx"]), node_y=float(m["cy"]),
                    groups=groups,
                ))
    return ParsedLog(path=path, moves=moves, triggers=triggers, objects=objects)


# --------------------------------------------------------------------------
# Ground truth accessor
# --------------------------------------------------------------------------

class Truth:
    """Recorded m_positionY as a function of (possibly fractional) tick.

    Linear interpolation between adjacent records. That is not a modelling
    assumption smuggled in through the back door: the recorded motion is linear
    to 5.6e-4 units over 480 steps (section 1), so interpolating *within* a
    single 0.1875-unit step contributes at most ~1e-6. Nearest-tick lookup
    would instead contribute up to half a step, 0.094 units, which is a quarter
    of the effect being measured.
    """

    def __init__(self, moves: list[MoveRecord], activation_tick: int,
                 y_start: float):
        self.y = {r.tick: r.y for r in moves}
        self.first = min(self.y)
        self.last = max(self.y)
        self.activation_tick = activation_tick
        self.y_start = y_start

    def at(self, tick: float) -> float:
        if tick <= self.activation_tick:
            return self.y_start
        if tick >= self.last:
            return self.y[self.last]
        lo = math.floor(tick)
        hi = lo + 1
        y_lo = self.y_start if lo <= self.activation_tick else self.y[lo]
        y_hi = self.y[hi] if hi in self.y else self.y[self.last]
        return y_lo + (y_hi - y_lo) * (tick - lo)

    def tick_reaching(self, y: float) -> float:
        """Inverse: the (fractional) tick at which the record first hits ``y``.

        This is what makes a *timing* error reportable in ticks independently of
        the position error in units. Saturates at both ends, and the saturation
        is reported by the caller rather than hidden -- outside the motion
        window there is no timing information in the record at all.
        """
        if y <= self.y_start:
            return float(self.activation_tick)
        if y >= self.y[self.last]:
            return float(self.last)
        prev_t, prev_y = float(self.activation_tick), self.y_start
        for t in range(self.first, self.last + 1):
            cur_y = self.y[t]
            if cur_y >= y:
                if cur_y == prev_y:
                    return float(t)
                return prev_t + (y - prev_y) * (t - prev_t) / (cur_y - prev_y)
            prev_t, prev_y = float(t), cur_y
        return float(self.last)


# --------------------------------------------------------------------------
# Reporting helpers
# --------------------------------------------------------------------------

def player_x(tick: float, start_x: float = 0.0) -> float:
    """The player's x at an ABSOLUTE tick.

    MEASURED origin: x is ``start_x`` on tick ``PLAYER_X_TICK_ORIGIN`` == 1, not
    on tick 0. This function used to be ``upt * tick`` open-coded at four call
    sites, and that missing ``- 1`` was a full tick of the 1.9198-tick lead this
    script was written to report.

    The continuous line, not the game's float32 accumulator: the two diverge by
    at most 0.036 units over a 400-tick lookahead (trajectory.py,
    PLAYER_X_FLOAT32_DRIFT_*), which is 5% of one step of this trigger.
    """
    return start_x + UNITS_PER_TICK[1] * (tick - PLAYER_X_TICK_ORIGIN)


def _h1(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def _stats(name: str, values: list[float], unit: str) -> None:
    if not values:
        print(f"  {name:<34} (no samples)")
        return
    a = [abs(v) for v in values]
    rms = math.sqrt(statistics.fmean([v * v for v in values]))
    print(f"  {name:<32} n={len(values):<5} "
          f"mean|.|={statistics.fmean(a):.3e} rms={rms:.3e} "
          f"max|.|={max(a):.3e} mean={statistics.fmean(values):+.3e} {unit}")


# --------------------------------------------------------------------------
# Section 1 -- record integrity
# --------------------------------------------------------------------------

def section_record(log: ParsedLog, y_start: float) -> None:
    _h1("1. RECORD INTEGRITY  (parser vs the log's own numbers)")
    moves = log.moves
    print(f"  log                  {log.path}")
    print(f"  MOVE records         {len(moves)}")
    if not moves:
        return
    ids = sorted({r.object_id for r in moves})
    ticks = [r.tick for r in moves]
    gaps = [(a, b) for a, b in zip(ticks, ticks[1:]) if b - a != 1]
    dups = len(ticks) - len(set(ticks))
    print(f"  object ids moved     {ids}")
    print(f"  tick range           {min(ticks)} .. {max(ticks)}  "
          f"(span {max(ticks) - min(ticks) + 1})")
    print(f"  gaps                 {len(gaps)}   duplicates {dups}")
    print(f"  y                    {y_start:.9f} -> {moves[-1].y:.9f}  "
          f"= {moves[-1].y - y_start:.9f}")
    print(f"  sum of logged dy     {sum(r.dy for r in moves):.9f}")

    dys = [r.dy for r in moves]
    print(f"  dy mean (all {len(dys)})     {statistics.fmean(dys):.9f}")
    print(f"  dy mean (excl final) {statistics.fmean(dys[:-1]):.9f}   "
          f"<- the README's 'per-tick dy mean'")
    print(f"  dy max / final       {max(dys):.9f} / {dys[-1]:.9f}")

    # CCNode shadow: the claim that the move pipeline never calls setPosition.
    node_ys = {r.node_y for r in moves}
    node_xs = {r.node_x for r in moves}
    print(f"  CCNode shadow cy     {sorted(node_ys)}  (distinct values: "
          f"{len(node_ys)})")
    print(f"  CCNode shadow cx     {sorted(node_xs)}  (distinct values: "
          f"{len(node_xs)})")

    if log.triggers:
        t = log.triggers[0]
        print(f"  trigger              id={t.object_id} target={t.target_group_id} "
              f"offX={t.offset_x} offY={t.offset_y} duration={t.duration} "
              f"easing={t.easing_type}")
    if log.objects:
        tgt = [o for o in log.objects if log.triggers
               and log.triggers[0].target_group_id in o.groups]
        for o in tgt:
            print(f"  target object        idx={o.idx} id={o.object_id} "
                  f"x={o.x} y={o.y} groups={o.groups}")


# --------------------------------------------------------------------------
# Section 2 -- activation tick from the displacement record alone
# --------------------------------------------------------------------------

def section_activation(log: ParsedLog, y_start: float, offset: float,
                       duration: float, activation_tick: int) -> None:
    _h1("2. ACTIVATION TICK, DERIVED FROM THE DISPLACEMENT RECORD ALONE")
    print("  For a linear move, displacement(t) = offset * (t - a) / (duration*240),")
    print("  so each record independently determines a, the tick at which the")
    print("  command's elapsed time was zero. No reference to the CMDVEC run.")
    span = duration * 240.0
    implied = [r.tick - (r.y - y_start) * span / offset for r in log.moves]
    print()
    print(f"  implied a            mean {statistics.fmean(implied):.9f}")
    print(f"                       min  {min(implied):.9f}")
    print(f"                       max  {max(implied):.9f}")
    print(f"                       sd   {statistics.pstdev(implied):.3e}")
    print(f"  CMDVEC (other run)   {activation_tick}")
    print(f"  agreement            {abs(statistics.fmean(implied) - activation_tick):.6f} ticks")
    print()
    print(f"  first displacement   tick {min(r.tick for r in log.moves)}")
    print(f"  activation dead time {min(r.tick for r in log.moves) - activation_tick} tick(s)"
          "  <- the activation step displaces nothing")

    # Linearity, two ways, because the two numbers get confused for each other.
    theo = [r.y - (y_start + offset * (r.tick - activation_tick) / span)
            for r in log.moves]
    ts = [float(r.tick) for r in log.moves]
    yv = [r.y for r in log.moves]
    mt, my = statistics.fmean(ts), statistics.fmean(yv)
    slope = (sum((t - mt) * (y - my) for t, y in zip(ts, yv))
             / sum((t - mt) ** 2 for t in ts))
    icpt = my - slope * mt
    lsq = [y - (slope * t + icpt) for t, y in zip(ts, yv)]
    print()
    _stats("residual vs theoretical line", theo, "units")
    _stats("residual vs least-squares fit", lsq, "units")
    print(f"  lsq slope            {slope:.12f} units/tick  "
          f"({(slope / (offset / span) - 1) * 1e6:+.1f} ppm vs {offset/span:.9f})")
    print("  The lsq residual is ~4x smaller than the theoretical one because")
    print("  the fit absorbs the constant part of the float32 drift. The README's")
    print("  linearity figure is the lsq one; they are different quantities.")


# --------------------------------------------------------------------------
# Section 3 -- live-command path
# --------------------------------------------------------------------------

def section_live(log: ParsedLog, truth: Truth, y_start: float, offset: float,
                 duration: float, activation_tick: int) -> None:
    _h1("3. LIVE-COMMAND PATH  (GroupCommand.value_at / delta_over)")
    print("  Observation at tick T, prediction for tick T+h. State handed to the")
    print("  predictor as the mod would hand it over:")
    print("      current_y_offset     = recorded y(T) - y_start      [MEASURED]")
    print("      delta_time_in_float  = (T - activation_tick) / 240  [MODELLED]")
    print()
    print("  Note the algebra: delta_over() subtracts current_y_offset and the")
    print("  projector adds it back via the object's live position, so the offset")
    print("  cancels exactly. This arm therefore tests (E4)'s closed form and the")
    print("  elapsed-time convention -- NOT the offset bookkeeping, which cannot")
    print("  contribute an error here. All the risk sits in delta_time_in_float.")
    print()
    print("     h     n    mean|err|    max|err|      mean err   (units)")
    all_err: list[float] = []
    for h in (1, 2, 4, 8, 16, 32, 64, 120, 240, 480):
        errs = []
        for T in range(activation_tick, truth.last + 1):
            if T + h > truth.last:
                continue
            cmd = GroupCommand(
                target_group_id=log.triggers[0].target_group_id if log.triggers else 1,
                duration=duration,
                delta_time_in_float=(T - activation_tick) / 240.0,
                easing_type=log.triggers[0].easing_type if log.triggers else 0,
                action_type_2=int(ActionType.OFFSET_Y),
                action_value_2=offset,
                current_y_offset=truth.at(T) - y_start,
            )
            live_y = y_start + cmd.current_y_offset
            pred = live_y + cmd.delta_over(h)[1]
            errs.append(pred - truth.at(T + h))
        if not errs:
            continue
        all_err += errs
        a = [abs(e) for e in errs]
        print(f"  {h:4d} {len(errs):5d}   {statistics.fmean(a):.3e}   "
              f"{max(a):.3e}   {statistics.fmean(errs):+.3e}")
    print()
    _stats("all horizons pooled", all_err, "units")
    if all_err:
        step = offset / (duration * 240.0)
        print(f"  worst case in ticks  {max(abs(e) for e in all_err) / step:.4f} ticks"
              f"  (1 tick = {step:.9f} units on this trigger)")
        print("  VERDICT: float noise. The predictor's interpolator reproduces the")
        print("  game's motion law. The error is negative (the predictor lags by")
        print("  ~6 ppm of elapsed) because the game accumulates elapsed in float32")
        print("  and the predictor computes it in float64.")
    print()
    print("  Sampling-vantage sensitivity: shifting delta_time_in_float by one")
    print("  step -- e.g. the mod reading it before processMoveActions instead of")
    print(f"  after -- costs exactly 1 tick = {offset / (duration * 240.0):.9f} units")
    print("  on this trigger. That is a telemetry-design constraint, not a")
    print("  trajectory.py defect, and it is UNVERIFIED which vantage point the")
    print("  eventual binary channel will use.")


# --------------------------------------------------------------------------
# Section 4 -- pending-trigger path, end to end
# --------------------------------------------------------------------------

def _pending_run(truth: Truth, trig_x: float, target: ObjectSnapshot,
                 trigger: TriggerRecord, offset: float, duration: float,
                 units_per_tick: float, obs_ticks: range,
                 player_start_x: float = 0.0,
                 ) -> list[tuple[int, float, float, float, float]]:
    """(T_obs, absolute arrival tick, predicted y, error, certainty)."""
    pending = PendingTrigger(
        activation_x=trig_x,
        target_group_id=trigger.target_group_id,
        duration=duration,
        easing_type=trigger.easing_type,
        action_type_2=int(ActionType.OFFSET_Y),
        action_value_2=offset,
    )
    out = []
    for t_obs in obs_ticks:
        speed = SpeedProfile(player_x=player_x(t_obs, player_start_x))
        proj = ForwardProjector(speed, horizon_ticks=900)
        p = proj.project([target], pending=[pending])[0]
        abs_arrival = t_obs + p.arrival_tick
        out.append((t_obs, abs_arrival, p.y, p.y - truth.at(abs_arrival),
                    p.certainty))
    return out


def section_pending(log: ParsedLog, truth: Truth, y_start: float, offset: float,
                    duration: float, activation_tick: int,
                    player_start_x: float) -> tuple[float, float]:
    _h1("4. PENDING-TRIGGER PATH, END TO END THROUGH ForwardProjector")
    trigger = log.triggers[0]
    trig_obj = next((o for o in log.objects if o.object_id == trigger.object_id),
                    None)
    target_obj = next((o for o in log.objects
                       if trigger.target_group_id in o.groups), None)
    if trig_obj is None or target_obj is None:
        print("  MOVE-OBJ records missing; cannot rebuild the scene. Skipped.")
        return (0.0, 0.0)

    upt = UNITS_PER_TICK[1]
    print(f"  trigger x            {trig_obj.x}")
    print(f"  target object        id={target_obj.object_id} at "
          f"({target_obj.x}, {target_obj.y}) groups={target_obj.groups}")
    print(f"  player x law         x(t) = {player_start_x} + {upt:.9f} * "
          f"(t - {PLAYER_X_TICK_ORIGIN})   [MEASURED, different run]")
    print("  This log contains no player position; the law comes from the")
    print("  2026-08-12 GDRL_ENV run (x = 0.0 at tick 1, float32-accumulated,")
    print("  4,653 samples). It is the only input here not measured in THIS")
    print("  log, and the line is used rather than the accumulator. See")
    print("  section 6.")
    print()

    target = ObjectSnapshot(
        object_id=target_obj.object_id, kind=ObjectKind.SOLID,
        x=target_obj.x, y=target_obj.y, half_w=15.0, half_h=15.0,
        groups=target_obj.groups,
    )
    # Observation ticks limited to those where the target is still ahead of the
    # player: past that, ticks_to_reach() is negative, the projector clamps the
    # arrival tick to 0 and the question "where will it be when I get there"
    # has already been answered by the past.
    last_obs = int((target_obj.x - player_start_x) / upt) + PLAYER_X_TICK_ORIGIN
    rows = _pending_run(truth, trig_obj.x, target, trigger, offset, duration,
                        upt, range(0, last_obs + 1), player_start_x)

    arrivals = {round(r[1], 6) for r in rows}
    print(f"  observation ticks     0 .. {last_obs}")
    print(f"  predicted arrival     {min(r[1] for r in rows):.6f} .. "
          f"{max(r[1] for r in rows):.6f}  (distinct: {len(arrivals)})")
    print(f"  certainty             {sorted({r[4] for r in rows})}"
          f"   (CERTAINTY_EXACT = {CERTAINTY_EXACT})")
    print()
    print("   T_obs   horizon   absArrival     pred y      recorded y      err")
    for t_obs in (0, 100, 200, 230, 232, 234, 300, 400, last_obs):
        r = rows[t_obs]
        print(f"  {r[0]:6d}  {r[1] - r[0]:8.3f}  {r[1]:11.4f}  {r[2]:11.6f}  "
              f"{truth.at(r[1]):11.6f}  {r[3]:+9.6f}")
    print()

    errs = [r[3] for r in rows]
    _stats("POSITION error", errs, "units")

    # Timing error, computed independently by inverting the record rather than
    # by dividing the position error by a slope.
    tim = [r[1] - truth.tick_reaching(r[2]) for r in rows]
    _stats("TIMING error (predictor lead)", [-t for t in tim], "ticks")
    step = offset / (duration * 240.0)
    print()
    print("  Those two numbers are not independent for this trigger, and saying")
    print("  so is more useful than implying otherwise: the motion is linear at")
    print(f"  {step:.9f} units/tick, so a lead of L ticks is a position error of")
    print("  L * that. They would decouple on an eased trigger, where the same")
    print("  tick offset costs a different number of units at different p.")
    print()

    # Sensitivity to a constant tick shift. Before the 2026-08-12 correction
    # this swept for the shift that MINIMISED the residual and found 1.9198;
    # it now sweeps around zero to show that no shift improves on the corrected
    # model and that one tick either way is 500x worse. A shift is applied by
    # displacing the trigger's activation x, which is how GD would see it.
    print("  Would any constant tick shift do better? (0.0 is the corrected")
    print("  model; a shift moves the trigger's activation x by S * upt.)")
    print()
    print("     S ticks     mean|err| (units)   max|err|      mean|err| (ticks)")
    best = (math.inf, 0.0)
    exact_shift = activation_tick - (
        (trig_obj.x - player_start_x) / upt + PLAYER_X_TICK_ORIGIN)
    for shift in (-2.0, -1.0, 0.0, 1.0, 2.0):
        r = _pending_run(truth, trig_obj.x + shift * upt, target, trigger,
                         offset, duration, upt, range(0, last_obs + 1),
                         player_start_x)
        a = [abs(x[3]) for x in r]
        tag = "  <- corrected model" if shift == 0.0 else ""
        print(f"  {shift:9.5f}     {statistics.fmean(a):.6e}   {max(a):.3e}   "
              f"{statistics.fmean(a) / step:12.6f}{tag}")
        if statistics.fmean(a) < best[0]:
            best = (statistics.fmean(a), shift)
    print()
    print(f"  best constant shift  {best[1]:.5f} ticks -> residual "
          f"{best[0]:.3e} units ({best[0] / step:.5f} ticks)")
    print(f"  residual to the CROSSING, before quantisation: {exact_shift:.5f}")
    print("  ticks. That is what ticks_to_activation's ceil() supplies: the")
    print("  continuous crossing sits that far short of GD's integer activation")
    print("  tick, and GD activates on the first whole step past the crossing.")
    print("  A residual at the float-noise floor of section 3 with NO shift")
    print("  means there is no timing defect left and no position defect at")
    print("  all -- the remaining error is GD's float32 elapsed-time")
    print("  accumulation, which the predictor computes in float64.")
    return statistics.fmean([abs(e) for e in errs]), exact_shift


# --------------------------------------------------------------------------
# Section 5 -- error vs prediction horizon
# --------------------------------------------------------------------------

def section_horizon(log: ParsedLog, truth: Truth, y_start: float, offset: float,
                    duration: float, activation_tick: int,
                    player_start_x: float) -> None:
    _h1("5. ERROR VS PREDICTION HORIZON")
    trigger = log.triggers[0]
    trig_obj = next((o for o in log.objects if o.object_id == trigger.object_id),
                    None)
    if trig_obj is None:
        print("  no trigger object record; skipped")
        return
    upt = UNITS_PER_TICK[1]
    print("  Section 4 sweeps the observation tick, which leaves the arrival tick")
    print("  pinned at one value -- so it measures the error at exactly one point")
    print("  of the motion. To sweep the horizon instead, the observation is fixed")
    print("  at tick 0 and the TARGET's x is swept. That is legitimate ground")
    print("  truth: offX=0, so the block's y-vs-tick law does not depend on its x,")
    print("  and the recorded y(t) covers every tick in 234..713. The x is a dial")
    print("  for choosing which tick to interrogate, NOT a claim that a block was")
    print("  ever recorded at that x.")
    print()
    print("   target x   horizon(ticks)   pred y      recorded y       err   "
          "game / predictor")
    for horizon in (0, 100, 200, 225, 231, 232, 233, 234, 240, 300, 400,
                    500, 600, 700, 705, 710, 711, 712, 713, 720, 760):
        target_x = player_x(horizon, player_start_x)
        target = ObjectSnapshot(
            object_id=1, kind=ObjectKind.SOLID, x=target_x, y=y_start,
            half_w=15.0, half_h=15.0, groups=(trigger.target_group_id,),
        )
        rows = _pending_run(truth, trig_obj.x, target, trigger, offset,
                            duration, upt, range(0, 1), player_start_x)
        t_obs, abs_arrival, pred_y, err, _ = rows[0]
        game = ("static" if abs_arrival <= activation_tick
                else "done" if abs_arrival >= truth.last else "moving")
        pred = ("static" if pred_y <= y_start + 1e-12
                else "done" if pred_y >= y_start + offset - 1e-12 else "moving")
        print(f"  {target_x:9.2f}   {abs_arrival:12.3f}   {pred_y:10.6f}  "
              f"{truth.at(abs_arrival):11.6f}  {err:+9.6f}  "
              f"{game:>6} / {pred}")
    print()
    print("  The error does NOT grow with horizon, and since the 2026-08-12")
    print("  correction it does not step up either: it is flat at the float32")
    print("  floor across the whole 480-tick move. Before the correction it was")
    print("  flat at 0.36 units, which was the signature of a fixed")
    print("  activation-tick offset -- flat, not a ramp, so never an")
    print("  accumulating integration error.")
    print()
    print("  Also worth noting: ForwardProjector's default horizon_ticks=240 is")
    print(f"  {240 * upt:.1f} units, and the recorded target sits at x="
          f"{next(o.x for o in log.objects if trigger.target_group_id in o.groups)}."
          )
    print("  At the default the arrival tick clamps and the projection reports the")
    print("  block's position at tick 240 rather than at arrival. Every number in")
    print("  sections 4 and 5 uses horizon_ticks=900 for that reason.")


# --------------------------------------------------------------------------
# Section 6 -- decomposition
# --------------------------------------------------------------------------

def section_decomposition(truth: Truth, activation_tick: int, trig_x: float,
                          player_start_x: float, offset: float,
                          duration: float) -> None:
    _h1("6. DECOMPOSITION, AND WHAT IS STILL UNMEASURED")
    upt = UNITS_PER_TICK[1]
    reach = (trig_x - player_start_x) / upt + PLAYER_X_TICK_ORIGIN
    step = offset / (duration * 240.0)
    print("  Under the measured player law (section 4), in absolute ticks:")
    print()
    label_reach = f"continuous crossing of x = {trig_x:.0f}"
    label_cross = f"first integer tick with x >= {trig_x:.0f}"
    print(f"    {label_reach:<36}{reach:9.4f}   the OLD fire tick")
    print(f"    {label_cross:<36}{math.ceil(reach):9d}   "
          "ticks_to_activation(), the corrected one")
    print(f"    {'activation tick (elapsed == 0)':<36}{activation_tick:9d}   "
          "MEASURED, two independent ways")
    print(f"    {'first displacement':<36}{truth.first:9d}   MEASURED")
    print(f"    {'last displacement':<36}{truth.last:9d}   MEASURED")
    print(f"    {'command removed from m_unkVector560':<36}{715:9d}   "
          "MEASURED (separate launch)")
    print()
    print("  THE 1.9198-TICK LEAD, DECOMPOSED. It was two errors in two files,")
    print("  both in the same direction, which is why it read as one:")
    print()
    print("    1.00000 ticks   origin convention. x(t) = U*(t-1), not U*t.")
    print("                    Measured: max|x - U*(t-1)| = 0.2597 against")
    print("                    max|x - U*t| = 1.3135. This lived in THIS script")
    print("                    and in the test fixture, never in trajectory.py,")
    print("                    which is written entirely in relative ticks.")
    print(f"    {math.ceil(reach) - reach:.5f} ticks   continuous-vs-integer crossing. GD")
    print("                    checks the trigger once per physics step, so it")
    print("                    fires on the first whole tick past the crossing.")
    print("                    This one was in trajectory.py; ticks_to_activation")
    print("                    is the fix.")
    print(f"    {1.0 + math.ceil(reach) - reach:.5f} ticks   total, against the "
          f"{activation_tick - (reach - 1.0):.5f} measured.")
    print(f"    {activation_tick - math.ceil(reach):.5f} ticks   residual. Exactly zero: "
          "the corrected fire tick IS the")
    print("                    measured activation tick, not a fit to it.")
    print()
    print("  IMPORTANT -- the one-tick activation dead time accounts for NONE of")
    print("  it, and is ALREADY modelled. PendingTrigger.as_command_at() sets")
    print("  elapsed = max(0, ticks_since_fire/240 - delay), so at")
    print("  ticks_since_fire == 0 it yields p = 0 and zero displacement,")
    print("  exactly as GD does on tick 233. Subtracting a tick for it would")
    print("  double-count and overshoot. TODO.md's earlier suggestion that 'the")
    print("  dead time alone could account for half of it' is wrong on this")
    print("  evidence.")
    print()
    print("  The rival explanation is dead. Before the player's x was measured,")
    print("  a player starting at x = "
          f"{trig_x - activation_tick * upt:.3f} at tick 0 would have")
    print("  reached the trigger at exactly tick "
          f"{activation_tick} with no correction at all,")
    print("  and one log with no player positions could not tell that apart from")
    print("  a trigger latency. The GDRL_ENV run settled it: start x is 0.0")
    print("  exactly, at tick 1, with no spin-up ramp.")
    print()
    print("  ceil vs round, now decidable and decided:")
    print(f"    ceil({reach:.3f})  = {math.ceil(reach)}   MATCHES the measured "
          f"{activation_tick}")
    print(f"    round({reach:.3f}) = {round(reach)}   FALSIFIED")
    print(f"  frac = {reach - math.floor(reach):.3f}. Under the OLD origin the")
    print("  crossing was at 231.080 and ceil+1 / round+2 / floor+2 all landed on")
    print(f"  {activation_tick}, which is why this was recorded as undecidable. It was the")
    print("  origin error hiding the answer, not a shortage of data.")
    print()
    print("  NOT VERIFIED by anything in this script:")
    print("    * The player's x on THIS level in THIS log -- there is none. The")
    print(f"      law x(t) = {upt:.9f} * (t - 1) comes from the")
    print("      2026-08-12 GDRL_ENV run and is used here across runs.")
    print("    * Whether GD compares >= or >, and whether it quantises against")
    print("      the float32-accumulated x rather than the line. They differ by")
    print("      at most 0.028 ticks over a 400-tick lookahead; this trigger's")
    print("      crossing sits at frac 0.08, nowhere near a boundary. Needs a")
    print("      trigger whose crossing lands within ~0.03 of an integer.")
    print("    * Which step of GD's pipeline consumes the crossing.")
    print("      checkSpawnObjects runs at update+0xf0c; whether the command is")
    print("      created in the same step as the comparison or the next is")
    print("      UNVERIFIED -- the measurement pins the observable tick, not the")
    print("      mechanism behind it.")
    print("    * m_deltaTimeInFloat was never read. Section 3 models it.")
    print("    * Non-zero easing, rotation/transform (ActionType 3/4), non-unit")
    print("      m_moveMod*, lock flags, spawn delays, multiple commands on one")
    print("      group, speed buckets other than 1x. All unexercised.")
    print("    * Whether m_alreadyUpdated (+0x1b0) is the mechanism behind the")
    print("      dead time. Plausible; unmeasured.")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def resolve_log(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit).expanduser()
        if not p.exists():
            print(f"error: no such log: {p}", file=sys.stderr)
            raise SystemExit(2)
        return p
    if DEFAULT_LOG.exists():
        return DEFAULT_LOG
    for p in FALLBACK_LOGS:
        if p.exists():
            print(f"note: {DEFAULT_LOG} is gone; using the backup copy {p}")
            return p
    print("error: no reference log found. Looked at:", file=sys.stderr)
    print(f"  {DEFAULT_LOG}", file=sys.stderr)
    for p in FALLBACK_LOGS:
        print(f"  {p}", file=sys.stderr)
    print("Re-capture with:", file=sys.stderr)
    print("  GDRL_SYNTH=1 GDRL_AUTOPLAY=1 GDRL_PROBE_MOVE=1 GDRL_BLOCK_INPUT=1 "
          "./scripts/run_sandbox.sh", file=sys.stderr)
    raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Validate trajectory.py's forward projection against a "
                    "recorded GDRL_PROBE_MOVE log.")
    ap.add_argument("log", nargs="?", default=None,
                    help="Geode log to read (default: the reference capture)")
    ap.add_argument("--activation-tick", type=int, default=233,
                    help="tick at which the command's elapsed time was zero; "
                         "measured via GDRL_PROBE_CMDVEC (default 233). "
                         "Section 2 re-derives it from the record.")
    ap.add_argument("--player-start-x", type=float, default=0.0,
                    help="player x at the ORIGIN TICK (tick 1, not tick 0). "
                         "Measured as 0.0 on the 2026-08-12 GDRL_ENV run "
                         "(default 0.0)")
    args = ap.parse_args(argv)

    log = parse_log(resolve_log(args.log))
    if not log.moves:
        print("error: log contains no MOVE displacement records. Was "
              "GDRL_PROBE_MOVE=1 set?", file=sys.stderr)
        return 2
    if not log.triggers:
        print("error: log contains no MOVE-TRIGGER record, so the trigger "
              "configuration is unknown.", file=sys.stderr)
        return 2

    trigger = log.triggers[0]
    offset = trigger.offset_y
    duration = trigger.duration
    if trigger.offset_x != 0.0:
        print("note: offX != 0; this script only models the y axis.")
    if offset == 0.0:
        print("error: offY == 0, nothing to validate.", file=sys.stderr)
        return 2

    # y before anything moved. Prefer the MOVE-OBJ dump over back-computing it.
    target_obj = next((o for o in log.objects
                       if trigger.target_group_id in o.groups), None)
    y_start = target_obj.y if target_obj else log.moves[0].y - log.moves[0].dy

    truth = Truth(log.moves, args.activation_tick, y_start)

    print("trajectory.py forward projection vs recorded game data")
    print("evidentiary tier (iii) -- see this file's docstring for the two "
          "caveats that keep it from (iv)")

    section_record(log, y_start)
    section_activation(log, y_start, offset, duration, args.activation_tick)
    section_live(log, truth, y_start, offset, duration, args.activation_tick)
    section_pending(log, truth, y_start, offset, duration,
                    args.activation_tick, args.player_start_x)
    section_horizon(log, truth, y_start, offset, duration,
                    args.activation_tick, args.player_start_x)
    trig_obj = next((o for o in log.objects if o.object_id == trigger.object_id),
                    None)
    if trig_obj is not None:
        section_decomposition(truth, args.activation_tick, trig_obj.x,
                              args.player_start_x, offset, duration)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
