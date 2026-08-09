// GDRL Probe
//
// Phase 0 (done): mod builds universal, loads, and can hook.
// Phase 1 (here): read live game state and work out how to query a local window
//                 of geometry around the player.
//
// The section grid is a 2D uniform spatial index the game already maintains, and
// re-buckets when triggers move objects. Rather than guess how world coordinates
// map onto section indices, this logs the factors alongside the player position
// and the game's own active-window indices, so the mapping can be read off a real
// level instead of assumed.

#include <Geode/Geode.hpp>
#include <Geode/modify/PlayLayer.hpp>
#include <Geode/modify/MenuLayer.hpp>
#include <Geode/modify/GJBaseGameLayer.hpp>

#include <algorithm>

using namespace geode::prelude;

namespace {
    int    g_frame        = 0;
    bool   g_dumpedGrid   = false;
    float  g_dtMin        = 1e9f;
    float  g_dtMax        = 0.f;
    int    g_dtSamples    = 0;
    double g_dtSum        = 0.0;

    void resetProbeState() {
        g_frame      = 0;
        g_dumpedGrid = false;
        g_dtMin      = 1e9f;
        g_dtMax      = 0.f;
        g_dtSamples  = 0;
        g_dtSum      = 0.0;
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
// Expect 'Player' and 0 stars. A real username or a real star count here means
// the sandbox is reading the real save and isolation has regressed.
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

            // Drive straight into a level when asked. Training needs thousands of
            // episode resets, so the mod has to own level entry -- clicking
            // through menus is not an option. Gated on an env var so an ordinary
            // launch still reaches the menu normally.
            //
            // Deferred by a frame: replacing the scene from inside MenuLayer::init
            // would tear down the scene currently being constructed.
            if (const char* want = std::getenv("GDRL_AUTOPLAY"); want && *want == '1') {
                Loader::get()->queueInMainThread([] {
                    auto* level = GameLevelManager::sharedState()->getMainLevel(1, true);
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

        resetProbeState();
        log::info("[gdrl] === level '{}' ===", level->m_levelName.c_str());
        return true;
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
        }

        // Once a second at 60fps. Logging both candidate mappings (divide vs
        // multiply) against the game's own active-window indices reveals which
        // convention the factors follow without having to guess.
        if (g_frame++ % 60 == 0) {
            const float px = p->getPositionX();
            const float py = p->getPositionY();

            // m_vehicleSize is the mini/normal scale and m_playerSpeed the speed
            // multiplier -- both feed the observation window, which must cover a
            // constant time horizon rather than a constant distance.
            log::info(
                "[gdrl] f={:<5} dt={:.5f} pos=({:.1f},{:.1f}) yv={:+.2f} "
                "ground={} ship={} up={} size={:.2f} spd={:.2f}",
                g_frame, dt, px, py, p->m_yVelocity,
                (int)p->m_isOnGround, (int)p->m_isShip,
                (int)p->m_isUpsideDown, p->m_vehicleSize, p->m_playerSpeed);

            log::info(
                "[gdrl]   active x=[{}..{}] y=[{}..{}] | x/xf={:.2f} x*xf={:.2f} "
                "| y/yf={:.2f} y*yf={:.2f}",
                m_leftSectionIndex, m_rightSectionIndex,
                m_bottomSectionIndex, m_topSectionIndex,
                m_sectionXFactor != 0.f ? px / m_sectionXFactor : -1.f,
                px * m_sectionXFactor,
                m_sectionYFactor != 0.f ? py / m_sectionYFactor : -1.f,
                py * m_sectionYFactor);
        }
    }
};
