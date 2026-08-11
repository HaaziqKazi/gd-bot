"""The environment: attach to the running game, decode a tick, answer with an action.

WHAT THIS IS
------------

mod/src/telemetry.cpp writes a fixed-layout binary record into POSIX shared
memory once per *physics step* and then blocks the game thread waiting for a
reply. This is the other end of that. Together they are ``env.step()``: the game
does not advance until a policy has answered, which is exactly the semantics an
RL loop needs and is the reason stalling the renderer is a feature here rather
than a cost.

The interface that actually matters is not gym's. It is:

  * an observation labelled with the **physics tick** it was taken at, and
  * an action placed at a **physics tick**.

The render:physics ratio is not constant -- 316 to 440 render frames covered the
same 391 ticks -- so anything keyed to frames re-introduces the nondeterminism
the engine itself does not have. ``reset()`` / ``step()`` exist because they are
convenient, not because the tick-exactness is a detail underneath them.

WHY EVERY DECODE IS CHECKED
---------------------------

This repo's recurring failure is not a crash. It is a broken measurement that
produces confident, plausible numbers: ``maxX`` sampled per render frame agreed
with itself across hundreds of attempts and was measuring the frame rate;
``m_currentStep`` was called the tick counter because it sat next to the seed
state; four runs were contaminated by 195 stray button events and read as a
physics anomaly. So nothing here trusts anything:

  * the schema hash and wire version are checked before a single field is read,
    and a mismatch refuses rather than decodes;
  * the observation carries its own validity block and this module will not hand
    out a frame that fails it unless you explicitly ask for the raw record;
  * ``header.tick`` is cross-checked against ``round(attemptTime * 240)``,
    because the mod computing it does not make it right;
  * ``header.playerX/Y/Speed`` duplicate ``players[0]`` on the wire (the README
    spec declared them), and the duplication is used as a check that the mod read
    the player it thinks it did;
  * the mod's ``kind`` collapse is re-derived here from the raw ``objectType``
    and asserted, so the one interpretation that happens in C++ is audited;
  * ``stepIndex`` and ``tick`` must advance together, which is how the
    UNVERIFIED question of where GD updates ``m_attemptTime`` inside a physics
    step shows up in the data instead of being assumed.

KNOWN vs EMPTY
--------------

Objective E needs the observation to distinguish "there is nothing here" from
"I did not look here". A naive occupancy grid renders an off-screen pit and an
empty floor as the same bytes, and a policy cannot be conservative about
uncertainty it cannot see. The wire carries a per-section-column coverage mask
(SCANNED / TRUNCATED / ABSENT / UNKNOWN) plus four ``*_UNAVAILABLE`` header
flags for whole tables the mod did not populate. ``known_mask()`` turns those
into a boolean grid aligned with ``trajectory.TrajectoryRaster``, and the
intended consumption is trajectory.py's existing *certainty* channel rather than
a parallel mechanism -- the failure modes are the same ones it was built for.

NOT A SIMULATOR
---------------

``SyntheticGame`` at the bottom implements the game side of the protocol in pure
Python. It exists so the wire format and the handshake can be tested without GD
running, because a test that needs the single contended GD slot is a test that
does not get run. It fabricates field values; it does not simulate physics, and
nothing it produces is evidence about the game.
"""

from __future__ import annotations

import ctypes
import errno
import mmap
import os
import time
from dataclasses import dataclass

import numpy as np

from schema_generated import (
    GDRL_COVERAGE_COLS,
    GDRL_MAGIC,
    GDRL_MAX_ACTIONS,
    GDRL_MAX_COMMANDS,
    GDRL_MAX_OBJECTS,
    GDRL_MAX_PENDING,
    GDRL_MAX_SPEED_SEGS,
    GDRL_SCHEMA_HASH,
    GDRL_TICK_HZ,
    GDRL_WIRE_VERSION,
    OFFSET_ACTION,
    OFFSET_CONTROL,
    OFFSET_OBS,
    OFFSET_HEADERSIZE,
    OFFSET_MAGIC,
    OFFSET_SCHEMAHASH,
    OFFSET_WIREVERSION,
    STRUCT_SIZES,
    GdrlActionKind,
    GdrlControl_DTYPE,
    GdrlActionBlock_DTYPE,
    GdrlCoverage,
    GdrlHeaderFlag,
    GdrlInputVerdict,
    GdrlObjectKind,
    GdrlObservation_DTYPE,
    GdrlStatus,
)

SHARED_SIZE = STRUCT_SIZES["GdrlShared"]


class HandshakeError(RuntimeError):
    """The mapping is not one this build can decode.

    Deliberately fatal. A wire-version or schema-hash mismatch means the C++ and
    Python halves disagree about what the bytes mean, and decoding anyway would
    produce a full observation of plausible garbage -- exactly the class of
    failure the schema hash exists to make impossible.
    """


class ObservationRejected(RuntimeError):
    """The frame arrived intact but its own validity block says not to use it."""


