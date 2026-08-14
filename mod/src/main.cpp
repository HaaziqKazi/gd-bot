// GDRL Probe
//
// Phase 0 (done): mod builds universal, loads, and can hook.
// Phase 1 (done-ish): live player state + section grid decoded.
// Phase 2a (here): does the same input sequence produce the same outcome?
//
// This is the load-bearing question for the whole project. The plan treats GD as
// a perfect forward model -- search over input sequences, replay, distill. All of
// that collapses if replays are not reproducible.
//
// The worry is concrete: update() receives real wall-clock frame time (~0.0084s,
// jittering, on a 120Hz display), not a fixed timestep. If physics integrates raw
// dt, trajectories drift with rendering load.
//
// Test: "no input" is a valid fixed input sequence. The player runs into the first
// spike and GD auto-retries, so we get many independent attempts of the same
// input for free -- no input injection needed yet. If physics is genuinely
// fixed-step, every attempt must die at a bit-identical position even though dt
// varies between them. Logging dt spread alongside the death position tests
// exactly that: same outcome despite different frame timing.

#include <Geode/Geode.hpp>
#include <Geode/modify/PlayLayer.hpp>
#include <Geode/modify/MenuLayer.hpp>
#include <Geode/modify/GJBaseGameLayer.hpp>
#include <Geode/modify/PlayerObject.hpp>
#include <Geode/modify/CCKeyboardDispatcher.hpp>
#include <Geode/modify/CCTouchDispatcher.hpp>

#include "nonap.hpp"
#include "synth.hpp"
#include "level_dump.hpp"

#include <algorithm>
#include <cmath>
#include <cstdlib>

using namespace geode::prelude;

// External linkage on purpose: experiments.cpp sets these to mark its own
// injected input, so the null-input guard below can let it through while still
// blocking stray human keypresses. Both are single-threaded, touched only from
// the game thread inside update().
bool g_gdrlInjecting     = false;
long g_gdrlInjectPending = 0;

// The attempt metrics, exported so experiments.cpp can print an outcome line
// that carries its own maxX and its own validity verdict instead of having to
// be correlated against the ATTEMPT line by log position. Two hooks on
// PlayLayer::resetLevel fire in an order that is a linker detail; a per-attempt
// record split across both of them would be silently mis-paired the day that
// order changed.
float g_gdrlMaxX     = 0.f;   // furthest x reached in the current attempt
long  g_gdrlBlocked  = 0;     // pushes dropped at queueButton this attempt
long  g_gdrlLeaked   = 0;     // pushes that reached the player anyway
int   g_gdrlLevelID  = -1;    // id of the level the current attempt is running on
long  g_gdrlUiEvents = 0;     // UI-level key/touch events swallowed, this attempt
long  g_gdrlUiTotal  = 0;     // ... and over the whole run

// Fold the live player position into g_gdrlMaxX. Idempotent, so both resetLevel
// hooks may call it and whichever runs first fixes the value for both.
void gdrlSnapshotMaxX();

namespace {
    int    g_attempt      = 0;
    int    g_frame        = 0;
    bool   g_dumpedGrid   = false;

    // dt spread within the current attempt
    float  g_dtMin        = 1e9f;
    float  g_dtMax        = 0.f;
    double g_dtSum        = 0.0;
    int    g_dtSamples    = 0;

    bool envOn(const char* name) {
        const char* v = std::getenv(name);
        return v && *v == '1';
    }

    // getenv on every physics tick is a linear scan of environ and is not
    // thread-safe against setenv. Read the switches once.
    const bool g_verbose  = envOn("GDRL_VERBOSE");

    // ---------------------------------------------------------------------
    // The null-input guard.
    //
    // Every determinism result in this repo rests on "no input at all" being a
    // perfectly repeatable input sequence. It is not, on a machine anyone is
    // using: GD captures the keyboard and mouse whenever it has focus, and a
    // single stray click is a jump. That silently contaminated four runs here --
    // 195 button events, attempts sailing past the first spike to x=3790 and
    // x=7967 instead of dying at 507.615234375. It reads as a physics anomaly
    // and is nothing of the kind.
    //
    // So the premise is enforced rather than assumed, and every ATTEMPT line
    // carries its own validity verdict. A null-input measurement that does not
    // assert leaked=0 is not evidence.
    const bool g_blockInput = envOn("GDRL_BLOCK_INPUT");

