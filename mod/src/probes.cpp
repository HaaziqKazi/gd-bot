// Four diagnostic probes, all default OFF and all required to add ZERO
// behaviour in that state -- ~550 attempts of determinism evidence (see
// README, "Physics is fixed-step at 240Hz") depend on the mod being
// bit-identical when its new features are disabled. Every hook below either
// forwards to the original unmodified before touching its env gate, or is
// gated on a `const bool`/`const int` read once at namespace scope (getenv on
// every physics tick is a linear scan of environ and is not thread-safe
// against setenv -- same discipline main.cpp uses for GDRL_VERBOSE et al.).
//
// Kept out of main.cpp/experiments.cpp deliberately: Geode supports multiple
// $modify classes on the same target class across translation units --
// experiments.cpp already proves this for GJBaseGameLayer and PlayLayer
// alongside main.cpp -- so this file adds its own hook classes rather than
// touching the existing ones.
//
// -------------------------------------------------------------------------
// Probe A: GDRL_PROBE_CMDVEC=1
//
// Which of GJEffectManager's seven candidate containers actually holds live
// GroupCommandObject2 instances? This gates the whole telemetry path in the
// spec (README, "Telemetry the mod must emit") and nothing else depends on
// it, so it is answered in isolation before anything is built on top of it.
//
// The seven candidates, verified against
// mod/build/_deps/bindings-src/bindings/2.2081/GeometryDash.bro (~line 8604)
// and cross-checked byte-for-byte against
// mod/build/bindings/bindings/Geode/binding_arm/GJEffectManager.hpp:
//
//   m_unkVector518  gd::vector<GroupCommandObject2*>
//   m_unkVector530  gd::vector<GroupCommandObject2*>
//   m_unkVector560  gd::vector<GroupCommandObject2>    -- BY VALUE, not by
//                                                          pointer. Only
//                                                          .size() is taken
//                                                          below, which is
//                                                          identical either
//                                                          way, but do not
//                                                          assume this one
//                                                          can be treated as
//                                                          a pointer vector
//                                                          if this file is
//                                                          ever extended to
//                                                          inspect elements.
//   m_unkVector5b0  gd::vector<GroupCommandObject2*>
//   m_unkVector600  gd::vector<GroupCommandObject2*>
//   m_unkMap5c8     gd::unordered_map<int, gd::vector<GroupCommandObject2*>>
//   m_unkMap770     gd::map<std::pair<int,int>, gd::vector<GroupCommandObject2*>>
//
// gd::vector/gd::map/gd::unordered_map are literally std:: aliases on this
// platform (mod/build/../loader/include/Geode/c++stl/gdstdlib.hpp selects
// aliastl.hpp for every platform except Android), so ordinary STL iteration
// and .size() are correct, not an ABI gamble.
//
// For the two maps, "size" is taken to mean total live command objects
// reachable through the map (sum of the per-key vector sizes), not the
// key count (map.size()). A map can hold N live commands under one key or
// under N keys, and the probe's question is about live *commands*, not live
// *groups* -- key count would silently undercount the former by reporting
// the latter. This is a judgement call, not a measured fact; it is called
// out here so a reader comparing this file's numbers against a raw
// map.size() elsewhere does not conclude the two disagree by mistake.
//
// Emission point: GJEffectManager::prepareMoveActions(float dt, bool
// intermediate), confirmed at m1 0x27de38 in both the .bro and in
// Geode/modify_arm/GJEffectManager.hpp's GEODE_APPLY_MODIFY_FOR_FUNCTION
// table. NOT GJBaseGameLayer::update -- that is per-RENDER-FRAME, and
// sampling a per-tick quantity per frame is the exact bug that made maxX a
// function of the frame rate (README, "Two probe bugs worth not repeating").
// prepareMoveActions is per-PHYSICS-STEP: its two `bl 0x27de38` call sites in
// the cached disassembly are
//   0x10012086c inside GJBaseGameLayer::loadUpToPosition (level SEEK, not
//               gameplay)
//   0x1001233e0 inside GJBaseGameLayer::update at +0x9f8, inside update's
//               fixed-step loop (loop body 0x122ea4..0x123d94, latch
//               0x122e98, exit 0x123d9c)
// -- both re-verified here with
//   grep -cP '\tbl\t0x27de38$' gd_arm64.asm            -> 2
//   grep -n '10012086c\|1001233e0' gd_arm64.asm        -> both `bl 0x27de38`
// confirming the call-site count and both addresses exactly as already
// established; not re-derived beyond that confirmation.
//
// Distinguishing the two call sites: a bare `PlayLayer::get() != nullptr`
// test is NOT sufficient, because loadUpToPosition also runs against a live
// PlayLayer -- e.g. on checkpoint seek, which re-simulates from the
// checkpoint forward through this same code path. Instead, this file's own
// GJBaseGameLayer::update hook sets a file-local flag immediately before
// delegating to the real update() and clears it immediately after; only a
// prepareMoveActions call observed while that flag is set is gameplay.
//
// Log volume: at 240 steps/sec, logging every call is a lot of lines for a
// quantity that is usually 0 and only occasionally nonzero for a few ticks
// around a move trigger. So logging is: edge-triggered (a size changed since
// last step) OR periodic (every kCmdVecLogPeriod steps, so a value stuck
// nonzero is still visible rather than silently missing from the log) OR the
// very first sample of the attempt (so a 0->1->0 transition has a visible
// baseline to transition away from -- edge-triggered logging that never
// prints a baseline is unreadable, because "no line yet" is indistinguishable
// from "still 0" and from "probe not running"). Plus one per-attempt summary
// of the max each container reached, which answers "did anything happen at
// all this attempt" in one line even if no trigger fired.
//
// -------------------------------------------------------------------------
// Probe B: GDRL_FORCE_VEHICLE=<n> (+ GDRL_FORCE_VEHICLE_TICK=<t>, default 60)
//
// Objective A's conditioning read path (README, "Conditioning state: the
// eight vehicles and their modifiers") has only ever been exercised in cube:
// with no input the player dies at the first spike, so ship/ball/ufo/wave/
// robot/spider/swing are all unverified -- including whether deriveVehicle's
// derived-first ordering is load-bearing (does GD leave a parent flag set
// while a child mode is active?) or merely defensive, which the probe logs
// and main.cpp already error-checks (`bits & (bits - 1)`) but has never seen
// exercised.
//
// This calls the mode togglers directly on the live PlayerObject instead of
// waiting for input injection to survive to a portal. Signatures and call
// counts verified against the .bro and re-confirmed against the cached
// disassembly:
//
//   grep -cP '\tbl\t0x<addr>$' gd_arm64.asm   (call sites, live == callable)
//   grep -cP '\tb\t0x<addr>$'  gd_arm64.asm   (tail calls, also live)
//
//   PlayerObject::toggleFlyMode(bool,bool)    m1 0x38b4d8   bl=12 b=0  (ship)
//   PlayerObject::toggleBirdMode(bool,bool)   m1 0x38bf50   bl=13 b=0  (ufo)
//   PlayerObject::toggleRollMode(bool,bool)   m1 0x38d5a0   bl=5  b=0  (ball)
//   PlayerObject::toggleDartMode(bool,bool)   m1 0x38cf84   bl=15 b=0  (wave)
//   PlayerObject::toggleRobotMode(bool,bool)  m1 0x38d95c   bl=13 b=0
//   PlayerObject::toggleSpiderMode(bool,bool) m1 0x38dd8c   bl=13 b=0
//   PlayerObject::toggleSwingMode(bool,bool)  m1 0x38c5b4   bl=11 b=1
//
// All seven counts above were re-measured against the cached disassembly
// while writing this file and match the numbers supplied exactly.
//
// A "gravity toggler" was also supplied for this probe as
// `PlayerObject::toggleGravityMode(bool)` at m1 0x3dbfb0 (bl=1, b=2, also
// re-measured and matching). It is NOT used here -- deliberately, and not
// merely because n only ranges 0..7 over vehicles. Checking the .bro at that
// exact byte offset (mod/build/_deps/bindings-src/bindings/2.2081/
// GeometryDash.bro:2556) shows `toggleGravityMode(bool gravityMode)` declared
// inside `class CreateParticlePopup`, a level-editor particle-effect popup --
// not PlayerObject. The bl=1/b=2 call-site counts are real, but they are
// call sites into a UI popup's own gravity-preview toggle, not into player
// gravity. Calling it through a PlayerObject* would call CreateParticlePopup
// code against a PlayerObject this-pointer, which is memory corruption, not
// a probe. The real player-side gravity flip, if this is ever wanted, is
// `PlayerObject::flipGravity(bool flip, bool noEffects)` at m1 0x37b40c
// (bl=28, also in the .bro, also re-measured) -- untouched here, flagged in
// the report instead, since fixing the task's data is out of scope for this
// file.
//
// What this probe validates and what it does NOT: forcing a mode flag is not
// equivalent to crossing a portal. A portal also sets size, gravity and
// speed, and may run additional setup this file knows nothing about. So this
// validates the flag DECODE and the conditioning READ PATH -- it does not
// validate portal transition semantics, and nothing here should be read as
// the stronger claim.

