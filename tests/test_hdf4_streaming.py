"""HDF4 streaming path: ``read_hdf4(..., stream=True)`` holds lazy SDS
payloads instead of arrays; the writer and verifier slice every SDS from a
reopened, identity-checked source handle in blocks bounded by the array
budget (``ncarnate.streaming.slices``). Fidelity must equal the eager path
bit-for-bit over every committed HDF-EOS2 fixture, plus synthetic files
covering many SDS, one large SDS, CHAR8, rank-1/single-element, and empty
unlimited datasets. Corrupted output and a source edited between read and
write are refused. Handles are closed on exceptions."""

from __future__ import annotations

import dataclasses
import os

import netCDF4 as nc
import numpy as np
import pytest
from pyhdf.SD import SD, SDC

from ncarnate import hdf4, hdf4_stream
from ncarnate.errors import (
    AllocationTooLargeError,
    NcarnateError,
    VerificationError,
)
from ncarnate.hdf4_stream import SdsPayload, SourceSession
from ncarnate.streaming import array_budget

from conftest import HDFEOS2_FIXTURES, assert_lossless_netcdf, stage

ENCODING = {"zlib": True, "shuffle": True, "complevel": 4}

_NUMERIC_DFNT = [
    (SDC.INT8, np.int8), (SDC.UINT8, np.uint8),
    (SDC.INT16, np.int16), (SDC.UINT16, np.uint16),
    (SDC.INT32, np.int32), (SDC.UINT32, np.uint32),
    (SDC.FLOAT32, np.float32), (SDC.FLOAT64, np.float64),
]


# --- helpers ---------------------------------------------------------------

def _fixture(stem_fragment):
    return next(f for f in HDFEOS2_FIXTURES if stem_fragment in f.stem)


def _write_sds(source, name, dfnt, data, attributes=()):
    sds = source.create(name, dfnt, data.shape)
    try:
        sds[:] = data
        for attr_name, attr_type, value in attributes:
            sds.attr(attr_name).set(attr_type, value)
    finally:
        sds.endaccess()


def _make_hdf(path, writer):
    source = SD(str(path), SDC.WRITE | SDC.CREATE | SDC.TRUNC)
    try:
        writer(source)
    finally:
        source.end()
    return path


def pyhdf_values(path):
    """Every SDS as pyhdf itself reads it, keyed by HDF4 name (empty
    datasets as zero-size arrays of the declared dtype)."""
    source = SD(str(path), SDC.READ)
    values = {}
    try:
        count, _ = source.info()
        for index in range(count):
            sds = source.select(index)
            try:
                name, rank, shape, dfnt, _ = sds.info()
                shape = hdf4_stream.sds_shape(rank, shape)
                if 0 in shape:
                    values[name] = np.empty(shape, hdf4._DFNT_DTYPES[dfnt])
                else:
                    values[name] = np.asarray(sds.get())
            finally:
                sds.endaccess()
    finally:
        source.end()
    return values


def output_variables(dataset):
    stack = [dataset]
    while stack:
        node = stack.pop()
        yield from node.variables.items()
        stack.extend(node.groups.values())


def find_output_variable(dataset, hdf4_name):
    wanted = hdf4.sanitize_name(hdf4_name)
    for name, variable in output_variables(dataset):
        if name == wanted:
            variable.set_auto_maskandscale(False)
            variable.set_auto_chartostring(False)
            return variable
    raise AssertionError(f"{hdf4_name} not found in output")


def tree_variables(tree):
    stack = [tree]
    while stack:
        node = stack.pop()
        yield from node.variables
        stack.extend(node.groups.values())


def values_equal(a, b):
    a, b = np.asarray(a), np.asarray(b)
    if a.dtype != b.dtype or a.shape != b.shape:
        return False
    return bool(np.array_equal(a, b, equal_nan=a.dtype.kind in "fc"))


