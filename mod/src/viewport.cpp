// ===========================================================================
// Probe V: GDRL_PROBE_VIEWPORT -- what is actually on screen, in world units?
// ===========================================================================
//
// WHY THIS EXISTS
//
// The project targets Benchmark A (true sightreading), so the observation
// window IS the benchmark definition, not a performance knob. Ours is
// GDRL_ENV_WIN_BEHIND=400 / _AHEAD=1400 / _VERT=600 (telemetry.cpp:184-186),
// and those three numbers were picked for convenience before Benchmark A
// existed. At 1x the player covers ~311.6 units/sec, so 1400 ahead is ~4.5
// seconds of lookahead, which is plausibly far more than the screen shows.
//
// This file does not answer that question. It MEASURES the camera viewport in
// world units per render frame and logs it; the answer comes from the run.
// Nothing here is derived from a constant looked up anywhere -- every number
// on the VIEWPORT line is read out of live camera/director state at the moment
// it is emitted.
//
// Default OFF, like every other GDRL_* switch. With GDRL_PROBE_VIEWPORT unset
// both hooks below delegate to the original before doing anything at all, so
// the behaviour is byte-identical to an unmodded run (README, "Defaults are
// clean").
//
// ---------------------------------------------------------------------------
// HOW THE VIEWPORT IS DERIVED, AND WHY THIS WAY
//
// The trustworthy definition of "on screen" is the one the renderer uses: a
// GameObject at world (x, y) is drawn at screen point
//
//     screen = nodeToWorldTransform(m_objectLayer) * (x, y)
//
// because objects live in m_objectLayer's coordinate space and cocos composes
// every ancestor transform into that matrix. So the visible world rect is the
// INVERSE map of the visible screen rect. That inverts the whole camera --
// pan, zoom, rotation, and any ancestor scaling -- in one step, without this
// file having to know how GD implements any of them. Everything else below is
// a cross-check on that primary number, not an input to it.
//
// ZOOM IS THE TRAP the task called out, and it is handled structurally rather
// than by remembering to multiply: zoom reaches this file only through the
// transform (as m_objectLayer's scale), so a zoom change cannot be silently
// dropped. m_gameState.m_cameraZoom and the layer's own scale are ALSO emitted
// on every line, so a run can see whether zoom varied at all and whether the
// two agree.
//
// Cross-checks emitted alongside, all read live, none used in the derivation:
//   * m_gameState.m_cameraPosition / m_cameraZoom / m_cameraAngle
//   * m_objectLayer's own position, scale and rotation
//   * GJBaseGameLayer::m_cameraWidth / m_cameraHeight / m_cameraUnzoomedX /
//     m_halfCameraWidth -- GD's own idea of camera extent, which if it is in
//     world units should reproduce the derived width
//   * pscr: the PLAYER mapped forward through the same transform into screen
//     points. This is the check that makes the whole thing falsifiable from
//     outside the mod: a screenshot says where the player actually is on
//     screen, and pscr predicts it. If pscr lands outside the visible rect, or
//     nowhere near where the player visibly is, the transform is the wrong one
//     and every world-unit number on the line is wrong with it.
//   * parentOK: whether m_player1's actual CCNode parent IS m_objectLayer.
//     If it is not, the player is drawn through a different transform than the
//     one being inverted, and pscr2 (computed through the player's REAL
//     parent) is emitted so the difference is visible instead of assumed.
//
// The four corners of the visible screen rect are mapped individually and
// min/max'd, so a rotated camera (m_cameraAngle != 0) yields the axis-aligned
// bounding box of the visible region rather than a silently wrong rect. That
// bbox OVERSTATES the visible area when rotated; the angle is on every line so
// a run can tell whether any sample was rotated at all. UNVERIFIED whether GD
// rotates the camera on Stereo Madness -- expected 0, not checked.
//
// ---------------------------------------------------------------------------
// WHICH SCREEN RECT
//
// GD renders at a design resolution that is independent of the OS window size,
// and GDRL_WINDOWED runs unattended in a small window -- so "does the visible
// world width depend on the OS window's aspect ratio?" is itself one of the
// questions. The VIEWPORT-CTX line therefore emits the full director/view
// geometry: design resolution, screen (frame) size, viewport rect, the view's
// scale factors and the resolution policy.
//
// The rect that gets inverted is the VISIBLE rect in design points, computed
// here from the view's own public fields rather than by calling
// CCDirector::getVisibleSize()/getVisibleOrigin():
//
//   kResolutionNoBorder: visible = screenSize / viewScale, centred in design
//                        space (part of the design rect is cropped off screen)
//   otherwise:           visible = the whole design rect
//
// That is cocos2d-x 2.x's own definition, reproduced inline so the derivation
// is auditable from this file and so nothing depends on a cocos symbol being
// exported. The raw inputs are all on the CTX line, so if this reproduction is
// wrong the run can recompute the rect from the log without a rebuild.
//
// ---------------------------------------------------------------------------
// WHERE IT SAMPLES, AND WHY TWO SITES
//
// The camera is a per-RENDER-FRAME quantity, not a per-physics-step one, so
// this probe samples per frame -- the opposite of the sampling rule for
// per-tick quantities (README, "Two probe bugs worth not repeating"), for the
// same reason: sample the thing at its own rate.
//
//   GDRL_PROBE_VIEWPORT=1  sample in PlayLayer::postUpdate, AFTER the original
//   GDRL_PROBE_VIEWPORT=2  additionally sample in GJBaseGameLayer::visit,
//                          BEFORE the original
//
// Mode 2 exists because I did NOT verify from disassembly where GD writes the
// camera into m_objectLayer, and therefore cannot assert that postUpdate's
// camera state is the state that gets drawn. visit() is the render pass
// itself, so a sample taken at its head is the transform the frame is about to
// be drawn with, by construction. Running mode 2 and diffing `where=post`
// against `where=visit` at the same tick MEASURES whether postUpdate is a
// truthful sampling point instead of assuming it. Mode 1 is the cheap default;
// mode 2 is the honesty check, and it should be run at least once before any
// number from mode 1 is quoted.
//
// PlayLayer::postUpdate is a real out-of-line function on this build
// (MacOS ARM 0xa8434, Intel 0xbaec0, per the 2.2081 binding); GJBaseGameLayer's
// own postUpdate is "Out of line" on every platform, which is why the hook is
// on PlayLayer. GJBaseGameLayer::visit is at MacOS ARM 0x131340 and PlayLayer
// does NOT override visit (no `virtual void visit` in PlayLayer's binding), so
// the base hook is the one that runs during gameplay. Neither address was
// re-derived from disassembly here; both are taken from the binding headers,
// and Geode failing to install either hook is loud at load time.
//
// ---------------------------------------------------------------------------
// ONE HONEST CAVEAT
//
// CCNode::nodeToWorldTransform() recomputes and CACHES each ancestor's
// transform, clearing its dirty flag. So this probe touches engine state, in
// the same family as the getObjectRect() concern the README already records
// for telemetry. The argued reason it cannot change what is rendered: the
// cached matrix is recomputed from position/scale/rotation, which this file
// never writes, and any later write re-dirties the flag -- so visit() either
// reuses an identical matrix or recomputes it anyway. That is an argument,
// NOT a measurement; it is UNVERIFIED. It is also why this probe is off by
// default and must not be enabled during a determinism or benchmark run.
//
// Precision: cocos affine transforms are float, so inverting one and mapping a
// world x of ~4000 carries roughly 1e-4 relative error (sub-0.5-unit). The
// inverse and the corner mapping are done in double here to avoid adding to
// that, but the float inputs set the floor. Fine for "is the horizon 1400 or
// 600"; do not read the fourth decimal as meaningful.
//
// WHAT THIS PROBE DOES NOT MEASURE: whether the player can PERCEIVE what is
// inside the rect. Objects can be faded, obscured by effects, or drawn behind
// a background. The rect is the geometric bound on what is on screen, which is
// an upper bound on what a human sees, and Benchmark A needs an upper bound.
//
// ---------------------------------------------------------------------------
// FIRST RUN, 2026-08-15 -- what this apparatus actually reported
//
// Log kept at backups/reference-logs/viewport-probe-20260815-171406.log.
// Recorded here because it is evidence about the APPARATUS, not the ruling on
// the horizon (which is not this file's to make):
//
//  * postUpdate and visit AGREE EXACTLY, every frame, including while the
//    camera was moving and during the death shake. So the mode-2 ordering
//    question is answered by measurement: postUpdate's camera state IS the
//    state the frame is drawn with, and mode 1 is a truthful sampling point.
//    Frame order observed: post(frame N) then visit(frame N), identical values.
//
//  * Three independent sources agree on the visible extent at zoom 1:
//      - this file's inverted render transform            -> 569.0 x 320.0
//      - the view's own design resolution (CTX line)      -> 569.0 x 320.0
//      - GD's m_cameraWidth / m_cameraHeight              -> 569.0 x 320.0
//    Those are not the same computation, so the agreement is a real check.
//
//  * policy=3 is kResolutionFixedHeight. That means the design HEIGHT is
//    fixed and the design WIDTH is recomputed from the window aspect ratio:
//    cocos does width = ceil(screenW / (screenH / designH)). Both window sizes
//    seen in that run (960x540 fullscreen, 396x223 GDRL_WINDOWED) are ~16:9 and
//    both produced 569, matching that formula to the unit. CONSEQUENCE, and it
//    is a benchmark-definition problem, not a probe problem: the horizontal
//    world extent VARIES WITH THE OS WINDOW'S ASPECT RATIO; the vertical
//    extent (320 units) does not. UNVERIFIED at any non-16:9 aspect -- the
//    formula predicts it, no run has been done at another aspect.

