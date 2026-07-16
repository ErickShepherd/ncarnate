#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

netCDF attribute *storage-type* inquiry (readiness action 3, KD-L6):
netCDF4-python has no public attribute-type API — ``getncattr`` returns
``str`` for both an ``NC_STRING`` scalar and an ``NC_CHAR`` attribute,
which is exactly the type erasure behind the scalar
``NC_STRING → NC_CHAR`` fidelity defect. This module does the netCDF-C
type inquiry (``nc_inq_atttype``) the readiness action names, via
ctypes against the libnetcdf that netCDF4-python itself links — no new
dependency, and the answer comes from the same library that wrote the
file.

Failure to bind libnetcdf raises (fail loud): proceeding without
inquiry would silently reintroduce the type degradation the fidelity
contract now excludes — a wrong copy is worse than a refused one.

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

from __future__ import annotations

# Standard library imports.
import ctypes
import ctypes.util
import glob
import os

# Third party imports.
import netCDF4 as nc

# Local application imports.
from ncarnate.errors import NcarnateError

__all__ = [
    "AttTypeInquiryError",
    "NC_CHAR",
    "NC_STRING",
    "string_attribute_names",
    "string_attributes_of",
]

# netCDF-C external type codes (netcdf.h).
NC_CHAR   = 2
NC_STRING = 12

# netcdf.h: attributes of the file/group itself use varid NC_GLOBAL.
_NC_GLOBAL   = -1
_NC_MAX_NAME = 256


class AttTypeInquiryError(NcarnateError):

    '''

    Raised when libnetcdf cannot be bound for attribute-type inquiry, or
    an inquiry call fails. Deliberately loud: without the inquiry the
    attribute copy/verify cannot honor the storage-type fidelity
    contract, and silently degrading types is the defect this module
    exists to prevent.

    '''


_LIB = None


def _libnetcdf():

    '''

    The libnetcdf shared library netCDF4-python links, bound for ctypes.

    Strategies, in order: dlopen the ``_netCDF4`` extension module itself
    (its dependency chain resolves libnetcdf's exports — manylinux and
    macOS wheels), the wheel's vendored library directories
    (``netCDF4.libs`` on Linux/Windows, ``.dylibs`` on macOS), then the
    system loader path. The handle is process-stable, so it is cached.

    '''

    global _LIB

    if _LIB is not None:

        return _LIB

    candidates = [nc._netCDF4.__file__]

    package_dir = os.path.dirname(nc.__file__)

    for pattern in (
        os.path.join("..", "netCDF4.libs", "*netcdf*"),
        os.path.join(".dylibs", "*netcdf*"),
    ):

        candidates += sorted(glob.glob(os.path.join(package_dir, pattern)))

    found = ctypes.util.find_library("netcdf")

    if found:

        candidates.append(found)

    for candidate in candidates:

        try:

            library = ctypes.CDLL(candidate)
            library.nc_inq_atttype  # symbol probe

        except (OSError, AttributeError):

            continue

        _LIB = library

        return library

    raise AttTypeInquiryError(
        "cannot bind libnetcdf for attribute storage-type inquiry "
        f"(tried {candidates}); refusing rather than silently degrading "
        "attribute types — please report this platform"
    )


def _check(return_code : int, call : str) -> None:

    if return_code != 0:

        raise AttTypeInquiryError(
            f"netCDF-C {call} failed with status {return_code}"
        )


def string_attribute_names(
    path : str, group_path : str = "/", variable : "str | None" = None
) -> frozenset:

    '''

    The names of the attributes stored as ``NC_STRING`` at one scope of
    the file at ``path``: the group named by ``group_path`` (``"/"`` for
    the root; netCDF4-python's ``Group.path`` convention), or, when
    ``variable`` is given, that variable within the group. One
    ``nc_open`` per call — callers inquire per scope, never per
    attribute.

    '''

    library = _libnetcdf()

    ncid = ctypes.c_int()
    _check(
        library.nc_open(os.fsencode(path), 0, ctypes.byref(ncid)),
        f"nc_open({path})",
    )

    try:

        group_id = ncid

        for part in group_path.split("/"):

            if not part:

                continue

            child = ctypes.c_int()
            _check(
                library.nc_inq_grp_ncid(
                    group_id, part.encode("utf-8"), ctypes.byref(child)
                ),
                f"nc_inq_grp_ncid({part})",
            )
            group_id = child

        if variable is None:

            varid = _NC_GLOBAL
            count = ctypes.c_int()
            _check(
                library.nc_inq_natts(group_id, ctypes.byref(count)),
                "nc_inq_natts",
            )

        else:

            var = ctypes.c_int()
            _check(
                library.nc_inq_varid(
                    group_id, variable.encode("utf-8"), ctypes.byref(var)
                ),
                f"nc_inq_varid({variable})",
            )
            varid = var.value
            count = ctypes.c_int()
            _check(
                library.nc_inq_varnatts(
                    group_id, varid, ctypes.byref(count)
                ),
                "nc_inq_varnatts",
            )

        names = []

        for index in range(count.value):

            buffer = ctypes.create_string_buffer(_NC_MAX_NAME + 1)
            _check(
                library.nc_inq_attname(group_id, varid, index, buffer),
                f"nc_inq_attname(#{index})",
            )
            att_type = ctypes.c_int()
            _check(
                library.nc_inq_atttype(
                    group_id, varid, buffer.value, ctypes.byref(att_type)
                ),
                f"nc_inq_atttype({buffer.value!r})",
            )

            if att_type.value == NC_STRING:

                names.append(buffer.value.decode("utf-8"))

        return frozenset(names)

    finally:

        library.nc_close(ncid)


def string_attributes_of(obj) -> frozenset:

    '''

    :func:`string_attribute_names` for an **open** netCDF4-python object
    — a ``Dataset``, ``Group``, or ``Variable`` — deriving the file
    path and scope from the object itself (``filepath()``, ``path``,
    ``Variable.group()``). The inquiry re-opens the file read-only via
    netCDF-C, so the object may be any readable handle.

    '''

    if hasattr(obj, "group"):  # a Variable

        group = obj.group()

        return string_attribute_names(
            group.filepath(), group.path, variable = obj.name
        )

    return string_attribute_names(obj.filepath(), obj.path)