    // ---------------------------------------------------------------------
    // The level pin.
    //
    // GDRL_BLOCK_INPUT stops stray input from becoming a *jump*. It does not
    // stop stray input from being a UI event, and that turned out to matter:
    // during a sequence search the game left Stereo Madness for the menu, spent
    // eight seconds there, came back, then loaded **Back On Track** and kept
    // producing perfectly clean-looking `input[clean blocked=0 leaked=0]`
    // attempts against different geometry. Two of them landed in the same sweep
    // group as real ones and looked exactly like nondeterminism.
    //
    // The guard therefore has to cover the whole session, not just the button:
    //   - swallow keyboard and touch events outright (nothing legitimate needs
    //     them; all our input arrives via queueButton),
    //   - neuter onQuit and pauseGame, the two exits from PlayLayer,
    //   - re-enter the pinned level if the game somehow reaches the menu anyway,
    //   - and stamp the level id onto every attempt so a measurement taken on
    //     the wrong level is identifiable after the fact rather than plausible.
    //
    // The last one is the important one. A blocked exit that silently failed
    // would leave no trace; a level id on every line cannot.
    const bool g_pinLevel = envOn("GDRL_PIN_LEVEL");
    const int  g_pinnedID = 1;   // Stereo Madness

    // Blocking drops pushes only. GD calls releaseButton internally on death and
    // reset -- 31 call sites against 3 for pushButton -- and swallowing those
    // risks leaving a button latched down, which would be a worse bug than the
    // one being fixed.
    const char* inputVerdict() {
        if (!g_blockInput)      return "UNGUARDED";
        if (g_gdrlLeaked > 0)  return "INVALID";
        return "clean";
    }

    // Forward decl: the conditioning edge detector is armed per attempt, so
    // every attempt logs the regime it starts in rather than only the changes.
    void resetCondEdge();

    void resetAttemptStats() {
        g_gdrlBlocked = 0;
        g_gdrlLeaked  = 0;
        g_gdrlUiEvents = 0;
        g_frame     = 0;
        g_gdrlMaxX      = 0.f;
        g_dtMin     = 1e9f;
        g_dtMax     = 0.f;
        g_dtSum     = 0.0;
        g_dtSamples = 0;
        resetCondEdge();
    }

    // ---------------------------------------------------------------------
    // Objective A: the conditioning state.
    //
    // GD is eight games sharing a renderer. The same geometry means different
    // things -- lethal vs irrelevant -- depending on which vehicle is active,
    // which way gravity points, how big the hitbox is, and how fast the world
    // scrolls. A policy therefore cannot use a fixed action mapping; it has to
    // be conditioned on this state.
    //
    // Everything below is read straight off PlayerObject/GJBaseGameLayer rather
    // than inferred from portals passed, so it stays correct through triggers,
    // mid-level respawns and checkpoint restores.
    // ---------------------------------------------------------------------

    enum class Vehicle : int {
        Cube = 0, Ship = 1, Ball = 2, Ufo = 3,
        Wave = 4, Robot = 5, Spider = 6, Swing = 7,
    };

    const char* vehicleName(Vehicle v) {
        switch (v) {
            case Vehicle::Cube:   return "cube";
            case Vehicle::Ship:   return "ship";
            case Vehicle::Ball:   return "ball";
            case Vehicle::Ufo:    return "ufo";
            case Vehicle::Wave:   return "wave";
            case Vehicle::Robot:  return "robot";
            case Vehicle::Spider: return "spider";
            case Vehicle::Swing:  return "swing";
        }
        return "?";
    }

    // Cube is the absence of every flag, so it is the fallthrough.
    //
    // Order is deliberate and defensive. The derived modes are checked before
    // the ones they derive from: swing is ship-like and spider is ball-like,
    // and if GD leaves the parent flag set while the child is active, checking
    // the parent first would silently report the wrong vehicle -- a wrong
    // conditioning label that still trains, which is the failure mode this repo
    // keeps running into. modeFlagBits() below makes any overlap visible rather
    // than letting this ordering quietly paper over it.
    Vehicle deriveVehicle(PlayerObject* p) {
        if (p->m_isSwing)  return Vehicle::Swing;
        if (p->m_isSpider) return Vehicle::Spider;
        if (p->m_isRobot)  return Vehicle::Robot;
        if (p->m_isDart)   return Vehicle::Wave;
        if (p->m_isBird)   return Vehicle::Ufo;
        if (p->m_isBall)   return Vehicle::Ball;
        if (p->m_isShip)   return Vehicle::Ship;
        return Vehicle::Cube;
    }

