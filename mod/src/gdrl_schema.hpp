// GENERATED FILE -- DO NOT EDIT.
//
// Emitted by trainer/schema.py. Edit that and re-run `python3 trainer/schema.py`.
// trainer/test_schema.py regenerates this in memory and fails if it differs, so
// an edit here is a test failure rather than a silent C++/Python skew.


#pragma once

#include <cstdint>
#include <cstddef>
#include <type_traits>

// All records are little-endian, naturally aligned, no bitfields --
// the convention the README telemetry spec already fixed. Every pad byte
// is a named member and every field offset carries a static_assert, so a
// compiler that lays these out differently from the Python model fails
// the build instead of writing a wire Python will misread.

#define GDRL_MAGIC 0x4C524447u
#define GDRL_OBS_MAGIC GDRL_MAGIC   // the README spec's name for it
#define GDRL_WIRE_VERSION 1u
#define GDRL_SCHEMA_HASH 0xBEE47E250E167BC7ull
#define GDRL_TICK_HZ 240
#define GDRL_MAX_OBJECTS 256
#define GDRL_MAX_GROUPS 10
#define GDRL_MAX_COMMANDS 64
#define GDRL_MAX_PENDING 64
#define GDRL_MAX_SPEED_SEGS 16
#define GDRL_MAX_ACTIONS 8
#define GDRL_COVERAGE_COLS 64

// A derived label, NOT a wire field. The wire carries the raw flag word
// (GdrlVehicleFlag); Python derives this, so the derivation order --
// swing before ship, spider before ball -- is testable and the
// overlapping-flags case is visible instead of being papered over by the
// ordering. Declared here only so conditioning.py imports it rather than
// redeclaring it, and so the C++ side keeps a name for the log lines.
enum class GdrlVehicle : uint8_t {
    CUBE = 0,
    SHIP = 1,
    BALL = 2,
    UFO = 3,
    WAVE = 4,
    ROBOT = 5,
    SPIDER = 6,
    SWING = 7,
};

// Bit positions of PlayerObject's seven mode flags, in exactly the order
// main.cpp's modeFlagBits() already uses -- changing this order silently
// relabels every observation, so it is pinned by the schema hash.
//
// Cube is the absence of all seven and therefore has no bit. 'no bit set'
// and 'two bits set' are different observations, which is why the raw
// word crosses the wire rather than an eight-valued enum: an overlap is
// evidence that deriveVehicle's ordering is load-bearing rather than
// merely defensive, and collapsing in C++ would destroy that evidence.
enum class GdrlVehicleFlag : uint16_t {
    SHIP = 1,
    BALL = 2,
    BIRD = 4,
    DART = 8,
    ROBOT = 16,
    SPIDER = 32,
    SWING = 64,
};

// GdrlObsHeader.flags, per the README spec (bit0 input-clean, bit1
// practice mode), extended.
//
// GAMEPLAY_STEP distinguishes the gameplay emission from the level-seek
// one. GJEffectManager::prepareMoveActions has two bl sites --
// GJBaseGameLayer::update (gameplay, inside the fixed-step loop, +0x9f8,
// loop body 0x122ea4..0x123d94) and GJBaseGameLayer::loadUpToPosition
// (the seek path). The mod sets this bit only for the former; an
// observation without it describes a level being fast-forwarded, not a
// level being played.
//
// The four *_UNAVAILABLE bits are the same known/unknown distinction the
// coverage mask makes, applied to whole tables. count==0 with the bit
// CLEAR means 'the mod looked and there were none'. count==0 with the
// bit SET means 'the mod did not look, because the measurement that
// would make the answer trustworthy has not been taken'. A decoder that
// treats those the same is asserting an empty world it never observed --
// which is exactly the failure trajectory.py's certainty channel exists
// to prevent.
enum class GdrlHeaderFlag : uint16_t {
    INPUT_CLEAN = 1,
    PRACTICE = 2,
    GAMEPLAY_STEP = 4,
    DUAL = 8,
    OBJECTS_UNAVAILABLE = 16,
    COMMANDS_UNAVAILABLE = 32,
    PENDING_UNAVAILABLE = 64,
    SPEEDSEGS_UNAVAILABLE = 128,
};

// Why this observation may not be usable. Carried in-band so Python can
// reject a frame on the frame's own evidence, the way every ATTEMPT line
// already carries input[clean blocked=0 leaked=0].
//
// LEVEL_EMPTY is the getMainLevel(id, dontGetLevelString=true) trap: a
// level object with 2 objects and levelLength 793 that loads, runs, and
// produces measurements that look real and mean nothing.
enum class GdrlStatus : uint32_t {
    OK = 0,
    NO_PLAYLAYER = 1,
    NO_PLAYER = 2,
    LEVEL_EMPTY = 3,
    WRONG_LEVEL = 4,
    PAUSED = 5,
    RESET_PENDING = 6,
    SEEK = 7,
};

// The same three-valued verdict main.cpp prints on every ATTEMPT line.
// UNGUARDED does not mean 'probably fine': with GDRL_BLOCK_INPUT off, a
// stray keypress is indistinguishable from a policy action, and four
// consecutive runs were contaminated that way before anyone noticed.
enum class GdrlInputVerdict : uint32_t {
    UNGUARDED = 0,
    CLEAN = 1,
    INVALID = 2,
};

// Objective E's epistemic mask, one entry per section column.
//
//   UNKNOWN   -- not scanned. Outside the window, or the object array
//                filled before this column was reached.
//   SCANNED   -- walked; every object in it made it into the array. A
//                SCANNED column holding no objects is genuinely empty.
//   TRUNCATED -- walked, but the array filled mid-column. Incomplete.
//   ABSENT    -- the column index is past the end of
//                GJBaseGameLayer::m_sections, so GD itself has no
//                geometry there. Known-empty, not unknown.
//
// An off-screen pit and an empty floor are the same bytes in a naive
// occupancy grid, so a policy cannot be conservative about uncertainty
// it cannot see. This is the same primitive trajectory.py already uses
// for motion (a certainty channel rather than a fabricated position),
// applied to visibility instead of to projection: env.py folds coverage
// into the same certainty channel rather than adding a parallel one.
enum class GdrlCoverage : uint8_t {
    UNKNOWN = 0,
    SCANNED = 1,
    TRUNCATED = 2,
    ABSENT = 3,
};

// Must stay numerically identical to trajectory.py's ObjectKind, which
// is what TrajectoryRaster indexes its channels by.
//
// This IS an interpretation, and it is on the wire only because the
// README spec put it there. The raw GameObjectType and m_slopeIsHazard
// ride alongside in the same record, and env.py re-derives the kind and
// asserts agreement -- so the C++ collapse is checked rather than
// trusted, and Python remains the authority on what an object means.
enum class GdrlObjectKind : uint8_t {
    HAZARD = 0,
    SOLID = 1,
    INTERACTIVE = 2,
    OTHER = 3,
};

