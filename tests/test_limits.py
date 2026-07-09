"""Systemic S1: attacker-declared allocation sizes are bounded before the
array materializes, across all three read paths (netCDF variable, HDF4
SDS, reconstructed grid mesh)."""

import pytest
from pyhdf.SD import SD, SDC

import ncarnate.limits
from ncarnate.eos import structmeta
from ncarnate import NcarnateError, recompress
from ncarnate.eos.grid import reconstruct
from ncarnate.limits import check_array_size

from conftest import HDFEOS2_FIXTURES, NETCDF_FIXTURES, stage


@pytest.fixture
def tiny_cap(monkeypatch):
    # Drop the ceiling so ordinary fixtures trip it — the mechanism, not
    # the 8 GiB default, is what these tests pin.
    monkeypatch.setattr(ncarnate.limits, "DEFAULT_MAX_ARRAY_BYTES", 1024)


def test_check_array_size_uses_python_bigints():
    # A shape that would overflow int64 must not overflow the check.
    with pytest.raises(NcarnateError, match="safety ceiling"):
        check_array_size((10 ** 6, 10 ** 6), 8, "bomb")


def test_check_array_size_passes_reasonable():
    check_array_size((896, 608), 8, "ok")  # ~4 MB, no raise


def test_netcdf_variable_read_is_bounded(tiny_cap, workdir):
    fixture = next(f for f in NETCDF_FIXTURES if f.stem == "packed_fill")
    src = stage(fixture, workdir)
    with pytest.raises(NcarnateError, match="safety ceiling"):
        recompress(str(src), overwrite=False)


def test_hdf4_sds_read_is_bounded(tiny_cap, workdir):
    fixture = next(f for f in HDFEOS2_FIXTURES if "raingrid" in f.stem)
    src = stage(fixture, workdir)
    with pytest.raises(NcarnateError, match="safety ceiling"):
        recompress(str(src))


def test_grid_mesh_is_bounded(tiny_cap):
    fixture = next(f for f in HDFEOS2_FIXTURES if "seaice" in f.stem)
    source = SD(str(fixture), SDC.READ)
    try:
        parts = sorted(
            n for n in source.attributes() if n.startswith("StructMetadata")
        )
        text = "".join(source.attributes()[n] for n in parts)
    finally:
        source.end()
    grid = structmeta.parse(text).grids[0]
    with pytest.raises(NcarnateError, match="safety ceiling"):
        reconstruct(grid)
