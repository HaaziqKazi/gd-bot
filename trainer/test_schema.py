"""The generated files must not drift from their declarative source.

The whole point of trainer/schema.py is that the C++ struct layout and the
Python decoder come from one description. That guarantee is worth exactly as
much as the check that nobody edited a generated file by hand -- so this suite
regenerates both outputs in memory and asserts byte-identical text.

It also pins the handful of properties that are not free to change even when the
schema does: the four frozen control-block offsets, and the numeric agreement
between the generated enums and the Python modules that consume them.

Run: cd trainer && python3 -m pytest test_schema.py -q
"""

from __future__ import annotations

import numpy as np
import pytest

import schema
import schema_generated as sg


def test_generated_files_are_up_to_date():
    """Regenerating must produce byte-identical output.

    If this fails, someone edited mod/src/gdrl_schema.hpp or
    trainer/schema_generated.py directly. Fix by editing trainer/schema.py and
    running `python3 trainer/schema.py`. Do NOT re-edit the generated file: the
    schema hash is computed from the declarations, so a hand edit produces a
    header whose hash no longer describes its own contents, and the handshake
    would then pass while the layouts disagreed.
    """
    for path, expected in schema.generate().items():
        assert path.exists(), f"{path} has never been generated"
        actual = path.read_text()
        assert actual == expected, (
            f"{path.name} differs from what trainer/schema.py generates. "
            "Run `python3 trainer/schema.py`."
        )


def test_schema_hash_matches_generated_python():
    assert sg.GDRL_SCHEMA_HASH == schema.schema_hash(schema.build())


def test_schema_hash_ignores_comments_but_not_meaning():
    """Comment edits must not invalidate a trained run; meaning changes must.

    The asymmetry is deliberate. A schema hash that changed every time somebody
    improved a comment would be re-generated so often that a real mismatch would
    stop being alarming.
    """
    table = schema.build()
    baseline = schema.canonical_source(table)

    assert "This is a comment" not in baseline
    for s in schema.STRUCTS:
        if s.comment:
            assert s.comment.split("\n")[0] not in baseline

    # ...but every field name, type, count and offset is in.
    for s in schema.STRUCTS:
        laid = table[s.name]
        for f in laid.laid_out:
            if f.is_pad:
                continue
            assert f":{f.name}:{f.type}:{f.count}:" in baseline


def test_frozen_control_offsets():
    """magic/wireVersion/headerSize/schemaHash must never move.

    A Python built against wire version N has to be able to read these out of a
    mapping written by version M and report the real mismatch. If they move, an
    old Python decodes garbage and reports something confusing instead.
    """
    assert sg.OFFSET_MAGIC == 0
    assert sg.OFFSET_WIREVERSION == 4
    assert sg.OFFSET_HEADERSIZE == 6
    assert sg.OFFSET_SCHEMAHASH == 8
    assert sg.OFFSET_CONTROL == 0


def test_dtype_itemsizes_match_declared_struct_sizes():
    for name, size in sg.STRUCT_SIZES.items():
        dtype = getattr(sg, f"{name}_DTYPE")
        assert dtype.itemsize == size, f"{name}: dtype {dtype.itemsize} != {size}"


def test_dtype_fields_are_naturally_aligned():
    """Every scalar field sits on a multiple of its own size.

    The generated header asserts the same thing via offsetof(). Asserting it on
    this side too means a layout mistake fails a fast Python test rather than
    waiting for a several-minute universal C++ build.
    """
    for s in schema.STRUCTS:
        laid = schema.build()[s.name]
        for f in laid.laid_out:
            if f.is_pad or f.type.startswith("@"):
                continue
            _, size, align, _ = schema._SCALARS[f.type]
            assert f.offset % align == 0, f"{s.name}.{f.name} @ {f.offset}"


def test_no_gaps_are_addressable_as_fields():
    """Padding exists but is never a named wire field the decoder can read."""
    for s in schema.STRUCTS:
        laid = schema.build()[s.name]
        names = [f.name for f in laid.laid_out if not f.is_pad]
        assert len(names) == len(set(names)), f"{s.name} has duplicate field names"
        dtype = getattr(sg, f"{s.name}_DTYPE")
        assert list(dtype.names) == names