// HOLD is (start_tick, hold_ticks): one record the mod expands into a
// push at targetTick and a release at targetTick + holdTicks. It is here
// in wire version 1 on purpose -- Objective C needs an action duration,
// and adding the field later would reshape every trained action record.
// PRESS/RELEASE remain so a policy can also hold across an arbitrary
// number of decisions without re-issuing.
enum class GdrlActionKind : uint8_t {
    NOOP = 0,
    PRESS = 1,
    RELEASE = 2,
    HOLD = 3,
};

// One PlayerObject, raw. Every field is read live off the object rather
// than inferred from portals passed, so it stays correct through
// triggers, mid-level respawns and checkpoint restores.
//
// 'present' says only that the pointer was non-null. It is NOT a dual
// test: GD allocates the second PlayerObject unconditionally and merely
// hides it outside dual sections, so m_player2 != nullptr is true on
// every level -- it reported dual=1 on Stereo Madness on the first run.
// GJGameState::m_isDualMode is the real flag and lives on the header.
struct GdrlPlayerState {
    double x;                                     // PlayerObject::getPositionX()
    double y;                                     // PlayerObject::getPositionY()
    double yVelocity;                             // m_yVelocity (a double in 2.2081)
    double gravity;                               // m_gravity. Reads 0.96 at normal gravity, not 1.0. Normalised on the Python side, never here.
    float rotation;                               // CCNode::getRotation(), degrees
    float vehicleSize;                            // m_vehicleSize. 1.0 normal, 0.6 mini. The mini THRESHOLD is Python's business, not the mod's.
    float playerSpeed;                            // m_playerSpeed. 0.90 at the 1x portal -- measured, not assumed.
    uint16_t vehicleFlags;                        // GdrlVehicleFlag bitfield, raw and uncollapsed
    uint8_t isUpsideDown;                         // m_isUpsideDown
    uint8_t isSideways;                           // m_isSideways
    uint8_t isOnGround;                           // m_isOnGround
    uint8_t isDashing;                            // m_isDashing
    uint8_t present;                              // the PlayerObject pointer was non-null
    uint8_t _pad0[5];                             // alignment padding, never read
};
static_assert(sizeof(GdrlPlayerState) == 56, "GdrlPlayerState size drifted from trainer/schema.py");
static_assert(alignof(GdrlPlayerState) == 8, "GdrlPlayerState alignment drifted from trainer/schema.py");
static_assert(std::is_standard_layout_v<GdrlPlayerState>, "GdrlPlayerState must be POD to cross the wire");
static_assert(std::is_trivially_copyable_v<GdrlPlayerState>, "GdrlPlayerState must be POD to cross the wire");
static_assert(offsetof(GdrlPlayerState, x) == 0, "GdrlPlayerState.x offset drifted");
static_assert(offsetof(GdrlPlayerState, y) == 8, "GdrlPlayerState.y offset drifted");
static_assert(offsetof(GdrlPlayerState, yVelocity) == 16, "GdrlPlayerState.yVelocity offset drifted");
static_assert(offsetof(GdrlPlayerState, gravity) == 24, "GdrlPlayerState.gravity offset drifted");
static_assert(offsetof(GdrlPlayerState, rotation) == 32, "GdrlPlayerState.rotation offset drifted");
static_assert(offsetof(GdrlPlayerState, vehicleSize) == 36, "GdrlPlayerState.vehicleSize offset drifted");
static_assert(offsetof(GdrlPlayerState, playerSpeed) == 40, "GdrlPlayerState.playerSpeed offset drifted");
static_assert(offsetof(GdrlPlayerState, vehicleFlags) == 44, "GdrlPlayerState.vehicleFlags offset drifted");
static_assert(offsetof(GdrlPlayerState, isUpsideDown) == 46, "GdrlPlayerState.isUpsideDown offset drifted");
static_assert(offsetof(GdrlPlayerState, isSideways) == 47, "GdrlPlayerState.isSideways offset drifted");
static_assert(offsetof(GdrlPlayerState, isOnGround) == 48, "GdrlPlayerState.isOnGround offset drifted");
static_assert(offsetof(GdrlPlayerState, isDashing) == 49, "GdrlPlayerState.isDashing offset drifted");
static_assert(offsetof(GdrlPlayerState, present) == 50, "GdrlPlayerState.present offset drifted");

// One GameObject in the observation window. README spec, lines 582-595,
// adopted field for field, plus two raw fields the spec derived from.
//
// halfW/halfH come from getObjectRect() rather than m_width/m_height:
// the latter are untransformed sprite extents, the rect is what
// collision actually uses.
//
// 'known' is per-slot validity. Zero means the slot carries no object,
// which is a different statement from an object sitting at the origin.
struct GdrlObject {
    int32_t uniqueID;                             // GameObject::m_uniqueID
    int16_t objectID;                             // GameObject::m_objectID
    uint8_t kind;                                 // GdrlObjectKind, collapsed from m_objectType. Derived; objectType below is the raw source and env.py checks this against its own derivation.
    uint8_t groupCount;                           // GameObject::m_groupCount, clamped to 10
    int16_t groups[10];                           // GameObject::m_groups (std::array<short,10>*)
    int16_t objectType;                           // raw GameObjectType. NOT in the README spec; added so the `kind` collapse above is auditable rather than trusted.
    uint8_t _pad0[2];                             // alignment padding, never read
    double x;                                     // m_positionX (doubles in GD)
    double y;                                     // m_positionY
    float halfW;                                  // getObjectRect().size.width  * 0.5
    float halfH;                                  // getObjectRect().size.height * 0.5
    float rotation;                               // CCNode::getRotation()
    float scaleX;                                 // m_scaleX
    float scaleY;                                 // m_scaleY
    uint8_t isHazard;                             // m_objectType == Hazard || m_slopeIsHazard. Derived; slopeIsHazard below is the raw source.
    uint8_t isGroupDisabled;                      // m_isGroupDisabled
    uint8_t slopeIsHazard;                        // raw m_slopeIsHazard. NOT in the README spec; a slope that kills and one that does not share a GameObjectType, so the raw flag is the only way to check the isHazard collapse.
    uint8_t known;                                // slot validity. 0 = no object here, which is not the same as an object at (0,0).
};
static_assert(sizeof(GdrlObject) == 72, "GdrlObject size drifted from trainer/schema.py");
static_assert(alignof(GdrlObject) == 8, "GdrlObject alignment drifted from trainer/schema.py");
static_assert(std::is_standard_layout_v<GdrlObject>, "GdrlObject must be POD to cross the wire");
static_assert(std::is_trivially_copyable_v<GdrlObject>, "GdrlObject must be POD to cross the wire");
static_assert(offsetof(GdrlObject, uniqueID) == 0, "GdrlObject.uniqueID offset drifted");
static_assert(offsetof(GdrlObject, objectID) == 4, "GdrlObject.objectID offset drifted");
static_assert(offsetof(GdrlObject, kind) == 6, "GdrlObject.kind offset drifted");
static_assert(offsetof(GdrlObject, groupCount) == 7, "GdrlObject.groupCount offset drifted");
static_assert(offsetof(GdrlObject, groups) == 8, "GdrlObject.groups offset drifted");
static_assert(offsetof(GdrlObject, objectType) == 28, "GdrlObject.objectType offset drifted");
static_assert(offsetof(GdrlObject, x) == 32, "GdrlObject.x offset drifted");
static_assert(offsetof(GdrlObject, y) == 40, "GdrlObject.y offset drifted");
static_assert(offsetof(GdrlObject, halfW) == 48, "GdrlObject.halfW offset drifted");
static_assert(offsetof(GdrlObject, halfH) == 52, "GdrlObject.halfH offset drifted");
static_assert(offsetof(GdrlObject, rotation) == 56, "GdrlObject.rotation offset drifted");
static_assert(offsetof(GdrlObject, scaleX) == 60, "GdrlObject.scaleX offset drifted");
static_assert(offsetof(GdrlObject, scaleY) == 64, "GdrlObject.scaleY offset drifted");
static_assert(offsetof(GdrlObject, isHazard) == 68, "GdrlObject.isHazard offset drifted");
static_assert(offsetof(GdrlObject, isGroupDisabled) == 69, "GdrlObject.isGroupDisabled offset drifted");
static_assert(offsetof(GdrlObject, slopeIsHazard) == 70, "GdrlObject.slopeIsHazard offset drifted");
static_assert(offsetof(GdrlObject, known) == 71, "GdrlObject.known offset drifted");

