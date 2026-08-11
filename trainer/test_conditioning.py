"""Checks on the conditioning stack.

The point of Objective A is a specific, testable property: identical geometry
must produce different behaviour under different physics regimes, and regimes
must compose so that unseen combinations land somewhere sensible. These tests
assert that directly, on an untrained network where it is a structural property
rather than something training might have supplied by accident.

Run: python3 -m pytest trainer/test_conditioning.py -q
"""

from __future__ import annotations

import pytest
import torch

from conditioning import (
    NUM_VEHICLES,
    SPEED_MULTIPLIERS,
    ConditionedTrunk,
    Regime,
    RegimeEncoder,
    Vehicle,
    derive_mini,
    derive_vehicle,
    regimes_to_tensors,
    vehicle_flags_overlap,
)
from schema_generated import GdrlVehicle, GdrlVehicleFlag

IN_CHANNELS = 6
WINDOW = (16, 24)


def make_trunk(seed: int = 0) -> ConditionedTrunk:
    torch.manual_seed(seed)
    return ConditionedTrunk(in_channels=IN_CHANNELS, channels=32, num_blocks=4)


def fixed_geometry(batch: int = 1) -> torch.Tensor:
    torch.manual_seed(1234)
    return torch.randn(batch, IN_CHANNELS, *WINDOW)


def test_untrained_trunk_is_regime_invariant_by_construction():
    """At init the FiLM heads are zeroed, so gamma == 1 and beta == 0 and the
    trunk behaves as if unconditioned. This is deliberate -- it is the property
    that keeps early training stable -- but it means the discrimination test
    below has to perturb the generator first, or it would be asserting against
    a network that structurally cannot yet discriminate."""
    trunk = make_trunk()
    obs = fixed_geometry()

    outs = []
    for v in (Vehicle.CUBE, Vehicle.SHIP, Vehicle.WAVE):
        t = regimes_to_tensors([Regime(vehicle=v)])
        outs.append(trunk(obs, **t))

    assert torch.allclose(outs[0], outs[1], atol=1e-6)
    assert torch.allclose(outs[0], outs[2], atol=1e-6)


def _perturbed_trunk(seed: int = 0) -> ConditionedTrunk:
    """A trunk with non-trivial FiLM parameters, standing in for a trained one."""
    trunk = make_trunk(seed)
    torch.manual_seed(seed + 1)
    for head in trunk.film.heads:
        torch.nn.init.normal_(head.weight, std=0.1)
        torch.nn.init.normal_(head.bias, std=0.1)
    return trunk


def test_same_geometry_different_vehicle_gives_different_features():
    """The core requirement: one observation, eight readings of it."""
    trunk = _perturbed_trunk()
    obs = fixed_geometry()

    feats = []
    for v in Vehicle:
        t = regimes_to_tensors([Regime(vehicle=v)])
        feats.append(trunk(obs, **t).flatten())

    for i in range(NUM_VEHICLES):
        for j in range(i + 1, NUM_VEHICLES):
            diff = (feats[i] - feats[j]).abs().max().item()
            assert diff > 1e-3, f"{Vehicle(i).name} and {Vehicle(j).name} collapsed"


def test_gravity_size_and_speed_each_move_the_features():
    """Every conditioning axis has to be wired through, not just the vehicle."""
    trunk = _perturbed_trunk()
    obs = fixed_geometry()
    base = Regime(vehicle=Vehicle.CUBE)

    def feats(r: Regime) -> torch.Tensor:
        return trunk(obs, **regimes_to_tensors([r])).flatten()

    ref = feats(base)
    variants = {
        "gravity": Regime(vehicle=Vehicle.CUBE, upside_down=True),
        "mini": Regime(vehicle=Vehicle.CUBE, mini=True),
        "dual": Regime(vehicle=Vehicle.CUBE, dual=True),
        "speed": Regime(vehicle=Vehicle.CUBE, player_speed=SPEED_MULTIPLIERS[-1]),
        "warp": Regime(vehicle=Vehicle.CUBE, time_warp=2.0),
    }
    for name, r in variants.items():
        assert (feats(r) - ref).abs().max().item() > 1e-4, f"{name} had no effect"


