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

It returns a ``KnownMask``, not the array, because there are frames for which no
grid exists at all: the mod did not scan, or there is no x -> column mapping to
scan with. Those are refusals, and an all-False grid is a different statement
("looked, knows nothing"). Handing back the same bytes for both is how "refused"
becomes "known-empty" without anyone deciding to make it so -- the same shape of
defect as the inverted section factor. ``KnownMask.grid()`` raises rather than
substituting; there is no default anywhere in this path.

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
import enum
import errno
import math
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


# ---------------------------------------------------------------------------
# Known vs empty: a mask you cannot read without reading the refusal
# ---------------------------------------------------------------------------

class MaskRefusal(enum.Enum):
    """Why no boolean grid can be produced for a frame.

    Deliberately NOT an IntEnum. A refusal must not be usable as an array index,
    must not compare equal to 0, and must not be falsy: every one of those is a
    way for "we did not look" to be silently spent as if it were data.
    """

    OBJECTS_UNAVAILABLE = "objects table not populated on this frame"
    NO_SECTION_MAPPING = "sectionXFactor unusable: no world x -> column mapping"


class MaskRefused(RuntimeError):
    """Raised when the grid of a refused :class:`KnownMask` is asked for.

    Deliberately fatal, and deliberately raised at the point of *use* rather
    than at the point of decode: a refusal is a normal, expected frame state
    (the mod raises ``OBJECTS_UNAVAILABLE`` on every no-player step), so
    constructing the observation must not throw -- but converting one into
    geometry must.
    """


