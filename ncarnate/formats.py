#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

Input file-format detection: ncarnate dispatches to a reader based on the
file's magic bytes, never its extension.

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

# Standard library imports.
import enum
import os

# Magic-byte signatures at file offset 0.
_HDF4_MAGIC    = b"\x0e\x03\x13\x01"
_HDF5_MAGIC    = b"\x89HDF\r\n\x1a\n"
_NETCDF3_MAGIC = (b"CDF\x01", b"CDF\x02", b"CDF\x05")

# An HDF5 superblock may sit after a user block, at any power-of-two offset
# that is a multiple of 512; scanning to 64 KiB covers realistic files.
_HDF5_MAX_SCAN = 65536


class FileFormat(enum.Enum):

    '''

    The input container formats ncarnate distinguishes between.

    '''

    NETCDF3 = "netCDF3 (classic)"
    HDF5    = "HDF5 / netCDF4"
    HDF4    = "HDF4 / HDF-EOS2"
    UNKNOWN = "unknown"


def detect_format(path : str) -> FileFormat:

    '''

    Identifies the container format of the file at ``path`` from its
    magic bytes.

    '''

    size = os.path.getsize(path)

    with open(path, "rb") as file:

        header = file.read(8)

        if header.startswith(_HDF4_MAGIC):

            return FileFormat.HDF4

        if header.startswith(_NETCDF3_MAGIC):

            return FileFormat.NETCDF3

        if header == _HDF5_MAGIC:

            return FileFormat.HDF5

        # Scans power-of-two multiples of 512 for a user-block-offset
        # HDF5 superblock.
        offset = 512

        while offset <= min(size, _HDF5_MAX_SCAN):

            file.seek(offset)

            if file.read(8) == _HDF5_MAGIC:

                return FileFormat.HDF5

            offset *= 2

    return FileFormat.UNKNOWN