#include <Geode/Geode.hpp>
#include <Geode/modify/PlayLayer.hpp>
#include <Geode/modify/GJBaseGameLayer.hpp>

#include "viewport.hpp"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <vector>

using namespace geode::prelude;

// main.cpp's per-attempt level id (external linkage on purpose there; see
// probes.cpp, which reads it the same way). Read-only here, stamped onto the
// per-attempt summary so a measurement taken on the wrong level is
// identifiable after the fact rather than plausible.
extern int g_gdrlLevelID;

namespace {

// ---------------------------------------------------------------------------
// Configuration, read once at namespace scope.
//
// getenv on every frame is a linear scan of environ and is not thread-safe
// against setenv; main.cpp, probes.cpp and telemetry.cpp all establish this
// convention.
// ---------------------------------------------------------------------------

int envInt(const char* name, int fallback) {
    const char* v = std::getenv(name);
    if (!v || !*v) return fallback;
    return std::atoi(v);
}

// 0 = off (the default, and the only value that must exist for an unmodded
// run), 1 = postUpdate only, 2 = postUpdate + visit.
const int g_mode = envInt("GDRL_PROBE_VIEWPORT", 0);

const bool g_atPost  = g_mode >= 1;
const bool g_atVisit = g_mode >= 2;

// Frames between periodic lines. Every frame is ACCUMULATED into the min/max
// summary regardless; the period only thins the log. 30 frames is ~0.5 s at
// 60 fps. Edge-triggering (below) means a zoom change is never waiting on the
// period to become visible.
const int g_period = envInt("GDRL_PROBE_VIEWPORT_PERIOD", 30);

// Movement in the derived edges, in world units, that counts as an edge worth
// logging immediately. 0.5 units is well above the float-transform noise floor
// (~1e-4 relative) and well below anything a real zoom change would produce.
constexpr double kEdgeEpsilon = 0.5;

// The repo's tick clock. NOT `t == n/240.0`: GD accumulates a float32 1/240
// into a double, so exact equality fails on the very first tick. This
// duplicates main.cpp's gdrlTick() rather than calling it because that helper
// lives in main.cpp's anonymous namespace and has no external linkage --
// probes.cpp duplicates it for the same reason. Same expression, verbatim.
constexpr double kTicksPerSec = 240.0;

long gdrlTickLocal(PlayLayer* pl) {
    return pl ? std::lround(pl->m_attemptTime * kTicksPerSec) : -1;
}

// ---------------------------------------------------------------------------
// A 2x3 affine inverse, done in double, spelled out rather than delegated.
//
// cocos's CCAffineTransformInvert would do this, but doing it here means the
// arithmetic is auditable from this file, is carried in double instead of
// float, and adds no dependency on a cocos symbol being exported to the mod.
// cocos convention (CCAffineTransform.h): x' = a*x + c*y + tx,
//                                          y' = b*x + d*y + ty.
// ---------------------------------------------------------------------------
struct Affine {
    double a, b, c, d, tx, ty;
    bool   ok;      // false when the matrix is singular (degenerate camera)
};

Affine fromCC(const CCAffineTransform& t) {
    return Affine{ t.a, t.b, t.c, t.d, t.tx, t.ty, true };
}

Affine invert(const Affine& t) {
    const double det = t.a * t.d - t.b * t.c;
    if (det == 0.0 || !std::isfinite(det)) {
        return Affine{ 0, 0, 0, 0, 0, 0, false };
    }
    const double inv = 1.0 / det;
    return Affine{
         t.d * inv,
        -t.b * inv,
        -t.c * inv,
         t.a * inv,
        (t.c * t.ty - t.d * t.tx) * inv,
        (t.b * t.tx - t.a * t.ty) * inv,
        true
    };
}

inline void applyAffine(const Affine& t, double x, double y, double& ox, double& oy) {
    ox = t.a * x + t.c * y + t.tx;
    oy = t.b * x + t.d * y + t.ty;
}

// ---------------------------------------------------------------------------
// Per-attempt accumulators.
//
// Updated on EVERY sample, not only on logged ones, so the summary's extremes
// cannot be an artifact of the logging period. Initialised to +/-inf with a
// separate count, so "no samples" is distinguishable from "a sample of zero" --
// a silent zero that could mean either is the clean-looking negative this repo
// keeps paying for.
// ---------------------------------------------------------------------------
struct Range {
    double lo =  std::numeric_limits<double>::infinity();
    double hi = -std::numeric_limits<double>::infinity();
    void add(double v) { lo = std::min(lo, v); hi = std::max(hi, v); }
};

long  g_samples   = 0;      // samples accumulated this attempt (both sites)
long  g_postCount = 0;      // frames sampled at postUpdate
long  g_frames    = 0;      // periodic-logging counter, postUpdate site only
bool  g_baseline  = false;  // has a baseline VIEWPORT line been printed?
bool  g_ctxLogged = false;  // has VIEWPORT-CTX been printed this attempt?

Range g_ahead, g_behind, g_up, g_down, g_width, g_height, g_zoom, g_speed;

double g_lastAhead = 0.0, g_lastUp = 0.0, g_lastZoom = 0.0;

void resetViewportAttempt() {
    g_samples   = 0;
    g_postCount = 0;
    g_frames    = 0;
    g_baseline  = false;
    g_ctxLogged = false;
    g_ahead = g_behind = g_up = g_down = Range{};
    g_width = g_height = g_zoom = g_speed = Range{};
    g_lastAhead = g_lastUp = g_lastZoom = 0.0;
}

// ---------------------------------------------------------------------------
// The visible screen rect, in design points, from the view's own public
// fields. See the header comment for why this is reproduced rather than
// called.
// ---------------------------------------------------------------------------
struct ScreenRect {
    double x, y, w, h;
};

ScreenRect visibleRect(CCEGLView* view) {
    const double dw = view->m_obDesignResolutionSize.width;
    const double dh = view->m_obDesignResolutionSize.height;

    if (view->m_eResolutionPolicy == kResolutionNoBorder &&
        view->m_fScaleX != 0.f && view->m_fScaleY != 0.f) {
        const double vw = view->m_obScreenSize.width  / view->m_fScaleX;
        const double vh = view->m_obScreenSize.height / view->m_fScaleY;
        return ScreenRect{ (dw - vw) / 2.0, (dh - vh) / 2.0, vw, vh };
    }
    return ScreenRect{ 0.0, 0.0, dw, dh };
}

// ===========================================================================
// STATE-NEUTRAL TRANSFORM READ
//
// CCNode::nodeToWorldTransform() walks the parent chain calling
// nodeToParentTransform() on each node, and cocos2d-x 2.x's implementation of
// that is a memoiser:
//
//     if (m_bTransformDirty) { ...compute...; m_sTransform = t;
//                              m_bTransformDirty = false; }
//     return m_sTransform;
//
// So it writes. On the probe that was tolerable and I labelled the safety
// argument UNVERIFIED. On the observation path it would run every physics step
// of every benchmark run, and the failure mode is real rather than theoretical:
// if the cache is dirty when we call, we recompute and CLEAR the flag; if GD
// then writes m_obPosition directly (a field write, not setPosition, which GD
// does do in places) without re-dirtying, visit() would reuse OUR matrix and
// render one write stale. That is a rendering shift no physics test would
// catch.
//
// Rather than argue the risk away or try to measure a negative, this removes
// it: snapshot every CCNode field that nodeToParentTransform could write, on
// every node of the chain, call the transform, put the fields back. Afterwards
// the chain is byte-identical to before, so the call is a pure read BY
// CONSTRUCTION. Note the two cases are individually harmless once restored:
//   * flag was clean -> cocos returns the cache and writes nothing anyway
//   * flag was dirty -> we recompute (same inputs, same result GD would get)
//                       and then restore dirty=true, so visit() recomputes
//                       exactly as it would have.
//
// The restored field list is deliberately WIDER than cocos 2.x needs, because
// this build is RobTop's fork and its nodeToParentTransform is not the stock
// one: m_bPositionDirty, m_fTransformX and m_fTransformY are RobTop additions
// sitting in the middle of the transform block, and restoring them costs
// nothing. m_sInverse/m_bInverseDirty are included for the same reason.
//
// GDRL_VERIFY_XFORM=1 turns the "by construction" claim into a measurement:
// it byte-compares the whole CCNode subobject before the call and after the
// restore, and logs any offset that differs. That is the check that says
// whether this file's field list is complete on the REAL binary, rather than
// complete against the header I read. Default off; it is a one-run check, not
// a permanent cost.
// ===========================================================================

const bool g_verifyXform = envInt("GDRL_VERIFY_XFORM", 0) == 1;

struct NodeCache {
    CCNode*           node;
    CCAffineTransform sTransform;
    CCAffineTransform sInverse;
    CCPoint           anchorInPoints;
    float             transformX;
    float             transformY;
    bool              transformDirty;
    bool              inverseDirty;
    bool              additionalTransformDirty;
    bool              positionDirty;
};

void snapshotNode(CCNode* n, NodeCache& c) {
    c.node                     = n;
    c.sTransform               = n->m_sTransform;
    c.sInverse                 = n->m_sInverse;
    c.anchorInPoints           = n->m_obAnchorPointInPoints;
    c.transformX               = n->m_fTransformX;
    c.transformY               = n->m_fTransformY;
    c.transformDirty           = n->m_bTransformDirty;
    c.inverseDirty             = n->m_bInverseDirty;
    c.additionalTransformDirty = n->m_bAdditionalTransformDirty;
    c.positionDirty            = n->m_bPositionDirty;
}

void restoreNode(const NodeCache& c) {
    CCNode* n                    = c.node;
    n->m_sTransform              = c.sTransform;
    n->m_sInverse                = c.sInverse;
    n->m_obAnchorPointInPoints   = c.anchorInPoints;
    n->m_fTransformX             = c.transformX;
    n->m_fTransformY             = c.transformY;
    n->m_bTransformDirty         = c.transformDirty;
    n->m_bInverseDirty           = c.inverseDirty;
    n->m_bAdditionalTransformDirty = c.additionalTransformDirty;
    n->m_bPositionDirty          = c.positionDirty;
}

// Guard against an unbounded or cyclic parent chain. A scene graph deeper than
// this in GD means something is wrong; bail rather than walk forever.
constexpr int kMaxChain = 32;

// Reused across calls so the hot path does no allocation after the first step.
std::vector<NodeCache>              g_chain;
std::vector<std::vector<uint8_t>>   g_chainBytes;   // GDRL_VERIFY_XFORM only

CCAffineTransform readWorldTransformNeutrally(CCNode* leaf, bool& ok) {
    ok = false;
    g_chain.clear();

    int depth = 0;
    for (CCNode* n = leaf; n != nullptr; n = n->getParent()) {
        if (++depth > kMaxChain) return CCAffineTransform{};
        NodeCache c{};
        snapshotNode(n, c);
        g_chain.push_back(c);
    }

    if (g_verifyXform) {
        g_chainBytes.resize(g_chain.size());
        for (size_t i = 0; i < g_chain.size(); i++) {
            g_chainBytes[i].resize(sizeof(CCNode));
            std::memcpy(g_chainBytes[i].data(), g_chain[i].node, sizeof(CCNode));
        }
    }

    const CCAffineTransform t = leaf->nodeToWorldTransform();

    for (auto& c : g_chain) restoreNode(c);

    if (g_verifyXform) {
        for (size_t i = 0; i < g_chain.size(); i++) {
            const auto* before = g_chainBytes[i].data();
            const auto* after  = reinterpret_cast<const uint8_t*>(g_chain[i].node);
            if (std::memcmp(before, after, sizeof(CCNode)) == 0) continue;

            // Report the exact byte offsets, not just "differs": an offset maps
            // straight back to a field, so a missing entry in the restore list
            // is identifiable from the log without a rebuild.
            std::string offsets;
            int shown = 0;
            for (size_t b = 0; b < sizeof(CCNode) && shown < 24; b++) {
                if (before[b] != after[b]) {
                    if (!offsets.empty()) offsets += ',';
                    offsets += fmt::format("0x{:x}", (unsigned)b);
                    shown++;
                }
            }
            log::error("[gdrl] VERIFY_XFORM chain[{}] node={} CCNode subobject "
                       "NOT restored; differing offsets: {}{}  -- the restore "
                       "list in viewport.cpp is INCOMPLETE and the camera read "
                       "is NOT state-neutral",
                       i, (void*)g_chain[i].node, offsets,
                       shown >= 24 ? " (truncated)" : "");
        }
    }

    ok = true;
    return t;
}

// ---------------------------------------------------------------------------
// One-per-attempt (and on change) dump of everything that is constant for a
// given window/resolution. Separated from the per-frame line so the per-frame
// line stays short enough to read and to parse.
// ---------------------------------------------------------------------------
void logContext(long tick, CCEGLView* view, const ScreenRect& vis) {
    const CCSize win = CCDirector::sharedDirector()->getWinSize();

    log::info(
        "[gdrl] VIEWPORT-CTX tick={} win=({:.2f},{:.2f}) design=({:.2f},{:.2f}) "
        "screen=({:.2f},{:.2f}) vpRect=({:.2f},{:.2f},{:.2f},{:.2f}) "
        "viewScale=({:.6f},{:.6f}) policy={} vis=({:.2f},{:.2f},{:.2f},{:.2f}) "
        "aspect={:.6f}",
        tick, win.width, win.height,
        view->m_obDesignResolutionSize.width, view->m_obDesignResolutionSize.height,
        view->m_obScreenSize.width, view->m_obScreenSize.height,
        view->m_obViewPortRect.origin.x, view->m_obViewPortRect.origin.y,
        view->m_obViewPortRect.size.width, view->m_obViewPortRect.size.height,
        view->m_fScaleX, view->m_fScaleY, (int)view->m_eResolutionPolicy,
        vis.x, vis.y, vis.w, vis.h,
        view->m_obScreenSize.height != 0.f
            ? view->m_obScreenSize.width / view->m_obScreenSize.height : 0.0);
}

// ---------------------------------------------------------------------------
// The sample. `where` is "post" or "visit"; see the header comment.
// ---------------------------------------------------------------------------
void sampleViewport(const char* where, bool periodicSite) {
    auto* pl = PlayLayer::get();
    if (!pl) return;

    auto* ol = pl->m_objectLayer;
    auto* p  = pl->m_player1;
    if (!ol || !p) return;

    auto* dir = CCDirector::sharedDirector();
    if (!dir) return;
    auto* view = dir->getOpenGLView();
    if (!view) return;

    const long tick = gdrlTickLocal(pl);
    const ScreenRect vis = visibleRect(view);

    if (!g_ctxLogged) {
        logContext(tick, view, vis);
        g_ctxLogged = true;
    }

    // The forward map (world -> screen) and its inverse (screen -> world).
    const Affine fwd = fromCC(ol->nodeToWorldTransform());
    const Affine inv = invert(fwd);
    if (!inv.ok) {
        log::error("[gdrl] VIEWPORT tick={} where={} SINGULAR transform "
                   "a={:.9f} b={:.9f} c={:.9f} d={:.9f}",
                   tick, where, fwd.a, fwd.b, fwd.c, fwd.d);
        return;
    }

    // All four corners, min/max'd: correct (as a bounding box) under camera
    // rotation instead of silently wrong. See header.
    double left =  std::numeric_limits<double>::infinity();
    double right = -std::numeric_limits<double>::infinity();
    double bot  =  std::numeric_limits<double>::infinity();
    double top  = -std::numeric_limits<double>::infinity();

    const double cx[4] = { vis.x,          vis.x + vis.w, vis.x,          vis.x + vis.w };
    const double cy[4] = { vis.y,          vis.y,         vis.y + vis.h,  vis.y + vis.h };
    for (int i = 0; i < 4; i++) {
        double wx = 0.0, wy = 0.0;
        applyAffine(inv, cx[i], cy[i], wx, wy);
        left  = std::min(left,  wx);
        right = std::max(right, wx);
        bot   = std::min(bot,   wy);
        top   = std::max(top,   wy);
    }

    // The player, in the world space the rect is expressed in. getPositionX/Y
    // is deliberate here and is NOT the m_positionX/m_positionY case from
    // README rule 7: that rule is about objects moved by the MOVE pipeline,
    // which never writes the CCNode position. The player is moved by the
    // physics step, which does write the CCNode position, and every other
    // player-position reader in this repo (main.cpp's maxX, telemetry.cpp)
    // uses getPositionX/Y. Both are emitted anyway so a disagreement is
    // visible rather than assumed.
    const double px = p->getPositionX();
    const double py = p->getPositionY();

    // The falsifiability hook: the player mapped FORWARD to screen points.
    // A screenshot can check this without trusting anything in this file.
    double pscrX = 0.0, pscrY = 0.0;
    applyAffine(fwd, px, py, pscrX, pscrY);

    // Is the player actually drawn through the transform being inverted?
    auto* parent = p->getParent();
    const bool parentOK = (parent == static_cast<CCNode*>(ol));
    double pscr2X = pscrX, pscr2Y = pscrY;
    if (!parentOK && parent) {
        const Affine viaParent = fromCC(parent->nodeToWorldTransform());
        applyAffine(viaParent, p->getPositionX(), p->getPositionY(), pscr2X, pscr2Y);
    }

    const double ahead  = right - px;
    const double behind = px - left;
    const double up     = top - py;
    const double down   = py - bot;

    g_samples++;
    g_ahead.add(ahead);
    g_behind.add(behind);
    g_up.add(up);
    g_down.add(down);
    g_width.add(right - left);
    g_height.add(top - bot);
    g_zoom.add(pl->m_gameState.m_cameraZoom);
    g_speed.add(p->m_playerSpeed);

    // Baseline / edge / periodic, the same three-way rule probes.cpp's CMDVEC
    // probe uses: without a printed baseline, "no line yet" is
    // indistinguishable from "unchanged" and from "probe not running".
    const bool changed =
        std::fabs(ahead - g_lastAhead) > kEdgeEpsilon ||
        std::fabs(up    - g_lastUp)    > kEdgeEpsilon ||
        std::fabs((double)pl->m_gameState.m_cameraZoom - g_lastZoom) > 1e-6;

    const bool periodic = periodicSite && g_period > 0 && (g_frames % g_period) == 0;

    if (!g_baseline || changed || periodic || !periodicSite) {
        // m_lastActivatedPortal1 is GD's own "last portal the player entered"
        // and is the cheap speed-portal context the task asked for: speed
        // portals are object ids 200-203 and 1334, so a run can bucket on it
        // without this file hard-coding an id->speed table it has not measured.
        // Emitted as an object id, -1 for none. NOT claimed to be a speed
        // portal -- it is the last portal of ANY kind.
        GameObject* lastPortal = pl->m_gameState.m_lastActivatedPortal1;
        const int lastPortalID = lastPortal ? (int)lastPortal->m_objectID : -1;

        log::info(
            "[gdrl] VIEWPORT tick={} n={} where={} "
            "left={:.4f} right={:.4f} bottom={:.4f} top={:.4f} "
            "w={:.4f} h={:.4f} px={:.4f} py={:.4f} "
            "ahead={:.4f} behind={:.4f} up={:.4f} down={:.4f} "
            "zoom={:.6f} tzoom={:.6f} angle={:.6f} "
            "olpos=({:.4f},{:.4f}) olscale=({:.6f},{:.6f}) olrot={:.4f} "
            "campos=({:.4f},{:.4f}) camw={:.4f} camh={:.4f} camunzx={:.4f} "
            "halfcamw={:.4f} camwoff={:.4f} camhoff={:.4f} "
            "pscr=({:.3f},{:.3f}) pscr2=({:.3f},{:.3f}) parentOK={} "
            "pmx={:.4f} pmy={:.4f} speed={:.6f} mult={:.6f} lastPortal={}",
            tick, g_samples, where,
            left, right, bot, top,
            right - left, top - bot, px, py,
            ahead, behind, up, down,
            pl->m_gameState.m_cameraZoom, pl->m_gameState.m_targetCameraZoom,
            pl->m_gameState.m_cameraAngle,
            ol->getPositionX(), ol->getPositionY(),
            ol->getScaleX(), ol->getScaleY(), ol->getRotation(),
            pl->m_gameState.m_cameraPosition.x, pl->m_gameState.m_cameraPosition.y,
            pl->m_cameraWidth, pl->m_cameraHeight, pl->m_cameraUnzoomedX,
            pl->m_halfCameraWidth, pl->m_cameraWidthOffset, pl->m_cameraHeightOffset,
            pscrX, pscrY, pscr2X, pscr2Y, (int)parentOK,
            p->m_positionX, p->m_positionY,
            p->m_playerSpeed, p->m_speedMultiplier, lastPortalID);

        g_baseline = true;
    }

    g_lastAhead = ahead;
    g_lastUp    = up;
    g_lastZoom  = pl->m_gameState.m_cameraZoom;
}

}   // namespace