def convert_stream(src, dst, geolocation=False):
    tree = hdf4.read_hdf4(str(src), geolocation=geolocation, stream=True)
    hdf4.write_netcdf(tree, str(dst), **ENCODING)
    hdf4.verify_conversion(str(src), str(dst))
    return tree


def assert_output_matches_pyhdf(src, dst):
    expected = pyhdf_values(src)
    assert expected
    with nc.Dataset(dst) as output:
        for hdf4_name, values in expected.items():
            variable = find_output_variable(output, hdf4_name)
            assert variable.dtype == values.dtype, hdf4_name
            assert variable.shape == values.shape, hdf4_name
            if values.size:
                assert values_equal(variable[...], values), hdf4_name


@pytest.fixture
def block_spy(monkeypatch):
    """Records (SDS name, block bytes) for every block the streaming path
    reads; ``_each_block`` resolves ``read_block`` through the module, so
    the spy sees writer, verifier and materialize reads alike."""
    seen = []
    original = hdf4_stream.read_block

    def spy(dataset, payload, selection):
        block = original(dataset, payload, selection)
        seen.append((payload.hdf4_name, int(block.nbytes)))
        return block

    monkeypatch.setattr(hdf4_stream, "read_block", spy)
    return seen


# --- the tree holds payloads, never arrays ----------------------------------

def test_stream_tree_holds_payloads_not_arrays(workdir):
    src = stage(HDFEOS2_FIXTURES[0], workdir)
    tree = hdf4.read_hdf4(str(src), geolocation=False, stream=True)
    variables = list(tree_variables(tree))
    assert variables
    for variable in variables:
        assert isinstance(variable.values, SdsPayload), variable.name
        assert variable.values.source.path == str(src)
        assert tuple(variable.values.shape) == _dim_size(tree, variable)
    # The read itself leaves no handle open (Windows would refuse the
    # delete otherwise); the payloads only carry identity.
    os.remove(src)


def _dim_size(tree, variable):
    # Resolve the variable's dimension sizes through its owning group
    # (by identity: TreeVariable's dataclass equality is not meaningful).
    for group in _groups(tree):
        if any(member is variable for member in group.variables):
            return tuple(group.dimensions[d] for d in variable.dimensions)
    raise AssertionError("variable not in tree")


def _groups(tree):
    stack = [tree]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(node.groups.values())


def test_payload_refuses_implicit_materialization(workdir):
    src = stage(_fixture("raingrid"), workdir)
    tree = hdf4.read_hdf4(str(src), geolocation=False, stream=True)
    payload = next(tree_variables(tree)).values
    with pytest.raises(NcarnateError, match="cannot be converted to an array"):
        np.asarray(payload)
    with pytest.raises(NcarnateError):
        np.array(payload)
    # NumPy array_equal catches failed coercion and returns False.
    assert not np.array_equal(payload, np.zeros(payload.shape))


def test_eager_default_is_unchanged(workdir):
    src = stage(_fixture("raingrid"), workdir)
    tree = hdf4.read_hdf4(str(src), geolocation=False)
    for variable in tree_variables(tree):
        assert isinstance(variable.values, np.ndarray), variable.name


@pytest.mark.parametrize("rank,shape,expected", [
    (1, 7, (7,)), (1, [7], (7,)), (2, [3, 4], (3, 4)), (2, (0, 4), (0, 4)),
])
def test_sds_shape_normalizes_pyhdf_rank1_int(rank, shape, expected):
    assert hdf4_stream.sds_shape(rank, shape) == expected


# --- every fixture profile, bounded ------------------------------------------

@pytest.mark.parametrize("fixture", HDFEOS2_FIXTURES, ids=lambda p: p.stem)
def test_fixture_streams_bit_identical_under_tiny_budget(
    fixture, workdir, block_spy
):
    src = stage(fixture, workdir)
    dst = workdir / f"{fixture.stem}.nc"
    budget = 4096
    with array_budget(budget):
        convert_stream(src, dst, geolocation=False)
    assert_output_matches_pyhdf(src, dst)
    # Bounded: no block exceeds the budget's transfer share, and at least
    # one SDS genuinely needed more than one block.
    assert block_spy
    assert max(nbytes for _, nbytes in block_spy) <= budget // 8
    per_sds = {}
    for name, _ in block_spy:
        per_sds[name] = per_sds.get(name, 0) + 1
    assert max(per_sds.values()) > 1


