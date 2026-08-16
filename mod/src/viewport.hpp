#pragma once

// The camera viewport in world units, shared between the diagnostic probe
// (viewport.cpp, GDRL_PROBE_VIEWPORT) and the observation path
// (telemetry.cpp, scanObjects). One implementation, so the sensor the agent
// gets and the number the probe reports cannot drift apart.
//
// Why this is a function of live camera state rather than three constants:
// measured 2026-08-15 (backups/reference-logs/viewport-probe-20260815-171406.log,
// Stereo Madness, 1x, zoom 1.0) the screen shows 569.0 x 320.0 world units --
// 359.5 ahead of the player and 209.5 behind, +215 up and -105 down. The old
// GDRL_ENV_WIN_* constants advertised 1400 / 400 / +-600. A constant cannot
// track camera zoom, and under kResolutionFixedHeight the visible width is a
// function of the window's aspect ratio, so a constant is not merely imprecise
// there -- it is undefined until the launch config is pinned.
//
// STATE NEUTRALITY. Deriving the rect needs the object layer's world
// transform, and cocos's CCNode::nodeToWorldTransform() RECOMPUTES and CACHES
// each ancestor's matrix, clearing its dirty flag. On a probe that was an
// argued-safe side effect; on the observation path it would run every physics
// step of every benchmark run, which is not a place for an argument. So
// cameraWorldRect() snapshots every CCNode cache field it could possibly
// write, on every node of the chain, and restores them afterwards -- making
// the call a pure read by construction rather than by reasoning. See
// viewport.cpp for the field list and for GDRL_VERIFY_XFORM, which checks the
// restore against the node's raw bytes.

#include <Geode/Geode.hpp>

class GJBaseGameLayer;

namespace gdrl {

// The visible world rect at the moment of the call, plus the raw camera state
// it was derived alongside, so a consumer can log or cross-check rather than
// take the rect on trust.
struct CameraRect {
    // false means the camera could not be read (no object layer, no GL view,
    // singular transform). A consumer must then claim NOTHING -- not fall back
    // to a guess. telemetry.cpp treats it exactly like an unusable
    // m_sectionXFactor: zero-area window, OBJECTS_UNAVAILABLE.
    bool   valid     = false;

    double left      = 0.0;
    double right     = 0.0;
    double bottom    = 0.0;
    double top       = 0.0;

    // Raw camera state, for logging and cross-checks. Not inputs to the rect.
    float  zoom      = 0.f;   // GJGameState::m_cameraZoom
    float  angle     = 0.f;   // GJGameState::m_cameraAngle
    float  camWidth  = 0.f;   // GJBaseGameLayer::m_cameraWidth
    float  camHeight = 0.f;   // GJBaseGameLayer::m_cameraHeight

    // The camera is rotated, so [left,right]x[bottom,top] is the axis-aligned
    // BOUNDING BOX of the visible region and OVERSTATES it. Never observed
    // nonzero in any run so far; carried so that a consumer can refuse or flag
    // rather than silently widen its sensor.
    bool   rotated   = false;

    // The derived size disagrees with GD's own m_cameraWidth / m_cameraHeight
    // by more than kSizeCheckTolerance. Measured agreement on 6146/6146
    // samples of the 2026-08-15 log, so this firing is news -- most likely
    // that m_cameraWidth stops tracking under a zoom this repo has never
    // observed. The rect still comes from the transform, which is correct
    // under zoom by construction; the flag exists so the disagreement is
    // reported instead of absorbed.
    bool   sizeMismatch = false;

    double width()  const { return right - left; }
    double height() const { return top - bottom; }
};

// Units of disagreement between the transform-derived size and
// m_cameraWidth/m_cameraHeight that count as a mismatch. Well above the float
// transform noise floor (~1e-4 relative on a 569-unit width), well below any
// real zoom step.
inline constexpr double kSizeCheckTolerance = 0.05;

// Read the camera and return the visible world rect. Safe to call with a null
// layer (returns valid=false). Does not mutate engine state -- see the header
// comment above and the field list in viewport.cpp.
CameraRect cameraWorldRect(GJBaseGameLayer* layer);

}   // namespace gdrl
