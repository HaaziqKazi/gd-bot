"""Minimal env client for the observer-passivity check.

Attaches to the mod's shared segment and answers every step with no action, so
the game runs its own GDRL_INJECT_SEQ trajectory while the full telemetry path
is exercised. The question this answers is narrow and important:

    does observing the game change what the game does?

GameObject caches its collision rect behind a dirty flag, so a telemetry pass
that calls getObjectRect() could plausibly change when GD recomputes it. If it
does, every determinism result gathered without telemetry stops applying the
moment telemetry is switched on -- and the failure would be silent, because the
numbers would still be self-consistent.

Running with no client attached is NOT a substitute: the mod times out after
GDRL_ENV_WAIT_US and resumes free-running, and an attempt with timeouts>0 is not
evidence by the harness's own admissibility rule.

Usage (from the repo root, alongside a GDRL_ENV=1 game):
    python3 trainer/passive_responder.py [seconds]
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from env import GdrlEnv  # noqa: E402


def main() -> int:
    budget = float(sys.argv[1]) if len(sys.argv) > 1 else 120.0
    deadline = time.time() + budget

    try:
        env = GdrlEnv(timeout=60.0)
    except Exception as exc:
        print(f"[responder] could not attach: {exc}", flush=True)
        return 1

    steps = 0
    attempts = set()
    try:
        with env:
            env.reset(timeout=10.0)
            while time.time() < deadline:
                try:
                    obs = env.step()
                except Exception as exc:
                    # The game exiting mid-wait is the normal way this ends.
                    print(f"[responder] step ended: {type(exc).__name__}: {exc}",
                          flush=True)
                    break
                steps += 1
                try:
                    attempts.add(obs.attempt)
                except Exception:
                    pass
                if steps % 2000 == 0:
                    print(f"[responder] steps={steps} attempts={len(attempts)}",
                          flush=True)
    finally:
        ch = getattr(env, "channel", None)
        print(f"[responder] served={steps} attempts={len(attempts)} "
              f"timeouts={getattr(ch, 'timeouts', '?')} "
              f"protoErrors={getattr(ch, 'protocol_errors', '?')}",
              flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