#include <Geode/Geode.hpp>
#include <Geode/modify/PlayLayer.hpp>
#include <Geode/modify/GJBaseGameLayer.hpp>
#include <Geode/modify/GJEffectManager.hpp>

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <limits>
#include <map>
#include <vector>

using namespace geode::prelude;

// main.cpp's per-attempt level id, with external linkage exactly as
// experiments.cpp already relies on (main.cpp: "External linkage on purpose
// ... so experiments.cpp can print an outcome line"). Read-only here: this
// file does not assign it, only stamps it onto its own summary line so a
// CMDVEC-SUM measurement taken on the wrong level is identifiable after the
// fact rather than plausible (same reasoning as main.cpp's own ATTEMPT line).
extern int g_gdrlLevelID;

namespace {
    bool envOn(const char* name) {
        const char* v = std::getenv(name);
        return v && *v == '1';
    }

    int envInt(const char* name, int fallback) {
        const char* v = std::getenv(name);
        if (!v || !*v) return fallback;
        return std::atoi(v);
    }

    // The measured physics timestep (README, "Physics is fixed-step at
    // 240Hz"). Used only to turn m_attemptTime into a tick index, exactly as
    // experiments.cpp does -- lround(t * 240), never t == n/240.0. The
    // residual is ~2.04e-05 ticks by construction (README, "The
    // input-placement clock"): GD accumulates m_attemptTime by adding a
    // float32 1/240 into a double, so exact equality fails on the very first
    // tick while rounding stays unambiguous for ~11 hours of attempt time.
    constexpr double kTicksPerSec = 240.0;

    // =======================================================================
    // Probe A: GDRL_PROBE_CMDVEC
    // =======================================================================
    const bool g_probeCmdVec = envOn("GDRL_PROBE_CMDVEC");