// One live GroupCommandObject2. README spec, lines 597-629, adopted
// verbatim; the struct offsets in the comments are the ones verified
// against the instructions in GroupCommandObject2::step (m1 0x44722c)
// and ::updateAction (m1 0x4472fc), not against field adjacency.
//
// Which GJEffectManager container these are read out of is UNVERIFIED --
// see GDRL_PROBE_CMDVEC in mod/src/probes.cpp. Everything downstream of
// this struct depends on that one measurement and nothing else does.
struct GdrlGroupCommand {
    int32_t targetGroupID;                        // +0x28  m_targetGroupID
    int32_t centerGroupID;                        // +0x2c  m_centerGroupID
    int32_t commandType;                          // +0xd0  m_commandType
    int32_t actionType1;                          // +0x190 m_actionType1  1=x 2=y 3/4=angular
    int32_t actionType2;                          // +0x194 m_actionType2
    int32_t easingType;                           // +0x0c  m_easingType
    int32_t triggerUniqueID;                      // +0x158 m_triggerUniqueID
    int32_t controlID;                            // +0x15c m_controlID
    double actionValue1;                          // +0x198 m_actionValue1
    double actionValue2;                          // +0x1a0 m_actionValue2
    double duration;                              // +0x18  m_duration
    double easingRate;                            // +0x10  m_easingRate
    double currentXOffset;                        // +0x30  m_currentXOffset
    double currentYOffset;                        // +0x38  m_currentYOffset
    double currentAngular;                        // +0x90  m_currentRotateOrTransformValue
    double moveModX;                              // +0x80  m_moveModX
    double moveModY;                              // +0x88  m_moveModY
    float deltaTimeInFloat;                       // +0x1ac m_deltaTimeInFloat (elapsed)
    uint8_t finished;                             // +0x70  m_finished
    uint8_t disabled;                             // +0x71  m_disabled
    uint8_t lockedInX;                            // +0x77  m_lockedInX
    uint8_t lockedInY;                            // +0x78  m_lockedInY
    uint8_t lockToPlayerX;                        // +0x73  m_lockToPlayerX
    uint8_t lockToPlayerY;                        // +0x74  m_lockToPlayerY
    uint8_t lockToCameraX;                        // +0x75  m_lockToCameraX
    uint8_t lockToCameraY;                        // +0x76  m_lockToCameraY
    uint8_t unmodellable;                         // NOT a GD field -- set by the mod when the command came from a player-follow, advanced-follow or keyframe source. Explicit, because 'all lock flags false' is not evidence of 'it is a plain move'.
    uint8_t known;                                // slot validity
    uint8_t _pad0[2];                             // alignment padding, never read
};
static_assert(sizeof(GdrlGroupCommand) == 120, "GdrlGroupCommand size drifted from trainer/schema.py");
static_assert(alignof(GdrlGroupCommand) == 8, "GdrlGroupCommand alignment drifted from trainer/schema.py");
static_assert(std::is_standard_layout_v<GdrlGroupCommand>, "GdrlGroupCommand must be POD to cross the wire");
static_assert(std::is_trivially_copyable_v<GdrlGroupCommand>, "GdrlGroupCommand must be POD to cross the wire");
static_assert(offsetof(GdrlGroupCommand, targetGroupID) == 0, "GdrlGroupCommand.targetGroupID offset drifted");
static_assert(offsetof(GdrlGroupCommand, centerGroupID) == 4, "GdrlGroupCommand.centerGroupID offset drifted");
static_assert(offsetof(GdrlGroupCommand, commandType) == 8, "GdrlGroupCommand.commandType offset drifted");
static_assert(offsetof(GdrlGroupCommand, actionType1) == 12, "GdrlGroupCommand.actionType1 offset drifted");
static_assert(offsetof(GdrlGroupCommand, actionType2) == 16, "GdrlGroupCommand.actionType2 offset drifted");
static_assert(offsetof(GdrlGroupCommand, easingType) == 20, "GdrlGroupCommand.easingType offset drifted");
static_assert(offsetof(GdrlGroupCommand, triggerUniqueID) == 24, "GdrlGroupCommand.triggerUniqueID offset drifted");
static_assert(offsetof(GdrlGroupCommand, controlID) == 28, "GdrlGroupCommand.controlID offset drifted");
static_assert(offsetof(GdrlGroupCommand, actionValue1) == 32, "GdrlGroupCommand.actionValue1 offset drifted");
static_assert(offsetof(GdrlGroupCommand, actionValue2) == 40, "GdrlGroupCommand.actionValue2 offset drifted");
static_assert(offsetof(GdrlGroupCommand, duration) == 48, "GdrlGroupCommand.duration offset drifted");
static_assert(offsetof(GdrlGroupCommand, easingRate) == 56, "GdrlGroupCommand.easingRate offset drifted");
static_assert(offsetof(GdrlGroupCommand, currentXOffset) == 64, "GdrlGroupCommand.currentXOffset offset drifted");
static_assert(offsetof(GdrlGroupCommand, currentYOffset) == 72, "GdrlGroupCommand.currentYOffset offset drifted");
static_assert(offsetof(GdrlGroupCommand, currentAngular) == 80, "GdrlGroupCommand.currentAngular offset drifted");
static_assert(offsetof(GdrlGroupCommand, moveModX) == 88, "GdrlGroupCommand.moveModX offset drifted");
static_assert(offsetof(GdrlGroupCommand, moveModY) == 96, "GdrlGroupCommand.moveModY offset drifted");
static_assert(offsetof(GdrlGroupCommand, deltaTimeInFloat) == 104, "GdrlGroupCommand.deltaTimeInFloat offset drifted");
static_assert(offsetof(GdrlGroupCommand, finished) == 108, "GdrlGroupCommand.finished offset drifted");
static_assert(offsetof(GdrlGroupCommand, disabled) == 109, "GdrlGroupCommand.disabled offset drifted");
static_assert(offsetof(GdrlGroupCommand, lockedInX) == 110, "GdrlGroupCommand.lockedInX offset drifted");
static_assert(offsetof(GdrlGroupCommand, lockedInY) == 111, "GdrlGroupCommand.lockedInY offset drifted");
static_assert(offsetof(GdrlGroupCommand, lockToPlayerX) == 112, "GdrlGroupCommand.lockToPlayerX offset drifted");
static_assert(offsetof(GdrlGroupCommand, lockToPlayerY) == 113, "GdrlGroupCommand.lockToPlayerY offset drifted");
static_assert(offsetof(GdrlGroupCommand, lockToCameraX) == 114, "GdrlGroupCommand.lockToCameraX offset drifted");
static_assert(offsetof(GdrlGroupCommand, lockToCameraY) == 115, "GdrlGroupCommand.lockToCameraY offset drifted");
static_assert(offsetof(GdrlGroupCommand, unmodellable) == 116, "GdrlGroupCommand.unmodellable offset drifted");
static_assert(offsetof(GdrlGroupCommand, known) == 117, "GdrlGroupCommand.known offset drifted");

