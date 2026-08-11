"""GENERATED FILE -- DO NOT EDIT.

Emitted by trainer/schema.py. Edit that and re-run `python3 trainer/schema.py`.
trainer/test_schema.py regenerates this in memory and fails if it differs, so an
edit here is a test failure rather than a silent C++/Python skew.
"""

from __future__ import annotations

from enum import IntEnum

import numpy as np

GDRL_MAGIC = 0x4C524447
GDRL_WIRE_VERSION = 1
GDRL_SCHEMA_HASH = 0xBEE47E250E167BC7
GDRL_TICK_HZ = 240
GDRL_MAX_OBJECTS = 256
GDRL_MAX_GROUPS = 10
GDRL_MAX_COMMANDS = 64
GDRL_MAX_PENDING = 64
GDRL_MAX_SPEED_SEGS = 16
GDRL_MAX_ACTIONS = 8
GDRL_COVERAGE_COLS = 64

# Byte offsets of the four fields that never move, so a Python built
# against one wire version can still diagnose a mapping written by
# another instead of decoding it as garbage.
OFFSET_MAGIC = 0
OFFSET_WIREVERSION = 4
OFFSET_HEADERSIZE = 6
OFFSET_SCHEMAHASH = 8

class GdrlVehicle(IntEnum):
    """
    A derived label, NOT a wire field. The wire carries the raw flag word
    (GdrlVehicleFlag); Python derives this, so the derivation order --
    swing before ship, spider before ball -- is testable and the
    overlapping-flags case is visible instead of being papered over by the
    ordering. Declared here only so conditioning.py imports it rather than
    redeclaring it, and so the C++ side keeps a name for the log lines.
    """

    CUBE = 0
    SHIP = 1
    BALL = 2
    UFO = 3
    WAVE = 4
    ROBOT = 5
    SPIDER = 6
    SWING = 7


class GdrlVehicleFlag(IntEnum):
    """
    Bit positions of PlayerObject's seven mode flags, in exactly the order
    main.cpp's modeFlagBits() already uses -- changing this order silently
    relabels every observation, so it is pinned by the schema hash.

    Cube is the absence of all seven and therefore has no bit. 'no bit set'
    and 'two bits set' are different observations, which is why the raw
    word crosses the wire rather than an eight-valued enum: an overlap is
    evidence that deriveVehicle's ordering is load-bearing rather than
    merely defensive, and collapsing in C++ would destroy that evidence.
    """

    SHIP = 1
    BALL = 2
    BIRD = 4
    DART = 8
    ROBOT = 16
    SPIDER = 32
    SWING = 64


class GdrlHeaderFlag(IntEnum):
    """
    GdrlObsHeader.flags, per the README spec (bit0 input-clean, bit1
    practice mode), extended.

    GAMEPLAY_STEP distinguishes the gameplay emission from the level-seek
    one. GJEffectManager::prepareMoveActions has two bl sites --
    GJBaseGameLayer::update (gameplay, inside the fixed-step loop, +0x9f8,
    loop body 0x122ea4..0x123d94) and GJBaseGameLayer::loadUpToPosition
    (the seek path). The mod sets this bit only for the former; an
    observation without it describes a level being fast-forwarded, not a
    level being played.

    The four *_UNAVAILABLE bits are the same known/unknown distinction the
    coverage mask makes, applied to whole tables. count==0 with the bit
    CLEAR means 'the mod looked and there were none'. count==0 with the
    bit SET means 'the mod did not look, because the measurement that
    would make the answer trustworthy has not been taken'. A decoder that
    treats those the same is asserting an empty world it never observed --
    which is exactly the failure trajectory.py's certainty channel exists
    to prevent.
    """

    INPUT_CLEAN = 1
    PRACTICE = 2
    GAMEPLAY_STEP = 4
    DUAL = 8
    OBJECTS_UNAVAILABLE = 16
    COMMANDS_UNAVAILABLE = 32
    PENDING_UNAVAILABLE = 64
    SPEEDSEGS_UNAVAILABLE = 128


class GdrlStatus(IntEnum):
    """
    Why this observation may not be usable. Carried in-band so Python can
    reject a frame on the frame's own evidence, the way every ATTEMPT line
    already carries input[clean blocked=0 leaked=0].

    LEVEL_EMPTY is the getMainLevel(id, dontGetLevelString=true) trap: a
    level object with 2 objects and levelLength 793 that loads, runs, and
    produces measurements that look real and mean nothing.
    """

    OK = 0
    NO_PLAYLAYER = 1
    NO_PLAYER = 2
    LEVEL_EMPTY = 3
    WRONG_LEVEL = 4
    PAUSED = 5
    RESET_PENDING = 6
    SEEK = 7