    // Probe D shares Probe A's prepareMoveActions emission point and gameplay
    // discriminator. Sampling here is per physics step, not per frame -- a
    // per-frame sample of a per-tick quantity is a measurement of the frame
    // rate (README, "Two probe bugs worth not repeating").
    const bool g_probeMove = envOn("GDRL_PROBE_MOVE");

    struct MovePosition {
        float x;
        float y;
    };
    std::map<GameObject*, MovePosition> g_movePrevious;
    bool g_moveTriggerLogged = false;

    void resetMoveAttempt() {
        g_movePrevious.clear();
        g_moveTriggerLogged = false;
    }

    void logMoveTriggerOnce(GJBaseGameLayer* layer, long tick) {
        if (g_moveTriggerLogged) return;
        g_moveTriggerLogged = true;

        // The synthetic fixture has one known Move trigger (object 901). The
        // moved-object scan below is deliberately generic; identifying this
        // authored source by id is only for reporting the source parameters GD
        // actually parsed, rather than silently comparing against synth.cpp's
        // constants.
        EffectGameObject* trigger = nullptr;
        if (layer && layer->m_objects) {
            const unsigned n = layer->m_objects->count();
            for (unsigned i = 0; i < n; i++) {
                auto* obj = static_cast<GameObject*>(layer->m_objects->objectAtIndex(i));
                if (obj && obj->m_objectID == 901) {
                    trigger = dynamic_cast<EffectGameObject*>(obj);
                    if (trigger) break;
                }
            }
        }

        if (trigger) {
            log::info(
                "[gdrl] MOVE-TRIGGER tick={} id={} target={} offX={:.9f} "
                "offY={:.9f} duration={:.9f} easing={}",
                tick, (int)trigger->m_objectID, trigger->m_targetGroupID,
                trigger->m_moveOffset.x, trigger->m_moveOffset.y,
                trigger->m_duration, (int)trigger->m_easingType);
        } else {
            // Still emit exactly one machine-readable parameter record for the
            // attempt. NaNs prevent a missing/wrongly-typed trigger from being
            // mistaken for a valid zero-valued trigger by the Python parser.
            const float nan = std::numeric_limits<float>::quiet_NaN();
            log::error(
                "[gdrl] MOVE-TRIGGER tick={} id=901 target=-1 offX={:.9f} "
                "offY={:.9f} duration={:.9f} easing=-1",
                tick, nan, nan, nan);
        }
    }

    void sampleMovedObjects(GJBaseGameLayer* layer, long tick) {
        if (!layer || !layer->m_objects) return;
        const unsigned n = layer->m_objects->count();
        for (unsigned i = 0; i < n; i++) {
            auto* obj = static_cast<GameObject*>(layer->m_objects->objectAtIndex(i));
            if (!obj) continue;

            const MovePosition now{obj->getPositionX(), obj->getPositionY()};
            auto [it, inserted] = g_movePrevious.emplace(obj, now);
            if (inserted) continue;  // per-attempt baseline, not movement

            const float dx = now.x - it->second.x;
            const float dy = now.y - it->second.y;
            if (dx != 0.f || dy != 0.f) {
                log::info(
                    "[gdrl] MOVE tick={} id={} x={:.9f} y={:.9f} "
                    "dx={:.9f} dy={:.9f}",
                    tick, (int)obj->m_objectID, now.x, now.y, dx, dy);
            }
            it->second = now;
        }
    }

    // True from just before this file's own GJBaseGameLayer::update hook
    // delegates to the real update() until just after it returns. Real
    // update() reaches prepareMoveActions synchronously (it is not queued or
    // deferred), so this flag is set for exactly the span in which a
    // prepareMoveActions call is the gameplay one rather than the
    // loadUpToPosition (seek) one. Single-threaded, touched only from the
    // game thread, same convention as main.cpp's g_gdrlInjecting.
    bool g_inGameplayUpdate = false;

    constexpr int kNumCmdVecs = 7;

    // Index order, fixed for the whole file: 518, 530, 560, 5b0, 600, 5c8, 770.
    long g_cvLast[kNumCmdVecs] = {0, 0, 0, 0, 0, 0, 0};
    long g_cvMax[kNumCmdVecs]  = {0, 0, 0, 0, 0, 0, 0};
    long g_cvStep              = 0;     // gameplay prepareMoveActions calls this attempt
    bool g_cvBaselinePrinted   = false;

    // 240 steps/sec, so this is one guaranteed line per second of attempt
    // time even if a size never changes -- enough to catch a container stuck
    // nonzero (a leak) without flooding the log the way logging every step
    // would (a multi-hundred-tick attempt would otherwise emit hundreds of
    // near-duplicate lines for the common case of "all seven stay 0").
    constexpr long kCmdVecLogPeriod = 240;

    void logCmdVecLine(const char* tag, long tick, const long sz[kNumCmdVecs]) {
        log::info(
            "[gdrl] {} tick={:<7} v518={:<4} v530={:<4} v560={:<4} v5b0={:<4} "
            "v600={:<4} m5c8={:<4} m770={:<4}",
            tag, tick, sz[0], sz[1], sz[2], sz[3], sz[4], sz[5], sz[6]);
    }

    void resetCmdVecAttempt() {
        for (int i = 0; i < kNumCmdVecs; i++) { g_cvLast[i] = 0; g_cvMax[i] = 0; }
        g_cvStep            = 0;
        g_cvBaselinePrinted = false;
    }

    // =======================================================================
    // Probe B: GDRL_FORCE_VEHICLE / GDRL_FORCE_VEHICLE_TICK
    // =======================================================================