// An EffectGameObject that has not fired yet. README spec, lines
// 631-655, adopted verbatim.
//
// The activation x comes from EffectGameObject::spawnXPosition
// (m1 0x1741b8), which is exactly: if (m_isSpawnTriggered ||
// m_isTouchTriggered) return m_spawnXPosition; else return
// getPosition().x. For an event-activated trigger the stored value
// means 'where it last fired', not 'where it will fire' -- so the
// isTouchTriggered / isSpawnTriggered flags below are not decoration,
// they say whether activationX means anything at all.
struct GdrlPendingTrigger {
    int32_t targetGroupID;                        // m_targetGroupID
    int32_t centerGroupID;                        // m_centerGroupID
    int32_t times360;                             // m_times360 (property 69)
    int32_t easingType;                           // m_easingType
    float activationX;                            // EffectGameObject::spawnXPosition()
    float duration;                               // m_duration
    float moveOffsetX;                            // m_moveOffset.x (properties 28/29)
    float moveOffsetY;                            // m_moveOffset.y
    float rotationDegrees;                        // m_rotationDegrees (property 68)
    float easingRate;                             // m_easingRate
    float spawnTriggerDelay;                      // m_spawnTriggerDelay
    int16_t objectID;                             // m_objectID -- which trigger kind
    uint8_t isTouchTriggered;                     // m_isTouchTriggered
    uint8_t isSpawnTriggered;                     // m_isSpawnTriggered
    uint8_t isMultiTriggered;                     // m_isMultiTriggered
    uint8_t useMoveTarget;                        // m_useMoveTarget (property 100)
    uint8_t moveTargetMode;                       // m_moveTargetMode (MoveTargetType)
    uint8_t lockToPlayerX;                        // m_lockToPlayerX
    uint8_t lockToPlayerY;                        // m_lockToPlayerY
    uint8_t targetIsRemapped;                     // mod-computed: the object is a Rand/Sequence trigger, or its group appears in GJBaseGameLayer::m_spawnRemapTriggers. The target group is drawn from m_randomSeed at fire time and is not a property of the level.
    uint8_t known;                                // slot validity
    uint8_t _pad0;                                // alignment padding, never read
};
static_assert(sizeof(GdrlPendingTrigger) == 56, "GdrlPendingTrigger size drifted from trainer/schema.py");
static_assert(alignof(GdrlPendingTrigger) == 4, "GdrlPendingTrigger alignment drifted from trainer/schema.py");
static_assert(std::is_standard_layout_v<GdrlPendingTrigger>, "GdrlPendingTrigger must be POD to cross the wire");
static_assert(std::is_trivially_copyable_v<GdrlPendingTrigger>, "GdrlPendingTrigger must be POD to cross the wire");
static_assert(offsetof(GdrlPendingTrigger, targetGroupID) == 0, "GdrlPendingTrigger.targetGroupID offset drifted");
static_assert(offsetof(GdrlPendingTrigger, centerGroupID) == 4, "GdrlPendingTrigger.centerGroupID offset drifted");
static_assert(offsetof(GdrlPendingTrigger, times360) == 8, "GdrlPendingTrigger.times360 offset drifted");
static_assert(offsetof(GdrlPendingTrigger, easingType) == 12, "GdrlPendingTrigger.easingType offset drifted");
static_assert(offsetof(GdrlPendingTrigger, activationX) == 16, "GdrlPendingTrigger.activationX offset drifted");
static_assert(offsetof(GdrlPendingTrigger, duration) == 20, "GdrlPendingTrigger.duration offset drifted");
static_assert(offsetof(GdrlPendingTrigger, moveOffsetX) == 24, "GdrlPendingTrigger.moveOffsetX offset drifted");
static_assert(offsetof(GdrlPendingTrigger, moveOffsetY) == 28, "GdrlPendingTrigger.moveOffsetY offset drifted");
static_assert(offsetof(GdrlPendingTrigger, rotationDegrees) == 32, "GdrlPendingTrigger.rotationDegrees offset drifted");
static_assert(offsetof(GdrlPendingTrigger, easingRate) == 36, "GdrlPendingTrigger.easingRate offset drifted");
static_assert(offsetof(GdrlPendingTrigger, spawnTriggerDelay) == 40, "GdrlPendingTrigger.spawnTriggerDelay offset drifted");
static_assert(offsetof(GdrlPendingTrigger, objectID) == 44, "GdrlPendingTrigger.objectID offset drifted");
static_assert(offsetof(GdrlPendingTrigger, isTouchTriggered) == 46, "GdrlPendingTrigger.isTouchTriggered offset drifted");
static_assert(offsetof(GdrlPendingTrigger, isSpawnTriggered) == 47, "GdrlPendingTrigger.isSpawnTriggered offset drifted");
static_assert(offsetof(GdrlPendingTrigger, isMultiTriggered) == 48, "GdrlPendingTrigger.isMultiTriggered offset drifted");
static_assert(offsetof(GdrlPendingTrigger, useMoveTarget) == 49, "GdrlPendingTrigger.useMoveTarget offset drifted");
static_assert(offsetof(GdrlPendingTrigger, moveTargetMode) == 50, "GdrlPendingTrigger.moveTargetMode offset drifted");
static_assert(offsetof(GdrlPendingTrigger, lockToPlayerX) == 51, "GdrlPendingTrigger.lockToPlayerX offset drifted");
static_assert(offsetof(GdrlPendingTrigger, lockToPlayerY) == 52, "GdrlPendingTrigger.lockToPlayerY offset drifted");
static_assert(offsetof(GdrlPendingTrigger, targetIsRemapped) == 53, "GdrlPendingTrigger.targetIsRemapped offset drifted");
static_assert(offsetof(GdrlPendingTrigger, known) == 54, "GdrlPendingTrigger.known offset drifted");