class GdrlInputVerdict(IntEnum):
    """
    The same three-valued verdict main.cpp prints on every ATTEMPT line.
    UNGUARDED does not mean 'probably fine': with GDRL_BLOCK_INPUT off, a
    stray keypress is indistinguishable from a policy action, and four
    consecutive runs were contaminated that way before anyone noticed.
    """

    UNGUARDED = 0
    CLEAN = 1
    INVALID = 2


class GdrlCoverage(IntEnum):
    """
    Objective E's epistemic mask, one entry per section column.

      UNKNOWN   -- not scanned. Outside the window, or the object array
                   filled before this column was reached.
      SCANNED   -- walked; every object in it made it into the array. A
                   SCANNED column holding no objects is genuinely empty.
      TRUNCATED -- walked, but the array filled mid-column. Incomplete.
      ABSENT    -- the column index is past the end of
                   GJBaseGameLayer::m_sections, so GD itself has no
                   geometry there. Known-empty, not unknown.

    An off-screen pit and an empty floor are the same bytes in a naive
    occupancy grid, so a policy cannot be conservative about uncertainty
    it cannot see. This is the same primitive trajectory.py already uses
    for motion (a certainty channel rather than a fabricated position),
    applied to visibility instead of to projection: env.py folds coverage
    into the same certainty channel rather than adding a parallel one.
    """

    UNKNOWN = 0
    SCANNED = 1
    TRUNCATED = 2
    ABSENT = 3


class GdrlObjectKind(IntEnum):
    """
    Must stay numerically identical to trajectory.py's ObjectKind, which
    is what TrajectoryRaster indexes its channels by.

    This IS an interpretation, and it is on the wire only because the
    README spec put it there. The raw GameObjectType and m_slopeIsHazard
    ride alongside in the same record, and env.py re-derives the kind and
    asserts agreement -- so the C++ collapse is checked rather than
    trusted, and Python remains the authority on what an object means.
    """

    HAZARD = 0
    SOLID = 1
    INTERACTIVE = 2
    OTHER = 3


class GdrlActionKind(IntEnum):
    """
    HOLD is (start_tick, hold_ticks): one record the mod expands into a
    push at targetTick and a release at targetTick + holdTicks. It is here
    in wire version 1 on purpose -- Objective C needs an action duration,
    and adding the field later would reshape every trained action record.
    PRESS/RELEASE remain so a policy can also hold across an arbitrary
    number of decisions without re-issuing.
    """

    NOOP = 0
    PRESS = 1
    RELEASE = 2
    HOLD = 3


# Padding is excluded from the dtypes below and replaced by explicit
# offsets plus an itemsize, so numpy's view of a record is exactly the
# bytes the C++ static_asserts pin -- never numpy's own alignment guess.

# GdrlPlayerState: 56 bytes, align 8
GdrlPlayerState_DTYPE = np.dtype({
    'names': ['x', 'y', 'yVelocity', 'gravity', 'rotation', 'vehicleSize', 'playerSpeed', 'vehicleFlags', 'isUpsideDown', 'isSideways', 'isOnGround', 'isDashing', 'present'],
    'formats': [
        '<f8',
        '<f8',
        '<f8',
        '<f8',
        '<f4',
        '<f4',
        '<f4',
        '<u2',
        '<u1',
        '<u1',
        '<u1',
        '<u1',
        '<u1',
    ],
    'offsets': [0, 8, 16, 24, 32, 36, 40, 44, 46, 47, 48, 49, 50],
    'itemsize': 56,
})

# GdrlObject: 72 bytes, align 8
GdrlObject_DTYPE = np.dtype({
    'names': ['uniqueID', 'objectID', 'kind', 'groupCount', 'groups', 'objectType', 'x', 'y', 'halfW', 'halfH', 'rotation', 'scaleX', 'scaleY', 'isHazard', 'isGroupDisabled', 'slopeIsHazard', 'known'],
    'formats': [
        '<i4',
        '<i2',
        '<u1',
        '<u1',
        ('<i2', (10,)),
        '<i2',
        '<f8',
        '<f8',
        '<f4',
        '<f4',
        '<f4',
        '<f4',
        '<f4',
        '<u1',
        '<u1',
        '<u1',
        '<u1',
    ],
    'offsets': [0, 4, 6, 7, 8, 28, 32, 40, 48, 52, 56, 60, 64, 68, 69, 70, 71],
    'itemsize': 72,
})