    unsigned modeFlagBits(PlayerObject* p) {
        return (unsigned(p->m_isShip)   << 0) | (unsigned(p->m_isBall)   << 1)
             | (unsigned(p->m_isBird)   << 2) | (unsigned(p->m_isDart)   << 3)
             | (unsigned(p->m_isRobot)  << 4) | (unsigned(p->m_isSpider) << 5)
             | (unsigned(p->m_isSwing)  << 6);
    }

    struct CondState {
        Vehicle vehicle     = Vehicle::Cube;
        bool    upsideDown  = false;
        bool    sideways    = false;
        bool    mini        = false;
        bool    dual        = false;
        bool    onGround    = false;
        bool    dashing     = false;
        float   vehicleSize = 1.f;
        float   playerSpeed = 0.9f;
        double  gravity     = 1.0;
        float   timeWarp    = 1.f;

        // Only the axes that change how inputs must be interpreted. Cosmetic
        // and continuously-varying quantities (position, velocity) belong in
        // the observation, not the conditioning vector.
        bool differsFrom(const CondState& o) const {
            return vehicle    != o.vehicle
                || upsideDown != o.upsideDown
                || sideways   != o.sideways
                || mini       != o.mini
                || dual       != o.dual
                || vehicleSize != o.vehicleSize
                || playerSpeed != o.playerSpeed
                || gravity     != o.gravity
                || timeWarp    != o.timeWarp;
        }
    };

    CondState readCond(GJBaseGameLayer* layer, PlayerObject* p) {
        CondState s;
        s.vehicle     = deriveVehicle(p);
        s.upsideDown  = p->m_isUpsideDown;
        s.sideways    = p->m_isSideways;
        s.onGround    = p->m_isOnGround;
        s.dashing     = p->m_isDashing;
        s.vehicleSize = p->m_vehicleSize;
        s.mini        = p->m_vehicleSize < 0.9f;   // 1.0 normal, 0.6 mini
        s.playerSpeed = p->m_playerSpeed;
        s.gravity     = p->m_gravity;
        // NOT `m_player2 != nullptr`. GD allocates the second PlayerObject
        // unconditionally and merely hides it outside dual sections, so the
        // pointer test reports dual=1 on every level including Stereo Madness.
        // Caught by the COND log on the first real run.
        s.dual        = layer->m_gameState.m_isDualMode;
        s.timeWarp    = layer->m_gameState.m_timeWarp;
        return s;
    }

    // Last conditioning state seen, for edge detection.
    CondState g_cond;
    bool      g_condInit = false;

    void resetCondEdge() {
        g_cond     = CondState{};
        g_condInit = false;
    }

    // The physics-tick index for a log line.
    //
    // NOT m_currentStep. That field was logged as `step=` on the COND and MODE
    // lines under a comment claiming it gave tick-exact attribution, and it
    // never did: it is CONSTANT across an attempt while x runs from 0 to 4468.
    // In six of the eight logs kept in backups/reference-logs it is 0 on every
    // COND line; in the other two (the same run) it is 0 at x=0.000 and 416 on
    // all six later lines, x=1163.871 through x=4468.861. Either way it dates
    // nothing. In the 2.2081 bindings it sits in GJBaseGameLayer's replay/record
    // block, between m_replayRandSeed and m_queuedButtons -- a recording cursor,
    // not a physics counter. (Why 416 rather than 0 in that run is not known and
    // was not chased; it is not a tick either way.)
    //
    // lround(m_attemptTime * 240) is the repo's established tick clock and the
    // one every other file here uses (probes.cpp, experiments.cpp,
    // telemetry.cpp:1037). Never `t == n/240.0`: GD accumulates a float32 1/240
    // into a double, so the sum is never exactly n/240.
    //
    // Validated live on 2026-08-14 (backups/reference-logs/
    // mode-tick-fixed-20260814-133745.log): across the synth level's five speed
    // segments the implied dx/dtick is 1.298340, 1.616995, 1.955795, 2.384043
    // and 1.046827 units/tick, i.e. 311.60 / 388.08 / 469.39 / 572.17 / 251.24
    // units/s against the 311.58 / 387.42 / 468.00 / 576.00 / 251.16 the README
    // lists -- within 0.7%, and within 0.03% on the two segments long enough for
    // the per-frame sampling of the endpoints not to dominate. That is a check
    // of the CLOCK, not of the speed constants.
    //
    // -1 means "no PlayLayer", which is a real state (menus, editor) and is
    // reported rather than papered over with 0 -- 0 is a legitimate tick.
    constexpr double kTicksPerSec = 240.0;