// One speed-portal boundary ahead. README spec, lines 657-660.
//
// bucket indexes trajectory.py's UNITS_PER_TICK, of which only index 1
// (1x, 1.298250437 units/tick) is measured here; the other four are
// community values and SpeedProfile.certainty() downgrades any arrival
// time that depends on them.
struct GdrlSpeedSegment {
    float startX;
    int32_t bucket;                               // index into SPEED_MULTIPLIERS
    uint8_t known;                                // slot validity
    uint8_t _pad0[3];                             // alignment padding, never read
};
static_assert(sizeof(GdrlSpeedSegment) == 12, "GdrlSpeedSegment size drifted from trainer/schema.py");
static_assert(alignof(GdrlSpeedSegment) == 4, "GdrlSpeedSegment alignment drifted from trainer/schema.py");
static_assert(std::is_standard_layout_v<GdrlSpeedSegment>, "GdrlSpeedSegment must be POD to cross the wire");
static_assert(std::is_trivially_copyable_v<GdrlSpeedSegment>, "GdrlSpeedSegment must be POD to cross the wire");
static_assert(offsetof(GdrlSpeedSegment, startX) == 0, "GdrlSpeedSegment.startX offset drifted");
static_assert(offsetof(GdrlSpeedSegment, bucket) == 4, "GdrlSpeedSegment.bucket offset drifted");
static_assert(offsetof(GdrlSpeedSegment, known) == 8, "GdrlSpeedSegment.known offset drifted");

// Can this frame be believed? Answered in-band, on the frame's own
// evidence, so Python never has to correlate against a log line by
// position. This is the binary-channel equivalent of
// input[clean blocked=0 leaked=0]; a decoder that ignores it is not
// reading evidence, it is reading numbers.
struct GdrlValidity {
    int64_t blocked;                              // pushes dropped at queueButton this attempt (g_gdrlBlocked)
    int64_t leaked;                               // pushes that reached the player anyway (g_gdrlLeaked). >0 voids the attempt.
    int64_t uiEvents;                             // UI key/touch events swallowed, lifetime (g_gdrlUiEvents)
    int64_t timeouts;                             // bounded action-waits that expired this attempt. A silent timeout is indistinguishable from a policy that chose not to jump, so it is counted and surfaced.
    uint32_t inputVerdict;                        // GdrlInputVerdict
    uint32_t status;                              // GdrlStatus
    int32_t levelID;                              // GJGameLevel::m_levelID of the running level
    int32_t pinnedLevelID;                        // the level GDRL_PIN_LEVEL demands, or -1 when unpinned
    int32_t objectCountTotal;                     // GJBaseGameLayer::m_objects->count(). Fewer than 10 means the level string never loaded and every measurement from the run is invalid.
    uint8_t levelPinned;                          // GDRL_PIN_LEVEL is on
    uint8_t blockInput;                           // GDRL_BLOCK_INPUT is on
    uint8_t objectsTruncated;                     // a capacity was hit; some columns are TRUNCATED
    uint8_t _pad0;                                // alignment padding, never read
};
static_assert(sizeof(GdrlValidity) == 56, "GdrlValidity size drifted from trainer/schema.py");
static_assert(alignof(GdrlValidity) == 8, "GdrlValidity alignment drifted from trainer/schema.py");
static_assert(std::is_standard_layout_v<GdrlValidity>, "GdrlValidity must be POD to cross the wire");
static_assert(std::is_trivially_copyable_v<GdrlValidity>, "GdrlValidity must be POD to cross the wire");
static_assert(offsetof(GdrlValidity, blocked) == 0, "GdrlValidity.blocked offset drifted");
static_assert(offsetof(GdrlValidity, leaked) == 8, "GdrlValidity.leaked offset drifted");
static_assert(offsetof(GdrlValidity, uiEvents) == 16, "GdrlValidity.uiEvents offset drifted");
static_assert(offsetof(GdrlValidity, timeouts) == 24, "GdrlValidity.timeouts offset drifted");
static_assert(offsetof(GdrlValidity, inputVerdict) == 32, "GdrlValidity.inputVerdict offset drifted");
static_assert(offsetof(GdrlValidity, status) == 36, "GdrlValidity.status offset drifted");
static_assert(offsetof(GdrlValidity, levelID) == 40, "GdrlValidity.levelID offset drifted");
static_assert(offsetof(GdrlValidity, pinnedLevelID) == 44, "GdrlValidity.pinnedLevelID offset drifted");
static_assert(offsetof(GdrlValidity, objectCountTotal) == 48, "GdrlValidity.objectCountTotal offset drifted");
static_assert(offsetof(GdrlValidity, levelPinned) == 52, "GdrlValidity.levelPinned offset drifted");
static_assert(offsetof(GdrlValidity, blockInput) == 53, "GdrlValidity.blockInput offset drifted");
static_assert(offsetof(GdrlValidity, objectsTruncated) == 54, "GdrlValidity.objectsTruncated offset drifted");

