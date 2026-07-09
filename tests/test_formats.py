"""Magic-byte format detection."""

import netCDF4 as nc

from ncarnate.formats import FileFormat, detect_format

from conftest import HDFEOS2_FIXTURES, NETCDF_FIXTURES


def test_netcdf4_fixtures_detect_as_hdf5():
    for fixture in NETCDF_FIXTURES:
        assert detect_format(str(fixture)) is FileFormat.HDF5


def test_hdfeos2_fixtures_detect_as_hdf4():
    for fixture in HDFEOS2_FIXTURES:
        assert detect_format(str(fixture)) is FileFormat.HDF4


def test_netcdf3_classic_detects(workdir):
    path = workdir / "classic.nc"
    with nc.Dataset(path, "w", format="NETCDF3_CLASSIC") as f:
        f.createDimension("x", 3)
    assert detect_format(str(path)) is FileFormat.NETCDF3


def test_garbage_detects_as_unknown(workdir):
    path = workdir / "garbage.nc"
    path.write_bytes(b"not a science file at all" * 10)
    assert detect_format(str(path)) is FileFormat.UNKNOWN


def test_detection_ignores_extension(workdir):
    # Detection is content-based: an .nc name with HDF4 magic is HDF4.
    path = workdir / "misnamed.nc"
    path.write_bytes(HDFEOS2_FIXTURES[0].read_bytes())
    assert detect_format(str(path)) is FileFormat.HDF4