    long gdrlTick() {
        auto* pl = PlayLayer::get();
        return pl ? std::lround(pl->m_attemptTime * kTicksPerSec) : -1;
    }
}

void gdrlSnapshotMaxX() {
    if (auto* pl = PlayLayer::get()) {
        if (auto* p = pl->m_player1) {
            g_gdrlMaxX = std::max(g_gdrlMaxX, p->getPositionX());
        }
    }
}

$execute {
    log::info("[gdrl] probe mod loaded");

    // Insurance against a stray focus loss mid-run. Not load-bearing -- windowed
    // mode is what actually restores full speed -- but a run that silently drops
    // to ~1fps partway through is exactly the kind of quiet corruption this repo
    // keeps getting bitten by, and this makes it not happen.
    //
    // Opt-in rather than defaulted: holding a user-initiated activity also
    // inhibits idle system sleep, which should not happen to someone who merely
    // launched the game to play it.
    if (envOn("GDRL_NO_APP_NAP")) {
        const bool ok = gdrl::disableAppNap("gdrl unattended run");
        log::info("[gdrl] App Nap suppression: {}", ok ? "ACTIVE" : "FAILED");
    }

    // Settles whether the sandbox actually isolates saves. Watching for writes is
    // unreliable -- a run that simply does not save looks identical to success --
    // so ask cocos which paths it resolved instead. All of these must sit inside
    // sandbox/home.
    log::info("[gdrl] HOME            = {}",
              std::getenv("HOME") ? std::getenv("HOME") : "(unset)");
    log::info("[gdrl] writable path   = {}",
              CCFileUtils::sharedFileUtils()->getWritablePath().c_str());
    // getWritablePath2 is RobTop's addition, not stock cocos.
    log::info("[gdrl] writable path 2 = {}",
              CCFileUtils::sharedFileUtils()->getWritablePath2().c_str());
    log::info("[gdrl] mod save dir    = {}",
              Mod::get()->getSaveDir().string());
}

// Save-isolation regression check, deliberately read-only.
//
// Testing the *read* path is the safe way to prove isolation. Forcing a save
// would also be decisive, but if redirection were ever half-working (reading the
// sandbox, writing the real home) it would overwrite a real profile with an empty
// one. Reads and writes share the same path computation, so this answers the same
// question with nothing at stake.
//
// Expect 'Player' and 0 stars. A real username or star count here means the
// sandbox is reading the real save and isolation has regressed.
class $modify(GDRLMenuLayer, MenuLayer) {
    bool init() {
        if (!MenuLayer::init()) return false;

        gdrl::dumpRequestedMainLevel();

        static bool logged = false;
        // Autoplay used to fire only on the first MenuLayer. Reaching the menu a
        // second time was therefore terminal for a run: the harness sat in the
        // menu for the rest of its wall clock. Re-arming makes an escape to the
        // menu self-correcting instead.
        if (!logged || g_pinLevel) {
            if (!logged) {
            logged = true;
            log::info("[gdrl] ISOLATION playerName = '{}'",
                      GameManager::sharedState()->m_playerName.c_str());
            log::info("[gdrl] ISOLATION stars      = {}",
                      GameStatsManager::sharedState()->getStat("6"));

            // Force windowed, once, at startup.
            //
            // GD starts fullscreen, which puts it on its own Space. Switching
            // away then makes it fully occluded, and macOS stops refreshing
            // occluded windows regardless of App Nap. Measured on Stereo
            // Madness, ~90s runs, maxX bit-identical throughout:
            //
            //   unfocused, nothing done          0 attempts (player never moves)
            //   unfocused, App Nap suppressed    3 attempts  (~12x slow)
            //   windowed + focused              32 attempts  0.41/s
            //   focused fullscreen (baseline)   37 attempts  0.41/s
            //
            // So windowed costs nothing against the baseline and leaves the rest
            // of the screen usable. App Nap suppression alone is NOT sufficient;
            // the residual throttle is occlusion, a separate mechanism.
            if (envOn("GDRL_WINDOWED")) {
                Loader::get()->queueInMainThread([] {
                    PlatformToolbox::toggleFullScreen(false, false, false);
                    log::info("[gdrl] forced windowed mode");
                });
            }
            } else {
                log::warn("[gdrl] MENU REACHED mid-run -- re-entering pinned level");
            }

            // Training needs thousands of episode resets, so the mod has to own
            // level entry -- clicking through menus is not an option. Gated on an
            // env var so an ordinary launch still reaches the menu normally.
            //
            // Deferred by a frame: replacing the scene from inside MenuLayer::init
            // would tear down the scene currently being constructed.
            if (envOn("GDRL_AUTOPLAY")) {
                Loader::get()->queueInMainThread([] {
                    // Second arg is dontGetLevelString. Passing true yields a
                    // level object with no content: 2 objects, levelLength 793,
                    // nothing to collide with. It still loads and still "runs",
                    // so every measurement taken against it looks plausible and
                    // is meaningless. Must be false.
                    // GDRL_SYNTH swaps in a generated level. The main levels
                    // contain no reachable move trigger and no speed portals at
                    // all, so the remaining trigger measurements cannot be taken
                    // on them -- see synth.hpp.
                    GJGameLevel* level = envOn("GDRL_SYNTH")
                        ? gdrl::makeSyntheticLevel()
                        : GameLevelManager::sharedState()->getMainLevel(1, false);
                    if (!level) {
                        log::error("[gdrl] level construction returned null");
                        return;
                    }
                    log::info("[gdrl] autoplay -> '{}'", level->m_levelName.c_str());
                    CCDirector::sharedDirector()->replaceScene(
                        PlayLayer::scene(level, false, false));
                });
            }
        }
        return true;
    }
};

