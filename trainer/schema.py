"""The single declarative source for the GD <-> Python wire format.

WHY THIS FILE EXISTS
--------------------

Two things in this repo were previously written out twice and kept in sync by a
comment. The ``Vehicle`` enum (``enum class Vehicle`` in mod/src/main.cpp,
``class Vehicle(IntEnum)`` in trainer/conditioning.py), and the telemetry record
layout, which existed only as prose in README.md under "Telemetry the mod must
emit". That is the exact shape of bug this repo keeps getting burned by: a wrong
conditioning label or a shifted struct field does not crash, it trains, and it
converges to something plausible. Nothing downstream can detect it.

So the enums and the binary record layout are declared once, here, and both
language bindings are *generated*:

    trainer/schema.py  --(generate)-->  mod/src/gdrl_schema.hpp
                       --(generate)-->  trainer/schema_generated.py

Both generated files are checked in, so ``geode build`` never depends on running
Python. ``test_schema.py`` regenerates them in memory and asserts byte-identical
output, so a drifted schema fails a test instead of corrupting a training run.

RELATIONSHIP TO THE README SPEC
-------------------------------

README.md lines ~563-661 already specify ``GdrlObsHeader``, ``GdrlObject``,
``GdrlGroupCommand``, ``GdrlPendingTrigger`` and ``GdrlSpeedSegment``, every
field annotated with its GD 2.2081 binding name and, for the command struct, its
verified struct offset. **That spec is adopted verbatim here, field name for
field name**, so trainer/trajectory.py remains the consumer without
modification. This file adds, and never renames or reorders within, those
structs:

  * a per-player regime block (``GdrlPlayerState``), because the spec's header
    carries playerX/playerY/playerSpeed only and Objective A needs the whole
    vehicle/gravity/size/dual axis set, for player 2 as well as player 1;
  * a validity block (``GdrlValidity``), the binary equivalent of the
    ``input[clean blocked=0 leaked=0]`` verdict every ATTEMPT line carries;
  * attempt / frame / step indices;
  * the coverage mask (Objective E; see ``GdrlCoverage``);
  * the control block and action record, which the spec did not cover.

Where a spec field is a *derived* value (``GdrlObject.kind``,
``GdrlObject.isHazard``) the raw source it was derived from rides alongside it
(``objectType``, ``slopeIsHazard``) so that env.py can re-derive and assert
agreement. The derivation is then testable rather than trusted, without breaking
the spec.

THE SCHEMA HASH
---------------

A 64-bit hash is computed over a *canonical rendering* of the declarations --
enum names and values, struct names, field names, field types, array counts, the
computed offsets and sizes, and the capacity constants. Comments and formatting
are deliberately excluded: editing a comment must not invalidate a checked-in
trained run, but any change to what the bytes mean must.

The mod writes the hash into the shared header at startup. Python asserts it
matches and refuses to decode otherwise. A mismatched pair fails at handshake
rather than silently decoding one struct as another.

FOUR FIELDS THAT MUST NEVER MOVE
--------------------------------

magic (u32) @0, wireVersion (u16) @4, headerSize (u16) @6, schemaHash (u64) @8,
all in GdrlControl, which is at offset 0 of the mapping. A Python built against
wire version N must be able to read those out of a mapping written by version M
and report the real mismatch. If they move, an old Python reads garbage and
reports a confusing error instead of the right one. ``test_schema.py`` pins them.

WHAT THIS FILE DELIBERATELY DOES NOT CONTAIN
--------------------------------------------

Semantics, with two named exceptions. No thresholds, no bucketing, no derived
booleans: ``mini`` is not a wire field, ``m_vehicleSize`` is, and Python decides
what 0.6 means. The vehicle is not a wire field either; the raw seven-bit flag
word off PlayerObject is, and ``deriveVehicle`` lives in Python where the
overlapping-flags case is testable.

The two exceptions are deliberate and follow the precedent trajectory.py already
set: ``GdrlGroupCommand.unmodellable`` and ``GdrlPendingTrigger.targetIsRemapped``
are mod-computed, because they encode *what the mod knows about where the value
came from* -- information that is not recoverable from the field values at all.
"all the lock flags are false" is not evidence of "it is a plain move".

Regenerate with:  python3 trainer/schema.py
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass, field as dc_field
from pathlib import Path

# ---------------------------------------------------------------------------
# Scalar types
# ---------------------------------------------------------------------------

# (C++ type, size, alignment, numpy format code). Alignment equals size for
# every scalar used here, which holds on arm64 and x86_64 macOS. A platform
# where it did not would fail the generated static_asserts at build time rather
# than mis-decode at runtime, which is the point of emitting them.
_SCALARS: dict[str, tuple[str, int, int, str]] = {
    "u8":  ("uint8_t",  1, 1, "u1"),
    "i8":  ("int8_t",   1, 1, "i1"),
    "u16": ("uint16_t", 2, 2, "u2"),
    "i16": ("int16_t",  2, 2, "i2"),
    "u32": ("uint32_t", 4, 4, "u4"),
    "i32": ("int32_t",  4, 4, "i4"),
    "u64": ("uint64_t", 8, 8, "u8"),
    "i64": ("int64_t",  8, 8, "i8"),
    "f32": ("float",    4, 4, "f4"),
    "f64": ("double",   8, 8, "f8"),
}


@dataclass(frozen=True)
class Field:
    name: str
    type: str            # a key of _SCALARS, or "@StructName"
    count: int = 1       # >1 makes it a fixed-capacity array
    comment: str = ""


@dataclass(frozen=True)
class LaidOutField:
    name: str
    type: str
    count: int
    comment: str
    offset: int
    size: int            # total bytes including the array multiplier
    is_pad: bool = False


@dataclass
class Struct:
    name: str
    fields: list[Field]
    comment: str = ""
    laid_out: list[LaidOutField] = dc_field(default_factory=list)
    size: int = 0
    align: int = 0


@dataclass(frozen=True)
class Enum:
    name: str
    underlying: str                    # a key of _SCALARS
    members: tuple[tuple[str, int], ...]
    comment: str = ""


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def _elem_size_align(type_name: str, structs: dict[str, Struct]) -> tuple[int, int]:
    if type_name.startswith("@"):
        s = structs[type_name[1:]]
        return s.size, s.align
    _, size, align, _ = _SCALARS[type_name]
    return size, align


def lay_out(struct: Struct, structs: dict[str, Struct]) -> None:
    """Assign offsets using natural alignment, materialising every pad byte.

    Padding is emitted as a real ``_padN`` member rather than left implicit. The
    generated header then carries a static_assert on the offset of every field,
    so "the compiler laid this out differently from the Python model" is a build
    failure and not a wrong number on the wire.
    """
    offset = 0
    struct_align = 1
    out: list[LaidOutField] = []
    pad_index = 0

    for f in struct.fields:
        if f.count < 1:
            raise ValueError(f"{struct.name}.{f.name}: count must be >= 1")
        esize, ealign = _elem_size_align(f.type, structs)
        struct_align = max(struct_align, ealign)
        misalign = offset % ealign
        if misalign:
            pad = ealign - misalign
            out.append(LaidOutField(f"_pad{pad_index}", "u8", pad, "", offset, pad, True))
            pad_index += 1
            offset += pad
        out.append(LaidOutField(f.name, f.type, f.count, f.comment,
                                offset, esize * f.count))
        offset += esize * f.count

    tail = offset % struct_align
    if tail:
        pad = struct_align - tail
        out.append(LaidOutField(f"_pad{pad_index}", "u8", pad, "", offset, pad, True))
        offset += pad

    struct.laid_out = out
    struct.size = offset
    struct.align = struct_align


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAGIC = 0x4C524447          # 'GDRL' read little-endian -- README's GDRL_OBS_MAGIC
WIRE_VERSION = 1

# Fixed capacities. A variable-length record in the hot path means a parse
# before you know how much to read; the point of this format is that a tick is
# one memcpy. When a capacity is not enough the record says so -- objectsDropped,
# commandsDropped, and per-column TRUNCATED coverage -- rather than silently
# emitting a partial world that looks like an empty one.
MAX_OBJECTS = 256           # ~4 screens of dense Stereo Madness geometry
MAX_GROUPS = 10             # GameObject::m_groups is std::array<short, 10>*
MAX_COMMANDS = 64           # live GroupCommandObject2 in flight
MAX_PENDING = 64            # not-yet-fired x-activated triggers ahead
MAX_SPEED_SEGS = 16         # speed portals in the lookahead window
MAX_ACTIONS = 8
COVERAGE_COLS = 64          # section columns described by the coverage mask

# The physics tick rate. Declared here as well as in trajectory.py because the
# wire's `tick` field is defined in terms of it and the two must not drift; the
# control block carries it so Python can assert agreement at attach.
TICK_HZ = 240


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

ENUMS: list[Enum] = [
    Enum(
        name="GdrlVehicle",
        underlying="u8",
        members=(
            ("CUBE", 0), ("SHIP", 1), ("BALL", 2), ("UFO", 3),
            ("WAVE", 4), ("ROBOT", 5), ("SPIDER", 6), ("SWING", 7),
        ),
        comment=(
            "A derived label, NOT a wire field. The wire carries the raw flag word\n"
            "(GdrlVehicleFlag); Python derives this, so the derivation order --\n"
            "swing before ship, spider before ball -- is testable and the\n"
            "overlapping-flags case is visible instead of being papered over by the\n"
            "ordering. Declared here only so conditioning.py imports it rather than\n"
            "redeclaring it, and so the C++ side keeps a name for the log lines."
        ),
    ),
    Enum(
        name="GdrlVehicleFlag",
        underlying="u16",
        members=(
            ("SHIP", 1 << 0),
            ("BALL", 1 << 1),
            ("BIRD", 1 << 2),     # UFO
            ("DART", 1 << 3),     # wave
            ("ROBOT", 1 << 4),
            ("SPIDER", 1 << 5),
            ("SWING", 1 << 6),
        ),
        comment=(
            "Bit positions of PlayerObject's seven mode flags, in exactly the order\n"
            "main.cpp's modeFlagBits() already uses -- changing this order silently\n"
            "relabels every observation, so it is pinned by the schema hash.\n"
            "\n"
            "Cube is the absence of all seven and therefore has no bit. 'no bit set'\n"
            "and 'two bits set' are different observations, which is why the raw\n"
            "word crosses the wire rather than an eight-valued enum: an overlap is\n"
            "evidence that deriveVehicle's ordering is load-bearing rather than\n"
            "merely defensive, and collapsing in C++ would destroy that evidence."
        ),
    ),
    Enum(
        name="GdrlHeaderFlag",
        underlying="u16",
        members=(
            ("INPUT_CLEAN", 1 << 0),
            ("PRACTICE", 1 << 1),
            ("GAMEPLAY_STEP", 1 << 2),
            ("DUAL", 1 << 3),
            ("OBJECTS_UNAVAILABLE", 1 << 4),
            ("COMMANDS_UNAVAILABLE", 1 << 5),
            ("PENDING_UNAVAILABLE", 1 << 6),
            ("SPEEDSEGS_UNAVAILABLE", 1 << 7),
        ),
        comment=(
            "GdrlObsHeader.flags, per the README spec (bit0 input-clean, bit1\n"
            "practice mode), extended.\n"
            "\n"
            "GAMEPLAY_STEP distinguishes the gameplay emission from the level-seek\n"
            "one. GJEffectManager::prepareMoveActions has two bl sites --\n"
            "GJBaseGameLayer::update (gameplay, inside the fixed-step loop, +0x9f8,\n"
            "loop body 0x122ea4..0x123d94) and GJBaseGameLayer::loadUpToPosition\n"
            "(the seek path). The mod sets this bit only for the former; an\n"
            "observation without it describes a level being fast-forwarded, not a\n"
            "level being played.\n"
            "\n"
            "The four *_UNAVAILABLE bits are the same known/unknown distinction the\n"
            "coverage mask makes, applied to whole tables. count==0 with the bit\n"
            "CLEAR means 'the mod looked and there were none'. count==0 with the\n"
            "bit SET means 'the mod did not look, because the measurement that\n"
            "would make the answer trustworthy has not been taken'. A decoder that\n"
            "treats those the same is asserting an empty world it never observed --\n"
            "which is exactly the failure trajectory.py's certainty channel exists\n"
            "to prevent."
        ),
    ),
    Enum(
        name="GdrlStatus",
        underlying="u32",
        members=(
            ("OK", 0),
            ("NO_PLAYLAYER", 1),
            ("NO_PLAYER", 2),
            ("LEVEL_EMPTY", 3),
            ("WRONG_LEVEL", 4),
            ("PAUSED", 5),
            ("RESET_PENDING", 6),
            ("SEEK", 7),
        ),
        comment=(
            "Why this observation may not be usable. Carried in-band so Python can\n"
            "reject a frame on the frame's own evidence, the way every ATTEMPT line\n"
            "already carries input[clean blocked=0 leaked=0].\n"
            "\n"
            "LEVEL_EMPTY is the getMainLevel(id, dontGetLevelString=true) trap: a\n"
            "level object with 2 objects and levelLength 793 that loads, runs, and\n"
            "produces measurements that look real and mean nothing."
        ),
    ),
    Enum(
        name="GdrlInputVerdict",
        underlying="u32",
        members=(("UNGUARDED", 0), ("CLEAN", 1), ("INVALID", 2)),
        comment=(
            "The same three-valued verdict main.cpp prints on every ATTEMPT line.\n"
            "UNGUARDED does not mean 'probably fine': with GDRL_BLOCK_INPUT off, a\n"
            "stray keypress is indistinguishable from a policy action, and four\n"
            "consecutive runs were contaminated that way before anyone noticed."
        ),
    ),
    Enum(
        name="GdrlCoverage",
        underlying="u8",
        members=(
            ("UNKNOWN", 0),
            ("SCANNED", 1),
            ("TRUNCATED", 2),
            ("ABSENT", 3),
        ),
        comment=(
            "Objective E's epistemic mask, one entry per section column.\n"
            "\n"
            "  UNKNOWN   -- not scanned. Outside the window, or the object array\n"
            "               filled before this column was reached.\n"
            "  SCANNED   -- walked; every object in it made it into the array. A\n"
            "               SCANNED column holding no objects is genuinely empty.\n"
            "  TRUNCATED -- walked, but the array filled mid-column. Incomplete.\n"
            "  ABSENT    -- the column index is past the end of\n"
            "               GJBaseGameLayer::m_sections, so GD itself has no\n"
            "               geometry there. Known-empty, not unknown.\n"
            "\n"
            "An off-screen pit and an empty floor are the same bytes in a naive\n"
            "occupancy grid, so a policy cannot be conservative about uncertainty\n"
            "it cannot see. This is the same primitive trajectory.py already uses\n"
            "for motion (a certainty channel rather than a fabricated position),\n"
            "applied to visibility instead of to projection: env.py folds coverage\n"
            "into the same certainty channel rather than adding a parallel one."
        ),
    ),
    Enum(
        name="GdrlObjectKind",
        underlying="u8",
        members=(("HAZARD", 0), ("SOLID", 1), ("INTERACTIVE", 2), ("OTHER", 3)),
        comment=(
            "Must stay numerically identical to trajectory.py's ObjectKind, which\n"
            "is what TrajectoryRaster indexes its channels by.\n"
            "\n"
            "This IS an interpretation, and it is on the wire only because the\n"
            "README spec put it there. The raw GameObjectType and m_slopeIsHazard\n"
            "ride alongside in the same record, and env.py re-derives the kind and\n"
            "asserts agreement -- so the C++ collapse is checked rather than\n"
            "trusted, and Python remains the authority on what an object means."
        ),
    ),
    Enum(
        name="GdrlActionKind",
        underlying="u8",
        members=(("NOOP", 0), ("PRESS", 1), ("RELEASE", 2), ("HOLD", 3)),
        comment=(
            "HOLD is (start_tick, hold_ticks): one record the mod expands into a\n"
            "push at targetTick and a release at targetTick + holdTicks. It is here\n"
            "in wire version 1 on purpose -- Objective C needs an action duration,\n"
            "and adding the field later would reshape every trained action record.\n"
            "PRESS/RELEASE remain so a policy can also hold across an arbitrary\n"
            "number of decisions without re-issuing."
        ),
    ),
]


# ---------------------------------------------------------------------------
# Structs
# ---------------------------------------------------------------------------

STRUCTS: list[Struct] = [
    Struct(
        name="GdrlPlayerState",
        comment=(
            "One PlayerObject, raw. Every field is read live off the object rather\n"
            "than inferred from portals passed, so it stays correct through\n"
            "triggers, mid-level respawns and checkpoint restores.\n"
            "\n"
            "'present' says only that the pointer was non-null. It is NOT a dual\n"
            "test: GD allocates the second PlayerObject unconditionally and merely\n"
            "hides it outside dual sections, so m_player2 != nullptr is true on\n"
            "every level -- it reported dual=1 on Stereo Madness on the first run.\n"
            "GJGameState::m_isDualMode is the real flag and lives on the header."
        ),
        fields=[
            Field("x", "f64", comment="PlayerObject::getPositionX()"),
            Field("y", "f64", comment="PlayerObject::getPositionY()"),
            Field("yVelocity", "f64", comment="m_yVelocity (a double in 2.2081)"),
            Field("gravity", "f64",
                  comment="m_gravity. Reads 0.96 at normal gravity, not 1.0. "
                          "Normalised on the Python side, never here."),
            Field("rotation", "f32", comment="CCNode::getRotation(), degrees"),
            Field("vehicleSize", "f32",
                  comment="m_vehicleSize. 1.0 normal, 0.6 mini. The mini "
                          "THRESHOLD is Python's business, not the mod's."),
            Field("playerSpeed", "f32",
                  comment="m_playerSpeed. 0.90 at the 1x portal -- measured, "
                          "not assumed."),
            Field("vehicleFlags", "u16",
                  comment="GdrlVehicleFlag bitfield, raw and uncollapsed"),
            Field("isUpsideDown", "u8", comment="m_isUpsideDown"),
            Field("isSideways", "u8", comment="m_isSideways"),
            Field("isOnGround", "u8", comment="m_isOnGround"),
            Field("isDashing", "u8", comment="m_isDashing"),
            Field("present", "u8", comment="the PlayerObject pointer was non-null"),
        ],
    ),
    Struct(
        name="GdrlObject",
        comment=(
            "One GameObject in the observation window. README spec, lines 582-595,\n"
            "adopted field for field, plus two raw fields the spec derived from.\n"
            "\n"
            "halfW/halfH come from getObjectRect() rather than m_width/m_height:\n"
            "the latter are untransformed sprite extents, the rect is what\n"
            "collision actually uses.\n"
            "\n"
            "'known' is per-slot validity. Zero means the slot carries no object,\n"
            "which is a different statement from an object sitting at the origin."
        ),
        fields=[
            Field("uniqueID", "i32", comment="GameObject::m_uniqueID"),
            Field("objectID", "i16", comment="GameObject::m_objectID"),
            Field("kind", "u8",
                  comment="GdrlObjectKind, collapsed from m_objectType. Derived; "
                          "objectType below is the raw source and env.py checks "
                          "this against its own derivation."),
            Field("groupCount", "u8", comment="GameObject::m_groupCount, clamped to 10"),
            Field("groups", "i16", count=MAX_GROUPS,
                  comment="GameObject::m_groups (std::array<short,10>*)"),
            Field("objectType", "i16",
                  comment="raw GameObjectType. NOT in the README spec; added so "
                          "the `kind` collapse above is auditable rather than "
                          "trusted."),
            Field("x", "f64", comment="m_positionX (doubles in GD)"),
            Field("y", "f64", comment="m_positionY"),
            Field("halfW", "f32", comment="getObjectRect().size.width  * 0.5"),
            Field("halfH", "f32", comment="getObjectRect().size.height * 0.5"),
            Field("rotation", "f32", comment="CCNode::getRotation()"),
            Field("scaleX", "f32", comment="m_scaleX"),
            Field("scaleY", "f32", comment="m_scaleY"),
            Field("isHazard", "u8",
                  comment="m_objectType == Hazard || m_slopeIsHazard. Derived; "
                          "slopeIsHazard below is the raw source."),
            Field("isGroupDisabled", "u8", comment="m_isGroupDisabled"),
            Field("slopeIsHazard", "u8",
                  comment="raw m_slopeIsHazard. NOT in the README spec; a slope "
                          "that kills and one that does not share a "
                          "GameObjectType, so the raw flag is the only way to "
                          "check the isHazard collapse."),
            Field("known", "u8",
                  comment="slot validity. 0 = no object here, which is not the "
                          "same as an object at (0,0)."),
        ],
    ),
    Struct(
        name="GdrlGroupCommand",
        comment=(
            "One live GroupCommandObject2. README spec, lines 597-629, adopted\n"
            "verbatim; the struct offsets in the comments are the ones verified\n"
            "against the instructions in GroupCommandObject2::step (m1 0x44722c)\n"
            "and ::updateAction (m1 0x4472fc), not against field adjacency.\n"
            "\n"
            "Which GJEffectManager container these are read out of is UNVERIFIED --\n"
            "see GDRL_PROBE_CMDVEC in mod/src/probes.cpp. Everything downstream of\n"
            "this struct depends on that one measurement and nothing else does."
        ),
        fields=[
            Field("targetGroupID", "i32", comment="+0x28  m_targetGroupID"),
            Field("centerGroupID", "i32", comment="+0x2c  m_centerGroupID"),
            Field("commandType", "i32", comment="+0xd0  m_commandType"),
            Field("actionType1", "i32", comment="+0x190 m_actionType1  1=x 2=y 3/4=angular"),
            Field("actionType2", "i32", comment="+0x194 m_actionType2"),
            Field("easingType", "i32", comment="+0x0c  m_easingType"),
            Field("triggerUniqueID", "i32", comment="+0x158 m_triggerUniqueID"),
            Field("controlID", "i32", comment="+0x15c m_controlID"),
            Field("actionValue1", "f64", comment="+0x198 m_actionValue1"),
            Field("actionValue2", "f64", comment="+0x1a0 m_actionValue2"),
            Field("duration", "f64", comment="+0x18  m_duration"),
            Field("easingRate", "f64", comment="+0x10  m_easingRate"),
            Field("currentXOffset", "f64", comment="+0x30  m_currentXOffset"),
            Field("currentYOffset", "f64", comment="+0x38  m_currentYOffset"),
            Field("currentAngular", "f64",
                  comment="+0x90  m_currentRotateOrTransformValue"),
            Field("moveModX", "f64", comment="+0x80  m_moveModX"),
            Field("moveModY", "f64", comment="+0x88  m_moveModY"),
            Field("deltaTimeInFloat", "f32", comment="+0x1ac m_deltaTimeInFloat (elapsed)"),
            Field("finished", "u8", comment="+0x70  m_finished"),
            Field("disabled", "u8", comment="+0x71  m_disabled"),
            Field("lockedInX", "u8", comment="+0x77  m_lockedInX"),
            Field("lockedInY", "u8", comment="+0x78  m_lockedInY"),
            Field("lockToPlayerX", "u8", comment="+0x73  m_lockToPlayerX"),
            Field("lockToPlayerY", "u8", comment="+0x74  m_lockToPlayerY"),
            Field("lockToCameraX", "u8", comment="+0x75  m_lockToCameraX"),
            Field("lockToCameraY", "u8", comment="+0x76  m_lockToCameraY"),
            Field("unmodellable", "u8",
                  comment="NOT a GD field -- set by the mod when the command came "
                          "from a player-follow, advanced-follow or keyframe "
                          "source. Explicit, because 'all lock flags false' is "
                          "not evidence of 'it is a plain move'."),
            Field("known", "u8", comment="slot validity"),
        ],
    ),
    Struct(
        name="GdrlPendingTrigger",
        comment=(
            "An EffectGameObject that has not fired yet. README spec, lines\n"
            "631-655, adopted verbatim.\n"
            "\n"
            "The activation x comes from EffectGameObject::spawnXPosition\n"
            "(m1 0x1741b8), which is exactly: if (m_isSpawnTriggered ||\n"
            "m_isTouchTriggered) return m_spawnXPosition; else return\n"
            "getPosition().x. For an event-activated trigger the stored value\n"
            "means 'where it last fired', not 'where it will fire' -- so the\n"
            "isTouchTriggered / isSpawnTriggered flags below are not decoration,\n"
            "they say whether activationX means anything at all."
        ),
        fields=[
            Field("targetGroupID", "i32", comment="m_targetGroupID"),
            Field("centerGroupID", "i32", comment="m_centerGroupID"),
            Field("times360", "i32", comment="m_times360 (property 69)"),
            Field("easingType", "i32", comment="m_easingType"),
            Field("activationX", "f32", comment="EffectGameObject::spawnXPosition()"),
            Field("duration", "f32", comment="m_duration"),
            Field("moveOffsetX", "f32", comment="m_moveOffset.x (properties 28/29)"),
            Field("moveOffsetY", "f32", comment="m_moveOffset.y"),
            Field("rotationDegrees", "f32", comment="m_rotationDegrees (property 68)"),
            Field("easingRate", "f32", comment="m_easingRate"),
            Field("spawnTriggerDelay", "f32", comment="m_spawnTriggerDelay"),
            Field("objectID", "i16", comment="m_objectID -- which trigger kind"),
            Field("isTouchTriggered", "u8", comment="m_isTouchTriggered"),
            Field("isSpawnTriggered", "u8", comment="m_isSpawnTriggered"),
            Field("isMultiTriggered", "u8", comment="m_isMultiTriggered"),
            Field("useMoveTarget", "u8", comment="m_useMoveTarget (property 100)"),
            Field("moveTargetMode", "u8", comment="m_moveTargetMode (MoveTargetType)"),
            Field("lockToPlayerX", "u8", comment="m_lockToPlayerX"),
            Field("lockToPlayerY", "u8", comment="m_lockToPlayerY"),
            Field("targetIsRemapped", "u8",
                  comment="mod-computed: the object is a Rand/Sequence trigger, "
                          "or its group appears in "
                          "GJBaseGameLayer::m_spawnRemapTriggers. The target "
                          "group is drawn from m_randomSeed at fire time and is "
                          "not a property of the level."),
            Field("known", "u8", comment="slot validity"),
        ],
    ),
    Struct(
        name="GdrlSpeedSegment",
        comment=(
            "One speed-portal boundary ahead. README spec, lines 657-660.\n"
            "\n"
            "bucket indexes trajectory.py's UNITS_PER_TICK, of which only index 1\n"
            "(1x, 1.298250437 units/tick) is measured here; the other four are\n"
            "community values and SpeedProfile.certainty() downgrades any arrival\n"
            "time that depends on them."
        ),
        fields=[
            Field("startX", "f32"),
            Field("bucket", "i32", comment="index into SPEED_MULTIPLIERS"),
            Field("known", "u8", comment="slot validity"),
        ],
    ),
    Struct(
        name="GdrlValidity",
        comment=(
            "Can this frame be believed? Answered in-band, on the frame's own\n"
            "evidence, so Python never has to correlate against a log line by\n"
            "position. This is the binary-channel equivalent of\n"
            "input[clean blocked=0 leaked=0]; a decoder that ignores it is not\n"
            "reading evidence, it is reading numbers."
        ),
        fields=[
            Field("blocked", "i64",
                  comment="pushes dropped at queueButton this attempt (g_gdrlBlocked)"),
            Field("leaked", "i64",
                  comment="pushes that reached the player anyway (g_gdrlLeaked). "
                          ">0 voids the attempt."),
            Field("uiEvents", "i64",
                  comment="UI key/touch events swallowed, lifetime (g_gdrlUiEvents)"),
            Field("timeouts", "i64",
                  comment="bounded action-waits that expired this attempt. A "
                          "silent timeout is indistinguishable from a policy that "
                          "chose not to jump, so it is counted and surfaced."),
            Field("inputVerdict", "u32", comment="GdrlInputVerdict"),
            Field("status", "u32", comment="GdrlStatus"),
            Field("levelID", "i32", comment="GJGameLevel::m_levelID of the running level"),
            Field("pinnedLevelID", "i32",
                  comment="the level GDRL_PIN_LEVEL demands, or -1 when unpinned"),
            Field("objectCountTotal", "i32",
                  comment="GJBaseGameLayer::m_objects->count(). Fewer than 10 "
                          "means the level string never loaded and every "
                          "measurement from the run is invalid."),
            Field("levelPinned", "u8", comment="GDRL_PIN_LEVEL is on"),
            Field("blockInput", "u8", comment="GDRL_BLOCK_INPUT is on"),
            Field("objectsTruncated", "u8",
                  comment="a capacity was hit; some columns are TRUNCATED"),
        ],
    ),
    Struct(
        name="GdrlObsHeader",
        comment=(
            "README spec, lines 566-580, adopted field for field and then extended\n"
            "below the marked line. Nothing above that line was renamed, retyped or\n"
            "reordered.\n"
            "\n"
            "tick is lround(m_attemptTime * 240) -- the verified placement clock.\n"
            "attemptTime rides alongside so the derivation is auditable from Python\n"
            "rather than trusted: t*240 is NOT an exact integer (GD accumulates a\n"
            "float32 1/240 into a double; the residual is 2.039e-05 ticks at 391\n"
            "ticks and stays unambiguous for ~11 hours), so never test t == n/240.\n"
            "\n"
            "stepIndex is the mod's own count of physics steps this attempt. It\n"
            "exists to make the tick derivation self-checking: at timeWarp 1,\n"
            "consecutive observations must advance stepIndex and tick by the same\n"
            "amount. Whether m_attemptTime is updated before or after\n"
            "prepareMoveActions within a step is UNVERIFIED, and this is how the\n"
            "answer shows up in the data instead of being assumed."
        ),
        fields=[
            # --- README spec, verbatim -------------------------------------
            Field("magic", "u32", comment="GDRL_OBS_MAGIC"),
            Field("version", "u16", comment="bump on any layout change"),
            Field("flags", "u16", comment="GdrlHeaderFlag bitfield"),
            Field("tick", "i32", comment="lround(PlayLayer::m_attemptTime * 240)"),
            Field("attemptTime", "f64", comment="PlayLayer::m_attemptTime (the verified clock)"),
            Field("dtPerStep", "f32",
                  comment="the 's10' fed to prepareMoveActions this step"),
            Field("timeWarp", "f32", comment="GJGameState::m_timeWarp"),
            Field("playerX", "f64", comment="PlayerObject::getPositionX()"),
            Field("playerY", "f64", comment="PlayerObject::getPositionY()"),
            Field("playerSpeed", "f32", comment="PlayerObject::m_playerSpeed"),
            Field("objectCount", "u32", comment="entries in objects[] with known=1"),
            Field("commandCount", "u32", comment="entries in commands[]"),
            Field("pendingCount", "u32", comment="entries in pending[]"),
            Field("speedSegCount", "u32", comment="entries in speedSegs[]"),
            # --- extensions ------------------------------------------------
            Field("attempt", "i64", comment="EXT: attempt index within this process"),
            Field("frame", "i64", comment="EXT: render frames elapsed this attempt"),
            Field("stepIndex", "i64",
                  comment="EXT: physics steps counted by the mod this attempt; "
                          "cross-checks `tick`"),
            Field("dtIn", "f32", comment="EXT: dt update() was called with, raw"),
            Field("dtUsed", "f32", comment="EXT: dt actually passed to the original"),
            Field("windowMinX", "f32", comment="EXT: scanned region, world units"),
            Field("windowMaxX", "f32"),
            Field("windowMinY", "f32"),
            Field("windowMaxY", "f32"),
            Field("sectionXFactor", "f32",
                  comment="EXT: m_sectionXFactor, emitted so the column indexing "
                          "can be checked rather than assumed"),
            Field("sectionYFactor", "f32", comment="EXT: m_sectionYFactor"),
            Field("levelLength", "f32", comment="EXT: m_levelLength"),
            Field("coverageStartCol", "i32",
                  comment="EXT: the section column coverage[0] describes"),
            Field("sectionColumns", "i32",
                  comment="EXT: m_sections.size(); columns past this are ABSENT"),
            Field("objectsDropped", "u16",
                  comment="EXT: objects inside the window that did not fit. >0 "
                          "means truncated, not empty."),
            Field("commandsDropped", "u16", comment="EXT: live commands that did not fit"),
            Field("pendingDropped", "u16", comment="EXT: pending triggers that did not fit"),
            Field("isDualMode", "u8",
                  comment="EXT: GJGameState::m_isDualMode -- the real dual test"),
            Field("isPaused", "u8", comment="EXT: PlayLayer::m_isPaused"),
            Field("inResetDelay", "u8", comment="EXT: PlayLayer::m_inResetDelay"),
        ],
    ),
    Struct(
        name="GdrlObservation",
        comment=(
            "One physics-step observation: header, validity, both players, the\n"
            "coverage mask, and the four fixed-capacity tables.\n"
            "\n"
            "seq is a seqlock: odd while a write is in progress, even and advanced\n"
            "by two when it completes. A reader samples seq, copies the body,\n"
            "re-samples seq, and retries if it moved or was odd. The ping-pong\n"
            "protocol already means the writer will not begin observation n+1 until\n"
            "it has consumed the action for n -- but that guarantee evaporates the\n"
            "moment a bounded wait times out and the game runs on, which is exactly\n"
            "when a torn read would be misdiagnosed as a physics anomaly."
        ),
        fields=[
            Field("seq", "u64", comment="seqlock; odd == write in progress"),
            Field("header", "@GdrlObsHeader"),
            Field("validity", "@GdrlValidity"),
            Field("players", "@GdrlPlayerState", count=2,
                  comment="[0] is player 1. header.playerX/Y/Speed duplicate "
                          "players[0] because the README spec declared them; "
                          "env.py asserts they agree, turning the redundancy into "
                          "a check that the mod read the player it thinks it did."),
            Field("coverage", "u8", count=COVERAGE_COLS, comment="GdrlCoverage per column"),
            Field("objects", "@GdrlObject", count=MAX_OBJECTS),
            Field("commands", "@GdrlGroupCommand", count=MAX_COMMANDS),
            Field("pending", "@GdrlPendingTrigger", count=MAX_PENDING),
            Field("speedSegs", "@GdrlSpeedSegment", count=MAX_SPEED_SEGS),
        ],
    ),
    Struct(
        name="GdrlAction",
        comment=(
            "One scheduled input. Placed by PHYSICS TICK, never by render frame:\n"
            "the render:physics ratio is not constant (316-440 render frames\n"
            "covered the same 391 ticks), so a frame-placed input lands on a\n"
            "different tick between runs and reintroduces exactly the\n"
            "nondeterminism the engine itself does not have."
        ),
        fields=[
            Field("targetTick", "i64",
                  comment="the physics tick the input must take effect on"),
            Field("holdTicks", "i32",
                  comment="HOLD only: ticks between the push and its release"),
            Field("kind", "u8", comment="GdrlActionKind"),
            Field("button", "u8", comment="PlayerButton; 1 = jump"),
            Field("player", "u8", comment="0 = player 1, 1 = player 2"),
        ],
    ),
    Struct(
        name="GdrlActionBlock",
        comment=(
            "Python's reply to one observation. seq must equal the seq of the\n"
            "observation being answered; the mod counts a mismatch as a protocol\n"
            "error rather than executing a stale plan against the wrong tick."
        ),
        fields=[
            Field("seq", "u64", comment="the GdrlObservation.seq being answered"),
            Field("nonce", "u64", comment="opaque; Python's own bookkeeping"),
            Field("advanceSteps", "i32",
                  comment="publish the next observation this many physics steps "
                          "later. 1 = every step (full fidelity). Larger values "
                          "trade observation rate for throughput WITHOUT losing "
                          "tick-exact action placement, because scheduled actions "
                          "are still queued per step in between."),
            Field("count", "u16", comment="entries in actions[]"),
            Field("detach", "u8",
                  comment="1 asks the mod to stop blocking and free-run"),
            Field("actions", "@GdrlAction", count=MAX_ACTIONS),
        ],
    ),
    Struct(
        name="GdrlControl",
        comment=(
            "The handshake block, at offset 0 of the mapping.\n"
            "\n"
            "The first four fields must never move (see the module docstring): an\n"
            "old Python has to be able to read magic/version/hash out of a newer\n"
            "mapping and report the real mismatch instead of a confusing one.\n"
            "\n"
            "modPid exists because a crashed GD leaves the segment behind. The mod\n"
            "shm_unlinks and recreates on every launch, so a stale segment can\n"
            "never be reused -- but a Python that mapped the old one before the\n"
            "relaunch still holds a valid mapping of a dead process, and\n"
            "kill(modPid, 0) is the only cheap way to tell."
        ),
        fields=[
            Field("magic", "u32", comment="GDRL_MAGIC ('GDRL' little-endian)"),
            Field("wireVersion", "u16", comment="GDRL_WIRE_VERSION"),
            Field("headerSize", "u16", comment="sizeof(GdrlControl)"),
            Field("schemaHash", "u64", comment="GDRL_SCHEMA_HASH"),
            Field("obsOffset", "u32", comment="byte offset of GdrlObservation"),
            Field("obsSize", "u32", comment="sizeof(GdrlObservation)"),
            Field("actOffset", "u32", comment="byte offset of GdrlActionBlock"),
            Field("actSize", "u32", comment="sizeof(GdrlActionBlock)"),
            Field("totalSize", "u32", comment="sizeof(GdrlShared)"),
            Field("maxObjects", "u32", comment="GDRL_MAX_OBJECTS"),
            Field("maxCommands", "u32", comment="GDRL_MAX_COMMANDS"),
            Field("maxPending", "u32", comment="GDRL_MAX_PENDING"),
            Field("maxSpeedSegs", "u32", comment="GDRL_MAX_SPEED_SEGS"),
            Field("maxActions", "u32", comment="GDRL_MAX_ACTIONS"),
            Field("coverageCols", "u32", comment="GDRL_COVERAGE_COLS"),
            Field("tickHz", "u32", comment="240; the clock `tick` is denominated in"),
            Field("modPid", "u32", comment="getpid() of the GD process"),
            Field("modAlive", "u32", comment="1 while the mod holds the mapping"),
            Field("pyAttached", "u32",
                  comment="1 while Python is answering. The mod only ever BLOCKS "
                          "when this is 1, so an unattached GD can never hang on "
                          "a Python that was never there."),
            Field("waitBudgetUs", "u32", comment="how long the game thread will block"),
            Field("detachedByTimeout", "u32",
                  comment="times the mod gave up on Python and resumed free-running"),
            Field("obsSeq", "u64", comment="mirror of the last published obs seq"),
            Field("actSeq", "u64", comment="last action seq the mod consumed"),
            Field("stepsServed", "u64", comment="observation/action round trips"),
            Field("stepTimeouts", "u64",
                  comment="bounded waits that expired, lifetime. Must be 0 for a "
                          "run to be usable as evidence."),
            Field("protocolErrors", "u64",
                  comment="stale or mismatched action seqs the mod refused"),
            Field("pyHeartbeat", "u64", comment="Python bumps this every step"),
        ],
    ),
    Struct(
        name="GdrlShared",
        comment=(
            "The whole mapping: one control block, one observation slot, one action\n"
            "slot. Single-slot is safe only because the protocol is strictly\n"
            "ping-pong; an asynchronous variant would need a ring, and the seqlock\n"
            "on the observation is what keeps a timed-out step from tearing."
        ),
        fields=[
            Field("control", "@GdrlControl"),
            Field("obs", "@GdrlObservation"),
            Field("action", "@GdrlActionBlock"),
        ],
    ),
]


def build() -> dict[str, Struct]:
    """Lay every struct out. Declaration order must put dependencies first."""
    table: dict[str, Struct] = {}
    for s in STRUCTS:
        for f in s.fields:
            if f.type.startswith("@") and f.type[1:] not in table:
                raise ValueError(
                    f"{s.name}.{f.name} references {f.type[1:]} before it is declared"
                )
        lay_out(s, table)
        table[s.name] = s
    return table


# ---------------------------------------------------------------------------
# The schema hash
# ---------------------------------------------------------------------------

def canonical_source(table: dict[str, Struct]) -> str:
    """The exact text the schema hash is taken over.

    Comments are excluded on purpose: a comment edit must not invalidate a
    checked-in trained run. Everything that changes what a byte *means* --
    names, types, counts, offsets, sizes, enum values, capacities -- is in.
    Offsets are included even though they are derived, so that a change to the
    layout algorithm itself also invalidates the hash.
    """
    lines: list[str] = [
        f"magic={MAGIC:#010x}",
        f"wire={WIRE_VERSION}",
        f"tickHz={TICK_HZ}",
        f"maxObjects={MAX_OBJECTS}",
        f"maxGroups={MAX_GROUPS}",
        f"maxCommands={MAX_COMMANDS}",
        f"maxPending={MAX_PENDING}",
        f"maxSpeedSegs={MAX_SPEED_SEGS}",
        f"maxActions={MAX_ACTIONS}",
        f"coverageCols={COVERAGE_COLS}",
    ]
    for e in ENUMS:
        lines.append(f"enum {e.name}:{e.underlying}")
        for name, value in e.members:
            lines.append(f"  {name}={value}")
    for s in STRUCTS:
        laid = table[s.name]
        lines.append(f"struct {s.name} size={laid.size} align={laid.align}")
        for f in laid.laid_out:
            lines.append(f"  {f.offset}:{f.name}:{f.type}:{f.count}:{f.size}")
    return "\n".join(lines) + "\n"


def schema_hash(table: dict[str, Struct]) -> int:
    digest = hashlib.sha256(canonical_source(table).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "little")


# ---------------------------------------------------------------------------
# C++ emission
# ---------------------------------------------------------------------------

_BANNER = (
    "// GENERATED FILE -- DO NOT EDIT.\n"
    "//\n"
    "// Emitted by trainer/schema.py. Edit that and re-run `python3 trainer/schema.py`.\n"
    "// trainer/test_schema.py regenerates this in memory and fails if it differs, so\n"
    "// an edit here is a test failure rather than a silent C++/Python skew.\n"
)


def _cpp_type(type_name: str) -> str:
    if type_name.startswith("@"):
        return type_name[1:]
    return _SCALARS[type_name][0]


def _comment_block(text: str, indent: str = "") -> list[str]:
    return [f"{indent}// {line}".rstrip() for line in text.split("\n")]


def emit_cpp(table: dict[str, Struct]) -> str:
    h = schema_hash(table)
    out: list[str] = [_BANNER, ""]
    out.append("#pragma once")
    out.append("")
    out.append("#include <cstdint>")
    out.append("#include <cstddef>")
    out.append("#include <type_traits>")
    out.append("")
    out.append("// All records are little-endian, naturally aligned, no bitfields --")
    out.append("// the convention the README telemetry spec already fixed. Every pad byte")
    out.append("// is a named member and every field offset carries a static_assert, so a")
    out.append("// compiler that lays these out differently from the Python model fails")
    out.append("// the build instead of writing a wire Python will misread.")
    out.append("")
    out.append(f"#define GDRL_MAGIC 0x{MAGIC:08X}u")
    out.append("#define GDRL_OBS_MAGIC GDRL_MAGIC   // the README spec's name for it")
    out.append(f"#define GDRL_WIRE_VERSION {WIRE_VERSION}u")
    out.append(f"#define GDRL_SCHEMA_HASH 0x{h:016X}ull")
    out.append(f"#define GDRL_TICK_HZ {TICK_HZ}")
    out.append(f"#define GDRL_MAX_OBJECTS {MAX_OBJECTS}")
    out.append(f"#define GDRL_MAX_GROUPS {MAX_GROUPS}")
    out.append(f"#define GDRL_MAX_COMMANDS {MAX_COMMANDS}")
    out.append(f"#define GDRL_MAX_PENDING {MAX_PENDING}")
    out.append(f"#define GDRL_MAX_SPEED_SEGS {MAX_SPEED_SEGS}")
    out.append(f"#define GDRL_MAX_ACTIONS {MAX_ACTIONS}")
    out.append(f"#define GDRL_COVERAGE_COLS {COVERAGE_COLS}")
    out.append("")

    for e in ENUMS:
        out.extend(_comment_block(e.comment))
        out.append(f"enum class {e.name} : {_SCALARS[e.underlying][0]} {{")
        for name, value in e.members:
            out.append(f"    {name} = {value},")
        out.append("};")
        out.append("")

    for s in STRUCTS:
        laid = table[s.name]
        out.extend(_comment_block(s.comment))
        out.append(f"struct {s.name} {{")
        for f in laid.laid_out:
            decl = f"    {_cpp_type(f.type)} {f.name}"
            if f.count > 1:
                decl += f"[{f.count}]"
            decl += ";"
            if f.is_pad:
                out.append(decl.ljust(50) + "// alignment padding, never read")
                continue
            if not f.comment:
                out.append(decl)
                continue
            parts = f.comment.split("\n")
            out.append(decl.ljust(50) + f"// {parts[0]}")
            for extra in parts[1:]:
                out.append(" " * 50 + f"// {extra}")
        out.append("};")
        out.append(f"static_assert(sizeof({s.name}) == {laid.size}, "
                   f"\"{s.name} size drifted from trainer/schema.py\");")
        out.append(f"static_assert(alignof({s.name}) == {laid.align}, "
                   f"\"{s.name} alignment drifted from trainer/schema.py\");")
        out.append(f"static_assert(std::is_standard_layout_v<{s.name}>, "
                   f"\"{s.name} must be POD to cross the wire\");")
        out.append(f"static_assert(std::is_trivially_copyable_v<{s.name}>, "
                   f"\"{s.name} must be POD to cross the wire\");")
        for f in laid.laid_out:
            if f.is_pad:
                continue
            out.append(f"static_assert(offsetof({s.name}, {f.name}) == {f.offset}, "
                       f"\"{s.name}.{f.name} offset drifted\");")
        out.append("")

    out.append("// The four fields an older Python must still be able to read out of a")
    out.append("// newer mapping in order to report the right mismatch rather than a")
    out.append("// confusing one. These offsets are frozen across every wire version.")
    out.append("static_assert(offsetof(GdrlShared, control) == 0, \"control must be first\");")
    out.append("static_assert(offsetof(GdrlControl, magic) == 0, \"magic offset is frozen\");")
    out.append("static_assert(offsetof(GdrlControl, wireVersion) == 4, \"wireVersion offset is frozen\");")
    out.append("static_assert(offsetof(GdrlControl, headerSize) == 6, \"headerSize offset is frozen\");")
    out.append("static_assert(offsetof(GdrlControl, schemaHash) == 8, \"schemaHash offset is frozen\");")
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Python emission
# ---------------------------------------------------------------------------

_PY_BANNER = (
    '"""GENERATED FILE -- DO NOT EDIT.\n'
    "\n"
    "Emitted by trainer/schema.py. Edit that and re-run `python3 trainer/schema.py`.\n"
    "trainer/test_schema.py regenerates this in memory and fails if it differs, so an\n"
    "edit here is a test failure rather than a silent C++/Python skew.\n"
    '"""'
)


