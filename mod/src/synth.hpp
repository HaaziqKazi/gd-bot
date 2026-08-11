#pragma once

#include <Geode/Geode.hpp>

// Synthetic test levels.
//
// The main levels cannot serve the remaining trigger measurements. Censusing all
// 21 found exactly four move triggers, all in Fingerdash at x=7813-8455 and all
// touch-triggered; level 1's vehicle portals sit at x=7995 and x=12555 against a
// best input-sequence reach of x=3959, and level 1 has no speed portals at all.
//
// So the content gets generated instead. A level string is built in memory and
// handed to PlayLayer, which needs no editor, no save-file surgery, and no hand
// authoring -- and stays reproducible from the repo, which a hand-built level
// would not.
//
// The layout deliberately contains no hazards: with no input the player runs to
// the end, crossing every portal and trigger on the way. That makes one run
// enough to observe all of them, and means a death is a signal that something is
// wrong rather than the expected outcome.
//
//   GDRL_SYNTH=1            load the synthetic level instead of main level 1
//   GDRL_SYNTH_COMPRESS=1   gzip+base64 the level string before handing it over
//
// The compression switch exists because whether PlayLayer wants the raw object
// string or the compressed form is not documented anywhere we can check, and
// guessing wrong produces a level that loads and runs while being empty -- a
// failure this repo has already paid for once.

namespace gdrl {

// Layout constants, exposed so probes can assert against them rather than
// against numbers copied into a second place.
namespace synth {
    constexpr float kMoveTriggerX = 300.f;   // plain, x-crossing activated
    constexpr float kMovedBlockX  = 600.f;   // member of group 1
    constexpr float kMoveDuration = 2.0f;    // seconds

    constexpr float kSpeed2xX     = 1200.f;
    constexpr float kSpeed3xX     = 1800.f;
    constexpr float kSpeed4xX     = 2400.f;
    constexpr float kSpeedHalfX   = 3000.f;
    constexpr float kSpeed1xX     = 3600.f;

    constexpr float kShipPortalX  = 4500.f;
    constexpr float kEndBlockX    = 6000.f;
}

// Builds the raw (uncompressed) level string. Exposed for logging and tests.
gd::string syntheticLevelString();

// A ready-to-play level, or nullptr if construction failed.
GJGameLevel* makeSyntheticLevel();

}