    // Recognised whenever the var is set at all (including "0" or an
    // out-of-range value), not only when it parses into [0,7] -- an
    // unrecognised value should be reported loudly, not silently ignored
    // (the recurring failure mode in this repo is a broken measurement that
    // looks plausible; see README, "Two probe bugs worth not repeating").
    const bool g_probeForceVehicle = std::getenv("GDRL_FORCE_VEHICLE") != nullptr;
    const int  g_forceVehicle      = envInt("GDRL_FORCE_VEHICLE", -1);
    const int  g_forceVehicleTick  = envInt("GDRL_FORCE_VEHICLE_TICK", 60);

    // How many ticks after the toggler call to take the second ("SETTLE")
    // sample. No specific asynchronous mode-completion mechanism has been
    // identified in the disassembly for this probe -- this is a guard
    // against one existing (e.g. a deferred visual/animation callback that
    // finishes setting a flag a few frames late), not evidence that one
    // does. 12 ticks (=0.05s) is long enough to catch a several-frame delay
    // without stalling the probe for many frames if there is no such delay.
    // UNVERIFIED: whether GD's mode flags ever settle asynchronously at all.
    constexpr long kSettleDelayTicks = 12;

    bool g_fvFired         = false;   // toggler called this attempt
    bool g_fvSettleLogged  = false;
    long g_fvFireTick      = -1;

    void resetForceVehicleAttempt() {
        g_fvFired        = false;
        g_fvSettleLogged = false;
        g_fvFireTick     = -1;
    }

    // Duplicated from main.cpp's `enum class Vehicle` / vehicleName() /
    // modeFlagBits(). main.cpp defines all three in an anonymous namespace
    // with no header, and this file may not edit main.cpp to export them, so
    // the numbering and bit order are copied by hand instead of shared. The
    // bit order is LOAD-BEARING -- it must stay ship<<0, ball<<1, bird<<2,
    // dart<<3, robot<<4, spider<<5, swing<<6, exactly as main.cpp's
    // modeFlagBits(), or a bit pattern logged by this probe means something
    // different from the same pattern logged by main.cpp's COND/MODE lines.
    // If main.cpp's enum or bit order ever changes, this copy must be
    // updated by hand; nothing enforces the two staying in sync.
    const char* vehicleNameFor(int n) {
        switch (n) {
            case 0: return "cube";
            case 1: return "ship";
            case 2: return "ball";
            case 3: return "ufo";
            case 4: return "wave";
            case 5: return "robot";
            case 6: return "spider";
            case 7: return "swing";
        }
        return "?";
    }

    unsigned modeFlagBitsProbe(PlayerObject* p) {
        return (unsigned(p->m_isShip)   << 0) | (unsigned(p->m_isBall)   << 1)
             | (unsigned(p->m_isBird)   << 2) | (unsigned(p->m_isDart)   << 3)
             | (unsigned(p->m_isRobot)  << 4) | (unsigned(p->m_isSpider) << 5)
             | (unsigned(p->m_isSwing)  << 6);
    }

    // One self-contained line: flags, the overlap verdict the probe exists to
    // answer, and every conditioning field main.cpp's CondState reads,
    // straight off the live objects.
    void logForceVehicleState(const char* tag, long tick, PlayLayer* pl, PlayerObject* p) {
        const unsigned bits    = modeFlagBitsProbe(p);
        const bool     overlap = (bits & (bits - 1)) != 0;
        log::info(
            "[gdrl] {} tick={:<6} bits=0b{:07b} overlap={} size={:.3f} "
            "grav={:.4f} spd={:.3f} up={} sideways={} ground={} dashing={} "
            "dual={} warp={:.2f}",
            tag, tick, bits, (int)overlap, p->m_vehicleSize, p->m_gravity,
            p->m_playerSpeed, (int)p->m_isUpsideDown, (int)p->m_isSideways,
            (int)p->m_isOnGround, (int)p->m_isDashing,
            (int)pl->m_gameState.m_isDualMode, pl->m_gameState.m_timeWarp);
    }
}

// ---------------------------------------------------------------------------
// The per-physics-step emission point for Probe A, and (unconditionally, when
// enabled) the tail of the GJEffectManager whose containers are the whole
// point of this probe.
class $modify(GDRLProbeEffectManager, GJEffectManager) {
    void prepareMoveActions(float dt, bool intermediate) {
        if (!g_probeCmdVec && !g_probeMove) {
            GJEffectManager::prepareMoveActions(dt, intermediate);
            return;
        }

        GJEffectManager::prepareMoveActions(dt, intermediate);

        // Seek-path call (loadUpToPosition) -- not gameplay. See the block
        // comment at the top of this file for why PlayLayer::get() != null
        // alone cannot make this distinction.
        if (!g_inGameplayUpdate) return;

        auto* pl = PlayLayer::get();
        if (!pl) return;   // guard: g_inGameplayUpdate implies a live PlayLayer, but never trust that

        const long tick = std::lround(pl->m_attemptTime * kTicksPerSec);

        if (g_probeMove) {
            logMoveTriggerOnce(pl, tick);
            sampleMovedObjects(pl, tick);
        }

        if (!g_probeCmdVec) return;

        long sz[kNumCmdVecs];
        sz[0] = (long)m_unkVector518.size();
        sz[1] = (long)m_unkVector530.size();
        sz[2] = (long)m_unkVector560.size();
        sz[3] = (long)m_unkVector5b0.size();
        sz[4] = (long)m_unkVector600.size();

        // Maps: total live commands reachable through the map (sum of the
        // per-key vectors), not the key count -- see the file header comment
        // for why.
        long m5c8 = 0;
        for (auto& kv : m_unkMap5c8) m5c8 += (long)kv.second.size();
        sz[5] = m5c8;

        long m770 = 0;
        for (auto& kv : m_unkMap770) m770 += (long)kv.second.size();
        sz[6] = m770;

        for (int i = 0; i < kNumCmdVecs; i++) {
            g_cvMax[i] = std::max(g_cvMax[i], sz[i]);
        }

        bool changed = false;
        for (int i = 0; i < kNumCmdVecs; i++) {
            if (sz[i] != g_cvLast[i]) { changed = true; break; }
        }
        const bool periodic = (g_cvStep % kCmdVecLogPeriod) == 0;

        // Baseline-or-edge-or-periodic: see file header for why all three
        // are needed to make a 0->1->0 transition readable directly.
        if (!g_cvBaselinePrinted || changed || periodic) {
            logCmdVecLine("CMDVEC", tick, sz);
            g_cvBaselinePrinted = true;
        }

        for (int i = 0; i < kNumCmdVecs; i++) g_cvLast[i] = sz[i];
        g_cvStep++;
    }
};

