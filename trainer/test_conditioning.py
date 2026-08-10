"""Checks on the conditioning stack.

The point of Objective A is a specific, testable property: identical geometry
must produce different behaviour under different physics regimes, and regimes
must compose so that unseen combinations land somewhere sensible. These tests
assert that directly, on an untrained network where it is a structural property
rather than something training might have supplied by accident.

Run: python3 -m pytest trainer/test_conditioning.py -q
"""

from __future__ import annotations

import torch

from conditioning import (
    NUM_VEHICLES,
    SPEED_MULTIPLIERS,
    ConditionedTrunk,
    Regime,
    RegimeEncoder,
    Vehicle,
    regimes_to_tensors,
)

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
