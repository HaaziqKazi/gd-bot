// GDRL environment transport -- public surface.
//
// Almost everything lives in telemetry.cpp's anonymous namespace; this header
// exists so other translation units can ask whether the environment channel is
// live and read the per-attempt counters, without any of them being able to
// write into the channel. There is exactly one writer by construction.
#pragma once

#include <cstdint>

namespace gdrl {

// True only when GDRL_ENV=1 AND the shared-memory segment was created. Both,
// deliberately: "the switch is on" and "the transport exists" are different
// claims, and a run that silently degraded from the second to the first would
// look identical to a policy that never acted.
bool envActive();

// True while Python has explicitly attached. The mod never blocks the game
// thread unless this is true, so a GD launched with GDRL_ENV=1 and no Python
// on the other end runs at full speed instead of hanging.
bool envAttached();

// Per-attempt counters, surfaced on the ENV log line at every attempt boundary.
// timeouts is the load-bearing one: a bounded wait that expired and fell back
// to "no input" is indistinguishable from a policy that chose not to jump, so
// it is counted and printed rather than absorbed.
extern long g_gdrlEnvSteps;      // observations published this attempt
extern long g_gdrlEnvTimeouts;   // action waits that expired this attempt
extern long g_gdrlEnvProtoErr;   // action blocks refused (stale/mismatched seq)

}  // namespace gdrl
