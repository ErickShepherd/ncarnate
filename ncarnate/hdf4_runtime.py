#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

The HDF4 runtime gate (readiness action 2, KD-L3/KD-L4): every HDF4 code
path acquires the :mod:`ncarnate.hdf4` module through
:func:`require_hdf4_runtime` instead of importing it directly, so a
machine without a usable pyhdf (no Windows pip wheel; a missing HDF4
DLL) gets one stable, explained ``HDF4_RUNTIME_UNAVAILABLE`` refusal —
naming the detected cause, the unaffected capabilities, and the exact
supported install command — never a raw ``ImportError`` traceback.

Copyright (c) 2020-2026 Erick Edward Shepherd. MIT License — see the
top-level LICENSE file.

'''

from __future__ import annotations

# Local application imports.
from ncarnate.errors import NcarnateError

__all__ = [
    "Hdf4RuntimeUnavailableError",
    "require_hdf4_runtime",
]


class Hdf4RuntimeUnavailableError(NcarnateError):

    '''

    Raised when an HDF4/HDF-EOS2 operation is attempted but the HDF4
    runtime (pyhdf) cannot be imported. ``code`` is the stable
    ``HDF4_RUNTIME_UNAVAILABLE`` registry string (KD-L4); the message
    names the detected cause, the unaffected capabilities, and the exact
    conda-forge install command. Raised **before any output is created**.

    '''


def require_hdf4_runtime():

    '''

    Imports and returns the :mod:`ncarnate.hdf4` module (which itself
    imports pyhdf), or raises :class:`Hdf4RuntimeUnavailableError` with
    the stable KD-L4 refusal. Call this at the top of every HDF4 code
    path — never import ``ncarnate.hdf4`` or ``pyhdf`` directly outside
    :mod:`ncarnate.hdf4` itself — so the degraded-capability surface
    stays a single, consistent message.

    '''

    # Imported lazily via the registry module's own import below only on
    # the failure path, so the gate itself adds no import weight to the
    # netCDF-only surface.
    try:

        from ncarnate import hdf4

        return hdf4

    except ImportError as error:

        from ncarnate.audit.codes import HDF4_RUNTIME_UNAVAILABLE

        raise Hdf4RuntimeUnavailableError(
            "HDF4/HDF-EOS2 support is unavailable on this install: the "
            f"HDF4 runtime (pyhdf) could not be imported ({error}). "
            "netCDF operations are unaffected — detection, netCDF "
            "recompression, audits of netCDF files, and netCDF-only "
            "manifests all work without it. To enable HDF4/HDF-EOS2 "
            "conversion, install ncarnate from conda-forge, which supplies "
            "the HDF4 runtime it needs: conda install -c conda-forge "
            "ncarnate. (Adding only pyhdf to an existing environment can "
            "work but is unsupported — the pip wheel ships no HDF4 runtime "
            "on Windows; see the README for that troubleshooting.)",
            code = HDF4_RUNTIME_UNAVAILABLE,
        ) from error