// ---------------------------------------------------------------------------
// The gameplay/seek discriminator for Probe A (sets g_inGameplayUpdate around
// the real update()), and the tick-triggered mode-forcing for Probe B. Both
// need the same per-tick vantage point -- pl->m_attemptTime, sampled inside
// GJBaseGameLayer::update -- so they share this one hook rather than each
// installing its own on the same function.
class $modify(GDRLProbeBaseGameLayer, GJBaseGameLayer) {
    void update(float dt) {
        if (!g_probeCmdVec && !g_probeMove && !g_probeForceVehicle) {
            GJBaseGameLayer::update(dt);
            return;
        }

        if (g_probeCmdVec || g_probeMove) g_inGameplayUpdate = true;
        GJBaseGameLayer::update(dt);
        if (g_probeCmdVec || g_probeMove) g_inGameplayUpdate = false;

        if (!g_probeForceVehicle) return;

        auto* pl = PlayLayer::get();
        if (!pl) return;
        auto* p = pl->m_player1;
        if (!p) return;

        const long tick = std::lround(pl->m_attemptTime * kTicksPerSec);

        if (!g_fvFired && tick >= g_forceVehicleTick) {
            // Before: distinguishes "the flag was already set" from "the
            // toggler set it", per spec.
            logForceVehicleState("FORCEVEH-PRE", tick, pl, p);

            if (g_forceVehicle >= 1 && g_forceVehicle <= 7) {
                // noEffects=true: this probe exists to validate the flag
                // decode and the conditioning read path, not visuals or
                // portal-equivalent side effects (see file header). Whether
                // a real portal calls these with noEffects=false is
                // UNVERIFIED -- the portal call sites were not disassembled
                // for this probe -- so this deliberately does not attempt to
                // reproduce portal behaviour.
                switch (g_forceVehicle) {
                    case 1: p->toggleFlyMode(true, true);    break;   // ship
                    case 2: p->toggleRollMode(true, true);   break;   // ball
                    case 3: p->toggleBirdMode(true, true);   break;   // ufo
                    case 4: p->toggleDartMode(true, true);   break;   // wave
                    case 5: p->toggleRobotMode(true, true);  break;   // robot
                    case 6: p->toggleSpiderMode(true, true); break;   // spider
                    case 7: p->toggleSwingMode(true, true);  break;   // swing
                }
            } else if (g_forceVehicle == 0) {
                // Cube is the absence of every flag (main.cpp's
                // deriveVehicle() falls through to it). There is no
                // "toggleCubeMode" -- no toggler call is known that clears
                // every mode flag back to the fallthrough state, so forcing
                // a return to cube is UNVERIFIED, not implemented, and not
                // guessed at here.
                log::warn("[gdrl] FORCEVEH n=0 (cube) requested -- UNVERIFIED: "
                          "no known call forces a return to cube; only the "
                          "PRE/POST flag read below is meaningful this run");
            } else {
                log::error("[gdrl] FORCEVEH n={} out of range [0,7] "
                           "(expected: {})", g_forceVehicle,
                           "0=cube 1=ship 2=ball 3=ufo 4=wave 5=robot 6=spider 7=swing");
            }

            // After: same tick, so any difference from PRE is attributable
            // to the toggler call itself and not to unrelated per-tick drift.
            logForceVehicleState("FORCEVEH-POST", tick, pl, p);

            g_fvFired    = true;
            g_fvFireTick = tick;
        } else if (g_fvFired && !g_fvSettleLogged
                   && tick >= g_fvFireTick + kSettleDelayTicks) {
            logForceVehicleState("FORCEVEH-SETTLE", tick, pl, p);
            g_fvSettleLogged = true;
        }
    }
};

