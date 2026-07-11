#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

Core recompression routines: copy a supported input file into a new
netCDF4 file with different compression settings, losslessly.

The fidelity contract (docs/fidelity-notes.md): only *storage* changes —
compression, shuffle, chunk/endian layout, container format — never the
science data. Variables are copied raw (no mask-and-scale round-trip),
``_FillValue`` is declared at creation time, and ``scale_factor`` /
``add_offset`` are carried across as declarations, never applied. Every
copy is verified against the source before it can replace anything.

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

# Standard library imports.
import os
import tempfile
from typing import Callable
from typing import TypeAlias

# Third party imports.
import netCDF4 as nc
import numpy as np

# Local application imports.
from ncarnate import hdf4
from ncarnate.errors import NcarnateError
from ncarnate.errors import UnsupportedFormatError
from ncarnate.errors import UnsupportedTypeError
from ncarnate.errors import VerificationError
from ncarnate.formats import FileFormat
from ncarnate.formats import detect_format
from ncarnate.limits import check_array_size

# A netCDF4 group or file object (netCDF4.Dataset subclasses Group).
_Group: TypeAlias = "nc.Dataset | nc.Group"


def recompress(src         : str,
               dst         : str | None = None,
               zlib        : bool       = True,
               shuffle     : bool       = True,
               complevel   : int        = 7,
               overwrite   : bool       = True,
               geolocation : bool       = True) -> str:

    '''

    Rewrites the file at ``src`` as a netCDF4 file with the given
    compression settings, losslessly, and returns the output path.

    For netCDF3/netCDF4/HDF5 input the output target is resolved as:

    - ``dst`` given: the output is written to ``dst`` and the source is
      left untouched (``overwrite`` is ignored).
    - ``dst`` omitted, ``overwrite`` true (the default): the source file
      is replaced in place — but only after the new file has been fully
      written and verified lossless against it.
    - ``dst`` omitted, ``overwrite`` false: the output is written next to
      the source with a ``_recompressed`` suffix.

    HDF4/HDF-EOS2 input is a format *conversion*: the output is written
    to ``dst`` (default: the source path with a ``.nc`` extension) and
    the source file is never replaced, regardless of ``overwrite``. When
    ``geolocation`` is true (the default), HDF-EOS2 grid/swath
    coordinates are reconstructed additively per the geolocation design;
    ``geolocation=False`` converts the SDS payload only.

    The new file is always written to a temporary path in the target's
    directory, verified value-for-value against the source, and only then
    atomically moved onto the target. On any failure the source file is
    untouched and the temporary file is removed.

    '''

    # Resolve symlinks: for in-place recompression `os.replace` onto a
    # symlink would replace the link (orphaning its target with stale
    # content) and the permission copy would stamp the target's mode onto
    # the new file. Operating on the real path makes the atomic replace act
    # on the actual file and keeps auto-derived outputs next to it.
    src_path = os.path.realpath(src)

    if not os.path.isfile(src_path):

        raise NcarnateError(f"No such file: {src_path}")

    file_format = detect_format(src_path)

    if file_format is FileFormat.UNKNOWN:

        raise UnsupportedFormatError(
            f"{src_path} is not a recognized netCDF3, netCDF4/HDF5, or "
            f"HDF4 file."
        )

    if file_format is FileFormat.HDF4:

        # Conversion, not recompression: the .hdf original is a different
        # format and is never destroyed.
        if dst is not None:

            dst_path = os.path.abspath(dst)

        else:

            filename = os.path.splitext(src_path)[0]
            dst_path = filename + ".nc"

            _guard_auto_destination(dst_path)

        if dst_path == src_path:

            raise NcarnateError(
                "The netCDF output of an HDF4 conversion cannot replace "
                "the HDF4 source; give dst a different path."
            )

        tree = hdf4.read_hdf4(src_path, geolocation = geolocation)

        def _write(tmp_path : str) -> None:

            hdf4.write_netcdf(tree, tmp_path, zlib, shuffle, complevel)

        def _verify(tmp_path : str) -> None:

            hdf4.verify_conversion(src_path, tmp_path)

    else:

        if dst is not None:

            dst_path = os.path.abspath(dst)

            if dst_path == src_path:

                raise NcarnateError(
                    "dst must differ from src; omit dst to recompress "
                    "in place."
                )

        elif overwrite:

            dst_path = src_path

        else:

            filename, file_extension = os.path.splitext(src_path)
            dst_path = filename + "_recompressed" + file_extension

            _guard_auto_destination(dst_path)

        def _write(tmp_path : str) -> None:

            with nc.Dataset(src_path, mode = "r") as src_file, \
                 nc.Dataset(tmp_path, mode = "w",
                            format = "NETCDF4") as dst_file:

                _copy_group(src_file, dst_file, zlib, shuffle, complevel)

        def _verify(tmp_path : str) -> None:

            _verify_lossless(src_path, tmp_path)

    _write_verified(src_path, dst_path, _write, _verify)

    return dst_path