def _np_format(type_name: str, count: int) -> str:
    if type_name.startswith("@"):
        base = f"{type_name[1:]}_DTYPE"
    else:
        base = f"'<{_SCALARS[type_name][3]}'"
    if count > 1:
        return f"({base}, ({count},))"
    return base


def emit_python(table: dict[str, Struct]) -> str:
    h = schema_hash(table)
    out: list[str] = [_PY_BANNER, ""]
    out.append("from __future__ import annotations")
    out.append("")
    out.append("from enum import IntEnum")
    out.append("")
    out.append("import numpy as np")
    out.append("")
    out.append(f"GDRL_MAGIC = 0x{MAGIC:08X}")
    out.append(f"GDRL_WIRE_VERSION = {WIRE_VERSION}")
    out.append(f"GDRL_SCHEMA_HASH = 0x{h:016X}")
    out.append(f"GDRL_TICK_HZ = {TICK_HZ}")
    out.append(f"GDRL_MAX_OBJECTS = {MAX_OBJECTS}")
    out.append(f"GDRL_MAX_GROUPS = {MAX_GROUPS}")
    out.append(f"GDRL_MAX_COMMANDS = {MAX_COMMANDS}")
    out.append(f"GDRL_MAX_PENDING = {MAX_PENDING}")
    out.append(f"GDRL_MAX_SPEED_SEGS = {MAX_SPEED_SEGS}")
    out.append(f"GDRL_MAX_ACTIONS = {MAX_ACTIONS}")
    out.append(f"GDRL_COVERAGE_COLS = {COVERAGE_COLS}")
    out.append("")
    out.append("# Byte offsets of the four fields that never move, so a Python built")
    out.append("# against one wire version can still diagnose a mapping written by")
    out.append("# another instead of decoding it as garbage.")
    ctrl = table["GdrlControl"]
    for name in ("magic", "wireVersion", "headerSize", "schemaHash"):
        off = next(f.offset for f in ctrl.laid_out if f.name == name)
        out.append(f"OFFSET_{name.upper()} = {off}")
    out.append("")

    for e in ENUMS:
        out.append(f"class {e.name}(IntEnum):")
        out.append('    """')
        for line in e.comment.split("\n"):
            out.append(f"    {line}".rstrip())
        out.append('    """')
        out.append("")
        for name, value in e.members:
            out.append(f"    {name} = {value}")
        out.append("")
        out.append("")

    out.append("# Padding is excluded from the dtypes below and replaced by explicit")
    out.append("# offsets plus an itemsize, so numpy's view of a record is exactly the")
    out.append("# bytes the C++ static_asserts pin -- never numpy's own alignment guess.")
    out.append("")
    for s in STRUCTS:
        laid = table[s.name]
        keep = [f for f in laid.laid_out if not f.is_pad]
        out.append(f"# {s.name}: {laid.size} bytes, align {laid.align}")
        out.append(f"{s.name}_DTYPE = np.dtype({{")
        out.append(f"    'names': {[f.name for f in keep]!r},")
        out.append("    'formats': [")
        for f in keep:
            out.append(f"        {_np_format(f.type, f.count)},")
        out.append("    ],")
        out.append(f"    'offsets': {[f.offset for f in keep]!r},")
        out.append(f"    'itemsize': {laid.size},")
        out.append("})")
        out.append("")

    out.append("STRUCT_SIZES = {")
    for s in STRUCTS:
        out.append(f"    '{s.name}': {table[s.name].size},")
    out.append("}")
    out.append("")
    out.append("# Offsets of the three top-level regions inside the mapping, so env.py")
    out.append("# can cross-check them against what the mod wrote into GdrlControl")
    out.append("# rather than trusting either side alone.")
    shared = table["GdrlShared"]
    for name in ("control", "obs", "action"):
        off = next(f.offset for f in shared.laid_out if f.name == name)
        out.append(f"OFFSET_{name.upper()} = {off}")
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

REPO = Path(__file__).resolve().parent.parent
CPP_PATH = REPO / "mod" / "src" / "gdrl_schema.hpp"
PY_PATH = REPO / "trainer" / "schema_generated.py"


def generate() -> dict[Path, str]:
    table = build()
    return {CPP_PATH: emit_cpp(table), PY_PATH: emit_python(table)}


def main(argv: list[str]) -> int:
    check = "--check" in argv
    drift = False
    for path, text in generate().items():
        existing = path.read_text() if path.exists() else None
        if existing == text:
            print(f"unchanged  {path.relative_to(REPO)}")
            continue
        drift = True
        if check:
            print(f"DRIFTED    {path.relative_to(REPO)}", file=sys.stderr)
        else:
            path.write_text(text)
            print(f"wrote      {path.relative_to(REPO)}")
    if check and drift:
        print("regenerate with: python3 trainer/schema.py", file=sys.stderr)
        return 1
    table = build()
    print(f"schema hash = 0x{schema_hash(table):016X}")
    for s in STRUCTS:
        print(f"  {s.name:<22} {table[s.name].size:>8} bytes  align {table[s.name].align}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