// ---------------------------------------------------------------------------
// Attempt-boundary summary and reset for both probes. A third resetLevel
// hook on PlayLayer, alongside main.cpp's and experiments.cpp's -- hook
// ordering between translation units is a linker detail (per the task brief
// and per main.cpp's own comment on this exact point), so this file's state
// is reset here regardless of what order the three hooks run in; none of the
// three depends on running before or after either of the others.
class $modify(GDRLProbePlayLayer, PlayLayer) {
    void resetLevel() {
        // Snapshot/log before delegating, not after: main.cpp's resetLevel
        // hook establishes why (README, "Two probe bugs worth not
        // repeating") -- reading state after the original call reads the
        // respawn, not the attempt that just ended. The values summarised
        // here are this file's own accumulators, not GD's, but the ordering
        // convention is kept for consistency and because resetLevel's own
        // internal state (which this file does not touch) could in
        // principle be reset by the call.
        if (g_probeCmdVec && g_cvStep > 0) {
            log::info(
                "[gdrl] CMDVEC-SUM lvl={} steps={:<6} maxV518={:<4} maxV530={:<4} "
                "maxV560={:<4} maxV5b0={:<4} maxV600={:<4} maxM5c8={:<4} maxM770={:<4}",
                g_gdrlLevelID, g_cvStep, g_cvMax[0], g_cvMax[1], g_cvMax[2],
                g_cvMax[3], g_cvMax[4], g_cvMax[5], g_cvMax[6]);
        }

        PlayLayer::resetLevel();

        if (g_probeCmdVec)      resetCmdVecAttempt();
        if (g_probeMove)        resetMoveAttempt();
        if (g_probeForceVehicle) resetForceVehicleAttempt();
    }
};

$execute {
    if (g_probeCmdVec) {
        log::info("[gdrl] PROBE_CMDVEC on -- logging GJEffectManager container "
                   "sizes once per gameplay physics step (period={} steps)",
                   kCmdVecLogPeriod);
    }
    if (g_probeForceVehicle) {
        log::info("[gdrl] PROBE_FORCE_VEHICLE on -- n={} ({}) at tick>={} "
                   "(NOT equivalent to a portal transition -- validates flag "
                   "decode + read path only, see probes.cpp header)",
                   g_forceVehicle, vehicleNameFor(g_forceVehicle), g_forceVehicleTick);
    }
    if (g_probeMove) {
        log::info("[gdrl] PROBE_MOVE on -- sampling m_objects after each gameplay "
                  "physics-step prepareMoveActions call");
    }
}

// ===========================================================================
// Probe C: GDRL_CENSUS=1 -- what is actually in this level?
//
// Two questions this answers, both of which currently gate other work:
//
//  1. WHERE IS THE FIRST NON-CUBE PORTAL, AND ON WHICH LEVEL. Objective A has
//     never executed a non-cube code path in-game: with no input the player
//     dies at the first spike, so ship/ball/ufo/wave/robot/spider/swing are all
//     unvalidated. Extending an injected jump sequence until it happens to
//     reach a portal is an open-ended grind; knowing the portal's x turns it
//     into a targeted one, and knowing it across several levels says which
//     level is cheapest for each vehicle.
//
//  2. DOES THIS LEVEL CONTAIN A MOVE TRIGGER AT ALL. GDRL_PROBE_CMDVEC's
//     protocol is "run a level with ONE known move trigger and watch exactly
//     one of the seven GJEffectManager containers go 0 -> 1 -> 0". Stereo
//     Madness is a 2013 level, from long before 2.0 introduced triggers. If the
//     pinned level contains none, every container stays 0 for the whole run and
//     the experiment produces a clean-looking negative that proves nothing
//     whatsoever -- this repo's signature failure mode, and one worth catching
//     before the run rather than after it.
//
// DESIGN RULE, and it is the reason this emits what it emits: the id -> meaning
// correspondence is NOT resolved here from a table anyone believes. GD's own
// GameObjectType is read live off each object, alongside its raw m_objectID and
// its x, so the correspondence is *read off a level* the same way the section
// grid factors were pinned down. The digests below group by the live
// m_objectType, never by a hardcoded id list.
//
// Likewise "is this a motion trigger" is answered by dynamic_cast to
// EffectGameObject plus that object's own m_targetGroupID / m_duration /
// m_moveOffset -- i.e. by what the object says about itself -- rather than by
// asserting that id 901 is Move.
//
// Runs once per level entry, off the section grid rather than m_objects,
// because the grid is the point of the grid. Cost is one pass at level entry
// and nothing thereafter.
// ===========================================================================

namespace {
    const bool g_census = envOn("GDRL_CENSUS");

    // GDRL_CENSUS_SWEEP=<lastId>: after censusing the current level, walk on to
    // the next main level and census that too, up to <lastId>. One launch then
    // answers "which level has a move trigger / a ship portal" across the whole
    // main-level set instead of one level per launch.
    //
    // Deliberately incompatible with GDRL_PIN_LEVEL, which exists to force the
    // game BACK to one level and would fight this on every transition. The two
    // are checked together at startup and the conflict is refused loudly rather
    // than producing a sweep that silently only ever censuses level 1.
    const int g_censusSweepTo = envInt("GDRL_CENSUS_SWEEP", 0);

    // Reset per level entry, detected off main.cpp's level id rather than off a
    // hook ordering: which of the several PlayLayer::init hooks in this project
    // runs first is a linker detail, and a census that depended on it would be
    // correct until the day the link order changed.
    int  g_censusLevel = -999;

    constexpr int kCensusMaxIdLines     = 400;   // distinct ids printed
    constexpr int kCensusMaxEffectLines = 200;   // EffectGameObjects printed

    struct IdStat {
        int   id      = 0;
        int   type    = -1;    // live GameObjectType of the first one seen
        long  count   = 0;
        float minX    = 0.f;
    };

