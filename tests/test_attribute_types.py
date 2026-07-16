"""Failing-test matrix for attribute *storage-type* fidelity
(IMPLEMENTATION_PLAN step 3 / priority-queue step 3.1+3.3 / readiness
action 3; KD-L6; gate G3).

The defect (production-readiness review finding 6, reproduced
2026-07-16): the attribute copy goes through ``getncattr`` /
``setncatts``, whose Python values erase the netCDF *storage type* — an
``NC_STRING`` scalar reads back as ``str`` and re-writes as ``NC_CHAR``
— and the verifier compares those same type-erased values, so the
degradation sails through "verified lossless". These tests pin the
KD-L6 contract: exact storage types are preserved at global, group, and
variable scope, and the verifier **fails** on any storage-type change.

Type inquiry: netCDF4-python has no public attribute-type API (that gap
IS the defect's origin), so the tests do their own netCDF-C type
inquiry (``nc_inq_atttype``) via ctypes against the library netCDF4
already links — the exact mechanism readiness action 3 names, and
deliberately independent of whatever inquiry the implementation adopts,
so the evidence never trusts the code under test.

These fail until the paired step-3 impl item lands (the pattern
test_convert_collisions.py / test_optional_hdf4.py established).
"""

import ctypes
import glob
import os

import netCDF4 as nc
import numpy as np
import pytest

from ncarnate import recompress
from ncarnate.core import _verify_lossless
from ncarnate.errors import VerificationError

# netCDF-C external type codes (netcdf.h).
NC_CHAR = 2
NC_STRING = 12

# netcdf.h: attributes of the file/group itself use varid NC_GLOBAL.
NC_GLOBAL = -1


def _libnetcdf():
    """The libnetcdf netCDF4-python links, loaded for ctypes inquiry.

    Two strategies, tried in order: dlopen the ``_netCDF4`` extension
    module itself (its dynamic-symbol table resolves the linked
    libnetcdf's exports — works for manylinux wheels, which vendor
    libnetcdf into ``netCDF4.libs``), then the wheel's bundled library
    directories directly (macOS ``.dylibs``). Failing BOTH is a loud
    test failure, never a skip — a silent skip would gut the storage-type
    pin on that platform.
    """
    candidates = [nc._netCDF4.__file__]
    pkgdir = os.path.dirname(nc.__file__)
    for pattern in ("../netCDF4.libs/libnetcdf*", ".dylibs/libnetcdf*"):
        candidates += sorted(glob.glob(os.path.join(pkgdir, pattern)))
    for candidate in candidates:
        try:
            lib = ctypes.CDLL(candidate)
            lib.nc_inq_atttype  # symbol probe
        except (OSError, AttributeError):
            continue
        return lib
    pytest.fail(
        "could not load a libnetcdf exposing nc_inq_atttype for type "
        f"inquiry (tried {candidates}); the storage-type assertions "
        "cannot run — this must not pass silently"
    )


def _att_type(path, name, *, group=None, variable=None):
    """The netCDF-C storage type code of one attribute, via nc_inq_atttype."""
    lib = _libnetcdf()
    ncid = ctypes.c_int()
    rc = lib.nc_open(str(path).encode(), 0, ctypes.byref(ncid))
    assert rc == 0, f"nc_open({path}) rc={rc}"
    try:
        gid = ncid
        if group is not None:
            gid = ctypes.c_int()
            rc = lib.nc_inq_grp_ncid(ncid, group.encode(), ctypes.byref(gid))
            assert rc == 0, f"nc_inq_grp_ncid({group}) rc={rc}"
        varid = NC_GLOBAL
        if variable is not None:
            vid = ctypes.c_int()
            rc = lib.nc_inq_varid(gid, variable.encode(), ctypes.byref(vid))
            assert rc == 0, f"nc_inq_varid({variable}) rc={rc}"
            varid = vid.value
        att_type = ctypes.c_int()
        rc = lib.nc_inq_atttype(
            gid, varid, name.encode(), ctypes.byref(att_type)
        )
        assert rc == 0, f"nc_inq_atttype({name}) rc={rc}"
        return att_type.value
    finally:
        lib.nc_close(ncid)