class $modify(GDRLPlayLayer, PlayLayer) {
    bool init(GJGameLevel* level, bool useReplay, bool dontCreateObjects) {
        // Reset *before* delegating. PlayLayer::init calls resetLevel() before
        // it returns, which reaches the hook below; with the old ordering that
        // fired against the previous level's counters and emitted a fabricated
        // ATTEMPT row on every level entry after the first.
        g_attempt    = 0;
        g_dumpedGrid = false;
        resetAttemptStats();

        if (!PlayLayer::init(level, useReplay, dontCreateObjects)) return false;

        g_gdrlLevelID = level->m_levelID;
        log::info("[gdrl] === level '{}' id={} ===",
                  level->m_levelName.c_str(), g_gdrlLevelID);

        // A run that wandered onto a different level kept producing attempts
        // that looked clean. Say so loudly and go back.
        if (g_pinLevel && g_gdrlLevelID != g_pinnedID) {
            log::error("[gdrl] WRONG LEVEL id={} ('{}') -- every attempt from here "
                       "is invalid; returning to id={}",
                       g_gdrlLevelID, level->m_levelName.c_str(), g_pinnedID);
            Loader::get()->queueInMainThread([] {
                auto* lv = GameLevelManager::sharedState()->getMainLevel(g_pinnedID, false);
                if (lv) {
                    CCDirector::sharedDirector()->replaceScene(
                        PlayLayer::scene(lv, false, false));
                }
            });
        }
        return true;
    }

    // The two ways out of PlayLayer, both closed while pinned. If the exit path
    // is neither of these the level id stamped on every attempt still catches
    // it -- this is belt and braces, not a claim to have enumerated them.
    void onQuit() {
        if (g_pinLevel) {
            log::warn("[gdrl] onQuit suppressed (level pinned)");
            return;
        }
        PlayLayer::onQuit();
    }

    void pauseGame(bool unfocused) {
        if (g_pinLevel) {
            log::warn("[gdrl] pauseGame({}) suppressed (level pinned)", (int)unfocused);
            return;
        }
        PlayLayer::pauseGame(unfocused);
    }