// ---------------------------------------------------------------------------
// Sampling site 1: PlayLayer::postUpdate, AFTER the original.
//
// Also carries the per-attempt summary on resetLevel. A fourth resetLevel hook
// alongside main.cpp's, experiments.cpp's and probes.cpp's -- hook ordering
// between translation units is a linker detail, so this file's state is reset
// here regardless of what order the four run in, and nothing here depends on
// running before or after any of the others.
// ---------------------------------------------------------------------------
class $modify(GDRLViewportPlayLayer, PlayLayer) {
    void postUpdate(float dt) {
        PlayLayer::postUpdate(dt);
        if (!g_atPost) return;
        sampleViewport("post", true);
        g_postCount++;
        g_frames++;
    }

    void resetLevel() {
        // Summary before delegating, not after: reading after the original
        // reads the respawn, not the attempt that just ended (README, "Two
        // probe bugs worth not repeating"). Emitted even when samples==0,
        // because "the hook never fired" is exactly the failure this probe
        // must be able to report.
        if (g_mode > 0 && g_samples == 0) {
            // No samples: emit the fact, NOT +/-inf ranges. A resetLevel with
            // nothing sampled is a real and expected state (the menu-side reset
            // that precedes the first attempt), and printing inf for it makes
            // the genuine "the hook never fired during gameplay" failure harder
            // to spot rather than easier.
            log::info("[gdrl] VIEWPORT-SUM lvl={} mode={} samples=0 postFrames=0 "
                      "(no gameplay frames sampled this attempt)",
                      g_gdrlLevelID, g_mode);
        } else if (g_mode > 0) {
            log::info(
                "[gdrl] VIEWPORT-SUM lvl={} mode={} samples={} postFrames={} "
                "ahead=[{:.4f},{:.4f}] behind=[{:.4f},{:.4f}] "
                "up=[{:.4f},{:.4f}] down=[{:.4f},{:.4f}] "
                "w=[{:.4f},{:.4f}] h=[{:.4f},{:.4f}] "
                "zoom=[{:.6f},{:.6f}] speed=[{:.6f},{:.6f}]",
                g_gdrlLevelID, g_mode, g_samples, g_postCount,
                g_ahead.lo, g_ahead.hi, g_behind.lo, g_behind.hi,
                g_up.lo, g_up.hi, g_down.lo, g_down.hi,
                g_width.lo, g_width.hi, g_height.lo, g_height.hi,
                g_zoom.lo, g_zoom.hi, g_speed.lo, g_speed.hi);
        }

        PlayLayer::resetLevel();

        if (g_mode > 0) resetViewportAttempt();
    }
};

