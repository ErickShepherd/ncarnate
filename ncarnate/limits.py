#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

Resource ceilings for allocations sized from untrusted file metadata.

A crafted HDF4/HDF-EOS2 or netCDF file can declare an enormous array (an
SDS, a grid mesh, or a highly compressible variable) that is tiny on disk
but materializes to terabytes in memory. These helpers bound the declared
size *before* the allocation happens, turning a would-be OOM crash into a
named, catchable error.

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

# Local application imports.
from ncarnate.errors import NcarnateError

# Per-array ceiling on the *uncompressed* size an input file may declare.
# Generously above any realistic single granule variable (tens of MB to a
# few GB for the target AMSR-E/MODIS products) while catching the
# tiny-file-declares-terabytes decompression/size bomb.
DEFAULT_MAX_ARRAY_BYTES = 8 * 1024 ** 3


def check_array_size(shape     : "tuple[int, ...]",
                     itemsize  : int,
                     context   : str,
                     max_bytes : "int | None" = None) -> None:

    '''

    Raises `NcarnateError` if an array of ``shape`` at ``itemsize`` bytes
    per element would exceed ``max_bytes`` (defaulting to the module-level
    `DEFAULT_MAX_ARRAY_BYTES`, read at call time so it stays adjustable).
    ``shape`` comes from untrusted file metadata, so the product is
    computed in unbounded Python ints (never overflowing) before any array
    is allocated.

    '''

    if max_bytes is None:

        max_bytes = DEFAULT_MAX_ARRAY_BYTES

    elements = 1

    for dimension in shape:

        elements *= int(dimension)

    total_bytes = elements * int(itemsize)

    if total_bytes > max_bytes:

        raise NcarnateError(
            f"{context}: declared array of shape {tuple(shape)} at "
            f"{itemsize} bytes/element is {total_bytes} bytes, exceeding "
            f"the {max_bytes}-byte safety ceiling; refusing to allocate. "
            f"This usually means a malformed or hostile input file.",
            code="DECLARED_ALLOCATION_TOO_LARGE",
        )
