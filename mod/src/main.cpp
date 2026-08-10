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

#include <algorithm>

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

    void resetAttemptStats() {
        g_frame     = 0;
        g_maxX      = 0.f;
        g_dtMin     = 1e9f;
        g_dtMax     = 0.f;
        g_dtSum     = 0.0;
        g_dtSamples = 0;
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
        if (!PlayLayer::init(level, useReplay, dontCreateObjects)) return false;

        g_attempt    = 0;
        g_dumpedGrid = false;
        resetAttemptStats();
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
                "dt[min={:.6f} max={:.6f} mean={:.6f}]",
                g_attempt, g_maxX, m_attemptTime, g_frame,
                g_dtMin, g_dtMax, dtMean);
        }

        PlayLayer::resetLevel();

        g_attempt++;
        resetAttemptStats();
    }
};

// Hooked on GJBaseGameLayer rather than PlayLayer: in GD 2.2 the player, object
// list, section grid and update loop all live on the base class.
class $modify(GDRLBaseGameLayer, GJBaseGameLayer) {
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

        // Periodic state dump, off by default -- it drowns the DEATH lines.
        if (envOn("GDRL_VERBOSE") && g_frame % 60 == 0) {
            log::info(
                "[gdrl] f={:<5} dt={:.5f} pos=({:.1f},{:.1f}) yv={:+.2f} "
                "ground={} ship={} up={} size={:.2f} spd={:.2f}",
                g_frame, dt, p->getPositionX(), p->getPositionY(), p->m_yVelocity,
                (int)p->m_isOnGround, (int)p->m_isShip,
                (int)p->m_isUpsideDown, p->m_vehicleSize, p->m_playerSpeed);
        }
    }
};
