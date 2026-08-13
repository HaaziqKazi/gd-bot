#pragma once

#include <Geode/Geode.hpp>

// Synthetic test levels.
//
// The main levels cannot serve the remaining trigger measurements.
//
// The justification that used to stand here -- "censusing all 21 main levels
// found exactly four move triggers, all in Fingerdash at x=7813-8455" -- is
// FALSIFIED and has been removed. The census walks layer->m_sections, and
// triggers are not collision geometry and are not reliably bucketed into it:
// it reported 4 move triggers across all 21 levels while Fingerdash's level
// string alone contains 177, a 44x undercount, with the x-range wrong too
// (real range x = 1..24993). Separately, `CENSUS lvl=0` is the only census line
// in all 52 surviving logs, so the 21-level sweep is not reproducible from the
// evidence that exists. Nothing about trigger counts in the main levels should
// be inferred from the census.
//
// What survives as a reason to generate content, on evidence that does not
// depend on the census:
//   - All 177 of Fingerdash's move triggers carry key 36=1, read off the
//     decompressed level string by level_dump.cpp -- an instrument that reads
//     the authored bytes and so cannot miss a trigger the way the census does.
//     Key 36 is believed to mean touch-triggered from community tables and that
//     reading is UNVERIFIED (TODO 1.1b: check it against
//     EffectGameObject::m_isTouchTriggered), but the count itself is measured.
//   - Level 1's vehicle portals sit at x=7995 and x=12555 against a best
//     input-sequence reach of x=3959, and level 1 has no speed portals. Those
//     x values came from the census, so treat them as approximate: the census
//     is a valid instrument for collision-ish geometry and is not known to be
//     complete for portals.
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
//
// X constants are in world units and need no conversion: measured runtime
// m_positionX equals the level-string x exactly (OBJLIST-ID firstX, and the
// move trigger at x=300 activating at tick 233 = 1 + ceil(300 / 1.298250437)).
//
// Y IS NOT THE SAME. Runtime m_positionY = level-string y + 90, measured 4-for-4
// across four distinct values on 2026-08-12 (585->675, 345->435, 105->195,
// 45->135). Everything below is authored in RUNTIME y and converted on the way
// into the string; nothing in this file should ever write a y into the level
// string without going through that conversion.
namespace synth {
    constexpr float kMoveTriggerX = 300.f;   // plain, x-crossing activated
    constexpr float kMovedBlockX  = 600.f;   // member of group 1
    constexpr float kMoveDuration = 2.0f;    // seconds

    // The measured level-string -> runtime y offset. See above.
    constexpr float kLevelStringYOffset = 90.f;

    // The player's measured runtime y: constant 105 over all 4,653 ticks of a
    // null-input run to level completion, m_isOnGround for 4,652 of them, never
    // jumped. An object a portal is meant to catch must be centred here: cube
    // half-height 15 puts the player rect at y in [90,120], so a portal centred
    // at runtime 195 (which is what kGroundY=105 used to produce) misses by 90
    // units and can only overlap if it is more than 150 units tall. That is why
    // every synth portal had never fired.
    constexpr float kPlayerRuntimeY = 105.f;

    constexpr float kSpeed2xX     = 1200.f;
    constexpr float kSpeed3xX     = 1800.f;
    constexpr float kSpeed4xX     = 2400.f;
    constexpr float kSpeedHalfX   = 3000.f;
    constexpr float kSpeed1xX     = 3600.f;

    constexpr float kShipPortalX  = 4500.f;
    constexpr float kEndBlockX    = 6000.f;

    // Runtime y of every authored object -- i.e. what a probe reading
    // m_positionY should see. syntheticLevelString() subtracts
    // kLevelStringYOffset from each of these before writing the string.
    //
    // Only kPortalRuntimeY changed on 2026-08-12 (195 -> 105). The other three
    // are the values the previous build already produced at runtime, restated
    // here in the units they were always in; the level string they generate is
    // byte-identical to before. The move trigger's y in particular MUST stay at
    // 135: its x-crossing activation at tick 233 is the one end-to-end
    // measurement that currently works, and forward projection's ground truth
    // is recorded against that run.
    constexpr float kPortalRuntimeY      = kPlayerRuntimeY;  // 105: speed + vehicle portals
    constexpr float kMoveTriggerRuntimeY = 135.f;            // was kGroundY - 60
    constexpr float kMovedBlockRuntimeY  = 435.f;            // was kGroundY + 240
    constexpr float kHighBlockRuntimeY   = 675.f;            // was kGroundY + 480
    constexpr float kEndBlockRuntimeY    = 435.f;            // was kGroundY + 240

    // Level-string y for a given runtime y. The inverse of the measured
    // load-time offset.
    constexpr float levelStringY(float runtimeY) {
        return runtimeY - kLevelStringYOffset;
    }
}

// Builds the raw (uncompressed) level string. Exposed for logging and tests.
gd::string syntheticLevelString();

// A ready-to-play level, or nullptr if construction failed.
GJGameLevel* makeSyntheticLevel();

}
