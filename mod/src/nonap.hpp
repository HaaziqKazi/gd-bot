#pragma once

// App Nap suppression.
//
// macOS throttles a backgrounded, non-visible app hard: an unfocused GD drops to
// roughly 1 fps and the player stops moving entirely. That made three separate
// measurement runs here produce empty or misleading data, and it is why runs so
// far have had to steal the screen via osascript.
//
// Input already goes in through queueButton rather than the keyboard, and
// GDRL_PIN_LEVEL suppresses GD's own pause-on-unfocus, so focus buys nothing
// else. Removing this last dependency is what makes unattended runs -- and
// eventually parallel instances -- possible.
//
// Deliberately opt-in via GDRL_NO_APP_NAP=1. Holding a user-initiated activity
// also inhibits idle system sleep, which should not happen to someone who merely
// launched the game to play it.

namespace gdrl {

// Idempotent. Returns true if an activity token is held on return.
bool disableAppNap(const char* reason);

// True if suppression is currently active.
bool appNapSuppressed();

}