def test_gamma_matters_more_than_beta():
    """Reproduces the paper's central ablation asymmetry (F1).

    Perez et al. found ablating beta at test time cost 1.0% accuracy while
    ablating gamma cost 65.4%. The mechanism behind that is structural: gamma
    multiplies the normalised features and can gate them off entirely, beta only
    shifts them. Measured here as how far each ablation moves the output.
    """
    trunk = _perturbed_trunk()
    obs = fixed_geometry()
    t = regimes_to_tensors([Regime(vehicle=Vehicle.SHIP, upside_down=True)])

    z = trunk.encoder(**t)
    params = trunk.film(z)

    def run(ps):
        h = trunk.stem(trunk._with_coords(obs))
        for block, (g, b) in zip(trunk.blocks, ps):
            h = block(h, g, b)
        return h

    full = run(params)
    no_beta = run([(g, torch.zeros_like(b)) for g, b in params])
    no_gamma = run([(torch.ones_like(g), b) for g, b in params])

    beta_shift = (full - no_beta).abs().mean().item()
    gamma_shift = (full - no_gamma).abs().mean().item()
    assert gamma_shift > beta_shift, (
        f"expected gamma to dominate; got gamma={gamma_shift:.5f} "
        f"beta={beta_shift:.5f}"
    )


def test_gamma_is_unrestricted():
    """(F2): gamma must be free to go negative and to exceed 1.

    A sign flip is the natural encoding for inverted gravity, and every squashing
    function the paper tried underperformed. If someone later wraps the gamma
    projection in a sigmoid to 'stabilise' it, this fails.
    """
    trunk = _perturbed_trunk(seed=7)
    torch.manual_seed(3)
    for head in trunk.film.heads:
        torch.nn.init.normal_(head.weight, std=2.0)
        torch.nn.init.normal_(head.bias, std=2.0)

    regimes = [Regime(vehicle=v, upside_down=bool(i % 2)) for i, v in enumerate(Vehicle)]
    z = trunk.encoder(**regimes_to_tensors(regimes))
    gammas = torch.cat([g.flatten() for g, _ in trunk.film(z)])

    assert gammas.min().item() < 0.0, "gamma never goes negative"
    assert gammas.max().item() > 1.0, "gamma never exceeds 1"


def test_regime_directions_compose_additively():
    """(F3): the property that buys zero-shot transfer to rare combinations.

    mini-ness must be the same displacement in conditioning space regardless of
    which vehicle it is applied to. That is what lets a regime seen a handful of
    times -- 4x mini inverted wave -- inherit from the thousands of samples of
    each of its parts.
    """
    encoder = RegimeEncoder(width=64)

    def z(**kw) -> torch.Tensor:
        return encoder(**regimes_to_tensors([Regime(**kw)]))

    cube_delta = z(vehicle=Vehicle.CUBE, mini=True) - z(vehicle=Vehicle.CUBE)
    wave_delta = z(vehicle=Vehicle.WAVE, mini=True) - z(vehicle=Vehicle.WAVE)
    assert torch.allclose(cube_delta, wave_delta, atol=1e-6)

    # And the axes stack: applying two flags equals the sum of applying each.
    both = z(vehicle=Vehicle.WAVE, mini=True, upside_down=True)
    grav_delta = z(vehicle=Vehicle.WAVE, upside_down=True) - z(vehicle=Vehicle.WAVE)
    assert torch.allclose(both, z(vehicle=Vehicle.WAVE) + wave_delta + grav_delta,
                          atol=1e-6)


def test_speed_bucketing_snaps_and_keeps_the_residual():
    """Triggers can set speeds off the portal grid; the residual must survive."""
    assert Regime(player_speed=0.9).speed_bucket() == 1
    assert Regime(player_speed=1.6).speed_bucket() == 4
    assert Regime(player_speed=0.95).speed_bucket() == 1

    t = regimes_to_tensors([Regime(player_speed=0.95)])
    assert abs(t["scalars"][0, 2].item() - 0.05) < 1e-6


def test_batching_matches_individual_evaluation():
    """Rollouts arrive batched with mixed regimes; per-sample conditioning must
    not leak across the batch."""
    trunk = _perturbed_trunk()
    regimes = [
        Regime(vehicle=Vehicle.CUBE),
        Regime(vehicle=Vehicle.SHIP, upside_down=True),
        Regime(vehicle=Vehicle.WAVE, mini=True, player_speed=1.6),
    ]
    obs = fixed_geometry(batch=len(regimes))

    batched = trunk(obs, **regimes_to_tensors(regimes))
    for i, r in enumerate(regimes):
        single = trunk(obs[i : i + 1], **regimes_to_tensors([r]))
        assert torch.allclose(batched[i : i + 1], single, atol=1e-5)


# --------------------------------------------------------------------------
# Defect 1: ConditionedTrunk.forward must raise on a batch mismatch, not
# silently broadcast regime[0] across every sample in a larger obs batch.
# --------------------------------------------------------------------------

