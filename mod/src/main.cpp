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

#include <algorithm>
#include <cstdlib>

using namespace geode::prelude;

namespace {
    int    g_attempt      = 0;
    int    g_frame        = 0;
    bool   g_dumpedGrid   = false;
    float  g_maxX         = 0.f;   // furthest x reached in the current attempt

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

    long g_inputBlocked = 0;   // pushes dropped at handleButton this attempt
    long g_inputLeaked  = 0;   // pushes that reached the player anyway

    // Blocking drops pushes only. GD calls releaseButton internally on death and
    // reset -- 31 call sites against 3 for pushButton -- and swallowing those
    // risks leaving a button latched down, which would be a worse bug than the
    // one being fixed.
    const char* inputVerdict() {
        if (!g_blockInput)      return "UNGUARDED";
        if (g_inputLeaked > 0)  return "INVALID";
        return "clean";
    }

    // Forward decl: the conditioning edge detector is armed per attempt, so
    // every attempt logs the regime it starts in rather than only the changes.
    void resetCondEdge();

    void resetAttemptStats() {
        g_inputBlocked = 0;
        g_inputLeaked  = 0;
        g_frame     = 0;
        g_maxX      = 0.f;
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
}

$execute {
    log::info("[gdrl] probe mod loaded");

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

        static bool logged = false;
        if (!logged) {
            logged = true;
            log::info("[gdrl] ISOLATION playerName = '{}'",
                      GameManager::sharedState()->m_playerName.c_str());
            log::info("[gdrl] ISOLATION stars      = {}",
                      GameStatsManager::sharedState()->getStat("6"));

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
                    auto* level = GameLevelManager::sharedState()->getMainLevel(1, false);
                    if (!level) {
                        log::error("[gdrl] getMainLevel(1) returned null");
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

        log::info("[gdrl] === level '{}' ===", level->m_levelName.c_str());
        return true;
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
        const double dtMean = g_dtSamples ? g_dtSum / g_dtSamples : 0.0;
        if (g_frame > 0) {
            log::info(
                "[gdrl] ATTEMPT {:<3} maxX={:.9f} t={:.9f} frames={:<6} "
                "dt[min={:.6f} max={:.6f} mean={:.6f}] "
                "input[{} blocked={} leaked={}]",
                g_attempt, g_maxX, m_attemptTime, g_frame,
                g_dtMin, g_dtMax, dtMean,
                inputVerdict(), g_inputBlocked, g_inputLeaked);
        }

        PlayLayer::resetLevel();

        g_attempt++;
        resetAttemptStats();
    }
};

// Hooked on GJBaseGameLayer rather than PlayLayer: in GD 2.2 the player, object
// list, section grid and update loop all live on the base class.
class $modify(GDRLBaseGameLayer, GJBaseGameLayer) {
    // The layer-level input entry point, and the only one that matters: two call
    // sites in the arm64 binary, both the input dispatcher. Dropping `down` here
    // stops a jump before it reaches either player object.
    void handleButton(bool down, int button, bool isPlayer1) {
        if (g_blockInput && down) {
            g_inputBlocked++;
            return;
        }
        GJBaseGameLayer::handleButton(down, button, isPlayer1);
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
        g_maxX = std::max(g_maxX, p->getPositionX());

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
        // transition can hide between two samples. Tick-exact attribution comes
        // from m_currentStep, logged alongside, and from the switchedToMode
        // hook below which fires on the transition itself.
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
                "[gdrl] COND step={:<7} x={:.3f} {:<6} grav={} size={:.2f} "
                "spd={:.2f} gmul={:.2f} warp={:.2f} dual={} sideways={}",
                m_currentStep, p->getPositionX(), vehicleName(cur.vehicle),
                cur.upsideDown ? "up" : "dn", cur.vehicleSize, cur.playerSpeed,
                cur.gravity, cur.timeWarp, (int)cur.dual, (int)cur.sideways);

            g_cond     = cur;
            g_condInit = true;
        }

        // Periodic state dump, off by default -- it drowns the ATTEMPT and
        // COND lines, which are the actual data.
        if (g_verbose && g_frame % 60 == 0) {
            log::info(
                "[gdrl] f={:<5} step={:<7} dt={:.5f} pos=({:.1f},{:.1f}) "
                "yv={:+.2f} {} ground={} up={} size={:.2f} spd={:.2f}",
                g_frame, m_currentStep, dt, p->getPositionX(), p->getPositionY(),
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
            g_inputLeaked++;
            log::warn("[gdrl] INPUT LEAKED past handleButton: btn={} x={:.3f}",
                      (int)button, this->getPositionX());
            return false;
        }
        return PlayerObject::pushButton(button);
    }

    void switchedToMode(GameObjectType type) {
        const Vehicle before = deriveVehicle(this);
        PlayerObject::switchedToMode(type);
        const Vehicle after  = deriveVehicle(this);

        auto* pl = PlayLayer::get();
        if (!pl || (pl->m_player1 != this && pl->m_player2 != this)) return;
        if (before == after) return;

        log::info("[gdrl] MODE step={:<7} x={:.3f} {} -> {} (objectType={}) p{}",
                  pl->m_currentStep, this->getPositionX(),
                  vehicleName(before), vehicleName(after),
                  (int)type, pl->m_player2 == this ? 2 : 1);
    }
};