# GdrlGroupCommand: 120 bytes, align 8
GdrlGroupCommand_DTYPE = np.dtype({
    'names': ['targetGroupID', 'centerGroupID', 'commandType', 'actionType1', 'actionType2', 'easingType', 'triggerUniqueID', 'controlID', 'actionValue1', 'actionValue2', 'duration', 'easingRate', 'currentXOffset', 'currentYOffset', 'currentAngular', 'moveModX', 'moveModY', 'deltaTimeInFloat', 'finished', 'disabled', 'lockedInX', 'lockedInY', 'lockToPlayerX', 'lockToPlayerY', 'lockToCameraX', 'lockToCameraY', 'unmodellable', 'known'],
    'formats': [
        '<i4',
        '<i4',
        '<i4',
        '<i4',
        '<i4',
        '<i4',
        '<i4',
        '<i4',
        '<f8',
        '<f8',
        '<f8',
        '<f8',
        '<f8',
        '<f8',
        '<f8',
        '<f8',
        '<f8',
        '<f4',
        '<u1',
        '<u1',
        '<u1',
        '<u1',
        '<u1',
        '<u1',
        '<u1',
        '<u1',
        '<u1',
        '<u1',
    ],
    'offsets': [0, 4, 8, 12, 16, 20, 24, 28, 32, 40, 48, 56, 64, 72, 80, 88, 96, 104, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117],
    'itemsize': 120,
})

# GdrlPendingTrigger: 56 bytes, align 4
GdrlPendingTrigger_DTYPE = np.dtype({
    'names': ['targetGroupID', 'centerGroupID', 'times360', 'easingType', 'activationX', 'duration', 'moveOffsetX', 'moveOffsetY', 'rotationDegrees', 'easingRate', 'spawnTriggerDelay', 'objectID', 'isTouchTriggered', 'isSpawnTriggered', 'isMultiTriggered', 'useMoveTarget', 'moveTargetMode', 'lockToPlayerX', 'lockToPlayerY', 'targetIsRemapped', 'known'],
    'formats': [
        '<i4',
        '<i4',
        '<i4',
        '<i4',
        '<f4',
        '<f4',
        '<f4',
        '<f4',
        '<f4',
        '<f4',
        '<f4',
        '<i2',
        '<u1',
        '<u1',
        '<u1',
        '<u1',
        '<u1',
        '<u1',
        '<u1',
        '<u1',
        '<u1',
    ],
    'offsets': [0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 46, 47, 48, 49, 50, 51, 52, 53, 54],
    'itemsize': 56,
})

# GdrlSpeedSegment: 12 bytes, align 4
GdrlSpeedSegment_DTYPE = np.dtype({
    'names': ['startX', 'bucket', 'known'],
    'formats': [
        '<f4',
        '<i4',
        '<u1',
    ],
    'offsets': [0, 4, 8],
    'itemsize': 12,
})

# GdrlValidity: 56 bytes, align 8
GdrlValidity_DTYPE = np.dtype({
    'names': ['blocked', 'leaked', 'uiEvents', 'timeouts', 'inputVerdict', 'status', 'levelID', 'pinnedLevelID', 'objectCountTotal', 'levelPinned', 'blockInput', 'objectsTruncated'],
    'formats': [
        '<i8',
        '<i8',
        '<i8',
        '<i8',
        '<u4',
        '<u4',
        '<i4',
        '<i4',
        '<i4',
        '<u1',
        '<u1',
        '<u1',
    ],
    'offsets': [0, 8, 16, 24, 32, 36, 40, 44, 48, 52, 53, 54],
    'itemsize': 56,
})

# GdrlObsHeader: 152 bytes, align 8
GdrlObsHeader_DTYPE = np.dtype({
    'names': ['magic', 'version', 'flags', 'tick', 'attemptTime', 'dtPerStep', 'timeWarp', 'playerX', 'playerY', 'playerSpeed', 'objectCount', 'commandCount', 'pendingCount', 'speedSegCount', 'attempt', 'frame', 'stepIndex', 'dtIn', 'dtUsed', 'windowMinX', 'windowMaxX', 'windowMinY', 'windowMaxY', 'sectionXFactor', 'sectionYFactor', 'levelLength', 'coverageStartCol', 'sectionColumns', 'objectsDropped', 'commandsDropped', 'pendingDropped', 'isDualMode', 'isPaused', 'inResetDelay'],
    'formats': [
        '<u4',
        '<u2',
        '<u2',
        '<i4',
        '<f8',
        '<f4',
        '<f4',
        '<f8',
        '<f8',
        '<f4',
        '<u4',
        '<u4',
        '<u4',
        '<u4',
        '<i8',
        '<i8',
        '<i8',
        '<f4',
        '<f4',
        '<f4',
        '<f4',
        '<f4',
        '<f4',
        '<f4',
        '<f4',
        '<f4',
        '<i4',
        '<i4',
        '<u2',
        '<u2',
        '<u2',
        '<u1',
        '<u1',
        '<u1',
    ],
    'offsets': [0, 4, 6, 8, 16, 24, 28, 32, 40, 48, 52, 56, 60, 64, 72, 80, 88, 96, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 142, 144, 146, 147, 148],
    'itemsize': 152,
})