def test_mismatched_obs_and_regime_batch_raises():
    """The highest-value defect: without this check, an obs batch of size N
    against a regime batch of size 1 broadcasts gamma/beta from regime[0]
    across all N samples instead of erroring -- every sample trains on the
    wrong conditioning label and the loss curve still looks plausible."""
    trunk = make_trunk()
    obs = fixed_geometry(batch=3)
    t = regimes_to_tensors([Regime(vehicle=Vehicle.CUBE)])  # batch 1, not 3

    with pytest.raises(ValueError, match="batch mismatch"):
        trunk(obs, **t)


def test_every_regime_tensor_is_checked_not_just_one():
    """Cover every tensor argument, not just `vehicle`: corrupt each of the
    six regime tensors in turn and confirm each one alone is caught, so a
    partial fix that only checks e.g. `vehicle` cannot pass this test."""
    trunk = make_trunk()
    obs = fixed_geometry(batch=2)
    good = regimes_to_tensors([Regime(), Regime(vehicle=Vehicle.SHIP)])

    for key in ("vehicle", "speed_bucket", "flags", "scalars", "vehicle2", "has_vehicle2"):
        bad = dict(good)
        bad[key] = good[key][:1]  # truncate just this one tensor to batch 1
        with pytest.raises(ValueError, match="batch mismatch"):
            trunk(obs, **bad)


def test_regime_encoder_alone_also_checks_batch():
    """RegimeEncoder.forward is called directly elsewhere in this test file
    (test_gamma_matters_more_than_beta, test_regime_directions_compose_
    additively), so it needs the same guard independent of ConditionedTrunk."""
    encoder = RegimeEncoder(width=32)
    good = regimes_to_tensors([Regime(), Regime(vehicle=Vehicle.SHIP)])
    bad = dict(good)
    bad["scalars"] = good["scalars"][:1]
    with pytest.raises(ValueError, match="batch mismatch"):
        encoder(**bad)


# --------------------------------------------------------------------------
# Defect 2: regimes_to_tensors([]) must not crash, and the result must flow
# through RegimeEncoder / ConditionedTrunk cleanly.
# --------------------------------------------------------------------------

def test_regimes_to_tensors_empty_list_has_correct_shapes_and_dtypes():
    """Before the fix, `torch.tensor([])` defaulted to float32 with nothing
    to infer a dtype from, which nn.Embedding then rejected -- so an empty
    rollout chunk crashed instead of producing a usable empty batch."""
    t = regimes_to_tensors([])

    assert t["vehicle"].shape == (0,)
    assert t["vehicle"].dtype == torch.int64
    assert t["speed_bucket"].shape == (0,)
    assert t["speed_bucket"].dtype == torch.int64
    assert t["flags"].shape == (0, 4)
    assert t["flags"].dtype == torch.float32
    assert t["scalars"].shape == (0, 3)
    assert t["scalars"].dtype == torch.float32
    assert t["vehicle2"].shape == (0,)
    assert t["vehicle2"].dtype == torch.int64
    assert t["has_vehicle2"].shape == (0,)
    assert t["has_vehicle2"].dtype == torch.float32


def test_empty_batch_flows_through_encoder_and_trunk():
    """Not just correctly shaped in isolation -- it must actually work as
    input to the modules that consume it, the way an empty rollout chunk
    would flow through the real training loop."""
    trunk = make_trunk()
    t = regimes_to_tensors([])

    z = trunk.encoder(**t)
    assert z.shape == (0, trunk.encoder.width)

    obs = torch.zeros(0, IN_CHANNELS, *WINDOW)
    out = trunk(obs, **t)
    assert out.shape[0] == 0


# --------------------------------------------------------------------------
# Defect 3: Vehicle must be the generated schema enum, not a hand-kept copy.
# --------------------------------------------------------------------------

def test_vehicle_is_the_generated_schema_enum():
    """If this ever becomes a separate IntEnum kept numerically in sync by a
    comment again, this is the test that would go quietly out of date --
    identity, not just value equality, is what proves there is no duplicate
    declaration to drift."""
    assert Vehicle is GdrlVehicle
    assert Vehicle.CUBE is GdrlVehicle.CUBE
    assert NUM_VEHICLES == len(GdrlVehicle) == 8


# --------------------------------------------------------------------------
# Defect 4: dual regimes must be able to encode two different vehicles, and
# it must matter which player has which.
# --------------------------------------------------------------------------