// README spec, lines 566-580, adopted field for field and then extended
// below the marked line. Nothing above that line was renamed, retyped or
// reordered.
//
// tick is lround(m_attemptTime * 240) -- the verified placement clock.
// attemptTime rides alongside so the derivation is auditable from Python
// rather than trusted: t*240 is NOT an exact integer (GD accumulates a
// float32 1/240 into a double; the residual is 2.039e-05 ticks at 391
// ticks and stays unambiguous for ~11 hours), so never test t == n/240.
//
// stepIndex is the mod's own count of physics steps this attempt. It
// exists to make the tick derivation self-checking: at timeWarp 1,
// consecutive observations must advance stepIndex and tick by the same
// amount. Whether m_attemptTime is updated before or after
// prepareMoveActions within a step is UNVERIFIED, and this is how the
// answer shows up in the data instead of being assumed.
struct GdrlObsHeader {
    uint32_t magic;                               // GDRL_OBS_MAGIC
    uint16_t version;                             // bump on any layout change
    uint16_t flags;                               // GdrlHeaderFlag bitfield
    int32_t tick;                                 // lround(PlayLayer::m_attemptTime * 240)
    uint8_t _pad0[4];                             // alignment padding, never read
    double attemptTime;                           // PlayLayer::m_attemptTime (the verified clock)
    float dtPerStep;                              // the 's10' fed to prepareMoveActions this step
    float timeWarp;                               // GJGameState::m_timeWarp
    double playerX;                               // PlayerObject::getPositionX()
    double playerY;                               // PlayerObject::getPositionY()
    float playerSpeed;                            // PlayerObject::m_playerSpeed
    uint32_t objectCount;                         // entries in objects[] with known=1
    uint32_t commandCount;                        // entries in commands[]
    uint32_t pendingCount;                        // entries in pending[]
    uint32_t speedSegCount;                       // entries in speedSegs[]
    uint8_t _pad1[4];                             // alignment padding, never read
    int64_t attempt;                              // EXT: attempt index within this process
    int64_t frame;                                // EXT: render frames elapsed this attempt
    int64_t stepIndex;                            // EXT: physics steps counted by the mod this attempt; cross-checks `tick`
    float dtIn;                                   // EXT: dt update() was called with, raw
    float dtUsed;                                 // EXT: dt actually passed to the original
    float windowMinX;                             // EXT: scanned region, world units
    float windowMaxX;
    float windowMinY;
    float windowMaxY;
    float sectionXFactor;                         // EXT: m_sectionXFactor, emitted so the column indexing can be checked rather than assumed
    float sectionYFactor;                         // EXT: m_sectionYFactor
    float levelLength;                            // EXT: m_levelLength
    int32_t coverageStartCol;                     // EXT: the section column coverage[0] describes
    int32_t sectionColumns;                       // EXT: m_sections.size(); columns past this are ABSENT
    uint16_t objectsDropped;                      // EXT: objects inside the window that did not fit. >0 means truncated, not empty.
    uint16_t commandsDropped;                     // EXT: live commands that did not fit
    uint16_t pendingDropped;                      // EXT: pending triggers that did not fit
    uint8_t isDualMode;                           // EXT: GJGameState::m_isDualMode -- the real dual test
    uint8_t isPaused;                             // EXT: PlayLayer::m_isPaused
    uint8_t inResetDelay;                         // EXT: PlayLayer::m_inResetDelay
    uint8_t _pad2[3];                             // alignment padding, never read
};
static_assert(sizeof(GdrlObsHeader) == 152, "GdrlObsHeader size drifted from trainer/schema.py");
static_assert(alignof(GdrlObsHeader) == 8, "GdrlObsHeader alignment drifted from trainer/schema.py");
static_assert(std::is_standard_layout_v<GdrlObsHeader>, "GdrlObsHeader must be POD to cross the wire");
static_assert(std::is_trivially_copyable_v<GdrlObsHeader>, "GdrlObsHeader must be POD to cross the wire");
static_assert(offsetof(GdrlObsHeader, magic) == 0, "GdrlObsHeader.magic offset drifted");
static_assert(offsetof(GdrlObsHeader, version) == 4, "GdrlObsHeader.version offset drifted");
static_assert(offsetof(GdrlObsHeader, flags) == 6, "GdrlObsHeader.flags offset drifted");
static_assert(offsetof(GdrlObsHeader, tick) == 8, "GdrlObsHeader.tick offset drifted");
static_assert(offsetof(GdrlObsHeader, attemptTime) == 16, "GdrlObsHeader.attemptTime offset drifted");
static_assert(offsetof(GdrlObsHeader, dtPerStep) == 24, "GdrlObsHeader.dtPerStep offset drifted");
static_assert(offsetof(GdrlObsHeader, timeWarp) == 28, "GdrlObsHeader.timeWarp offset drifted");
static_assert(offsetof(GdrlObsHeader, playerX) == 32, "GdrlObsHeader.playerX offset drifted");
static_assert(offsetof(GdrlObsHeader, playerY) == 40, "GdrlObsHeader.playerY offset drifted");
static_assert(offsetof(GdrlObsHeader, playerSpeed) == 48, "GdrlObsHeader.playerSpeed offset drifted");
static_assert(offsetof(GdrlObsHeader, objectCount) == 52, "GdrlObsHeader.objectCount offset drifted");
static_assert(offsetof(GdrlObsHeader, commandCount) == 56, "GdrlObsHeader.commandCount offset drifted");
static_assert(offsetof(GdrlObsHeader, pendingCount) == 60, "GdrlObsHeader.pendingCount offset drifted");
static_assert(offsetof(GdrlObsHeader, speedSegCount) == 64, "GdrlObsHeader.speedSegCount offset drifted");
static_assert(offsetof(GdrlObsHeader, attempt) == 72, "GdrlObsHeader.attempt offset drifted");
static_assert(offsetof(GdrlObsHeader, frame) == 80, "GdrlObsHeader.frame offset drifted");
static_assert(offsetof(GdrlObsHeader, stepIndex) == 88, "GdrlObsHeader.stepIndex offset drifted");
static_assert(offsetof(GdrlObsHeader, dtIn) == 96, "GdrlObsHeader.dtIn offset drifted");
static_assert(offsetof(GdrlObsHeader, dtUsed) == 100, "GdrlObsHeader.dtUsed offset drifted");
static_assert(offsetof(GdrlObsHeader, windowMinX) == 104, "GdrlObsHeader.windowMinX offset drifted");
static_assert(offsetof(GdrlObsHeader, windowMaxX) == 108, "GdrlObsHeader.windowMaxX offset drifted");
static_assert(offsetof(GdrlObsHeader, windowMinY) == 112, "GdrlObsHeader.windowMinY offset drifted");
static_assert(offsetof(GdrlObsHeader, windowMaxY) == 116, "GdrlObsHeader.windowMaxY offset drifted");
static_assert(offsetof(GdrlObsHeader, sectionXFactor) == 120, "GdrlObsHeader.sectionXFactor offset drifted");
static_assert(offsetof(GdrlObsHeader, sectionYFactor) == 124, "GdrlObsHeader.sectionYFactor offset drifted");
static_assert(offsetof(GdrlObsHeader, levelLength) == 128, "GdrlObsHeader.levelLength offset drifted");
static_assert(offsetof(GdrlObsHeader, coverageStartCol) == 132, "GdrlObsHeader.coverageStartCol offset drifted");
static_assert(offsetof(GdrlObsHeader, sectionColumns) == 136, "GdrlObsHeader.sectionColumns offset drifted");
static_assert(offsetof(GdrlObsHeader, objectsDropped) == 140, "GdrlObsHeader.objectsDropped offset drifted");
static_assert(offsetof(GdrlObsHeader, commandsDropped) == 142, "GdrlObsHeader.commandsDropped offset drifted");
static_assert(offsetof(GdrlObsHeader, pendingDropped) == 144, "GdrlObsHeader.pendingDropped offset drifted");
static_assert(offsetof(GdrlObsHeader, isDualMode) == 146, "GdrlObsHeader.isDualMode offset drifted");
static_assert(offsetof(GdrlObsHeader, isPaused) == 147, "GdrlObsHeader.isPaused offset drifted");
static_assert(offsetof(GdrlObsHeader, inResetDelay) == 148, "GdrlObsHeader.inResetDelay offset drifted");