    // The determinism measurement, taken at the attempt boundary.
    //
    // Printed at maximum precision on purpose: the question is not "roughly the
    // same place" but "bit-identical". A drift of 1e-4 across attempts would still
    // mean replays diverge, and would still sink search-based planning -- it would
    // just take longer to notice.
    //
    // destroyPlayer turned out to be unusable for this: it fires every physics
    // tick with a constant killer id, which is not death semantics and suggests a
    // mis-mapped binding. resetLevel is called once per attempt and needs no
    // assumptions about GD's death plumbing, so the summary is emitted here from
    // state accumulated in update().
    //
    // ATTEMPT lines are the determinism data. Across attempts of an identical
    // input sequence (here: no input at all), maxX and t must be bit-identical
    // while the dt spread varies. Same outcome despite different frame timing is
    // exactly what fixed-step physics predicts.
    void resetLevel() {
        // Capture the final position at the attempt boundary, before delegating.
        //
        // g_gdrlMaxX is otherwise sampled once per render frame in update(), which
        // makes it a function of the frame rate rather than of the simulation:
        // at 32 ticks/frame the player advances ~41.5 units between samples, and
        // the last pre-death sample can miss the true endpoint. Skipping the
        // respawn animation exposed exactly that -- maxX read 498.527496338
        // instead of 507.615234375, short by 9.087738037 units, which is 6.999988
        // physics ticks. The simulation was identical; t matched to the bit. Only
        // the sampling had changed.
        //
        // Before delegating, for the reason in "Two probe bugs worth not
        // repeating": after the original, this reads the respawn, not the death.
        gdrlSnapshotMaxX();

        const double dtMean = g_dtSamples ? g_dtSum / g_dtSamples : 0.0;
        if (g_frame > 0) {
            log::info(
                "[gdrl] ATTEMPT {:<3} lvl={} maxX={:.9f} t={:.9f} frames={:<6} "
                "dt[min={:.6f} max={:.6f} mean={:.6f}] "
                "input[{} blocked={} leaked={} ui={} uiTot={}]",
                g_attempt, g_gdrlLevelID, g_gdrlMaxX, m_attemptTime, g_frame,
                g_dtMin, g_dtMax, dtMean,
                inputVerdict(), g_gdrlBlocked, g_gdrlLeaked, g_gdrlUiEvents,
                g_gdrlUiTotal);
        }

        PlayLayer::resetLevel();

        g_attempt++;
        resetAttemptStats();
    }
};

// Hooked on GJBaseGameLayer rather than PlayLayer: in GD 2.2 the player, object
// list, section grid and update loop all live on the base class.
class $modify(GDRLBaseGameLayer, GJBaseGameLayer) {
    // The guard blocks here rather than at handleButton, because handleButton
    // cannot tell injected input from human input. The call graph is:
    //
    //   queueButton -> [queue] -> processQueuedButtons -> handleButton -> pushButton
    //
    // and handleButton's only two call sites are both inside
    // processQueuedButtons, so everything converges before it. queueButton is
    // the last point where the caller is still distinguishable -- deliberate
    // injection sets g_gdrlInjecting synchronously around its own call, and
    // anything else is a stray human keypress.
    void queueButton(int button, bool push, bool isPlayer2, double timestamp) {
        if (g_blockInput && !g_gdrlInjecting) {
            g_gdrlBlocked++;
            return;
        }
        // Injected pushes are expected to surface at pushButton a frame or so
        // later, when the queue drains. Count them so the leak detector can tell
        // them apart from input that arrived by an unmapped route.
        if (g_gdrlInjecting && push) g_gdrlInjectPending++;
        GJBaseGameLayer::queueButton(button, push, isPlayer2, timestamp);
    }