@pytest.mark.parametrize("fixture", HDFEOS2_FIXTURES, ids=lambda p: p.stem)
def test_stream_equals_eager_with_geolocation(fixture, workdir):
    src = stage(fixture, workdir)
    eager_out = workdir / "eager.nc"
    stream_out = workdir / "stream.nc"

    eager_tree = hdf4.read_hdf4(str(src), geolocation=True)
    hdf4.write_netcdf(eager_tree, str(eager_out), **ENCODING)
    hdf4.verify_conversion(str(src), str(eager_out), stream=False)

    with array_budget(8192):
        stream_tree = convert_stream(src, stream_out, geolocation=True)

    # Reconstructed variables are ndarrays on both paths; SDS variables are
    # payloads only on the streaming path.
    sds_names = set(pyhdf_values(src))
    for variable in tree_variables(stream_tree):
        original = variable.attributes.get("hdf4_name", variable.name)
        if original in sds_names:
            assert isinstance(variable.values, SdsPayload), variable.name
        else:
            assert isinstance(variable.values, (np.ndarray, np.generic)), \
                variable.name

    assert_lossless_netcdf(eager_out, stream_out)


def test_scalar_grid_mapping_variable_streams(workdir):
    src = stage(_fixture("seaice"), workdir)
    dst = workdir / "out.nc"
    convert_stream(src, dst, geolocation=True)
    with nc.Dataset(dst) as output:
        mapping = output.groups["NpPolarGrid12km"]["polar_stereographic"]
        assert mapping.shape == ()
        assert int(mapping[()]) == 0


# --- synthetic shapes and types --------------------------------------------

def test_many_sds_stream_with_typed_attributes(workdir, block_spy):
    rng = np.random.default_rng(20261008)
    count = 160
    expected = {}

    def writer(source):
        for number in range(count):
            dfnt, dtype = _NUMERIC_DFNT[number % len(_NUMERIC_DFNT)]
            dtype = np.dtype(dtype)
            if dtype.kind in "iu":
                info = np.iinfo(dtype)
                data = rng.integers(info.min, info.max, size=(20, 10),
                                    endpoint=True, dtype=dtype)
                fill = int(data.flat[1])
            else:
                data = rng.standard_normal((20, 10)).astype(dtype)
                data[number % 20, number % 10] = np.nan
                fill = -999.0
            name = f"field {number:03d}/x" if number % 3 else f"field_{number:03d}"
            expected[name] = data
            _write_sds(source, name, dfnt, data, attributes=[
                ("_FillValue", dfnt, fill),
                ("units", SDC.CHAR8, "count"),
            ])

    src = _make_hdf(workdir / "many.hdf", writer)
    dst = workdir / "many.nc"
    with array_budget(1024):
        tree = convert_stream(src, dst)
    assert sum(1 for _ in tree_variables(tree)) == count
    with nc.Dataset(dst) as output:
        for name, data in expected.items():
            variable = find_output_variable(output, name)
            assert values_equal(variable[...], data), name
            assert variable.getncattr("units") == "count"
            assert variable.getncattr("_FillValue").dtype == data.dtype
            if hdf4.sanitize_name(name) != name:
                assert variable.getncattr("hdf4_name") == name
    assert max(nbytes for _, nbytes in block_spy) <= 1024 // 8
    assert len({name for name, _ in block_spy}) == count