    // Walk m_objects directly, not the section grid.
    //
    // The section walk below is not a valid instrument for triggers. It reported
    // 4 move triggers across all 21 main levels while Fingerdash's level string
    // alone holds 177 -- a 44x undercount -- because triggers are not collision
    // geometry and are not reliably bucketed into m_sections. Every conclusion
    // about whether a synthetic object "loaded" that rested on the section walk
    // is therefore unfounded, including the recorded claim that object 901 is
    // rejected by the parser.
    //
    // m_objects is the array GD itself builds from the level string, so it
    // answers the loading question directly.
    void runObjectListCensus(GJBaseGameLayer* layer) {
        if (!layer || !layer->m_objects) {
            log::error("[gdrl] OBJLIST no m_objects");
            return;
        }

        std::map<int, std::pair<int, float>> byId;   // id -> (count, first x)
        const unsigned n = layer->m_objects->count();
        for (unsigned i = 0; i < n; i++) {
            auto* obj = static_cast<GameObject*>(layer->m_objects->objectAtIndex(i));
            if (!obj) continue;
            const int id = obj->m_objectID;
            auto it = byId.find(id);
            if (it == byId.end()) byId.emplace(id, std::make_pair(1, obj->getPositionX()));
            else                  it->second.first++;
        }

        log::info("[gdrl] OBJLIST m_objects={} distinctIds={}", n, byId.size());
        for (auto const& [id, v] : byId) {
            log::info("[gdrl] OBJLIST-ID id={:<5} n={:<4} firstX={:.1f}",
                      id, v.first, v.second);
        }
    }

