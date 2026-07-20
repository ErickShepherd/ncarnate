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
import importlib.metadata
import os
import tempfile
import time
from typing import Callable
from typing import TypeAlias

# Third party imports.
import netCDF4 as nc
import numpy as np

# Local application imports.
from ncarnate.atttypes import string_attributes_of
from ncarnate.constants import __version__ as _NCARNATE_VERSION
from ncarnate.errors import NcarnateError
from ncarnate.errors import UnsupportedFormatError
from ncarnate.errors import UnsupportedTypeError
from ncarnate.errors import VerificationError
from ncarnate.formats import FileFormat
from ncarnate.formats import detect_format
from ncarnate.hashing import sha256_of_file
from ncarnate.limits import check_array_size
from ncarnate.result import Attribute
from ncarnate.result import CoordinateActions
from ncarnate.result import Dimension
from ncarnate.result import EncodingOptions
from ncarnate.result import Environment
from ncarnate.result import GroupNode
from ncarnate.result import NameMapping
from ncarnate.result import OperationResult
from ncarnate.result import OutputIdentity
from ncarnate.result import SkippedCoordinate
from ncarnate.result import SourceIdentity
from ncarnate.result import Variable
from ncarnate.result import VerificationRecord
from ncarnate.result import json_safe

# A netCDF4 group or file object (netCDF4.Dataset subclasses Group).
_Group: TypeAlias = "nc.Dataset | nc.Group"