    void update(float dt) {
        GJBaseGameLayer::update(dt);

        auto* p = m_player1;
        if (!p) return;

        g_dtMin = std::min(g_dtMin, dt);
        g_dtMax = std::max(g_dtMax, dt);
        g_dtSum += dt;
        g_dtSamples++;
        g_frame++;
        g_gdrlMaxX = std::max(g_gdrlMaxX, p->getPositionX());

        if (!g_dumpedGrid) {
            g_dumpedGrid = true;

            size_t nonNullCols = 0, maxColHeight = 0, totalBuckets = 0;
            for (auto* col : m_sections) {
                if (!col) continue;
                nonNullCols++;
                maxColHeight = std::max(maxColHeight, col->size());
                for (auto* bucket : *col) {
                    if (bucket) totalBuckets++;
                }
            }

            log::info("[gdrl] --- section grid ---");
            log::info("[gdrl] columns (x)      = {}", m_sections.size());
            log::info("[gdrl] non-null columns = {}", nonNullCols);
            log::info("[gdrl] max column (y)   = {}", maxColHeight);
            log::info("[gdrl] non-null buckets = {}", totalBuckets);
            log::info("[gdrl] sectionXFactor   = {}", m_sectionXFactor);
            log::info("[gdrl] sectionYFactor   = {}", m_sectionYFactor);
            log::info("[gdrl] levelLength      = {}", m_levelLength);
            log::info("[gdrl] object count     = {}", m_objects ? m_objects->count() : 0u);

            // Fail loudly on an empty level. An empty level runs happily and
            // produces measurements that look real, which cost a whole debugging
            // cycle once already. Stereo Madness is ~2400 objects; anything in
            // single digits means the level string never loaded.
            const unsigned objCount = m_objects ? m_objects->count() : 0u;
            if (objCount < 10) {
                log::error("[gdrl] LEVEL LOOKS EMPTY ({} objects, length {}) -- "
                           "level string almost certainly did not load. Any "
                           "measurement from this run is invalid.",
                           objCount, m_levelLength);
            }
        }

        // --- Objective A: conditioning-state edge log ---------------------
        //
        // Sampled per render frame rather than per physics tick, which is fine
        // *for cataloguing*: a regime change persists for many ticks, so no
        // transition can hide between two samples. `tick=` therefore dates the
        // frame that NOTICED the change, not the tick that caused it -- it is an
        // upper bound, loose by up to one frame's worth of ticks (32 at the
        // speeds this runs at). The MODE line below is the tighter one: it fires
        // inside the transition itself, so it is exact to within the one-tick
        // question telemetry.cpp's test list still has open -- whether GD
        // advances m_attemptTime before or after the physics step. Do not read
        // either as sub-tick attribution.
        //
        // `tick=` was `step=` (m_currentStep) until 2026-08-14, which was a
        // constant for the whole attempt and dated nothing. See gdrlTick().
        //
        // Emitted on change only. A level is a few dozen regime changes, so
        // this is a complete record of the conditioning trajectory at a cost
        // that does not perturb the frame budget.
        const CondState cur = readCond(this, p);
        if (!g_condInit || cur.differsFrom(g_cond)) {
            const unsigned bits = modeFlagBits(p);

            // Exactly zero (cube) or one vehicle flag should ever be set. More
            // than one means deriveVehicle's ordering is load-bearing rather
            // than merely defensive, and the labels it produces are a guess.
            if (bits & (bits - 1)) {
                log::error("[gdrl] OVERLAPPING VEHICLE FLAGS bits=0b{:07b} -- "
                           "vehicle label '{}' is not trustworthy",
                           bits, vehicleName(cur.vehicle));
            }

            log::info(
                "[gdrl] COND tick={:<7} x={:.3f} {:<6} grav={} size={:.2f} "
                "spd={:.2f} gmul={:.2f} warp={:.2f} dual={} sideways={}",
                gdrlTick(), p->getPositionX(), vehicleName(cur.vehicle),
                cur.upsideDown ? "up" : "dn", cur.vehicleSize, cur.playerSpeed,
                cur.gravity, cur.timeWarp, (int)cur.dual, (int)cur.sideways);

            g_cond     = cur;
            g_condInit = true;
        }

        // Periodic state dump, off by default -- it drowns the ATTEMPT and
        // COND lines, which are the actual data.
        if (g_verbose && g_frame % 60 == 0) {
            log::info(
                "[gdrl] f={:<5} tick={:<7} dt={:.5f} pos=({:.1f},{:.1f}) "
                "yv={:+.2f} {} ground={} up={} size={:.2f} spd={:.2f}",
                g_frame, gdrlTick(), dt, p->getPositionX(), p->getPositionY(),
                p->m_yVelocity, vehicleName(cur.vehicle),
                (int)p->m_isOnGround, (int)p->m_isUpsideDown,
                p->m_vehicleSize, p->m_playerSpeed);
        }
    }
};

// Mode transitions, caught at the source.
//
// The COND edge log above tells you the conditioning state changed; this tells
// you what changed it. GameObjectType is logged raw because the portal-id ->
// enum mapping is not something to assume -- run a level with known portals and
// read the correspondence off the log, the same way the section grid factors
// were pinned down.
//
// Guarded on the active PlayLayer's player: PlayerObject is also instantiated
// for icon previews in menus and the garage, where these calls are meaningless.
class $modify(GDRLPlayerObject, PlayerObject) {
    // Second line of defence for the null-input guard. If a push reaches the
    // player while handleButton is being blocked, it arrived by a route not yet
    // mapped -- so it is counted and reported rather than silently swallowed,
    // and it marks the attempt INVALID. Failing loudly is the whole point: a
    // contaminated attempt that still looks plausible is what cost a full day.
    bool pushButton(PlayerButton button) {
        auto* pl = PlayLayer::get();
        if (g_blockInput && pl && (pl->m_player1 == this || pl->m_player2 == this)) {
            if (g_gdrlInjectPending > 0) {
                g_gdrlInjectPending--;   // ours, arriving as expected
            } else {
                g_gdrlLeaked++;
                log::warn("[gdrl] INPUT LEAKED past queueButton: btn={} x={:.3f}",
                          (int)button, this->getPositionX());
                return false;
            }
        }
        return PlayerObject::pushButton(button);
    }