// One physics-step observation: header, validity, both players, the
// coverage mask, and the four fixed-capacity tables.
//
// seq is a seqlock: odd while a write is in progress, even and advanced
// by two when it completes. A reader samples seq, copies the body,
// re-samples seq, and retries if it moved or was odd. The ping-pong
// protocol already means the writer will not begin observation n+1 until
// it has consumed the action for n -- but that guarantee evaporates the
// moment a bounded wait times out and the game runs on, which is exactly
// when a torn read would be misdiagnosed as a physics anomaly.
struct GdrlObservation {
    uint64_t seq;                                 // seqlock; odd == write in progress
    GdrlObsHeader header;
    GdrlValidity validity;
    GdrlPlayerState players[2];                   // [0] is player 1. header.playerX/Y/Speed duplicate players[0] because the README spec declared them; env.py asserts they agree, turning the redundancy into a check that the mod read the player it thinks it did.
    uint8_t coverage[64];                         // GdrlCoverage per column
    GdrlObject objects[256];
    GdrlGroupCommand commands[64];
    GdrlPendingTrigger pending[64];
    GdrlSpeedSegment speedSegs[16];
};
static_assert(sizeof(GdrlObservation) == 30280, "GdrlObservation size drifted from trainer/schema.py");
static_assert(alignof(GdrlObservation) == 8, "GdrlObservation alignment drifted from trainer/schema.py");
static_assert(std::is_standard_layout_v<GdrlObservation>, "GdrlObservation must be POD to cross the wire");
static_assert(std::is_trivially_copyable_v<GdrlObservation>, "GdrlObservation must be POD to cross the wire");
static_assert(offsetof(GdrlObservation, seq) == 0, "GdrlObservation.seq offset drifted");
static_assert(offsetof(GdrlObservation, header) == 8, "GdrlObservation.header offset drifted");
static_assert(offsetof(GdrlObservation, validity) == 160, "GdrlObservation.validity offset drifted");
static_assert(offsetof(GdrlObservation, players) == 216, "GdrlObservation.players offset drifted");
static_assert(offsetof(GdrlObservation, coverage) == 328, "GdrlObservation.coverage offset drifted");
static_assert(offsetof(GdrlObservation, objects) == 392, "GdrlObservation.objects offset drifted");
static_assert(offsetof(GdrlObservation, commands) == 18824, "GdrlObservation.commands offset drifted");
static_assert(offsetof(GdrlObservation, pending) == 26504, "GdrlObservation.pending offset drifted");
static_assert(offsetof(GdrlObservation, speedSegs) == 30088, "GdrlObservation.speedSegs offset drifted");

// One scheduled input. Placed by PHYSICS TICK, never by render frame:
// the render:physics ratio is not constant (316-440 render frames
// covered the same 391 ticks), so a frame-placed input lands on a
// different tick between runs and reintroduces exactly the
// nondeterminism the engine itself does not have.
struct GdrlAction {
    int64_t targetTick;                           // the physics tick the input must take effect on
    int32_t holdTicks;                            // HOLD only: ticks between the push and its release
    uint8_t kind;                                 // GdrlActionKind
    uint8_t button;                               // PlayerButton; 1 = jump
    uint8_t player;                               // 0 = player 1, 1 = player 2
    uint8_t _pad0;                                // alignment padding, never read
};
static_assert(sizeof(GdrlAction) == 16, "GdrlAction size drifted from trainer/schema.py");
static_assert(alignof(GdrlAction) == 8, "GdrlAction alignment drifted from trainer/schema.py");
static_assert(std::is_standard_layout_v<GdrlAction>, "GdrlAction must be POD to cross the wire");
static_assert(std::is_trivially_copyable_v<GdrlAction>, "GdrlAction must be POD to cross the wire");
static_assert(offsetof(GdrlAction, targetTick) == 0, "GdrlAction.targetTick offset drifted");
static_assert(offsetof(GdrlAction, holdTicks) == 8, "GdrlAction.holdTicks offset drifted");
static_assert(offsetof(GdrlAction, kind) == 12, "GdrlAction.kind offset drifted");
static_assert(offsetof(GdrlAction, button) == 13, "GdrlAction.button offset drifted");
static_assert(offsetof(GdrlAction, player) == 14, "GdrlAction.player offset drifted");

// Python's reply to one observation. seq must equal the seq of the
// observation being answered; the mod counts a mismatch as a protocol
// error rather than executing a stale plan against the wrong tick.
struct GdrlActionBlock {
    uint64_t seq;                                 // the GdrlObservation.seq being answered
    uint64_t nonce;                               // opaque; Python's own bookkeeping
    int32_t advanceSteps;                         // publish the next observation this many physics steps later. 1 = every step (full fidelity). Larger values trade observation rate for throughput WITHOUT losing tick-exact action placement, because scheduled actions are still queued per step in between.
    uint16_t count;                               // entries in actions[]
    uint8_t detach;                               // 1 asks the mod to stop blocking and free-run
    uint8_t _pad0;                                // alignment padding, never read
    GdrlAction actions[8];
};
static_assert(sizeof(GdrlActionBlock) == 152, "GdrlActionBlock size drifted from trainer/schema.py");
static_assert(alignof(GdrlActionBlock) == 8, "GdrlActionBlock alignment drifted from trainer/schema.py");
static_assert(std::is_standard_layout_v<GdrlActionBlock>, "GdrlActionBlock must be POD to cross the wire");
static_assert(std::is_trivially_copyable_v<GdrlActionBlock>, "GdrlActionBlock must be POD to cross the wire");
static_assert(offsetof(GdrlActionBlock, seq) == 0, "GdrlActionBlock.seq offset drifted");
static_assert(offsetof(GdrlActionBlock, nonce) == 8, "GdrlActionBlock.nonce offset drifted");
static_assert(offsetof(GdrlActionBlock, advanceSteps) == 16, "GdrlActionBlock.advanceSteps offset drifted");
static_assert(offsetof(GdrlActionBlock, count) == 20, "GdrlActionBlock.count offset drifted");
static_assert(offsetof(GdrlActionBlock, detach) == 22, "GdrlActionBlock.detach offset drifted");
static_assert(offsetof(GdrlActionBlock, actions) == 24, "GdrlActionBlock.actions offset drifted");