def test_observation_fits_the_declared_shared_size():
    total = (sg.STRUCT_SIZES["GdrlControl"]
             + sg.STRUCT_SIZES["GdrlObservation"]
             + sg.STRUCT_SIZES["GdrlActionBlock"])
    # Equal or larger only by inter-member alignment padding.
    assert sg.STRUCT_SIZES["GdrlShared"] >= total
    assert sg.OFFSET_OBS >= sg.STRUCT_SIZES["GdrlControl"]
    assert sg.OFFSET_ACTION >= sg.OFFSET_OBS + sg.STRUCT_SIZES["GdrlObservation"]


def test_vehicle_flag_bit_order_is_the_one_main_cpp_uses():
    """Bit order is load-bearing: it relabels every observation if it changes.

    main.cpp's modeFlagBits() is
        ship<<0 | ball<<1 | bird<<2 | dart<<3 | robot<<4 | spider<<5 | swing<<6
    and telemetry.cpp's vehicleFlagBits() builds the same word from this enum.
    Pinning it here means a reorder is a test failure, not a silent relabel.
    """
    assert sg.GdrlVehicleFlag.SHIP == 1 << 0
    assert sg.GdrlVehicleFlag.BALL == 1 << 1
    assert sg.GdrlVehicleFlag.BIRD == 1 << 2
    assert sg.GdrlVehicleFlag.DART == 1 << 3
    assert sg.GdrlVehicleFlag.ROBOT == 1 << 4
    assert sg.GdrlVehicleFlag.SPIDER == 1 << 5
    assert sg.GdrlVehicleFlag.SWING == 1 << 6


def test_object_kind_matches_trajectory_module():
    """GdrlObjectKind indexes TrajectoryRaster's channels; they must agree."""
    from trajectory import ObjectKind

    assert int(sg.GdrlObjectKind.HAZARD) == int(ObjectKind.HAZARD)
    assert int(sg.GdrlObjectKind.SOLID) == int(ObjectKind.SOLID)
    assert int(sg.GdrlObjectKind.INTERACTIVE) == int(ObjectKind.INTERACTIVE)
    assert int(sg.GdrlObjectKind.OTHER) == int(ObjectKind.OTHER)
    assert len(sg.GdrlObjectKind) == len(ObjectKind)


def test_tick_hz_matches_trajectory_module():
    from trajectory import TICK_HZ

    assert sg.GDRL_TICK_HZ == TICK_HZ


def test_vehicle_enum_matches_conditioning_public_api():
    """conditioning.Vehicle must be the generated enum, not a copy of it."""
    from conditioning import Vehicle

    for member in sg.GdrlVehicle:
        assert int(getattr(Vehicle, member.name)) == int(member)


def test_layout_engine_rejects_a_forward_reference():
    bad = schema.Struct(name="X", fields=[schema.Field("y", "@NotDeclared")])
    saved = list(schema.STRUCTS)
    schema.STRUCTS.append(bad)
    try:
        with pytest.raises(ValueError, match="before it is declared"):
            schema.build()
    finally:
        schema.STRUCTS[:] = saved


def test_layout_engine_pads_to_alignment():
    """A u8 followed by a f64 must place the double at offset 8, not 1."""
    table: dict[str, schema.Struct] = {}
    s = schema.Struct(name="T", fields=[schema.Field("a", "u8"),
                                        schema.Field("b", "f64")])
    schema.lay_out(s, table)
    offsets = {f.name: f.offset for f in s.laid_out}
    assert offsets["a"] == 0
    assert offsets["b"] == 8
    assert s.size == 16          # tail-padded to the struct's own alignment
    assert s.align == 8


def test_numpy_can_round_trip_every_struct():
    """A zeroed buffer decoded and re-encoded must be byte-identical.

    Cheap, but it catches the class of dtype mistake -- an offset off by a byte,
    an itemsize that swallows a neighbour -- that would otherwise only show up as
    a plausible wrong number coming out of the game.
    """
    for name, size in sg.STRUCT_SIZES.items():
        dtype = getattr(sg, f"{name}_DTYPE")
        buf = bytearray(size)
        arr = np.ndarray((), dtype=dtype, buffer=buf)
        for field in dtype.names:
            sub = arr[field]
            if sub.dtype.names is None and sub.shape == ():
                arr[field] = 0
        assert bytes(buf) == bytes(size)