    // WHY THIS LOGS UNCONDITIONALLY, AND WHY `after` IS NOT THE NEW VEHICLE.
    //
    // This hook fired zero times in every run ever recorded, including the runs
    // whose COND line shows a genuine cube -> ship edge at x=4468. The cause was
    // an `if (before == after) return;` guard here, and it could never be false
    // on a mode ENTRY. Read off the shipped arm64 binary (GD 2.2081), not
    // inferred:
    //
    //   GJBaseGameLayer::switchToFlyMode  (m1 0xfcebc)
    //     0x1000fcee8  bl 0x100388338   <- PlayerObject::switchedToMode, FIRST
    //     ...
    //     0x1000fcf50  cmp w22,#0x5 / b.ne ...      switch on the portal type
    //     0x1000fcf94  bl 0x10038bf50   <- toggleBirdMode(true, ...)   type 19
    //     0x1000fcfc4  bl 0x10038b4d8   <- toggleFlyMode(true, ...)    type 5
    //
    // and switchedToMode itself (0x100388338) only ever calls the toggles with
    // enable = 0 (`mov w1,#0` at 0x100388354/0x100388364/0x10038837c before each
    // bl): it CLEARS the outgoing vehicle. The flag that names the incoming one
    // is set by the caller, after this returns. So entering a ship from a cube
    // is before = cube, after = cube, and the guard swallowed it. The instrument
    // was broken, not the mechanism -- switchedToMode has 21 `bl` call sites and
    // 0 `b` ones in the arm64 slice, so it is a real call, not inlined away.
    //
    // Consequence for the reader: `cleared=` is the vehicle state after the
    // outgoing mode was turned off, which is why it is usually the same as
    // `from=`. The vehicle that was ENTERED is named by the next COND line, and
    // `type=` is the raw GameObjectType the portal passed -- logged raw because
    // the portal-id -> enum mapping is read off logs, not assumed. Measured so
    // far: type=5 at the x=4500 ship portal.
    void switchedToMode(GameObjectType type) {
        const Vehicle before = deriveVehicle(this);
        PlayerObject::switchedToMode(type);
        const Vehicle cleared = deriveVehicle(this);

        auto* pl = PlayLayer::get();
        if (!pl || (pl->m_player1 != this && pl->m_player2 != this)) return;

        log::info("[gdrl] MODE tick={:<7} x={:.3f} from={} cleared={} type={} p{}",
                  gdrlTick(), this->getPositionX(),
                  vehicleName(before), vehicleName(cleared),
                  (int)type, pl->m_player2 == this ? 2 : 1);
    }
};

// UI input, swallowed at the source while the level is pinned.
//
// queueButton stops a stray click becoming a jump. It does nothing about the
// same click hitting a menu button, and that is how a search run ended up
// running attempts on Back On Track. Nothing this harness does needs keyboard
// or touch input -- every input it issues goes through queueButton -- so under
// GDRL_PIN_LEVEL both are dropped wholesale and counted.
//
// Counted, not merely dropped: `ui=` on the ATTEMPT line staying at 0 is the
// evidence that the machine was actually idle, and a nonzero count is the
// evidence that it was not. Dropping silently would destroy exactly the signal
// that identified this problem.
class $modify(GDRLKeyboard, CCKeyboardDispatcher) {
    bool dispatchKeyboardMSG(enumKeyCodes key, bool down, bool repeat, double ts) {
        if (g_pinLevel) {
            if (down && !repeat) {
                g_gdrlUiEvents++; g_gdrlUiTotal++;
                log::warn("[gdrl] UI KEY swallowed key={} (uiTot={})",
                          (int)key, g_gdrlUiTotal);
            }
            return true;
        }
        return CCKeyboardDispatcher::dispatchKeyboardMSG(key, down, repeat, ts);
    }
};

class $modify(GDRLTouch, CCTouchDispatcher) {
    void touchesBegan(CCSet* touches, CCEvent* event) {
        if (g_pinLevel) {
            g_gdrlUiEvents++; g_gdrlUiTotal++;
            log::warn("[gdrl] UI TOUCH swallowed (uiTot={})", g_gdrlUiTotal);
            return;
        }
        CCTouchDispatcher::touchesBegan(touches, event);
    }
};