// The handshake block, at offset 0 of the mapping.
//
// The first four fields must never move (see the module docstring): an
// old Python has to be able to read magic/version/hash out of a newer
// mapping and report the real mismatch instead of a confusing one.
//
// modPid exists because a crashed GD leaves the segment behind. The mod
// shm_unlinks and recreates on every launch, so a stale segment can
// never be reused -- but a Python that mapped the old one before the
// relaunch still holds a valid mapping of a dead process, and
// kill(modPid, 0) is the only cheap way to tell.
struct GdrlControl {
    uint32_t magic;                               // GDRL_MAGIC ('GDRL' little-endian)
    uint16_t wireVersion;                         // GDRL_WIRE_VERSION
    uint16_t headerSize;                          // sizeof(GdrlControl)
    uint64_t schemaHash;                          // GDRL_SCHEMA_HASH
    uint32_t obsOffset;                           // byte offset of GdrlObservation
    uint32_t obsSize;                             // sizeof(GdrlObservation)
    uint32_t actOffset;                           // byte offset of GdrlActionBlock
    uint32_t actSize;                             // sizeof(GdrlActionBlock)
    uint32_t totalSize;                           // sizeof(GdrlShared)
    uint32_t maxObjects;                          // GDRL_MAX_OBJECTS
    uint32_t maxCommands;                         // GDRL_MAX_COMMANDS
    uint32_t maxPending;                          // GDRL_MAX_PENDING
    uint32_t maxSpeedSegs;                        // GDRL_MAX_SPEED_SEGS
    uint32_t maxActions;                          // GDRL_MAX_ACTIONS
    uint32_t coverageCols;                        // GDRL_COVERAGE_COLS
    uint32_t tickHz;                              // 240; the clock `tick` is denominated in
    uint32_t modPid;                              // getpid() of the GD process
    uint32_t modAlive;                            // 1 while the mod holds the mapping
    uint32_t pyAttached;                          // 1 while Python is answering. The mod only ever BLOCKS when this is 1, so an unattached GD can never hang on a Python that was never there.
    uint32_t waitBudgetUs;                        // how long the game thread will block
    uint32_t detachedByTimeout;                   // times the mod gave up on Python and resumed free-running
    uint8_t _pad0[4];                             // alignment padding, never read
    uint64_t obsSeq;                              // mirror of the last published obs seq
    uint64_t actSeq;                              // last action seq the mod consumed
    uint64_t stepsServed;                         // observation/action round trips
    uint64_t stepTimeouts;                        // bounded waits that expired, lifetime. Must be 0 for a run to be usable as evidence.
    uint64_t protocolErrors;                      // stale or mismatched action seqs the mod refused
    uint64_t pyHeartbeat;                         // Python bumps this every step
};
static_assert(sizeof(GdrlControl) == 136, "GdrlControl size drifted from trainer/schema.py");
static_assert(alignof(GdrlControl) == 8, "GdrlControl alignment drifted from trainer/schema.py");
static_assert(std::is_standard_layout_v<GdrlControl>, "GdrlControl must be POD to cross the wire");
static_assert(std::is_trivially_copyable_v<GdrlControl>, "GdrlControl must be POD to cross the wire");
static_assert(offsetof(GdrlControl, magic) == 0, "GdrlControl.magic offset drifted");
static_assert(offsetof(GdrlControl, wireVersion) == 4, "GdrlControl.wireVersion offset drifted");
static_assert(offsetof(GdrlControl, headerSize) == 6, "GdrlControl.headerSize offset drifted");
static_assert(offsetof(GdrlControl, schemaHash) == 8, "GdrlControl.schemaHash offset drifted");
static_assert(offsetof(GdrlControl, obsOffset) == 16, "GdrlControl.obsOffset offset drifted");
static_assert(offsetof(GdrlControl, obsSize) == 20, "GdrlControl.obsSize offset drifted");
static_assert(offsetof(GdrlControl, actOffset) == 24, "GdrlControl.actOffset offset drifted");
static_assert(offsetof(GdrlControl, actSize) == 28, "GdrlControl.actSize offset drifted");
static_assert(offsetof(GdrlControl, totalSize) == 32, "GdrlControl.totalSize offset drifted");
static_assert(offsetof(GdrlControl, maxObjects) == 36, "GdrlControl.maxObjects offset drifted");
static_assert(offsetof(GdrlControl, maxCommands) == 40, "GdrlControl.maxCommands offset drifted");
static_assert(offsetof(GdrlControl, maxPending) == 44, "GdrlControl.maxPending offset drifted");
static_assert(offsetof(GdrlControl, maxSpeedSegs) == 48, "GdrlControl.maxSpeedSegs offset drifted");
static_assert(offsetof(GdrlControl, maxActions) == 52, "GdrlControl.maxActions offset drifted");
static_assert(offsetof(GdrlControl, coverageCols) == 56, "GdrlControl.coverageCols offset drifted");
static_assert(offsetof(GdrlControl, tickHz) == 60, "GdrlControl.tickHz offset drifted");
static_assert(offsetof(GdrlControl, modPid) == 64, "GdrlControl.modPid offset drifted");
static_assert(offsetof(GdrlControl, modAlive) == 68, "GdrlControl.modAlive offset drifted");
static_assert(offsetof(GdrlControl, pyAttached) == 72, "GdrlControl.pyAttached offset drifted");
static_assert(offsetof(GdrlControl, waitBudgetUs) == 76, "GdrlControl.waitBudgetUs offset drifted");
static_assert(offsetof(GdrlControl, detachedByTimeout) == 80, "GdrlControl.detachedByTimeout offset drifted");
static_assert(offsetof(GdrlControl, obsSeq) == 88, "GdrlControl.obsSeq offset drifted");
static_assert(offsetof(GdrlControl, actSeq) == 96, "GdrlControl.actSeq offset drifted");
static_assert(offsetof(GdrlControl, stepsServed) == 104, "GdrlControl.stepsServed offset drifted");
static_assert(offsetof(GdrlControl, stepTimeouts) == 112, "GdrlControl.stepTimeouts offset drifted");
static_assert(offsetof(GdrlControl, protocolErrors) == 120, "GdrlControl.protocolErrors offset drifted");
static_assert(offsetof(GdrlControl, pyHeartbeat) == 128, "GdrlControl.pyHeartbeat offset drifted");

// The whole mapping: one control block, one observation slot, one action
// slot. Single-slot is safe only because the protocol is strictly
// ping-pong; an asynchronous variant would need a ring, and the seqlock
// on the observation is what keeps a timed-out step from tearing.
struct GdrlShared {
    GdrlControl control;
    GdrlObservation obs;
    GdrlActionBlock action;
};
static_assert(sizeof(GdrlShared) == 30568, "GdrlShared size drifted from trainer/schema.py");
static_assert(alignof(GdrlShared) == 8, "GdrlShared alignment drifted from trainer/schema.py");
static_assert(std::is_standard_layout_v<GdrlShared>, "GdrlShared must be POD to cross the wire");
static_assert(std::is_trivially_copyable_v<GdrlShared>, "GdrlShared must be POD to cross the wire");
static_assert(offsetof(GdrlShared, control) == 0, "GdrlShared.control offset drifted");
static_assert(offsetof(GdrlShared, obs) == 136, "GdrlShared.obs offset drifted");
static_assert(offsetof(GdrlShared, action) == 30416, "GdrlShared.action offset drifted");

// The four fields an older Python must still be able to read out of a
// newer mapping in order to report the right mismatch rather than a
// confusing one. These offsets are frozen across every wire version.
static_assert(offsetof(GdrlShared, control) == 0, "control must be first");
static_assert(offsetof(GdrlControl, magic) == 0, "magic offset is frozen");
static_assert(offsetof(GdrlControl, wireVersion) == 4, "wireVersion offset is frozen");
static_assert(offsetof(GdrlControl, headerSize) == 6, "headerSize offset is frozen");
static_assert(offsetof(GdrlControl, schemaHash) == 8, "schemaHash offset is frozen");
