#!/usr/bin/env python3
"""TODO.md 2.1's own acceptance test: is checkpoint restore deterministic?

RUN LIVE ONLY VIA scripts/practice_determinism.sh. This script assumes GD is
already running with GDRL_ENV=1 GDRL_PRACTICE=1 GDRL_BLOCK_INPUT=1
GDRL_PIN_LEVEL=1 GDRL_AUTOPLAY=1 and refuses to proceed past the first
observation if header.practiceMode == 0 -- see that script for why every
switch is set inline, on one command line, by the wrapper, not here.

WHAT THIS MEASURES, mapped onto TODO.md 2.1's own four checklist items
-------------------------------------------------------------------------
mod/src/telemetry.cpp's GDRL_PRACTICE block (search "WHAT IS STILL
UNVERIFIED, LOUDLY") states plainly: restore determinism past the mod's own
force-set of m_attemptTime is NOT established, and names m_attemptTime,
m_extraDelta, the RNG seeds, m_queuedButtons and the section grid as
suspected gaps in what CheckpointObject actually snapshots. This script is
the "restore the same checkpoint N times ... require bit-identical outcomes"
test TODO.md 2.1 says decides whether the whole idea survives.

  1. "createCheckpoint/loadFromCheckpoint actually called" -- not
     re-measured here; that is disassembly evidence already in telemetry.
     cpp's own comment (bl=3/b=0 for both). What THIS script adds is
     behavioural: if calling them did nothing, the player would not visibly
     return to the checkpoint state, which VERDICT below would catch as an
     immediate, large divergence between legs.
  2. "restore is deterministic" -- THE primary measurement. See
     compare_trajectories() and phase_a.bit_identical_across_all_legs.
  3. "m_attemptTime survives a restore" -- checked directly: header.
     attemptTime on the first observation after every restore is compared,
     with ==, against the attemptTime recorded at CHECKPOINT_SAVE time.
  4. "m_randomSeed / m_replayRandSeed behaviour" -- NOT directly observable;
     no wire field exposes either seed (the closest thing on the wire is
     GdrlObject.targetIsRemapped, a derived bool that says a spawn used
     m_randomSeed, not the seed's value). If an RNG divergence affected
     player physics or a trigger this wire format reports, it would surface
     as a trajectory divergence in phase_a. If it diverges somewhere this
     wire format does not report, this script cannot see it -- said here
     rather than silently claimed covered.

METHOD
-------------------------------------------------------------------------
Two independent restore code paths exist in the mod (see telemetry.cpp's
CHECKPOINT_RESTORE handler and the resetLevel() hook's own comment on why it
does NOT call loadFromCheckpoint itself) and both are exercised:

  PHASE A -- DEATH-TRIGGERED, the one the throughput story in telemetry.
  cpp's own GDRL_PRACTICE comment is about (237/254 attempts dying at the
  same wall, each paying for a full tick-0 replay). Play a short,
  level-position-agnostic prefix of pure NOOP from tick 0 to --pre-ticks,
  CHECKPOINT_SAVE, then keep playing pure NOOP -- no jump, ever -- until the
  player dies against whatever is there. GD's own resetLevel() is
  disassembled (telemetry.cpp, "A FURTHER RESULT") calling
  loadFromCheckpoint itself on death when a checkpoint exists; the mod's
  resetLevel() hook does not call it again, only PREDICTS the restore
  happened and force-sets m_attemptTime. This produces --restores + 1 death
  cycles ("legs"), each replaying the identical NOOP policy from the
  (supposedly) restored position; they must die at the same tick, at the
  same x, with the same intervening trajectory, or the acceptance test
  fails.

  PHASE B -- ON-DEMAND (CHECKPOINT_RESTORE, "independent of dying"). After
  phase A, ask for one explicit mid-attempt restore with no death involved,
  and check the same invariants over a short trailing window, compared
  against phase A's own baseline leg. This exercises the OTHER call site
  (getLastCheckpoint()+loadFromCheckpoint() inside consumeActions, not
  inside resetLevel()) -- a genuinely different code path from phase A.

NOOP-only is deliberate, not a limitation: "the same subsequent inputs" is
satisfied trivially and does not require knowing where Stereo Madness's
hazards are, which keeps this script honest about not assuming a solved
level. Death is not a bug here -- it is the stimulus needed to reach the
death-triggered restore path at all.

EVIDENCE TIER: (i) before this script has ever been run against the game.
After one live run it is tier (iii) for the EXACT level/prefix/--pre-ticks/
--restores combination that run used -- a recorded comparison against the
live game, not a reimplementation -- and nothing broader. It proves nothing
about any other prefix, level, or action pattern; TODO.md 2.1 is not closed
by one run with default arguments, only by reading what that run actually
measured.

Run (via the wrapper, which launches GD first):
    ./scripts/practice_determinism.sh
Run directly against an already-launched, already-configured GD (advanced
use; the wrapper is the supported entry point):
    python3 trainer/practice_determinism.py --json /tmp/pd.json --jsonl /tmp/pd.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import schema_generated as sg
from env import Action, Channel, HandshakeError, wait_for_shared


class AbortRun(RuntimeError):
    """A precondition failed hard enough that continuing would prove nothing."""


@dataclass
class Step:
    """One observed physics step -- only the fields "bit-identical outcome"
    is checked against, not the full Observation.

    Every field here is compared with ==, never a tolerance: a bit-identical
    claim that used np.isclose would not be the claim TODO.md 2.1 asks for.
    """
    tick: int
    attempt: int
    x: float
    y: float
    y_velocity: float
    rotation: float
    is_on_ground: int
    attempt_time: float
    resume_tick: int
    checkpoint_tick: int
    has_checkpoint: bool
    practice_mode: bool
    status: str


def _step_from_obs(obs) -> Step:
    p0 = obs.record["players"][0]
    return Step(
        tick=obs.tick,
        attempt=obs.attempt,
        x=float(obs.header["playerX"]),
        y=float(obs.header["playerY"]),
        y_velocity=float(p0["yVelocity"]),
        rotation=float(p0["rotation"]),
        is_on_ground=int(p0["isOnGround"]),
        attempt_time=obs.attempt_time,
        resume_tick=obs.resume_tick,
        checkpoint_tick=obs.checkpoint_tick,
        has_checkpoint=obs.has_checkpoint,
        practice_mode=obs.practice_mode,
        status=obs.status.name,
    )


def _fields_match(a: Step, b: Step) -> bool:
    return (
        a.x == b.x and a.y == b.y and a.y_velocity == b.y_velocity
        and a.rotation == b.rotation and a.is_on_ground == b.is_on_ground
        and a.attempt_time == b.attempt_time
    )


def compare_trajectories(baseline: list[Step], other: list[Step]) -> dict:
    """Tick-for-tick comparison, position error and timing error kept SEPARATE.

    This repo's own rule: a one-tick offset and a genuine position error are
    different defects and must be reported as two different numbers, not
    folded into one pass/fail. ``first_divergence`` distinguishes a
    "tick_offset" (the two legs' clocks disagree at the same list index --
    a pacing/stride defect, not necessarily physics) from a
    "state_divergence" (same tick, some other field disagrees).
    """
    n = min(len(baseline), len(other))
    first_divergence = None
    max_pos_err = 0.0
    for i in range(n):
        b, o = baseline[i], other[i]
        if b.tick != o.tick:
            first_divergence = {
                "index": i, "kind": "tick_offset",
                "baseline_tick": b.tick, "other_tick": o.tick,
            }
            break
        pos_err = ((b.x - o.x) ** 2 + (b.y - o.y) ** 2) ** 0.5
        if not _fields_match(b, o):
            # pos_err at the DIVERGENT point itself goes in first_divergence,
            # not into max_pos_err below -- max_pos_err is explicitly "before
            # divergence" (the agreeing prefix only), so a large jump at the
            # divergence point cannot masquerade as gradual pre-existing
            # drift.
            first_divergence = {
                "index": i, "kind": "state_divergence", "tick": b.tick,
                "baseline": asdict(b), "other": asdict(o),
                "position_error_units": pos_err,
                "timing_error_ticks": 0,
            }
            break
        max_pos_err = max(max_pos_err, pos_err)
    length_mismatch = len(baseline) != len(other)
    return {
        "bit_identical": first_divergence is None and not length_mismatch,
        "first_divergence": first_divergence,
        "length_mismatch": length_mismatch,
        "baseline_len": len(baseline),
        "other_len": len(other),
        "max_position_error_units_before_divergence": max_pos_err,
    }


# ---------------------------------------------------------------------------
# Driving the game
# ---------------------------------------------------------------------------

def _step(chan: Channel, timeout: float, log) -> object:
    chan.respond([])
    obs = chan.poll(timeout=timeout)
    log(obs)
    return obs


def play_prefix(chan: Channel, obs, target_tick: int, timeout: float, log):
    """Pure NOOP from wherever we are up to (at least) target_tick."""
    while obs.tick < target_tick:
        obs = _step(chan, timeout, log)
    return obs


def save_checkpoint(chan: Channel, obs, timeout: float, log):
    errs_before = chan.protocol_errors
    chan.respond([Action(target_tick=0, kind=sg.GdrlActionKind.CHECKPOINT_SAVE)])
    obs = chan.poll(timeout=timeout)
    log(obs)
    if chan.protocol_errors != errs_before:
        raise AbortRun(
            f"protocolErrors moved ({errs_before} -> {chan.protocol_errors}) "
            "immediately after CHECKPOINT_SAVE -- refused. Check the GD log "
            "for '[gdrl] ENV CHECKPOINT_SAVE requested but GDRL_PRACTICE is "
            "not set'."
        )
    if not obs.has_checkpoint:
        raise AbortRun(
            "CHECKPOINT_SAVE produced no protocol error but "
            "header.hasCheckpoint is still 0 -- the mod's own live mirror "
            "disagrees with itself. Not a state this script can proceed "
            "from meaningfully."
        )
    return obs


def play_until_new_attempt(chan: Channel, obs, start_attempt: int,
                            budget_ticks: int, timeout: float, log):
    """Pure NOOP until obs.attempt changes (death -> resetLevel()) or budget runs out.

    Returns (trajectory, obs_after, reason); reason is "new_attempt" or
    "budget_exhausted".
    """
    traj = [_step_from_obs(obs)]
    start_tick = obs.tick
    while True:
        obs = _step(chan, timeout, log)
        if obs.attempt != start_attempt:
            traj.append(_step_from_obs(obs))
            return traj, obs, "new_attempt"
        traj.append(_step_from_obs(obs))
        if obs.tick - start_tick > budget_ticks:
            return traj, obs, "budget_exhausted"


def play_noop_ticks(chan: Channel, obs, n_ticks: int, timeout: float, log):
    traj = [_step_from_obs(obs)]
    target = obs.tick + n_ticks
    while obs.tick < target:
        obs = _step(chan, timeout, log)
        traj.append(_step_from_obs(obs))
    return traj, obs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="TODO.md 2.1's live acceptance test: is checkpoint "
                     "restore deterministic?",
        epilog="Run via scripts/practice_determinism.sh, which launches GD "
               "with every required GDRL_* switch inline. Do not launch GD "
               "yourself and call this directly unless you have already "
               "set GDRL_ENV=1 GDRL_PRACTICE=1 GDRL_BLOCK_INPUT=1 "
               "GDRL_PIN_LEVEL=1.")
    ap.add_argument("--restores", type=int, default=4,
                    help="additional death-triggered restore cycles after "
                         "the checkpoint's first death; total legs compared "
                         "= restores + 1")
    ap.add_argument("--pre-ticks", type=int, default=40,
                    help="ticks of pure NOOP played before CHECKPOINT_SAVE. "
                         "Small and level-position-agnostic on purpose (see "
                         "module docstring) -- not tuned to any known "
                         "obstacle")
    ap.add_argument("--attempt-budget-ticks", type=int, default=2000,
                    help="give up on one death/restore leg after this many "
                         "ticks of NOOP with no attempt-counter change (the "
                         "prefix survived longer than expected -- raise "
                         "this or lower --pre-ticks)")
    ap.add_argument("--ondemand-ticks", type=int, default=40,
                    help="ticks of NOOP played after phase B's on-demand "
                         "CHECKPOINT_RESTORE, compared against phase A's "
                         "baseline leg")
    ap.add_argument("--timeout", type=float, default=20.0,
                    help="seconds to wait for one observation")
    ap.add_argument("--shm", default="gdrl.env", help="shared segment name")
    ap.add_argument("--json", default=None,
                    help="write the full report here at the end of the run")
    ap.add_argument("--jsonl", default=None,
                    help="append EVERY observed step here as it happens, "
                         "flushed and fsynced immediately -- durable even if "
                         "this process is killed mid-run")
    args = ap.parse_args(argv)

    jsonl_fh = open(args.jsonl, "a", buffering=1) if args.jsonl else None

    def log_obs(obs) -> None:
        if jsonl_fh is None:
            return
        jsonl_fh.write(json.dumps(asdict(_step_from_obs(obs))) + "\n")
        jsonl_fh.flush()
        os.fsync(jsonl_fh.fileno())

    try:
        buf = wait_for_shared(args.shm, timeout=30.0)
    except HandshakeError as exc:
        print(f"never found a live game to attach to: {exc}", file=sys.stderr)
        return 2

    chan = Channel(buf)
    try:
        chan.attach()
    except HandshakeError as exc:
        print(f"attach failed, nothing was played: {exc}", file=sys.stderr)
        buf.close()
        return 2

    report: dict = {"phase_a": None, "phase_b": None, "fatal": None}
    try:
        obs = chan.poll(timeout=args.timeout)
        log_obs(obs)

        if not obs.practice_mode:
            raise AbortRun(
                "header.practiceMode == 0 -- GDRL_PRACTICE was not set for "
                "this run (or this process attached before the mod's "
                "practiceMode field was populated). This script proves "
                "nothing without it. Re-run through "
                "scripts/practice_determinism.sh, which sets GDRL_PRACTICE=1 "
                "inline on the launch command."
            )

        # ---- prefix -------------------------------------------------------
        obs = play_prefix(chan, obs, args.pre_ticks, args.timeout, log_obs)

        # ---- CHECKPOINT_SAVE ------------------------------------------------
        obs = save_checkpoint(chan, obs, args.timeout, log_obs)
        checkpoint_tick = obs.checkpoint_tick
        checkpoint_attempt_time = obs.attempt_time

        # ---- Phase A: death-triggered restore, repeated ---------------------
        legs = []
        cur_obs = obs
        cur_attempt = obs.attempt
        for leg in range(args.restores + 1):
            traj, cur_obs, reason = play_until_new_attempt(
                chan, cur_obs, cur_attempt, args.attempt_budget_ticks,
                args.timeout, log_obs)
            legs.append({"leg": leg, "reason": reason, "steps": traj})
            if reason != "new_attempt":
                raise AbortRun(
                    f"phase A leg {leg}: no new attempt within "
                    f"{args.attempt_budget_ticks} ticks of NOOP play -- "
                    "either the prefix survives longer than this script's "
                    "budget (raise --attempt-budget-ticks) or death did not "
                    "trigger a resetLevel()."
                )
            cur_attempt = cur_obs.attempt

        baseline = legs[0]["steps"]
        comparisons = [
            {"leg": other["leg"], **compare_trajectories(baseline, other["steps"])}
            for other in legs[1:]
        ]
        attempt_time_after = [leg["steps"][-1].attempt_time for leg in legs[1:]]
        resume_tick_after = [leg["steps"][-1].resume_tick for leg in legs[1:]]

        report["phase_a"] = {
            "checkpoint_tick": checkpoint_tick,
            "checkpoint_attempt_time": checkpoint_attempt_time,
            "legs": len(legs),
            "reasons": [leg["reason"] for leg in legs],
            "comparisons": comparisons,
            "bit_identical_across_all_legs": all(c["bit_identical"] for c in comparisons),
            "attempt_time_survives_restore": all(
                t == checkpoint_attempt_time for t in attempt_time_after),
            "attempt_time_after_restore": attempt_time_after,
            "resume_tick_equals_checkpoint_tick": all(
                rt == checkpoint_tick for rt in resume_tick_after),
            "resume_tick_after_restore": resume_tick_after,
            "full_attempts_after_run": cur_obs.full_attempts,
            "practice_attempts_after_run": cur_obs.practice_attempts,
            # Exactly one full (tick-0) attempt the whole run -- the level's
            # initial load, before any checkpoint existed -- and exactly one
            # practiceAttempts increment per leg's death (restores + 1 of
            # them: leg 0's death is the FIRST restore, ..., the last leg's
            # death is the (restores+1)-th). See the module docstring's
            # attempt-numbering walkthrough.
            "attempt_accounting_ok": (
                cur_obs.full_attempts == 1
                and cur_obs.practice_attempts == args.restores + 1
            ),
        }

        # ---- Phase B: on-demand restore, no death ----------------------------
        errs_before = chan.protocol_errors
        chan.respond([Action(target_tick=0, kind=sg.GdrlActionKind.CHECKPOINT_RESTORE)])
        obs = chan.poll(timeout=args.timeout)
        log_obs(obs)
        phase_b: dict = {"refused": chan.protocol_errors != errs_before}
        if not phase_b["refused"]:
            phase_b["tick_after_restore"] = obs.tick
            phase_b["landed_on_checkpoint_tick"] = obs.tick == checkpoint_tick
            phase_b["attempt_time_after_restore"] = obs.attempt_time
            phase_b["attempt_time_ok"] = obs.attempt_time == checkpoint_attempt_time
            traj_b, obs = play_noop_ticks(chan, obs, args.ondemand_ticks,
                                          args.timeout, log_obs)
            phase_b["comparison_vs_phase_a_baseline"] = compare_trajectories(
                baseline[:len(traj_b)], traj_b)
        report["phase_b"] = phase_b

    except (AbortRun, TimeoutError, HandshakeError) as exc:
        report["fatal"] = f"{type(exc).__name__}: {exc}"
    finally:
        chan.detach()
        buf.close()
        if jsonl_fh is not None:
            jsonl_fh.close()

    phase_a = report["phase_a"]
    verdict_pass = bool(
        report["fatal"] is None
        and phase_a is not None
        and phase_a["bit_identical_across_all_legs"]
        and phase_a["attempt_time_survives_restore"]
        and phase_a["resume_tick_equals_checkpoint_tick"]
        and phase_a["attempt_accounting_ok"]
        and not (report["phase_b"] or {}).get("refused", True)
        and (report["phase_b"] or {}).get("comparison_vs_phase_a_baseline", {}).get("bit_identical", False)
    )
    report["verdict"] = "PASS" if verdict_pass else "FAIL"

    print(json.dumps(report, indent=2, default=str))
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=2, default=str)
        print(f"\nfull report written to {args.json}")
    if report["fatal"]:
        print(f"\nFATAL: {report['fatal']}", file=sys.stderr)
    print(f"\nVERDICT: {report['verdict']} -- tier (iii) for THIS run's exact "
          f"arguments only (pre_ticks={args.pre_ticks}, restores={args.restores}). "
          "See the module docstring before generalising this result.")
    return 0 if verdict_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
