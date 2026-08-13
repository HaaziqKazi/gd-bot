#include <Geode/Geode.hpp>

#include "synth.hpp"

#include <sstream>
#include <string>
#include <vector>

using namespace geode::prelude;

namespace {

// ---------------------------------------------------------------------------
// Level string format
//
// <header>;<object>;<object>;...
//
// Each object is a flat comma-separated list of <propertyID>,<value> pairs.
// The property numbers below are the ones this file actually uses; the rest of
// GD's several hundred are irrelevant here and deliberately not enumerated,
// because a half-remembered table is worse than none.
// ---------------------------------------------------------------------------

constexpr int kPropObjectID   = 1;
constexpr int kPropX          = 2;
constexpr int kPropY          = 3;
constexpr int kPropDuration   = 10;   // trigger duration, seconds
constexpr int kPropMoveX      = 28;
constexpr int kPropMoveY      = 29;
constexpr int kPropEasing     = 30;
constexpr int kPropTargetGrp  = 51;
constexpr int kPropGroups     = 57;   // dot-separated group membership

// Object IDs.
constexpr int kIdBlock        = 1;
constexpr int kIdMoveTrigger  = 901;
constexpr int kIdSpeedHalf    = 200;
constexpr int kIdSpeed1x      = 201;
constexpr int kIdSpeed2x      = 202;
constexpr int kIdSpeed3x      = 203;
constexpr int kIdSpeed4x      = 1334;
constexpr int kIdShipPortal   = 13;

// There is deliberately no kGroundY here any more.
//
// It was `constexpr float kGroundY = 105.f`, commented "the player spawns at
// y=105", and every object's y was written as kGroundY + something. That is a
// runtime coordinate written straight into the level string, and the level
// string is NOT in runtime coordinates: runtime m_positionY = level-string y
// + 90 (measured 4-for-4 on 2026-08-12). So the five speed portals and the ship
// portal, meant to sit on the player at runtime y=105, were emitted at
// level-string 105 and landed at runtime 195 -- 90 units above the player, with
// no overlap possible for anything under 150 units tall. m_playerSpeed stayed
// 0.8999999761581421 on all 57,009 observations of a full run past all six.
//
// Layout now lives in synth.hpp in RUNTIME y and goes through
// synth::levelStringY() on the way out, so the two coordinate systems cannot be
// confused in this file again.

struct Prop {
    int         key;
    std::string value;
};

std::string objectString(std::initializer_list<Prop> props) {
    std::ostringstream o;
    bool first = true;
    for (const auto& p : props) {
        if (!first) o << ',';
        o << p.key << ',' << p.value;
        first = false;
    }
    return o.str();
}

std::string num(float v) {
    std::ostringstream o;
    o << v;
    return o.str();
}

} // namespace