# GdrlObservation: 30280 bytes, align 8
GdrlObservation_DTYPE = np.dtype({
    'names': ['seq', 'header', 'validity', 'players', 'coverage', 'objects', 'commands', 'pending', 'speedSegs'],
    'formats': [
        '<u8',
        GdrlObsHeader_DTYPE,
        GdrlValidity_DTYPE,
        (GdrlPlayerState_DTYPE, (2,)),
        ('<u1', (64,)),
        (GdrlObject_DTYPE, (256,)),
        (GdrlGroupCommand_DTYPE, (64,)),
        (GdrlPendingTrigger_DTYPE, (64,)),
        (GdrlSpeedSegment_DTYPE, (16,)),
    ],
    'offsets': [0, 8, 160, 216, 328, 392, 18824, 26504, 30088],
    'itemsize': 30280,
})

# GdrlAction: 16 bytes, align 8
GdrlAction_DTYPE = np.dtype({
    'names': ['targetTick', 'holdTicks', 'kind', 'button', 'player'],
    'formats': [
        '<i8',
        '<i4',
        '<u1',
        '<u1',
        '<u1',
    ],
    'offsets': [0, 8, 12, 13, 14],
    'itemsize': 16,
})

# GdrlActionBlock: 152 bytes, align 8
GdrlActionBlock_DTYPE = np.dtype({
    'names': ['seq', 'nonce', 'advanceSteps', 'count', 'detach', 'actions'],
    'formats': [
        '<u8',
        '<u8',
        '<i4',
        '<u2',
        '<u1',
        (GdrlAction_DTYPE, (8,)),
    ],
    'offsets': [0, 8, 16, 20, 22, 24],
    'itemsize': 152,
})

# GdrlControl: 136 bytes, align 8
GdrlControl_DTYPE = np.dtype({
    'names': ['magic', 'wireVersion', 'headerSize', 'schemaHash', 'obsOffset', 'obsSize', 'actOffset', 'actSize', 'totalSize', 'maxObjects', 'maxCommands', 'maxPending', 'maxSpeedSegs', 'maxActions', 'coverageCols', 'tickHz', 'modPid', 'modAlive', 'pyAttached', 'waitBudgetUs', 'detachedByTimeout', 'obsSeq', 'actSeq', 'stepsServed', 'stepTimeouts', 'protocolErrors', 'pyHeartbeat'],
    'formats': [
        '<u4',
        '<u2',
        '<u2',
        '<u8',
        '<u4',
        '<u4',
        '<u4',
        '<u4',
        '<u4',
        '<u4',
        '<u4',
        '<u4',
        '<u4',
        '<u4',
        '<u4',
        '<u4',
        '<u4',
        '<u4',
        '<u4',
        '<u4',
        '<u4',
        '<u8',
        '<u8',
        '<u8',
        '<u8',
        '<u8',
        '<u8',
    ],
    'offsets': [0, 4, 6, 8, 16, 20, 24, 28, 32, 36, 40, 44, 48, 52, 56, 60, 64, 68, 72, 76, 80, 88, 96, 104, 112, 120, 128],
    'itemsize': 136,
})

# GdrlShared: 30568 bytes, align 8
GdrlShared_DTYPE = np.dtype({
    'names': ['control', 'obs', 'action'],
    'formats': [
        GdrlControl_DTYPE,
        GdrlObservation_DTYPE,
        GdrlActionBlock_DTYPE,
    ],
    'offsets': [0, 136, 30416],
    'itemsize': 30568,
})

STRUCT_SIZES = {
    'GdrlPlayerState': 56,
    'GdrlObject': 72,
    'GdrlGroupCommand': 120,
    'GdrlPendingTrigger': 56,
    'GdrlSpeedSegment': 12,
    'GdrlValidity': 56,
    'GdrlObsHeader': 152,
    'GdrlObservation': 30280,
    'GdrlAction': 16,
    'GdrlActionBlock': 152,
    'GdrlControl': 136,
    'GdrlShared': 30568,
}

# Offsets of the three top-level regions inside the mapping, so env.py
# can cross-check them against what the mod wrote into GdrlControl
# rather than trusting either side alone.
OFFSET_CONTROL = 0
OFFSET_OBS = 136
OFFSET_ACTION = 30416