// ---------------------------------------------------------------------------
// Sampling site 2 (mode 2 only): GJBaseGameLayer::visit, BEFORE the original.
//
// This is the render pass itself, so the transform read here is the one the
// frame is about to be drawn with. Its whole purpose is to be diffed against
// the `where=post` sample at the same tick; see the header comment.
//
// Not periodic-gated: in mode 2 every visit sample is logged, because the
// question it answers is a per-frame agreement question and a thinned series
// cannot answer it. Mode 2 is loud on purpose and is not for long runs.
// ---------------------------------------------------------------------------
class $modify(GDRLViewportBaseLayer, GJBaseGameLayer) {
    void visit() {
        if (g_atVisit) sampleViewport("visit", false);
        GJBaseGameLayer::visit();
    }
};

$execute {
    if (g_mode > 0) {
        log::info("[gdrl] PROBE_VIEWPORT on -- mode={} ({}), period={} frames. "
                  "Derives the visible world rect by INVERTING "
                  "m_objectLayer->nodeToWorldTransform() over the visible "
                  "design-resolution rect; zoom enters only through that "
                  "transform. Off by default; do NOT enable during a "
                  "determinism or benchmark run (it touches cocos transform "
                  "caches -- see viewport.cpp header).",
                  g_mode,
                  g_mode >= 2 ? "postUpdate + visit" : "postUpdate",
                  g_period);
    }
}