# The per-verifier conversion-verification wording (design §The verification /
# validation separation). Scoped exactly to the fidelity contract
# (docs/fidelity-notes.md) and never beyond it: both claim re-read value
# identity within the dtype scope, not scientific correctness.
_VERIFY_METHOD_NETCDF = (
    "re-read value-identical raw arrays (np.array_equal, equal_nan for float; "
    "complex excluded), dtype / dimension (incl. unlimited) / attribute (incl. "
    "NC_STRING vs NC_CHAR storage type) / group-tree equal; storage-only changes."
)
_VERIFY_METHOD_HDF4 = (
    "SDS values re-read value-identical (bit-for-bit integer / char8, equal_nan "
    "float); reconstructed geolocation is additive (the output tree is a superset "
    "of the SDS payload); character-attribute NUL padding stripped and embedded-NUL "
    "attributes recoded to uint8 per the fidelity contract."
)


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

    This is the released public entry point; its ``-> str`` return is
    unchanged (design KD2). The full structured
    :class:`~ncarnate.result.OperationResult` is produced by
    :func:`_recompress_result` underneath (the stage API's ``execute``
    primitive, step 4B, wraps the same internal); ``recompress`` returns
    only ``result.destination.path``.

    '''

    return _recompress_result(
        src, dst, zlib, shuffle, complevel, overwrite, geolocation,
    ).destination.path


def _recompress_result(src         : str,
                       dst         : str | None = None,
                       zlib        : bool       = True,
                       shuffle     : bool       = True,
                       complevel   : int        = 7,
                       overwrite   : bool       = True,
                       geolocation : bool       = True) -> OperationResult:

    '''

    Rewrites the file at ``src`` as a netCDF4 file with the given
    compression settings, losslessly, and returns a structured
    :class:`~ncarnate.result.OperationResult` describing what it did.

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

        # Acquired here, not at module level, so the netCDF-only surface
        # never touches the HDF4 runtime (KD-L3): ncarnate.hdf4 pulls in
        # pyhdf, which has no Windows pip wheel. The gate turns a missing
        # runtime into the stable HDF4_RUNTIME_UNAVAILABLE refusal, before
        # any output is created (KD-L4).
        from ncarnate.hdf4_runtime import require_hdf4_runtime

        hdf4 = require_hdf4_runtime()

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

        operation      = "convert"
        verifier       = "ncarnate.hdf4.verify_conversion"
        verify_method  = _VERIFY_METHOD_HDF4

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

        operation      = "recompress"
        verifier       = "ncarnate._verify_lossless"
        verify_method  = _VERIFY_METHOD_NETCDF

    # Source identity is captured BEFORE the write: an in-place recompression
    # replaces the source at its own path, so its original bytes and size must
    # be read now, while they still exist (design KD10). The digest is over the
    # bytes this conversion reads; hashing is unconditional here (the manifest
    # path's separate preflight hash is discarded, so there is none to reuse).
    source = SourceIdentity(
        path            = src_path,
        detected_format = file_format.name,
        size_bytes      = os.path.getsize(src_path),
        sha256          = sha256_of_file(src_path),
    )

    options = EncodingOptions(
        zlib=zlib, shuffle=shuffle, complevel=complevel, geolocation=geolocation,
    )

    start = time.monotonic()
    _write_verified(src_path, dst_path, _write, _verify)
    elapsed = time.monotonic() - start

    return _build_operation_result(
        source, dst_path, operation, options, verifier, verify_method, elapsed,
    )


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
    #
    # Storage types are preserved exactly (KD-L6): `getncattr` erases the
    # NC_STRING/NC_CHAR distinction (both scalars read back as `str`), so
    # a blanket `setncatts` re-writes every text scalar as NC_CHAR — the
    # scalar NC_STRING degradation of readiness finding 6. The netCDF-C
    # inquiry names which source attributes are stored NC_STRING; those
    # are re-created with `setncattr_string`, everything else with
    # `setncattr` (which keeps NC_CHAR for str and the numeric types
    # as-is).
    string_attrs = string_attributes_of(src_obj)

    for attr in src_obj.ncattrs():

        if attr in exclude:

            continue

        value = src_obj.getncattr(attr)

        if attr in string_attrs:

            dst_obj.setncattr_string(attr, value)

        else:

            dst_obj.setncattr(attr, value)


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

    # Storage types, not just Python values (KD-L6): `getncattr` returns
    # `str` for both an NC_STRING scalar and an NC_CHAR attribute, so a
    # value-only comparison is blind to exactly the degradation the copy
    # used to introduce. Compare which attributes are stored NC_STRING on
    # each side via the netCDF-C inquiry.
    src_strings = string_attributes_of(src_obj) & src_names
    dst_strings = string_attributes_of(dst_obj) & dst_names

    _require(
        src_strings == dst_strings,
        f"attribute storage types differ on {path} "
        f"(NC_STRING only in source: {sorted(src_strings - dst_strings)}; "
        f"only in copy: {sorted(dst_strings - src_strings)})"
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


# ---------------------------------------------------------------------------
# Structured operation result (step 4A). The engine reads the committed output
# back to record its EFFECTIVE structure and encoding (ground truth, not the
# requested options echoed back — design KD5) plus the output digest.
# ---------------------------------------------------------------------------

def _build_operation_result(source        : SourceIdentity,
                            dst_path       : str,
                            operation      : str,
                            options        : EncodingOptions,
                            verifier       : str,
                            verify_method  : str,
                            elapsed        : float) -> OperationResult:

    '''

    Assemble the :class:`~ncarnate.result.OperationResult` for a conversion
    whose verified output already sits at ``dst_path``. Reads the committed
    output back for its group/dimension/attribute tree, effective per-variable
    encoding, and digest; derives the sanitized-name companions and coordinate
    actions from that tree (conversion path only — a storage-only recompression
    reconstructs no coordinates and renames nothing).

    '''

    with nc.Dataset(dst_path, mode = "r") as out_file:

        structure = _read_group_node(out_file, "/")

    destination = OutputIdentity(
        path             = dst_path,
        container_format = "NETCDF4",
        size_bytes       = os.path.getsize(dst_path),
        sha256           = sha256_of_file(dst_path),
    )

    if operation == "convert":

        name_mappings = _collect_name_mappings(structure)
        coordinates   = _coordinate_actions(options, structure)

    else:

        name_mappings = []
        coordinates   = CoordinateActions()

    verification = VerificationRecord(
        status           = "verified",
        verifier         = verifier,
        verifier_version = _NCARNATE_VERSION,
        method           = verify_method,
    )

    return OperationResult(
        source          = source,
        destination     = destination,
        operation       = operation,
        options         = options,
        structure       = structure,
        verification    = verification,
        environment     = Environment(adapter_versions = _adapter_versions()),
        elapsed_seconds = elapsed,
        name_mappings   = name_mappings,
        coordinates     = coordinates,
    )


def _adapter_versions() -> "dict[str, str | None]":

    '''

    The native-library versions that actually produced the output bytes. Any
    adapter absent from this install maps to ``None`` (e.g. pyhdf on a Windows
    pip install, KD-L3) rather than raising. ``libhdf4`` has no clean
    version-query API and stays ``None`` for now.

    '''

    def _pkg(name : str) -> "str | None":

        try:

            return importlib.metadata.version(name)

        except importlib.metadata.PackageNotFoundError:

            return None

    return {
        "numpy"   : _pkg("numpy"),
        "netCDF4" : _pkg("netCDF4"),
        "netcdf_c": getattr(nc, "__netcdf4libversion__", None),
        "libhdf5" : getattr(nc, "__hdf5libversion__", None),
        "pyhdf"   : _pkg("pyhdf"),
        "libhdf4" : None,
    }


def _child_path(parent : str, name : str) -> str:

    # "/" + "g" -> "/g"; "/g" + "h" -> "/g/h".
    return parent.rstrip("/") + "/" + name


def _read_group_node(obj : _Group, path : str) -> GroupNode:

    '''

    Build a :class:`~ncarnate.result.GroupNode` for an open netCDF group,
    recursively. Lists are sorted by name so the serialization (and its
    canonical hash) is order-stable regardless of on-disk traversal order.

    '''

    dimensions = [
        Dimension(name = name, size = dim.size, unlimited = dim.isunlimited())
        for name, dim in sorted(obj.dimensions.items())
    ]

    attributes = _read_attributes(obj)

    variables = [
        _read_variable(obj.variables[name]) for name in sorted(obj.variables)
    ]

    groups = [
        _read_group_node(obj.groups[name], _child_path(path, name))
        for name in sorted(obj.groups)
    ]

    return GroupNode(
        path       = path,
        dimensions = dimensions,
        attributes = attributes,
        variables  = variables,
        groups     = groups,
    )


def _endian_of(dtype : np.dtype) -> str:

    # The same friendly classification `_copy_variables` uses.
    if dtype.isnative:

        return "native"

    if dtype.str.startswith(">"):

        return "big"

    return "little"


def _read_variable(var : "nc.Variable") -> Variable:

    '''

    Build a :class:`~ncarnate.result.Variable` from an open netCDF variable,
    recording the EFFECTIVE encoding the library actually wrote (``filters()``
    / ``chunking()``) — for a contiguous source this differs from what was
    requested, which is exactly why it is read back (design KD5).

    '''

    var.set_auto_maskandscale(False)

    filters  = var.filters() or {}
    chunking = var.chunking()
    chunksizes = (
        [int(size) for size in chunking] if isinstance(chunking, list) else None
    )

    return Variable(
        name       = var.name,
        dtype      = var.dtype.str,
        endian     = _endian_of(var.dtype),
        dimensions = list(var.dimensions),
        zlib       = bool(filters.get("zlib", False)),
        shuffle    = bool(filters.get("shuffle", False)),
        complevel  = int(filters.get("complevel", 0)),
        chunksizes = chunksizes,
        attributes = _read_attributes(var),
    )


def _read_attributes(obj : "_Group | nc.Variable") -> "list[Attribute]":

    '''

    Read an object's attributes into :class:`~ncarnate.result.Attribute`s,
    preserving the ``NC_STRING`` vs ``NC_CHAR`` storage distinction (KD-L6)
    the converter works to keep — a numeric attribute records its numpy dtype
    string instead. Values are :func:`~ncarnate.result.json_safe`-coerced.

    '''

    string_attrs = string_attributes_of(obj)

    attributes = []

    for name in sorted(obj.ncattrs()):

        value = obj.getncattr(name)

        if name in string_attrs:

            storage_type = "NC_STRING"

        elif isinstance(value, str):

            storage_type = "NC_CHAR"

        else:

            storage_type = np.asarray(value).dtype.str

        attributes.append(
            Attribute(
                name         = name,
                storage_type = storage_type,
                value        = json_safe(value),
            )
        )

    return attributes


def _collect_name_mappings(root : GroupNode) -> "list[NameMapping]":

    '''

    Derive the sanitized-name companions from the output tree (design
    §Module & type layout): a variable's ``hdf4_name`` (an SDS rename), a
    group's ``hdf4_eos_name`` (a grid/swath rename), and any
    ``<attr>__hdf4_name`` (an attribute rename, whose ``parent_path`` — the
    owning group or variable — disambiguates a name that recurs on many
    owners). Sorted for a stable serialization.

    '''

    mappings : "list[NameMapping]" = []

    def visit(node : GroupNode) -> None:

        node_attrs = {attr.name: attr.value for attr in node.attributes}

        if "hdf4_eos_name" in node_attrs:

            mappings.append(NameMapping(
                netcdf_name   = node.path.rstrip("/").rsplit("/", 1)[-1],
                original_name = node_attrs["hdf4_eos_name"],
                kind          = "group",
                parent_path   = node.path,
            ))

        for attr in node.attributes:

            if attr.name.endswith("__hdf4_name"):

                mappings.append(NameMapping(
                    netcdf_name   = attr.name[: -len("__hdf4_name")],
                    original_name = attr.value,
                    kind          = "attribute",
                    parent_path   = node.path,
                ))

        for var in node.variables:

            var_path  = _child_path(node.path, var.name)
            var_attrs = {attr.name: attr.value for attr in var.attributes}

            if "hdf4_name" in var_attrs:

                mappings.append(NameMapping(
                    netcdf_name   = var.name,
                    original_name = var_attrs["hdf4_name"],
                    kind          = "variable",
                    parent_path   = node.path,
                ))

            for attr in var.attributes:

                if attr.name.endswith("__hdf4_name"):

                    mappings.append(NameMapping(
                        netcdf_name   = attr.name[: -len("__hdf4_name")],
                        original_name = attr.value,
                        kind          = "attribute",
                        parent_path   = var_path,
                    ))

        for child in node.groups:

            visit(child)

    visit(root)

    mappings.sort(key = lambda m: (m.parent_path, m.kind, m.netcdf_name))

    return mappings


def _coordinate_actions(options   : EncodingOptions,
                        root      : GroupNode) -> CoordinateActions:

    '''

    Report the coordinate actions of an HDF4/HDF-EOS2 conversion. With
    ``geolocation`` disabled the conversion is SDS-only — one ``skipped``
    entry records that. Otherwise ``generated`` lists the reconstructed
    coordinate / grid-mapping variables, detected from the output tree by the
    reconstruction ``comment`` marker or a ``grid_mapping_name`` attribute
    (only the freshly-converted output is inspected, so a stale marker on a
    recompressed netCDF source can never appear here).

    '''

    if not options.geolocation:

        return CoordinateActions(
            generated = [],
            skipped   = [SkippedCoordinate(
                name   = "geolocation",
                reason = "geolocation reconstruction disabled "
                         "(SDS-only conversion)",
                code   = None,
            )],
        )

    generated : "list[str]" = []

    def visit(node : GroupNode) -> None:

        for var in node.variables:

            names   = {attr.name for attr in var.attributes}
            comment = next(
                (attr.value for attr in var.attributes
                 if attr.name == "comment"),
                "",
            )

            reconstructed = (
                "grid_mapping_name" in names
                or (isinstance(comment, str)
                    and "reconstructed" in comment.lower())
            )

            if reconstructed:

                generated.append(_child_path(node.path, var.name))

        for child in node.groups:

            visit(child)

    visit(root)

    generated.sort()

    return CoordinateActions(generated = generated, skipped = [])
