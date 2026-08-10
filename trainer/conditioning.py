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
reads them off PlayerObject and GJBaseGameLayer every frame. Field names and
semantics are pinned to GD 2.2081 bindings.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

import torch
import torch.nn as nn
import torch.nn.functional as F


class Vehicle(IntEnum):
    """Must stay numerically identical to `enum class Vehicle` in main.cpp."""

    CUBE = 0
    SHIP = 1
    BALL = 2
    UFO = 3
    WAVE = 4
    ROBOT = 5
    SPIDER = 6
    SWING = 7


NUM_VEHICLES = len(Vehicle)

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
    """

    vehicle: Vehicle = Vehicle.CUBE
    upside_down: bool = False
    mini: bool = False
    dual: bool = False
    sideways: bool = False
    player_speed: float = 0.9
    gravity: float = GRAVITY_NORMAL
    time_warp: float = 1.0

    def speed_bucket(self) -> int:
        """Nearest speed portal. Triggers can set values off the portal grid, so
        this snaps rather than looking up, and the residual is carried through
        the continuous channel below."""
        return min(
            range(NUM_SPEEDS),
            key=lambda i: abs(SPEED_MULTIPLIERS[i] - self.player_speed),
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
    ) -> torch.Tensor:
        z = self.vehicle(vehicle) + self.speed(speed_bucket)

        dirs = torch.stack(
            [self.gravity_dir, self.mini_dir, self.dual_dir, self.sideways_dir]
        )                                    # (4, width)
        z = z + flags @ dirs                 # (B, width)
        z = z + self.continuous(scalars)
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
    ) -> torch.Tensor:
        if self.coord_maps:
            obs = self._with_coords(obs)

        z = self.encoder(vehicle, speed_bucket, flags, scalars)
        params = self.film(z)

        h = self.stem(obs)
        for block, (gamma, beta) in zip(self.blocks, params):
            h = block(h, gamma, beta)
        return h


def regimes_to_tensors(
    regimes: list[Regime], device: torch.device | str = "cpu"
) -> dict[str, torch.Tensor]:
    """Batch a list of Regime records into the tensors the trunk expects."""
    vehicle = torch.tensor([int(r.vehicle) for r in regimes], device=device)
    speed_bucket = torch.tensor([r.speed_bucket() for r in regimes], device=device)
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
    return {
        "vehicle": vehicle,
        "speed_bucket": speed_bucket,
        "flags": flags,
        "scalars": scalars,
    }