def _guard_auto_destination(dst_path : str) -> None:

    '''

    Refuses to overwrite a pre-existing file at an *auto-derived*
    destination (the ``<stem>.nc`` conversion target or the
    ``_recompressed`` sibling). The user never named this path, so
    clobbering a file that happens to already sit there would be silent
    data loss of something unrelated. An explicit ``dst`` bypasses this.

    '''

    if os.path.exists(dst_path):

        raise NcarnateError(
            f"Refusing to overwrite the existing file {dst_path}, which "
            f"was auto-derived from the input name. Pass an explicit "
            f"output path, or move/remove the existing file."
        )


def _write_verified(src_path : str,
                    dst_path : str,
                    write    : "Callable[[str], None]",
                    verify   : "Callable[[str], None]") -> None:

    '''

    The safe-overwrite scaffold shared by both read paths: write to a
    temporary file in the target's directory, verify it against the
    source, then atomically replace the target. On any failure the
    source and target are untouched and the temporary file is removed.

    '''

    descriptor, tmp_path = tempfile.mkstemp(
        dir    = os.path.dirname(dst_path),
        prefix = os.path.basename(dst_path) + ".",
        suffix = ".tmp"
    )

    os.close(descriptor)

    try:

        write(tmp_path)
        verify(tmp_path)

        # `mkstemp` creates the file 0o600; carry the source's permission
        # bits over so the output isn't unreadable to the user's group
        # (masking off setuid/setgid/sticky — no reason to propagate them).
        os.chmod(tmp_path, os.stat(src_path).st_mode & 0o777)

        # The temporary file lives in the target's directory, so the
        # replace is a same-filesystem atomic rename.
        os.replace(tmp_path, dst_path)

    except BaseException:

        # Best-effort cleanup that must never mask the original failure:
        # a raised unlink (or a link-following exists check) would replace
        # the real cause with a cleanup error.
        try:

            if os.path.lexists(tmp_path):

                os.unlink(tmp_path)

        except OSError:

            pass

        raise


def _copy_group(src_obj   : _Group,
                dst_obj   : _Group,
                zlib      : bool,
                shuffle   : bool,
                complevel : int) -> None:

    _copy_dimensions(src_obj, dst_obj)
    _copy_attributes(src_obj, dst_obj)
    _copy_variables(src_obj, dst_obj, zlib, shuffle, complevel)

    for name, src_group in src_obj.groups.items():

        dst_group = dst_obj.createGroup(name)

        _copy_group(src_group, dst_group, zlib, shuffle, complevel)


def _copy_dimensions(src_obj : _Group, dst_obj : _Group) -> None:

    # Copies the dimensions of the source file or group; unlimited
    # dimensions stay unlimited.
    for name, dimension in src_obj.dimensions.items():

        if dimension.isunlimited():

            size = None

        else:

            size = dimension.size

        dst_obj.createDimension(name, size)


def _copy_attributes(src_obj : _Group,
                     dst_obj : _Group,
                     exclude : tuple[str, ...] = ()) -> None:

    # Copies the attributes of the source file, group, or variable.
    # `_copy_variables` excludes `_FillValue`, which it declares at
    # `createVariable` time instead; group/global attributes copy verbatim.
    attributes = {
        attr : src_obj.getncattr(attr)
        for attr in src_obj.ncattrs()
        if attr not in exclude
    }

    dst_obj.setncatts(attributes)


