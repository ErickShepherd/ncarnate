"""Shared test fixtures and the raw-value losslessness helper."""

from __future__ import annotations

import shutil
from pathlib import Path

import netCDF4 as nc
import numpy as np
import pytest

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "data"
NETCDF_FIXTURES = sorted((FIXTURE_ROOT / "netcdf").glob("*.nc"))
HDFEOS2_FIXTURES = sorted((FIXTURE_ROOT / "hdfeos2").glob("*.hdf"))

# Raw multi-MB granules live outside the repo; the tests marked
# raw_granules only run where they exist (never in CI).
GRANULE_DIR = Path.home() / "ncarnate-data" / "granules"


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    return tmp_path


def stage(fixture: Path, workdir: Path) -> Path:
    """Copies a committed fixture into the scratch dir and returns it."""
    staged = workdir / fixture.name
    shutil.copyfile(fixture, staged)
    return staged


def structmetadata_text(attributes: dict) -> str:
    """Concatenate StructMetadata.N parts ordered by numeric suffix (so .10
    follows .2), matching production (`ncarnate.hdf4._metadata_part_order`)
    and the generator. A lexicographic sort here would silently diverge for
    a >=11-part granule."""
    def order(name: str) -> int:
        suffix = name.rsplit(".", 1)[-1]
        return int(suffix) if suffix.isdigit() else 0

    parts = sorted(
        (n for n in attributes if n.startswith("StructMetadata")), key=order
    )
    return "".join(str(attributes[n]) for n in parts)


def assert_lossless_netcdf(src_path: Path, dst_path: Path) -> None:
    """Independent raw-read comparison: the recompressed netCDF file must
    match the source in dimensions, attributes (values AND dtypes),
    groups, variable dtypes/endianness, and bit-identical raw values."""
    with nc.Dataset(src_path) as src, nc.Dataset(dst_path) as dst:
        _assert_group_equal(src, dst, "/")


def _assert_group_equal(src, dst, path: str) -> None:
    assert set(src.dimensions) == set(dst.dimensions), path
    for name, dim in src.dimensions.items():
        other = dst.dimensions[name]
        assert dim.size == other.size, (path, name)
        assert dim.isunlimited() == other.isunlimited(), (path, name)

    _assert_attrs_equal(src, dst, path)

    assert set(src.variables) == set(dst.variables), path
    for name, var in src.variables.items():
        other = dst.variables[name]
        var.set_auto_maskandscale(False)
        other.set_auto_maskandscale(False)
        assert var.dtype == other.dtype, (path, name)
        assert var.dtype.str == other.dtype.str, (path, name, "endianness")
        assert var.dimensions == other.dimensions, (path, name)
        _assert_attrs_equal(var, other, f"{path}{name}")
        equal_nan = var.dtype.kind in "fc"
        assert np.array_equal(var[...], other[...], equal_nan=equal_nan), \
            (path, name, "values")

    assert set(src.groups) == set(dst.groups), path
    for name, group in src.groups.items():
        _assert_group_equal(group, dst.groups[name], f"{path}{name}/")


def _assert_attrs_equal(src, dst, path: str) -> None:
    assert set(src.ncattrs()) == set(dst.ncattrs()), path
    for name in src.ncattrs():
        a = np.asarray(src.getncattr(name))
        b = np.asarray(dst.getncattr(name))
        assert a.dtype == b.dtype, (path, name, "attr dtype")
        equal_nan = a.dtype.kind in "fc"
        assert np.array_equal(a, b, equal_nan=equal_nan), (path, name)