namespace gdrl {

gd::string syntheticLevelString() {
    using namespace gdrl::synth;

    std::ostringstream o;

    // Header. Minimal but complete enough that GD does not fall back to
    // defaults mid-parse: start gamemode cube (kA2), normal size (kA3), normal
    // speed (kA4), no dual (kA8), no 2-player (kA11).
    o << "kA13,0,kA15,0,kA16,0,kA14,,kA6,0,kA7,0,kA17,0,kA18,0,"
         "kA2,0,kA3,0,kA8,0,kA4,0,kA9,0,kA10,0,kA11,0";

    std::vector<std::string> objects;

    // Sacrificial first object.
    //
    // The first attempt at this level emitted the move trigger first and it was
    // the one object of nine that did not load -- everything after it landed at
    // exactly the requested x. That is the signature of the first chunk after
    // the header being consumed rather than of a malformed trigger, so a
    // throwaway block goes first and the diagnosis is checkable: if this block
    // is the one missing from the census and 901 is present, the header/object
    // boundary eats one chunk and this is the fix. If 901 is still missing, the
    // trigger's own property set is wrong and this changed nothing.
    //
    // Placed high and far left so it cannot touch the player either way.
    objects.push_back(objectString({
        {kPropObjectID, std::to_string(kIdBlock)},
        {kPropX,        "50"},
        {kPropY,        num(levelStringY(kHighBlockRuntimeY))},
    }));

    // The Probe A target: a *plain* move trigger. Neither touch (11) nor spawn
    // (62) is set, which is what makes it fire on the player crossing its own x
    // -- EffectGameObject::spawnXPosition returns getPosition().x in exactly
    // that case. Touch- and spawn-triggered variants are the ones forward
    // projection classifies as not computable, so they would not exercise the
    // path that matters.
    objects.push_back(objectString({
        {kPropObjectID,  std::to_string(kIdMoveTrigger)},
        {kPropX,         num(kMoveTriggerX)},
        {kPropY,         num(levelStringY(kMoveTriggerRuntimeY))},
        {kPropTargetGrp, "1"},
        {kPropDuration,  num(kMoveDuration)},
        {kPropMoveX,     "0"},
        {kPropMoveY,     "90"},
        {kPropEasing,    "0"},          // linear: no endpoint quirks to model
    }));

    // What the trigger moves. Group 1, well clear of the player's path so its
    // motion cannot kill the run and confound the measurement.
    objects.push_back(objectString({
        {kPropObjectID, std::to_string(kIdBlock)},
        {kPropX,        num(kMovedBlockX)},
        {kPropY,        num(levelStringY(kMovedBlockRuntimeY))},
        {kPropGroups,   "1"},
    }));

    // Speed portals, one per unmeasured bucket, spaced far enough apart that
    // the per-tick advance settles before the next one. Ordered 2x/3x/4x/0.5x
    // and back to 1x so each is entered from a different speed -- if the
    // per-tick advance depended on the previous bucket rather than the current
    // one, this ordering would show it.
    struct Portal { int id; float x; };
    for (const auto& p : std::vector<Portal>{
             {kIdSpeed2x,   kSpeed2xX},
             {kIdSpeed3x,   kSpeed3xX},
             {kIdSpeed4x,   kSpeed4xX},
             {kIdSpeedHalf, kSpeedHalfX},
             {kIdSpeed1x,   kSpeed1xX},
         }) {
        objects.push_back(objectString({
            {kPropObjectID, std::to_string(p.id)},
            {kPropX,        num(p.x)},
            {kPropY,        num(levelStringY(kPortalRuntimeY))},
        }));
    }

    // Vehicle portal last: a ship section changes the physics regime, so
    // anything after it measures something different. Nothing here depends on
    // what happens past this point except the conditioning read.
    objects.push_back(objectString({
        {kPropObjectID, std::to_string(kIdShipPortal)},
        {kPropX,        num(kShipPortalX)},
        {kPropY,        num(levelStringY(kPortalRuntimeY))},
    }));

    // Sets the level length. m_levelLength is derived from the furthest object,
    // and a level that ends the moment the last portal is crossed would cut the
    // ship section short.
    objects.push_back(objectString({
        {kPropObjectID, std::to_string(kIdBlock)},
        {kPropX,        num(kEndBlockX)},
        {kPropY,        num(levelStringY(kEndBlockRuntimeY))},
    }));

    for (const auto& obj : objects) o << ';' << obj;
    o << ';';

    return gd::string(o.str().c_str());
}

GJGameLevel* makeSyntheticLevel() {
    auto* level = GJGameLevel::create();
    if (!level) {
        log::error("[gdrl] SYNTH GJGameLevel::create() returned null");
        return nullptr;
    }

    const gd::string raw = syntheticLevelString();

    const bool compress = [] {
        const char* v = std::getenv("GDRL_SYNTH_COMPRESS");
        return v && *v == '1';
    }();

    level->m_levelName   = "GDRL Synth";
    level->m_levelString = compress
        ? ZipUtils::compressString(raw, false, 0)
        : raw;

    // Editor/local, so nothing tries to fetch it from the server.
    level->m_levelType = GJLevelType::Editor;

    log::info("[gdrl] SYNTH built level: {} objects declared, rawLen={} compress={}",
              // one per ';' after the header
              (int)std::count(raw.begin(), raw.end(), ';'),
              (int)raw.size(), compress ? 1 : 0);
    // The authored string itself, so the level-string y of every object is
    // checkable from the log against the runtime m_positionY the census prints,
    // without inferring either from the other. ~300 bytes, and only ever
    // emitted under GDRL_SYNTH=1.
    log::info("[gdrl] SYNTH raw={}", raw.c_str());

    return level;
}

} // namespace gdrl
