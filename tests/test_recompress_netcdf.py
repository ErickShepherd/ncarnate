"""The netCDF->netCDF round-trip oracle: losslessness, safe overwrite,
compression effectiveness, and the fail-loud guarantee boundary."""

import numpy as np
import netCDF4 as nc
import pytest

import ncarnate.core
from ncarnate import (
    NcarnateError,
    UnsupportedTypeError,
    VerificationError,
    recompress,
)

from conftest import NETCDF_FIXTURES, assert_lossless_netcdf, stage


@pytest.mark.parametrize(
    "fixture", NETCDF_FIXTURES, ids=lambda p: p.stem
)
def test_round_trip_is_lossless(fixture, workdir):
    src = stage(fixture, workdir)
    dst = recompress(str(src), overwrite=False, complevel=9)
    assert dst == str(workdir / f"{fixture.stem}_recompressed.nc")
    assert_lossless_netcdf(src, dst)


def test_in_place_overwrite(workdir):
    fixture = next(f for f in NETCDF_FIXTURES if f.stem == "packed_fill")
    pristine = stage(fixture, workdir)
    target = workdir / "inplace.nc"
    target.write_bytes(pristine.read_bytes())
    result = recompress(str(target), complevel=9)
    assert result == str(target)
    assert_lossless_netcdf(pristine, target)


def test_compression_is_applied(workdir):
    fixture = next(f for f in NETCDF_FIXTURES if f.stem == "packed_fill")
    src = stage(fixture, workdir)
    dst = recompress(str(src), overwrite=False, complevel=9)
    with nc.Dataset(dst) as f:
        filters = next(iter(f.variables.values())).filters()
    assert filters["zlib"] and filters["shuffle"]
    assert filters["complevel"] == 9


def test_compressible_file_gets_smaller(workdir):
    # The committed fixtures are too small for compression to beat the
    # chunked-storage overhead; a compressible MB-scale file must shrink.
    src = workdir / "compressible.nc"
    with nc.Dataset(src, "w") as f:
        f.createDimension("y", 512)
        f.createDimension("x", 512)
        var = f.createVariable("field", "f8", ("y", "x"))
        var[:] = np.add.outer(np.arange(512.0), np.arange(512.0))
    original_size = src.stat().st_size
    dst = recompress(str(src), overwrite=False, complevel=9)
    assert (workdir / "compressible_recompressed.nc").stat().st_size \
        < original_size
    assert_lossless_netcdf(src, dst)


def test_failed_verification_never_touches_source(workdir, monkeypatch):
    fixture = next(f for f in NETCDF_FIXTURES if f.stem == "packed_fill")
    src = stage(fixture, workdir)
    before = src.read_bytes()

    def sabotage(src_path, tmp_path):
        raise VerificationError("Verification failed: sabotaged")

    monkeypatch.setattr(ncarnate.core, "_verify_lossless", sabotage)
    with pytest.raises(VerificationError):
        recompress(str(src))
    assert src.read_bytes() == before
    assert list(workdir.glob("*.tmp")) == []


def test_cleanup_error_does_not_mask_original(workdir, monkeypatch):
    import os
    fixture = next(f for f in NETCDF_FIXTURES if f.stem == "packed_fill")
    src = stage(fixture, workdir)

    def sabotage(src_path, tmp_path):
        raise VerificationError("Verification failed: sabotaged")

    monkeypatch.setattr(ncarnate.core, "_verify_lossless", sabotage)
    monkeypatch.setattr(os, "unlink", lambda p: (_ for _ in ()).throw(
        OSError("cleanup boom")))
    # The real failure (VerificationError), not the cleanup OSError, must
    # propagate.
    with pytest.raises(VerificationError):
        recompress(str(src))


def test_user_defined_types_fail_loud(workdir):
    src = workdir / "vlen.nc"
    with nc.Dataset(src, "w") as f:
        f.createDimension("x", 2)
        var = f.createVariable("names", str, ("x",))
        var[0] = "a"
    with pytest.raises(UnsupportedTypeError):
        recompress(str(src), overwrite=False)


def test_dst_equal_to_src_is_rejected(workdir):
    src = stage(NETCDF_FIXTURES[0], workdir)
    with pytest.raises(NcarnateError):
        recompress(str(src), dst=str(src))


def test_missing_source_is_rejected(workdir):
    with pytest.raises(NcarnateError):
        recompress(str(workdir / "missing.nc"))


def test_in_place_through_symlink_replaces_real_file(workdir):
    import os
    fixture = next(f for f in NETCDF_FIXTURES if f.stem == "packed_fill")
    real = stage(fixture, workdir)
    real_target = workdir / "real.nc"
    real.rename(real_target)
    link = workdir / "link.nc"
    os.symlink(real_target, link)
    recompress(str(link), complevel=9)
    # The symlink must still be a symlink pointing at the real file, and
    # the real file (not a detached copy) must hold the recompressed data.
    assert link.is_symlink()
    assert os.path.realpath(link) == str(real_target)
    with nc.Dataset(real_target) as f:
        v = next(iter(f.variables.values()))
        assert v.filters()["complevel"] == 9