# The attribute matrix written at every scope (readiness action 3:
# scalar strings, string arrays, fixed character arrays, numeric
# scalars, numeric arrays — in both text storage types where the
# distinction exists).
def _write_matrix(obj):
    obj.setncattr_string("att_string_scalar", "vlen scalar")     # NC_STRING
    obj.setncattr("att_char_scalar", "fixed char text")          # NC_CHAR
    obj.setncattr_string("att_string_array", ["one", "two"])     # NC_STRING[]
    obj.setncattr("att_numeric_scalar", np.int32(7))
    obj.setncattr("att_numeric_array", np.arange(3.0))


MATRIX_NAMES = [
    "att_string_scalar",
    "att_char_scalar",
    "att_string_array",
    "att_numeric_scalar",
    "att_numeric_array",
]

# (id, group, variable) triples naming each scope's inquiry target.
SCOPES = [
    ("global", None, None),
    ("group", "g", None),
    ("variable", None, "v"),
]


def _build_source(path):
    ds = nc.Dataset(path, "w", format="NETCDF4")
    try:
        ds.createDimension("x", 3)
        var = ds.createVariable("v", "f8", ("x",))
        var[:] = [1.0, 2.0, 3.0]
        group = ds.createGroup("g")
        _write_matrix(ds)
        _write_matrix(group)
        _write_matrix(var)
    finally:
        ds.close()
    return path


@pytest.mark.parametrize(("scope", "group", "variable"),
                         SCOPES, ids=[s[0] for s in SCOPES])
@pytest.mark.parametrize("name", MATRIX_NAMES)
def test_recompress_preserves_attribute_storage_types(
    scope, group, variable, name, workdir
):
    # KD-L6: query + recreate the exact storage type at every scope. The
    # NC_STRING scalar cases fail today (degrade to NC_CHAR); the rest
    # pin the classes that already survive.
    src = _build_source(workdir / "src.nc")
    dst = workdir / "dst.nc"

    recompress(str(src), dst=str(dst))

    src_type = _att_type(src, name, group=group, variable=variable)
    dst_type = _att_type(dst, name, group=group, variable=variable)
    assert dst_type == src_type, (
        f"{scope} attribute {name!r} storage type changed "
        f"{src_type} -> {dst_type}"
    )


def test_scalar_nc_string_regression_shape(workdir):
    # The named reproduction (finding 6): conversion *completes* — the
    # value-level verify passes — which is exactly why the silent type
    # degradation is dangerous. Pin the storage type end-to-end.
    src = _build_source(workdir / "src.nc")
    dst = workdir / "dst.nc"

    recompress(str(src), dst=str(dst))          # must not raise

    assert _att_type(src, "att_string_scalar") == NC_STRING
    assert _att_type(dst, "att_string_scalar") == NC_STRING


def _build_single_attribute_file(path, *, as_string):
    ds = nc.Dataset(path, "w", format="NETCDF4")
    try:
        if as_string:
            ds.setncattr_string("att", "same text")   # NC_STRING
        else:
            ds.setncattr("att", "same text")          # NC_CHAR
    finally:
        ds.close()
    return path


@pytest.mark.parametrize(("src_is_string", "dst_is_string"),
                         [(True, False), (False, True)],
                         ids=["string_to_char", "char_to_string"])
def test_verifier_fails_on_storage_type_change(
    src_is_string, dst_is_string, workdir
):
    # Readiness action 3 done-when: "the verifier fails on any
    # storage-type change." Two files identical except the attribute's
    # storage type must NOT verify as lossless — today they do, because
    # the comparison is value-only (core._verify_attributes).
    src = _build_single_attribute_file(
        workdir / "src.nc", as_string=src_is_string
    )
    dst = _build_single_attribute_file(
        workdir / "dst.nc", as_string=dst_is_string
    )

    with pytest.raises(VerificationError):
        _verify_lossless(str(src), str(dst))
