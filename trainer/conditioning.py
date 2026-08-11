"""Objective A: vehicle and state mechanics conditioning.

GD is eight games sharing a renderer. The same local geometry -- a spike two
tiles ahead at floor level -- is lethal in cube, irrelevant in ship at altitude,
and means the opposite thing under inverted gravity. A single static mapping
from observation to action therefore cannot work; the policy has to be
conditioned on the active physics regime.

The approach here is FiLM (Perez et al., AAAI 2018, arXiv:1709.07871): a small
network reads the regime and emits a per-channel affine transform (gamma, beta)
that is applied inside every residual block of the geometry trunk. The trunk
never sees the regime directly -- it only ever gets *reconfigured* by it.

Three findings from that paper drive the design decisions below, each marked at
the point where it applies:

  (F1) gamma carries the conditioning. Ablating beta at test time cost them 1.0%
       accuracy; ablating gamma cost 65.4%. Multiplicative gating can switch a
       feature detector off, which is what "this hazard does not apply to the
       current vehicle" requires. An additive bias cannot.

  (F2) Never squash gamma. Sigmoid, tanh and exp restrictions all underperformed
       an unrestricted affine projection. Negative gamma inverts a feature's
       sign, which is the natural encoding for a gravity flip.

  (F3) The conditioning space is linear and composes. On CLEVR-CoGenT they
       generalised zero-shot to unseen attribute combinations, and improved
       further by manipulating the FiLM parameters linearly. Our regime space is
       8 vehicles x 2 gravity x 2 size x 5 speeds x timewarp, and training
       coverage of it will be violently skewed -- 1x normal cube constantly,
       4x mini inverted wave almost never. Additive composition in conditioning
       space is what transfers across those cells; a per-mode head or a mixture
       of experts would not.

The regime fields mirror the COND log lines emitted by mod/src/main.cpp, which
reads them off PlayerObject and GJBaseGameLayer every frame. Field names are
pinned to GD 2.2081 bindings.

The mod's job stops at extraction: it serialises raw measured values and never
interprets them. Every derivation below -- which vehicle a flag word encodes,
whether a vehicle size counts as mini, dual-vehicle representation -- lives in
this file, testable in Python without a running game, rather than split across
the C++/Python boundary where half of it silently trains on the wrong label.
`Regime.from_wire` is the boundary: raw wire scalars in, semantics applied
here.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn as nn
import torch.nn.functional as F

from schema_generated import GdrlVehicle, GdrlVehicleFlag

# Re-exported rather than redeclared. This used to be `class Vehicle(IntEnum)`
# here, kept numerically identical to `enum class Vehicle` in main.cpp by a
# comment -- the exact shape of bug schema.py's docstring calls out: a drifted
# duplicate does not crash, it silently mislabels every regime and still
# trains to a plausible loss curve. Both language bindings are now generated
# from trainer/schema.py; conditioning.py just imports its half instead of
# keeping a second copy in sync by hand.
Vehicle = GdrlVehicle

NUM_VEHICLES = len(Vehicle)

# Bit positions of the raw flag word straight off PlayerObject's seven mode
# flags (GdrlVehicleFlag in schema_generated.py, generated from schema.py).
# Imported rather than hardcoded so the bit order can only change in one
# place, and that place is schema-hashed.
_SWING = GdrlVehicleFlag.SWING
_SPIDER = GdrlVehicleFlag.SPIDER
_ROBOT = GdrlVehicleFlag.ROBOT
_DART = GdrlVehicleFlag.DART
_BIRD = GdrlVehicleFlag.BIRD
_BALL = GdrlVehicleFlag.BALL
_SHIP = GdrlVehicleFlag.SHIP
_ALL_VEHICLE_BITS = (_SWING, _SPIDER, _ROBOT, _DART, _BIRD, _BALL, _SHIP)


def vehicle_flags_overlap(flags: int) -> bool:
    """True if more than one vehicle-mode bit is set in the raw flag word.

    PlayerObject's seven mode flags are supposed to be mutually exclusive, but
    that is an assumption about GD's internal state, not something this repo
    has verified -- the README notes only cube has actually been reached with
    no input injection, so every other vehicle's flag behaviour is UNVERIFIED.
    `derive_vehicle`'s check order is a defensive guess for exactly the case
    where the exclusivity assumption breaks; this function is what makes that
    guess visible to the caller instead of letting the ordering quietly paper
    over it.
    """
    return bin(flags & sum(_ALL_VEHICLE_BITS)).count("1") > 1


def derive_vehicle(flags: int) -> Vehicle:
    """Vehicle label from the raw 7-bit mode-flag word off PlayerObject.

    Reproduces mod/src/main.cpp's `deriveVehicle` check order exactly: swing,
    spider, robot, dart (wave), bird (ufo), ball, ship, then cube as the
    fallthrough (absence of all seven). See README "Vehicle flags are checked
    derived-first" -- the order is defensive, not arbitrary: swing is
    ship-like and spider is ball-like, so if GD ever leaves a parent flag set
    while a child mode is active, testing the parent first would silently
    return the wrong label. Use `vehicle_flags_overlap` to detect when that
    ambiguity is actually present in a given flag word; this function always
    returns a single label regardless.
    """
    if flags & _SWING:
        return Vehicle.SWING
    if flags & _SPIDER:
        return Vehicle.SPIDER
    if flags & _ROBOT:
        return Vehicle.ROBOT
    if flags & _DART:
        return Vehicle.WAVE
    if flags & _BIRD:
        return Vehicle.UFO
    if flags & _BALL:
        return Vehicle.BALL
    if flags & _SHIP:
        return Vehicle.SHIP
    return Vehicle.CUBE


def derive_mini(vehicle_size: float) -> bool:
    """Mini threshold. Measured values (main.cpp readCond / README "Conditioning
    state"): 1.0 normal, 0.6 mini. 0.9 is the midpoint main.cpp used
    (`m_vehicleSize < 0.9f`); reproduced verbatim here rather than re-derived,
    since the mod comment is the only record of where that split falls."""
    return vehicle_size < 0.9


# GD's five speed portals, as the internal m_playerSpeed multiplier rather than
# the 0.5x/1x/2x/3x/4x the UI advertises. 1x is 0.9 internally -- measured, not
# assumed: 1.298250437 units per 1/240s tick is 311.6 units/s at m_playerSpeed
# 0.90 (see README, "Physics is fixed-step at 240Hz").
SPEED_MULTIPLIERS = (0.7, 0.9, 1.1, 1.3, 1.6)
NUM_SPEEDS = len(SPEED_MULTIPLIERS)

# Likewise m_gravity reads 0.96 at normal gravity, not 1.0 -- observed in the
# COND log on Stereo Madness. Gravity is normalised against this so that the
# scalar channel carries "how far from normal" rather than a constant 0.96 that
# the network has to subtract off itself.
GRAVITY_NORMAL = 0.96


@dataclass(frozen=True)
class Regime:
    """The conditioning state at one physics tick.

    Deliberately excludes position, velocity and anything else that varies
    continuously within a regime -- those are observations. This holds only the
    axes that change *how an input is interpreted*.

    `vehicle` is always player 1's (or the sole player's, outside dual mode).
    `vehicle2`, player 2's, exists because a dual section is not one regime
    with a `dual` flag -- the two players can be in genuinely different
    vehicles (cube + ship is a normal dual-mode setup), which a single
    `vehicle` field cannot represent at all. `vehicle2 is None` means "not
    dual, or dual but player 2's vehicle was not measured" and degrades to
    exactly the old single-vehicle behaviour (see RegimeEncoder). It is only
    ever meaningful when `dual` is True, which `__post_init__` enforces so a
    mislabelled regime fails at construction rather than downstream in the
    encoder.
    """

    vehicle: Vehicle = Vehicle.CUBE
    upside_down: bool = False
    mini: bool = False
    dual: bool = False
    sideways: bool = False
    player_speed: float = 0.9
    gravity: float = GRAVITY_NORMAL
    time_warp: float = 1.0
    vehicle2: Vehicle | None = None

    def __post_init__(self) -> None:
        # Fail loudly: a non-dual regime carrying a second player's vehicle is
        # not a plausible-but-wrong regime, it is a contradiction, and this
        # repo's recurring failure mode is exactly a broken label that trains
        # anyway. Better to raise here than have RegimeEncoder silently add a
        # player-2 contribution nobody asked for.
        if self.vehicle2 is not None and not self.dual:
            raise ValueError(
                "Regime.vehicle2 is set but dual is False -- a non-dual "
                "regime cannot have a second player's vehicle"
            )

    def speed_bucket(self) -> int:
        """Nearest speed portal. Triggers can set values off the portal grid, so
        this snaps rather than looking up, and the residual is carried through
        the continuous channel below."""
        return min(
            range(NUM_SPEEDS),
            key=lambda i: abs(SPEED_MULTIPLIERS[i] - self.player_speed),
        )

    @classmethod
    def from_wire(
        cls,
        *,
        vehicle_flags: int,
        is_upside_down: bool,
        is_sideways: bool,
        vehicle_size: float,
        player_speed: float,
        gravity: float,
        is_dual_mode: bool,
        time_warp: float,
        vehicle_flags2: int | None = None,
    ) -> "Regime":
        """Build a Regime from raw wire scalars: GdrlPlayerState's fields for
        one player, plus GdrlObsHeader.isDualMode and .timeWarp (regime-level,
        not per-player, hence not on GdrlPlayerState).

        Plain Python scalars, not a numpy structured record, so this is
        testable without a live shared-memory mapping or a numpy fixture --
        the caller (env.py) is expected to pull these off a decoded
        GdrlObservation and pass them through. Every derivation the mod used
        to make itself (`mini = vehicleSize < 0.9f` lived in main.cpp readCond
        until this fix) happens here instead: raw in, interpreted Regime out.

        `vehicle_flags2` is not a literal GdrlPlayerState field name -- it is
        players[1].vehicleFlags, the second player's raw flag word. It is
        optional and keyword-only because most callers (non-dual sections, or
        a dual section before player 2's state has been wired up) do not have
        it; passing None (the default) produces vehicle2=None, i.e. no
        change from the pre-fix single-vehicle behaviour. It is ignored
        (forced to None) when is_dual_mode is False, matching the
        __post_init__ invariant above rather than raising on what is more
        likely a caller passing stale data than an actual contradiction.
        """
        vehicle2 = (
            derive_vehicle(vehicle_flags2)
            if is_dual_mode and vehicle_flags2 is not None
            else None
        )
        return cls(
            vehicle=derive_vehicle(vehicle_flags),
            upside_down=bool(is_upside_down),
            mini=derive_mini(vehicle_size),
            dual=bool(is_dual_mode),
            sideways=bool(is_sideways),
            player_speed=player_speed,
            gravity=gravity,
            time_warp=time_warp,
            vehicle2=vehicle2,
        )


def _check_batch(expected: int, **tensors: torch.Tensor) -> None:
    """Raise if any tensor's leading (batch) dimension disagrees with `expected`.

    The precedent is trajectory.py's TrajectoryRaster.render_batch, which
    raises on a length mismatch instead of letting torch.stack paper over it.
    Without a check like this, ConditionedTrunk.forward silently broadcasts:
    if the regime batch is size 1 and the obs batch is size N, gamma/beta
    broadcast across every sample, so every sample in the batch gets
    conditioned on regime[0] and nothing downstream can tell -- the run still
    trains to a plausible-looking loss curve. See the module docstring; this
    is that failure mode, closed.
    """
    for name, t in tensors.items():
        if t.shape[0] != expected:
            raise ValueError(
                f"batch mismatch: expected batch {expected}, got {name} with "
                f"batch {t.shape[0]}"
            )


class RegimeEncoder(nn.Module):
    """Regime -> conditioning vector.

    Discrete axes get embeddings; continuous ones are appended as scalars. The
    two are summed rather than concatenated so that the space stays additive:
    "mini" is a fixed direction that can be added to any vehicle embedding,
    including combinations never observed together in training. That additivity
    is the mechanism behind (F3), and it is the reason this is not simply an MLP
    over a one-hot concatenation.
    """

    def __init__(self, width: int = 128):
        super().__init__()
        self.width = width

        self.vehicle = nn.Embedding(NUM_VEHICLES, width)
        self.speed = nn.Embedding(NUM_SPEEDS, width)

        # Binary axes as directions in the same space, gated by their flag.
        # A zero flag contributes exactly nothing, so an unseen combination is
        # the sum of directions each of which was trained on.
        #
        # Randomly initialised at the same scale as the embeddings, not zeroed.
        # Zero-init leaves these axes contributing nothing at all until trained,
        # so a fresh network is blind to a gravity flip while already
        # distinguishing vehicles -- and worse, indistinguishable from having
        # forgotten to wire the axis up.
        def direction() -> nn.Parameter:
            return nn.Parameter(torch.randn(width) * 0.02)

        self.gravity_dir = direction()
        self.mini_dir = direction()
        self.dual_dir = direction()
        self.sideways_dir = direction()

        # Second player's vehicle (F4 fix -- see Regime.vehicle2), its own
        # embedding table rather than the `vehicle` table above plus a fixed
        # offset. A fixed per-slot offset was the first thing tried here and
        # it does not work: for any constant `off`, embed(v1) + [embed(v2) +
        # off] is invariant under exchanging v1 and v2, because addition is
        # commutative regardless of which value the offset happens to be
        # attached to -- embed(A) + embed(B) + off == embed(B) + embed(A) +
        # off for every A, B. No per-slot additive constant can encode which
        # player is in which vehicle; only two functions that differ in more
        # than a constant shift can. So this is structurally the same move as
        # `speed` already being a separate table from `vehicle` two lines up:
        # player 2's vehicle is its own axis, not a modifier of player 1's.
        # It is deliberately NOT weight-tied to `self.vehicle`, so it does not
        # inherit player 1's "ship" representation for free -- dual-section
        # training has to teach it independently, which is real cost, but the
        # alternative (any tied, offset-only scheme) cannot represent (cube,
        # ship) and (ship, cube) as different regimes at all, which is worse:
        # a representation that cannot express the input is not a training
        # problem, it is a bug at the input encoding.
        self.vehicle2 = nn.Embedding(NUM_VEHICLES, width)
        nn.init.normal_(self.vehicle2.weight, std=0.02)

        # Continuous residuals: gravity multiplier, timewarp, and the part of
        # player_speed that the bucket does not capture. Kept separate from the
        # embeddings so trigger-set off-grid values degrade gracefully instead
        # of snapping to a wrong bucket and losing the difference entirely.
        self.continuous = nn.Linear(3, width)

        nn.init.normal_(self.vehicle.weight, std=0.02)
        nn.init.normal_(self.speed.weight, std=0.02)

    def forward(
        self,
        vehicle: torch.Tensor,       # (B,) int64
        speed_bucket: torch.Tensor,  # (B,) int64
        flags: torch.Tensor,         # (B, 4) float: upside_down, mini, dual, sideways
        scalars: torch.Tensor,       # (B, 3) float: gravity, time_warp, speed residual
        vehicle2: torch.Tensor,      # (B,) int64: player 2's vehicle, meaningless where has_vehicle2 == 0
        has_vehicle2: torch.Tensor,  # (B,) float: 1.0 where vehicle2 was actually measured
    ) -> torch.Tensor:
        _check_batch(vehicle.shape[0], speed_bucket=speed_bucket, flags=flags,
                     scalars=scalars, vehicle2=vehicle2, has_vehicle2=has_vehicle2)

        z = self.vehicle(vehicle) + self.speed(speed_bucket)

        dirs = torch.stack(
            [self.gravity_dir, self.mini_dir, self.dual_dir, self.sideways_dir]
        )                                    # (4, width)
        z = z + flags @ dirs                 # (B, width)
        z = z + self.continuous(scalars)

        # has_vehicle2 is a 0/1 gate, not a sentinel vehicle index, for the
        # same reason the flag-gated directions above are gated rather than
        # sentinel-valued: a zero contributes exactly nothing, so the single-
        # vehicle / non-dual case (has_vehicle2 all zero) is numerically
        # identical to the pre-F4 encoder, and every existing test that never
        # sets vehicle2 is asserting a structural property that still holds.
        z = z + has_vehicle2[:, None] * self.vehicle2(vehicle2)
        return z


class FiLMGenerator(nn.Module):
    """Conditioning vector -> (gamma, beta) for every block.

    One shared trunk with a per-block linear head, matching the paper's setup
    (a GRU over the question, linearly projected per ResBlock). No activation on
    the projection -- see (F2), every restriction they tried hurt.

    gamma is produced as a residual around 1.0 rather than directly. At init the
    projection outputs ~0, so gamma ~= 1 and beta ~= 0: the network starts as an
    unconditioned trunk and learns to modulate away from it. Predicting gamma
    directly from a zero-init layer would instead start with every feature map
    multiplied by zero and kill the gradient signal through the trunk.
    """

    def __init__(self, cond_width: int, channels: int, num_blocks: int):
        super().__init__()
        self.channels = channels
        self.num_blocks = num_blocks

        self.trunk = nn.Sequential(
            nn.Linear(cond_width, cond_width),
            nn.ReLU(inplace=True),
        )
        self.heads = nn.ModuleList(
            [nn.Linear(cond_width, 2 * channels) for _ in range(num_blocks)]
        )
        for head in self.heads:
            nn.init.zeros_(head.weight)
            nn.init.zeros_(head.bias)

    def forward(self, z: torch.Tensor) -> list[tuple[torch.Tensor, torch.Tensor]]:
        h = self.trunk(z)
        out = []
        for head in self.heads:
            gamma_res, beta = head(h).chunk(2, dim=-1)
            out.append((1.0 + gamma_res, beta))
        return out


class FiLMResBlock(nn.Module):
    """A residual block whose features are modulated by (gamma, beta).

    Layout follows the paper: 1x1 conv, 3x3 conv, normalisation with its own
    affine disabled, then FiLM, then ReLU, then the residual add. The affine is
    disabled because FiLM *is* the affine -- leaving both in place gives two
    redundant per-channel scales fighting each other.

    GroupNorm rather than BatchNorm: the paper used BatchNorm and found removing
    normalisation cost ~4%, but RL rollouts arrive correlated and with small or
    varying batch sizes, where BatchNorm's batch statistics are actively
    harmful. GroupNorm keeps the normalisation benefit without the batch
    coupling.
    """

    def __init__(self, channels: int, groups: int = 8):
        super().__init__()
        self.conv1 = nn.Conv2d(channels, channels, kernel_size=1)
        self.conv2 = nn.Conv2d(channels, channels, kernel_size=3, padding=1)
        self.norm = nn.GroupNorm(groups, channels, affine=False)

    def forward(
        self, x: torch.Tensor, gamma: torch.Tensor, beta: torch.Tensor
    ) -> torch.Tensor:
        h = F.relu(self.conv1(x), inplace=True)
        residual = h
        h = self.norm(self.conv2(h))
        h = gamma[:, :, None, None] * h + beta[:, :, None, None]
        h = F.relu(h, inplace=True)
        return h + residual


class ConditionedTrunk(nn.Module):
    """Local geometry window -> regime-conditioned features.

    The observation is a small multi-channel occupancy window around the player
    (hazards, solids, portals, orbs, ...). Its exact channel layout is not fixed
    yet -- that is the next objective -- so this takes in_channels as a
    parameter and does not assume anything beyond "spatial grid".

    (F3) again: depth is not critical. The paper ran 1 to 12 blocks across a
    4.2-point accuracy band, so there is no need to tune this before the
    environment exists. Four is their default and a reasonable starting point.
    """

    def __init__(
        self,
        in_channels: int,
        channels: int = 128,
        num_blocks: int = 4,
        cond_width: int = 128,
        coord_maps: bool = True,
    ):
        super().__init__()
        self.coord_maps = coord_maps
        stem_in = in_channels + (2 if coord_maps else 0)

        self.stem = nn.Conv2d(stem_in, channels, kernel_size=3, padding=1)
        self.blocks = nn.ModuleList(
            [FiLMResBlock(channels) for _ in range(num_blocks)]
        )
        self.encoder = RegimeEncoder(cond_width)
        self.film = FiLMGenerator(cond_width, channels, num_blocks)

    def _with_coords(self, x: torch.Tensor) -> torch.Tensor:
        """Append normalised x/y coordinate channels.

        Worth only ~2% in the paper, but cheap and more likely to matter here:
        vertical position relative to the player is the difference between a
        ceiling hazard and a floor hazard, and that flips meaning under inverted
        gravity -- exactly the kind of interaction FiLM is positioned to encode.
        """
        b, _, h, w = x.shape
        ys = torch.linspace(-1, 1, h, device=x.device, dtype=x.dtype)
        xs = torch.linspace(-1, 1, w, device=x.device, dtype=x.dtype)
        grid_y = ys[None, None, :, None].expand(b, 1, h, w)
        grid_x = xs[None, None, None, :].expand(b, 1, h, w)
        return torch.cat([x, grid_x, grid_y], dim=1)

    def forward(
        self,
        obs: torch.Tensor,           # (B, C, H, W)
        vehicle: torch.Tensor,       # (B,)
        speed_bucket: torch.Tensor,  # (B,)
        flags: torch.Tensor,         # (B, 4)
        scalars: torch.Tensor,       # (B, 3)
        vehicle2: torch.Tensor,      # (B,)
        has_vehicle2: torch.Tensor,  # (B,)
    ) -> torch.Tensor:
        # The defect this guards: obs and the regime tensors arrive as
        # separate arguments from separate call sites (a rollout buffer's obs
        # tensor, regimes_to_tensors()'s output), so nothing at the type level
        # stops them disagreeing in length. Without this check torch broadcasts
        # a size-1 regime batch across an N-sample obs batch instead of erroring
        # -- every sample gets conditioned on regime[0], and the run still
        # trains to a plausible loss curve with silently-wrong labels. See
        # _check_batch's docstring and trajectory.py's render_batch, the
        # precedent this matches.
        _check_batch(obs.shape[0], vehicle=vehicle, speed_bucket=speed_bucket,
                     flags=flags, scalars=scalars, vehicle2=vehicle2,
                     has_vehicle2=has_vehicle2)

        if self.coord_maps:
            obs = self._with_coords(obs)

        z = self.encoder(vehicle, speed_bucket, flags, scalars, vehicle2, has_vehicle2)
        params = self.film(z)

        h = self.stem(obs)
        for block, (gamma, beta) in zip(self.blocks, params):
            h = block(h, gamma, beta)
        return h


def regimes_to_tensors(
    regimes: list[Regime], device: torch.device | str = "cpu"
) -> dict[str, torch.Tensor]:
    """Batch a list of Regime records into the tensors the trunk expects.

    The empty-list case matters: a rollout buffer can legitimately flush zero
    transitions (e.g. every sample in a chunk got dropped by validity
    checks), and that must not be a crash. It used to be one: `torch.tensor([])`
    defaults to float32 with no elements given to infer a dtype from, and
    nn.Embedding requires int64 indices, so `vehicle`/`speed_bucket` on an
    empty batch raised inside the encoder instead of producing a usable
    empty result. Every dtype is now passed explicitly rather than inferred,
    which fixes the empty case and is also just correct for the non-empty one
    (the old code relied on Python int -> int64 type inference happening to
    match what nn.Embedding wants). The 2-D tensors (flags, scalars) need
    their trailing dimension stated explicitly too, since an empty list gives
    torch no columns to infer -- matching the precedent in trajectory.py's
    TrajectoryRaster.render_batch, which returns a correctly-shaped zero
    tensor for zero samples rather than special-casing the caller.
    """
    vehicle = torch.tensor(
        [int(r.vehicle) for r in regimes], dtype=torch.int64, device=device
    )
    speed_bucket = torch.tensor(
        [r.speed_bucket() for r in regimes], dtype=torch.int64, device=device
    )
    if regimes:
        flags = torch.tensor(
            [
                [float(r.upside_down), float(r.mini), float(r.dual), float(r.sideways)]
                for r in regimes
            ],
            dtype=torch.float32,
            device=device,
        )
        scalars = torch.tensor(
            [
                [
                    r.gravity - GRAVITY_NORMAL,
                    r.time_warp - 1.0,
                    r.player_speed - SPEED_MULTIPLIERS[r.speed_bucket()],
                ]
                for r in regimes
            ],
            dtype=torch.float32,
            device=device,
        )
    else:
        flags = torch.zeros((0, 4), dtype=torch.float32, device=device)
        scalars = torch.zeros((0, 3), dtype=torch.float32, device=device)

    # vehicle2's index is meaningless wherever has_vehicle2 is 0 (RegimeEncoder
    # gates it out), so CUBE (0) is used as an arbitrary valid embedding index
    # rather than a sentinel that would need special-casing -- any in-range
    # value works identically since it is multiplied by zero.
    vehicle2 = torch.tensor(
        [int(r.vehicle2) if r.vehicle2 is not None else 0 for r in regimes],
        dtype=torch.int64,
        device=device,
    )
    has_vehicle2 = torch.tensor(
        [1.0 if r.vehicle2 is not None else 0.0 for r in regimes],
        dtype=torch.float32,
        device=device,
    )
    return {
        "vehicle": vehicle,
        "speed_bucket": speed_bucket,
        "flags": flags,
        "scalars": scalars,
        "vehicle2": vehicle2,
        "has_vehicle2": has_vehicle2,
    }