    void runCensus(GJBaseGameLayer* layer) {
        if (!layer) return;
        runObjectListCensus(layer);

        // objectID is a short in the bindings; 4096 covers every id GD 2.2
        // ships and the bound below is checked rather than assumed, so an id
        // outside it is reported instead of scribbling past the array.
        constexpr int kMaxId = 4096;
        std::vector<IdStat> stats(kMaxId);
        long total = 0, outOfRange = 0;

        struct EffectRow {
            int    id;
            int    type;
            float  x, y;
            int    targetGroup, centerGroup;
            float  duration, offX, offY;
            int    easing;
            bool   touch, spawn;
        };
        std::vector<EffectRow> effects;
        long effectTotal = 0;

        for (auto* column : layer->m_sections) {
            if (!column) continue;
            for (auto* bucket : *column) {
                if (!bucket) continue;
                for (auto* obj : *bucket) {
                    if (!obj) continue;
                    total++;

                    const int id = obj->m_objectID;
                    if (id < 0 || id >= kMaxId) { outOfRange++; continue; }

                    auto& s = stats[id];
                    if (s.count == 0) {
                        s.id   = id;
                        s.type = (int)obj->m_objectType;
                        s.minX = (float)obj->m_positionX;
                    } else if ((float)obj->m_positionX < s.minX) {
                        s.minX = (float)obj->m_positionX;
                    }
                    s.count++;

                    // "Is this a motion source" answered by the object rather
                    // than by an id table. dynamic_cast is the strong form of
                    // the question: it is true exactly when GD itself built an
                    // EffectGameObject, with no interpretation in between.
                    if (auto* eff = dynamic_cast<EffectGameObject*>(obj)) {
                        effectTotal++;
                        if ((long)effects.size() < kCensusMaxEffectLines) {
                            effects.push_back(EffectRow{
                                id, (int)obj->m_objectType,
                                (float)obj->m_positionX, (float)obj->m_positionY,
                                eff->m_targetGroupID, eff->m_centerGroupID,
                                eff->m_duration,
                                eff->m_moveOffset.x, eff->m_moveOffset.y,
                                (int)eff->m_easingType,
                                eff->m_isTouchTriggered, eff->m_isSpawnTriggered,
                            });
                        }
                    }
                }
            }
        }

        std::vector<IdStat> present;
        for (auto& s : stats) if (s.count > 0) present.push_back(s);
        std::sort(present.begin(), present.end(),
                  [](const IdStat& a, const IdStat& b) { return a.minX < b.minX; });

        // Cross-check the section walk against m_objects.
        //
        // Walking the grid is correct -- it is what the observation window
        // queries -- but a sparse window and a broken traversal look identical
        // from any single observation. A level opening is legitimately almost
        // empty, so "few objects" is not evidence of a bug, and "many objects"
        // is not evidence of correctness. Comparing the whole walk against the
        // array GD maintains separately is the one cheap check that tells them
        // apart. Stereo Madness measured 2384 of 2399; the shortfall is objects
        // not yet sectioned, not a traversal fault.
        const unsigned inArray = layer->m_objects ? layer->m_objects->count() : 0u;

        log::info("[gdrl] CENSUS lvl={} objects={} m_objects={} distinctIds={} "
                  "effects={} outOfRangeIds={} sections={}",
                  g_gdrlLevelID, total, (long)inArray, (long)present.size(),
                  effectTotal, outOfRange, (long)layer->m_sections.size());

        if (inArray > 0 && total < (long)inArray / 2) {
            log::error("[gdrl] CENSUS TRAVERSAL IS MISSING OBJECTS ({} of {}) -- "
                       "every window observation is incomplete",
                       total, (long)inArray);
        }

        // The raw histogram, ordered by where the id first appears, so a
        // playthrough of a known level can be read against it directly.
        int printed = 0;
        for (auto& s : present) {
            if (printed++ >= kCensusMaxIdLines) {
                log::warn("[gdrl] CENSUS truncated after {} distinct ids "
                          "({} more not shown)",
                          kCensusMaxIdLines, (long)present.size() - printed + 1);
                break;
            }
            log::info("[gdrl] CENSUS-ID id={:<5} type={:<3} n={:<5} firstX={:.1f}",
                      s.id, s.type, s.count, s.minX);
        }

        // Digest 1: the portals, grouped by GD's own GameObjectType. The type
        // constants come from Geode's GameObjectType, and the id -> type
        // mapping is read off each object at runtime, so nothing here asserts
        // that a particular id IS a ship portal -- it reports which id GD
        // labelled ShipPortal on this level, and where.
        //
        // Reported per (type, id) pair rather than per type: if two ids share a
        // type that is itself a finding, and collapsing them would hide it.
        const GameObjectType kPortalTypes[] = {
            GameObjectType::ShipPortal,        GameObjectType::CubePortal,
            GameObjectType::BallPortal,        GameObjectType::UfoPortal,
            GameObjectType::WavePortal,        GameObjectType::RobotPortal,
            GameObjectType::SpiderPortal,      GameObjectType::SwingPortal,
            GameObjectType::MiniSizePortal,    GameObjectType::RegularSizePortal,
            GameObjectType::InverseGravityPortal, GameObjectType::NormalGravityPortal,
            GameObjectType::GravityTogglePortal,
            GameObjectType::DualPortal,        GameObjectType::SoloPortal,
            GameObjectType::InverseMirrorPortal, GameObjectType::NormalMirrorPortal,
            GameObjectType::TeleportPortal,
        };
        long portalHits = 0;
        for (auto& s : present) {
            for (GameObjectType t : kPortalTypes) {
                if (s.type != (int)t) continue;
                portalHits++;
                log::info("[gdrl] CENSUS-PORTAL type={:<3} id={:<5} n={:<4} firstX={:.1f}",
                          s.type, s.id, s.count, s.minX);
            }
        }
        if (portalHits == 0) {
            // Loud, because "no portal lines" and "the census did not run" look
            // identical in a log otherwise, and this is exactly the recon
            // result Objective A is waiting on.
            log::warn("[gdrl] CENSUS-PORTAL none on lvl={}: every non-cube "
                      "vehicle is unreachable on this level, so it cannot "
                      "validate Objective A's conditioning path.",
                      g_gdrlLevelID);
        }

        // Digest 2: the motion sources. m_targetGroupID != 0 with a nonzero
        // m_duration or m_moveOffset is what a move/rotate trigger looks like
        // from its own fields; the raw values are printed rather than a verdict
        // so the reader can judge.
        for (auto& e : effects) {
            log::info("[gdrl] CENSUS-EFFECT id={:<5} type={:<3} pos=({:.1f},{:.1f}) "
                      "target={:<5} center={:<5} dur={:.3f} off=({:.1f},{:.1f}) "
                      "easing={} touch={} spawn={}",
                      e.id, e.type, e.x, e.y, e.targetGroup, e.centerGroup,
                      e.duration, e.offX, e.offY, e.easing,
                      (int)e.touch, (int)e.spawn);
        }
        if (effectTotal == 0) {
            // The one this file exists to catch. GDRL_PROBE_CMDVEC needs a live
            // GroupCommandObject2 to appear in exactly one container; with no
            // EffectGameObject in the level none can ever be created, so all
            // seven stay 0 and the run looks like a clean negative while being
            // no evidence at all.
            log::error("[gdrl] CENSUS-EFFECT lvl={} contains ZERO "
                       "EffectGameObjects. GDRL_PROBE_CMDVEC cannot produce "
                       "evidence on this level -- every container will read 0 "
                       "for the whole run and that is not a negative result, it "
                       "is an absent experiment. Pick a level with a move "
                       "trigger.", g_gdrlLevelID);
        } else if (effectTotal > kCensusMaxEffectLines) {
            log::warn("[gdrl] CENSUS-EFFECT {} of {} shown", kCensusMaxEffectLines,
                      effectTotal);
        }

        if (g_censusSweepTo > 0 && g_gdrlLevelID > 0 && g_gdrlLevelID < g_censusSweepTo) {
            const int next = g_gdrlLevelID + 1;
            // Deferred by a frame for the same reason main.cpp defers autoplay:
            // replacing the scene from inside an update would tear down the
            // scene currently being stepped.
            Loader::get()->queueInMainThread([next] {
                // dontGetLevelString MUST be false. Passing true yields a level
                // object with 2 objects and levelLength 793 that loads, runs,
                // and censuses to a confident, meaningless zero.
                auto* lv = GameLevelManager::sharedState()->getMainLevel(next, false);
                if (!lv) {
                    log::error("[gdrl] CENSUS sweep: getMainLevel({}) returned null "
                               "-- sweep stops here", next);
                    return;
                }
                log::info("[gdrl] CENSUS sweep -> id={} '{}'", next,
                          lv->m_levelName.c_str());
                CCDirector::sharedDirector()->replaceScene(
                    PlayLayer::scene(lv, false, false));
            });
        }
    }
}

class $modify(GDRLCensusBaseGameLayer, GJBaseGameLayer) {
    void update(float dt) {
        GJBaseGameLayer::update(dt);
        if (!g_census) return;
        // Run after delegating, on the first update of a level: at PlayLayer
        // init time the section grid is not necessarily populated, and a census
        // of a half-built grid would under-report exactly the sparse trigger
        // objects it exists to find.
        if (g_censusLevel == g_gdrlLevelID) return;
        g_censusLevel = g_gdrlLevelID;
        runCensus(this);
    }
};

$execute {
    if (g_censusSweepTo > 0 && envOn("GDRL_PIN_LEVEL")) {
        log::error("[gdrl] CENSUS_SWEEP={} and GDRL_PIN_LEVEL are both set. The "
                   "level pin forces the game back to one level on every entry, "
                   "so the sweep would census level 1 repeatedly and look like a "
                   "completed sweep. Unset GDRL_PIN_LEVEL for census runs.",
                   g_censusSweepTo);
    }
    if (g_census) {
        log::info("[gdrl] CENSUS on (sweepTo={})", g_censusSweepTo);
    }
}