def test_large_single_sds_is_block_bounded(workdir, block_spy):
    shape = (1024, 2048)                       # 8 MiB of float32
    data = np.arange(np.prod(shape), dtype=np.float32).reshape(shape)
    data[5, 7] = np.nan                        # NaN must compare equal
    data[-1, -1] = -0.0

    src = _make_hdf(workdir / "large.hdf",
                    lambda s: _write_sds(s, "big", SDC.FLOAT32, data))
    dst = workdir / "large.nc"
    budget = 256 * 1024
    with array_budget(budget):
        convert_stream(src, dst)

    with nc.Dataset(dst) as output:
        variable = find_output_variable(output, "big")
        assert values_equal(variable[...], data)

    # Writer and verifier each read the SDS; neither block exceeded the
    # transfer share and both needed many blocks.
    assert max(nbytes for _, nbytes in block_spy) <= budget // 8
    assert len(block_spy) >= 2 * (data.nbytes // (budget // 8))


def test_char8_sds_streams_as_S1(workdir):
    text = np.frombuffer(b"abcdef", dtype="S1").reshape(2, 3)
    line = np.frombuffer(b"hello", dtype="S1")

    def writer(source):
        _write_sds(source, "label", SDC.CHAR8, text)
        _write_sds(source, "line", SDC.CHAR8, line)
        _write_sds(source, "num", SDC.INT16,
                   np.arange(6, dtype=np.int16).reshape(2, 3))

    src = _make_hdf(workdir / "char.hdf", writer)
    dst = workdir / "char.nc"
    with array_budget(1024):
        convert_stream(src, dst)
    assert_output_matches_pyhdf(src, dst)
    with nc.Dataset(dst) as output:
        label = find_output_variable(output, "label")
        assert label.dtype == np.dtype("S1")
        assert label[...].tobytes() == b"abcdef"
        assert find_output_variable(output, "line")[...].tobytes() == b"hello"


def test_rank1_and_single_element_sds(workdir, block_spy):
    vector = np.linspace(-1.0, 1.0, 5000, dtype=np.float64)
    single = np.array([np.int32(-7)], dtype=np.int32)

    def writer(source):
        _write_sds(source, "vector", SDC.FLOAT64, vector)
        _write_sds(source, "single", SDC.INT32, single)

    src = _make_hdf(workdir / "rank1.hdf", writer)
    dst = workdir / "rank1.nc"
    with array_budget(1024):
        tree = convert_stream(src, dst)
    shapes = {v.name: v.values.shape for v in tree_variables(tree)}
    assert shapes == {"vector": (5000,), "single": (1,)}
    assert_output_matches_pyhdf(src, dst)
    assert sum(1 for name, _ in block_spy if name == "vector") > 2
    assert sum(1 for name, _ in block_spy if name == "single") == 2  # write + verify


def test_empty_unlimited_sds_round_trips(workdir, block_spy):
    def writer(source):
        sds = source.create("empty", SDC.INT16, (SDC.UNLIMITED, 3))
        sds.endaccess()
        _write_sds(source, "filled", SDC.INT16,
                   np.arange(12, dtype=np.int16).reshape(4, 3))

    src = _make_hdf(workdir / "empty.hdf", writer)
    dst = workdir / "empty.nc"
    tree = convert_stream(src, dst)
    empty = next(v for v in tree_variables(tree) if v.name == "empty")
    assert isinstance(empty.values, SdsPayload)
    assert empty.values.shape == (0, 3)
    with nc.Dataset(dst) as output:
        variable = find_output_variable(output, "empty")
        assert variable.shape == (0, 3)
        assert variable.dtype == np.int16
        unlimited = output.dimensions[variable.dimensions[0]]
        assert unlimited.isunlimited() and unlimited.size == 0
        assert values_equal(find_output_variable(output, "filled")[...],
                            np.arange(12, dtype=np.int16).reshape(4, 3))
    # Nothing was read for the empty SDS, on either pass.
    assert all(name != "empty" for name, _ in block_spy)


def test_empty_unlimited_sds_eager_path(workdir):
    src = _make_hdf(workdir / "empty.hdf", lambda s: s.create(
        "empty", SDC.FLOAT32, (SDC.UNLIMITED, 2)).endaccess())
    tree = hdf4.read_hdf4(str(src), geolocation=False)
    (variable,) = list(tree_variables(tree))
    assert isinstance(variable.values, np.ndarray)
    assert variable.values.shape == (0, 2)
    dst = workdir / "empty.nc"
    hdf4.write_netcdf(tree, str(dst), **ENCODING)
    hdf4.verify_conversion(str(src), str(dst), stream=False)


# --- verification catches corruption ----------------------------------------

def _corrupt_one_value(src, dst):
    """Flips one element of the first numeric SDS in the output to a value
    that is guaranteed to differ (next float for finite floats, low bit
    for integers), and returns the SDS name."""
    for name, values in pyhdf_values(src).items():
        if not values.size or values.dtype.kind not in "iuf":
            continue
        if values.dtype.kind == "f":
            candidates = np.argwhere(np.isfinite(values))
            if not len(candidates):
                continue
            index = tuple(int(i) for i in candidates[0])
            replacement = np.nextafter(
                values[index], np.array(np.inf, dtype=values.dtype)
            )
        else:
            index = tuple(0 for _ in values.shape)
            replacement = np.bitwise_xor(
                values[index], np.array(1, dtype=values.dtype)
            )
        assert replacement != values[index]
        with nc.Dataset(dst, "a") as output:
            variable = find_output_variable(output, name)
            variable[index] = np.asarray(replacement, dtype=values.dtype)
        return name
    raise AssertionError("no corruptible numeric SDS")


def test_verification_detects_corrupted_value(workdir):
    src = stage(_fixture("raingrid"), workdir)
    dst = workdir / "out.nc"
    convert_stream(src, dst)
    _corrupt_one_value(src, dst)
    with pytest.raises(VerificationError, match="values differ"):
        hdf4.verify_conversion(str(src), str(dst))
    with pytest.raises(VerificationError, match="values differ"):
        hdf4.verify_conversion(str(src), str(dst), stream=False)


def test_verification_detects_corrupted_last_block(workdir):
    # The corruption sits in the *last* block under a tiny budget, so the
    # verifier must compare every block, not just the first.
    data = np.arange(3000, dtype=np.int32).reshape(50, 60)
    src = _make_hdf(workdir / "blocks.hdf",
                    lambda s: _write_sds(s, "data", SDC.INT32, data))
    dst = workdir / "blocks.nc"
    with array_budget(1024):
        convert_stream(src, dst)
    with nc.Dataset(dst, "a") as output:
        variable = find_output_variable(output, "data")
        variable[-1, -1] = np.int32(-1)
    with array_budget(1024), pytest.raises(VerificationError, match="data"):
        hdf4.verify_conversion(str(src), str(dst))


def test_verification_detects_dropped_attribute(workdir):
    src = _make_hdf(workdir / "attributed.hdf", lambda s: _write_sds(
        s, "data", SDC.INT32, np.arange(10, dtype=np.int32),
        attributes=(("units", SDC.CHAR8, "metres"),)))
    dst = workdir / "out.nc"
    tree = convert_stream(src, dst)
    # Any attribute but the declared fill (netCDF forbids deleting it).
    variable, attr_name = next(
        (v, n) for v in tree_variables(tree) for n in v.attributes
        if n != "_FillValue"
    )
    with nc.Dataset(dst, "a") as output:
        find_output_variable(output, variable.attributes.get(
            "hdf4_name", variable.name)).delncattr(attr_name)
    with pytest.raises(VerificationError, match="was not preserved"):
        hdf4.verify_conversion(str(src), str(dst))


def test_nan_values_verify_equal_not_corrupt(workdir):
    data = np.full((8, 8), np.nan, dtype=np.float64)
    data[0, 0] = -0.0
    src = _make_hdf(workdir / "nan.hdf",
                    lambda s: _write_sds(s, "nan", SDC.FLOAT64, data))
    dst = workdir / "nan.nc"
    convert_stream(src, dst)   # verify_conversion inside must not raise
    with nc.Dataset(dst, "a") as output:
        variable = find_output_variable(output, "nan")
        variable[0, 0] = np.float64(0.0)   # -0.0 -> +0.0 is still equal
    hdf4.verify_conversion(str(src), str(dst))


# --- source identity ----------------------------------------------------------

def test_source_changed_between_read_and_write_is_refused(workdir):
    src = stage(_fixture("raingrid"), workdir)
    tree = hdf4.read_hdf4(str(src), geolocation=False, stream=True)
    with open(src, "ab") as handle:
        handle.write(b"\0")
    with pytest.raises(NcarnateError, match="changed since it was read"):
        hdf4.write_netcdf(tree, str(workdir / "out.nc"), **ENCODING)


def test_source_identity_check_reports_size_and_mtime(workdir):
    src = stage(_fixture("raingrid"), workdir)
    identity = hdf4_stream.SourceIdentity.capture(str(src))
    assert identity.differences() == []
    with open(src, "ab") as handle:
        handle.write(b"\0")
    differences = identity.differences()
    assert any(d.startswith("size") for d in differences)
    with pytest.raises(VerificationError, match="changed since it was read"):
        identity.check("ctx", error_type=VerificationError)
    os.remove(src)
    assert identity.differences() and "cannot stat" in identity.differences()[0]


def test_sds_identity_mismatch_is_refused(workdir):
    src = _make_hdf(workdir / "two.hdf", lambda s: (
        _write_sds(s, "a", SDC.INT16, np.ones((2, 2), np.int16)),
        _write_sds(s, "b", SDC.INT16, np.zeros((2, 2), np.int16)),
    ))
    tree = hdf4.read_hdf4(str(src), geolocation=False, stream=True)
    a, b = (v.values for v in tree_variables(tree))
    target = np.empty((2, 2), np.int16)
    for wrong in (
        dataclasses.replace(a, reference=a.reference + 1),
        dataclasses.replace(a, index=b.index),          # wrong dataset
        dataclasses.replace(a, shape=(2, 3)),
        dataclasses.replace(a, dfnt_code=SDC.INT32),
    ):
        with SourceSession() as session, \
             pytest.raises(NcarnateError, match="not the dataset"):
            hdf4_stream.copy_payload(wrong, session, target)
    with SourceSession() as session:
        hdf4_stream.copy_payload(a, session, target)   # the real one is fine
    assert (target == 1).all()


def test_session_serves_one_source(workdir):
    first = _make_hdf(workdir / "one.hdf", lambda s: _write_sds(
        s, "a", SDC.INT16, np.ones((2, 2), np.int16)))
    second = _make_hdf(workdir / "two.hdf", lambda s: _write_sds(
        s, "a", SDC.INT16, np.ones((2, 2), np.int16)))
    a = next(tree_variables(hdf4.read_hdf4(str(first), stream=True))).values
    b = next(tree_variables(hdf4.read_hdf4(str(second), stream=True))).values
    with SourceSession() as session:
        hdf4_stream.materialize(a, session, "a")
        with pytest.raises(NcarnateError, match="one HDF4 source"):
            hdf4_stream.materialize(b, session, "b")


# --- lifecycle --------------------------------------------------------------

@pytest.fixture
def session_spy(monkeypatch):
    instances = []

    class SpySession(SourceSession):
        def __init__(self):
            super().__init__()
            instances.append(self)

    monkeypatch.setattr(hdf4, "SourceSession", SpySession)
    return instances


def test_handles_closed_when_write_fails_mid_copy(workdir, session_spy,
                                                  monkeypatch):
    src = stage(_fixture("mod03"), workdir)
    tree = hdf4.read_hdf4(str(src), geolocation=False, stream=True)
    calls = []
    original = hdf4_stream.read_block

    def failing(dataset, payload, selection):
        calls.append(1)
        if len(calls) == 3:
            raise RuntimeError("simulated read failure")
        return original(dataset, payload, selection)

    monkeypatch.setattr(hdf4_stream, "read_block", failing)
    with array_budget(1024), pytest.raises(RuntimeError, match="simulated"):
        hdf4.write_netcdf(tree, str(workdir / "out.nc"), **ENCODING)
    assert session_spy and all(not s.is_open for s in session_spy)
    # A still-open HDF4 handle would make this fail on Windows.
    os.remove(src)
    os.remove(workdir / "out.nc")


def test_handles_closed_when_verification_fails(workdir, session_spy):
    src = stage(_fixture("raingrid"), workdir)
    dst = workdir / "out.nc"
    convert_stream(src, dst)
    _corrupt_one_value(src, dst)
    with pytest.raises(VerificationError):
        hdf4.verify_conversion(str(src), str(dst))
    assert session_spy and all(not s.is_open for s in session_spy)
    os.remove(src)
    os.remove(dst)


def test_session_closes_on_exception_inside_select(workdir):
    src = _make_hdf(workdir / "one.hdf", lambda s: _write_sds(
        s, "a", SDC.INT16, np.ones((2, 2), np.int16)))
    payload = next(tree_variables(hdf4.read_hdf4(str(src), stream=True))).values
    session = SourceSession()
    with pytest.raises(RuntimeError):
        with session:
            with session.select(payload):
                assert session.is_open
                raise RuntimeError("boom")
    assert not session.is_open
    os.remove(src)


# --- geolocation materialization is explicit and bounded --------------------

def test_coordinate_materialization_is_bounded(workdir, monkeypatch):
    # myd05 is the dimension-mapped swath: interpolation needs the coarse
    # Latitude/Longitude in memory, the one full read on the streaming
    # path, and it must be refused above the explicit ceiling.
    src = stage(_fixture("myd05"), workdir)
    monkeypatch.setattr(hdf4_stream, "MAX_COORDINATE_SOURCE_BYTES", 16)
    with pytest.raises(AllocationTooLargeError, match="safety ceiling"):
        hdf4.read_hdf4(str(src), geolocation=True, stream=True)
    # The payload-only read is unaffected by the geolocation ceiling.
    hdf4.read_hdf4(str(src), geolocation=False, stream=True)


def test_materialize_reads_in_blocks_and_matches(workdir, block_spy):
    src = stage(_fixture("mod03"), workdir)
    tree = hdf4.read_hdf4(str(src), geolocation=False, stream=True)
    latitude = next(v for v in tree_variables(tree) if v.name == "Latitude")
    with array_budget(1024), SourceSession() as session:
        values = hdf4_stream.materialize(latitude.values, session, "test")
    assert values_equal(values, pyhdf_values(src)["Latitude"])
    assert len(block_spy) > 1
    assert max(nbytes for _, nbytes in block_spy) <= 1024 // 8
    with pytest.raises(AllocationTooLargeError):
        with SourceSession() as session:
            hdf4_stream.materialize(latitude.values, session, "test",
                                    max_bytes=8)


def test_dimension_mapped_swath_streams_identically(workdir):
    src = stage(_fixture("myd05"), workdir)
    dst = workdir / "out.nc"
    with array_budget(2048):
        convert_stream(src, dst, geolocation=True)
    with nc.Dataset(dst) as output:
        group = output.groups["mod05"]
        assert group["Water_Vapor_Near_Infrared"].getncattr("coordinates") \
            == "Longitude_interpolated Latitude_interpolated"
        lat_1km = group["Latitude_interpolated"]
        lat_5km = group["Latitude"]
        lat_1km.set_auto_maskandscale(False)
        lat_5km.set_auto_maskandscale(False)
        assert float(lat_1km[2, 1347]) == pytest.approx(
            float(lat_5km[0, 269]), abs=5e-5
        )