# ---------------------------------------------------------------------------
# Attaching
# ---------------------------------------------------------------------------

_libc = ctypes.CDLL(None, use_errno=True)
# shm_open is variadic: `int shm_open(const char *, int, ...)`. On arm64 macOS
# the variadic slot is passed on the stack, and ctypes does not know a function
# is variadic -- declaring three argtypes would put the mode in a register the
# callee never reads. It is only read when O_CREAT is set, and Python never
# creates the segment (the mod does, and it unlinks/recreates on every launch so
# a stale one from a crashed GD can never be adopted). So two args, always.
_libc.shm_open.argtypes = [ctypes.c_char_p, ctypes.c_int]
_libc.shm_open.restype = ctypes.c_int


def open_shared(name: str = "gdrl.env") -> mmap.mmap:
    """Map the mod's segment read/write.

    Not ``multiprocessing.shared_memory``: that registers with the POSIX
    resource_tracker, which will unlink a segment it does not own at interpreter
    exit and emit a leak warning for one it does. Neither is acceptable for a
    segment whose lifetime belongs to the game process.
    """
    path = name if name.startswith("/") else "/" + name
    if len(path) > 31:
        # macOS PSHMNAMLEN. The mod refuses the same way; catching it on both
        # sides means the error names the cause instead of ENAMETOOLONG.
        raise HandshakeError(
            f"shm name {path!r} is {len(path)} bytes; macOS allows 31 including "
            "the leading slash"
        )
    fd = _libc.shm_open(path.encode(), os.O_RDWR)
    if fd < 0:
        eno = ctypes.get_errno()
        raise HandshakeError(
            f"shm_open({path!r}) failed: {os.strerror(eno)}. "
            + (
                "The segment does not exist -- is GD running with GDRL_ENV=1?"
                if eno == errno.ENOENT
                else ""
            )
        )
    try:
        buf = mmap.mmap(fd, SHARED_SIZE, mmap.MAP_SHARED,
                        mmap.PROT_READ | mmap.PROT_WRITE)
    finally:
        os.close(fd)   # the mapping keeps the object alive
    return buf


def wait_for_shared(name: str = "gdrl.env", timeout: float = 30.0) -> mmap.mmap:
    """Poll until the mod creates the segment. For launching GD alongside."""
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return open_shared(name)
        except HandshakeError as exc:
            last = exc
            time.sleep(0.05)
    raise HandshakeError(f"timed out after {timeout}s waiting for {name!r}") from last


# ---------------------------------------------------------------------------
# Decoded observation
# ---------------------------------------------------------------------------

# Must mirror collapseKind() in mod/src/telemetry.cpp. It is duplicated on
# purpose rather than shared: the point is to re-derive the collapse
# independently and assert the two agree, and a shared table would agree with
# itself no matter how wrong it was. Values are GameObjectType from
# Geode/Enums.hpp.
_HAZARD_TYPES = frozenset({2, 47})                 # Hazard, AnimatedHazard
_SOLID_TYPES = frozenset({0, 21, 25, 39})          # Solid, Breakable, Slope, CollisionObject
_OTHER_TYPES = frozenset({7, 20, 40})              # Decoration, Modifier, Special


def derive_kind(object_type: int, slope_is_hazard: bool) -> int:
    if slope_is_hazard:
        return int(GdrlObjectKind.HAZARD)
    if object_type in _HAZARD_TYPES:
        return int(GdrlObjectKind.HAZARD)
    if object_type in _SOLID_TYPES:
        return int(GdrlObjectKind.SOLID)
    if object_type in _OTHER_TYPES:
        return int(GdrlObjectKind.OTHER)
    # Pads, rings, portals, collectibles -- and the unknown tail, which is filed
    # as INTERACTIVE for the same reason the mod does: a new object type that
    # actually does something is more dangerous filed as scenery than scenery is
    # filed as interactive.
    return int(GdrlObjectKind.INTERACTIVE)