@dataclass(frozen=True, eq=False)
class KnownMask:
    """A (height, width) boolean grid, or a refusal -- never a stand-in for one.

    WHY THIS IS NOT AN ndarray
    --------------------------

    ``known_mask()`` used to return a bare array, and it returned a bit-identical
    all-False array for three materially different situations:

      1. the section factor is unusable, so there is no mapping from world x to a
         coverage column at all;
      2. the mod did not populate the objects table this frame, so ``coverage``
         and the window bounds are last frame's;
      3. the mod looked, the mapping is fine, and every column in the window was
         TRUNCATED or ABSENT -- genuine knowledge that nothing is known.

    (1) and (2) are refusals. (3) is an observation. A consumer holding only the
    array could not tell them apart, and nothing in the signature forced it to
    ask. That is the same defect class as the inverted section factor: not a
    crash, a confident answer nobody measured.

    So the grid is not reachable without passing the refusal. ``grid()`` raises
    :class:`MaskRefused` when refused, ``__array__`` raises the same thing (so
    ``np.asarray(km)`` cannot launder it), ``__bool__`` always raises, and there
    is no ``.any()`` / ``.shape`` / ``[]`` to fall through to -- a consumer that
    kept writing ``obs.known_mask(...).any()`` gets an ``AttributeError`` on the
    first frame rather than a plausible empty grid forever.

    There is deliberately no ``mask_or_empty()`` and no default. Collapsing a
    refusal into all-False is exactly the bug; if a caller decides to do it, that
    decision belongs in the caller's source where it can be read.
    """

    shape: tuple[int, int]
    refusal: MaskRefusal | None
    _grid: np.ndarray | None = None

    @property
    def refused(self) -> bool:
        return self.refusal is not None

    def grid(self) -> np.ndarray:
        """The boolean grid. Raises :class:`MaskRefused` if there is not one.

        True where the mod actually looked. Note that all-False is a legitimate
        return: it is case (3), "looked, and knows nothing" -- which is a
        different statement from either refusal and is why they cannot share a
        representation.
        """
        if self.refusal is not None:
            raise MaskRefused(
                f"no known-mask for this frame: {self.refusal.value} "
                f"({self.refusal.name}). This is a refusal, not an empty grid -- "
                "treating it as 'nothing is here' asserts geometry nobody "
                "observed. Handle it, or drop the frame."
            )
        assert self._grid is not None
        return self._grid

    # np.asarray() is the other route to a grid, so it goes through the same
    # gate. Not defining it at all would let np.asarray() build a 0-d object
    # array that fails somewhere far from the cause.
    def __array__(self, dtype=None, copy=None):
        g = self.grid()
        if dtype is not None:
            g = g.astype(dtype, copy=bool(copy))
        return g

    def __bool__(self) -> bool:
        raise TypeError(
            "KnownMask has no truth value: 'refused' and 'nothing is known' are "
            "different frames. Ask .refused, or call .grid()."
        )

    def __repr__(self) -> str:
        if self.refusal is not None:
            return f"KnownMask(shape={self.shape}, REFUSED: {self.refusal.name})"
        return f"KnownMask(shape={self.shape}, known={int(self._grid.sum())} cells)"


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

    # -- GDRL_PRACTICE (wire version 2) ------------------------------------
    #
    # Four thin accessors over header fields that already existed on the wire
    # (see gdrl_schema.hpp's GdrlObsHeader) but had no Python-side name yet.
    # Deliberately not collapsed into one "in practice mode" bool: resumeTick
    # is LATCHED per attempt, checkpointTick/hasCheckpoint are LIVE (can change
    # mid-attempt via an on-demand CHECKPOINT_SAVE/CLEAR), and practiceMode is a
    # whole-RUN switch that can be 1 on an attempt whose own resumeTick is still
    # 0 (practice mode on, but this is the first attempt, before any checkpoint
    # existed). Collapsing those into one flag would erase exactly the
    # distinction the wire format was built to keep visible.
    @property
    def resume_tick(self) -> int:
        """The tick THIS attempt began at. 0 for a full (tick-0) attempt."""
        return int(self.header["resumeTick"])

    @property
    def checkpoint_tick(self) -> int:
        """Tick of the checkpoint currently held, or -1 if none. Live, not latched."""
        return int(self.header["checkpointTick"])

    @property
    def has_checkpoint(self) -> bool:
        """A checkpoint exists right now and CHECKPOINT_RESTORE would do something."""
        return bool(self.header["hasCheckpoint"])

    @property
    def practice_mode(self) -> bool:
        """GDRL_PRACTICE=1 for this whole run, not just this attempt."""
        return bool(self.header["practiceMode"])

    @property
    def is_practice_attempt(self) -> bool:
        """Did THIS attempt begin from a checkpoint restore, not tick 0?

        Derived from resumeTick, deliberately NOT from practice_mode -- see the
        block comment above. This is the per-attempt question; practice_mode is
        the per-run one. A caller building an A-attempts count must gate on
        THIS, not on practice_mode, or a practice run's very first (tick-0)
        attempt gets misfiled as not-A-eligible when it is, in fact, a real
        tick-0 attempt.
        """
        return self.resume_tick != 0

    @property
    def full_attempts(self) -> int:
        """GdrlValidity.fullAttempts: lifetime count of tick-0 attempts. Benchmark A's number."""
        return int(self.validity["fullAttempts"])

    @property
    def practice_attempts(self) -> int:
        """GdrlValidity.practiceAttempts: lifetime count of checkpoint-resumed attempts.

        Kept on a SEPARATE wire field from full_attempts on purpose (see
        gdrl_schema.hpp) so a decoder cannot average an A number and a
        practice-assisted one into one figure just by reading "attempts".
        There is deliberately no `.total_attempts` here: a caller that wants a
        sum must write `full_attempts + practice_attempts` at its own call
        site, in its own source, where the fact that it is mixing benchmark
        variants is visible to whoever reads that line.
        """
        return int(self.validity["practiceAttempts"])

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

    def section_factor(self) -> float | None:
        """``m_sectionXFactor`` if it can be used as an index multiplier.

        ``None`` if it cannot. See :meth:`section_width` for the measurement and
        for why there is no fallback constant.
        """
        sxf = float(self.header["sectionXFactor"])
        if not (sxf > 0.0) or not np.isfinite(sxf):
            return None
        return sxf

    def section_width(self) -> float | None:
        """Units of world x per section column, or ``None`` if unusable.

        ``m_sectionXFactor`` is a MULTIPLIER (1 / section width), not a divisor:
        the column index is ``floor(x * sxf)``. Measured live 2026-08-12 as
        ``0.01`` (on the wire as the float ``0.009999999776482582``), which makes
        ``floor(x * sxf) == floor(x / 100)`` and agrees with README's documented
        ``sectionIndex = floor(x / 100)``. Independently cross-checked against
        ``m_sections.size() == floor(levelLength * sxf) + 1``, exact on two
        levels (Stereo Madness 26724 -> 268 columns, synth 6340 -> 64). The
        divisor reading would need 2,672,400 columns for the same level.

        There is deliberately NO fallback constant. The old ``or 100.0`` was the
        right order of magnitude under the divisor reading and is wrong by 1e4
        under the multiplier one, and a wrong section width does not produce a
        wrong answer -- it produces a confident one. ``None`` here means the same
        thing the mod's own guard means: we cannot speak for any column.

        This is the inverse direction only (column -> x, for describing a span).
        Indexing x -> column goes through :meth:`section_factor` and a multiply,
        the same expression the mod uses, so the two cannot round apart.
        """
        sxf = self.section_factor()
        if sxf is None:
            return None
        return 1.0 / sxf

    def level_length_agrees_with_section_count(self) -> bool | None:
        """Diagnostic: does ``floor(levelLength * sxf) + 1 == sectionColumns``?

        ``None`` when the question cannot be asked (``sectionXFactor`` unusable,
        or ``levelLength`` / ``sectionColumns`` unpopulated), ``True`` / ``False``
        otherwise.

        A DIAGNOSTIC, NOT A GATE. It is deliberately absent from
        ``problems()``, and neither ``known_mask()`` nor ``section_factor()``
        consults it. That is a considered trade, not an oversight:

        * The identity itself is UNVERIFIED as a general property of GD. It has
          been measured on exactly two levels -- Stereo Madness (m_levelLength
          26724, m_sections.size() 268) and the synth test level (6340, 64), both
          from the 2026-08-12 section-grid dump, recorded at telemetry.cpp:
          685-688. Two points do not establish that ``m_sections`` is sized from
          ``m_levelLength`` at all; it may be sized from the rightmost object, a
          rounded-up level bound, or something else that merely happened to
          agree twice.
        * It is a weak constraint even where it holds. The band of sxf that
          satisfies it is [267/26724, 268/26724) = [0.0099910, 0.0100284) on
          Stereo Madness -- 0.37% wide -- and [63/6340, 64/6340) =
          [0.0099369, 0.0100946) on the synth level, 1.58% wide. It is weakest
          exactly where levels are shortest, i.e. where a mis-scaled factor
          costs the least to detect elsewhere.

        Letting a twice-measured identity of that width blank the entire
        uncertainty channel for a whole level is a worse failure than reporting
        the disagreement and continuing. What it IS good for is the failure the
        positivity guard cannot see: ``sectionXFactor = 1e-30`` is positive and
        finite, passes ``section_factor()``, and yields a confident mask in which
        every x indexes to column 0. This returns False for it.
        """
        sxf = self.section_factor()
        if sxf is None:
            return None
        length = float(self.header["levelLength"])
        cols = int(self.header["sectionColumns"])
        if not np.isfinite(length) or length <= 0.0 or cols <= 0:
            return None
        return math.floor(length * sxf) + 1 == cols

    def column_span(self) -> tuple[float, float] | None:
        """World-x range that ``coverage[0]`` .. ``coverage[-1]`` describes.

        ``None`` when ``sectionXFactor`` is unusable: the mask then describes no
        world x at all, and a numeric span would be a claim about geometry
        nobody indexed. Callers must treat ``None`` as "did not look", the same
        way ``unavailable_tables()`` is treated.
        """
        sec_w = self.section_width()
        if sec_w is None:
            return None
        start = int(self.header["coverageStartCol"])
        return start * sec_w, (start + GDRL_COVERAGE_COLS) * sec_w

    def known_mask(self, height: int, width: int, cell_size: float,
                   player_col: int) -> KnownMask:
        """Where the mod actually looked -- as a :class:`KnownMask`, not an array.

        The grid is (height, width) bool, True where the mod looked, aligned with
        ``trajectory.TrajectoryRaster`` so the two can be stacked. The arguments
        mirror that class's constructor rather than being read off it, because
        importing the raster here would make the wire depend on a rendering
        choice. Call ``.grid()`` to get it; that call is where a refusal becomes
        an exception, and see :class:`KnownMask` for why it is not just an array.

        A cell is known when it lies inside the scanned window AND its section
        column was SCANNED or ABSENT. ABSENT counts as known: it means the index
        is past the end of GD's own ``m_sections``, so the engine has no geometry
        there -- genuinely empty, not merely unobserved. TRUNCATED does not
        count: the array filled mid-column, so what is missing is unknown. An
        all-False grid is therefore a real answer ("looked, knows nothing") and
        must not share a representation with the two refusals below.

        Two whole-frame refusals come first, and neither yields a grid:

        * ``OBJECTS_UNAVAILABLE`` set -> ``MaskRefusal.OBJECTS_UNAVAILABLE``. The
          mod raises it on the no-player path (``telemetry.cpp:1173``) *without*
          calling ``scanObjects``, so on that frame ``coverage`` and the window
          bounds are whatever the previous frame left there. Reading them would
          be reporting last frame's geometry as this frame's observation.
        * ``sectionXFactor`` unusable -> ``MaskRefusal.NO_SECTION_MAPPING``.
          Without the section width there is no mapping from world x to a
          coverage column, so no column can be spoken for. See
          ``section_width()`` for why there is no fallback constant.

        The order matters for the *label*, not for the outcome:
        ``OBJECTS_UNAVAILABLE`` is checked first because when the mod did not
        scan, ``sectionXFactor`` is also whatever the previous frame left in the
        header -- reporting a factor refusal there would be a claim read off
        stale bytes. The mod's own bad-factor path sets both.
        """
        h = self.header
        if self.has_flag(GdrlHeaderFlag.OBJECTS_UNAVAILABLE):
            return KnownMask((height, width), MaskRefusal.OBJECTS_UNAVAILABLE)
        sxf = self.section_factor()
        if sxf is None:
            return KnownMask((height, width), MaskRefusal.NO_SECTION_MAPPING)

        player_x = float(h["playerX"])
        player_y = float(h["playerY"])
        origin_x = player_x - player_col * cell_size
        origin_y = player_y - (height // 2) * cell_size

        cols_x = origin_x + (np.arange(width) + 0.5) * cell_size          # (W,)
        rows_y = origin_y + (np.arange(height) + 0.5) * cell_size         # (H,)

        in_x = (cols_x >= float(h["windowMinX"])) & (cols_x <= float(h["windowMaxX"]))
        in_y = (rows_y >= float(h["windowMinY"])) & (rows_y <= float(h["windowMaxY"]))

        # floor(x * sxf) -- a MULTIPLY, and the same expression the mod indexes
        # with (telemetry.cpp:842). Not a divide: sxf is 0.01, so dividing lands
        # the index 1e4 too high and every column falls outside the mask, which
        # is how this silently reported "nothing is known" everywhere.
        start_col = int(h["coverageStartCol"])
        cov = np.asarray(self.coverage())
        section = np.floor(cols_x * sxf).astype(np.int64) - start_col
        # WHY A WINDOW THAT IS TOO WIDE CANNOT LIE.
        #
        # The advertised window and the coverage mask DO disagree at the left
        # edge, and not rarely: the mod publishes windowMinX as the window's own
        # left edge (telemetry.cpp:928) -- since 2026-08-15 that is the LIVE
        # CAMERA's left edge, previously px - g_winBehind -- but derives
        # col0 = floor(minX * sxf) (:842), so the window starts partway INTO
        # column col0, by up to a full section. Nothing below depends on which
        # of the two the left edge came from; that is the point of deriving the
        # column from the window rather than the reverse.
        #
        # That disagreement can never manufacture knowledge, and the reason is
        # structural rather than empirical: `state` below is looked up from the
        # PER-CELL section index, not from the window. A cell the window admits
        # is still UNKNOWN unless its own column says SCANNED or ABSENT, and a
        # cell outside the window is dropped by the `in_x &` regardless of what
        # its column says. Knowledge is the INTERSECTION of the two, so widening
        # either one alone can only ever remove cells, never add them. A window
        # that overreaches (past the coverage array, before column col0, or into
        # a column the scan never reached) hits `valid` or a non-SCANNED state
        # and resolves to UNKNOWN. That is a property of the expression, not a
        # measurement of one fixture, and it holds for any window the mod emits.
        #
        # A column the mask cannot speak for is therefore UNKNOWN, never "empty".
        valid = (section >= 0) & (section < GDRL_COVERAGE_COLS)
        state = np.where(valid, cov[np.clip(section, 0, GDRL_COVERAGE_COLS - 1)],
                         int(GdrlCoverage.UNKNOWN))
        col_known = in_x & (
            (state == int(GdrlCoverage.SCANNED)) | (state == int(GdrlCoverage.ABSENT))
        )
        return KnownMask((height, width), None,
                         _grid=col_known[None, :] & in_y[:, None])

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


# Measured live 2026-08-12 from the section-grid dump (`[gdrl] sectionXFactor`
# / `sectionYFactor` in main.cpp), identical in all 52 surviving logs:
#
#   m_sectionXFactor = 0.01   -- a MULTIPLIER (1 / section width). On the wire as
#                                the float32 0.009999999776482582. Column index
#                                is floor(x * sxf), which is README's documented
#                                floor(x / 100).
#   m_sectionYFactor = 0      -- and this is the measurement, not a hole. GD's
#                                grid is effectively 1-D: the middle vector of
#                                m_sections is 1 deep on every level dumped, and
#                                the mod's vertical filter is a direct
#                                m_positionY compare. Nothing may "fix" this by
#                                symmetry with x, and nothing may divide by it.
#
# The fixture publishes these rather than a round 100.0 because a test built on
# a header the game never produces is testing a world that does not exist.
MEASURED_SECTION_X_FACTOR = float(np.float32(0.01))
MEASURED_SECTION_Y_FACTOR = 0.0

# Stereo Madness, from the same dump: m_levelLength 26724, m_sections.size() 268.
# floor(26724 * 0.01) + 1 == 268 -- exact, and the arithmetic only closes under
# the multiplier reading (the divisor reading would need 2,672,400 columns).
MEASURED_LEVEL_LENGTH = 26724.0
MEASURED_SECTION_COLUMNS = 268

# The synth test level from the same dump (telemetry.cpp:686-687): 6340 -> 64.
# Kept because it is the SECOND of the only two levels the section-count identity
# has ever been measured on, and because it is the one where that identity is
# weakest -- see Observation.level_length_agrees_with_section_count().
MEASURED_SYNTH_LEVEL_LENGTH = 6340.0
MEASURED_SYNTH_SECTION_COLUMNS = 64

# ---------------------------------------------------------------------------
# THE WINDOW IS NO LONGER THREE CONSTANTS. Read this before using the four
# below for anything except shaping a fixture frame.
#
# Until 2026-08-15 the mod's observation window was px +- GDRL_ENV_WIN_BEHIND /
# _AHEAD / _VERT, defaulting to 400 / 1400 / 600, and this file's constants were
# a faithful copy of those defaults. They are not any more. The mod now derives
# the window from the LIVE CAMERA every physics step (telemetry.cpp:828-832,
# gdrl::cameraWorldRect); GDRL_ENV_WIN_* survive only as off-by-default
# overrides for ablations and the Benchmark B oracle, and telemetry.cpp:233-243
# says in as many words that setting one makes the run non-A.
#
# Consequences for anything Python-side that wants to know "how big is the
# window":
#
#   * There is no answer available from a constant. It depends on camera zoom
#     and, under kResolutionFixedHeight, on the OS window's aspect ratio.
#   * The answer IS available per frame, on the wire, and always was:
#     windowMinX / windowMaxX / windowMinY / windowMaxY are whatever the mod
#     actually used. known_mask() has only ever read those, which is why the
#     decoder needed no change for any of this.
#   * The real window is NOT symmetric vertically. The player sits low on
#     screen: +215 above, -105 below.
#
# MEASURED_CAM_* are the 2026-08-15 probe numbers (GDRL_PROBE_VIEWPORT, Stereo
# Madness, 1x, zoom 1.0, ~16:9 -- backups/reference-logs/viewport-probe-
# 20260815-171406.log). They are a RECORD OF ONE MEASUREMENT, not a definition:
# nothing may reconstruct a window from them, because the whole reason the mod
# stopped using constants is that a constant cannot track zoom or aspect. They
# exist so a fixture can publish a frame shaped like one the game produces.
MEASURED_CAM_BEHIND = 209.5
MEASURED_CAM_AHEAD = 359.5
MEASURED_CAM_UP = 215.0
MEASURED_CAM_DOWN = 105.0

# The fixture's default window shape. NOT the mod's window -- see above -- and
# named so that nothing reads it as one. It is the pre-2026-08-15 geometry,
# retained verbatim because ~60 mask tests are built on it and re-shaping them
# would change what those tests mean without testing anything new. It also
# remains a shape the mod can still be asked for, via the GDRL_ENV_WIN_*
# overrides, so it is not a fabricated configuration.
FIXTURE_WIN_BEHIND = 400.0
FIXTURE_WIN_AHEAD = 1400.0
FIXTURE_WIN_VERT = 600.0


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
        # GDRL_PRACTICE: every CHECKPOINT_SAVE/RESTORE/CLEAR seen by consume(),
        # in order. Deliberately just a log, not a state machine -- see
        # consume()'s own comment for why this fixture does not decide
        # accept/refuse for these three kinds the way the mod does.
        self.checkpoint_requests: list[GdrlActionKind] = []
        # How many coverage entries the LAST publish() actually marked SCANNED.
        # It is not always the ``coverage_cols`` that was asked for: the mod's
        # window can bind first (see publish()), and a test that assumed
        # otherwise would be checking a geometry the fixture did not produce.
        self.scanned_cols = 0
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
                object_count_total: int = 2400,
                section_x_factor: float = MEASURED_SECTION_X_FACTOR,
                section_y_factor: float = MEASURED_SECTION_Y_FACTOR,
                layout_x_factor: float | None = None,
                level_length: float = MEASURED_LEVEL_LENGTH,
                section_columns: int = MEASURED_SECTION_COLUMNS,
                win_behind: float = FIXTURE_WIN_BEHIND,
                win_ahead: float = FIXTURE_WIN_AHEAD,
                win_vert: float = FIXTURE_WIN_VERT,
                win_up: float | None = None,
                win_down: float | None = None,
                resume_tick: int = 0,
                checkpoint_tick: int = -1,
                has_checkpoint: bool = False,
                practice_mode: bool = False,
                full_attempts: int = 0,
                practice_attempts: int = 0) -> int:
        """Write one observation and advance the sequence, seqlock and all.

        ``coverage_cols`` is the fixture's stand-in for ``GDRL_COVERAGE_COLS``
        in the clamp at telemetry.cpp:874 -- how many columns the mask is
        allowed to speak for. It is an UPPER bound, not a promise: the requested
        window can bind first, exactly as it does in the mod. In the real game
        it now ALWAYS binds first and by a wide margin: the measured camera is
        569 world units wide, ~7 columns at the measured 100-unit section, so
        the mod's 64-column clamp is slack. That is a change from the old
        1400-ahead constant (18 columns, still slack) and it is recorded here
        because a clamp that never binds is how a test quietly stops testing
        what its name says (TODO N2). Read ``self.scanned_cols`` for how many
        entries actually came out SCANNED.

        ``win_behind`` / ``win_ahead`` / ``win_vert`` DO NOT DESCRIBE THE MOD'S
        WINDOW ANY MORE. Since 2026-08-15 the mod takes its window from the live
        camera each step and these three survive there only as off-by-default
        GDRL_ENV_WIN_* overrides -- see the FIXTURE_WIN_* comment above. Here
        they are a way to shape the fixture's rectangle, nothing else. Every
        mask test recomputes its expectation from the published header, so the
        shape is exercise, not doctrine.

        ``win_up`` / ``win_down`` make the vertical window ASYMMETRIC, which the
        real one is: the player sits low on screen, +215 up and -105 down. Both
        default to ``None``, meaning ``win_vert`` either side, which is the
        symmetric shape every pre-existing test was written against. Pass
        ``MEASURED_CAM_UP`` / ``MEASURED_CAM_DOWN`` for a camera-shaped frame.

        ``section_x_factor`` defaults to the measured 0.01 and is a parameter
        only so a test can inject an unusable value and check that the decoder
        refuses rather than substitutes. It is the value that lands in the
        header.

        ``layout_x_factor`` is the factor the *geometry* is built from -- the
        window bounds, ``coverageStartCol``, and which coverage entries are
        SCANNED. It defaults to ``section_x_factor``, i.e. header and geometry
        agree, which is the only combination GD is claimed to produce.

        Passing them differently FABRICATES a header: it is the only way to hand
        the decoder an unusable factor on a frame that otherwise looks normal,
        and it exists because the honest refusal frame cannot test the factor
        guard. When the factor is unusable the mod refuses the whole scan --
        zero-area window, every column UNKNOWN, ``OBJECTS_UNAVAILABLE`` set --
        and the decoder checks that flag first, so ``section_x_factor=0.0``
        alone never reaches the factor branch, and the zero-area window makes an
        empty mask prove nothing (a decoder that substituted 0.01 would produce
        an empty mask too, from a window of zero area). Use

            publish(section_x_factor=0.0,
                    layout_x_factor=MEASURED_SECTION_X_FACTOR)

        to get a real window, a SCANNED coverage prefix, no
        ``OBJECTS_UNAVAILABLE``, and an unusable factor as the only defect. That
        header is a test instrument. It is NOT asserted to be a state the game
        produces.

        ``resume_tick`` / ``checkpoint_tick`` / ``has_checkpoint`` /
        ``practice_mode`` / ``full_attempts`` / ``practice_attempts`` are the
        GDRL_PRACTICE wire fields (wire version 2), written straight through with
        no derivation and no bookkeeping -- exactly like ``status`` above. The
        caller decides the sequence (e.g. "this call is a checkpoint-resumed
        attempt") and passes the numbers that sequence implies; this method does
        not infer resume_tick from has_checkpoint or increment either attempt
        counter itself. That is deliberate: a fixture that decided this FOR the
        test would be re-deriving the same accounting the mod does and then
        checking its own derivation, which proves nothing about the mod. Defaults
        (0, -1, False, False, 0, 0) match the mod's own "nothing happened yet"
        state (gdrl_schema.hpp: checkpointTick is -1 when none exists), so a test
        that never mentions these params gets a full, non-practice attempt.
        """
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
        h["sectionXFactor"] = section_x_factor
        h["sectionYFactor"] = section_y_factor
        h["levelLength"] = level_length
        h["isDualMode"] = 0
        h["isPaused"] = 0
        h["inResetDelay"] = 0
        h["resumeTick"] = resume_tick
        h["checkpointTick"] = checkpoint_tick
        h["hasCheckpoint"] = 1 if has_checkpoint else 0
        h["practiceMode"] = 1 if practice_mode else 0
        h["commandCount"] = 0
        h["pendingCount"] = 0
        h["speedSegCount"] = 0
        h["flags"] = (int(GdrlHeaderFlag.GAMEPLAY_STEP)
                      | int(GdrlHeaderFlag.INPUT_CLEAN)
                      | int(GdrlHeaderFlag.COMMANDS_UNAVAILABLE)
                      | int(GdrlHeaderFlag.PENDING_UNAVAILABLE)
                      | int(GdrlHeaderFlag.SPEEDSEGS_UNAVAILABLE)
                      | extra_flags)

        # The window arithmetic is a TRANSCRIPTION of scanObjects(),
        # telemetry.cpp:718-1051, in the same order and the same form -- the
        # WINDOW is primary and the columns are derived from it, never the other
        # way round. Each step below cites the line it mirrors. The one
        # substitution is `coverage_cols` where the mod writes
        # GDRL_COVERAGE_COLS, so a test can hand the decoder a narrow mask; with
        # coverage_cols == GDRL_COVERAGE_COLS this is the mod's expression
        # unchanged.
        #
        # ONE PLACE THE TRANSCRIPTION CANNOT BE LITERAL, since 2026-08-15. The
        # mod's four window bounds are the live camera rect (telemetry.cpp:
        # 828-832, `cam.left` / `cam.right` / `cam.bottom` / `cam.top`); there
        # is no camera here, so the fixture builds a rectangle around the player
        # from its parameters instead. Everything downstream of the four bounds
        # -- col0, col1, the clamp, the right edge, the coverage loop -- is
        # still statement-for-statement the mod's, and that is the part this
        # transcription is for. The bounds themselves are graded by the header
        # comparison, not by this arithmetic: whatever the mod computed arrives
        # in windowMinX..windowMaxY and known_mask() reads it from there.
        #
        # It used to derive windowMinX from col0 instead (`col0 * sec_w`), which
        # snapped the advertised left edge DOWN to a column boundary and made the
        # fixture's window up to a full section wider than any the mod publishes
        # -- 100 units too wide at the default player_x=500, which is the frame
        # nearly every mask test stands on. The mechanism was the same sub-0.01
        # sxf that caused the original inversion: 100 * 0.009999999776482582 is
        # 0.9999999776, which floors to 0, so col0 was 0 while minX was 100.
        # The mod defines what arrives on the wire, so the fixture moved.
        #
        # sectionYFactor is not used for anything, here or in the mod -- the
        # vertical filter is a direct y compare -- so its measured 0 never
        # reaches a divide.
        #
        # The geometry is laid out from layout_x_factor, and the refusal branch
        # is taken when THAT is unusable -- because it is what the window and the
        # coverage array would have been computed from. The header carries
        # section_x_factor either way; see the docstring for why they can differ.
        sxf = float(np.float32(section_x_factor if layout_x_factor is None
                               else layout_x_factor))
        cov = self.obs["coverage"]
        if not (sxf > 0.0) or not np.isfinite(sxf):
            # The mod's refusal path (refuseScan, telemetry.cpp:651-669):
            # claim nothing.
            # Zero-area window, every column UNKNOWN, OBJECTS_UNAVAILABLE set so
            # a count of 0 reads as "did not look" rather than "looked and found
            # none".
            h["coverageStartCol"] = 0
            h["sectionColumns"] = section_columns
            h["windowMinX"] = player_x
            h["windowMaxX"] = player_x
            h["windowMinY"] = player_y
            h["windowMaxY"] = player_y
            h["flags"] = int(h["flags"]) | int(GdrlHeaderFlag.OBJECTS_UNAVAILABLE)
            cov[:] = int(GdrlCoverage.UNKNOWN)
            self.scanned_cols = 0
        else:
            sec_w = 1.0 / sxf                                    # :718
            min_x = player_x - win_behind                        # :828
            max_x_requested = player_x + win_ahead               # :829
            # Asymmetric when asked, symmetric by default. win_vert is exactly
            # what GDRL_ENV_WIN_VERT still means in the mod -- a HALF-extent
            # applied either side -- while the camera's own bounds are not
            # symmetric at all.
            below = win_vert if win_down is None else win_down
            above = win_vert if win_up is None else win_up
            min_y = player_y - below                             # :831
            max_y = player_y + above                             # :832
            n_cols = section_columns                             # :841 m_sections.size()
            col0 = int(math.floor(min_x * sxf))                  # :842
            col0 = max(0, col0)                                  # :843
            col1 = int(math.floor(max_x_requested * sxf))        # :844
            col1 = min(col1, col0 + coverage_cols - 1)           # :874
            # No right-edge back-off. A `std::nextafter` loop used to sit
            # between these two lines, mirroring 8dd5ceb:677-680; the mod
            # measured it dead over every float32 px in [-2048, 131072) at
            # sxf = 0.01f (2.37e9 values, stored float bit-identical with and
            # without it) and deleted it in efc32e9. Deleted here to match.
            col_edge = (col1 + 1) * sec_w                        # :920
            max_x = min(max_x_requested, col_edge)               # :921
            h["coverageStartCol"] = col0                         # :926
            h["sectionColumns"] = n_cols                         # :927
            h["windowMinX"] = min_x                              # :928
            h["windowMaxX"] = max_x                              # :929
            h["windowMinY"] = min_y                              # :930
            h["windowMaxY"] = max_y                              # :931
            # The coverage loop, telemetry.cpp:936-1051: UNKNOWN past col1,
            # ABSENT past the end of GD's own section grid, SCANNED otherwise.
            # (TRUNCATED is one state the fixture cannot reach -- it needs the
            # object array to fill mid-column -- so tests that need it set it by
            # hand.)
            scanned = 0
            for c in range(GDRL_COVERAGE_COLS):
                col = col0 + c
                if col > col1:
                    cov[c] = int(GdrlCoverage.UNKNOWN)
                elif col >= n_cols:
                    cov[c] = int(GdrlCoverage.ABSENT)
                else:
                    cov[c] = int(GdrlCoverage.SCANNED)
                    scanned += 1
            self.scanned_cols = scanned

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
        v["fullAttempts"] = full_attempts
        v["practiceAttempts"] = practice_attempts

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
            elif kind in (GdrlActionKind.CHECKPOINT_SAVE,
                          GdrlActionKind.CHECKPOINT_RESTORE,
                          GdrlActionKind.CHECKPOINT_CLEAR):
                # GDRL_PRACTICE. Deliberately NOT modelled beyond "a request of
                # this kind arrived": the mod's actual accept/refuse decision
                # (GDRL_PRACTICE off, no checkpoint held, getLastCheckpoint()
                # returning null) is exactly the logic TODO.md 2.1 says is
                # UNVERIFIED without the game, and this fixture has no game to
                # check it against. Encoding a guess here and then testing
                # against that guess would be self-consistency, not evidence
                # (see this repo's own rule on that). This log is enough to
                # prove the three new action kinds survive the wire intact;
                # see checkpoint_requests.
                self.checkpoint_requests.append(kind)
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