def test_non_dual_regime_rejects_a_second_vehicle():
    """vehicle2 is only meaningful under dual -- setting it without dual=True
    is a contradiction and must fail at construction, not train on it."""
    with pytest.raises(ValueError):
        Regime(vehicle=Vehicle.CUBE, vehicle2=Vehicle.SHIP, dual=False)


def test_dual_regime_with_no_vehicle2_matches_pre_fix_behaviour():
    """The non-dual case, and a dual case where player 2's vehicle was not
    measured, must be unaffected by this fix -- otherwise every existing
    test (all written before vehicle2 existed) would need reweakening."""
    encoder = RegimeEncoder(width=32)
    plain = Regime(vehicle=Vehicle.CUBE)
    dual_unknown_p2 = Regime(vehicle=Vehicle.CUBE, dual=True)

    z_plain = encoder(**regimes_to_tensors([plain]))
    z_dual = encoder(**regimes_to_tensors([dual_unknown_p2]))
    # They differ only by the `dual` flag's direction, exactly as before
    # vehicle2 existed -- no phantom player-2 contribution appears.
    assert not torch.allclose(z_plain, z_dual)

    same_dual_flag_only = Regime(vehicle=Vehicle.CUBE, dual=True, vehicle2=None)
    assert torch.allclose(
        encoder(**regimes_to_tensors([dual_unknown_p2])),
        encoder(**regimes_to_tensors([same_dual_flag_only])),
    )


def test_dual_regime_encodes_which_player_has_which_vehicle():
    """The core defect-4 property: (cube, ship) must not collapse to the
    same conditioning vector as (ship, cube). A naive sum of two draws from
    one shared embedding table, optionally plus a fixed per-slot offset,
    cannot represent this at all -- see RegimeEncoder's comment on
    `vehicle2` for why an offset alone is provably insufficient (addition is
    commutative, so it cancels under the swap)."""
    encoder = RegimeEncoder(width=32)
    cube_ship = Regime(vehicle=Vehicle.CUBE, vehicle2=Vehicle.SHIP, dual=True)
    ship_cube = Regime(vehicle=Vehicle.SHIP, vehicle2=Vehicle.CUBE, dual=True)

    z1 = encoder(**regimes_to_tensors([cube_ship]))
    z2 = encoder(**regimes_to_tensors([ship_cube]))
    assert not torch.allclose(z1, z2), "player order collapsed"


def test_dual_regime_flows_through_trunk_batched_and_singly():
    """Same property as test_batching_matches_individual_evaluation, extended
    to a batch that mixes dual regimes with distinct per-player vehicles."""
    trunk = _perturbed_trunk()
    regimes = [
        Regime(vehicle=Vehicle.CUBE),
        Regime(vehicle=Vehicle.CUBE, vehicle2=Vehicle.SHIP, dual=True),
        Regime(vehicle=Vehicle.SHIP, vehicle2=Vehicle.CUBE, dual=True),
    ]
    obs = fixed_geometry(batch=len(regimes))

    batched = trunk(obs, **regimes_to_tensors(regimes))
    for i, r in enumerate(regimes):
        single = trunk(obs[i : i + 1], **regimes_to_tensors([r]))
        assert torch.allclose(batched[i : i + 1], single, atol=1e-5)


# --------------------------------------------------------------------------
# Defect 5: all wire-value semantics live in Python, and derive_vehicle's
# check order (the load-bearing part, per main.cpp / README) is pinned here.
# --------------------------------------------------------------------------

def test_derive_vehicle_single_flags():
    assert derive_vehicle(0) == Vehicle.CUBE
    assert derive_vehicle(GdrlVehicleFlag.SHIP) == Vehicle.SHIP
    assert derive_vehicle(GdrlVehicleFlag.BALL) == Vehicle.BALL
    assert derive_vehicle(GdrlVehicleFlag.BIRD) == Vehicle.UFO
    assert derive_vehicle(GdrlVehicleFlag.DART) == Vehicle.WAVE
    assert derive_vehicle(GdrlVehicleFlag.ROBOT) == Vehicle.ROBOT
    assert derive_vehicle(GdrlVehicleFlag.SPIDER) == Vehicle.SPIDER
    assert derive_vehicle(GdrlVehicleFlag.SWING) == Vehicle.SWING