def _copy_variables(src_obj   : _Group,
                    dst_obj   : _Group,
                    zlib      : bool,
                    shuffle   : bool,
                    complevel : int) -> None:

    # Copies the variables of the source file or group, raw: stored
    # values are transferred bit-for-bit, with `scale_factor`/`add_offset`
    # re-declared, never applied.
    for name, src_var in src_obj.variables.items():

        src_var.set_auto_maskandscale(False)

        dtype      = src_var.datatype
        dimensions = src_var.dimensions

        if not isinstance(dtype, np.dtype):

            group_path = src_var.group().path.rstrip("/")

            raise UnsupportedTypeError(
                f"Variable {group_path}/{name} uses a "
                f"user-defined type ({dtype!r}); compound, VLen, enum, and "
                f"opaque types are outside the v2 fidelity guarantee.",
                code="UNSUPPORTED_TYPE",
            )

        if dtype.isnative:

            endian = "native"

        elif dtype.str.startswith(">"):

            endian = "big"

        else:

            endian = "little"

        if "_FillValue" in src_var.ncattrs():

            fill_value = src_var.getncattr("_FillValue")

        else:

            fill_value = None

        variable_kwargs = {
            "endian"     : endian,
            "zlib"       : zlib,
            "shuffle"    : shuffle,
            "complevel"  : complevel,
            "fill_value" : fill_value
        }

        # Preserves the source chunk shape where one exists; a contiguous
        # source is left to the library's default chunking (compression
        # requires chunked storage).
        chunking = src_var.chunking()

        if isinstance(chunking, list):

            variable_kwargs["chunksizes"] = chunking

        dst_var = dst_obj.createVariable(
            name, dtype, dimensions, **variable_kwargs
        )

        dst_var.set_auto_maskandscale(False)

        # Copies the variable attributes (minus the declared `_FillValue`).
        _copy_attributes(src_var, dst_var, exclude = ("_FillValue",))

        # Copies the variable's stored values, raw. Zero-size variables
        # (an empty unlimited dimension) have nothing to write. The whole
        # variable is materialized in memory, so bound its declared size
        # first — a tiny, highly compressible crafted file can otherwise
        # declare a variable that expands to terabytes on read.
        if 0 not in src_var.shape:

            check_array_size(
                src_var.shape, src_var.dtype.itemsize,
                f"Variable {name!r}"
            )

            dst_var[...] = src_var[...]


def _verify_lossless(src_path : str, dst_path : str) -> None:

    '''

    Re-opens both files and asserts the copy is faithful: identical
    dimension/group trees, attributes, dtypes, and value-identical raw
    arrays (compared with ``np.array_equal(equal_nan=True)``, so distinct
    NaN bit-patterns and ``-0.0``/``+0.0`` count as equal). Raises
    `VerificationError` on the first difference.

    '''

    with nc.Dataset(src_path, mode = "r") as src_file, \
         nc.Dataset(dst_path, mode = "r") as dst_file:

        _verify_group(src_file, dst_file, "/")


def _verify_group(src_obj : _Group, dst_obj : _Group, path : str) -> None:

    _require(
        set(src_obj.dimensions) == set(dst_obj.dimensions),
        f"dimension names differ in group {path}"
    )

    for name, src_dim in src_obj.dimensions.items():

        dst_dim = dst_obj.dimensions[name]

        _require(
            src_dim.size == dst_dim.size,
            f"dimension {path}{name} size differs "
            f"({src_dim.size} != {dst_dim.size})"
        )

        _require(
            src_dim.isunlimited() == dst_dim.isunlimited(),
            f"dimension {path}{name} unlimited flag differs"
        )

    _verify_attributes(src_obj, dst_obj, path)

    _require(
        set(src_obj.variables) == set(dst_obj.variables),
        f"variable names differ in group {path}"
    )

    for name, src_var in src_obj.variables.items():

        dst_var  = dst_obj.variables[name]
        location = f"{path}{name}"

        src_var.set_auto_maskandscale(False)
        dst_var.set_auto_maskandscale(False)

        _require(
            src_var.dtype == dst_var.dtype,
            f"variable {location} dtype differs "
            f"({src_var.dtype} != {dst_var.dtype})"
        )

        _require(
            src_var.dimensions == dst_var.dimensions,
            f"variable {location} dimensions differ"
        )

        _verify_attributes(src_var, dst_var, location)

        equal_nan  = src_var.dtype.kind in "fc"
        src_values = src_var[...]
        dst_values = dst_var[...]

        _require(
            np.array_equal(src_values, dst_values, equal_nan = equal_nan),
            f"variable {location} values differ"
        )

    _require(
        set(src_obj.groups) == set(dst_obj.groups),
        f"subgroup names differ in group {path}"
    )

    for name, src_group in src_obj.groups.items():

        _verify_group(src_group, dst_obj.groups[name], f"{path}{name}/")


def _verify_attributes(src_obj : _Group, dst_obj : _Group, path : str) -> None:

    src_names = set(src_obj.ncattrs())
    dst_names = set(dst_obj.ncattrs())

    _require(
        src_names == dst_names,
        f"attribute names differ on {path} "
        f"(only in source: {sorted(src_names - dst_names)}; "
        f"only in copy: {sorted(dst_names - src_names)})"
    )

    for name in src_names:

        src_value = np.asarray(src_obj.getncattr(name))
        dst_value = np.asarray(dst_obj.getncattr(name))

        equal_nan = src_value.dtype.kind in "fc"

        _require(
            src_value.dtype == dst_value.dtype
            and np.array_equal(src_value, dst_value, equal_nan = equal_nan),
            f"attribute {name!r} on {path} differs"
        )


def _require(condition : bool, message : str) -> None:

    if not condition:

        raise VerificationError(f"Verification failed: {message}")