@dataclass(frozen=True)
class Observation:
    """One physics step, decoded and checked.

    ``record`` is a private numpy copy of the wire bytes, not a view into shared
    memory: the game thread may overwrite the slot as soon as it is answered, and
    a view would then quietly change under the policy mid-forward-pass.
    """

    record: np.ndarray          # 0-d structured scalar, GdrlObservation_DTYPE
    seq: int
    retries: int                # seqlock re-reads needed; >0 means we raced

    # -- the fields a caller usually wants -------------------------------
    @property
    def header(self):
        return self.record["header"]

    @property
    def validity(self):
        return self.record["validity"]

    @property
    def tick(self) -> int:
        return int(self.header["tick"])

    @property
    def attempt_time(self) -> float:
        return float(self.header["attemptTime"])

    @property
    def attempt(self) -> int:
        return int(self.header["attempt"])

    @property
    def step_index(self) -> int:
        return int(self.header["stepIndex"])

    @property
    def status(self) -> GdrlStatus:
        return GdrlStatus(int(self.validity["status"]))

    @property
    def input_verdict(self) -> GdrlInputVerdict:
        return GdrlInputVerdict(int(self.validity["inputVerdict"]))

    @property
    def flags(self) -> int:
        return int(self.header["flags"])

    def has_flag(self, flag: GdrlHeaderFlag) -> bool:
        return bool(self.flags & int(flag))

    # -- validity ---------------------------------------------------------
    def problems(self) -> list[str]:
        """Every reason this frame is not evidence, from the frame's own bytes.

        The list is the point. A single boolean would let a caller collapse
        "the level never loaded" and "one stray keypress got through" into the
        same shrug, and those need different responses.
        """
        out: list[str] = []
        v = self.validity
        h = self.header

        if int(h["magic"]) != GDRL_MAGIC:
            out.append(f"header magic 0x{int(h['magic']):08X} != 0x{GDRL_MAGIC:08X}")
        if int(h["version"]) != GDRL_WIRE_VERSION:
            out.append(f"header version {int(h['version'])} != {GDRL_WIRE_VERSION}")

        if self.status is not GdrlStatus.OK:
            out.append(f"status={self.status.name}")
        if int(v["leaked"]) > 0:
            # A push reached the player by an unmapped route. main.cpp marks the
            # whole attempt INVALID for this and so does the wire; a measurement
            # that does not assert leaked=0 is not evidence.
            out.append(f"input leaked={int(v['leaked'])}")
        if self.input_verdict is GdrlInputVerdict.UNGUARDED:
            out.append("input UNGUARDED (GDRL_BLOCK_INPUT off): a stray keypress "
                       "is indistinguishable from a policy action")
        if int(v["timeouts"]) > 0:
            # The mod blocked, nobody answered, and the step ran with no action.
            # That is indistinguishable from a policy that chose not to act.
            out.append(f"action waits expired: timeouts={int(v['timeouts'])}")
        if int(v["objectCountTotal"]) < 10:
            out.append(f"level has {int(v['objectCountTotal'])} objects -- the "
                       "level string almost certainly did not load")
        if not self.has_flag(GdrlHeaderFlag.GAMEPLAY_STEP):
            out.append("not a gameplay step (loadUpToPosition / seek path)")

        # tick must be the rounding of attemptTime. Never an equality test on
        # t == n/240: GD accumulates a float32 1/240 into a double, so the
        # residual is ~2.04e-05 ticks by construction. What is checked is that
        # the mod's lround agrees with ours.
        expect = round(self.attempt_time * GDRL_TICK_HZ)
        if expect != self.tick:
            out.append(f"tick {self.tick} != round(attemptTime*240) = {expect}")

        # header.playerX/Y/Speed duplicate players[0] because the README spec
        # declared them there. Rather than pick one, use the redundancy: a
        # disagreement means the mod filled the header from a different
        # PlayerObject than the one it serialised.
        p0 = self.record["players"][0]
        if int(p0["present"]):
            for hname, pname in (("playerX", "x"), ("playerY", "y"),
                                 ("playerSpeed", "playerSpeed")):
                a, b = float(h[hname]), float(p0[pname])
                if a != b and abs(a - b) > 1e-6 * max(1.0, abs(b)):
                    out.append(f"header.{hname}={a} disagrees with players[0].{pname}={b}")
        return out

    def require_ok(self) -> Observation:
        problems = self.problems()
        if problems:
            raise ObservationRejected(
                f"tick {self.tick}, attempt {self.attempt}: " + "; ".join(problems)
            )
        return self

    # -- objects ----------------------------------------------------------
    def objects(self) -> np.ndarray:
        """The populated slots only, as a structured array."""
        objs = self.record["objects"]
        return objs[objs["known"] != 0]

    def check_kind_collapse(self) -> list[str]:
        """Re-derive the mod's ``kind`` and report every disagreement.

        The mod performs exactly one interpretation -- collapsing 46
        GameObjectType values into four gameplay roles -- and only because the
        README telemetry spec put ``kind`` on the wire. This is what keeps that
        one exception audited rather than trusted.
        """
        bad: list[str] = []
        for o in self.objects():
            want = derive_kind(int(o["objectType"]), bool(o["slopeIsHazard"]))
            if int(o["kind"]) != want:
                bad.append(
                    f"uid={int(o['uniqueID'])} objectType={int(o['objectType'])} "
                    f"wire kind={int(o['kind'])} derived={want}"
                )
            want_hazard = 1 if want == int(GdrlObjectKind.HAZARD) else 0
            if int(o["isHazard"]) != want_hazard:
                bad.append(
                    f"uid={int(o['uniqueID'])} isHazard={int(o['isHazard'])} "
                    f"but derived kind is {want}"
                )
        return bad

    # -- Objective E: what was actually looked at -------------------------
    def coverage(self) -> np.ndarray:
        return self.record["coverage"]

    def column_span(self) -> tuple[float, float]:
        """World-x range that ``coverage[0]`` .. ``coverage[-1]`` describes."""
        sxf = float(self.header["sectionXFactor"]) or 100.0
        start = int(self.header["coverageStartCol"])
        return start * sxf, (start + GDRL_COVERAGE_COLS) * sxf

    def known_mask(self, height: int, width: int, cell_size: float,
                   player_col: int) -> np.ndarray:
        """(height, width) bool grid: True where the mod actually looked.

        Aligned with ``trajectory.TrajectoryRaster`` so the two can be stacked.
        The arguments mirror that class's constructor rather than being read off
        it, because importing the raster here would make the wire depend on a
        rendering choice.

        A cell is known when it lies inside the scanned window AND its section
        column was SCANNED or ABSENT. ABSENT counts as known: it means the index
        is past the end of GD's own ``m_sections``, so the engine has no geometry
        there -- genuinely empty, not merely unobserved. TRUNCATED does not
        count: the array filled mid-column, so what is missing is unknown.
        """
        h = self.header
        player_x = float(h["playerX"])
        player_y = float(h["playerY"])
        origin_x = player_x - player_col * cell_size
        origin_y = player_y - (height // 2) * cell_size

        cols_x = origin_x + (np.arange(width) + 0.5) * cell_size          # (W,)
        rows_y = origin_y + (np.arange(height) + 0.5) * cell_size         # (H,)

        in_x = (cols_x >= float(h["windowMinX"])) & (cols_x <= float(h["windowMaxX"]))
        in_y = (rows_y >= float(h["windowMinY"])) & (rows_y <= float(h["windowMaxY"]))

        sxf = float(h["sectionXFactor"]) or 100.0
        start_col = int(h["coverageStartCol"])
        cov = np.asarray(self.coverage())
        section = np.floor(cols_x / sxf).astype(np.int64) - start_col
        valid = (section >= 0) & (section < GDRL_COVERAGE_COLS)
        state = np.where(valid, cov[np.clip(section, 0, GDRL_COVERAGE_COLS - 1)],
                         int(GdrlCoverage.UNKNOWN))
        col_known = in_x & (
            (state == int(GdrlCoverage.SCANNED)) | (state == int(GdrlCoverage.ABSENT))
        )
        return col_known[None, :] & in_y[:, None]

    def unavailable_tables(self) -> list[str]:
        """Tables the mod did not populate, so a count of 0 is not an observation.

        ``count == 0`` with the flag CLEAR means the mod looked and found none.
        ``count == 0`` with the flag SET means it did not look, because the
        measurement that would make the answer trustworthy has not been taken.
        Treating those the same asserts an empty world nobody observed.
        """
        names = []
        for flag, name in (
            (GdrlHeaderFlag.OBJECTS_UNAVAILABLE, "objects"),
            (GdrlHeaderFlag.COMMANDS_UNAVAILABLE, "commands"),
            (GdrlHeaderFlag.PENDING_UNAVAILABLE, "pending"),
            (GdrlHeaderFlag.SPEEDSEGS_UNAVAILABLE, "speedSegs"),
        ):
            if self.has_flag(flag):
                names.append(name)
        return names


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Action:
    """One input, placed at a physics tick.

    ``HOLD`` carries its own duration rather than being a PRESS the policy has
    to remember to release. That is Objective C's requirement: consecutive jump
    pads and ship altitude need a hold as a single decision, and a
    press/release-per-frame action space cannot express one across the many
    ticks between two observations.
    """

    target_tick: int
    kind: GdrlActionKind = GdrlActionKind.NOOP
    hold_ticks: int = 0
    button: int = 1          # PlayerButton::Jump
    player: int = 0          # 0 = p1, 1 = p2

    def __post_init__(self) -> None:
        if self.kind is GdrlActionKind.HOLD and self.hold_ticks < 1:
            raise ValueError(
                "HOLD needs hold_ticks >= 1; a zero-length hold is a press with "
                "no release, which latches the button down"
            )
        if self.player not in (0, 1):
            raise ValueError(f"player must be 0 or 1, got {self.player}")


def noop(target_tick: int) -> Action:
    return Action(target_tick=target_tick, kind=GdrlActionKind.NOOP)


# ---------------------------------------------------------------------------
# The channel
# ---------------------------------------------------------------------------

class Channel:
    """The Python end of the protocol, over any writable buffer.

    Taking a buffer rather than opening the shared memory itself is what makes
    the loopback test possible: the same code path runs against an anonymous
    mapping driven by ``SyntheticGame``. A wire format tested only by a run that
    needs the contended GD slot is a wire format that is not tested.
    """

    def __init__(self, buf, validate: bool = True):
        self._buf = buf
        self._control = np.ndarray((), dtype=GdrlControl_DTYPE,
                                   buffer=buf, offset=OFFSET_CONTROL)
        if validate:
            self.validate()
        self._obs_view = np.ndarray((), dtype=GdrlObservation_DTYPE,
                                    buffer=buf, offset=int(self._control["obsOffset"]))
        self._act_view = np.ndarray((), dtype=GdrlActionBlock_DTYPE,
                                    buffer=buf, offset=int(self._control["actOffset"]))
        self._last_seq = 0
        self._heartbeat = 0

    # -- handshake --------------------------------------------------------
    def validate(self) -> None:
        """Refuse anything this build cannot decode. Never decode anyway.

        The four fields checked first (magic, wireVersion, headerSize,
        schemaHash) are at frozen offsets 0/4/6/8 across every wire version
        precisely so this function keeps working against a mapping written by a
        version it has never heard of -- and reports the real mismatch instead
        of a confusing one.
        """
        raw = memoryview(self._buf)
        magic = int(np.frombuffer(raw, np.uint32, 1, OFFSET_MAGIC)[0])
        if magic != GDRL_MAGIC:
            raise HandshakeError(
                f"magic 0x{magic:08X} != 0x{GDRL_MAGIC:08X} at offset {OFFSET_MAGIC}. "
                "Either the segment is not a GDRL mapping, or the mod has not "
                "finished publishing it (the mod stores magic last, on purpose)."
            )
        version = int(np.frombuffer(raw, np.uint16, 1, OFFSET_WIREVERSION)[0])
        if version != GDRL_WIRE_VERSION:
            raise HandshakeError(
                f"wire version {version} != {GDRL_WIRE_VERSION}: rebuild the mod "
                "and re-run `python3 trainer/schema.py`"
            )
        hashed = int(np.frombuffer(raw, np.uint64, 1, OFFSET_SCHEMAHASH)[0])
        if hashed != GDRL_SCHEMA_HASH:
            raise HandshakeError(
                f"schema hash 0x{hashed:016X} != 0x{GDRL_SCHEMA_HASH:016X}. The mod "
                "and this Python were generated from different versions of "
                "trainer/schema.py. Decoding anyway would produce a full "
                "observation of plausible garbage."
            )
        header_size = int(np.frombuffer(raw, np.uint16, 1, OFFSET_HEADERSIZE)[0])
        if header_size != STRUCT_SIZES["GdrlControl"]:
            raise HandshakeError(
                f"control block is {header_size} bytes, expected "
                f"{STRUCT_SIZES['GdrlControl']}"
            )

        c = self._control
        for name, want in (("obsOffset", OFFSET_OBS), ("actOffset", OFFSET_ACTION),
                           ("obsSize", STRUCT_SIZES["GdrlObservation"]),
                           ("actSize", STRUCT_SIZES["GdrlActionBlock"]),
                           ("totalSize", SHARED_SIZE),
                           ("maxObjects", GDRL_MAX_OBJECTS),
                           ("maxActions", GDRL_MAX_ACTIONS),
                           ("coverageCols", GDRL_COVERAGE_COLS),
                           ("tickHz", GDRL_TICK_HZ)):
            got = int(c[name])
            if got != want:
                # A matching hash with a mismatched geometry would mean the
                # generator and the compiler disagree, which the generated
                # static_asserts are supposed to make impossible. Say so loudly.
                raise HandshakeError(
                    f"control.{name} = {got}, expected {want}, despite a matching "
                    "schema hash. The C++ struct layout does not match the "
                    "generator's model."
                )

    # -- liveness ---------------------------------------------------------
    @property
    def mod_alive(self) -> bool:
        return bool(int(self._control["modAlive"]))

    @property
    def mod_pid(self) -> int:
        return int(self._control["modPid"])

    def mod_process_alive(self) -> bool:
        """Is the writing process still there?

        ``modAlive`` is set by the mod and cleared on a clean shutdown, so it
        stays 1 after a crash. This is the only cheap way to tell a live server
        from a dead one holding a valid-looking mapping.
        """
        pid = self.mod_pid
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @property
    def timeouts(self) -> int:
        return int(self._control["stepTimeouts"])

    @property
    def protocol_errors(self) -> int:
        return int(self._control["protocolErrors"])

    @property
    def steps_served(self) -> int:
        return int(self._control["stepsServed"])

    # -- attach / detach --------------------------------------------------
    def attach(self) -> None:
        if not self.mod_alive:
            raise HandshakeError("modAlive == 0: the mod is not serving this segment")
        self._last_seq = int(self._control["obsSeq"])
        self._control["pyAttached"] = 1

    def detach(self) -> None:
        """Release the game thread. Idempotent and safe from a finally block.

        The mod only ever blocks while ``pyAttached`` is 1, so clearing it is
        what guarantees a trainer that dies does not wedge GD -- the bounded
        wait is the backstop, not the mechanism.
        """
        try:
            self._control["pyAttached"] = 0
        except (ValueError, BufferError):
            pass   # buffer already closed

    def __enter__(self) -> Channel:
        self.attach()
        return self

    def __exit__(self, *exc) -> None:
        self.detach()

    # -- the step ---------------------------------------------------------
    def poll(self, timeout: float = 5.0) -> Observation:
        """Wait for the next observation and return it, seqlock-safe.

        The seqlock exists because the ping-pong guarantee -- the writer will not
        start observation n+1 before n is answered -- evaporates the moment a
        bounded wait times out and the game runs on. A torn read there would
        surface as an object in an impossible place, which is exactly the shape
        of thing this project would otherwise spend a day blaming on physics.
        """
        deadline = time.monotonic() + timeout
        seq_field = self._obs_view["seq"]
        while True:
            seq = int(seq_field)
            if seq > self._last_seq and seq % 2 == 0:
                retries = 0
                while True:
                    snapshot = np.array(self._obs_view)     # deep copy off the wire
                    after = int(self._obs_view["seq"])
                    if after == seq:
                        self._last_seq = seq
                        return Observation(record=snapshot, seq=seq, retries=retries)
                    # The writer moved on mid-copy. Restart from the new value
                    # rather than looping on the stale one.
                    seq = after if after % 2 == 0 else after + 1
                    retries += 1
                    if retries > 16:
                        raise RuntimeError(
                            "seqlock never settled after 16 retries -- the writer "
                            "is publishing faster than this reader can copy, which "
                            "means the ping-pong protocol is not being honoured"
                        )
            if time.monotonic() > deadline:
                alive = "alive" if self.mod_process_alive() else "DEAD"
                raise TimeoutError(
                    f"no observation within {timeout}s (last seq {self._last_seq}, "
                    f"current {int(seq_field)}, mod pid {self.mod_pid} {alive}, "
                    f"modAlive={int(self._control['modAlive'])})"
                )
            time.sleep(0.0002)

    def respond(self, actions: list[Action] | None = None, *,
                advance_steps: int = 1, detach: bool = False) -> None:
        """Answer the observation returned by the last ``poll``.

        ``advance_steps`` is the observation stride: 1 gives an observation every
        physics tick, larger values trade decision rate for throughput. It costs
        nothing in action precision -- the mod queues scheduled inputs on every
        step regardless of whether it published one -- which is what makes it a
        strictly better knob than experiments.cpp's adaptive-dt scheme.
        """
        actions = actions or []
        if len(actions) > GDRL_MAX_ACTIONS:
            raise ValueError(
                f"{len(actions)} actions exceeds the wire capacity of "
                f"{GDRL_MAX_ACTIONS}; the mod would drop the overflow"
            )
        if advance_steps < 1:
            raise ValueError("advance_steps must be >= 1")

        a = self._act_view
        for i, act in enumerate(actions):
            slot = a["actions"][i]
            slot["targetTick"] = act.target_tick
            slot["holdTicks"] = act.hold_ticks
            slot["kind"] = int(act.kind)
            slot["button"] = act.button
            slot["player"] = act.player
        a["count"] = len(actions)
        a["advanceSteps"] = advance_steps
        a["detach"] = 1 if detach else 0
        self._heartbeat += 1
        a["nonce"] = self._heartbeat
        a["seq"] = self._last_seq
        self._control["pyHeartbeat"] = self._heartbeat
        # seq LAST. The mod spins on control.actSeq and reads the body only
        # after it matches, so publishing the sequence before the body would let
        # it execute a half-written plan.
        self._control["actSeq"] = self._last_seq

    def step(self, actions: list[Action] | None = None, *,
             advance_steps: int = 1, timeout: float = 5.0) -> Observation:
        """Answer the outstanding observation and wait for the next one."""
        self.respond(actions, advance_steps=advance_steps)
        return self.poll(timeout=timeout)


class GdrlEnv:
    """Convenience wrapper that owns the mapping.

    Deliberately thin. The tick-exact placement in ``Channel`` is the real
    interface; this only saves the caller from managing an mmap. There is no
    gym ``Space``, no reward and no ``done`` here, because none of those are
    decided yet and inventing them would freeze choices the objectives have not
    made.
    """

    def __init__(self, name: str = "gdrl.env", timeout: float = 30.0):
        self._buf = wait_for_shared(name, timeout=timeout)
        self.channel = Channel(self._buf)

    def __enter__(self) -> GdrlEnv:
        self.channel.attach()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def reset(self, timeout: float = 5.0) -> Observation:
        """The next observation, whatever attempt it belongs to.

        Note what this does NOT do: it does not force an attempt boundary. The
        game owns episode boundaries (death, resetLevel, the fast-reset path),
        and a reset() that pretended otherwise would be lying about which
        trajectory the returned tick belongs to. Read ``obs.attempt`` to see.
        """
        return self.channel.poll(timeout=timeout)

    def step(self, actions: list[Action] | None = None, *,
             advance_steps: int = 1, timeout: float = 5.0) -> Observation:
        return self.channel.step(actions, advance_steps=advance_steps, timeout=timeout)

    def close(self) -> None:
        self.channel.detach()
        self._buf.close()


# ---------------------------------------------------------------------------
# Loopback reference implementation of the game side
# ---------------------------------------------------------------------------

def make_loopback_buffer() -> mmap.mmap:
    """An anonymous mapping laid out exactly like the mod's segment."""
    return mmap.mmap(-1, SHARED_SIZE)


class SyntheticGame:
    """The mod's half of the protocol, in Python, for tests.

    This is NOT a simulator. It writes plausible-looking bytes so the encode /
    decode / handshake / seqlock paths can be exercised without GD, and nothing
    it produces is evidence about the game. It is also the only place the
    protocol is written down twice, which is the point: if the two descriptions
    disagree, the loopback test says so.
    """

    def __init__(self, buf, pid: int | None = None):
        self._buf = buf
        self.control = np.ndarray((), dtype=GdrlControl_DTYPE,
                                  buffer=buf, offset=OFFSET_CONTROL)
        self.obs = np.ndarray((), dtype=GdrlObservation_DTYPE,
                              buffer=buf, offset=OFFSET_OBS)
        self.action = np.ndarray((), dtype=GdrlActionBlock_DTYPE,
                                 buffer=buf, offset=OFFSET_ACTION)
        self.seq = 0
        self.scheduled: list[tuple[int, bool, int, int]] = []   # (tick, push, button, player)
        self._init_control(pid if pid is not None else os.getpid())

    def _init_control(self, pid: int) -> None:
        c = self.control
        c["wireVersion"] = GDRL_WIRE_VERSION
        c["headerSize"] = STRUCT_SIZES["GdrlControl"]
        c["schemaHash"] = GDRL_SCHEMA_HASH
        c["obsOffset"] = OFFSET_OBS
        c["obsSize"] = STRUCT_SIZES["GdrlObservation"]
        c["actOffset"] = OFFSET_ACTION
        c["actSize"] = STRUCT_SIZES["GdrlActionBlock"]
        c["totalSize"] = SHARED_SIZE
        c["maxObjects"] = GDRL_MAX_OBJECTS
        c["maxCommands"] = GDRL_MAX_COMMANDS
        c["maxPending"] = GDRL_MAX_PENDING
        c["maxSpeedSegs"] = GDRL_MAX_SPEED_SEGS
        c["maxActions"] = GDRL_MAX_ACTIONS
        c["coverageCols"] = GDRL_COVERAGE_COLS
        c["tickHz"] = GDRL_TICK_HZ
        c["modPid"] = pid
        c["waitBudgetUs"] = 250000
        c["modAlive"] = 1
        c["magic"] = GDRL_MAGIC          # last, mirroring the mod

    def publish(self, tick: int, *, attempt: int = 0, step_index: int | None = None,
                player_x: float = 0.0, player_y: float = 105.0,
                objects: list[dict] | None = None,
                coverage_cols: int = 8,
                status: GdrlStatus = GdrlStatus.OK,
                extra_flags: int = 0,
                object_count_total: int = 2400) -> int:
        """Write one observation and advance the sequence, seqlock and all."""
        self.seq += 1                       # odd: write in progress
        self.obs["seq"] = self.seq

        h = self.obs["header"]
        h["magic"] = GDRL_MAGIC
        h["version"] = GDRL_WIRE_VERSION
        # attemptTime is built the way GD builds it -- repeated addition of the
        # float32 1/240 into a double -- so the decoder's lround cross-check is
        # exercised against the real residual rather than an exact multiple.
        h["attemptTime"] = _accumulated_attempt_time(tick)
        h["tick"] = tick
        h["dtPerStep"] = 1.0 / GDRL_TICK_HZ
        h["timeWarp"] = 1.0
        h["playerX"] = player_x
        h["playerY"] = player_y
        h["playerSpeed"] = 0.9
        h["attempt"] = attempt
        h["frame"] = tick
        h["stepIndex"] = tick if step_index is None else step_index
        h["dtIn"] = 1.0 / GDRL_TICK_HZ
        h["dtUsed"] = 1.0 / GDRL_TICK_HZ
        h["sectionXFactor"] = 100.0
        h["sectionYFactor"] = 100.0
        h["levelLength"] = 26724.0
        h["isDualMode"] = 0
        h["isPaused"] = 0
        h["inResetDelay"] = 0
        h["commandCount"] = 0
        h["pendingCount"] = 0
        h["speedSegCount"] = 0
        h["flags"] = (int(GdrlHeaderFlag.GAMEPLAY_STEP)
                      | int(GdrlHeaderFlag.INPUT_CLEAN)
                      | int(GdrlHeaderFlag.COMMANDS_UNAVAILABLE)
                      | int(GdrlHeaderFlag.PENDING_UNAVAILABLE)
                      | int(GdrlHeaderFlag.SPEEDSEGS_UNAVAILABLE)
                      | extra_flags)

        col0 = max(0, int((player_x - 400.0) // 100.0))
        h["coverageStartCol"] = col0
        h["sectionColumns"] = 268
        h["windowMinX"] = col0 * 100.0
        h["windowMaxX"] = (col0 + coverage_cols) * 100.0
        h["windowMinY"] = player_y - 600.0
        h["windowMaxY"] = player_y + 600.0

        cov = self.obs["coverage"]
        cov[:] = int(GdrlCoverage.UNKNOWN)
        cov[:coverage_cols] = int(GdrlCoverage.SCANNED)

        p0 = self.obs["players"][0]
        p0["present"] = 1
        p0["x"] = player_x
        p0["y"] = player_y
        p0["yVelocity"] = 0.0
        p0["gravity"] = 0.96
        p0["rotation"] = 0.0
        p0["vehicleSize"] = 1.0
        p0["playerSpeed"] = 0.9
        p0["vehicleFlags"] = 0
        for f in ("isUpsideDown", "isSideways", "isOnGround", "isDashing"):
            p0[f] = 0
        p0["isOnGround"] = 1
        self.obs["players"][1]["present"] = 0

        v = self.obs["validity"]
        v["blocked"] = 0
        v["leaked"] = 0
        v["uiEvents"] = 0
        v["timeouts"] = 0
        v["inputVerdict"] = int(GdrlInputVerdict.CLEAN)
        v["status"] = int(status)
        v["levelID"] = 1
        v["pinnedLevelID"] = 1
        v["objectCountTotal"] = object_count_total
        v["levelPinned"] = 1
        v["blockInput"] = 1
        v["objectsTruncated"] = 0

        slots = self.obs["objects"]
        slots["known"] = 0
        for i, o in enumerate(objects or []):
            if i >= GDRL_MAX_OBJECTS:
                break
            s = slots[i]
            s["known"] = 1
            s["uniqueID"] = o.get("uniqueID", i + 1)
            s["objectID"] = o.get("objectID", 1)
            s["objectType"] = o.get("objectType", 0)
            s["slopeIsHazard"] = o.get("slopeIsHazard", 0)
            s["isGroupDisabled"] = 0
            kind = derive_kind(int(s["objectType"]), bool(s["slopeIsHazard"]))
            s["kind"] = o.get("kind", kind)
            s["isHazard"] = o.get(
                "isHazard", 1 if int(s["kind"]) == int(GdrlObjectKind.HAZARD) else 0)
            s["x"] = o.get("x", 0.0)
            s["y"] = o.get("y", 0.0)
            s["halfW"] = o.get("halfW", 15.0)
            s["halfH"] = o.get("halfH", 15.0)
            s["rotation"] = 0.0
            s["scaleX"] = 1.0
            s["scaleY"] = 1.0
            s["groupCount"] = 0
        h["objectCount"] = min(len(objects or []), GDRL_MAX_OBJECTS)
        h["objectsDropped"] = max(0, len(objects or []) - GDRL_MAX_OBJECTS)

        self.seq += 1                       # even: complete
        self.obs["seq"] = self.seq
        self.control["obsSeq"] = self.seq
        return self.seq

    def consume(self) -> int:
        """Apply the outstanding action block the way the mod does.

        Returns the requested stride. Raises on a sequence mismatch, because the
        mod refuses one rather than executing a stale plan and this side must
        agree about that or the loopback proves nothing.
        """
        a = self.action
        if int(a["seq"]) != self.seq:
            self.control["protocolErrors"] = int(self.control["protocolErrors"]) + 1
            raise ObservationRejected(
                f"action seq {int(a['seq'])} != observation seq {self.seq}"
            )
        n = min(int(a["count"]), GDRL_MAX_ACTIONS)
        for i in range(n):
            act = a["actions"][i]
            kind = GdrlActionKind(int(act["kind"]))
            tick = int(act["targetTick"])
            btn = int(act["button"])
            player = int(act["player"])
            if kind is GdrlActionKind.NOOP:
                continue
            if kind is GdrlActionKind.PRESS:
                self.scheduled.append((tick, True, btn, player))
            elif kind is GdrlActionKind.RELEASE:
                self.scheduled.append((tick, False, btn, player))
            elif kind is GdrlActionKind.HOLD:
                hold = max(1, int(act["holdTicks"]))
                self.scheduled.append((tick, True, btn, player))
                self.scheduled.append((tick + hold, False, btn, player))
        self.control["actSeq"] = self.seq
        self.control["stepsServed"] = int(self.control["stepsServed"]) + 1
        return max(1, int(a["advanceSteps"]))


def _accumulated_attempt_time(tick: int) -> float:
    """m_attemptTime as GD builds it: a float32 1/240 added into a double.

    Not ``tick / 240``. The distinction is the whole reason the decoder rounds
    instead of testing equality -- the residual reaches 2.039e-05 ticks at 391
    ticks, and a synthetic writer that produced exact multiples would let a
    decoder that used ``==`` pass the loopback test and then fail on the game.
    """
    step = float(np.float32(1.0 / GDRL_TICK_HZ))
    t = 0.0
    for _ in range(tick):
        t += step
    return t