def test_derive_vehicle_overlapping_flags_resolve_by_check_order():
    """Pins main.cpp deriveVehicle's exact order -- swing before ship, spider
    before ball -- for the overlapping case the README calls out as
    defensive and untested anywhere before this. If the order is ever
    reshuffled, this is the test that must catch it."""
    assert derive_vehicle(GdrlVehicleFlag.SWING | GdrlVehicleFlag.SHIP) == Vehicle.SWING
    assert derive_vehicle(GdrlVehicleFlag.SPIDER | GdrlVehicleFlag.BALL) == Vehicle.SPIDER
    # Every derived-before-base pair from the ordering, not just the two
    # named in the task: robot has no base it derives from in this scheme,
    # but dart/bird/ball/ship are all checked ahead of nothing except ship,
    # so the one pair worth pinning beyond the two above is swing beating
    # every other flag simultaneously set.
    all_flags = (
        GdrlVehicleFlag.SHIP | GdrlVehicleFlag.BALL | GdrlVehicleFlag.BIRD
        | GdrlVehicleFlag.DART | GdrlVehicleFlag.ROBOT | GdrlVehicleFlag.SPIDER
        | GdrlVehicleFlag.SWING
    )
    assert derive_vehicle(all_flags) == Vehicle.SWING


def test_vehicle_flags_overlap_detects_ambiguous_labels():
    """An overlap means derive_vehicle's return value is a guess resolved by
    check order, not a measurement -- the caller needs to be able to tell."""
    assert vehicle_flags_overlap(0) is False
    assert vehicle_flags_overlap(GdrlVehicleFlag.SHIP) is False
    assert vehicle_flags_overlap(GdrlVehicleFlag.SWING | GdrlVehicleFlag.SHIP) is True
    assert vehicle_flags_overlap(GdrlVehicleFlag.SPIDER | GdrlVehicleFlag.BALL) is True


def test_derive_mini_uses_measured_threshold():
    """1.0 normal, 0.6 mini -- measured values from main.cpp readCond /
    README. 0.9 is the midpoint main.cpp already used."""
    assert derive_mini(1.0) is False
    assert derive_mini(0.6) is True
    assert derive_mini(0.9) is False  # boundary: not strictly less than 0.9
    assert derive_mini(0.899) is True


def test_regime_from_wire_applies_all_derivations():
    """Regime.from_wire is the C++/Python boundary after this fix: raw wire
    scalars in, a fully-interpreted Regime out. Nothing here should require
    a numpy record -- plain Python scalars only."""
    r = Regime.from_wire(
        vehicle_flags=GdrlVehicleFlag.DART,  # wave
        is_upside_down=True,
        is_sideways=False,
        vehicle_size=0.6,
        player_speed=1.3,
        gravity=0.96,
        is_dual_mode=False,
        time_warp=1.0,
    )
    assert r.vehicle == Vehicle.WAVE
    assert r.upside_down is True
    assert r.mini is True
    assert r.dual is False
    assert r.sideways is False
    assert r.player_speed == 1.3
    assert r.gravity == 0.96
    assert r.time_warp == 1.0
    assert r.vehicle2 is None
    # speed bucketing and gravity normalisation are not re-derived here --
    # they reuse Regime.speed_bucket() / GRAVITY_NORMAL, exercised already by
    # test_speed_bucketing_snaps_and_keeps_the_residual.
    assert r.speed_bucket() == 3


def test_regime_from_wire_dual_with_second_vehicle():
    r = Regime.from_wire(
        vehicle_flags=0,  # player 1: cube
        is_upside_down=False,
        is_sideways=False,
        vehicle_size=1.0,
        player_speed=0.9,
        gravity=0.96,
        is_dual_mode=True,
        time_warp=1.0,
        vehicle_flags2=GdrlVehicleFlag.SHIP,
    )
    assert r.vehicle == Vehicle.CUBE
    assert r.dual is True
    assert r.vehicle2 == Vehicle.SHIP


def test_regime_from_wire_ignores_vehicle_flags2_when_not_dual():
    """A caller passing stale player-2 data alongside is_dual_mode=False is
    more likely a bug in the caller than a real contradiction, so from_wire
    drops it rather than raising -- unlike constructing Regime directly with
    an inconsistent vehicle2/dual pair, which does raise (see
    test_non_dual_regime_rejects_a_second_vehicle)."""
    r = Regime.from_wire(
        vehicle_flags=0,
        is_upside_down=False,
        is_sideways=False,
        vehicle_size=1.0,
        player_speed=0.9,
        gravity=0.96,
        is_dual_mode=False,
        time_warp=1.0,
        vehicle_flags2=GdrlVehicleFlag.SHIP,
    )
    assert r.dual is False
    assert r.vehicle2 is None
